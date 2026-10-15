"""期货板块热力 —— 品种热力图（全部 116 品种）+ 9 大板块聚合统计。

热力图面积口径 = 名义持仓额（无持仓数据的国际/上金所品种用成交额或均权，
由前端按 `metric` 决定；本模块只提供原始值）。
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


def get_heatmap(limit: int | None = None) -> dict:
    """全部品种热力数据（默认全量 116 个，treemap 用）。"""

    def _load() -> dict:
        trade_date, snap = _cross_section()
        if not trade_date or snap.empty:
            return {"trade_date": None, "items": []}
        df = _enrich(snap)
        df = df.dropna(subset=["pct_change"])
        df = df.sort_values("oi_amount", ascending=False, na_position="last")
        if limit:
            df = df.head(limit)
        items = []
        for _, r in df.iterrows():
            items.append(
                {
                    "symbol": str(r["symbol"]),
                    "name": str(r["name"]),
                    "sector": str(r["sector"]),
                    "sector_name": SECTORS.get(str(r["sector"]), str(r["sector"])),
                    "exchange": str(r["exchange"]),
                    "pct_change": _num(r["pct_change"]),
                    "close": _num(r["close"]),
                    "oi_amount_yi": _yi(pd.to_numeric(r["oi_amount"], errors="coerce")),
                    "turnover_yi": _yi(pd.to_numeric(r["turnover"], errors="coerce")),
                    "has_oi": bool(pd.notna(r["position"])),
                }
            )
        return {"trade_date": to_iso(trade_date), "items": items}

    return cached(f"futures_heatmap:{limit or 'all'}", _load)


def get_sector_stats() -> dict:
    """9 大板块聚合：涨跌广度 / 中位涨幅 / 名义成交额与持仓额 / 首尾品种。

    只统计**国内品种**（.CN）：国际品种量仓缺失、上金所仅贵金属现货，
    混入会让板块中位数失去可比性（页面注明口径）。
    """

    def _load() -> dict:
        trade_date, snap = _cross_section()
        if not trade_date or snap.empty:
            return {"trade_date": None, "sectors": []}
        df = _enrich(snap)
        df = df[df["symbol"].astype(str).str.endswith(".CN")]
        rows = []
        for sec, sub in df.groupby("sector"):
            pct = pd.to_numeric(sub["pct_change"], errors="coerce")
            valid = sub[pct.notna()]
            if valid.empty:
                continue
            valid = valid.assign(_pct=pct[pct.notna()])
            turnover = pd.to_numeric(sub["turnover"], errors="coerce")
            oi_amount = pd.to_numeric(sub["oi_amount"], errors="coerce")
            oi_change = pd.to_numeric(sub["oi_change"], errors="coerce")
            top = valid.sort_values("_pct", ascending=False).iloc[0]
            bottom = valid.sort_values("_pct", ascending=True).iloc[0]
            rows.append(
                {
                    "sector": str(sec),
                    "name": SECTORS.get(str(sec), str(sec)),
                    "count": int(len(sub)),
                    "up": int((valid["_pct"] > 0).sum()),
                    "down": int((valid["_pct"] < 0).sum()),
                    "median_pct": _num(valid["_pct"].median()),
                    "turnover_yi": _yi(turnover.sum(min_count=1)),
                    "oi_amount_yi": _yi(oi_amount.sum(min_count=1)),
                    "oi_change_wan": _num(oi_change.sum(min_count=1) / 1e4),
                    "leader": {
                        "symbol": str(top["symbol"]),
                        "name": str(top["name"]),
                        "pct_change": _num(top["_pct"]),
                    },
                    "laggard": {
                        "symbol": str(bottom["symbol"]),
                        "name": str(bottom["name"]),
                        "pct_change": _num(bottom["_pct"]),
                    },
                }
            )
        rows.sort(key=lambda x: (x["median_pct"] is None, -(x["median_pct"] or 0)))
        return {"trade_date": to_iso(trade_date), "sectors": rows}

    return cached("futures_sector_stats", _load)
