"""期货仓单库存 —— 交易所注册仓单（日度，品种级）。

口径：
- 仓单（warehouse receipt）= 已注册的标准仓单数量（张/手），是实物
  「显性库存」的官方口径；仓单增减通常领先/同步于现货升贴水变化
- 源为各交易所日度仓单汇总（`warehouse_receipts`，按 dt 分区）；
  **覆盖不全**：上期所接口失效时最新日期可能仅 CZCE——页面按最新日期
  实际收录的交易所如实标注，绝不把缺数据渲染成 0
"""

from __future__ import annotations

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _inventory_em,
    _num,
    _rank_pct,
    _receipts,
    to_iso,
)
from backend.shared.futures_universe import CN_MAIN, symbol_meta

# stem → 品种中文名（仓单 variety 列为 stem，如 SR/CU）
STEM_NAME: dict[str, str] = {k[:-1]: v for k, v in CN_MAIN.items()}

_VENUE_LABELS = {
    "SHFE": "上期所",
    "DCE": "大商所",
    "CZCE": "郑商所",
    "GFEX": "广期所",
    "INE": "上能源",
}


def _history(variety: str, days: int) -> dict:
    """单品种仓单历史（含每个交易日的交易所）。"""
    df = _receipts().dropna(subset=["receipt_qty"])  # 全 NaN 组 sum 会冒充 0
    sub = df[df["variety"] == variety].copy()
    if sub.empty:
        return {
            "variety": variety,
            "name": STEM_NAME.get(variety, variety),
            "points": [],
            "latest": None,
            "change_wan": None,
            "change_days": None,
        }
    agg = sub.groupby(["dt", "venue"])["receipt_qty"].sum().reset_index()
    latest, prev = None, None
    points = []
    dt_days = sorted(agg["dt"].unique())[-days:]
    for d in dt_days:
        day = agg[agg["dt"] == d]
        total = float(day["receipt_qty"].sum())
        venue = str(day.loc[day["receipt_qty"].idxmax(), "venue"])
        prev = latest
        latest = (d, total)
        points.append(
            {
                "date": to_iso(d),
                "receipt_qty_wan": _num(total / 1e4),
                "venue": venue,
            }
        )
    change_wan = None
    if latest and prev:
        change_wan = _num((latest[1] - prev[1]) / 1e4)
    return {
        "variety": variety,
        "name": STEM_NAME.get(variety, variety),
        "points": points,
        "latest": to_iso(latest[0]) if latest else None,
        "latest_qty_wan": _num(latest[1] / 1e4) if latest else None,
        "change_wan": change_wan,
        "change_days": 1 if change_wan is not None else None,
    }


def get_receipts(variety: str | None = None, days: int = 30) -> dict:
    """仓单库存：最新两日按品种增减榜 + （可选）单品种历史。

    `kinds` 为未指定品种时的总览：最新日期实际收录的交易所 / 有仓单品种数。
    """

    def _load() -> dict:
        df = _receipts().dropna(subset=["receipt_qty"])  # 全 NaN 组 sum 会冒充 0
        if df.empty:
            return {
                "latest_date": None,
                "prev_date": None,
                "venues": [],
                "variety_count": 0,
                "rows": [],
                "history": None,
            }
        latest = df["dt"].max()
        dates = sorted(df["dt"].unique())
        prev = dates[-2] if len(dates) > 1 else None

        def _day_totals(d: str) -> pd.DataFrame:
            day = df[df["dt"] == d]
            return day.groupby(["variety", "venue"])["receipt_qty"].sum().reset_index()

        cur = _day_totals(latest)
        cur_total = cur.groupby("variety")["receipt_qty"].sum()
        prev_total = (
            _day_totals(prev).groupby("variety")["receipt_qty"].sum()
            if prev
            else pd.Series(dtype="float64")
        )
        m = cur_total.rename("qty").to_frame()
        m["venue"] = cur.groupby("variety")["venue"].agg(lambda s: str(s.iloc[0]))
        m["prev_qty"] = prev_total
        m["change"] = m["qty"] - m["prev_qty"]
        m = m.sort_values("qty", ascending=False)
        rows = []
        # 循环变量不得叫 variety——函数作用域下会覆盖同名参数，令下方
        # `if variety:` 误取排序末位品种（曾致 get_receipts("SR") 返回 ZC
        # 历史、无参调用也凭空带出 ZC 历史）
        for variety_name, r in m.iterrows():
            rows.append(
                {
                    "variety": str(variety_name),
                    "name": STEM_NAME.get(str(variety_name), str(variety_name)),
                    "venue": str(r["venue"]),
                    "venue_name": _VENUE_LABELS.get(str(r["venue"]), str(r["venue"])),
                    # sector 必须按行内品种取（曾误用外层参数 variety：
                    # 无参调用全部行落 None0.CN 兜底 "other"、带参调用全部行
                    # 被赋成该品种板块）
                    "sector": symbol_meta(f"{variety_name}0.CN")["sector"],
                    "receipt_qty_wan": _num(float(r["qty"]) / 1e4),
                    "change_wan": _num(
                        float(r["change"]) / 1e4
                        if pd.notna(r["change"])
                        else float("nan")
                    ),
                }
            )
        venues = sorted(
            df.loc[df["dt"] == latest, "venue"].dropna().astype(str).unique().tolist()
        )
        history = None
        if variety:
            v = variety.strip().upper()
            history = _history(v, days)
        return {
            "latest_date": to_iso(latest),
            "prev_date": to_iso(prev) if prev else None,
            "venues": venues,
            "venue_names": {v: _VENUE_LABELS.get(v, v) for v in venues},
            "variety_count": int(m.shape[0]),
            "rows": rows,
            "history": history,
        }

    return cached(f"futures_receipts:{variety or 'all'}:{days}", _load)


