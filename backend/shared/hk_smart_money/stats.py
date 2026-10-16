"""港股聪明钱 · 统计层（信号面板 → 双过闸评估 → 席位收缩 → 复合分）。

口径（细案 §3.2–§3.5）：

- **信号定义域**：某票某类型**当日或前日**持仓 qty>0 才定义信号；从未持有记
  缺失（绝不填 0）。域内无流 = 0（事实零变动，事件闸已做去噪）。
- **4 变体**：net5_z=Σ5d净流/(std20d 日净流，std=0→NaN) / net5_tt / net20_tt
  （成交额归一）/ breadth5=(Σ5d 净增席位 − 净减席位)÷Σ5d 有变动席位（CCASS 5 类；
  南向单通道改用 chg5 = qty/qty(t−5)−1）。
- **双过闸**（照抄 hk_opportunity_ic_check 判据）：sign(IC)==sign(q5−q1) ∧
  |IC|≥0.01 ∧ |q5−q1|>1bp ∧ n_days≥60；IC 对 raw fwd，五分位超额对
  **当日全市场中位**（datasets.forward_matrices 单源——事件层 fwd_exc 列、
  评估层 exc 共用同一实现）。
- **增持/减持不对称**：buy_hit=P(exc>0|分位 top20%)、sell_hit=P(exc<0|bottom20%)、
  asym_bp=q5_bp+q1_bp（增持边际 − 减持反向边际，>0 = 增持更聪明）。
- **席位收缩**：收缩到**中性**（0bp / 50%）而非类型先验（先验会压平同类型席位
  间差异），w=n/(n+20)；命中率/席位统计只用 episode 首日（episode_lead==1）。
- **复合分**：仅 admitted 信号；分位在信号自身定义域截面内计算，负向取
  100−pct；等权平均；covered<1 不发布分数（缺失绝不填 50）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from backend.shared.hk_smart_money import (
    ADMIT_HORIZON,
    CCASS_CATEGORIES,
    CCASS_SIGNALS,
    CORR_COLLAPSE,
    HORIZONS,
    MIN_ABS_IC,
    MIN_DAYS_TAIL,
    MIN_NAMES_PER_DAY,
    MIN_Q51_BP,
    MIN_SEG_DAYS,
    SCORE_MIN_COVERED,
    SEAT_MIN_EVENTS,
    SEAT_NEUTRAL_EXC,
    SEAT_NEUTRAL_HIT,
    SEAT_SHRINK_K,
    SEAT_WINDOWS,
    SIGNAL_KEY_SEP,
    SOUTH_SIGNALS,
)
from backend.shared.hk_smart_money import datasets as ds

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 面板构建


def _flows_field(
    flows: pd.DataFrame,
    field: str,
    calendar: pd.DatetimeIndex,
    symbols: pd.Index,
    category: str,
) -> pd.DataFrame:
    """单字段 日×票 矩阵（缺失 → NaN；调用方按定义域填 0）。

    索引 = calendar（DatetimeIndex）：面板与 close/amount 同轴，否则 mask/相除
    因索引错位静默全 NaN（2026-10-16 事故）。产物 dt 列是 YYYYMMDD 字符串，
    转 datetime 后再 reindex。
    """
    sub = flows[flows["category"] == category]
    if sub.empty:
        return pd.DataFrame(np.nan, index=calendar, columns=symbols, dtype="float64")
    piv = sub.pivot_table(
        index="dt", columns="symbol", values=field, aggfunc="sum", observed=False
    )
    piv.index = pd.to_datetime(piv.index, format="%Y%m%d")
    return piv.reindex(index=calendar, columns=symbols)


def _holdings_presence(
    holdings: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    symbols: pd.Index,
    category: str,
) -> pd.DataFrame:
    """日×票 持仓存在掩码（某类型 qty>0 的票；索引 = calendar，口径同上）。"""
    sub = holdings[(holdings["category"] == category) & (holdings["qty_sum"] > 0)]
    if sub.empty:
        return pd.DataFrame(False, index=calendar, columns=symbols, dtype="bool")
    piv = sub.assign(one=1).pivot_table(
        index="dt", columns="symbol", values="one", aggfunc="max", observed=False
    )
    piv.index = pd.to_datetime(piv.index, format="%Y%m%d")
    return piv.reindex(index=calendar, columns=symbols).notna()


def _domain(presence: pd.DataFrame) -> pd.DataFrame:
    """信号定义域 = 当日或前日持仓（前日用于承接清仓日的流出）。"""
    return presence | presence.shift(1, fill_value=False)


def _rolling_denoms(amt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        amt.rolling(5, min_periods=5).sum(),
        amt.rolling(20, min_periods=20).sum(),
    )


def _variants_from_flow(
    net: pd.DataFrame,
    up: pd.DataFrame | None,
    dn: pd.DataFrame | None,
    act: pd.DataFrame | None,
    amt: pd.DataFrame,
    dom: pd.DataFrame,
    breadth: bool,
) -> dict[str, pd.DataFrame]:
    sum5 = net.rolling(5, min_periods=5).sum()
    sum20 = net.rolling(20, min_periods=20).sum()
    std20 = net.rolling(20, min_periods=20).std()
    amt5, amt20 = _rolling_denoms(amt)
    out = {
        "net5_z": (sum5 / std20.where(std20 > 0)).where(dom),
        "net5_tt": (sum5 / amt5.where(amt5 > 0)).where(dom),
        "net20_tt": (sum20 / amt20.where(amt20 > 0)).where(dom),
    }
    if breadth and up is not None and dn is not None and act is not None:
        net_seats = (up - dn).rolling(5, min_periods=5).sum()
        act5 = act.rolling(5, min_periods=5).sum()
        out["breadth5"] = (net_seats / act5.where(act5 > 0)).where(dom)
    return out


def compute_all_signals(
    close: pd.DataFrame, amount: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """全部候选信号矩阵（键 ``{category}::{variant}``；6 类 × 4 变体 − 南向 breadth）。

    依赖事件层产物（events/flows/holdings 分区）与 hsgt_south 原始分区——
    构建脚本须先跑 ``events.build_events()``。
    """
    calendar = close.index
    symbols = close.columns
    flows = ds.load_flows()
    holdings = ds.load_holdings()
    if flows.empty:
        raise RuntimeError("flows 产物为空——先构建事件仓（build_events）")
    amt = amount.reindex(index=calendar, columns=symbols)

    panels: dict[str, pd.DataFrame] = {}
    for cat in CCASS_CATEGORIES:
        pres = _holdings_presence(holdings, calendar, symbols, cat)
        dom = _domain(pres)
        net = _flows_field(flows, "net_value", calendar, symbols, cat)
        net = net.mask(dom & net.isna(), 0.0)
        up = _flows_field(flows, "n_up", calendar, symbols, cat)
        up = up.mask(dom & up.isna(), 0.0)
        dn = _flows_field(flows, "n_dn", calendar, symbols, cat)
        dn = dn.mask(dom & dn.isna(), 0.0)
        act = _flows_field(flows, "n_active", calendar, symbols, cat)
        act = act.mask(dom & act.isna(), 0.0)
        amt_c = amt.mask(dom & amt.isna(), 0.0)
        variants = _variants_from_flow(net, up, dn, act, amt_c, dom, breadth=True)
        del net, up, dn, act, amt_c, pres
        for variant, mat in variants.items():
            panels[f"{cat}{SIGNAL_KEY_SEP}{variant}"] = mat

    # 南向：直读 hsgt_south 变体（完整表口径：捕获日缺席=0，缺口 ffill）
    qty = ds.south_qty_matrix(calendar, symbols)
    held = qty > 0
    dom_s = _domain(held)
    dq = qty.diff()
    net_s = dq * close.reindex(index=calendar, columns=symbols)
    qty5 = qty.shift(5)
    amt_s = amt.mask(dom_s & amt.isna(), 0.0)
    variants_s = _variants_from_flow(
        net_s, None, None, None, amt_s, dom_s, breadth=False
    )
    chg5 = (qty / qty5.where(qty5 > 0) - 1.0).where(dom_s)
    variants_s["chg5"] = chg5
    for variant, mat in variants_s.items():
        panels[f"southbound{SIGNAL_KEY_SEP}{variant}"] = mat
    assert set(SOUTH_SIGNALS) == set(variants_s), "南向变体表与常量不一致"
    for cat in CCASS_CATEGORIES:
        keys = {
            k.split(SIGNAL_KEY_SEP)[1]
            for k in panels
            if k.startswith(f"{cat}{SIGNAL_KEY_SEP}")
        }
        assert keys == set(CCASS_SIGNALS), f"{cat} 变体表与常量不一致：{keys}"
    return panels


# ---------------------------------------------------------------- 评估核心


def tail_ok(ic_mean: float, q51_bp: float, n_days: int) -> bool:
    return bool(
        np.sign(ic_mean) == np.sign(q51_bp)
        and abs(ic_mean) >= MIN_ABS_IC
        and abs(q51_bp) > MIN_Q51_BP
        and n_days >= MIN_DAYS_TAIL
    )


def seg_stats(
    mat: pd.DataFrame,
    ret: pd.DataFrame,
    exc: pd.DataFrame,
    min_names: int = MIN_NAMES_PER_DAY,
) -> dict | None:
    """单段秩 IC 流 + 五分位超额（全市场中位口径）+ 增持/减持命中。样本不足 None。"""
    ics: list[float] = []
    q_acc: dict[int, list[float]] = {i: [] for i in range(5)}
    top_obs = top_pos = bot_obs = bot_neg = 0
    for d in mat.index:
        if d not in ret.index:
            continue
        r = mat.loc[d]
        f = ret.loc[d]
        ok = r.notna() & f.notna()
        if ok.sum() < min_names:
            continue
        rr = r[ok]
        ff = f[ok]
        ic_val = float(np.corrcoef(rr.rank(), ff.rank())[0, 1])
        if np.isfinite(ic_val):
            ics.append(ic_val)
        e = exc.loc[d].reindex(rr.index)
        q = pd.qcut(rr.rank(method="first"), 5, labels=False)
        for i in range(5):
            q_acc[i].append(float(e[q == i].mean()))
        e4 = e[q == 4]
        e0 = e[q == 0]
        top_obs += int(e4.notna().sum())
        top_pos += int((e4 > 0).sum())
        bot_obs += int(e0.notna().sum())
        bot_neg += int((e0 < 0).sum())
    if len(ics) < MIN_SEG_DAYS:
        return None
    arr = np.asarray(ics)
    std = arr.std(ddof=1)
    q_means = [float(np.mean(q_acc[i])) * 1e4 for i in range(5)]
    q51 = q_means[4] - q_means[0]
    ic_mean = float(arr.mean())
    return {
        "n_days": int(len(arr)),
        "rank_ic": round(ic_mean, 4),
        "icir": round(ic_mean / std, 3) if std > 0 else None,
        "ic_pos_rate": round(float((arr > 0).mean()), 3),
        "q1_bp": round(q_means[0], 0),
        "q2_bp": round(q_means[1], 0),
        "q3_bp": round(q_means[2], 0),
        "q4_bp": round(q_means[3], 0),
        "q5_bp": round(q_means[4], 0),
        "q5_minus_q1_bp": round(q51, 0),
        "buy_hit": round(top_pos / top_obs, 3) if top_obs else None,
        "sell_hit": round(bot_neg / bot_obs, 3) if bot_obs else None,
        "asym_bp": round(q_means[4] + q_means[0], 0),
        "n_top_obs": top_obs,
        "n_bot_obs": bot_obs,
        "tail_ok": tail_ok(ic_mean, q51, len(arr)),
    }


def evaluate(
    mat: pd.DataFrame,
    fwd_map: dict[int, tuple[pd.DataFrame, pd.DataFrame]],
) -> dict[str, Any]:
    """单信号：h=5/10/20 全窗 + 分年（IC 对 raw fwd、分位超额对全市场中位 exc）。"""
    out: dict[str, Any] = {"results": {}, "by_year": {}}
    for h in HORIZONS:
        ret, exc = fwd_map[h]
        out["results"][str(h)] = seg_stats(mat, ret, exc)
        per_year: dict[str, dict | None] = {}
        for y in sorted({d.year for d in mat.index}):
            sub = mat.loc[mat.index.year == y]
            if sub.empty:
                continue
            per_year[str(y)] = seg_stats(sub, ret, exc)
        out["by_year"][str(h)] = per_year
    return out


def direction_from(result: dict | None) -> int | None:
    """入柱方向 = h10 实测 IC 符号（+1/−1）。"""
    if not result or result.get("rank_ic") is None:
        return None
    return 1 if result["rank_ic"] >= 0 else -1


def corr_matrix(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """两两截面秩相关（共同有值 ≥20 才计），取日中位。"""
    names = list(panels)
    out = pd.DataFrame(np.nan, index=names, columns=names)
    for a, b in combinations(names, 2):
        ma, mb = panels[a], panels[b]
        dates = ma.index.intersection(mb.index)
        rhos: list[float] = []
        for d in dates:
            ra, rb = ma.loc[d], mb.loc[d]
            ok = ra.notna() & rb.notna()
            if ok.sum() < 20:
                continue
            rho = float(np.corrcoef(ra[ok].rank(), rb[ok].rank())[0, 1])
            if np.isfinite(rho):
                rhos.append(rho)
        med = float(np.median(rhos)) if rhos else np.nan
        out.loc[a, b] = out.loc[b, a] = med
    return out


def collapse_admitted(
    passed: list[str],
    ic10: dict[str, float],
    corr: pd.DataFrame,
    threshold: float = CORR_COLLAPSE,
) -> tuple[list[str], list[dict], list[dict]]:
    """贪心相关塌缩：|ρ|>阈值成对信号保留 |IC| 强者。返回 (admitted, pairs, collapsed)。"""
    pairs: list[dict] = []
    for a, b in combinations(passed, 2):
        rho = corr.loc[a, b] if a in corr.index and b in corr.columns else np.nan
        if pd.notna(rho) and abs(rho) > threshold:
            pairs.append({"a": a, "b": b, "rho": round(float(rho), 3)})
    drop: set[str] = set()
    collapsed: list[dict] = []
    for pair in sorted(pairs, key=lambda p: -abs(p["rho"])):
        a, b = pair["a"], pair["b"]
        if a in drop or b in drop:
            continue
        loser = a if abs(ic10.get(a, 0.0)) < abs(ic10.get(b, 0.0)) else b
        winner = b if loser == a else a
        drop.add(loser)
        collapsed.append(
            {
                "signal": loser,
                "kept": winner,
                "rho": pair["rho"],
                "reason": f"|ρ|={abs(pair['rho'])}>{threshold} 与 {winner} 重复计权，保留 |IC| 强者",
            }
        )
    return [n for n in passed if n not in drop], pairs, collapsed


# ---------------------------------------------------------------- 席位统计


def _window_cutoff(calendar: pd.DatetimeIndex, window: int) -> str | None:
    if window <= 0 or len(calendar) <= window:
        return None
    return calendar[-window].strftime("%Y%m%d")


def seat_metrics(events: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """席位 × 三档窗口指标（细案 §3.4；收缩到中性 w=n/(n+20)）。

    事件集 = episode 首日 ∧ ¬嫌疑（first_seen/hkscc 在事件层已结构性排除）。
    命中/均值只用 fwd_exc_10 可测（未成熟 NaN 剔除）；n_buy/n_sell 为原始计数。
    """
    use = events[
        (events["episode_lead"] == 1)
        & (~events["transfer_suspect"])
        & (events["category"].isin(CCASS_CATEGORIES))
    ].copy()
    rows: list[dict] = []
    for pid, grp in use.groupby("participant_id", sort=False):
        grp = grp.sort_values("dt")
        exc10 = grp["fwd_exc_10"] * 1e4
        stability: bool | None = None
        half = len(grp) // 2
        if half >= 1 and len(grp) - half >= 1:
            a = exc10.iloc[:half].mean()
            b = exc10.iloc[half:].mean()
            if pd.notna(a) and pd.notna(b):
                stability = bool(np.sign(a) == np.sign(b))
        last_dt = str(grp["dt"].max())
        meta = grp.iloc[-1]
        for window in SEAT_WINDOWS:
            cutoff = _window_cutoff(calendar, window)
            sub = grp if cutoff is None else grp[grp["dt"] >= cutoff]
            n_buy = int((sub["direction"] > 0).sum())
            n_sell = int((sub["direction"] < 0).sum())
            e = sub["fwd_exc_10"] * 1e4
            ev = e.dropna()
            dirv = sub.loc[ev.index, "direction"]
            n_exc = int(len(ev))
            hit = avg = t_stat = None
            buy_hit = sell_hit = None
            if n_exc:
                avg = float(ev.mean())
                hit = float(((dirv > 0) & (ev > 0) | (dirv < 0) & (ev < 0)).mean())
                if n_exc >= 2:
                    sd = float(ev.std(ddof=1))
                    if sd > 0:
                        t_stat = float(avg / (sd / np.sqrt(n_exc)))
                b = ev[dirv > 0]
                s = ev[dirv < 0]
                buy_hit = float((b > 0).mean()) if len(b) else None
                sell_hit = float((s < 0).mean()) if len(s) else None
            w = n_exc / (n_exc + SEAT_SHRINK_K) if n_exc else 0.0
            shrunk_exc = (
                w * avg + (1 - w) * SEAT_NEUTRAL_EXC if avg is not None else None
            )
            shrunk_hit = (
                w * hit + (1 - w) * SEAT_NEUTRAL_HIT if hit is not None else None
            )
            rows.append(
                {
                    "participant_id": pid,
                    "participant_name": meta["participant_name"],
                    "category": meta["category"],
                    "kind": meta["kind"],
                    "window": "all" if window == 0 else str(window),
                    "n_events": n_buy + n_sell,
                    "n_buy": n_buy,
                    "n_sell": n_sell,
                    "n_exc10": n_exc,
                    "avg_exc_bp": round(avg, 1) if avg is not None else None,
                    "hit": round(hit, 3) if hit is not None else None,
                    "buy_hit": round(buy_hit, 3) if buy_hit is not None else None,
                    "sell_hit": round(sell_hit, 3) if sell_hit is not None else None,
                    "t": round(t_stat, 2) if t_stat is not None else None,
                    "shrunk_exc_bp": (
                        round(shrunk_exc, 1) if shrunk_exc is not None else None
                    ),
                    "shrunk_hit": (
                        round(shrunk_hit, 3) if shrunk_hit is not None else None
                    ),
                    "stability": stability,
                    "eligible": bool(n_buy + n_sell >= SEAT_MIN_EVENTS),
                    "last_event_date": last_dt,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 复合分


def compute_scores(
    day: pd.Timestamp,
    panels: dict[str, pd.DataFrame],
    spec: Sequence[tuple[str, int]],
) -> pd.DataFrame:
    """单日复合分（细案 §3.5）：admitted 信号截面分位（负向 100−pct）等权平均。

    spec=((信号键, 方向±1), …)。行集 = 各信号有效值并集；covered=有效信号数；
    covered<SCORE_MIN_COVERED → score=NaN（缺失绝不填 50）。列含 val_/pct_ 下钻。
    """
    spec = tuple(spec)
    cols = [
        "symbol",
        "score",
        "covered",
        "n_pillars",
        *[f"val_{n}" for n, _ in spec],
        *[f"pct_{n}" for n, _ in spec],
    ]
    d = pd.Timestamp(day)
    vals_by: dict[str, pd.Series | None] = {}
    sym_order: list[str] = []
    seen: set[str] = set()
    for key, _dir in spec:
        mat = panels.get(key)
        vals = (
            mat.loc[d] if mat is not None and not mat.empty and d in mat.index else None
        )
        vals_by[key] = vals
        if vals is not None:
            for s in vals.dropna().index:
                if s not in seen:
                    seen.add(s)
                    sym_order.append(s)
    if not sym_order:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(index=pd.Index(sym_order, name="symbol"))
    pct_cols: list[str] = []
    for key, direction in spec:
        vals = vals_by[key]
        if vals is None:
            vals = pd.Series(np.nan, index=out.index, dtype="float64")
        else:
            vals = vals.reindex(out.index)
        out[f"val_{key}"] = vals
        pct = pd.Series(np.nan, index=out.index, dtype="float64")
        valid = vals.dropna()
        if len(valid):
            raw = valid.rank(pct=True) * 100.0
            pct.loc[valid.index] = raw if direction >= 0 else 100.0 - raw
        out[f"pct_{key}"] = pct
        pct_cols.append(f"pct_{key}")
    out["covered"] = out[pct_cols].notna().sum(axis=1)
    out["n_pillars"] = len(spec)
    score = out[pct_cols].mean(axis=1, skipna=True)
    out["score"] = score.where(out["covered"] >= SCORE_MIN_COVERED).round(2)
    return out.reset_index()[cols]


# ---------------------------------------------------------------- 展示辅助


def daily_category_totals(flows: pd.DataFrame) -> pd.DataFrame:
    """日 × 类型净流合计（港元）与净变动票数（trends/overview 用）。"""
    if flows.empty:
        return pd.DataFrame(columns=["dt", "category", "net_value", "n_symbols"])
    agg = (
        flows.assign(active=(flows["net_qty"] != 0).astype("int64"))
        .groupby(["dt", "category"], as_index=False)
        .agg(net_value=("net_value", "sum"), n_symbols=("active", "sum"))
    )
    return agg
