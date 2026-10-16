"""聪明钱信号：事件池 + 柱计算 + 复合分（纯函数，DataFrame in/out，可金样单测）。

口径（细案 docs/市场分析_聪明钱追踪与选股_细案_20261015.md §3）：
- 事件池：最近 POOL_WINDOW 个交易日内有龙虎榜/机构席位/大宗/调研事件，
  或两融净买入 5 日 z20 绝对值 ≥ MARGIN_Z_THRESHOLD。
- 柱：每柱 → 当日**池内有值成员**截面分位 0–100（**按入柱方向对齐**：负向柱
  取 100−分位，方向=h10 实测 IC 符号，HK 六柱先例）；复合分 = 入柱等权均值；
  缺失柱记 FILL_SCORE=50 并披露 covered 计数（HK 六柱模式：缺失 ≠ 0，
  界面分位缺失显示「—」）。
- 反前视：事件日在 T 日盘后可得；日历对齐取「≥事件日的首个交易日」
  （周末公告 → 下一交易日），滚动窗口一律只用 ≤T 的数据。

**封存硬规则**：fwd_ret_*（东财「上榜后表现」）只可展示、绝不可入信号。
本模块全部按白名单取列；单测以「毒化 fwd_* 后柱值逐位不变」钉死。
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Callable

import numpy as np
import pandas as pd

#: 事件池回看窗口（交易日）
POOL_WINDOW = 5
#: 滚动词窗口（5 日累计类）
WINDOW_5D = 5
#: 两融异动阈值（|净买 5 日 z20|）
MARGIN_Z_THRESHOLD = 2.0
#: z 窗口与最小观测
Z_WINDOW = 20
Z_MIN_PERIODS = 15
#: 调研累计窗口（日历日）
SURVEY_WINDOW_DAYS = 90
#: 缺失柱填充分（披露口径：covered 计数随行输出，绝不把缺失当 0）
FILL_SCORE = 50.0

#: 柱候选全集（细案 §3.3/§3.6；最终入柱由验证闸门裁定）
PILLAR_CANDIDATES: tuple[str, ...] = (
    "inst_net_ratio_5d",
    "inst_net_buy_5d_z",
    "lhb_net_ratio_5d",
    "margin_net_5d_z",
    "margin_bal_chg_20d",
    "dzjy_amount_ratio_5d",
    "dzjy_premium_5d",
    "dzjy_inst_amount_ratio_5d",
    "dzjy_inst_premium_5d",
    "survey_inst_90d",
)

#: 柱展示元数据（label 供前端/证据台；unit 供格式化）——与 PILLAR_CANDIDATES 同步
PILLAR_META: dict[str, dict[str, str]] = {
    "inst_net_ratio_5d": {"label": "机构净买占比·5日", "unit": "ratio"},
    "inst_net_buy_5d_z": {"label": "机构净买额·5日z20", "unit": "z"},
    "lhb_net_ratio_5d": {"label": "龙虎榜净买占比·5日", "unit": "ratio"},
    "margin_net_5d_z": {"label": "融资净买入·5日z20", "unit": "z"},
    "margin_bal_chg_20d": {"label": "融资余额变化·20日", "unit": "ratio"},
    "dzjy_amount_ratio_5d": {"label": "大宗成交占比·5日", "unit": "ratio"},
    "dzjy_premium_5d": {"label": "大宗折溢率·5日", "unit": "ratio"},
    "dzjy_inst_amount_ratio_5d": {"label": "机构接盘大宗占比·5日", "unit": "ratio"},
    "dzjy_inst_premium_5d": {"label": "机构接盘大宗折溢率·5日", "unit": "ratio"},
    "survey_inst_90d": {"label": "机构调研家数·90日", "unit": "count"},
}

#: 入柱裁定（名, 方向）——方向 = 验证 h10 IC 符号（+1/−1）。分位按方向对齐：
#: 负向柱取 100−分位（HK 六柱「方向=实测方向」先例 hk_opportunity.py:151），
#: 使柱分高 = 历史有利方向。由 backend/scripts/smart_money_validate.py 产出
#: （2026-10-16 六柱过闸，新增「机构专用」买方口径双柱：dzjy_inst_amount_ratio_5d
#: IC=−0.0313 / dzjy_inst_premium_5d IC=+0.0618〔全柱最强〕；存量四柱
#: inst_net_buy_5d_z IC=+0.0195 / lhb_net_ratio_5d IC=−0.0413 /
#: margin_net_5d_z IC=−0.0102 / margin_bal_chg_20d IC=−0.0414，n=662；
#: 机构双柱因过滤全市场大宗样本 n=599；塌缩 0。否决：inst_net_ratio_5d 弱 /
#: dzjy_amount_ratio_5d、dzjy_premium_5d、survey_inst_90d 反向。
#: 审计全文见 data/market-analysis/smart_money/evidence.json）；
#: 空=未裁定：build 拒绝出分（先跑验证脚本）。
ADMITTED_PILLARS: tuple[tuple[str, int], ...] = (
    ("inst_net_buy_5d_z", 1),
    ("lhb_net_ratio_5d", -1),
    ("margin_net_5d_z", -1),
    ("margin_bal_chg_20d", -1),
    ("dzjy_inst_amount_ratio_5d", -1),
    ("dzjy_inst_premium_5d", 1),
)


# ---------------------------------------------------------------- 日历对齐


def align_events(events: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """事件表 → 日历对齐：trade_date 归到「≥事件日的首个交易日」。

    周末/节假日公告归入下一交易日（信息已在开盘前可得，不构成前视）；
    晚于日历末端的事件丢弃。事件日期本身是交易日时为恒等映射。
    """
    if events is None or events.empty:
        return pd.DataFrame() if events is None else events
    cal = pd.DatetimeIndex(calendar)
    dates = pd.to_datetime(events["trade_date"])
    pos = cal.searchsorted(dates.values, side="left")
    keep = pos < len(cal)
    out = events.loc[keep].copy()
    out["trade_date"] = cal[pos[keep]]
    return out


# ---------------------------------------------------------------- 矩阵工具


def _empty(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(index=pd.DatetimeIndex(calendar), dtype=float)


def _pivot(
    events: pd.DataFrame, calendar: pd.DatetimeIndex, value_col: str
) -> pd.DataFrame:
    """(trade_date, symbol) 长表 → 日×标的矩阵（同键求和）。空表返回空矩阵。"""
    if events is None or events.empty or value_col not in events.columns:
        return _empty(calendar)
    mat = events.pivot_table(
        index="trade_date", columns="symbol", values=value_col, aggfunc="sum"
    )
    return mat.reindex(index=pd.DatetimeIndex(calendar))


def _daily_symbol_sum(events: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    """事件表按 (trade_date, symbol) 聚合成每股每日一行。

    cols: {输出列: (源列, 聚合函数)}；仅允许白名单列（fwd_* 永不入内）。
    """
    if events is None or events.empty:
        return pd.DataFrame(columns=["trade_date", "symbol", *cols])
    use = events[["trade_date", "symbol", *[c for c, _ in cols.values()]]]
    g = use.groupby(["trade_date", "symbol"], as_index=False).agg(
        **{out: (src, fn) for out, (src, fn) in cols.items()}
    )
    return g


def _rolled_event_sum(mat: pd.DataFrame, window: int) -> pd.DataFrame:
    """事件窗滚动：窗内累计；窗内无事件 → NaN（有事件才出值）。

    事件日贡献其值，非事件日贡献 0（累计语义），有效性由计数掩码裁决。
    """
    if mat.empty:
        return mat
    cnt = mat.notna().astype(float)
    val = mat.fillna(0.0).rolling(window, min_periods=1).sum()
    ok = cnt.rolling(window, min_periods=1).sum() > 0
    return val.where(ok)


def _zscore(
    mat: pd.DataFrame, window: int = Z_WINDOW, min_periods: int = Z_MIN_PERIODS
) -> pd.DataFrame:
    """逐标的时序 z 分（窗口不足/全同值 → NaN，不出伪值）。"""
    if mat.empty:
        return mat
    mean = mat.rolling(window, min_periods=min_periods).mean()
    std = mat.rolling(window, min_periods=min_periods).std(ddof=0)
    z = (mat - mean) / std.where(std > 0)
    return z.replace([np.inf, -np.inf], np.nan)


def _daily_ratio(daily: pd.DataFrame, num_col: str, den_col: str) -> pd.DataFrame:
    """每股每日比值（分母 ≤0 → NaN）。"""
    if daily.empty:
        return daily
    out = daily.copy()
    out["_ratio"] = out[num_col] / out[den_col].where(out[den_col] > 0)
    return out


# ---------------------------------------------------------------- 柱计算
# 统一签名：(calendar, data, margin) -> DataFrame(index=交易日, columns=标的)
# data 为已 align_events 的各原始表；margin 为 datasets.margin_daily() 结果。


def pillar_inst_net_ratio_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """机构净买额占市场总成交比，5 日累计（jgmmtj 白名单列）。"""
    daily = _daily_symbol_sum(
        data.get("lhb_inst"),
        {"net": ("inst_net_buy", "sum"), "ma": ("market_amount", "median")},
    )
    daily = _daily_ratio(daily, "net", "ma")
    return _rolled_event_sum(_pivot(daily, calendar, "_ratio"), WINDOW_5D)


def pillar_inst_net_buy_5d_z(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """机构净买额 5 日累计的 z20（自身时序，非事件日记 0）。"""
    daily = _daily_symbol_sum(data.get("lhb_inst"), {"net": ("inst_net_buy", "sum")})
    mat = _pivot(daily, calendar, "net")
    if mat.empty:
        return mat
    s5 = mat.fillna(0.0).rolling(WINDOW_5D, min_periods=1).sum()
    return _zscore(s5)


def pillar_lhb_net_ratio_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """龙虎榜净买额占市场总成交比（榜单整体），5 日累计。"""
    daily = _daily_symbol_sum(
        data.get("lhb_detail"),
        {"net": ("lhb_net_buy", "sum"), "ma": ("market_amount", "median")},
    )
    daily = _daily_ratio(daily, "net", "ma")
    return _rolled_event_sum(_pivot(daily, calendar, "_ratio"), WINDOW_5D)


def _margin_pivot(
    calendar: pd.DatetimeIndex, margin: pd.DataFrame | None, col: str
) -> pd.DataFrame:
    if margin is None or margin.empty or col not in margin.columns:
        return _empty(calendar)
    mat = margin.pivot_table(
        index="trade_date", columns="symbol", values=col, aggfunc="sum"
    )
    return mat.reindex(index=pd.DatetimeIndex(calendar))


def pillar_margin_net_5d_z(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """融资净买入 5 日累计 z20（全市场密集序列；缺失日 NaN 不当 0）。

    单位万元不影响 z（量纲无关）。
    """
    mat = _margin_pivot(calendar, margin, "finance_net")
    if mat.empty:
        return mat
    s5 = mat.rolling(WINDOW_5D, min_periods=3).sum()
    return _zscore(s5)


def pillar_margin_bal_chg_20d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """融资余额 20 交易日变化率（存量口径：按标的 ffill 至最近披露值）。

    两融披露滞后行情 1 个交易日（截尾日常见，见 freshness.margin_last vs
    kline_last）；余额是**存量水平**，以最近披露值充当当日水平不构成前视，
    否则最新日整柱缺失。**净买流量列（finance_net）绝不 ffill**——流量填 0
    等于伪造「当日无净买」。
    """
    bal = _margin_pivot(calendar, margin, "finance_balance")
    if bal.empty:
        return bal
    bal = bal.ffill()
    prev = bal.shift(Z_WINDOW)
    return bal / prev.where(prev > 0) - 1.0


def pillar_dzjy_amount_ratio_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """大宗交易成交额占流通市值比，5 日累计。"""
    mat = _pivot(data.get("block_trade"), calendar, "amount_ratio")
    return _rolled_event_sum(mat, WINDOW_5D)


def pillar_dzjy_premium_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """大宗交易 5 日成交额加权折溢率（分数；正=溢价）。"""
    bt = data.get("block_trade")
    if bt is None or bt.empty:
        return _empty(calendar)
    use = bt[["trade_date", "symbol", "premium", "amount"]].copy()
    use = use[use["amount"] > 0]
    use["_num"] = use["premium"] * use["amount"]
    num5 = _rolled_event_sum(_pivot(use, calendar, "_num"), WINDOW_5D)
    den5 = _rolled_event_sum(_pivot(use, calendar, "amount"), WINDOW_5D)
    if num5.empty:
        return num5
    return num5 / den5.where(den5 > 0)


def _dzjy_inst_rows(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """大宗明细中机构专用作**买方**（接盘）的行。

    卖方为机构专用的是机构出货方向，不算接盘；无 seat_buyer 列（老数据）→ 空。
    """
    detail = data.get("dzjy_detail")
    if detail is None or detail.empty or "seat_buyer" not in detail.columns:
        return pd.DataFrame()
    m = detail["seat_buyer"].astype(str).str.contains("机构专用", na=False)
    return detail[m].copy()


def pillar_dzjy_inst_amount_ratio_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """机构专用接盘大宗成交额占流通市值比，5 日累计（细案 §3.6）。"""
    use = _dzjy_inst_rows(data)
    if use.empty:
        return _empty(calendar)
    return _rolled_event_sum(_pivot(use, calendar, "amount_ratio"), WINDOW_5D)


def pillar_dzjy_inst_premium_5d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """机构专用接盘大宗 5 日成交额加权折溢率（分数；正=溢价接盘）。"""
    use = _dzjy_inst_rows(data)
    if use.empty:
        return _empty(calendar)
    use = use[use["amount"] > 0].copy()
    use["_num"] = use["premium"] * use["amount"]
    num5 = _rolled_event_sum(_pivot(use, calendar, "_num"), WINDOW_5D)
    den5 = _rolled_event_sum(_pivot(use, calendar, "amount"), WINDOW_5D)
    if num5.empty:
        return num5
    return num5 / den5.where(den5 > 0)


def pillar_survey_inst_90d(
    calendar: pd.DatetimeIndex,
    data: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None,
) -> pd.DataFrame:
    """90 日历日接待机构家数累计（调研公告日口径）。"""
    mat = _pivot(data.get("survey"), calendar, "inst_count")
    if mat.empty:
        return mat
    val = mat.fillna(0.0).rolling(f"{SURVEY_WINDOW_DAYS}D").sum()
    ok = mat.notna().astype(float).rolling(f"{SURVEY_WINDOW_DAYS}D").sum() > 0
    return val.where(ok)


_PILLAR_FUNCS: dict[str, Callable[..., pd.DataFrame]] = {
    "inst_net_ratio_5d": pillar_inst_net_ratio_5d,
    "inst_net_buy_5d_z": pillar_inst_net_buy_5d_z,
    "lhb_net_ratio_5d": pillar_lhb_net_ratio_5d,
    "margin_net_5d_z": pillar_margin_net_5d_z,
    "margin_bal_chg_20d": pillar_margin_bal_chg_20d,
    "dzjy_amount_ratio_5d": pillar_dzjy_amount_ratio_5d,
    "dzjy_premium_5d": pillar_dzjy_premium_5d,
    "dzjy_inst_amount_ratio_5d": pillar_dzjy_inst_amount_ratio_5d,
    "dzjy_inst_premium_5d": pillar_dzjy_inst_premium_5d,
    "survey_inst_90d": pillar_survey_inst_90d,
}

#: 事件类原始表（池来源；两融由 z 阈值另入）
_EVENT_TABLES = ("lhb_detail", "lhb_inst", "block_trade", "survey")
#: 柱计算数据面 = 事件表 + 机构大宗明细（后者与 block_trade 同源同事件，
#: 只增机构买方视角、不入池——池窗口内的事件集合不变）
_PILLAR_DATA_TABLES = (*_EVENT_TABLES, "dzjy_detail")


def compute_pillars(
    calendar: pd.DatetimeIndex,
    raw: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None = None,
    names: Sequence[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """构建柱矩阵：{柱名: DataFrame(index=交易日, columns=标的, 原始值)}。"""
    use_names = tuple(names) if names is not None else PILLAR_CANDIDATES
    unknown = [n for n in use_names if n not in _PILLAR_FUNCS]
    if unknown:
        raise ValueError(f"未知柱 {unknown}（可选：{tuple(_PILLAR_FUNCS)}）")
    data = {
        name: align_events(raw.get(name, pd.DataFrame()), calendar)
        for name in _PILLAR_DATA_TABLES
    }
    return {name: _PILLAR_FUNCS[name](calendar, data, margin) for name in use_names}


# ---------------------------------------------------------------- 事件池 / 复合分


def compute_pool(
    calendar: pd.DatetimeIndex,
    raw: dict[str, pd.DataFrame],
    margin: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """事件驱动池：date×symbol 布尔。

    近 POOL_WINDOW 交易日任一事件（龙虎榜 ∪ 机构席位 ∪ 大宗 ∪ 调研）
    ∪ 两融 |净买 5 日 z20| ≥ MARGIN_Z_THRESHOLD。
    """
    cal = pd.DatetimeIndex(calendar)
    masks: list[pd.DataFrame] = []
    for name in _EVENT_TABLES:
        ev = align_events(raw.get(name, pd.DataFrame()), cal)
        if ev.empty:
            continue
        m = ev.assign(_one=1.0).pivot_table(
            index="trade_date", columns="symbol", values="_one", aggfunc="max"
        )
        m = m.reindex(index=cal)
        masks.append(m.rolling(POOL_WINDOW, min_periods=1).sum() > 0)
    z = pillar_margin_net_5d_z(cal, {}, margin)
    if not z.empty:
        masks.append(z.abs() >= MARGIN_Z_THRESHOLD)
    if not masks:
        return pd.DataFrame(index=cal, dtype=bool)
    cols = sorted(set().union(*[m.columns for m in masks]))
    out = pd.DataFrame(False, index=cal, columns=cols)
    for m in masks:
        # fill_value=False：reindex 不产生 NaN，避免 object 降型（bool 保持）
        out = out | m.reindex(index=cal, columns=cols, fill_value=False).astype(bool)
    return out


def compute_scores(
    date: pd.Timestamp,
    pool_mask: pd.DataFrame,
    pillars: dict[str, pd.DataFrame],
    spec: Sequence[tuple[str, int]],
) -> pd.DataFrame:
    """单日评分：池内成员 × 入柱 → 截面分位 0–100 与复合分。

    spec = ((柱名, 方向±1), …)：**负向柱取 100−分位**（方向=h10 实测 IC 符号，
    HK 六柱先例），使柱分高 = 历史有利方向；val_<柱> 保留原值供下钻展示。

    Returns [symbol, score, covered, n_pillars, val_<柱>, pct_<柱>…]；
    缺失柱 pct 记 NaN（展示「—」），复合分按 FILL_SCORE 计入并反映在 covered
    （covered = 有值柱数，界面上与 score 并列披露）。
    """
    spec = tuple(spec)
    names = [n for n, _ in spec]
    cols = [
        "symbol",
        "score",
        "covered",
        "n_pillars",
        *[f"val_{n}" for n in names],
        *[f"pct_{n}" for n in names],
    ]
    d = pd.Timestamp(date)
    if pool_mask is None or pool_mask.empty or d not in pool_mask.index:
        return pd.DataFrame(columns=cols)
    members = pool_mask.loc[d]
    syms = members.index[members.astype(bool)]
    if len(syms) == 0:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(index=pd.Index(syms, name="symbol"))
    pct_cols: list[str] = []
    for name, direction in spec:
        mat = pillars.get(name)
        if mat is None or mat.empty or d not in mat.index:
            vals = pd.Series(np.nan, index=syms, dtype=float)
        else:
            vals = mat.loc[d].reindex(syms)
        out[f"val_{name}"] = vals
        valid = vals.dropna()
        pct = pd.Series(np.nan, index=syms, dtype=float)
        if len(valid):
            raw = valid.rank(pct=True) * 100.0
            pct.loc[valid.index] = raw if direction >= 0 else 100.0 - raw
        out[f"pct_{name}"] = pct
        pct_cols.append(f"pct_{name}")
    out["covered"] = out[pct_cols].notna().sum(axis=1)
    out["n_pillars"] = len(spec)
    out["score"] = out[pct_cols].fillna(FILL_SCORE).mean(axis=1).round(2)
    return out.reset_index()[cols]
