"""聪明钱市场级统计（概览带 / 趋势序列 / 营业部榜 / 北向摘要）。纯函数。

单位口径：对外一律**亿元**（金额）或**家数**；缺失为 NaN，绝不补 0。
- 龙虎榜/机构/大宗：原始层已是元 → /1e8
- 两融：QuantDB 万元 → /1e4
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.shared.smart_money.signals import POOL_WINDOW, WINDOW_5D, align_events

_EVENT_TABLES = ("lhb_detail", "lhb_inst", "block_trade", "survey")


def _align_all(raw: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    return {
        name: align_events(raw.get(name, pd.DataFrame()), calendar)
        for name in _EVENT_TABLES
    }


def daily_market_stats(
    calendar: pd.DatetimeIndex,
    raw: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """逐交易日市场级统计（index=交易日）。

    列：
    - lhb_n_stocks / lhb_net_buy_yi：龙虎榜上榜家数 / 净买额合计
    - inst_n_stocks / inst_net_buy_yi：机构席位上榜家数 / 机构净买额合计
    - dzjy_amount_yi / dzjy_premium_share：大宗成交额 / 溢价成交金额占比（分数）
    - survey_n_stocks / survey_n_inst：调研家数 / 接待机构家数合计
    - margin_net_5d_yi / margin_bal_yi：全市场融资净买 5 日累计 / 融资余额
    """
    cal = pd.DatetimeIndex(calendar)
    out = pd.DataFrame(index=cal)
    data = _align_all(raw, cal)

    d = data["lhb_detail"]
    if not d.empty:
        g = d.groupby("trade_date").agg(
            lhb_n_stocks=("symbol", "nunique"), lhb_net=("lhb_net_buy", "sum")
        )
        out["lhb_n_stocks"] = g["lhb_n_stocks"].reindex(cal).astype(float)
        out["lhb_net_buy_yi"] = (g["lhb_net"] / 1e8).reindex(cal)

    i = data["lhb_inst"]
    if not i.empty:
        g = i.groupby("trade_date").agg(
            inst_n_stocks=("symbol", "nunique"), inst_net=("inst_net_buy", "sum")
        )
        out["inst_n_stocks"] = g["inst_n_stocks"].reindex(cal).astype(float)
        out["inst_net_buy_yi"] = (g["inst_net"] / 1e8).reindex(cal)

    b = data["block_trade"]
    if not b.empty:
        bb = b[b["amount"] > 0].copy()
        bb["_prem_amt"] = bb["amount"].where(bb["premium"] > 0, 0.0)
        g = bb.groupby("trade_date").agg(amt=("amount", "sum"), prem=("_prem_amt", "sum"))
        out["dzjy_amount_yi"] = (g["amt"] / 1e8).reindex(cal)
        out["dzjy_premium_share"] = (g["prem"] / g["amt"].where(g["amt"] > 0)).reindex(cal)

    s = data["survey"]
    if not s.empty:
        g = s.groupby("trade_date").agg(
            n_stocks=("symbol", "nunique"), n_inst=("inst_count", "sum")
        )
        out["survey_n_stocks"] = g["n_stocks"].reindex(cal).astype(float)
        out["survey_n_inst"] = g["n_inst"].reindex(cal)

    if margin is not None and not margin.empty:
        mg = margin.groupby("trade_date").agg(
            net=("finance_net", "sum"), bal=("finance_balance", "sum")
        )
        mg = mg.reindex(cal)
        out["margin_net_5d_yi"] = (mg["net"].rolling(WINDOW_5D, min_periods=3).sum() / 1e4)
        out["margin_bal_yi"] = mg["bal"] / 1e4

    return out


def branch_rank(
    raw_branch: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    end: pd.Timestamp,
    window: int = POOL_WINDOW,
    top_n: int = 20,
) -> list[dict]:
    """活跃营业部榜：窗内净买合计降序（amounts 元）。

    n_days=窗内上榜天数；stocks_bought 取窗内最近一次非空「买入股票」文本。
    """
    if raw_branch is None or raw_branch.empty:
        return []
    cal = pd.DatetimeIndex(calendar)
    pos = cal.searchsorted(pd.Timestamp(end), side="right")
    if pos == 0:
        return []
    win = cal[max(0, pos - window):pos]
    sub = raw_branch[raw_branch["trade_date"].isin(win)]
    if sub.empty:
        return []
    sub = sub.sort_values("trade_date")
    g = sub.groupby("branch_name", as_index=False).agg(
        net_amount=("net_amount", "sum"),
        buy_amount=("buy_amount", "sum"),
        sell_amount=("sell_amount", "sum"),
        n_days=("trade_date", "nunique"),
        stocks_bought=("stocks_bought", "last"),
    )
    g = g.sort_values("net_amount", ascending=False).head(top_n)
    out: list[dict] = []
    for _, r in g.iterrows():
        out.append(
            {
                "branch_name": r["branch_name"],
                "net_amount": _f(r["net_amount"]),
                "buy_amount": _f(r["buy_amount"]),
                "sell_amount": _f(r["sell_amount"]),
                "n_days": int(r["n_days"]),
                "stocks_bought": r["stocks_bought"] if isinstance(r["stocks_bought"], str) else None,
            }
        )
    return out


def north_summary(north_delta: pd.DataFrame, top_n: int = 10) -> dict:
    """最新季度北向持仓变化摘要（delta 为持仓占比分数差 → 对外百分点）。"""
    empty = {
        "quarter": None,
        "report_date": None,
        "n_total": None,
        "n_inc": None,
        "n_dec": None,
        "top_inc": [],
        "top_dec": [],
    }
    if north_delta is None or north_delta.empty:
        return empty
    df = north_delta.dropna(subset=["delta"])
    if df.empty:
        return empty
    quarter = str(df["quarter"].iloc[0])
    report_date = str(df["report_date"].iloc[0])

    def _rows(sub: pd.DataFrame) -> list[dict]:
        return [
            {
                "symbol": r["symbol"],
                "stock_name": str(r["stock_name"]) if pd.notna(r["stock_name"]) else None,
                "delta_pp": _f(r["delta"] * 100.0),
                "pct": _f(r["pct"] * 100.0),
            }
            for _, r in sub.iterrows()
        ]

    return {
        "quarter": quarter,
        "report_date": report_date,
        "n_total": int(len(df)),
        "n_inc": int((df["delta"] > 0).sum()),
        "n_dec": int((df["delta"] < 0).sum()),
        "top_inc": _rows(df.nlargest(top_n, "delta")),
        "top_dec": _rows(df.nsmallest(top_n, "delta")),
    }


def latest_event_map(
    raw: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex, end: pd.Timestamp
) -> dict[str, dict]:
    """每股最近一次事件（≤end）：{symbol: {type, date}}（选股榜「最新事件」列）。"""
    cal = pd.DatetimeIndex(calendar)
    d = pd.Timestamp(end)
    data = _align_all(raw, cal)
    # 内部一律用 Timestamp 比较（str 比较会 TypeError）；输出才格式化。
    # 同日跨表并列 → 先遍历的表优先（lhb_detail > lhb_inst > block_trade > survey）。
    best: dict[str, tuple[pd.Timestamp, str]] = {}
    for name, label in (
        ("lhb_detail", "lhb"),
        ("lhb_inst", "inst"),
        ("block_trade", "block_trade"),
        ("survey", "survey"),
    ):
        df = data.get(name)
        if df is None or df.empty:
            continue
        sub = df[df["trade_date"] <= d]
        if sub.empty:
            continue
        idx = sub.sort_values("trade_date").groupby("symbol")["trade_date"].idxmax()
        for _, r in sub.loc[idx].iterrows():
            sym = r["symbol"]
            ts = pd.Timestamp(r["trade_date"])
            cur = best.get(sym)
            if cur is None or ts > cur[0]:
                best[sym] = (ts, label)
    return {
        sym: {"type": label, "date": ts.strftime("%Y-%m-%d")}
        for sym, (ts, label) in best.items()
    }


def _f(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or np.isinf(f)) else f
