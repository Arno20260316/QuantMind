"""通达信桥采集状态/清单路由（批次 1，2026-10-15）。

- GET /tdx/ingest/status    档位时间线/下一档/预算用量/各数据集最近一轮/阻塞集/最近台账
- GET /tdx/ingest/datasets  数据集清单：声明元数据 + PG 行数/最新业务日 + 归档根

数据由 trade 常驻任务 ``tdx_ingest`` 写交易库 Redis（db2，``qm:tdx_ingest:*``）
与 PG（``tdx_ingest_runs``/``tdx_ingest_cursor``）；本路由**只读**，不重算、
不触发（手动触发 = admin 写 Redis 键 ``qm:tdx_ingest:run_once:{dataset}``，
常驻循环 60s 内认领；admin 写侧随批次 2 交付）。

闸门：本前缀登记在 ``live_trading_gate._MARKET_DATA_PREFIXES``——采集状态属于
行情数据面，实盘开关关闭时同样可见（与 ``/tdx/l2`` 同口径）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from backend.services.live_trading.services.tdx_ingest import sinks
from backend.services.live_trading.services.tdx_ingest.datasets import (
    DATASETS,
    all_slots,
)
from backend.services.live_trading.services.tdx_ingest.rate_budget import _minute_key
from backend.services.live_trading.services.tdx_ingest.task import (
    BLOCKED_KEY_FMT,
    STATUS_KEY,
    next_slot_at,
)
from backend.services.live_trading.services.trading_session import TZ
from backend.services.trade.services.daily_pnl_report_task import _env_bool
from backend.services.trade_shared.deps import AuthContext, get_auth_context

logger = logging.getLogger(__name__)
router = APIRouter()


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _json_or_none(raw) -> dict | list | None:
    if raw is None:
        return None
    text = _decode(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _read_ingest_redis() -> dict:
    """同步 Redis 读（放线程跑）：状态 Hash + blocked 键 + 本分钟计数。"""
    from backend.services.trade_shared.deps import get_redis

    client = getattr(get_redis(), "client", None)
    if client is None:
        raise RuntimeError("交易 Redis 不可用")

    status_raw = client.hgetall(STATUS_KEY) or {}
    status = {_decode(k): _decode(v) for k, v in status_raw.items()}

    blocked: dict[str, str] = {}
    for spec in DATASETS:
        msg = client.get(BLOCKED_KEY_FMT.format(key=spec.key))
        if msg:
            blocked[spec.key] = _decode(msg)

    minute_raw = client.get(_minute_key())
    try:
        minute_used = int(minute_raw) if minute_raw is not None else 0
    except (TypeError, ValueError):
        minute_used = 0

    return {"status": status, "blocked": blocked, "minute_used": minute_used}


@router.get("/tdx/ingest/status")
async def get_tdx_ingest_status(auth: AuthContext = Depends(get_auth_context)):
    """采集状态总览：档位/下一档/预算/数据集最近一轮/阻塞/最近 20 条台账。"""
    now = datetime.now(TZ)
    try:
        data = await asyncio.to_thread(_read_ingest_redis)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    status = data["status"]
    runs = await sinks.recent_runs(limit=20)
    budget = _json_or_none(status.get("budget"))
    if not isinstance(budget, dict):
        budget = {}
    return {
        "server_time": now.isoformat(timespec="seconds"),
        "enabled": _env_bool("TDX_INGEST_ENABLED", True),
        "slots": list(all_slots()),
        "next_slot_at": next_slot_at(now).isoformat(timespec="minutes"),
        "status_ts": status.get("ts", ""),
        "budget": {**budget, "used_this_minute": data["minute_used"]},
        "datasets": _json_or_none(status.get("datasets")) or {},
        "slot_last": _json_or_none(status.get("slot_last")),
        "blocked": data["blocked"],
        "recent_runs": runs,
        "rate_limited_recent": sum(
            1 for r in runs if r.get("status") == "rate_limited"
        ),
    }


@router.get("/tdx/ingest/datasets")
async def get_tdx_ingest_datasets(auth: AuthContext = Depends(get_auth_context)):
    """数据集清单：声明元数据 + PG 行数/最新业务日 + 归档根（面板轮询面）。"""
    blocked: dict[str, str] = {}
    try:
        blocked = (await asyncio.to_thread(_read_ingest_redis))["blocked"]
    except RuntimeError:
        pass  # Redis 不可用只丢 blocked 标注，PG 面照给

    items = []
    for spec in DATASETS:
        stats = await sinks.table_stats(spec.table)
        items.append(
            {
                "key": spec.key,
                "name": spec.name,
                "method": spec.method,
                "table": spec.table,
                "slots": list(spec.slots),
                "batch_size": spec.batch_size,
                "est_calls": spec.est_calls,
                "blocked_family": spec.blocked_family,
                "enabled": spec.enabled,
                "suspended_reason": spec.suspended_reason,
                "fields": len(spec.fields),
                "desc": spec.desc,
                "pg_rows": stats["rows"],
                "pg_latest_date": stats["latest_date"],
                "blocked": blocked.get(spec.key, ""),
            }
        )
    return {
        "datasets": items,
        "archive_base": str(sinks.archive_base()),
        "server_time": datetime.now(TZ).isoformat(timespec="seconds"),
    }
