"""TdxAiData 订阅集群守护（2026-10-14）——缺片自动拉起 + 调度心跳。

背景：分片 worker 是 `start_new_session` 的独立子进程，但**没有任何定时任务负责
拉起**——容器重建 / worker 被杀 / SDK 段错误后，订阅静默不在，直到有人手动点面板
「连通性自检」或调 `default_cluster().ensure_all()`（2026-10-14 复查发现：分片集群
唯一的拉起入口是这两个手动面）。数据面断流有 ``quote_freshness_watch`` 告警，但
**告警 ≠ 恢复**；本任务按周期体检集群，缺片即拉起（自愈），并写调度心跳
（``qm:sched:hb:tdx_cluster_watch``，体检 C07 判活）。

判活/自愈口径：
- 仅在 ``config.is_enabled()`` 且 ``config.subscription_enabled()`` 时做体检与拉起
  （订阅关 = 不需要常驻 SDK 进程；请求类调用仍走 client 的按需懒拉起）；
- 体检 = ``cluster.status(try_start=False)``（无副作用）；``worker != up``（含
  degraded，即部分片缺失）→ ``cluster.ensure_all()``；
- **自愈节流**：两次拉起尝试间隔 ≥ ``MIN_ENSURE_GAP_S``（默认 120s）——worker 持续
  崩溃（ini 坏 / token 失效）时避免「拉起风暴」把崩溃现场刷得不可读；
- 巡检周期 ``WATCH_INTERVAL_S``（默认 60s）；订阅关/通道关时低频空转（仍写心跳）。

状态 ``tdx_cluster_watch_status`` 供排障/测试读取（进程内存，无 Redis 消费方；
心跳与 C07 是进程外的唯一观测面）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from backend.shared.tdx_aidata import config
from backend.shared.tdx_aidata.client import default_cluster

logger = logging.getLogger(__name__)

WATCH_INTERVAL_S = max(15, int(os.getenv("QM_TDX_CLUSTER_WATCH_INTERVAL_S", "60")))
MIN_ENSURE_GAP_S = max(30.0, float(os.getenv("QM_TDX_CLUSTER_WATCH_MIN_GAP_S", "120")))
IDLE_SLEEP_S = 300.0  # 订阅关/通道关时的空转周期（心跳照写）

#: 运行状态（供排障/测试读取；进程内存）
tdx_cluster_watch_status: dict = {
    "enabled": False,
    "checks": 0,
    "ensures": 0,
    "last_check_at": None,
    "last_ensure_at": None,
    "last_worker": None,
    "last_shards_up": None,
    "last_error": None,
}


def decide_action(
    worker_state: str | None, *, now: float, last_ensure_ts: float | None
) -> str:
    """体检结论 → 动作（纯函数）：``up``→ok；缺片→ensure（节流窗内→wait）。"""
    if worker_state == "up":
        return "ok"
    if last_ensure_ts is not None and now - last_ensure_ts < MIN_ENSURE_GAP_S:
        return "wait"
    return "ensure"


async def run_tdx_cluster_watch_task() -> None:
    """常驻巡检：体检集群 → 缺片拉起（节流）→ 调度心跳。"""
    logger.info(
        "[TdxClusterWatch] 订阅集群守护启动 interval=%.0fs min_gap=%.0fs",
        WATCH_INTERVAL_S,
        MIN_ENSURE_GAP_S,
    )
    tdx_cluster_watch_status["enabled"] = True
    last_ensure_ts: float | None = None
    while True:
        # 调度心跳（T-P1-06）：每次循环写入（含空转），体检 C07 依此判活
        try:
            from backend.shared.scheduler_registry import heartbeat as _sched_heartbeat

            _sched_heartbeat("tdx_cluster_watch")
        except Exception:  # noqa: BLE001 - best-effort
            pass
        try:
            if not (config.is_enabled() and config.subscription_enabled()):
                tdx_cluster_watch_status["last_worker"] = "off"
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            st = await default_cluster().status(try_start=False)
            worker_state = str(st.get("worker") or "down")
            now = time.time()
            tdx_cluster_watch_status.update(
                checks=tdx_cluster_watch_status["checks"] + 1,
                last_check_at=now,
                last_worker=worker_state,
                last_shards_up=st.get("shards_up"),
            )
            action = decide_action(worker_state, now=now, last_ensure_ts=last_ensure_ts)
            if action == "ensure":
                started = await default_cluster().ensure_all()
                last_ensure_ts = time.time()
                tdx_cluster_watch_status.update(
                    ensures=tdx_cluster_watch_status["ensures"] + 1,
                    last_ensure_at=last_ensure_ts,
                )
                logger.warning(
                    "[TdxClusterWatch] 订阅集群缺片（%s），拉起结果: %s",
                    worker_state,
                    started,
                )
        except Exception as exc:  # noqa: BLE001 - 循环永不退出
            tdx_cluster_watch_status["last_error"] = str(exc)[:200]
            logger.error("[TdxClusterWatch] 巡检异常: %s", exc, exc_info=True)
        await asyncio.sleep(WATCH_INTERVAL_S)
