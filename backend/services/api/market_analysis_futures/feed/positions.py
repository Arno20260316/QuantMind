"""期货持仓分析 —— CFTC 国际持仓（周度）+ 国内增减仓四象限。

口径：
- CFTC：`c` = 非商业（投机）持仓，`merchant_goods` = 商业（套保）持仓；
  周度发布（每周六发上一周二数据），净仓位 = 多头 − 空头（张）
- 国内：N 日持仓变化 = 最新完整交易日持仓量 − N 个交易日前持仓量（手）；
  四象限 = 价格变化方向 × 持仓变化方向的经典期货持仓分析
  （增仓上涨=多头增仓 / 增仓下跌=空头增仓 / 减仓上涨=空头回补 / 减仓下跌=多头离场）
- 排行按 |持仓额变化|（价格 × 手数 × 乘数，元）排序——不同品种手数不可比
"""

from __future__ import annotations

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _cftc_long,
    _enrich,
    _num,
    _series,
    _yi,
    to_iso,
)
from backend.shared.futures_universe import SECTORS, symbol_meta

_QUADRANT_LABELS = {
    "build_long": "多头增仓",
    "build_short": "空头增仓",
    "short_covering": "空头回补",
    "long_unwind": "多头离场",
}


def _cftc_block(kind: str, limit: int) -> dict:
    df = _cftc_long(kind)
    if df.empty:
        return {"report_date": None, "rows": []}
    latest = df["date"].max()
    cur = df[df["date"] == latest]
    prev_date = df[df["date"] < latest]["date"].max()
    prev = (
        df[df["date"] == prev_date][["item", "net"]].rename(columns={"net": "net_prev"})
        if prev_date
        else pd.DataFrame(columns=["item", "net_prev"])
    )
    m = cur.merge(prev, on="item", how="left")
    m["net_change"] = m["net"] - m["net_prev"]
    m = m.reindex(m["net"].abs().sort_values(ascending=False).index).head(limit)
    rows = []
    for _, r in m.iterrows():
        rows.append(
            {
                "item": str(r["item"]),
                "net": _num(r["net"], 0),
                "net_prev": _num(r.get("net_prev"), 0),
                "net_change": _num(r.get("net_change"), 0),
                "long": _num(r.get("long"), 0),
                "short": _num(r.get("short"), 0),
            }
        )
    return {"report_date": latest, "prev_date": prev_date, "rows": rows}


def get_cftc(limit: int = 15) -> dict:
    """CFTC 持仓全景：投机仓 vs 商业仓 双榜（净仓位绝对值降序）。"""

    def _load() -> dict:
        return {
            "speculative": _cftc_block("c", limit),
            "commercial": _cftc_block("merchant_goods", limit),
        }

    return cached(f"futures_cftc:{limit}", _load)


def _quadrant(pct: float, oi_change: float) -> str | None:
    """四象限（教材口径，与模块 docstring 一致）：

    涨势由主动买单推动、跌势由主动卖单推动——持仓方向决定这两类
    主动方是「开仓」还是「平仓」：
    涨+增仓=多头增仓 / 跌+增仓=空头增仓 /
    涨+减仓=空头回补（买平推涨）/ 跌+减仓=多头离场（卖平砸跌）。
    """
    if pd.isna(pct) or pd.isna(oi_change):
        return None
    if oi_change >= 0:
        return "build_long" if pct >= 0 else "build_short"
    return "short_covering" if pct >= 0 else "long_unwind"


def get_oi_changes(days: int = 1, limit: int = 30) -> dict:
    """国内 N 日增减仓四象限（仅国内品种；国际持仓量源不提供）。"""

    def _load() -> dict:
        dates, k = _series(days + 1)
        empty = {
            "trade_date": None,
            "days": days,
            "rows": [],
            "quadrants": dict.fromkeys(_QUADRANT_LABELS, 0),
        }
        if k.empty or len(dates) < 2:
            return empty
        cur_date, base_date = dates[0], dates[-1]
        cols = ["symbol", "close", "position", "volume"]
        cur = k[k["dt"] == cur_date][cols]
        base = k[k["dt"] == base_date][["symbol", "close", "position"]].rename(
            columns={"close": "base_close", "position": "base_position"}
        )
        m = cur.merge(base, on="symbol", how="inner")
        m = m.dropna(subset=["base_close", "base_position", "position", "close"])
        m = m[(m["base_close"] > 0) & (m["base_position"] > 0)]
        if m.empty:
            return empty
        m["pct_change_n"] = (m["close"] / m["base_close"] - 1) * 100
        m["oi_change"] = m["position"] - m["base_position"]
        m["oi_pct_n"] = (m["position"] / m["base_position"] - 1) * 100
        m = _enrich(m)
        mult = m["symbol"].map(lambda s: symbol_meta(str(s))["multiplier"])
        m["oi_amount_change"] = m["oi_change"] * m["close"] * mult
        m = m.dropna(subset=["oi_amount_change"])
        m["quadrant"] = [
            _quadrant(p, c)
            for p, c in zip(m["pct_change_n"], m["oi_change"], strict=True)
        ]
        quadrants = {key: int((m["quadrant"] == key).sum()) for key in _QUADRANT_LABELS}
        m = m.reindex(m["oi_amount_change"].abs().sort_values(ascending=False).index)
        rows = []
        for _, r in m.head(limit).iterrows():
            quad = r["quadrant"]
            rows.append(
                {
                    "symbol": str(r["symbol"]),
                    "name": str(r["name"]),
                    "sector": str(r["sector"]),
                    "sector_name": SECTORS.get(str(r["sector"]), str(r["sector"])),
                    "close": _num(r["close"]),
                    "pct_change_n": _num(r["pct_change_n"]),
                    "oi_wan": _num(r["position"] / 1e4),
                    "oi_change_wan": _num(r["oi_change"] / 1e4),
                    "oi_pct_n": _num(r["oi_pct_n"]),
                    "oi_amount_yi": _yi(r["oi_amount"]),
                    "oi_amount_change_yi": _yi(r["oi_amount_change"]),
                    "quadrant": quad,
                    "quadrant_label": _QUADRANT_LABELS.get(quad) if quad else None,
                }
            )
        return {
            "trade_date": to_iso(cur_date),
            "base_date": to_iso(base_date),
            "days": days,
            "rows": rows,
            "quadrants": quadrants,
        }

    return cached(f"futures_oi_changes:{days}:{limit}", _load)
