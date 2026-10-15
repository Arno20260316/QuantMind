"""集合竞价取数路由（2026-10-15）。

- GET /call-auction/status        任务心跳/体检（档期、名单数、行数、错误）
- GET /call-auction/board?day=    当日竞价板（前缀码 → 每票摘要 + 逐笔可选）

数据由 trade 常驻任务 ``call_auction_watch`` 按档采样写入交易库 Redis
（``qm:call_auction:{day}`` Hash / ``qm:call_auction:status``）。读侧只读该键，
**不做重算**；逐笔全量在归档 ``/data/call_auction/date=YYYYMMDD/``。
"""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.services.trade_shared.deps import AuthContext, get_auth_context
from backend.services.live_trading.services.call_auction_watch import (
    BOARD_KEY_TPL,
    STATUS_KEY,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _read_auction_redis(day: str, with_rows: bool) -> dict:
    """同步 Redis 读（放线程跑）：状态 + 当日板。"""
    from backend.services.trade_shared.deps import get_redis

    client = getattr(get_redis(), "client", None)
    if client is None:
        raise RuntimeError("交易 Redis 不可用")

    out: dict = {"day": day}
    raw_status = client.get(STATUS_KEY)
    out["status"] = json.loads(raw_status) if raw_status else None

    board_key = BOARD_KEY_TPL.format(day=day)
    rows_raw = client.hgetall(board_key)
    items = []
    for field, value in sorted(rows_raw.items()):
        field_s = field.decode() if isinstance(field, bytes) else str(field)
        value_s = value.decode() if isinstance(value, bytes) else str(value)
        try:
            item = json.loads(value_s)
        except (TypeError, ValueError):
            continue
        item["symbol"] = field_s
        if not with_rows:
            item.pop("rows", None)
        items.append(item)
    out["board"] = items
    out["total"] = len(items)
    return out


@router.get("/call-auction/status")
async def get_call_auction_status(auth: AuthContext = Depends(get_auth_context)):
    """集合竞价任务状态：最近一档、名单数（持仓/候选分列）、错误与归档路径。"""
    day = datetime.now().strftime("%Y%m%d")
    try:
        data = await asyncio.to_thread(_read_auction_redis, day, False)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    status = data["status"]
    return {
        "status": status,
        "board_day": day,
        "board_size": data["total"],
        "server_time": datetime.now().isoformat(),
    }


@router.get("/call-auction/board")
async def get_call_auction_board(
    day: str | None = Query(
        None, pattern=r"^\d{8}$", description="交易日 YYYYMMDD，默认今天"
    ),
    with_rows: bool = Query(
        False, description="是否携带逐笔全量（rows）；默认只给摘要"
    ),
    auth: AuthContext = Depends(get_auth_context),
):
    """当日集合竞价板：field=前缀码，value=名称/角色/摘要（可选全量逐笔）。

    摘要为**该档采样时点的末帧**（09:25:05 档=当日早盘定稿；15:00:05 档含尾盘）。
    注意「日期盲」：数据由档期任务在 09:15 后写入，本读侧只透传不当日校验。
    """
    target = str(day or datetime.now().strftime("%Y%m%d"))
    try:
        return await asyncio.to_thread(_read_auction_redis, target, with_rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
