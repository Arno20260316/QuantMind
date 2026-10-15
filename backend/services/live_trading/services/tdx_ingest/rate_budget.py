"""通达信桥采集令牌桶——按分钟计数的全局预算 + RATE_LIMITED 自适应降速。

背景（探针实测，2026-10-15）
----------------------------
桥侧限流 **600 req/min/IP 全端点共享**（`tools/bridge-windows/config.yaml`
security 段）。盘中热集 ~333/min + L2 ~300/min 已把配额占满，所以本采集链
只在盘后窗口跑，预算默认 **180/min**（env ``TDX_INGEST_RATE_PER_MIN``），
盘后合计 ≤525 < 600 留足余量。

设计
----
* 计数键 ``qm:tdx_ingest:rate:{YYYYmmddHHMM}``（交易库 db2），INCR + EXPIRE——
  同分钟窗口内所有**进程**（常驻任务 + 手动 --once）共享一份计数；
* 计数超过当前有效速率 → 睡到下一分钟再试（不忙等）；
* 桥仍回 RATE_LIMITED（说明别的进程也在挤，或桥侧限流比配置更严）→
  **指数退避**（5s→10s→…→60s 封顶）且**当前有效速率减半**（下限 30/min），
  之后每成功一次缓慢恢复——降速是自愈的，不需要人工干预；
* 本模块只依赖注入的 redis（同步客户端）+ asyncio 睡眠，纯逻辑可单测。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: 默认速率（次/分钟）——盘后窗口预算，env 可调。
DEFAULT_RATE_PER_MIN = 180

#: 降速下限（次/分钟）：再挤也要保持基本吞吐，避免退成每分钟个位数。
MIN_RATE_PER_MIN = 30

#: RATE_LIMITED 退避：起始秒数 × 2^连击，封顶 60s。
_BACKOFF_BASE_S = 5.0
_BACKOFF_CAP_S = 60.0

#: 计数键 TTL（秒）：跨过分钟边界即失活，留 2 分钟防时钟偏差。
_KEY_TTL_S = 120


def rate_per_min_from_env() -> int:
    try:
        value = int(str(os.getenv("TDX_INGEST_RATE_PER_MIN") or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_RATE_PER_MIN
    return max(MIN_RATE_PER_MIN, min(600, value))


def _minute_key(now: float | None = None) -> str:
    dt = datetime.fromtimestamp(
        now if now is not None else time.time(), tz=timezone.utc
    )
    return f"qm:tdx_ingest:rate:{dt:%Y%m%d%H%M}"


def seconds_into_minute(now: float) -> float:
    """当前时刻在本分钟内的秒数（睡眠到下一分钟用）。"""
    return now % 60.0


class RateBudget:
    """采集调用前的准入闸（单实例挂在任务/手动运行器里，状态进程内）。"""

    def __init__(
        self,
        redis,
        *,
        rate_per_min: int | None = None,
        sleep=asyncio.sleep,
        clock=time.time,
    ) -> None:
        self._redis = redis
        self._base_rate = int(rate_per_min or rate_per_min_from_env())
        self._effective_rate = self._base_rate
        self._penalty_until = 0.0
        self._consecutive_limited = 0
        self._sleep = sleep
        self._clock = clock

    @property
    def base_rate(self) -> int:
        return self._base_rate

    @property
    def effective_rate(self) -> int:
        return self._effective_rate

    # ---- Redis 计数（同步客户端在 to_thread 里跑） ----

    def _peek_and_bump(self) -> tuple[bool, int]:
        """先读当前计数（超限时只读不增——不该把计数继续推高），再决定是否自增。

        返回 ``(是否放行, 当前分钟计数)``。**放行与否不靠调用方比大小**：
        计数已到上限时本分钟放行额度就是 0，此时「已到上限」与「未到上限」
        必须是两个不同的返回值——早先版本在到限时不 INCR 却把计数原样返回，
        调用方 ``count <= rate`` 恒真，单进程下限额从未真正生效（到限后的
        每一次调用都被放行），本分钟预算形同虚设。

        实现为「读 → 未超限才 INCR」两步。多进程下两步之间有竞态，但那只是
        让个别调用多挤过去一两次，无害（我们是保守侧）。

        Redis 不可用（``redis=None``，trade 启动期降级路径）→ 计数恒 0（放行）：
        预算闸是「省配额」用的，不是「锁死采集」用的——桥侧 600/min 限流仍在
        最后一道兜底。绝不因 Redis 挂了把整个采集链报成 error。
        """
        if self._redis is None:
            return True, 0
        key = _minute_key(self._clock())
        current = self._redis.get(key)
        try:
            current_n = int(current) if current is not None else 0
        except (TypeError, ValueError):
            current_n = 0
        if current_n >= self._effective_rate:
            return False, current_n
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, _KEY_TTL_S)
        results = pipe.execute()
        return True, int(results[0])

    async def acquire(self) -> None:
        """调用一次桥之前必须通过；超限则睡到下一分钟（最多绕 3 轮防呆）。"""
        for _ in range(3):
            now = self._clock()
            if now < self._penalty_until:
                wait = self._penalty_until - now
                logger.info("[TdxIngest] RATE_LIMITED 退避中，等待 %.1fs", wait)
                await self._sleep(wait)
                continue
            try:
                admitted, count = await asyncio.to_thread(self._peek_and_bump)
            except Exception as exc:  # noqa: BLE001 - 同 redis=None：放行由桥限流兜底
                logger.warning("[TdxIngest] 令牌桶 Redis 异常（放行一次）: %s", exc)
                return
            if admitted:
                return
            # 本分钟额度用完：睡到下一分钟 + 100~400ms 抖动（多进程错峰）
            wait = (
                60.0 - seconds_into_minute(self._clock()) + 0.1 + random.random() * 0.3
            )
            logger.info(
                "[TdxIngest] 本分钟预算已满（%d/%d），等待 %.1fs 到下一分钟",
                count,
                self._effective_rate,
                wait,
            )
            await self._sleep(wait)
        # 绕了 3 轮还进不去（时钟异常/redis 坏）——放行一次，桥侧限流兜底
        logger.warning("[TdxIngest] 令牌桶连续 3 轮未放行，放行一次由桥侧限流兜底")

    # ---- 桥反馈（调用侧回报） ----

    def on_rate_limited(self) -> None:
        """桥回 RATE_LIMITED：退避 + 有效速率减半（自愈式降速）。"""
        self._consecutive_limited += 1
        backoff = min(
            _BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** (self._consecutive_limited - 1))
        )
        self._penalty_until = self._clock() + backoff
        old = self._effective_rate
        self._effective_rate = max(MIN_RATE_PER_MIN, self._effective_rate // 2)
        logger.warning(
            "[TdxIngest] 桥限流（第 %d 连击）：退避 %.0fs，速率 %d→%d/min",
            self._consecutive_limited,
            backoff,
            old,
            self._effective_rate,
        )

    def on_success(self) -> None:
        """成功一次：连击清零；速率按步长缓慢恢复到基准。"""
        self._consecutive_limited = 0
        if self._effective_rate < self._base_rate:
            step = max(1, self._base_rate // 20)
            self._effective_rate = min(self._base_rate, self._effective_rate + step)


__all__ = [
    "DEFAULT_RATE_PER_MIN",
    "MIN_RATE_PER_MIN",
    "RateBudget",
    "rate_per_min_from_env",
    "seconds_into_minute",
]
