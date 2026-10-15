#!/usr/bin/env python3
"""聪明钱候选信号 · 双过闸验证（细案 §4；结构照 hk_opportunity_ic_check.py）。

判据（与 HK 六柱一字不改）：
  tail_ok = sign(IC)==sign(q5−q1) ∧ |IC|≥0.01 ∧ |q5−q1|>1bp ∧ n_days≥60
收益口径（防前视）：fwd(t,h) = close[t+h]/close[t+1] − 1（T 盘后信号 → T+1 收盘入场），
clip(-0.5, +1.0) 防仙股；close 用 QuantDB daily_forward 前复权（禁 daily_backward）。

事件条件因子特则：LHB/大宗/调研类柱只在事件股上有值（非事件股 NaN 不参与），
日截面样本门槛降为 MIN_NAMES_EVENT=30（实测 LHB 日 50–90 只）；两融/全市场类
维持 MIN_NAMES_MARKET=100。特则与数值一并写入 evidence.json 披露。

入柱裁定 = 全窗 h=10 过闸 ∧ 相关塌缩（|ρ|>0.8 的成对信号保留 |IC| 强者，
防重复计权）后的幸存者。复合分（第二轮）：若 scores/dt=* 已回填，对复合分
矩阵跑同一套评估，结果写 evidence.composite。

产出：
- {product}/evidence.json（页面证据台唯一读源：IC/ICIR/pos_rate/q1..q5/分年/
  tail_ok/裁定/塌缩/复合分/口径说明）
- logs/smart_money_validate.csv（全量明细）

用法：
  python backend/scripts/smart_money_validate.py
  python backend/scripts/smart_money_validate.py --start 2024-01-01 --no-composite
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.shared.smart_money import datasets, signals  # noqa: E402
from backend.shared.utc_datetime import utc_now  # noqa: E402

log = logging.getLogger("smart_money_validate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

OUT_CSV = PROJECT_ROOT / "logs" / "smart_money_validate.csv"

MIN_NAMES_EVENT = 30
MIN_NAMES_MARKET = 100
MIN_ABS_IC = 0.01
MIN_DAYS_TAIL = 60
HORIZONS = (5, 10)
ADMIT_HORIZON = 10  # 入柱判据取 h=10（h=5 一同披露）
CORR_COLLAPSE = 0.8
DEFAULT_WARMUP = "2023-10-01"  # 柱暖机（z20 / 90D 窗需要历史）

FAMILY: dict[str, str] = {
    "inst_net_ratio_5d": "机构席位",
    "inst_net_buy_5d_z": "机构席位",
    "lhb_net_ratio_5d": "榜单整体",
    "margin_net_5d_z": "两融",
    "margin_bal_chg_20d": "两融",
    "dzjy_amount_ratio_5d": "大宗",
    "dzjy_premium_5d": "大宗",
    "survey_inst_90d": "调研",
}


def _min_names(pillar: str) -> int:
    return MIN_NAMES_MARKET if FAMILY.get(pillar) == "两融" else MIN_NAMES_EVENT


def _direction(r10: dict | None) -> int | None:
    """入柱方向 = h10 实测 IC 符号（+1/−1）——负向柱分位取 100−pct（HK 先例）。"""
    if not r10 or r10.get("rank_ic") is None:
        return None
    return 1 if r10["rank_ic"] >= 0 else -1


# ---------------------------------------------------------------- 评估核心


def _tail_ok(ic_mean: float, q51_bp: float, n_days: int) -> bool:
    return bool(
        np.sign(ic_mean) == np.sign(q51_bp)
        and abs(ic_mean) >= MIN_ABS_IC
        and abs(q51_bp) > 1
        and n_days >= MIN_DAYS_TAIL
    )


def _seg_stats(mat: pd.DataFrame, fwd: pd.DataFrame, min_names: int) -> dict | None:
    """单段（全窗或某年）秩 IC 流 + 五分位超额。样本不足返回 None。"""
    ics: list[float] = []
    q_acc: dict[int, list[float]] = {i: [] for i in range(5)}
    for d in mat.index:
        if d not in fwd.index:
            continue
        r = mat.loc[d]
        f = fwd.loc[d]
        ok = r.notna() & f.notna()
        if ok.sum() < min_names:
            continue
        rr = r[ok]
        ff = f[ok]
        ic_val = float(np.corrcoef(rr.rank(), ff.rank())[0, 1])
        if np.isfinite(ic_val):
            ics.append(ic_val)
        q = pd.qcut(rr.rank(method="first"), 5, labels=False)
        exc = ff - ff.median()
        for i in range(5):
            q_acc[i].append(float(exc[q == i].mean()))
    if len(ics) < 30:
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
        "tail_ok": _tail_ok(ic_mean, q51, len(arr)),
    }


def _evaluate(mat: pd.DataFrame, closes: pd.DataFrame, min_names: int) -> dict:
    """单信号：h=5/10 全窗 + 分年。"""
    out: dict = {"results": {}, "by_year": {}}
    for h in HORIZONS:
        fwd = (closes.shift(-h) / closes.shift(-1) - 1).clip(-0.5, 1.0)
        fwd = fwd.replace([np.inf, -np.inf], np.nan)
        out["results"][str(h)] = _seg_stats(mat, fwd, min_names)
        per_year: dict[str, dict | None] = {}
        for y in sorted({d.year for d in mat.index}):
            sub = mat.loc[mat.index.year == y]
            if sub.empty:
                continue
            per_year[str(y)] = _seg_stats(sub, fwd, min_names)
        out["by_year"][str(h)] = per_year
    return out


def _corr_matrix(pillars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """两两截面秩相关（共同有值成员 ≥20 才计），取日中位。"""
    names = list(pillars)
    out = pd.DataFrame(np.nan, index=names, columns=names)
    for a, b in combinations(names, 2):
        ma, mb = pillars[a], pillars[b]
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


# ---------------------------------------------------------------- 复合分（第二轮）


def _composite_eval(closes: pd.DataFrame, eval_start: pd.Timestamp) -> dict | None:
    dates = datasets.score_dates()
    if not dates:
        return None
    frames = []
    covered_vals = []
    for dt in dates:
        s = datasets.load_scores(dt)
        if s is None or s.empty:
            continue
        s = s[["symbol", "score", "covered"]].copy()
        s["trade_date"] = pd.Timestamp(dt)
        frames.append(s)
        covered_vals.append(s["covered"].astype(float))
    if not frames:
        return None
    alls = pd.concat(frames, ignore_index=True)
    mat = alls.pivot_table(index="trade_date", columns="symbol", values="score", aggfunc="last")
    mat = mat.loc[mat.index >= eval_start]
    if mat.empty:
        return None
    ev = _evaluate(mat, closes, MIN_NAMES_EVENT)
    ev["n_dates"] = int(len(mat.index))
    ev["avg_pool"] = round(float(mat.notna().sum(axis=1).mean()), 1)
    ev["avg_covered"] = (
        round(float(pd.concat(covered_vals).mean()), 2) if covered_vals else None
    )
    return ev


# ---------------------------------------------------------------- 主流程


def run(eval_start: pd.Timestamp, warmup_start: pd.Timestamp, with_composite: bool) -> dict:
    end_str = datasets.latest_kline_date() or pd.Timestamp.today().strftime("%Y%m%d")
    end = pd.Timestamp(end_str)
    log.info("加载行情 %s → %s", warmup_start.date(), end.date())
    closes = datasets.close_matrix(warmup_start, end)
    if closes.empty:
        raise SystemExit("daily_forward 无数据")
    log.info("价格矩阵 %d 天 × %d 标的", *closes.shape)

    calendar = closes.index
    raw = {name: datasets.load_raw(name) for name in datasets.RAW_DATASETS}
    margin = datasets.margin_daily(start=(warmup_start - pd.Timedelta(days=45)).strftime("%Y%m%d"), end=end_str)
    log.info("原始表行数 %s；两融 %d 行", {k: len(v) for k, v in raw.items()}, len(margin))

    pillars_all = signals.compute_pillars(calendar, raw, margin)
    log.info("柱矩阵：%s", {k: list(v.shape) for k, v in pillars_all.items()})

    # 只在评估窗口上做 IC（暖机段参与滚动窗口但不出分）
    eval_pillars = {k: v.loc[v.index >= eval_start] for k, v in pillars_all.items() if not v.empty}

    evidence: dict = {
        "validated_at": utc_now().isoformat(),
        "window": {
            "eval_start": eval_start.strftime("%Y-%m-%d"),
            "eval_end": end.strftime("%Y-%m-%d"),
            "warmup_start": warmup_start.strftime("%Y-%m-%d"),
        },
        "method": {
            "return": "fwd(t,h)=close[t+h]/close[t+1]-1, clip(-0.5,+1.0), qfq=daily_forward",
            "gate": "sign(IC)==sign(q5-q1) ∧ |IC|≥0.01 ∧ |q5-q1|>1bp ∧ n_days≥60（入柱以 h=10 为准，h=5 披露）",
            "min_names_per_day": {"event_conditioned": MIN_NAMES_EVENT, "market_wide": MIN_NAMES_MARKET},
            "min_names_note": "LHB/大宗/调研类柱只在事件股上有值，日截面 30–400 只，门槛按性质分档并在此披露",
            "collapse": f"|截面秩相关|>{CORR_COLLAPSE} 的成对信号留 |IC| 强者",
            "direction": "入柱方向 = h10 IC 符号（+1/−1）；负向柱分位取 100−pct（方向对齐，HK 六柱先例），使柱分高 = 历史有利方向",
            "indicators_only": "柱候选 8 个：机构席位 2 / 榜单整体 1 / 两融 2 / 大宗 2 / 调研 1",
        },
        "signals": [],
        "correlation": {"pairs": [], "collapsed": []},
        "composite": None,
        "evidence_only": [
            {
                "name": "l2_big_order",
                "conclusion": "个股层裸流量信号已否决（净额比 IC≈+0.006，不达门槛），v1 不进柱不进榜",
                "use": "证据台引述",
            },
            {
                "name": "north_quarterly",
                "conclusion": "北向 2024-08-19 起仅季度披露（本库 7 个季度），无日频统计功效",
                "use": "C 级背书：个股档案列示季度持仓变化，不进复合分",
            },
        ],
    }

    rows_csv: list[dict] = []
    passed: list[str] = []
    for name in signals.PILLAR_CANDIDATES:
        mat = eval_pillars.get(name, pd.DataFrame())
        mn = _min_names(name)
        if mat.empty or mat.notna().sum().sum() == 0:
            evidence["signals"].append(
                {
                    "name": name,
                    "family": FAMILY.get(name),
                    "min_names": mn,
                    "admitted": False,
                    "direction": None,
                    "decision": "数据缺失（柱矩阵全空）",
                    "results": {},
                    "by_year": {},
                }
            )
            continue
        ev = _evaluate(mat, closes, mn)
        r10 = ev["results"].get(str(ADMIT_HORIZON))
        ok10 = bool(r10 and r10["tail_ok"])
        if ok10:
            passed.append(name)
        sig_rec = {
            "name": name,
            "family": FAMILY.get(name),
            "min_names": mn,
            "coverage_per_day_avg": round(float(mat.notna().sum(axis=1).mean()), 1),
            "admitted": ok10,  # 塌缩后可能改判，末尾统一覆盖
            "direction": _direction(r10),  # 入柱方向（build 按此对齐分位）
            "decision": "过闸（h=10）" if ok10 else "未过闸",
            "results": ev["results"],
            "by_year": ev["by_year"],
        }
        evidence["signals"].append(sig_rec)
        for h in HORIZONS:
            r = ev["results"].get(str(h))
            if r:
                rows_csv.append({"signal": name, "segment": "full", "horizon": h, **r})
            for y, ry in ev["by_year"].get(str(h), {}).items():
                if ry:
                    rows_csv.append({"signal": name, "segment": y, "horizon": h, **ry})
        r5 = ev["results"].get("5")
        msg = (
            f"{name:<24s} h10 IC={r10['rank_ic']:+.4f} q5-q1={r10['q5_minus_q1_bp']:+.0f}bp "
            f"n={r10['n_days']} {'✓' if ok10 else '✗'}"
            if r10
            else f"{name:<24s} h10 样本不足"
        )
        if r5 is not None:
            msg += f" | h5 IC={r5['rank_ic']:+.4f} q5-q1={r5['q5_minus_q1_bp']:+.0f}bp"
        log.info(msg)

    # 相关塌缩（仅对过闸者）
    corr = _corr_matrix({k: v for k, v in eval_pillars.items() if k in passed})
    if not corr.empty:
        for a, b in combinations(list(corr.index), 2):
            rho = corr.loc[a, b]
            if pd.notna(rho) and abs(rho) > CORR_COLLAPSE:
                evidence["correlation"]["pairs"].append({"a": a, "b": b, "rho": round(float(rho), 3)})
        # 贪心：每对保留 |IC_10| 强者
        ic10 = {
            s["name"]: abs(s["results"].get("10", {}).get("rank_ic", 0.0) or 0.0)
            for s in evidence["signals"]
            if s["name"] in passed
        }
        drop: set[str] = set()
        for pair in sorted(
            evidence["correlation"]["pairs"], key=lambda p: -abs(p["rho"])
        ):
            a, b = pair["a"], pair["b"]
            if a in drop or b in drop:
                continue
            loser = a if ic10.get(a, 0) < ic10.get(b, 0) else b
            drop.add(loser)
            evidence["correlation"]["collapsed"].append(
                {
                    "signal": loser,
                    "kept": b if loser == a else a,
                    "rho": pair["rho"],
                    "reason": f"|ρ|={abs(pair['rho'])}>{CORR_COLLAPSE} 与 {b if loser == a else a} 重复计权，保留 |IC| 强者",
                }
            )
        admitted = [s for s in passed if s not in drop]
    else:
        admitted = list(passed)

    admitted_set = set(admitted)
    rejected = []
    for s in evidence["signals"]:
        if s["name"] in admitted_set:
            s["admitted"] = True
            s["decision"] = "入柱"
            continue
        s["admitted"] = False
        r10 = s["results"].get("10") if s.get("results") else None
        if s["name"] in passed:
            why = next(
                (c["reason"] for c in evidence["correlation"]["collapsed"] if c["signal"] == s["name"]),
                "相关塌缩",
            )
            s["decision"] = f"否决（{why}）"
        elif r10:
            why = (
                "IC 与尾部反向"
                if np.sign(r10["rank_ic"]) != np.sign(r10["q5_minus_q1_bp"])
                else ("|IC| 过弱" if abs(r10["rank_ic"]) < MIN_ABS_IC else "尾部差过小或样本不足")
            )
            s["decision"] = f"否决（{why}）"
        rejected.append(
            {
                "name": s["name"],
                "family": s.get("family"),
                "reason": s["decision"],
                "rank_ic_10": r10["rank_ic"] if r10 else None,
                "q5_minus_q1_bp_10": r10["q5_minus_q1_bp"] if r10 else None,
                "n_days_10": r10["n_days"] if r10 else None,
            }
        )

    evidence["admitted"] = admitted
    evidence["rejected"] = rejected
    log.info("入柱裁定：%s（h=10 过闸 %d，塌缩掉 %d）", admitted, len(passed), len(passed) - len(admitted))

    if with_composite:
        evidence["composite"] = _composite_eval(closes, eval_start)
        if evidence["composite"]:
            cr = evidence["composite"]["results"].get("10")
            log.info("复合分 h10：%s", cr)
        else:
            log.info("复合分：无 scores 快照（先跑 build_smart_money.py 回填后复跑本脚本）")

    # 落盘
    product = datasets.product_dir()
    product.mkdir(parents=True, exist_ok=True)
    tmp = product / "evidence.json.tmp"
    tmp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(product / "evidence.json")
    if rows_csv:
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows_csv).to_csv(OUT_CSV, index=False)
    log.info("evidence.json → %s", product / "evidence.json")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="聪明钱候选信号双过闸验证")
    parser.add_argument("--start", default="2024-01-01", help="评估起始（默认 2024-01-01）")
    parser.add_argument("--warmup", default=DEFAULT_WARMUP, help="柱暖机起始（默认 2023-10-01）")
    parser.add_argument("--no-composite", action="store_true", help="跳过复合分第二轮")
    args = parser.parse_args()
    ev = run(
        pd.Timestamp(args.start),
        pd.Timestamp(args.warmup),
        with_composite=not args.no_composite,
    )
    print("\n===== 聪明钱入柱裁定 =====")
    for a in ev["admitted"]:
        print(f"  ✓ {a}")
    for r in ev["rejected"]:
        print(f"  ✗ {r['name']:<24s} {r['reason']} (IC10={r['rank_ic_10']}, q5-q1={r['q5_minus_q1_bp_10']}bp)")
    dirs = {s["name"]: s.get("direction") for s in ev["signals"]}
    pairs = tuple((n, dirs.get(n)) for n in ev["admitted"])
    print(f"\nADMITTED_PILLARS = {pairs!r}  # 回填 backend/shared/smart_money/signals.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
