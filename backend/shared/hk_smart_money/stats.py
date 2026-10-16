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
    SEAT_PERSIST_MIN_N1,
    SEAT_PERSIST_MIN_N2,
    SEAT_PERSIST_MIN_SEATS,
    SEAT_PERSIST_QUARTILE_MIN_N,
    SEAT_SHRINK_K,
    SEAT_SKILL_Z,
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


def _skill_bp(exc_bp: pd.Series, direction: pd.Series) -> pd.Series:
    """方向技能（bp）：买入事件取 +超额、卖出事件取 −超额（方向对不对）。

    v2「机构优势」的正确度量——2026-10-16 实测其前后半秩相关（≈0.12）强于
    裸超额（≈0.04）与命中率（≈0.04），细则见细案 §1。
    """
    return exc_bp.where(direction > 0, -exc_bp)


def _seat_use(events: pd.DataFrame) -> pd.DataFrame:
    """席位统计事件集：episode 首日 ∧ ¬嫌疑 ∧ CCASS 5 类（口径唯一出处）。"""
    return events[
        (events["episode_lead"] == 1)
        & (~events["transfer_suspect"])
        & (events["category"].isin(CCASS_CATEGORIES))
    ]


def _txt(v: Any) -> str | None:
    """pandas 标量 → JSON 安全 str|None（NaN/pd.NA 一律 None，绝不落「nan」）。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    s = str(v).strip()
    return s if s else None


def seat_metrics(events: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """席位 × 三档窗口指标（细案 §3.4；收缩到中性 w=n/(n+20)；v2 扩技能列）。

    事件集 = episode 首日 ∧ ¬嫌疑（first_seen/hkscc 在事件层已结构性排除）。
    命中/均值只用 fwd_exc_10 可测（未成熟 NaN 剔除）；n_buy/n_sell 为原始计数。
    v2 新增（全部按窗口成套，bp 存库）：skill_bp/skill_t/lb_skill_bp（90% 下界，
    仅 n_exc10 ≥ SEAT_MIN_EVENTS 时给出——更小样本正态近似失效，绝不假标）/
    vw_skill_bp（|Δvalue| 加权）/exc5_bp/exc20_bp（裸超额对照）/buy_exc_bp/
    sell_exc_bp（各侧原始）/n_symbols/avg_value_m/first_event_date。
    输入须含列：dt/symbol/participant_id/participant_name/category/kind/direction/
    delta_value/episode_lead/transfer_suspect/fwd_exc_5/10/20。
    """
    use = _seat_use(events).copy()
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
        first_dt = str(grp["dt"].min())
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
            skill_bp = skill_t = lb_skill = vw_skill = None
            buy_exc = sell_exc = None
            if n_exc:
                avg = float(ev.mean())
                hit = float(((dirv > 0) & (ev > 0) | (dirv < 0) & (ev < 0)).mean())
                skill = _skill_bp(ev, dirv)
                skill_bp = float(skill.mean())
                if n_exc >= 2:
                    sd = float(ev.std(ddof=1))
                    if sd > 0:
                        t_stat = float(avg / (sd / np.sqrt(n_exc)))
                    sd_s = float(skill.std(ddof=1))
                    se_s = sd_s / np.sqrt(n_exc)
                    # n<SEAT_MIN_EVENTS 不给下界：正态近似失效（n=2 零方差时
                    # se=0 会伪造 lb=skill 浮顶）——「—」比假标 90% 诚实
                    if n_exc >= SEAT_MIN_EVENTS:
                        lb_skill = skill_bp - SEAT_SKILL_Z * se_s
                    if sd_s > 0:
                        skill_t = skill_bp / se_s
                b = ev[dirv > 0]
                s = ev[dirv < 0]
                buy_hit = float((b > 0).mean()) if len(b) else None
                sell_hit = float((s < 0).mean()) if len(s) else None
                buy_exc = float(b.mean()) if len(b) else None
                sell_exc = float(s.mean()) if len(s) else None
                lv = sub.loc[ev.index, "delta_value"].abs()
                wsum = float(lv.sum())
                if wsum > 0:
                    vw_skill = float((skill * lv).sum() / wsum)
            e5 = (sub["fwd_exc_5"] * 1e4).dropna()
            e20 = (sub["fwd_exc_20"] * 1e4).dropna()
            exc5_bp = float(e5.mean()) if len(e5) else None
            exc20_bp = float(e20.mean()) if len(e20) else None
            avg_value_m = (
                float(sub["delta_value"].abs().mean() / 1e6) if len(sub) else None
            )
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
                    "first_event_date": first_dt,
                    "skill_bp": round(skill_bp, 1) if skill_bp is not None else None,
                    "skill_t": round(skill_t, 2) if skill_t is not None else None,
                    "lb_skill_bp": round(lb_skill, 1) if lb_skill is not None else None,
                    "vw_skill_bp": round(vw_skill, 1) if vw_skill is not None else None,
                    "exc5_bp": round(exc5_bp, 1) if exc5_bp is not None else None,
                    "exc20_bp": round(exc20_bp, 1) if exc20_bp is not None else None,
                    "buy_exc_bp": round(buy_exc, 1) if buy_exc is not None else None,
                    "sell_exc_bp": round(sell_exc, 1) if sell_exc is not None else None,
                    "n_symbols": int(sub["symbol"].nunique()),
                    "avg_value_m": (
                        round(avg_value_m, 2) if avg_value_m is not None else None
                    ),
                }
            )
    return pd.DataFrame(rows)


def seat_monthly(events: pd.DataFrame) -> pd.DataFrame:
    """席位 × 自然月 战绩序列（档案抽屉数据源；口径与 seat_metrics 同源）。

    只用 fwd_exc_10 可测样本；month = dt 前 6 位的 YYYY-MM。列：
    participant_id, month, n, skill_bp, vw_skill_bp, hit。
    """
    cols = ["participant_id", "month", "n", "skill_bp", "vw_skill_bp", "hit"]
    use = _seat_use(events)
    e = use["fwd_exc_10"] * 1e4
    ok = e.notna()
    if not ok.any():
        return pd.DataFrame(columns=cols)
    m = use.loc[ok].assign(
        month=(
            use.loc[ok, "dt"]
            .astype(str)
            .str.slice(0, 6)
            .map(lambda s: f"{s[:4]}-{s[4:]}")
        ),
        skill=_skill_bp(e[ok], use.loc[ok, "direction"]),
        w=use.loc[ok, "delta_value"].abs(),
    )
    m["skill_w"] = m["skill"] * m["w"]
    agg = m.groupby(["participant_id", "month"], sort=True).agg(
        n=("skill", "size"),
        skill_mean=("skill", "mean"),
        skill_w=("skill_w", "sum"),
        w=("w", "sum"),
        hit=("skill", lambda s: float((s > 0).mean())),
    )
    out = agg.reset_index()
    wsum = out["w"].astype("float64")
    out["skill_bp"] = out["skill_mean"].round(1)
    out["vw_skill_bp"] = [
        round(float(sw / wv), 1) if wv > 0 else None
        for sw, wv in zip(out["skill_w"], wsum, strict=True)
    ]
    out["hit"] = out["hit"].round(3)
    out["participant_id"] = out["participant_id"].astype(str)
    out["month"] = out["month"].astype(str)
    out["n"] = out["n"].astype(int)
    return out[cols]


def seat_symbols(events: pd.DataFrame) -> pd.DataFrame:
    """席位 × 标的 聚合（档案 top_symbols 数据源；读侧按 n 降序取前 N）。

    列：participant_id, symbol, stock_name, n, skill_bp, vw_skill_bp, last_dt。
    """
    cols = [
        "participant_id",
        "symbol",
        "stock_name",
        "n",
        "skill_bp",
        "vw_skill_bp",
        "last_dt",
    ]
    use = _seat_use(events)
    e = use["fwd_exc_10"] * 1e4
    ok = e.notna()
    if not ok.any():
        return pd.DataFrame(columns=cols)
    m = use.loc[ok].assign(
        skill=_skill_bp(e[ok], use.loc[ok, "direction"]),
        w=use.loc[ok, "delta_value"].abs(),
    )
    m["skill_w"] = m["skill"] * m["w"]
    agg = m.groupby(["participant_id", "symbol"], sort=False).agg(
        n=("skill", "size"),
        skill_mean=("skill", "mean"),
        skill_w=("skill_w", "sum"),
        w=("w", "sum"),
        last_dt=("dt", "max"),
    )
    out = agg.reset_index()
    names = (
        use.dropna(subset=["stock_name"])
        .groupby(["participant_id", "symbol"])["stock_name"]
        .last()
        .rename("stock_name")
    )
    out = out.merge(names, on=["participant_id", "symbol"], how="left")
    wsum = out["w"].astype("float64")
    out["skill_bp"] = out["skill_mean"].round(1)
    out["vw_skill_bp"] = [
        round(float(sw / wv), 1) if wv > 0 else None
        for sw, wv in zip(out["skill_w"], wsum, strict=True)
    ]
    out["participant_id"] = out["participant_id"].astype(str)
    out["symbol"] = out["symbol"].astype(str)
    out["stock_name"] = out["stock_name"].map(_txt)
    out["last_dt"] = out["last_dt"].astype(str)
    out["n"] = out["n"].astype(int)
    return out[cols]


def category_skill(events: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """类别层方向技能（latest.categories 的 v2 字段）：skill_bp / vw_skill_bp /
    n_lead_events。南向不在 CCASS 事件仓 → 无此键（读侧「—」）。"""
    use = _seat_use(events)
    e = use["fwd_exc_10"] * 1e4
    ok = e.notna()
    out: dict[str, dict[str, Any]] = {}
    if not ok.any():
        return out
    m = use.loc[ok].assign(
        skill=_skill_bp(e[ok], use.loc[ok, "direction"]),
        w=use.loc[ok, "delta_value"].abs(),
    )
    m["skill_w"] = m["skill"] * m["w"]
    agg = m.groupby("category", sort=True).agg(
        skill_mean=("skill", "mean"),
        skill_w=("skill_w", "sum"),
        w=("w", "sum"),
        n=("skill", "size"),
    )
    for cat, r in agg.iterrows():
        wsum = float(r["w"])
        out[str(cat)] = {
            "skill_bp": round(float(r["skill_mean"]), 1),
            "vw_skill_bp": (round(float(r["skill_w"]) / wsum, 1) if wsum > 0 else None),
            "n_lead_events": int(r["n"]),
        }
    return out


def _half_skill(df: pd.DataFrame, exc_col: str) -> pd.DataFrame:
    """半年片按席方向技能聚合（size/mean/std）；×1e4 后无 NaN 泄漏。"""
    e = df[exc_col] * 1e4
    ok = e.notna()
    if not ok.any():
        return pd.DataFrame(columns=["size", "mean", "std"])
    m = df.loc[ok].assign(skill=_skill_bp(e[ok], df.loc[ok, "direction"]))
    return m.groupby("participant_id")["skill"].agg(["size", "mean", "std"])


def _half_win(df: pd.DataFrame) -> pd.DataFrame:
    """半年片按席方向命中率聚合（size/mean；win = 技能>0）。"""
    e = df["fwd_exc_10"] * 1e4
    ok = e.notna()
    if not ok.any():
        return pd.DataFrame(columns=["size", "mean"])
    m = df.loc[ok].assign(
        win=(_skill_bp(e[ok], df.loc[ok, "direction"]) > 0).astype(float)
    )
    return m.groupby("participant_id")["win"].agg(["size", "mean"])


def _spearman(a: pd.DataFrame, b: pd.DataFrame, min1: int, min2: int) -> float | None:
    """两半片均值秩相关（同门槛；席数不足或全同值 → None）。"""
    if a.empty or b.empty:
        return None
    j = a.join(b, lsuffix="_1", rsuffix="_2", how="inner")
    j = j[(j["size_1"] >= min1) & (j["size_2"] >= min2)]
    if len(j) < SEAT_PERSIST_MIN_SEATS:
        return None
    rho = j["mean_1"].corr(j["mean_2"], method="spearman")
    return None if pd.isna(rho) else round(float(rho), 3)


def _cat_vw_skill(df: pd.DataFrame) -> tuple[float | None, int]:
    """（vw 方向技能 bp, 可测事件数）——类别前后半对比用。"""
    e = df["fwd_exc_10"] * 1e4
    ok = e.notna()
    if not ok.any():
        return None, 0
    n = int(ok.sum())
    skill = _skill_bp(e[ok], df.loc[ok, "direction"])
    w = df.loc[ok, "delta_value"].abs()
    wsum = float(w.sum())
    if wsum <= 0:
        return None, n
    return round(float((skill * w).sum() / wsum), 1), n


def seat_persistence(events: pd.DataFrame) -> dict[str, Any]:
    """持续性审计（细案 §1）：散点 + 秩相关 + 四分位 OOS + 类别前后半。

    事实背板（2026-10-16 实测）：席位层方向技能前/后半 Spearman≈0.12（弱），
    四分位 OOS 无梯度——本审计是界面「稳健性」面数据源：榜单为描述性统计，
    不是可跟随信号，如实披露。数字全部构建期实算，绝不写死。
    """
    out: dict[str, Any] = {
        "split_date": None,
        "scatter": [],
        "spearman": {"h10": None, "h20": None, "hit": None},
        "quartiles": [],
        "category_halves": [],
        "method": {
            "skill": "方向技能 = 买 +超额 / 卖 −超额（fwd_exc_10 对当日全市场中位，bp）",
            "scatter": (
                f"单席点：前/后半可测事件 ≥{SEAT_PERSIST_MIN_N1}/{SEAT_PERSIST_MIN_N2}；"
                "x=前半均值、y=后半均值"
            ),
            "quartile": (
                f"前半年按技能 90% 下界四分位（各席前半可测 ≥{SEAT_PERSIST_QUARTILE_MIN_N}）"
                "→ 后半年跟随（成员全体事件技能均值）"
            ),
            "note": "席位层弱持续是实测结论（非故障）：榜单为描述性统计，不是可跟随信号",
        },
    }
    use = _seat_use(events).copy()
    if use.empty:
        return out
    use["dt_s"] = use["dt"].astype(str)
    dates = np.sort(use["dt_s"].unique())
    if len(dates) < 4:
        return out
    mid = str(dates[len(dates) // 2])
    out["split_date"] = (
        f"{mid[:4]}-{mid[4:6]}-{mid[6:]}" if len(mid) == 8 and mid.isdigit() else mid
    )
    head, tail = use[use["dt_s"] < mid], use[use["dt_s"] >= mid]

    a10, b10 = _half_skill(head, "fwd_exc_10"), _half_skill(tail, "fwd_exc_10")
    if not a10.empty and not b10.empty:
        meta = (
            use.sort_values("dt_s")
            .groupby("participant_id")[["participant_name", "category"]]
            .last()
        )
        j = a10.join(b10, lsuffix="_1", rsuffix="_2", how="inner")
        j = j[
            (j["size_1"] >= SEAT_PERSIST_MIN_N1) & (j["size_2"] >= SEAT_PERSIST_MIN_N2)
        ]
        for pid, r in j.sort_values("size_1", ascending=False).iterrows():
            nm = meta.loc[pid] if pid in meta.index else None
            out["scatter"].append(
                {
                    "participant_id": str(pid),
                    "name": _txt(nm["participant_name"]) if nm is not None else None,
                    "category": _txt(nm["category"]) if nm is not None else None,
                    "n1": int(r["size_1"]),
                    "n2": int(r["size_2"]),
                    "skill1_bp": round(float(r["mean_1"]), 1),
                    "skill2_bp": round(float(r["mean_2"]), 1),
                }
            )
    out["spearman"]["h10"] = _spearman(
        a10, b10, SEAT_PERSIST_MIN_N1, SEAT_PERSIST_MIN_N2
    )
    a20, b20 = _half_skill(head, "fwd_exc_20"), _half_skill(tail, "fwd_exc_20")
    out["spearman"]["h20"] = _spearman(
        a20, b20, SEAT_PERSIST_MIN_N1, SEAT_PERSIST_MIN_N2
    )
    out["spearman"]["hit"] = _spearman(
        _half_win(head), _half_win(tail), SEAT_PERSIST_MIN_N1, SEAT_PERSIST_MIN_N2
    )

    q = a10[a10["size"] >= SEAT_PERSIST_QUARTILE_MIN_N].copy()
    if len(q) >= 4:
        q["lb"] = q["mean"] - SEAT_SKILL_Z * (q["std"].fillna(0.0) / np.sqrt(q["size"]))
        q = q.sort_values("lb", ascending=False)
        tail_skill = tail.assign(
            skill=_skill_bp(tail["fwd_exc_10"] * 1e4, tail["direction"])
        ).dropna(subset=["skill"])
        for i, part in enumerate(np.array_split(q, 4), start=1):
            fw = tail_skill[tail_skill["participant_id"].isin(part.index)]
            out["quartiles"].append(
                {
                    "q": i,
                    "n_seats": int(len(part)),
                    "n_events": int(len(fw)),
                    "forward_skill_bp": (
                        round(float(fw["skill"].mean()), 1) if len(fw) else None
                    ),
                    "forward_hit": (
                        round(float((fw["skill"] > 0).mean()), 3) if len(fw) else None
                    ),
                }
            )

    for cat in CCASS_CATEGORIES:
        fv, fvn = _cat_vw_skill(head[head["category"] == cat])
        sv, svn = _cat_vw_skill(tail[tail["category"] == cat])
        out["category_halves"].append(
            {
                "category": cat,
                "n": fvn + svn,
                "first_half_vw_skill_bp": fv,
                "second_half_vw_skill_bp": sv,
            }
        )
    return out


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