# ---- 东财库存（59 品种，与仓单互为印证） ----

_CYCLE_BAND = 0.005  # 20 日变化 ±0.5% 内记「持平」
_STALE_DAYS = 7  # 品种 as-of 落后全局最新 > 7 自然日 → 陈旧显形


def get_inventory_em(symbol: str | None = None, limit: int = 80) -> dict:
    """东财库存榜：5/20 日变化、自身历史分位、累去库标签；（可选）单品种历史。

    每品种 as-of 可能不同（个别源侧停更，实测 AP/FU）——out_of_sync 显形，
    绝不用全局最新日冒充。库存单位随品种（东财口径），只做相对比较。
    """

    def _load() -> dict:
        df = _inventory_em()
        if df.empty:
            return {
                "as_of": None,
                "rows": [],
                "variety_count": 0,
                "history": None,
                "notes": ["inventory_em 数据不可用"],
            }
        global_latest = str(df["dt"].max())
        stale_cut = (
            pd.Timestamp(global_latest) - pd.Timedelta(days=_STALE_DAYS)
        ).strftime("%Y%m%d")
        rows = []
        for sym, sub in df.groupby("symbol"):
            sub = sub.dropna(subset=["inventory"]).sort_values("dt")
            if sub.empty:
                continue
            inv = sub["inventory"]
            latest_row = sub.iloc[-1]
            chg5 = (
                float(inv.iloc[-1] / inv.iloc[-6] - 1.0) * 100.0
                if len(sub) >= 6 and inv.iloc[-6]
                else float("nan")
            )
            chg20 = (
                float(inv.iloc[-1] / inv.iloc[-21] - 1.0) * 100.0
                if len(sub) >= 21 and inv.iloc[-21]
                else float("nan")
            )
            pct_hist = _rank_pct(inv).iloc[-1] if len(sub) >= 20 else float("nan")
            cycle = None
            if chg20 == chg20:
                if chg20 > _CYCLE_BAND * 100:
                    cycle = "build"  # 累库
                elif chg20 < -_CYCLE_BAND * 100:
                    cycle = "draw"  # 去库
                else:
                    cycle = "flat"
            stem = str(sym)
            rows.append(
                {
                    "symbol": stem,
                    "name": STEM_NAME.get(stem, stem),
                    "as_of": to_iso(str(latest_row["dt"])),
                    "out_of_sync": str(latest_row["dt"]) < stale_cut,
                    "inventory": _num(latest_row["inventory"], 0),
                    "delta_1d": _num(latest_row.get("delta"), 0),
                    "chg_5d_pct": _num(chg5, 1) if chg5 == chg5 else None,
                    "chg_20d_pct": _num(chg20, 1) if chg20 == chg20 else None,
                    "pct_hist": _num(pct_hist, 1) if pct_hist == pct_hist else None,
                    "cycle": cycle,
                    "sample_days": int(len(sub)),
                }
            )
        rows.sort(
            key=lambda r: (
                r["chg_20d_pct"] is None,
                -(r["chg_20d_pct"] or 0),
            )
        )

        history = None
        if symbol:
            stem = str(symbol).strip().upper().replace(".CN", "")
            sub = df[df["symbol"] == stem].sort_values("dt")
            if not sub.empty:
                history = {
                    "symbol": stem,
                    "name": STEM_NAME.get(stem, stem),
                    "points": [
                        {
                            "date": to_iso(str(r["dt"])),
                            "inventory": _num(r["inventory"], 0),
                            "delta": _num(r.get("delta"), 0),
                        }
                        for _, r in sub.iterrows()
                    ],
                }
        fresh_n = sum(1 for r in rows if not r["out_of_sync"])
        return {
            "as_of": to_iso(global_latest),
            "rows": rows[:limit],
            "variety_count": len(rows),
            "fresh_count": fresh_n,
            "history": history,
            "notes": [
                "东财库存口径，单位随品种（只做相对分位与变化）",
                f"{len(rows) - fresh_n} 个品种 as-of 落后最新（源侧停更，已显形）"
                if fresh_n < len(rows)
                else "全部品种跟随最新",
                "theory of storage：去库通常对应近端偏强（前瞻板库存因子即此口径取反）",
            ],
        }

    return cached(f"futures_inventory_em:{symbol or 'all'}:{limit}", _load, ttl=1800.0)
