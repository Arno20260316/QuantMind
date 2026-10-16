"""聪明钱分析引擎（机构级 v2）——**纯函数层**，不触 IO、不读环境。

构建端（backend/scripts/market_analysis/build_smart_money.py）负责组装
输入并落盘 JSON（track_record.json / event_study.json / seats.json）；
单测用合成金样直接喂 DataFrame。

口径（改动即破坏证据与前沿，须同步金样 + 细案 §3）：
- 前向收益 fwd(t,h) = close[t+h]/close[t+1] − 1（T 日盘后信号 → T+1 收盘
  入场；h=2 即 1 个交易日持有、h=5 即 4 个交易日持有——**h 是索引差不是
  持有天数**），与验证脚本逐字同款（smart_money_validate.py:141）。
  展示面「h5/h10」必须与 evidence.json 的 h5/h10 同口径，否则证据台与
  图表对不上；前端文案用「T+k 收盘」标注，不写「k 日持有」。
  h=1 同价出场恒 0（原始收益），一律不出展示。clip(−0.5, +1.0) 防仙股；
  入场价 0（停牌脏价）→ NaN（验证脚本 div 出 inf 仅靠 clip 兜底，这里更严）
- **超额** = 标的收益 − 全市场等权（同一 close 面板、同一 h）。注意：这是
  展示层口径（「跑赢大盘没有」）；验证面 evidence.json 的 IC/q5−q1 是裸
  收益口径，两者不可混引
- 视线未走完（末日/h 未闭合）→ NaN → 输出 None（「持有中」），绝不填 0
- 分数一律用 v1 compute_scores 的 score 列（0–100 复合分）
- 输出 JSON-ready：日期 'YYYY-MM-DD' 字符串、float 保留 None（无 NaN）

席位分类（classify_seat）：机构专用 / 陆股通 / 券商席位 / 其他——只按
**名称文本**归类（数据面事实），不做任何胜率以外的推断。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from backend.shared.smart_money.signals import align_events
from backend.shared.stock_utils import StockCodeUtil

log = logging.getLogger("smart_money.analytics")

# ---------------------------------------------------------------- 常量
#: 追踪组合头数（每日按 score 降序取前 N）
TRACK_TOP_N = 10
#: 追踪视野（索引差 h；h=2 ⇔ 1 个交易日持有；h=1 恒 0 故不用）
TRACK_HORIZONS = (2, 3, 5, 10)
#: 追踪汇总窗（近端，交易日）；全史由 None 表示（输出 label "all"）
TRACK_WINDOWS: tuple[int, ...] = (20, 60, 120)
#: 追踪案例回看（近端交易日）
TRACK_RECENT_DAYS = 10
TRACK_RECENT_TOP = 5

#: 分箱（右开；末箱上界 100.01 兜住 score=100）
SCORE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 50.0),
    (50.0, 70.0),
    (70.0, 85.0),
    (85.0, 100.01),
)
BUCKET_LABELS = ("<50", "50-70", "70-85", "85+")

#: 事件族 CAR 点数（k 从 2 起：T+2..T+car_days+1 收盘，k=2 ⇔ 1 日持有）
CAR_DAYS = 20
#: IC 衰减视野（索引差 h，同验证脚本；h=1 退化剔除）
IC_HORIZONS = (2, 3, 5, 10, 20)
IC_ROLLING_H = 10  # 滚动 IC 用入柱视野（与 evidence 口径一致）
IC_ROLLING_WINDOW = 60
MIN_IC_NAMES = 30

#: 席位榜最小净买样本（不足不进入 seats 列表，仍计入 classes 分席统计）
SEAT_MIN_TRADES = 5
#: 席位榜窗口（交易日；0=全史；与 HK 聪明钱 SEAT_WINDOWS 同构，
#: 端点 window=20/60/all 与这里同源）
SEAT_WINDOWS: tuple[int, ...] = (20, 60, 0)
#: seats 全量列表上限（读侧端点在列表内过滤/排序；超出按全史胜率截断）
SEAT_LIST_CAP = 300
SEAT_CLASS_LABELS: dict[str, str] = {
    "inst": "机构专用",
    "north": "陆股通",
    "branch": "券商席位",
    "other": "其他",
}

#: 行业资金流窗（交易日）
INDUSTRY_WINDOW = 20


def _r(v: Any, nd: int = 4) -> float | None:
    """NaN/None → None；其余 round 后转 float（JSON 安全）。"""
    if v is None:
        return None
    f = float(v)
    if not np.isfinite(f):
        return None
    return round(f, nd)


# ---------------------------------------------------------------- 基础：前向收益


def forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """fwd(t,h) = close[t+h]/close[t+1] − 1，clip(−0.5, +1.0)（h≥2）。

    h<2 返回全 NaN（h=1 同价出场恒 0，无意义不算）；末 h 天自然 NaN（未闭合）。
    实现与验证脚本 smart_money_validate.py 逐算式同款（div-by-0 记 NaN，
    比其 inf→clip 兜底更严）。
    """
    if close.empty or horizon < 2:
        return pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    entry = close.shift(-1)
    exit_ = close.shift(-horizon)
    fwd = exit_.div(entry.where(entry != 0)) - 1.0
    return fwd.clip(lower=-0.5, upper=1.0)


def market_ew(fwd: pd.DataFrame) -> pd.Series:
    """全市场等权收益（行内有效标的均值）。"""
    if fwd.empty:
        return pd.Series(dtype=float)
    return fwd.mean(axis=1)


def excess_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """逐标的超额收益矩阵 = fwd − 当日全市场等权。"""
    fwd = forward_returns(close, horizon)
    if fwd.empty:
        return fwd
    return fwd.sub(market_ew(fwd), axis=0)


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """秩相关（不引 scipy：rank 后 pearson；并列取平均秩）。"""
    m = a.notna() & b.notna()
    if int(m.sum()) < 3:
        return float("nan")
    ra, rb = a[m].rank(), b[m].rank()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(ra.corr(rb))


def _indexers(
    mat: pd.DataFrame, pairs: Sequence[tuple[pd.Timestamp, str]]
) -> tuple[np.ndarray, np.ndarray]:
    """(日, 标的) 对 → (行位, 列位)；缺失记 -1（调用侧过滤）。"""
    idx = pd.MultiIndex.from_tuples([(pd.Timestamp(d), s) for d, s in pairs])
    rows = mat.index.get_indexer(idx.get_level_values(0))
    cols = mat.columns.get_indexer(idx.get_level_values(1))
    ok = (rows >= 0) & (cols >= 0)
    return rows[ok], cols[ok]


# ---------------------------------------------------------------- 追踪（Top-N 组合）


def track_record(
    close: pd.DataFrame,
    scores_by_date: Mapping[pd.Timestamp, pd.DataFrame],
    *,
    names: pd.Series | None = None,
    top_n: int = TRACK_TOP_N,
    horizons: Sequence[int] = TRACK_HORIZONS,
    recent_days: int = TRACK_RECENT_DAYS,
    recent_top: int = TRACK_RECENT_TOP,
    bucket_min_names: int = 10,
) -> dict:
    """每日 Top-N 组合的历史追踪（「历史那些聪明钱有多聪明」）。

    输出段落（前端 TrackRecordSection 消费）：
    - daily[]：逐日组合收益 / 基准 / 超额（各 h）
    - windows[]：近 20/60/120/全史 汇总（均值超额 bp、胜率=超额>0 占比、均值收益）
    - cum_excess{h}[]：逐日超额累加曲线（**展示口径**：重叠窗叠加，非无重叠
      资金曲线，图上须标注）
    - buckets：分数分箱 → 平均 h5 超额（按日先均值再跨日平均，等权防日大户）
      + 单调性（箱中值 vs 箱均值 的 pearson 秩一致度 ∈ [−1,1]）
    - recent[]：最近 recent_days 个计分日 × Top-recent_top 案例，含
      「已实现 h10」/「持有中」（附至今浮动收益 ret_so_far、持有天数）

    基准 = 全市场等权（同一 close 面板，含未评分标的）。
    """
    cal = close.index
    horizons = tuple(horizons)
    empty = {
        "top_n": top_n,
        "horizons": list(horizons),
        "benchmark": "全市场等权",
        "daily": [],
        "windows": [],
        "cum_excess": {},
        "buckets": {
            "labels": list(BUCKET_LABELS),
            "mean_excess_bp": [],
            "n_days": [],
            "monotonicity": None,
        },
        "recent": [],
    }
    if close.empty or not scores_by_date:
        return empty

    # ---- 逐日组合（h 外层循环：峰值只驻留一个 fwd 矩阵）
    by_h: dict[int, dict[pd.Timestamp, tuple[float | None, float | None]]] = {
        h: {} for h in horizons
    }
    for h in horizons:
        fwd = forward_returns(close, h)
        bench = market_ew(fwd)
        for d, sdf in scores_by_date.items():
            d = pd.Timestamp(d)
            if sdf is None or sdf.empty or d not in cal:
                continue
            syms = [
                s
                for s in sdf.nlargest(top_n, "score")["symbol"].tolist()
                if s in close.columns
            ]
            ret = None
            if syms:
                basket = fwd.loc[d, syms].dropna()
                if len(basket):
                    ret = float(basket.mean())
            b = bench.get(d)
            by_h[h][d] = (ret, float(b) if b is not None and pd.notna(b) else None)

    days = sorted(set().union(*[set(m) for m in by_h.values()])) if horizons else []
    daily: list[dict[str, Any]] = []
    for d in days:
        row: dict[str, Any] = {"date": d.strftime("%Y-%m-%d")}
        for h in horizons:
            ret, b = by_h[h].get(d, (None, None))
            row[f"ret_{h}"] = _r(ret, 5)
            row[f"bench_{h}"] = _r(b, 5)
            row[f"excess_{h}"] = (
                _r(ret - b, 5) if ret is not None and b is not None else None
            )
        daily.append(row)

    # ---- 窗口汇总
    def _summary(rows: list[dict], h: int) -> dict:
        exc = [r[f"excess_{h}"] for r in rows if r.get(f"excess_{h}") is not None]
        ret = [r[f"ret_{h}"] for r in rows if r.get(f"ret_{h}") is not None]
        if not exc:
            return {
                "n_days": 0,
                "mean_excess_bp": None,
                "hit_rate": None,
                "mean_ret_bp": None,
            }
        arr = np.asarray(exc, dtype=float)
        return {
            "n_days": len(exc),
            "mean_excess_bp": _r(arr.mean() * 1e4, 1),
            "hit_rate": _r((arr > 0).mean(), 3),
            "mean_ret_bp": _r(float(np.mean(ret)) * 1e4, 1) if ret else None,
        }

    windows: list[dict] = []
    for w in (*TRACK_WINDOWS, None):
        rows = daily if w is None else daily[-w:]
        windows.append(
            {
                "window": "all" if w is None else f"{w}d",
                "h5": _summary(rows, 5),
                "h10": _summary(rows, 10) if 10 in horizons else None,
            }
        )

    # ---- 累计超额曲线（重叠窗叠加，展示口径）
    cum_excess: dict[str, list[dict]] = {}
    for h in horizons:
        acc = 0.0
        series: list[dict] = []
        for r in daily:
            v = r[f"excess_{h}"]
            if v is None:
                continue
            acc += v
            series.append({"date": r["date"], "cum": _r(acc, 4)})
        cum_excess[f"h{h}"] = series

    # ---- 分箱（h5 超额；按日等权，防单日大池主导）
    bucket_acc: list[list[float]] = [[] for _ in SCORE_BUCKETS]
    e5 = excess_returns(close, 5)
    for d, sdf in scores_by_date.items():
        d = pd.Timestamp(d)
        if sdf is None or sdf.empty or d not in cal or d not in e5.index:
            continue
        sc = sdf.set_index("symbol")["score"]
        row = e5.loc[d]
        common = sc.index.intersection(row.dropna().index)
        if len(common) < bucket_min_names:
            continue
        scores, vals = sc.loc[common], row.loc[common]
        for i, (lo, hi) in enumerate(SCORE_BUCKETS):
            m = (scores >= lo) & (scores < hi)
            if int(m.sum()):
                bucket_acc[i].append(float(vals[m].mean()))
    mids = [round((lo + min(hi, 100.0)) / 2, 1) for lo, hi in SCORE_BUCKETS]
    means = [float(np.mean(a)) if a else float("nan") for a in bucket_acc]
    valid = np.isfinite(means)
    mono = None
    if int(valid.sum()) >= 3:
        mono = _r(
            float(np.corrcoef(np.asarray(mids)[valid], np.asarray(means)[valid])[0, 1]),
            3,
        )
    buckets = {
        "labels": list(BUCKET_LABELS),
        "midpoints": mids,
        "mean_excess_bp": [_r(m * 1e4, 1) for m in means],
        "n_days": [len(a) for a in bucket_acc],
        "monotonicity": mono,
    }

    # ---- 最近案例（含持有中）
    recent: list[dict] = []
    if days:
        fwd_cache = {
            h: forward_returns(close, h) for h in sorted({2, 5, 10} & set(horizons))
        }
        name_series = names if names is not None else pd.Series(dtype=str)
        last_pos = len(cal) - 1
        for d in sorted(scores_by_date)[-recent_days:]:
            d = pd.Timestamp(d)
            sdf = scores_by_date.get(d)
            if sdf is None or sdf.empty or d not in cal:
                continue
            pos = cal.get_loc(d)
            for _, srow in sdf.nlargest(recent_top, "score").iterrows():
                sym = srow["symbol"]
                item: dict[str, Any] = {
                    "date": d.strftime("%Y-%m-%d"),
                    # 展示/API 契约=前缀式（同 items.build_items）；scores 内部为后缀式，
                    # 这里归一，前端点击行可直接走个股终端浮窗
                    "symbol": StockCodeUtil.to_prefix(str(sym)) or str(sym),
                    "name": str(name_series.get(sym, "")) or None,
                    "score": _r(srow["score"], 2),
                }
                for h in sorted(fwd_cache):
                    item[f"ret_{h}"] = (
                        _r(fwd_cache[h].at[d, sym], 5) if sym in close.columns else None
                    )
                mature10 = pos + 10 <= last_pos
                ret_so_far = None
                if sym in close.columns and pos + 1 <= last_pos:
                    entry = close[sym].iloc[pos + 1]
                    if pd.notna(entry) and entry != 0:
                        ret_so_far = _r(close[sym].iloc[last_pos] / entry - 1.0, 5)
                item["status"] = "已实现h10" if mature10 else "持有中"
                item["hold_days"] = 0 if mature10 else max(last_pos - (pos + 1), 0)
                item["ret_so_far"] = ret_so_far
                recent.append(item)

    return {
        "top_n": top_n,
        "horizons": list(horizons),
        "benchmark": "全市场等权",
        "daily": daily,
        "windows": windows,
        "cum_excess": cum_excess,
        "buckets": buckets,
        "recent": recent,
    }


# ---------------------------------------------------------------- 事件族 CAR


def event_study(
    close: pd.DataFrame,
    scores_by_date: Mapping[pd.Timestamp, pd.DataFrame],
    events_by_family: Mapping[str, pd.DataFrame],
    *,
    car_days: int = CAR_DAYS,
    min_daily_names: int = 10,
) -> dict:
    """事件族 CAR（T+1 入场，超额=减全市场等权）：族内当日截面分位 q5 vs q1。

    回答的问题：同一类事件（龙虎榜/机构席位/大宗/调研）发生后，
    **当日复合分高的那一档** 是否走出持续更强的超额路径。

    - 事件 = 族表内 (trade_date, symbol) 去重；日期归到 ≥事件日首个交易日
      （非交易日公告归下一交易日，信息开盘前可得，非前视）
    - 分位 = 事件标的在**当日池内**的 score 百分位；q5≥80、q1≤20
    - 输出 car_q5_bp / car_q1_bp / spread_bp：car_days 个点，第 i 点对应
      k=i+2（T+k 收盘出场；k=2 ⇔ 1 个交易日持有），None=该日不足样本
    """
    cal = close.index
    out: dict[str, Any] = {
        "car_days": car_days,
        "benchmark": "全市场等权",
        "families": {},
    }
    if close.empty:
        return out

    for fam, ev in events_by_family.items():
        rec: dict[str, Any] = {
            "n_events": 0,
            "n_scored": 0,
            "car_q5_bp": [],
            "car_q1_bp": [],
            "spread_bp": [],
        }
        out["families"][fam] = rec
        if ev is None or ev.empty:
            continue
        ev = align_events(ev[["trade_date", "symbol"]].drop_duplicates(), cal)
        rec["n_events"] = int(len(ev))
        q5: list[tuple[pd.Timestamp, str]] = []
        q1: list[tuple[pd.Timestamp, str]] = []
        for d, grp in ev.groupby("trade_date"):
            sdf = scores_by_date.get(pd.Timestamp(d))
            if sdf is None or sdf.empty:
                continue
            # 分位分母 = **当日全池**（该日全部计分标的），不是事件子集；
            # 分位本身是展示口径「在这批标的里排多前」，事件只是从中取样
            pool = sdf.set_index("symbol")["score"]
            if len(pool) < min_daily_names:
                continue
            pct = pool.rank(pct=True) * 100.0
            for sym, p in pct.reindex(grp["symbol"]).dropna().items():
                (q5 if p >= 80.0 else q1 if p <= 20.0 else []).append(
                    (pd.Timestamp(d), sym)
                )
        rec["n_scored"] = len(q5) + len(q1)
        rec["n_q5"], rec["n_q1"] = len(q5), len(q1)
        for label, group in (("car_q5_bp", q5), ("car_q1_bp", q1)):
            series: list[float | None] = []
            if not group:
                rec[label] = [None] * car_days
                continue
            for k in range(2, car_days + 2):
                fwd = forward_returns(close, k)
                bench = market_ew(fwd)
                rows, cols = _indexers(fwd, group)
                if len(rows) == 0:
                    series.append(None)
                    continue
                v = fwd.to_numpy()[rows, cols]
                b = bench.reindex(fwd.index).to_numpy()[rows]
                ok = np.isfinite(v) & np.isfinite(b)
                series.append(
                    _r(float(np.mean(v[ok] - b[ok])) * 1e4, 1) if ok.any() else None
                )
            rec[label] = series
        rec["spread_bp"] = [
            _r(a - b, 1) if a is not None and b is not None else None
            for a, b in zip(rec["car_q5_bp"], rec["car_q1_bp"], strict=True)
        ]
    return out


# ---------------------------------------------------------------- IC 衰减 / 滚动


def ic_decay(
    close: pd.DataFrame,
    scores_by_date: Mapping[pd.Timestamp, pd.DataFrame],
    *,
    horizons: Sequence[int] = IC_HORIZONS,
    min_names: int = MIN_IC_NAMES,
    rolling_h: int = IC_ROLLING_H,
    rolling_window: int = IC_ROLLING_WINDOW,
) -> dict:
    """复合分 IC 随视野衰减 + 滚动 IC（默认 h10、60 交易日窗）。

    IC = 当日 score 与 fwd(t,h) 的秩相关（裸收益口径，与 evidence 一致）；
    n_days = 有效日数（当日池内 ≥ min_names 且有可算样本）。
    """
    cal = close.index
    horizons = tuple(horizons)
    out: dict[str, Any] = {
        "horizons": list(horizons),
        "min_names": min_names,
        "decay": [],
        "rolling": {"h": rolling_h, "window": rolling_window, "series": []},
    }
    if close.empty or not scores_by_date:
        return out

    per_h: dict[int, list[tuple[pd.Timestamp, float]]] = {h: [] for h in horizons}
    for h in horizons:
        fwd = forward_returns(close, h)
        for d, sdf in scores_by_date.items():
            d = pd.Timestamp(d)
            if sdf is None or sdf.empty or d not in cal:
                continue
            sc = sdf.set_index("symbol")["score"]
            f = fwd.loc[d]
            common = sc.index.intersection(f.dropna().index)
            if len(common) < min_names:
                continue
            ic = _spearman(sc.loc[common], f.loc[common])
            if pd.notna(ic):
                per_h[h].append((d, float(ic)))

    for h in horizons:
        vals = np.asarray([v for _, v in per_h[h]], dtype=float)
        if len(vals) == 0:
            out["decay"].append(
                {"h": h, "ic": None, "icir": None, "n_days": 0, "ic_pos_share": None}
            )
            continue
        mean = float(vals.mean())
        std = float(vals.std(ddof=1)) if len(vals) > 1 else float("nan")
        out["decay"].append(
            {
                "h": h,
                "ic": _r(mean, 4),
                "icir": _r(mean / std, 3) if np.isfinite(std) and std > 0 else None,
                "n_days": len(vals),
                "ic_pos_share": _r(float((vals > 0).mean()), 3),
            }
        )

    roll = pd.Series(dict(per_h.get(rolling_h, []))).sort_index()
    if len(roll):
        rm = roll.rolling(rolling_window, min_periods=min(rolling_window, 20)).mean()
        out["rolling"]["series"] = [
            {"date": d.strftime("%Y-%m-%d"), "ic": _r(v, 4)}
            for d, v in rm.dropna().items()
        ]
    return out


# ---------------------------------------------------------------- 席位（分类 + 胜率榜）


def classify_seat(seat_name: object) -> str:
    """席位名称 → 类别（inst / north / branch / other）。只按文本，不做推断。"""
    n = str(seat_name or "")
    if "机构专用" in n:
        return "inst"
    if "股通专用" in n or "沪股通" in n or "深股通" in n:
        return "north"
    if "证券" in n or "营业部" in n or "总部" in n or "分公司" in n:
        return "branch"
    return "other"


def _window_floor(cal: pd.DatetimeIndex, window: int) -> pd.Timestamp | None:
    """窗起点（含）：0=全史；日历不足 window 天 → None（退化为全史，不虚报短窗）。

    与 HK 聪明钱 ``_window_cutoff`` 同一语义（那边返回字符串截断点，
    这里返回 Timestamp 供逐窗切片；单侧改动须两侧同步）。
    """
    if window <= 0 or len(cal) <= window:
        return None
    return cal[-window]


def seat_stats(
    seat: pd.DataFrame,
    close: pd.DataFrame,
    *,
    horizon: int = 5,
    min_trades: int = SEAT_MIN_TRADES,
    windows: Sequence[int] = SEAT_WINDOWS,
    list_cap: int = SEAT_LIST_CAP,
) -> dict:
    """席位胜率榜（三档窗口：近 20 / 近 60 / 全史）。

    - 事件 = (日, 股, 席位) 净买入（Σ买入−Σ卖出 > 0；同日买卖都现则相抵）
    - 样本 = **已闭合**（该股 h 视野走完，超额可算）；未闭合不计入分母，
      净买总数在顶层 n_events 披露（n 列 = 已闭合样本数）
    - 每席位 × 每窗 {n, win_rate, avg_excess_bp}；窗 = 交易日回溯（0=全史），
      日历不足该窗天数时退化为全史（不虚报短窗；与 HK 席位口径一致）
    - baseline_win_rate 同窗全市场 (日,股) h 超额 > 0 占比（「比大盘强多少」）
    - seats 列表 = 全史已闭合样本 ≥ min_trades 的席位，按全史胜率截断至
      list_cap（读侧端点在这份列表内按窗/类过滤排序；本函数不预排序展示）
    - last_seen = 最近**已闭合**事件日（未闭合事件不入榜，故可能落后实际）
    """
    labels = ["all" if w <= 0 else str(w) for w in windows]
    out: dict[str, Any] = {
        "horizon": horizon,
        "min_trades": min_trades,
        "windows": labels,
        "as_of": close.index[-1].strftime("%Y-%m-%d") if len(close.index) else None,
        "baseline_win_rate": dict.fromkeys(labels),
        "n_events": 0,
        "classes": [],
        "seats": [],
    }
    if seat is None or seat.empty or close.empty:
        return out

    e = excess_returns(close, horizon)
    arr = e.to_numpy()
    finite = np.isfinite(arr)
    for label, w in zip(labels, windows, strict=True):
        floor = _window_floor(close.index, w)
        if floor is None:
            mask = finite
        else:
            # 注：DatetimeIndex >= Timestamp 返回 ndarray（不是索引），无 .to_numpy()
            date_mask = np.asarray(e.index >= floor)
            mask = finite & date_mask[:, None]
        out["baseline_win_rate"][label] = (
            _r(float((arr[mask] > 0).mean()), 3) if mask.any() else None
        )

    df = seat.copy()
    for c in ("buy_amount", "sell_amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    own = df.groupby(["trade_date", "symbol", "seat_name"], as_index=False).agg(
        buy=("buy_amount", "sum"), sell=("sell_amount", "sum")
    )
    own["net"] = own["buy"] - own["sell"]
    nb = own[own["net"] > 0].copy()
    out["n_events"] = int(len(nb))
    if nb.empty:
        return out
    nb["dt"] = pd.to_datetime(nb["trade_date"], errors="coerce")
    nb = nb.dropna(subset=["dt"]).drop(columns=["trade_date"])  # 防 merge 列名冲突

    # 对齐超额：仅取 (日,股) 同时落在 close 面板的
    # 注：future_stack 保留 NaN 行（pandas 3 移除 dropna 参数），显式丢弃
    long = (
        e.rename_axis("trade_date")
        .rename_axis("symbol", axis=1)
        .stack(future_stack=True)
        .rename("exc")
        .reset_index()
        .dropna(subset=["exc"])
    )
    merged = nb.merge(
        long, left_on=["dt", "symbol"], right_on=["trade_date", "symbol"], how="inner"
    )
    if merged.empty:
        return out

    floors = {
        label: _window_floor(close.index, w)
        for label, w in zip(labels, windows, strict=True)
    }

    def _win_metrics(g: pd.DataFrame, floor: pd.Timestamp | None) -> dict:
        sub = g if floor is None else g[g["dt"] >= floor]
        if sub.empty:
            return {"n": 0, "win_rate": None, "avg_excess_bp": None}
        return {
            "n": int(len(sub)),
            "win_rate": _r(float((sub["exc"] > 0).mean()), 3),
            "avg_excess_bp": _r(float(sub["exc"].mean()) * 1e4, 1),
        }

    rows: list[dict] = []
    for name, g in merged.groupby("seat_name"):
        cls = classify_seat(name)
        rows.append(
            {
                "seat_name": str(name),
                "class": cls,
                "class_label": SEAT_CLASS_LABELS.get(cls, cls),
                "total_net_yi": _r(float(g["net"].sum()) / 1e8, 2),
                "last_seen": g["dt"].max().strftime("%Y-%m-%d"),
                "windows": {lab: _win_metrics(g, floors[lab]) for lab in labels},
            }
        )
    ranked = [r for r in rows if r["windows"]["all"]["n"] >= min_trades]
    ranked.sort(
        key=lambda r: (
            # min_trades=0 时全史 n=0 的席位 win_rate=None：沉底（−1）不炸排序
            r["windows"]["all"]["win_rate"]
            if r["windows"]["all"]["win_rate"] is not None
            else -1.0,
            r["windows"]["all"]["n"],
            r["total_net_yi"] or 0.0,
        ),
        reverse=True,
    )
    out["seats"] = ranked[:list_cap]

    classes: list[dict] = []
    merged["class"] = [classify_seat(s) for s in merged["seat_name"]]
    for cls, g in merged.groupby("class"):
        classes.append(
            {
                "class": cls,
                "label": SEAT_CLASS_LABELS.get(cls, cls),
                "n_events": int(len(g)),
                "n_seats": int(g["seat_name"].nunique()),
                "win_rate": _r(float((g["exc"] > 0).mean()), 3),
                "avg_excess_bp": _r(float(g["exc"].mean()) * 1e4, 1),
                "total_net_yi": _r(float(g["net"].sum()) / 1e8, 1),
            }
        )
    classes.sort(key=lambda c: -c["n_events"])
    out["classes"] = classes
    return out


# ---------------------------------------------------------------- 行业资金流


def industry_flow(
    industry: pd.Series,
    calendar: pd.DatetimeIndex,
    *,
    lhb: pd.DataFrame | None = None,
    inst: pd.DataFrame | None = None,
    dzjy: pd.DataFrame | None = None,
    scores_latest: pd.DataFrame | None = None,
    window: int = INDUSTRY_WINDOW,
) -> list[dict]:
    """近 window 交易日的行业级机构资金流（榜单整体 / 机构席位 / 大宗机构专用）。

    - lhb_net_yi：龙虎榜净买额（lhb_detail.lhb_net_buy）
    - inst_net_yi：机构席位净买额（lhb_inst.inst_net_buy）
    - dzjy_inst_net_yi：大宗「机构专用」净承接（买方为机构 +，卖方为机构 −）
    - n_stocks：三类事件涉及的标的数（去重）
    - avg_score：最新计分日该行业入选标的的平均复合分（未评分行业为 None）
    """
    cal = pd.DatetimeIndex(calendar)
    if len(cal) == 0:
        return []
    floor = cal[-window] if len(cal) >= window else cal[0]
    floor_s = floor.strftime("%Y-%m-%d")

    def _net_by_industry(df: pd.DataFrame | None, col: str) -> pd.Series:
        if df is None or df.empty or col not in df.columns:
            return pd.Series(dtype=float)
        sub = df[df["trade_date"] >= floor_s]
        if sub.empty:
            return pd.Series(dtype=float)
        by_sym = sub.groupby("symbol")[col].sum()
        ind = industry.reindex(by_sym.index)
        return by_sym.groupby(ind).sum() / 1e8

    lhb_net = _net_by_industry(lhb, "lhb_net_buy")
    inst_net = _net_by_industry(inst, "inst_net_buy")

    dzjy_net = pd.Series(dtype=float)
    n_stocks_map: dict[Any, set] = {}
    if dzjy is not None and not dzjy.empty:
        sub = dzjy[dzjy["trade_date"] >= floor_s].copy()
        if not sub.empty:
            sub["amount"] = pd.to_numeric(sub["amount"], errors="coerce").fillna(0.0)
            buy_inst = sub["seat_buyer"].astype(str).str.contains("机构专用", na=False)
            sell_inst = (
                sub["seat_seller"].astype(str).str.contains("机构专用", na=False)
            )
            only = sub[buy_inst | sell_inst].copy()
            if not only.empty:
                sign = np.where(buy_inst[only.index], 1.0, 0.0) - np.where(
                    sell_inst[only.index], 1.0, 0.0
                )
                only["net"] = only["amount"] * sign
                by_sym = only.groupby("symbol")["net"].sum()
                dzjy_net = by_sym.groupby(industry.reindex(by_sym.index)).sum() / 1e8
                for sym in only["symbol"].unique():
                    n_stocks_map.setdefault(industry.get(sym), set()).add(sym)

    for df in (lhb, inst):
        if df is None or df.empty:
            continue
        sub = df[df["trade_date"] >= floor_s]
        for sym in sub["symbol"].unique():
            key = industry.get(sym, pd.NA)
            if pd.isna(key):
                continue
            n_stocks_map.setdefault(key, set()).add(sym)

    score_by_ind = pd.Series(dtype=float)
    if scores_latest is not None and not scores_latest.empty:
        # grouper 与 scores_latest 行位对齐（reindex 结果的 index 是股票代码，
        # 不是 0..n-1，直接 groupby 会整表对不上 → 空组）
        ind = pd.Series(
            industry.reindex(scores_latest["symbol"]).to_numpy(),
            index=scores_latest.index,
        )
        score_by_ind = scores_latest.groupby(ind)["score"].mean()

    keys = (
        set(lhb_net.index)
        | set(inst_net.index)
        | set(dzjy_net.index)
        | set(n_stocks_map)
    )
    rows: list[dict] = []
    for k in keys:
        if pd.isna(k):
            continue
        rows.append(
            {
                "industry": str(k),
                "lhb_net_yi": _r(lhb_net.get(k), 2),
                "inst_net_yi": _r(inst_net.get(k), 2),
                "dzjy_inst_net_yi": _r(dzjy_net.get(k), 2),
                "n_stocks": len(n_stocks_map.get(k, ())),
                "avg_score": _r(score_by_ind.get(k), 1),
            }
        )
    rows.sort(
        key=lambda r: abs((r["inst_net_yi"] or 0.0) + (r["dzjy_inst_net_yi"] or 0.0)),
        reverse=True,
    )
    return rows
