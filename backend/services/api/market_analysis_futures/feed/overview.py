"""期货行情总览 —— 全市场统计卡 + 涨跌/成交额/持仓额/增减仓榜单。

口径（页面需如实标注）：
- 榜单基于**主力连续**（含换月跳空，属数据形态非行情）
- 成交额/持仓额为**名义口径** = 价格 × 手数 × 合约乘数；国际品种与
  上金所无成交量/持仓量数据 → 对应字段为 null（显示 —，不落 0）
"""

from __future__ import annotations

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _cross_section,
    _enrich,
    _num,
    _yi,
    to_iso,
)
from backend.shared.futures_universe import SECTORS

_RANKING_SCORE = {
    "change": "pct_change",
    "turnover": "turnover",
    "oi_amount": "oi_amount",
    "oi_change": "oi_change",
}


def _row(r: pd.Series) -> dict:
    """榜单行（缺失一律 null，绝不落 0）。"""
    return {
        "symbol": str(r["symbol"]),
        "name": str(r["name"]),
        "sector": str(r["sector"]),
        "sector_name": SECTORS.get(str(r["sector"]), str(r["sector"])),
        "exchange": str(r["exchange"]),
        "unit": str(r["unit"]),
        "close": _num(r["close"]),
        "pct_change": _num(r.get("pct_change")),
        "volume_wan": _num(pd.to_numeric(r.get("volume"), errors="coerce") / 1e4),
        "oi_wan": _num(pd.to_numeric(r.get("position"), errors="coerce") / 1e4),
        "oi_change_wan": _num(pd.to_numeric(r.get("oi_change"), errors="coerce") / 1e4),
        "turnover_yi": _yi(pd.to_numeric(r.get("turnover"), errors="coerce")),
        "oi_amount_yi": _yi(pd.to_numeric(r.get("oi_amount"), errors="coerce")),
    }


def _group_stat(sub: pd.DataFrame) -> dict:
    valid = pd.to_numeric(sub["pct_change"], errors="coerce").dropna()
    turnover = pd.to_numeric(sub["turnover"], errors="coerce")
    oi_amount = pd.to_numeric(sub["oi_amount"], errors="coerce")
    oi_change = pd.to_numeric(sub["oi_change"], errors="coerce")
    volume = pd.to_numeric(sub["volume"], errors="coerce")
    return {
        "total": int(len(sub)),
        "up": int((valid > 0).sum()),
        "down": int((valid < 0).sum()),
        "flat": int((valid == 0).sum()),
        "median_pct": _num(valid.median()),
        "active": int((volume.fillna(0) > 0).sum()),
        # min_count=1：全组无量仓数据（如国际品种）时 sum 应为 NaN→null，
        # 默认 skipna 会给 0.0 冒名「成交额为零」
        "turnover_yi": _yi(turnover.sum(min_count=1)),
        "oi_amount_yi": _yi(oi_amount.sum(min_count=1)),
        "oi_change_wan": _num(oi_change.sum(min_count=1) / 1e4),
    }


def get_overview() -> dict:
    """全市场统计卡：涨跌家数 / 中位涨幅 / 名义成交额与持仓额 / 首尾板块。"""

    def _load() -> dict:
        trade_date, snap = _cross_section()
        if not trade_date or snap.empty:
            return {"trade_date": None, "totals": {}, "domestic": {}, "groups": []}
        df = _enrich(snap)
        syms = df["symbol"].astype(str)
        domestic = df[syms.str.endswith(".CN")]
        intl = df[syms.str.endswith(".FUT")]
        sge = df[~(syms.str.endswith(".CN") | syms.str.endswith(".FUT"))]

        # 板块中位数（仅国内；国际品种板块分类粗糙，不进首尾板块）
        sector_rows = []
        for sec, sub in domestic.groupby("sector"):
            med = pd.to_numeric(sub["pct_change"], errors="coerce").median()
            if pd.notna(med):
                sector_rows.append((str(sec), float(med)))
        sector_rows.sort(key=lambda x: x[1], reverse=True)

        def _sector_entry(item: tuple[str, float]) -> dict:
            return {
                "sector": item[0],
                "name": SECTORS.get(item[0], item[0]),
                "median_pct": _num(item[1]),
            }

        return {
            "trade_date": to_iso(trade_date),
            "totals": _group_stat(df),
            "domestic": _group_stat(domestic),
            "international": _group_stat(intl),
            "sge": _group_stat(sge),
            "top_sector": _sector_entry(sector_rows[0]) if sector_rows else None,
            "bottom_sector": _sector_entry(sector_rows[-1]) if sector_rows else None,
        }

    return cached("futures_overview", _load)


def get_ranking(kind: str, sector: str | None = None, limit: int = 20) -> dict:
    """榜单：kind=change 返回涨幅/跌幅双榜；其余返回单榜（含国内仅有持仓品种）。"""
    col = _RANKING_SCORE.get(kind)
    if col is None:
        raise ValueError(f"kind 必须是 {'/'.join(_RANKING_SCORE)}，收到 {kind}")

    def _load() -> dict:
        trade_date, snap = _cross_section()
        if not trade_date or snap.empty:
            return {
                "trade_date": None,
                "kind": kind,
                "gainers": [],
                "losers": [],
                "rows": [],
            }
        df = _enrich(snap)
        if sector:
            df = df[df["sector"] == sector]
        df = df.dropna(subset=[col])

        def _top(sub: pd.DataFrame, ascending: bool) -> list[dict]:
            s = sub.sort_values(col, ascending=ascending).head(limit)
            return [_row(r) for _, r in s.iterrows()]

        if kind == "change":
            return {
                "trade_date": to_iso(trade_date),
                "kind": kind,
                "gainers": _top(df, ascending=False),
                "losers": _top(df, ascending=True),
                "rows": [],
            }
        return {
            "trade_date": to_iso(trade_date),
            "kind": kind,
            "gainers": [],
            "losers": [],
            "rows": _top(df, ascending=False),
        }

    key = f"futures_ranking:{kind}:{sector or 'all'}:{limit}"
    return cached(key, _load)
