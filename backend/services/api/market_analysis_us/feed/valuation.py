"""美股估值主题 —— 高股息 / 低估值榜 / 市值分层 / 全市场估值概览。

**数据源是 `2_base_sector/f10` 快照，不是 `5_technical_derived/valuation` 分区。**
后者在 2026-08-28 起曾长期静默写空（字段名 camelCase/snake_case 不匹配），
虽然底层 bug 已修，但 f10 快照本身更完整（含中文名、52 周高低、平均成交量），
且是单点快照、读取更快，故作为主路径。

口径：PE/PB 只取**正值**参与排名（亏损公司的负 PE 会污染低估值榜）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_shared.display import fmt_yi, safe_float
from backend.services.api.market_analysis_shared.market_days import to_iso
from backend.services.api.market_analysis_us.feed.base import (
    _avail,
    _f10_snapshot,
    _hot_snapshot,
    _names,
    _sector_cn,
    _sector_map,
)

_VALUATION_TTL = 1800.0

# ---- 榜单健全性门槛 ----
# f10 是单点快照，个别标的（已被收购/更名的壳、退市残留）的基本面会长期不更新，
# 而其股价仍在交易，导致 PE 0.06、PB 0.01 之类的失真值霸占「低估值榜」
# （实测 PARA 收购残留、SBNY 倒闭残留）。以下门槛把这些剔除，
# 使榜单落在可投资范围内 —— 门槛值与理由写死在这里，避免各处散落魔数。
_MIN_MARKET_CAP = 2e9  # 市值下限 20 亿美元
_PE_MIN = 3.0  # PE 下限：低于 3 几乎必为陈旧快照
_PB_MIN = 0.3  # PB 下限
_MIN_DIVIDEND_YIELD = 0.5  # 股息率下限（%），滤掉象征性派息

# 市值分层阈值（美元）：mega >= 2000 亿 / large >= 100 亿 / mid >= 20 亿 / 其余 small
_SIZE_TIERS = (
    ("mega", "超大盘", 2e11),
    ("large", "大盘", 1e10),
    ("mid", "中盘", 2e9),
    ("small", "小盘", 0.0),
)

_TIER_LABELS = {key: label for key, label, _ in _SIZE_TIERS}
_TIER_KEYS = tuple(_TIER_LABELS)


def _tier_subset(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """按分层键取市值子集：floor(本层) ≤ 市值 < floor(上一层)。

    阈值一律从 `_SIZE_TIERS` 推导，概览与下钻共用同一份口径；
    调用方须保证 `market_cap` 已为数值列。
    """
    idx = _TIER_KEYS.index(key)
    floor = _SIZE_TIERS[idx][2]
    cap = df["market_cap"]
    mask = cap >= floor
    if idx > 0:
        mask &= cap < _SIZE_TIERS[idx - 1][2]
    return df[mask]


def get_valuation_rankings(kind: str = "dividend", limit: int = 20) -> dict[str, Any]:
    """估值主题榜：dividend=高股息 / pe=低PE / pb=低PB。"""
    if kind not in ("dividend", "pe", "pb"):
        kind = "dividend"
    limit = max(5, min(int(limit), 50))

    def _load() -> dict[str, Any]:
        empty = {"kind": kind, "items": []}
        if not _avail():
            return empty
        f10 = _f10_snapshot()
        if f10.empty:
            return empty
        cfg = {
            "dividend": ("dividend_yield", False, "dividend_yield"),
            "pe": ("pe_ratio", True, "pe_ratio"),
            "pb": ("pb_ratio", True, "pb_ratio"),
        }[kind]
        col, ascending, _ = cfg
        df = f10.copy()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
        df = df[df[col].notna() & (df["market_cap"].fillna(0) >= _MIN_MARKET_CAP)]
        if kind in ("pe", "pb"):
            floor = _PE_MIN if kind == "pe" else _PB_MIN
            df = df[df[col] >= floor]
        else:
            df = df[df[col] >= _MIN_DIVIDEND_YIELD]
        if df.empty:
            return empty
        df = df.sort_values(col, ascending=ascending).head(limit)
        df = df.merge(_sector_map(), on="symbol", how="left")
        items = []
        for _, r in df.iterrows():
            items.append(
                {
                    "symbol": r["symbol"],
                    "name": r.get("name") or r["symbol"],
                    "sector": _sector_cn(r.get("sector")),
                    "value": round(safe_float(r[col]), 2),
                    "market_cap_yi": fmt_yi(safe_float(r.get("market_cap"))),
                    "pe_ratio": _opt(r.get("pe_ratio")),
                    "pb_ratio": _opt(r.get("pb_ratio")),
                    "dividend_yield": _opt(r.get("dividend_yield")),
                }
            )
        return {"kind": kind, "items": items}

    return cached(f"us_valuation_rankings:{kind}:{limit}", _load, ttl=_VALUATION_TTL)


def get_size_tiers() -> dict[str, Any]:
    """市值分层：超大盘 / 大盘 / 中盘 / 小盘的估值与表现概览。

    市值取自 f10 快照；分组内的涨跌幅中位数取自最新截面（若可用）。
    """

    def _load() -> dict[str, Any]:
        empty = {"tiers": [], "total_market_cap_yi": 0.0}
        if not _avail():
            return empty
        f10 = _f10_snapshot()
        if f10.empty:
            return empty
        df = f10.copy()
        df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
        df["pe_ratio"] = pd.to_numeric(df["pe_ratio"], errors="coerce")
        df["dividend_yield"] = pd.to_numeric(df["dividend_yield"], errors="coerce")
        df = df[df["market_cap"].fillna(0) > 0]
        if df.empty:
            return empty

        tiers = []
        for key, label, _floor in _SIZE_TIERS:
            sub = _tier_subset(df, key)
            if sub.empty:
                continue
            pe_pos = sub["pe_ratio"].where(sub["pe_ratio"] >= _PE_MIN)
            tiers.append(
                {
                    "key": key,
                    "label": label,
                    "count": int(len(sub)),
                    "market_cap_yi": fmt_yi(float(sub["market_cap"].sum())),
                    "pe_median": _median(pe_pos),
                    "dividend_yield_median": _median(sub["dividend_yield"]),
                }
            )
        return {
            "tiers": tiers,
            "total_market_cap_yi": fmt_yi(float(df["market_cap"].sum())),
            "as_of": "",
        }

    return cached("us_size_tiers", _load, ttl=_VALUATION_TTL)


def get_size_tiers_drill(tier: str, limit: int = 300) -> dict[str, Any] | None:
    """市值分层下钻：某分层成分个股（按市值降序）+ 中位数摘要。

    `tier` 取 /valuation/size-tiers 的 key（mega/large/mid/small），Pydantic 之外的
    语义校验也在这里 —— 未知 key 返回 None，路由层转 404（不静默回落成别的层）。
    涨跌幅取自最新截面（行情侧不可用时为 None，不拖垮估值主数据）；
    PE/PB/股息率给 f10 快照原值（含负值），是否可用于展示由前端按 '--' 缺省处理。
    """
    limit = max(10, min(int(limit), 300))
    key = (tier or "").strip().lower()
    if key not in _TIER_LABELS:
        return None

    def _load() -> dict[str, Any]:
        label = _TIER_LABELS[key]
        empty: dict[str, Any] = {
            "tier": key,
            "label": label,
            "trade_date": "",
            "items": [],
            "summary": {
                "members": 0,
                "shown": 0,
                "truncated": False,
                "total_mv_yi": 0.0,
                "pe_median": None,
                "pb_median": None,
                "dividend_yield_median": None,
                "advance": 0,
                "decline": 0,
                "median_pct": None,
            },
        }
        if not _avail():
            return empty
        f10 = _f10_snapshot()
        if f10.empty:
            return empty
        df = f10.copy()
        for col in ("market_cap", "pe_ratio", "pb_ratio", "dividend_yield"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[df["market_cap"].fillna(0) > 0]
        if df.empty:
            return empty
        sub = _tier_subset(df, key)
        if sub.empty:
            return empty
        members = int(len(sub))
        total_mv = float(sub["market_cap"].sum())
        sub = sub.sort_values("market_cap", ascending=False)

        # 最新截面涨跌幅（附加信息）：行情侧缺日不阻塞估值下钻
        trade_date = ""
        pct_by_symbol: dict[str, float] = {}
        try:
            latest, snap = _hot_snapshot()
            trade_date = to_iso(str(latest))
            sp = snap[["symbol", "pct_change"]].dropna(subset=["pct_change"])
            pct_by_symbol = {
                str(s): float(p)
                for s, p in zip(sp["symbol"], sp["pct_change"], strict=False)
            }
        except Exception:  # noqa: BLE001 —— 见上：涨跌幅缺失只是 None，不算失败
            trade_date = ""

        merged = sub.merge(_sector_map(), on="symbol", how="left")
        top = merged.head(limit)
        name_map = _names([str(s) for s in top["symbol"]])
        items = []
        for _, r in top.iterrows():
            sym = str(r["symbol"])
            cn = name_map.get(sym)
            # 中文名优先（security_master 口径）；无中文名才回落 f10 的英文长名
            display = cn if cn and cn != sym else (r.get("name") or sym)
            cap = safe_float(r.get("market_cap"), 0.0) or 0.0
            items.append(
                {
                    "symbol": sym,
                    "name": display,
                    "sector": _sector_cn(r.get("sector")),
                    "market_cap_yi": fmt_yi(cap),
                    "pe_ratio": _opt(r.get("pe_ratio")),
                    "pb_ratio": _opt(r.get("pb_ratio")),
                    "dividend_yield": _opt(r.get("dividend_yield")),
                    "pct_change": (
                        round(pct_by_symbol[sym], 2) if sym in pct_by_symbol else None
                    ),
                }
            )

        # 涨跌家数 / 中位涨跌幅按**整层**统计（items 只截前 limit 只，
        # 拿它统计会把摘要说成"这一页"的涨跌，与 members 口径打架）
        all_pcts = [
            pct_by_symbol[str(s)] for s in sub["symbol"] if str(s) in pct_by_symbol
        ]
        pe_pos = sub["pe_ratio"].where(sub["pe_ratio"] >= _PE_MIN)
        pb_pos = sub["pb_ratio"].where(sub["pb_ratio"] >= _PB_MIN)
        return {
            "tier": key,
            "label": label,
            "trade_date": trade_date,
            "items": items,
            "summary": {
                "members": members,
                "shown": len(items),
                "truncated": members > len(items),
                "total_mv_yi": fmt_yi(total_mv),
                "pe_median": _median(pe_pos),
                "pb_median": _median(pb_pos),
                "dividend_yield_median": _median(sub["dividend_yield"]),
                "advance": sum(1 for p in all_pcts if p > 0),
                "decline": sum(1 for p in all_pcts if p < 0),
                "median_pct": _median(pd.Series(all_pcts)) if all_pcts else None,
            },
        }

    return cached(f"us_size_tiers_drill:{key}:{limit}", _load, ttl=_VALUATION_TTL)


def get_valuation_overview() -> dict[str, Any]:
    """全市场估值概览：PE/PB/股息率中位数 + 行业分布极值。

    中位数只取有效正值；分位数（25/50/75）用于判断当前估值
    在标的池内部的相对位置。
    """

    def _load() -> dict[str, Any]:
        empty = {
            "coverage": 0,
            "pe_median": None,
            "pe_p25": None,
            "pe_p75": None,
            "pb_median": None,
            "dividend_yield_median": None,
            "dividend_payers": 0,
        }
        if not _avail():
            return empty
        f10 = _f10_snapshot()
        if f10.empty:
            return empty
        df = f10.copy()
        pe = pd.to_numeric(df["pe_ratio"], errors="coerce")
        pb = pd.to_numeric(df["pb_ratio"], errors="coerce")
        dy = pd.to_numeric(df["dividend_yield"], errors="coerce")
        pe_pos = pe[pe >= _PE_MIN]
        pb_pos = pb[pb >= _PB_MIN]
        return {
            "coverage": int(len(df)),
            "pe_median": _median(pe_pos),
            "pe_p25": _quantile(pe_pos, 0.25),
            "pe_p75": _quantile(pe_pos, 0.75),
            "pb_median": _median(pb_pos),
            "dividend_yield_median": _median(dy[dy >= _MIN_DIVIDEND_YIELD]),
            "dividend_payers": int((dy >= _MIN_DIVIDEND_YIELD).sum()),
            "as_of": "",
        }

    return cached("us_valuation_overview", _load, ttl=_VALUATION_TTL)


def _median(series: pd.Series) -> float | None:
    vals = series.dropna()
    if vals.empty:
        return None
    return round(float(vals.median()), 2)


def _quantile(series: pd.Series, q: float) -> float | None:
    vals = series.dropna()
    if vals.empty:
        return None
    return round(float(vals.quantile(q)), 2)


def _opt(value: Any) -> float | None:
    out = safe_float(value, None)
    return round(out, 2) if out is not None else None
