# mypy: disable-error-code=untyped-decorator

"""期货市场分析 API Router — 行情总览 / 板块热力 / 期限结构 / 持仓分析 /
仓单库存 / 内外盘与期现价差。

端点前缀：`/api/v1/market-analysis-futures`
结构对标美股 `market_analysis_us/router.py`（REST 同步 + SSE 流式重算），
数据层由 `feed/` 下按域拆分的模块驱动。

口径提示（页面必须如实呈现，勿用股票习惯误读）：
- **主力连续**（`RB0.CN` 等）含换月跳空，单日涨跌幅可能混入移仓缺口
- 成交额/持仓额为**名义口径**（价格 × 手数 × 合约乘数），非真实资金流水
- 国际品种与上金所**无量仓数据**，对应字段为 null（前端显示 —，不是 0）
- 仓单覆盖所随数据源可用性变动，以 `/status` 的 `receipts_venues` 为准
- 当日凌晨同步先落国际夜盘品种，「完整交易日」以国内品种齐全的分区为准
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.services.api.market_analysis_futures.feed import (
    inventory,
    overview,
    positions,
    sectors,
    spread,
    term,
)
from backend.services.api.market_analysis_futures.feed.base import (
    clear_cache_futures,
    feed_status,
)
from backend.services.api.user_app.middleware.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/market-analysis-futures", tags=["Market Analysis Futures"]
)


def _fatal(exc: Exception) -> HTTPException:
    logger.exception("[market-analysis-futures] 请求失败")
    return HTTPException(status_code=500, detail=f"期货市场分析失败: {exc}")


# ---- 诊断 ----


@router.get("/status")
async def get_status(current_user: dict = Depends(get_current_user)) -> dict:
    """数据可用性、各数据集最新日期与口径提示（前端诊断 & 面板日期标注）。"""
    _ = current_user
    return feed_status()


# ---- Tab 1 行情总览 ----


@router.get("/overview")
async def get_market_overview(current_user: dict = Depends(get_current_user)) -> dict:
    """全市场统计卡：涨跌家数 / 名义成交额与持仓额 / 首尾板块（国内口径）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(overview.get_overview)
    except Exception as exc:
        raise _fatal(exc) from exc


