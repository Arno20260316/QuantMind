"""现货基差 —— 生意社现货 vs 期货（52 品种全景 + 单品种历史）。

口径：
- 基差率 = (现货 − 盘面) / **现货**（源侧口径，正 = 现货升水）
- `dom_pct_250` = 品种**自身**近 250 个交易日 dom_basis_rate 的时序分位
  （正基差历史位置），样本不足 60 日不出分位（「—」显形）
- 期限溢价的地面真值：现货升水通常对应近端偏紧、back 结构更可能延续
- 源侧噪声：个别品种现货报价与盘面脱钩（如小品种），时序分位天然稳健化
"""

from __future__ import annotations

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _num,
    _rank_pct,
    _spot_basis,
    to_iso,
)
from backend.shared.futures_universe import CN_MAIN, SECTORS, symbol_meta

# 分位最少样本（不足则不出分位——短样本分位没有意义）
_MIN_PCT_N = 60

_STEM_NAME: dict[str, str] = {k[:-1]: v for k, v in CN_MAIN.items()}


def _meta_of(stem: str) -> dict:
    meta = symbol_meta(f"{stem}0.CN")
    return {
        "name": _STEM_NAME.get(stem, meta["name"]),
        "sector": meta["sector"],
        "sector_name": SECTORS.get(meta["sector"], meta["sector"]),
    }


def get_basis(symbol: str | None = None, limit: int = 60, days: int = 500) -> dict:
    """现货基差全景：最新基差率榜（含自身 250d 时序分位）+（可选）单品种历史。

    symbol 为品种 stem（如 AU / au / AU0 均可）；历史返回近 2 年逐日点。
    """

    def _load() -> dict:
        df = _spot_basis(days)
        if df.empty or "dom_basis_rate" not in df.columns:
            return {
                "as_of": None,
                "rows": [],
                "variety_count": 0,
                "history": None,
                "notes": ["spot_basis 数据不可用"],
            }
        latest = str(df["dt"].max())
        rows = []
        for sym, sub in df.groupby("symbol"):
            valid = sub.dropna(subset=["dom_basis_rate"])
            if valid.empty:
                continue
            tail = valid.tail(250)
            latest_row = valid.iloc[-1]
            pct = (
                _rank_pct(tail["dom_basis_rate"]).iloc[-1]
                if len(tail) >= _MIN_PCT_N
                else float("nan")
            )
            meta = _meta_of(str(sym))

            def _pct100(v) -> float | None:
                return (
                    _num(pd.to_numeric(v, errors="coerce") * 100.0)
                    if pd.notna(v)
                    else None
                )

            rows.append(
                {
                    "symbol": str(sym),
                    "name": meta["name"],
                    "sector": meta["sector"],
                    "sector_name": meta["sector_name"],
                    "as_of": to_iso(str(latest_row["dt"])),
                    "is_latest": str(latest_row["dt"]) == latest,
                    "spot_price": _num(latest_row.get("spot_price")),
                    "near_contract": str(latest_row.get("near_contract") or "") or None,
                    "near_price": _num(latest_row.get("near_contract_price")),
                    "near_basis_rate_pct": _pct100(latest_row.get("near_basis_rate")),
                    "dom_contract": str(latest_row.get("dominant_contract") or "")
                    or None,
                    "dom_price": _num(latest_row.get("dominant_contract_price")),
                    "dom_basis_rate_pct": _pct100(latest_row.get("dom_basis_rate")),
                    "dom_pct_250": _num(pct, 1) if pd.notna(pct) else None,
                    "sample_days": int(len(tail)),
                }
            )
        rows.sort(
            key=lambda r: (
                r["dom_basis_rate_pct"] is None,
                -(r["dom_basis_rate_pct"] or 0),
            )
        )

        history = None
        if symbol:
            stem = str(symbol).strip().upper().replace(".CN", "")
            sub = df[df["symbol"] == stem].sort_values("dt")
            if not sub.empty:
                history = {
                    "symbol": stem,
                    "name": _meta_of(stem)["name"],
                    "points": [
                        {
                            "date": to_iso(str(r["dt"])),
                            "spot_price": _num(r.get("spot_price")),
                            "dom_price": _num(r.get("dominant_contract_price")),
                            "dom_basis_rate_pct": _num(
                                pd.to_numeric(r.get("dom_basis_rate"), errors="coerce")
                                * 100.0
                            ),
                            "near_basis_rate_pct": _num(
                                pd.to_numeric(r.get("near_basis_rate"), errors="coerce")
                                * 100.0
                            ),
                        }
                        for _, r in sub.iterrows()
                    ],
                }
        return {
            "as_of": to_iso(latest),
            "rows": rows[:limit],
            "variety_count": len(rows),
            "history": history,
            "notes": [
                "基差率 = (现货−盘面)/现货，正 = 现货升水",
                "分位 = 品种自身近 250 交易日的时序分位（样本 < 60 日不出）",
                "源：生意社现货报价（个别品种现期脱钩，分位口径稳健化）",
                "极值多为品种口径差异非机会（如烧碱现货折百价、干湿基差异），"
                "判读以时序分位为准",
            ],
        }

    return cached(f"futures_basis:{symbol or 'all'}:{limit}:{days}", _load, ttl=1800.0)
