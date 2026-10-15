"""席位排名 —— SHFE/CZCE 主力合约席位榜（新浪口径）。

口径：
- 数据源 `member_rank_sina`（每日分区，long/short/volume 三榜，前 10-20 席位）
- `long_wan`/`short_wan` = 前 N 席位多/空单持仓合计（万手）；
  `net_wan` = 多 − 空（万手）；`net_delta_wan` = 多空日增减合成净变化
- `top5_long_share_pct` = 前 5 席位持仓集中度（前 5 合计 / 榜单合计 × 100）
- **覆盖仅 SHFE/CZCE**（DCE/CFFEX/INE 无免费源）；样本自 2026-10-08 起，
  `sample_days` 如实显形——短样本不做分位、不做趋势结论
"""

from __future__ import annotations

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _member_rank,
    _num,
    to_iso,
)
from backend.shared.futures_universe import CN_MAIN, SECTORS, symbol_meta

_STEM_NAME: dict[str, str] = {k[:-1]: v for k, v in CN_MAIN.items()}

_SIDE_LABEL = {"long": "多单持仓", "short": "空单持仓", "volume": "成交量"}


def _meta_of(stem: str) -> dict:
    meta = symbol_meta(f"{stem}0.CN")
    return {
        "name": _STEM_NAME.get(stem, meta["name"]),
        "sector": meta["sector"],
        "sector_name": SECTORS.get(meta["sector"], meta["sector"]),
    }


def _w(v) -> float | None:
    """手 → 万手出口（缺失 None）。"""
    if v is None or pd.isna(v):
        return None
    return _num(float(v) / 1e4)


def _board_of(cur: pd.DataFrame, venue: str, contract: str) -> dict:
    """单品种三榜明细（多空成交各自按名次）。"""

    def _list(side: str) -> list[dict]:
        sub = cur[(cur["side"] == side)].sort_values("rank")
        return [
            {
                "rank": int(r["rank"]) if pd.notna(r["rank"]) else None,
                "member": str(r["member"]),
                "size_wan": _w(r["size"]),
                "delta_wan": _w(r["delta"]),
            }
            for _, r in sub.iterrows()
        ]

    return {
        "venue": venue,
        "contract": contract,
        "long": _list("long"),
        "short": _list("short"),
        "volume": _list("volume"),
    }


def get_members(symbol: str | None = None, limit: int = 60) -> dict:
    """席位总览榜（多空净额/集中度/日变化）+ （可选 symbol）单品种三榜明细。

    仅最新分区进榜；单品种附加逐日净多趋势（样本期内全部交易日）。
    """

    def _load() -> dict:
        mb = _member_rank()
        notes = [
            "新浪席位口径：主力合约前 10-20 席位",
            "覆盖仅 SHFE/CZCE（DCE/CFFEX/INE 无免费源）",
        ]
        if mb.empty:
            return {
                "as_of": None,
                "sample_days": 0,
                "rows": [],
                "board": None,
                "net_trend": [],
                "notes": notes + ["member_rank_sina 数据不可用"],
            }
        latest = str(mb["dt"].max())
        sample_days = int(mb["dt"].nunique())
        cur = mb[mb["dt"] == latest]
        notes.append(f"样本 {sample_days} 个交易日（勿做趋势结论）")

        rows = []
        for variety, sub in cur.groupby("variety"):
            lng = sub[sub["side"] == "long"]
            sht = sub[sub["side"] == "short"]
            long_sum = lng["size"].sum(min_count=1)
            short_sum = sht["size"].sum(min_count=1)
            if pd.isna(long_sum) and pd.isna(short_sum):
                continue
            net = (
                float(long_sum) - float(short_sum)
                if pd.notna(long_sum) and pd.notna(short_sum)
                else float("nan")
            )

            def _delta_sum(side_df: pd.DataFrame) -> float:
                return float(side_df["delta"].sum(min_count=1))

            d_l, d_s = _delta_sum(lng), _delta_sum(sht)
            net_delta = (
                d_l - d_s if d_l == d_l and d_s == d_s else float("nan")
            )  # NaN 不合成

            def _top5_share(side_df: pd.DataFrame, total: float) -> float | None:
                if not total or total != total or total <= 0:
                    return None
                top = side_df.sort_values("size", ascending=False).head(5)["size"].sum()
                return _num(float(top) / float(total) * 100.0, 1)

            meta = _meta_of(str(variety))
            contract = ""
            if len(lng):
                contract = str(lng.iloc[0].get("contract") or "")
            rows.append(
                {
                    "symbol": str(variety),
                    "name": meta["name"],
                    "sector": meta["sector"],
                    "sector_name": meta["sector_name"],
                    "venue": str(sub["venue"].iloc[0]) if len(sub) else None,
                    "contract": contract or None,
                    "long_wan": _w(long_sum),
                    "short_wan": _w(short_sum),
                    "net_wan": _w(net),
                    "net_delta_wan": _w(net_delta),
                    "top5_long_share_pct": _top5_share(lng, float(long_sum))
                    if pd.notna(long_sum)
                    else None,
                    "top5_short_share_pct": _top5_share(sht, float(short_sum))
                    if pd.notna(short_sum)
                    else None,
                    "n_long": int(len(lng.dropna(subset=["member"]))),
                    "n_short": int(len(sht.dropna(subset=["member"]))),
                }
            )
        rows.sort(
            key=lambda r: (
                r["net_wan"] is None,
                -abs(r["net_wan"] or 0),
            )
        )

        board = None
        net_trend: list[dict] = []
        if symbol:
            stem = str(symbol).strip().upper().replace(".CN", "")
            sub_all = mb[mb["variety"] == stem]
            if not sub_all.empty:
                cur_v = sub_all[sub_all["dt"] == latest]
                venue = str(cur_v["venue"].iloc[0]) if len(cur_v) else ""
                contract = ""
                lng_v = cur_v[cur_v["side"] == "long"]
                if len(lng_v):
                    contract = str(lng_v.iloc[0].get("contract") or "")
                board = _board_of(cur_v, venue, contract)
                for d, day in sub_all.groupby("dt"):
                    dl = day[day["side"] == "long"]["size"].sum(min_count=1)
                    ds = day[day["side"] == "short"]["size"].sum(min_count=1)
                    net = (
                        float(dl) - float(ds)
                        if pd.notna(dl) and pd.notna(ds)
                        else float("nan")
                    )
                    net_trend.append({"date": to_iso(str(d)), "net_wan": _w(net)})
                net_trend.sort(key=lambda x: x["date"])
        return {
            "as_of": to_iso(latest),
            "sample_days": sample_days,
            "rows": rows[:limit],
            "variety_count": len(rows),
            "board": board,
            "net_trend": net_trend,
            "side_labels": _SIDE_LABEL,
            "notes": notes,
        }

    return cached(f"futures_members:{symbol or 'all'}:{limit}", _load, ttl=1800.0)
