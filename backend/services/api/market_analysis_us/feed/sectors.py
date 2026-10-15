"""GICS 板块 —— 热力矩形图 / 多周期轮动 / 板块内宽度 / 板块估值。

行业口径：`2_base_sector/sector/{SYMBOL}.parquet` 的 `sector` 列（GICS 11 大类，
yahoo 源），细分行业在 `industry` 列。**security_master 里没有行业字段**。
516 只覆盖中有 29 只 sector 为空，统一归入「未分类」而不是丢弃。

聚合一律取**中位数**而非均值：日线是未复权原始价，拆股当日的个股会出现
巨大跳变，均值会被单只股票带偏（中位数对异常稳健）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_shared.display import fmt_yi, safe_float
from backend.services.api.market_analysis_shared.market_days import to_iso
from backend.services.api.market_analysis_us.feed.base import (
    INDEX_REL,
    KLINE_REL,
    _avail,
    _dedupe_bars,
    _f10_snapshot,
    _hot_snapshot,
    _index_trading_days,
    _industry_cn,
    _latest_index_date,
    _market_pct_snapshot,
    _names,
    _read_partitioned,
    _sector_cn,
    _sector_map,
    _trading_days,
)

# 轮动周期（交易日）
_ROTATION_WINDOWS = (1, 5, 20, 60)
_SECTOR_TTL = 900.0
# 下钻个股层上限（对齐 A 股 MAX_DRILL_ITEMS 约定）
_MAX_DRILL_ITEMS = 300


def _sector_returns(window: int = 60) -> pd.DataFrame:
    """各标的在 1/5/20/60 日窗口的收益率（中位数聚合用）。

    返回 DataFrame[symbol, ret_1d, ret_5d, ret_20d, ret_60d]。
    需读 window+5 个交易日以保证最长窗口有前收。
    """
    need = max(_ROTATION_WINDOWS) + 5
    need = max(need, window + 1)
    days = _trading_days(None, need)
    if not days:
        return pd.DataFrame()
    k = _read_partitioned(KLINE_REL, days, columns="symbol, dt, close")
    if k.empty:
        return pd.DataFrame()
    k = _dedupe_bars(k, ["symbol", "dt"])
    k["dt"] = k["dt"].astype(str)
    k = k[k["close"].fillna(0) > 0]
    mat = k.pivot_table(index="dt", columns="symbol", values="close").sort_index()
    if len(mat) < 2:
        return pd.DataFrame()

    out = pd.DataFrame(index=mat.columns)
    for w in _ROTATION_WINDOWS:
        if len(mat) <= w:
            out[f"ret_{w}d"] = None
            continue
        base = mat.iloc[-1 - w]
        valid = base.notna() & (base > 0)
        out[f"ret_{w}d"] = ((mat.iloc[-1] / base - 1) * 100).where(valid)
    out.index.name = "symbol"
    return out.reset_index()


def _sector_frame() -> pd.DataFrame:
    """标的 × 行业 × 多周期收益的合并表（板块各视图共用）。"""

    def _load() -> pd.DataFrame:
        rets = _sector_returns()
        if rets.empty:
            return pd.DataFrame()
        smap = _sector_map()
        if smap.empty:
            return pd.DataFrame()
        return rets.merge(smap, on="symbol", how="inner")

    return cached("us:sector_frame", _load, ttl=_SECTOR_TTL)


def get_sector_heatmap(limit: int = 40) -> list[dict[str, Any]]:
    """GICS 板块热力图：中位涨幅 / 总市值（面积）/ 领涨龙头。

    面积口径 = 板块成分总市值（亿美元），与下钻个股层（面积=个股市值）
    父子面积自洽；市值全缺的板块回落成交额，`value_basis` 如实标注。
    对齐热力图组件的数据形状（name/value/pct_change/leader/leader_pct），
    与 A 股、港股共用同一个 treemap 组件。
    """
    limit = max(5, min(int(limit), 80))

    def _load() -> list[dict[str, Any]]:
        if not _avail():
            return []
        latest, snap = _market_pct_snapshot()
        if not latest or snap.empty:
            return []
        smap = _sector_map()
        if smap.empty:
            return []
        df = snap.merge(smap, on="symbol", how="inner")
        f10 = _f10_snapshot()
        if not f10.empty and "market_cap" in f10.columns:
            df = df.merge(
                f10[["symbol", "market_cap"]].drop_duplicates("symbol"),
                on="symbol",
                how="left",
            )
        else:
            df["market_cap"] = None
        name_map = _names(df["symbol"].tolist())

        items: list[dict[str, Any]] = []
        for sector, g in df.groupby("sector", sort=False):
            amount = float(g["amount"].fillna(0).sum())
            mv_sum = float(
                pd.to_numeric(g["market_cap"], errors="coerce").fillna(0).sum()
            )
            if amount <= 0 and mv_sum <= 0:
                continue
            top = g.sort_values("pct_change", ascending=False).iloc[0]
            basis = "market_cap" if mv_sum > 0 else "amount"
            items.append(
                {
                    "name": _sector_cn(sector),
                    "sector": sector,
                    "value": (
                        round(mv_sum / 1e8, 1)
                        if basis == "market_cap"
                        else fmt_yi(amount)
                    ),
                    "value_basis": basis,
                    "market_cap_yi": round(mv_sum / 1e8, 1) if mv_sum > 0 else None,
                    "amount_yi": fmt_yi(amount),
                    "pct_change": round(float(g["pct_change"].median()), 2),
                    "leader": name_map.get(top["symbol"], top["symbol"]),
                    "leader_symbol": top["symbol"],
                    "leader_pct": round(safe_float(top["pct_change"]), 2),
                    "stock_count": int(len(g)),
                    "advance_count": int((g["pct_change"] > 0).sum()),
                }
            )
        items.sort(key=lambda x: x["value"], reverse=True)
        return items[:limit]

    return cached(f"us_sector_heatmap:{limit}", _load)


def get_sector_drill(
    sector: str, limit: int = _MAX_DRILL_ITEMS
) -> dict[str, Any] | None:
    """板块下钻：成分个股热力矩形图（面积=总市值，颜色=涨跌幅）。

    `sector` 为英文 GICS 类名（热力图 `sector` 字段原样透传），"Unknown" 为
    未分类桶。板块名不在分类表 → 返回 ``None``（router → 404），非空结果带
    `industry`（细分行业英文）/`industry_cn`（中译展示）供 tooltip 与个股画像。

    与 A 股 `/heatmap/drill` 契约对齐：items[symbol/name/value/pct_change/
    is_leader] + summary（advance/decline/flat/avg_pct/total_mv_yi），前端
    共用同一套下钻交互。个股层市值缺失回落成交额（`value_basis="amount"`），
    超过 :data:`_MAX_DRILL_ITEMS` 按市值降序截断并在 summary 如实披露。
    """
    limit = max(10, min(int(limit), _MAX_DRILL_ITEMS))
    sector = str(sector or "").strip()
    if not sector:
        return None
    smap = _sector_map()
    if smap.empty or sector not in set(smap["sector"]):
        return None

    def _load() -> dict[str, Any]:
        empty_summary = {
            "members": 0,
            "shown": 0,
            "truncated": False,
            "advance": 0,
            "decline": 0,
            "flat": 0,
            "avg_pct": 0.0,
            "median_pct": 0.0,
            "leader": None,
            "leader_symbol": None,
            "leader_pct": None,
            "total_mv_yi": None,
            "total_amount_yi": None,
        }
        latest, snap = _hot_snapshot()
        if not latest or snap.empty:
            return {
                "trade_date": None,
                "sector": sector,
                "sector_cn": _sector_cn(sector),
                "items": [],
                "summary": empty_summary,
            }
        g = snap.merge(smap[smap["sector"] == sector], on="symbol", how="inner")
        if g.empty:
            return {
                "trade_date": to_iso(str(latest)),
                "sector": sector,
                "sector_cn": _sector_cn(sector),
                "items": [],
                "summary": empty_summary,
            }

        f10 = _f10_snapshot()
        mv_map: dict[str, float] = {}
        f10_name: dict[str, str] = {}
        if not f10.empty:
            for r in f10.drop_duplicates("symbol").itertuples(index=False):
                mv = safe_float(getattr(r, "market_cap", None))
                if mv > 0:
                    mv_map[str(r.symbol)] = mv
                nm = str(getattr(r, "name", "") or "").strip()
                if nm and nm.lower() != "nan":
                    f10_name[str(r.symbol)] = nm
        name_map = _names(g["symbol"].tolist())

        rows: list[dict[str, Any]] = []
        mv_total = 0.0
        amount_total = 0.0
        for r in g.itertuples(index=False):
            sym = str(r.symbol)
            mv = mv_map.get(sym, 0.0)
            amount = safe_float(getattr(r, "amount", 0.0))
            mv_total += mv
            amount_total += amount
            basis = "market_cap" if mv > 0 else "amount"
            value = (mv if mv > 0 else amount) / 1e8
            rv = getattr(r, "rvol", None)
            rvol = round(float(rv), 2) if rv is not None and rv == rv else None
            cn = name_map.get(sym, sym)
            if cn == sym and f10_name.get(sym):
                cn = f10_name[sym]
            industry = str(getattr(r, "industry", "") or "")
            rows.append(
                {
                    "symbol": sym,
                    "name": cn,
                    "industry": industry,
                    "industry_cn": _industry_cn(industry),
                    "value": max(round(value, 2), 0.01),
                    "value_basis": basis,
                    "market_cap_yi": round(mv / 1e8, 2) if mv > 0 else None,
                    "amount_yi": round(amount / 1e8, 2),
                    "pct_change": round(safe_float(r.pct_change), 2),
                    "rvol": rvol,
                    "is_leader": False,
                }
            )

        # 领涨龙头在**截断前**的全量成分上取，与被截掉的尾部无关
        top = max(rows, key=lambda x: x["pct_change"])
        top["is_leader"] = True

        total = len(rows)
        rows.sort(key=lambda x: x["value"], reverse=True)
        truncated = total > limit
        rows = rows[:limit]

        adv = sum(1 for r in rows if r["pct_change"] > 0)
        dec = sum(1 for r in rows if r["pct_change"] < 0)
        flat = len(rows) - adv - dec
        pcts = sorted(r["pct_change"] for r in rows)
        avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else 0.0
        median_pct = (
            round(
                pcts[len(pcts) // 2]
                if len(pcts) % 2
                else (pcts[len(pcts) // 2 - 1] + pcts[len(pcts) // 2]) / 2,
                2,
            )
            if pcts
            else 0.0
        )

        return {
            "trade_date": to_iso(str(latest)),
            "sector": sector,
            "sector_cn": _sector_cn(sector),
            "items": rows,
            "summary": {
                "members": total,
                "shown": len(rows),
                "truncated": truncated,
                "advance": adv,
                "decline": dec,
                "flat": flat,
                "avg_pct": avg_pct,
                "median_pct": median_pct,
                "leader": top["name"],
                "leader_symbol": top["symbol"],
                "leader_pct": top["pct_change"],
                "total_mv_yi": round(mv_total / 1e8, 1) if mv_total > 0 else None,
                "total_amount_yi": round(amount_total / 1e8, 1),
            },
        }

    # 缓存键必须带 limit：截断在 _load 内完成，漏掉 limit 会让先到的
    # 小页响应（如 limit=10）污染默认 300 的请求
    return cached(f"us:sector_drill:{sector}:{limit}", _load, ttl=_SECTOR_TTL)


def get_sector_rotation(limit: int = 24) -> dict[str, Any]:
    """GICS 板块多周期轮动：1/5/20/60 日收益 + 相对标普强弱 + 板块内宽度。

    相对强弱（RS）用板块 20 日收益减标普 20 日收益；标普 20 日收益走
    index_daily（**注意其分区可能滞后个股**，故 RS 单独标注指数日期）。
    """
    limit = max(5, min(int(limit), 50))

    def _load() -> dict[str, Any]:
        if not _avail():
            return {"trade_date": "", "index_date": "", "sectors": []}
        frame = _sector_frame()
        if frame.empty:
            return {"trade_date": "", "index_date": "", "sectors": []}
        latest = _trading_days(None, 1)
        benchmark = _benchmark_return(20)

        rows: list[dict[str, Any]] = []
        for sector, g in frame.groupby("sector", sort=False):
            rets = {}
            for w in _ROTATION_WINDOWS:
                col = f"ret_{w}d"
                vals = g[col].dropna() if col in g.columns else pd.Series(dtype=float)
                rets[col] = round(float(vals.median()), 2) if len(vals) else None
            r20 = rets.get("ret_20d")
            rows.append(
                {
                    "name": _sector_cn(sector),
                    "sector": sector,
                    "ret_1d": rets.get("ret_1d"),
                    "ret_5d": rets.get("ret_5d"),
                    "ret_20d": r20,
                    "ret_60d": rets.get("ret_60d"),
                    "rs_20d": (
                        round(r20 - benchmark, 2)
                        if r20 is not None and benchmark is not None
                        else None
                    ),
                    "breadth_20d": (
                        round(float((g["ret_20d"].dropna() > 0).mean() * 100), 1)
                        if g["ret_20d"].notna().any()
                        else None
                    ),
                    "stock_count": int(len(g)),
                }
            )
        rows.sort(key=lambda x: (x["ret_20d"] is None, -(x["ret_20d"] or 0)))
        return {
            "trade_date": to_iso(latest[0]) if latest else "",
            "index_date": to_iso(_latest_index_date() or "")
            if _latest_index_date()
            else "",
            "benchmark_return_20d": benchmark,
            "sectors": rows[:limit],
        }

    return cached(f"us_sector_rotation:{limit}", _load, ttl=_SECTOR_TTL)


def _benchmark_return(window: int) -> float | None:
    """标普 500 的 window 日收益（%），供相对强弱使用。"""
    latest = _latest_index_date()
    if not latest:
        return None
    days = _index_trading_days(latest, window + 2)
    if len(days) < window + 1:
        return None
    df = _read_partitioned(INDEX_REL, days, columns="symbol, dt, close")
    if df.empty:
        return None
    df["dt"] = df["dt"].astype(str)
    sub = df[df["symbol"] == "SPX.US"].sort_values("dt")
    if len(sub) < window + 1:
        return None
    closes = [safe_float(c) for c in sub["close"].tolist()]
    base = closes[-1 - window]
    if base <= 0:
        return None
    return round((closes[-1] / base - 1) * 100, 2)


def get_sector_fund_flow(limit: int = 24) -> dict[str, Any]:
    """板块资金流：成交额占比及其相对 20 日基准的变化。

    **这是判断「哪里在变热」的核心口径** —— 单看今日成交额只能看出谁体量大
    （信息技术永远最大），要看**占比相对自身近期基准的变化**才能识别资金迁移：
    占比 +1.5pp 意味着资金正在往这个板块集中。

    `amount_share_change_pp` 单位为百分点（percentage point），非百分比。
    """
    limit = max(5, min(int(limit), 50))

    def _load() -> dict[str, Any]:
        empty = {
            "trade_date": "",
            "total_amount_yi": 0.0,
            "base_days": 0,
            "sectors": [],
        }
        if not _avail():
            return empty
        latest, snap = _market_pct_snapshot()
        if not latest or snap.empty:
            return empty
        smap = _sector_map()
        if smap.empty:
            return empty

        today = snap.merge(smap, on="symbol", how="inner")
        total_today = float(today["amount"].fillna(0).sum())
        if total_today <= 0:
            return empty
        cur = today.groupby("sector")["amount"].sum().rename("amount").reset_index()
        cur["share"] = cur["amount"] / total_today * 100

        # 20 日基准占比：每日各板块成交额 / 当日全市场成交额，再取均值
        days = _trading_days(None, 21)[1:]  # 排除最新日
        base_share: dict[str, float] = {}
        base_days = 0
        if days:
            k = _read_partitioned(KLINE_REL, days, columns="symbol, dt, amount")
            if not k.empty:
                k = _dedupe_bars(k, ["symbol", "dt"])
                k["dt"] = k["dt"].astype(str)
                k = k.merge(smap[["symbol", "sector"]], on="symbol", how="inner")
                per_day = k.groupby(["dt", "sector"])["amount"].sum().reset_index()
                totals = per_day.groupby("dt")["amount"].transform("sum")
                per_day["share"] = per_day["amount"] / totals * 100
                base_share = per_day.groupby("sector")["share"].mean().to_dict()
                base_days = int(per_day["dt"].nunique())

        rows = []
        for _, r in cur.iterrows():
            sector = r["sector"]
            base = base_share.get(sector)
            rows.append(
                {
                    "name": _sector_cn(sector),
                    "sector": sector,
                    "amount_yi": fmt_yi(safe_float(r["amount"])),
                    "share": round(safe_float(r["share"]), 2),
                    "base_share": round(base, 2) if base is not None else None,
                    "share_change_pp": (
                        round(safe_float(r["share"]) - base, 2)
                        if base is not None
                        else None
                    ),
                }
            )
        rows.sort(
            key=lambda x: (x["share_change_pp"] is None, -(x["share_change_pp"] or 0))
        )
        return {
            "trade_date": to_iso(latest),
            "total_amount_yi": fmt_yi(total_today),
            "base_days": base_days,
            "sectors": rows[:limit],
        }

    return cached(f"us_sector_fund_flow:{limit}", _load, ttl=_SECTOR_TTL)


def get_sector_valuation(limit: int = 24) -> list[dict[str, Any]]:
    """板块估值温度计：GICS 板块 × PE/PB/股息率中位数 + 市值合计。

    数据源 `f10` 快照（**不是 valuation 分区**，后者曾长期静默写空）。
    PE 中位数只取正值（亏损公司的负 PE 会污染中位数）。
    """
    limit = max(5, min(int(limit), 50))

    def _load() -> list[dict[str, Any]]:
        if not _avail():
            return []
        f10 = _f10_snapshot()
        smap = _sector_map()
        if f10.empty or smap.empty:
            return []
        df = f10.merge(smap, on="symbol", how="inner")
        df["pe_pos"] = df["pe_ratio"].where(df["pe_ratio"] > 0)
        df["pb_pos"] = df["pb_ratio"].where(df["pb_ratio"] > 0)

        rows: list[dict[str, Any]] = []
        for sector, g in df.groupby("sector", sort=False):
            rows.append(
                {
                    "name": _sector_cn(sector),
                    "sector": sector,
                    "pe_median": _median_or_none(g["pe_pos"]),
                    "pb_median": _median_or_none(g["pb_pos"]),
                    "dividend_yield_median": _median_or_none(g["dividend_yield"]),
                    "market_cap_yi": fmt_yi(float(g["market_cap"].fillna(0).sum())),
                    "stock_count": int(len(g)),
                }
            )
        rows.sort(key=lambda x: (x["pe_median"] is None, x["pe_median"] or 0))
        return rows[:limit]

    return cached(f"us_sector_valuation:{limit}", _load, ttl=_SECTOR_TTL)


def _median_or_none(series: pd.Series) -> float | None:
    """中位数（无有效值返回 None，**不返回 0**：0 会被前端当成真实估值）。"""
    vals = series.dropna()
    if vals.empty:
        return None
    return round(float(vals.median()), 2)
