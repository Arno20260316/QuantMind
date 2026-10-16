"""选股榜条目富化（build 与 API 历史日期路径共用一份实现）。

为什么在 shared：读侧 ``GET /smart-money/stocks?date=`` 需要按历史快照日
**现算**同一套富化字段（5 日窗金额 / 北向季差 / 最新事件），若 API 复刻
一份逻辑，日后改窗宽/加列必然两处漂移。build 侧 ``_build_items`` 是唯一
权威实现，2026-10-16 迁到本模块，scripts 与 services 同引这里。

口径（改动须两侧金样同步）：
- symbol 输出前缀式；缺失一律 None（前端「—」），绝不补 0
- 5 日展示窗 = 日历上 ``[pos−4, pos]``（pos = as_of 在 cal 中的位置，越界截断）
- 前向收益封存列 fwd_ret_* 不进本模块（根本不读）
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.shared.smart_money import datasets, signals, stats
from backend.shared.stock_utils import StockCodeUtil


def n_or_none(v: Any) -> float | None:
    """numpy/NaN → JSON 安全 float|None（缺失一律 None，绝不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def i_or_none(v: Any) -> int | None:
    f = n_or_none(v)
    return None if f is None else int(f)


def window_sum(
    raw: dict[str, pd.DataFrame], table: str, col: str, win: pd.DatetimeIndex
) -> dict:
    """窗内 (表, 列) 按标的求和；表缺/列缺/窗空 → 空 dict。"""
    df = raw.get(table)
    if df is None or df.empty or col not in df.columns:
        return {}
    sub = df[df["trade_date"].isin(win)]
    if sub.empty:
        return {}
    return sub.groupby("symbol")[col].sum().to_dict()


def build_items(
    scores: pd.DataFrame,
    raw: dict[str, pd.DataFrame],
    cal: pd.DatetimeIndex,
    as_of: pd.Timestamp,
    margin: pd.DataFrame,
    admitted: tuple[tuple[str, int], ...],
    north_delta_pp: dict[str, float | None],
) -> list[dict]:
    """当日（或历史日）选股榜条目（score 降序；金额单位随柱原样，缺失 null）。

    ``margin`` 为 as_of 附近的两融日频（本函数只取 5 日窗内行）；
    ``north_delta_pp`` 为 build 侧已算好的 {后缀码: delta_pp}。
    """
    pillar_names = [n for n, _ in admitted]
    names = datasets.name_map()
    industries = datasets.industry_map()
    pos = int(cal.searchsorted(as_of))
    win = cal[max(0, pos - signals.WINDOW_5D + 1) : pos + 1]
    inst5 = window_sum(raw, "lhb_inst", "inst_net_buy", win)
    lhb5 = window_sum(raw, "lhb_detail", "lhb_net_buy", win)
    mg5: dict = {}
    if margin is not None and not margin.empty:
        mw = margin[margin["trade_date"].isin(win)]
        if not mw.empty:
            mg5 = mw.groupby("symbol")["finance_net"].sum().to_dict()
    ev_map = stats.latest_event_map(raw, cal, as_of)

    def _label(series: pd.Series, sym: str) -> str | None:
        if series.empty or sym not in series.index:
            return None
        return datasets.s_or_none(series.get(sym))

    items: list[dict] = []
    for _, r in scores.sort_values("score", ascending=False).iterrows():
        sym = str(r["symbol"])
        items.append(
            {
                "symbol": StockCodeUtil.to_prefix(sym) or sym,
                "name": _label(names, sym),
                "industry": _label(industries, sym),
                "score": n_or_none(r["score"]),
                "covered": i_or_none(r["covered"]),
                "n_pillars": i_or_none(r["n_pillars"]),
                "pct": {n: n_or_none(r.get(f"pct_{n}")) for n in pillar_names},
                "val": {n: n_or_none(r.get(f"val_{n}")) for n in pillar_names},
                "inst_net_5d": n_or_none(inst5.get(sym)),
                "lhb_net_5d": n_or_none(lhb5.get(sym)),
                "margin_net_5d": n_or_none(mg5.get(sym)),
                "north_q_delta_pp": n_or_none(north_delta_pp.get(sym)),
                "latest_event": ev_map.get(sym),
            }
        )
    return items
