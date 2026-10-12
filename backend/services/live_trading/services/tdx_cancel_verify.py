"""TDX 撤单结果终态复核（唯一实现）。

2026-10-12 实测: 桥撤单路由只认 ``Value=="1"``，而直连账号撤单成功码为
``"2"``（与下单路径 plan_executor 口径一致）——实际已生效的撤单被桥报成
``{"success": false}``，委托约 2s 后终态 ``side=cancel / status=rejected``。

纪律: 凡「桥握手报失败/结果未知」的撤单，一律按当日委托终态复核后再下结论
（成功不代表未成交，失败也不代表没撤掉）。终态分类:
  - ``side=cancel``（BSFlag=-1，优先于 Status——TDX Status=0 已撤与废单同码）
    或 status ∈ {cancelled, partial_cancelled} → **cancelled**（撤单已成）
  - status=filled 或成交量已满 → **filled**（撤销前已成交）
  - status ∈ {rejected, expired} 且非撤单 → **dead**（废单/已终结，无需撤销）
  - 其余在途状态 → **active**（真失败，还在途）
  - 查不到/认不出 → **unknown**（结果未知）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# 复核节拍: 3 次 × 0.6s ≈ 2s 窗口（实测 TQ 异步落终态 ≤2s；读模块属性便于测试打桩）
CANCEL_VERIFY_ATTEMPTS = 3
CANCEL_VERIFY_DELAY_S = 0.6

_CANCELLED_STATUSES = ("cancelled", "partial_cancelled")
_DEAD_STATUSES = ("rejected", "expired")
_ACTIVE_STATUSES = (
    "submitted",
    "partial_fill",
    "pending",
    "needs_confirm",
    "cancelling",
    "cancel_failed",
)


def classify_order_terminal(order: dict | None) -> str:
    """把一条（桥归一化后的）委托分类: cancelled/filled/dead/active/unknown。"""
    if not order:
        return "unknown"
    side = str(order.get("side") or "").strip().lower()
    status = str(order.get("status") or "").strip().lower()

    def _num(key: str) -> float:
        try:
            return float(order.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    # BSFlag=-1（撤单记录）优先：TDX Status=0 已撤与废单同码，只能靠 side 区分
    if side == "cancel" or status in _CANCELLED_STATUSES:
        return "cancelled"
    filled, total = _num("filled_volume"), _num("total_volume")
    if status == "filled" or (filled > 0 and filled >= total > 0):
        return "filled"
    if status in _DEAD_STATUSES:
        return "dead"
    if status in _ACTIVE_STATUSES:
        return "active"
    return "unknown"


async def verify_cancel_terminal(
    query_one: Callable[[], Awaitable[dict]],
    *,
    attempts: int | None = None,
    delay: float | None = None,
) -> tuple[str, dict]:
    """轮询当日委托终态。``query_one``: async () -> 归一化委托 dict（找不到给 {}）。

    返回 ``(state, order)``；state ∈ cancelled/filled/dead/active/unknown。
    ``active`` = 多次查询都成功但仍在途（真未撤掉）；``unknown`` = 查询一直
    不可用（撤单结果未知）。查询异常不视为撤单失败，继续轮询。
    """
    attempts = CANCEL_VERIFY_ATTEMPTS if attempts is None else max(1, int(attempts))
    interval = CANCEL_VERIFY_DELAY_S if delay is None else max(0.0, float(delay))
    last_order: dict = {}
    queried_ok = False
    for i in range(attempts):
        await asyncio.sleep(interval)
        try:
            order = await query_one() or {}
        except Exception as exc:  # noqa: BLE001 - 查询失败≠撤单失败，继续轮询
            logger.warning(
                "[CancelVerify] 终态复核查询失败(第 %s/%s 次): %s", i + 1, attempts, exc
            )
            continue
        queried_ok = True
        last_order = order
        state = classify_order_terminal(order)
        if state not in ("active", "unknown"):
            return state, order
    if not queried_ok:
        return "unknown", {}
    return classify_order_terminal(last_order), last_order