@router.get("/ranking")
async def get_ranking(
    kind: str = Query(
        default="change", pattern="^(change|turnover|oi_amount|oi_change)$"
    ),
    sector: str | None = Query(default=None),
    limit: int = Query(default=20, ge=5, le=100),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """榜单：change=涨跌幅双榜 / turnover=成交额 / oi_amount=持仓额 / oi_change=增减仓。"""
    _ = current_user
    try:
        return await asyncio.to_thread(overview.get_ranking, kind, sector, limit)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- Tab 2 板块热力 ----


@router.get("/heatmap")
async def get_heatmap(
    limit: int | None = Query(default=None, ge=5, le=200),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """品种热力图（默认全量，按名义持仓额降序）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(sectors.get_heatmap, limit)
    except Exception as exc:
        raise _fatal(exc) from exc


@router.get("/sector-stats")
async def get_sector_stats(current_user: dict = Depends(get_current_user)) -> dict:
    """9 大板块聚合：涨跌广度 / 中位涨幅 / 成交额与持仓额 / 首尾品种。"""
    _ = current_user
    try:
        return await asyncio.to_thread(sectors.get_sector_stats)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- Tab 3 期限结构 ----


@router.get("/term")
async def get_term_structure(
    symbol: str = Query(min_length=1, max_length=20),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """单品种近远月曲线 + 结构判定 + 主力合约（symbol 接受 AU0/AU0.CN/AU）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(term.get_term_structure, symbol)
    except Exception as exc:
        raise _fatal(exc) from exc


@router.get("/term-ranking")
async def get_term_ranking(
    limit: int = Query(default=40, ge=5, le=100),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """全市场期限结构全景（按 near_far_spread_pct 降序，backwardation 在前）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(term.get_term_ranking, limit)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- Tab 4 持仓分析 ----


@router.get("/cftc")
async def get_cftc(
    limit: int = Query(default=15, ge=5, le=50),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """CFTC 持仓：投机（非商业）vs 商业套保 双榜，净仓位及周度变化。"""
    _ = current_user
    try:
        return await asyncio.to_thread(positions.get_cftc, limit)
    except Exception as exc:
        raise _fatal(exc) from exc


@router.get("/oi-changes")
async def get_oi_changes(
    days: int = Query(default=1, ge=1, le=20),
    limit: int = Query(default=30, ge=5, le=100),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """国内 N 日增减仓四象限（增仓上涨 / 增仓下跌 / 空头回补 / 多头离场）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(positions.get_oi_changes, days, limit)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- Tab 5 仓单库存 ----


@router.get("/receipts")
async def get_receipts(
    variety: str | None = Query(default=None, max_length=10),
    days: int = Query(default=30, ge=5, le=120),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """仓单库存：最新两日增减榜 + （可选 variety）单品种历史。

    覆盖所以 `latest_date` 实际收录者为准（源侧缺数据时如实为空，不落 0）。
    """
    _ = current_user
    try:
        return await asyncio.to_thread(inventory.get_receipts, variety, days)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- Tab 6 内外盘与期现价差 ----


@router.get("/cross-market")
async def get_cross_market(
    days: int = Query(default=60, ge=10, le=250),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """内外盘价差（贵金属/有色/原油 9 组）：逐日汇率折算 + 价差率历史。"""
    _ = current_user
    try:
        return await asyncio.to_thread(spread.get_cross_market, days)
    except Exception as exc:
        raise _fatal(exc) from exc


@router.get("/precious-basis")
async def get_precious_basis(
    days: int = Query(default=60, ge=10, le=250),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """贵金属期现基差：上金所现货 vs 沪期主力（收盘对收盘）。"""
    _ = current_user
    try:
        return await asyncio.to_thread(spread.get_precious_basis, days)
    except Exception as exc:
        raise _fatal(exc) from exc


# ---- 手动刷新 ----


def _sse(event: str, data: Any) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


_REFRESH_STEPS = [
    ("overview", overview.get_overview),
    ("ranking", overview.get_ranking, "change", None, 20),
    ("heatmap", sectors.get_heatmap, None),
    ("sector_stats", sectors.get_sector_stats),
    ("term_ranking", term.get_term_ranking, 40),
    ("cftc", positions.get_cftc, 15),
    ("oi_changes", positions.get_oi_changes, 1, 30),
    ("receipts", inventory.get_receipts, None, 30),
    ("cross_market", spread.get_cross_market, 60),
    ("precious_basis", spread.get_precious_basis, 60),
]


@router.post("/refresh")
async def trigger_refresh(current_user: dict = Depends(get_current_user)) -> dict:
    """清缓存并重新计算全部期货市场分析数据（同步返回汇总）。"""
    _ = current_user
    try:
        clear_cache_futures()
        ov = await asyncio.to_thread(overview.get_overview)
        return {
            "status": "success",
            "trade_date": ov.get("trade_date") or "",
            "total_symbols": (ov.get("totals") or {}).get("total", 0),
            "message": "期货市场分析缓存已刷新",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        raise _fatal(exc) from exc


@router.post("/refresh/stream")
async def trigger_refresh_stream(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """SSE 流式重算：清缓存后按步骤推送各板块，客户端边收边渲染。

    steps: start -> overview -> ranking -> heatmap -> sector_stats
    -> term_ranking -> cftc -> oi_changes -> receipts -> cross_market
    -> precious_basis -> done
    """
    _ = current_user

    async def event_stream():
        yield _sse("start", {"message": "开始刷新期货市场分析…"})
        try:
            await asyncio.to_thread(clear_cache_futures)
            for name, func, *args in _REFRESH_STEPS:
                result = await asyncio.to_thread(func, *args)
                if await request.is_disconnected():
                    return
                yield _sse(name, result)

            ov = await asyncio.to_thread(overview.get_overview)
            yield _sse(
                "done",
                {
                    "trade_date": ov.get("trade_date") or "",
                    "message": "期货市场分析刷新完成",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("[market-analysis-futures][stream] 刷新失败")
            yield _sse("error", {"message": f"期货市场分析刷新失败: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
