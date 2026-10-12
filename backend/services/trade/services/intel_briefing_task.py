"""QQ 情报速览派发（trade 常驻任务）——每交易日 3 档冲刷非持仓情报合集。

2026-10-12 用户拍板重规划：非持仓新闻/大盘情报不再即时推，聚合到
``08:35 盘前 / 11:35 午间 / 15:15 盘后``（北京时间）三个档期的卡片式速览
（组合规则与排版见 :mod:`backend.shared.qq_briefing`）。持仓相关情报仍由
``sentinel_alert_service`` / ``holding_sentinel`` 即时推，不经过本任务。

纪律（与值班摘要 ``duty_summary`` 同款）：

- **哨兵在写、本任务在清**：条目由 sentinel 路由经 ``enqueue_briefing`` 落
  Redis 缓冲；本任务到点冲刷。任务晚起/重启都不丢条目（缓冲在 Redis）。
- **只认最近一档**：16:00 才启动不会把早/午/盘后连着补发三遍；错过档期的
  条目由下一档合集兜住（有 20h 新鲜度上限，周末旧闻自然过期）。
- **读失败 ≠ 没有**：``flush_market`` 返回 ``None``（读不到）时**不落 done 键**，
  下个周期重试；返回 0（合法空档/全超龄已消费）才落 done——把 Redis 故障
  渲染成「今天没情报」是这个任务最坏的失败模式。
- **非交易日秒过**：与收盘报表同口径（``trading_calendar`` 不可用按工作日近似）。
- 任务由 ``trade/main.py`` lifespan 起停；开关 ``QM_QQ_BRIEFING_ENABLED``
  在 main（建不建任务）与本循环（跑不跑）各判一次。心跳键 ``intel_briefing``
  用于调度体检。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime

from backend.services.live_trading.services.trading_session import TZ
from backend.services.trade.services.daily_pnl_report_task import (
    _env_bool,
    _is_trading_day,
    _redis_client,
)
from backend.shared.market_labels import market_label
from backend.shared.qq_briefing import (
    DONE_TTL_S,
    done_key,
    flush_market,
    latest_due_slot,
)

logger = logging.getLogger(__name__)

#: 冲刷的市场面（缓冲按市场分键；空缓冲冲刷为无害 no-op）。
_MARKETS: tuple[str, ...] = ("CN", "HK", "US")

#: 轮询间隔（秒）：档期粒度是分钟级，60s 足够且与值班摘要同节奏。
_DEFAULT_INTERVAL_S = 60.0


def _poll_interval_s() -> float:
    try:
        return max(
            5.0, float(os.getenv("QM_QQ_BRIEFING_INTERVAL_S") or _DEFAULT_INTERVAL_S)
        )
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_S


async def run_intel_briefing_cycle(now: datetime, is_trading_day: bool) -> int:
    """单周期：找最近档期 → 交易日门 → 逐市场冲刷 + 落 done 键。

    ``now``/``is_trading_day`` 由调用方注入（测试直接调本函数，不必等真时钟）。
    冲刷失败（``flush_market`` 返回 None）**不落 done**，下个周期重试。
    返回**成功冲刷的市场数**（含空缓冲的合法 0 条档；供 schedule_ctl 重跑面回显）。
    """
    slot = latest_due_slot((now.hour, now.minute))
    if slot is None or not is_trading_day:
        return 0
    from backend.services.trade_shared.deps import get_redis

    client = _redis_client(get_redis())
    if client is None:
        raise RuntimeError("redis client 不可用")
    day = now.date().strftime("%Y%m%d")
    flushed = 0
    for market in _MARKETS:
        key = done_key(market, day, slot.key)
        try:
            if client.exists(key):
                continue
        except Exception:  # noqa: BLE001 - done 查不到 = 不敢盲冲
            # 重复发一档速览是用户最烦的噪音形态之一；宁可跳过本周期
            # （60s 后重来），绝不拿「读不到 done」当「没发过」。
            logger.warning("[IntelBriefing] done 键读取失败，本周期跳过: %s", key)
            continue
        sent = flush_market(
            client,
            market,
            slot,
            market_label=market_label(market),
        )
        if sent is None:
            # 读失败：不落 done，下个周期重试（绝不渲染成「没有」）
            logger.warning(
                "[IntelBriefing] %s 速览冲刷失败（读不到），保留 done 键待重试",
                market,
            )
            continue
        flushed += 1
        try:
            client.set(key, "1", ex=DONE_TTL_S)
        except Exception:  # noqa: BLE001 - done 落不下最多重复冲刷
            logger.warning("[IntelBriefing] done 键写入失败: %s", key)
    return flushed


async def run_intel_briefing_task() -> None:
    """常驻循环：每交易日到档期冲刷一次三市场速览缓冲。"""
    if not _env_bool("QM_QQ_BRIEFING_ENABLED", True):
        logger.info("[IntelBriefing] 速览任务关闭（QM_QQ_BRIEFING_ENABLED=0）")
        return

    from backend.shared.scheduler_registry import heartbeat as _sched_heartbeat

    interval = _poll_interval_s()
    logger.info(
        "[IntelBriefing] 速览任务启动：每交易日 08:35/11:35/15:15 三档（%.0fs 轮询）",
        interval,
    )
    last_error = ""
    trading_day_memo: tuple[date, bool] | None = None
    while True:
        try:
            _sched_heartbeat("intel_briefing")
        except Exception:  # noqa: BLE001 心跳是旁路
            pass
        try:
            now = datetime.now(TZ)
            today = now.date()
            if latest_due_slot((now.hour, now.minute)) is not None:
                if trading_day_memo is None or trading_day_memo[0] != today:
                    trading_day_memo = (today, await _is_trading_day(today))
                await run_intel_briefing_cycle(now, trading_day_memo[1])
            last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if message != last_error:
                logger.error("[IntelBriefing] 任务异常: %s", exc, exc_info=True)
            last_error = message
        await asyncio.sleep(interval)
