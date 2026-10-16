#!/usr/bin/env python3
"""984 → 300 训练特征选型：近期截面 IC + 语义分类均衡 + 冗余终检。

输入：
  <quantdb>/factor_research/screening/factor_selection.json   —— 筛选 kept（984 条，
      各带 library / ic_mean / icir / turnover，2026-10-15 全量筛选产物）
  <quantdb>/factor_research/screening/cross_corr.npz          —— 候选列池化联合相关
      （names 2520 / matrix 2520²，冗余终检用）
  <quantdb>/6_ml_datasets/{ds}/dt=*/data.parquet              —— 各库逐日分区

选型口径（2026-10-16 细案 docs/因子选型与T5跨库训练_细案_20261016.md）：
  1. 可训池 = kept − features_daily（读层硬拦，含未来收益列）− factor_research
     （不在 6_ml_datasets 训练目录体系）；两库合计排除 27 条，逐条记录。
  2. 「近期预测性」= 与训练**逐字同口径**的近窗截面 IC 时序：
       标签 = 锚库 l1_factors close，先剔「全市场 volume==0」的节假日填充行，
       再按标的 shift：lag=1、horizon=5 → label(t)=close(t+6)/close(t+1)−1；
       主窗默认 2025-09-01..2026-09-10（t+6 完整），对照窗 = 主窗末 −6 个月；
       逐日 Spearman（rank 后 Pearson，按特征与标签成对完整样本，最少 300 只）。
  3. 门槛：n_dates ≥ --min-dates 且 |ic_12m| ≥ --min-abs-ic（只剔近期失效者）。
  4. 配额：可训数 ≤15 的类别全取；其余按 √n 分 300 剩余席位，类内按 |icir_12m| 降序。
  5. 冗余终检：池化 |ρ| ≥ --corr-threshold 贪婪剔除 + 同类顺位替补（记录明细）。

输出（<quantdb>/factor_research/screening/）：
  recent_ic.parquet           逐日 IC 时序（name × trade_date × ic × n_valid，可审计）
  training_selection_300.json 300 明细 + 配额表 + 排除清单 + features_payload + train_plan
  训练选型报告_YYYYMMDD.md     人读报告（漏斗/配额/各类 Top/排除/冗余明细）

用法：
  python3 backend/scripts/select_training_features.py                 # 全量选型
  python3 backend/scripts/select_training_features.py --max-dates 12  # 冒烟（只算最近 12 期）
  python3 backend/scripts/select_training_features.py --n-target 200 --corr-threshold 0.8
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.engine.factor_research.taxonomy import (  # noqa: E402
    CATEGORY_BY_ID,
    classify_code,
)
from backend.shared.quantdb_paths import resolve_quantdb_dir  # noqa: E402

logger = logging.getLogger("select_training_features")

ANCHOR_LIB = "l1_factors"
# 排除库（2026-10-16 侦查定论，两条均经实测）：features_daily 被读层
# EXCLUDED_FROM_TRAINING 硬拦且含 return_Nd 未来收益列；factor_research 的
# OURS 码不被 6_ml_datasets 体系物化（目录不存在），无训练目录键。
EXCLUDED_LIBS: dict[str, str] = {
    "features_daily": "读层硬拦库（EXCLUDED_FROM_TRAINING，含未来收益列）",
    "factor_research": "不在 6_ml_datasets 训练目录体系（无分区、无目录键）",
}
SMALL_CATEGORY_MAX = 15  # 可训数 ≤ 此值的类别全取
MIN_SYMBOLS_PER_DATE = 300  # 单期成对完整样本下限
CST = timezone(timedelta(hours=8))

_WORKER: dict = {}


def _dataset_dir(lib: str) -> Path:
    return resolve_quantdb_dir() / "6_ml_datasets" / lib


# ── 标签构建（镜像 docker/training/data/loading.py 直读路径） ──────────────


def _read_anchor_panel(start_dt: str, end_dt: str | None) -> pd.DataFrame:
    """锚库 close/volume 面板（symbol 前缀式；dt 为 YYYYMMDD 字符串）。

    end_dt=None 表示不设上限——窗尾标签需要 t+6 的 close，锚库必须读过头。
    """
    adir = _dataset_dir(ANCHOR_LIB)
    dts = sorted(
        p.name[3:]
        for p in adir.glob("dt=*")
        if p.name[3:] >= start_dt and (end_dt is None or p.name[3:] <= end_dt)
    )
    if not dts:
        raise RuntimeError(f"锚库 {ANCHOR_LIB} 在 {start_dt}..{end_dt} 无分区")
    frames = []
    for dt in dts:
        f = adir / f"dt={dt}" / "data.parquet"
        part = pd.read_parquet(f, columns=["symbol", "close", "volume"])
        part["date"] = dt
        frames.append(part)
    panel = pd.concat(frames, ignore_index=True)
    # 节假日填充行剔除：当日全市场 max(volume)==0（QuantDB parquet 含 ~6.6% 假交易日）
    max_vol = panel.groupby("date")["volume"].max()
    bad = set(max_vol[max_vol.fillna(0.0) <= 0].index)
    if bad:
        logger.info("剔除 %d 个节假日填充日（全市场 volume==0）", len(bad))
        panel = panel[~panel["date"].isin(bad)]
    return panel


def build_labels(
    start_dt: str, end_dt: str | None, horizon: int, lag: int
) -> pd.DataFrame:
    """label(t)=close(t+lag+horizon)/close(t+lag)−1，按标的在（剔假日后）序列上 shift。"""
    panel = _read_anchor_panel(start_dt, end_dt)
    logger.info(
        "锚库面板 %d 行，dt %s..%s",
        len(panel),
        panel["date"].min(),
        panel["date"].max(),
    )
    panel = panel.sort_values(["symbol", "date"], kind="stable")
    g = panel.groupby("symbol", sort=False)["close"]
    exec_close = g.shift(-lag)
    future_close = g.shift(-(lag + horizon))
    panel["__label"] = future_close / exec_close - 1.0
    return panel[["date", "symbol", "__label"]]


# ── 逐日 IC（多进程） ────────────────────────────────────────────────────


def _read_lib_partition(root: Path, lib: str, dt: str, cols: list[str]):
    """单库单期分区读取；列代际断层降级为「可用列交集 + 缺列补 NaN」。"""
    f = root / "6_ml_datasets" / lib / f"dt={dt}" / "data.parquet"
    if not f.exists():
        return None
    try:
        part = pd.read_parquet(f, columns=["symbol", *cols])
    except Exception:  # noqa: BLE001
        try:
            import pyarrow.parquet as pq

            have = set(pq.ParquetFile(f).schema_arrow.names)
            use = [c for c in cols if c in have]
            part = pd.read_parquet(f, columns=["symbol", *use])
            part = part.reindex(columns=["symbol", *cols])
        except Exception:  # noqa: BLE001
            return None
    part = part.set_index("symbol")
    return part[~part.index.duplicated(keep="first")]


def _init_worker(root: str, blocks: list, feats_all: list, min_syms: int) -> None:
    _WORKER.update(root=Path(root), blocks=blocks, min_syms=min_syms)
    _WORKER["feat_idx"] = {f: i for i, f in enumerate(feats_all)}


def _ic_one_date(task) -> tuple[str, np.ndarray, np.ndarray]:
    dt, lab = task  # lab: DataFrame[symbol, __label]
    idx = _WORKER["feat_idx"]
    ic_vec = np.full(len(idx), np.nan)
    n_vec = np.zeros(len(idx), dtype=np.int32)
    lab = lab[~lab["symbol"].duplicated(keep="first")]
    df = lab.set_index("symbol")
    joined = False
    for lib, cols in _WORKER["blocks"]:
        part = _read_lib_partition(_WORKER["root"], lib, dt, cols)
        if part is None:
            continue
        df = df.join(part, how="left")
        joined = True
    if not joined:
        return dt, ic_vec, n_vec
    df = df.reindex(columns=["__label", *idx.keys()])
    y = df["__label"].rank().to_numpy(dtype=np.float64)
    X = df[list(idx.keys())].rank().to_numpy(dtype=np.float64)
    mask = ~np.isnan(X) & ~np.isnan(y)[:, None]
    n = mask.sum(axis=0)
    n_vec[:] = n
    ok = n >= _WORKER["min_syms"]
    if not ok.any():
        return dt, ic_vec, n_vec
    Xm = np.where(mask, X, 0.0)
    ym = np.where(mask, y[:, None], 0.0)
    nf = n.astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        sx = Xm.sum(axis=0)
        sy = ym.sum(axis=0)
        sxx = (Xm * Xm).sum(axis=0)
        syy = (ym * ym).sum(axis=0)
        sxy = (Xm * ym).sum(axis=0)
        cov = sxy - sx * sy / nf
        vx = sxx - sx * sx / nf
        vy = syy - sy * sy / nf
        ic = cov / np.sqrt(vx * vy)
    ic_vec[ok] = ic[ok]
    return dt, ic_vec, n_vec


def compute_recent_ic(
    dates: list[str],
    labels: pd.DataFrame,
    blocks: list[tuple[str, list[str]]],
    feats_all: list[str],
    min_syms: int,
    workers: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    date_set = set(dates)
    lab_by_date = {
        dt: sub[["symbol", "__label"]]
        for dt, sub in labels.groupby("date", sort=False)
        if dt in date_set
    }
    tasks = [(dt, lab_by_date[dt]) for dt in dates if dt in lab_by_date]
    if not tasks:
        raise RuntimeError("窗口内无任何可计算日期（锚库标签与分区不交集）")
    root = str(resolve_quantdb_dir())
    rows_ic: dict[str, np.ndarray] = {}
    rows_n: dict[str, np.ndarray] = {}
    t0 = time.time()
    if workers <= 1:
        _init_worker(root, blocks, feats_all, min_syms)
        it = (_ic_one_date(t) for t in tasks)
        for i, (dt, ic, n) in enumerate(it, 1):
            rows_ic[dt], rows_n[dt] = ic, n
            if i % 50 == 0:
                logger.info("IC 进度 %d/%d（%.0fs）", i, len(tasks), time.time() - t0)
    else:
        with Pool(
            workers,
            initializer=_init_worker,
            initargs=(root, blocks, feats_all, min_syms),
        ) as pool:
            for i, (dt, ic, n) in enumerate(
                pool.imap_unordered(_ic_one_date, tasks, chunksize=1), 1
            ):
                rows_ic[dt], rows_n[dt] = ic, n
                if i % 50 == 0:
                    logger.info(
                        "IC 进度 %d/%d（%.0fs）", i, len(tasks), time.time() - t0
                    )
    order = sorted(rows_ic)
    ic_df = pd.DataFrame([rows_ic[dt] for dt in order], index=order, columns=feats_all)
    n_df = pd.DataFrame([rows_n[dt] for dt in order], index=order, columns=feats_all)
    logger.info(
        "IC 计算完成：%d 期 × %d 因子（%.0fs）",
        len(order),
        len(feats_all),
        time.time() - t0,
    )
    return ic_df, n_df


# ── 统计与门槛 ──────────────────────────────────────────────────────────


def _stats(s: pd.Series) -> dict:
    s = s.dropna()
    n = int(len(s))
    if n == 0:
        return {"n": 0, "mean": None, "icir": None, "win": None}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if n > 1 else float("nan")
    icir = mean / std if std and not math.isnan(std) and std > 0 else None
    win = float((np.sign(s) == np.sign(mean)).mean()) if mean != 0 else None
    return {"n": n, "mean": mean, "icir": icir, "win": win}


# ── 配额选型 ────────────────────────────────────────────────────────────


def quota_select(eligible: list[dict], n_target: int) -> tuple[list[dict], list[dict]]:
    """小类全取 + 大类 √n 配额；返回 (选中, 配额表)。"""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in eligible:  # eligible 已按 score 降序
        by_cat[f["category"]].append(f)
    tiny = {c: lst for c, lst in by_cat.items() if len(lst) <= SMALL_CATEGORY_MAX}
    big = {c: lst for c, lst in by_cat.items() if len(lst) > SMALL_CATEGORY_MAX}
    selected: list[dict] = []
    for lst in tiny.values():
        selected.extend(lst)
    remaining = max(0, n_target - len(selected))
    quotas: dict[str, int] = {c: len(lst) for c, lst in tiny.items()}
    if big and remaining > 0:
        w = {c: math.sqrt(len(lst)) for c, lst in big.items()}
        wsum = sum(w.values())
        raw = {c: remaining * w[c] / wsum for c in big}
        quota = {c: min(int(math.floor(raw[c])), len(big[c])) for c in big}
        # 余数按小数部分降序补（限额内），不足则顺序顺延
        frac = sorted(big, key=lambda c: (-(raw[c] - math.floor(raw[c])), c))
        short = remaining - sum(quota.values())
        while short > 0:
            moved = False
            for c in frac:
                if short <= 0:
                    break
                if quota[c] < len(big[c]):
                    quota[c] += 1
                    short -= 1
                    moved = True
            if not moved:
                break  # 大类池子不够，交由全局补位
        for c, q in quota.items():
            quotas[c] = q
            selected.extend(big[c][:q])
    quota_rows = []
    for c in sorted(by_cat, key=lambda x: -len(by_cat[x])):
        quota_rows.append(
            {
                "category": c,
                "category_name": CATEGORY_BY_ID[c].name,
                "n_eligible": len(by_cat[c]),
                "quota": quotas.get(c, 0),
                "taken": min(quotas.get(c, 0), len(by_cat[c])),
                "tiny": c in tiny,
            }
        )
    # 全局补位：某类池不足时，从剩余未选者按 score 降序补齐
    chosen = {f["name"] for f in selected}
    if len(selected) < n_target:
        for f in eligible:
            if len(selected) >= n_target:
                break
            if f["name"] not in chosen:
                selected.append(f)
                chosen.add(f["name"])
    return selected, quota_rows


def corr_finalize(
    selected: list[dict],
    eligible: list[dict],
    n_target: int,
    threshold: float,
    corr_npz: Path,
) -> tuple[list[dict], list[dict], list[dict]]:
    """|ρ| ≥ 阈值贪婪终检 + 同类顺位替补。返回 (终选, 剔除明细, 替补明细)。"""
    z = np.load(corr_npz, allow_pickle=True)
    names = [str(x) for x in z["names"]]
    M = z["matrix"]
    pos = {n: i for i, n in enumerate(names)}

    def rho(a: str, b: str) -> float | None:
        i, j = pos.get(a), pos.get(b)
        if i is None or j is None:
            return None
        v = abs(float(M[i, j]))
        return v if not math.isnan(v) else None

    order = sorted(selected, key=lambda f: f["_sort_key"])
    kept: list[dict] = []
    kept_names: list[str] = []
    dropped: list[dict] = []
    for f in order:
        conflict = None
        for k in kept_names:
            r = rho(f["name"], k)
            if r is not None and r >= threshold:
                conflict = (k, r)
                break
        if conflict:
            dropped.append(
                {**f, "conflict_with": conflict[0], "abs_rho": round(conflict[1], 4)}
            )
        else:
            kept.append(f)
            kept_names.append(f["name"])
    # 替补：被剔者优先同类别顺位；不足再全局顺位
    used = {f["name"] for f in kept} | {f["name"] for f in dropped}
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in eligible:
        if f["name"] not in used:
            by_cat[f["category"]].append(f)
    swaps: list[dict] = []

    def try_add(f: dict, for_dropped: dict | None) -> bool:
        for k in kept_names:
            r = rho(f["name"], k)
            if r is not None and r >= threshold:
                return False
        kept.append(f)
        kept_names.append(f["name"])
        swaps.append(
            {
                "dropped": for_dropped["name"] if for_dropped else None,
                "dropped_library": for_dropped["library"] if for_dropped else None,
                "replaced_by": f["name"],
                "replaced_library": f["library"],
                "category": f["category"],
                "dropped_abs_rho_with": (
                    for_dropped.get("conflict_with") if for_dropped else None
                ),
            }
        )
        return True

    for d in dropped:
        if len(kept) >= n_target:
            break
        pool = by_cat.get(d["category"], [])
        while pool:
            cand = pool.pop(0)
            if cand["name"] in used:
                continue
            used.add(cand["name"])
            if try_add(cand, d):
                break
    if len(kept) < n_target:
        for c in sorted(by_cat):
            for cand in by_cat[c]:
                if len(kept) >= n_target:
                    break
                if cand["name"] in used:
                    continue
                used.add(cand["name"])
                try_add(cand, None)
    return kept, dropped, swaps


# ── 报告 ────────────────────────────────────────────────────────────────


def _fmt(v, nd=4, pct=False):
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:.{nd}f}"


def write_report(
    path: Path,
    args,
    funnel: dict,
    quota_rows: list[dict],
    kept: list[dict],
    dropped_corr: list[dict],
    swaps: list[dict],
    excluded: list[dict],
    sub_start: str,
    dates_used: int,
    sign_agree: float | None,
) -> None:
    lines: list[str] = []
    lines.append(f"# 训练特征选型报告（984 → {len(kept)}）· {args.report_tag}")
    lines.append("")
    lines.append(
        "> 口径全文见 `docs/因子选型与T5跨库训练_细案_20261016.md`；"
        "机器可读明细见 `training_selection_300.json`，逐日 IC 见 `recent_ic.parquet`。"
    )
    lines.append("")
    lines.append("## 1. 漏斗")
    lines.append("")
    lines.append("| 阶段 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 筛选 kept（2026-10-15 全量扩展） | {funnel['kept_total']} |")
    lines.append(
        f"| 排除不可训（features_daily / factor_research） | −{funnel['excluded']} |"
    )
    lines.append(f"| 可训池 | {funnel['trainable']} |")
    lines.append(
        f"| 门槛：n_dates≥{args.min_dates} 且 |IC_12m|≥{args.min_abs_ic} | {funnel['eligible']} |"
    )
    lines.append(
        f"| 配额 + |ρ|≥{args.corr_threshold} 终检（剔 {len(dropped_corr)}、补 {len(swaps)}） "
        f"| **{len(kept)}** |"
    )
    lines.append("")
    lines.append(
        f"- 主窗 {args.window_start}..{args.window_end}，有效 {dates_used} 期；"
        f"对照窗 {sub_start}..{args.window_end}；锚库 {ANCHOR_LIB}（lag=1、horizon=5）。"
    )
    if sign_agree is not None:
        lines.append(
            f"- 方向一致性自检：近窗 IC 方向与全史 ic_mean 同号的因子占比 "
            f"{sign_agree * 100:.1f}%（口径不同、仅供 sanity，非门槛）。"
        )
    lines.append("")
    lines.append("## 2. 类别配额")
    lines.append("")
    lines.append("| 类别 | 可训 | 配额 | 入选 | 策略 |")
    lines.append("|---|---|---|---|---|")
    for r in quota_rows:
        lines.append(
            f"| {r['category_name']} | {r['n_eligible']} | {r['quota']} | {r['taken']} | "
            f"{'小类全取' if r['tiny'] else '√n 配额'} |"
        )
    lines.append("")
    lines.append("## 3. 入选明细（各类前 15，按 |ICIR_12m| 降序）")
    lines.append("")
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for f in kept:
        by_cat[f["category"]].append(f)
    for c in sorted(by_cat, key=lambda x: -len(by_cat[x])):
        lst = by_cat[c]
        lines.append(f"### {CATEGORY_BY_ID[c].name}（{len(lst)}）")
        lines.append("")
        lines.append(
            "| 因子 | 库 | IC_12m | ICIR_12m | 胜率 | IC_6m | ICIR_6m | 期数 | 全史IC |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for f in lst[:15]:
            lines.append(
                f"| {f['name']} | {f['library']} | {_fmt(f['ic_12m'])} | {_fmt(f['icir_12m'], 2)} "
                f"| {_fmt(f['win_12m'], pct=True)} | {_fmt(f['ic_6m'])} | {_fmt(f['icir_6m'], 2)} "
                f"| {f['n_dates']} | {_fmt(f['full_ic'])} |"
            )
        if len(lst) > 15:
            lines.append(f"| …其余 {len(lst) - 15} 条见 JSON | | | | | | | | |")
        lines.append("")
    if dropped_corr:
        lines.append(
            f"## 4. 冗余终检剔除（|ρ|≥{args.corr_threshold}，{len(dropped_corr)} 条）"
        )
        lines.append("")
        lines.append("| 剔除 | 冲突对象 | |ρ| |")
        lines.append("|---|---|---|")
        for d in dropped_corr[:40]:
            lines.append(
                f"| {d['name']}（{d['library']}） | {d['conflict_with']} | {d['abs_rho']} |"
            )
        if len(dropped_corr) > 40:
            lines.append(f"| …其余 {len(dropped_corr) - 40} 条见 JSON | | |")
        lines.append("")
    lines.append(f"## 5. 排除清单（{len(excluded)} 条不可训）")
    lines.append("")
    lines.append("| 库 | 因子 | 原因 |")
    lines.append("|---|---|---|")
    for e in excluded:
        lines.append(f"| {e['library']} | {e['name']} | {e['reason']} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_dir = resolve_quantdb_dir() / "factor_research" / "screening"
    ap.add_argument(
        "--selection-json", type=Path, default=default_dir / "factor_selection.json"
    )
    ap.add_argument("--corr-npz", type=Path, default=default_dir / "cross_corr.npz")
    ap.add_argument("--out-dir", type=Path, default=default_dir)
    ap.add_argument("--window-start", default="2025-09-01")
    ap.add_argument("--window-end", default="2026-09-10")
    ap.add_argument(
        "--label-end", default="", help="锚库面板读取上限（空=不限，须覆盖窗尾 t+6）"
    )
    ap.add_argument("--sub-months", type=int, default=6, help="对照窗 = 主窗末 −N 个月")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--lag", type=int, default=1)
    ap.add_argument("--min-dates", type=int, default=120)
    ap.add_argument("--min-abs-ic", type=float, default=0.01)
    ap.add_argument("--corr-threshold", type=float, default=0.85)
    ap.add_argument("--n-target", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--max-dates", type=int, default=0, help="冒烟：只算最近 N 期（0=全量）"
    )
    ap.add_argument("--report-tag", default=None, help="默认 CST 当日 YYYYMMDD")
    args = ap.parse_args()

    tag = args.report_tag or datetime.now(CST).strftime("%Y%m%d")
    args.report_tag = tag
    t_all = time.time()

    sel = json.loads(args.selection_json.read_text(encoding="utf-8"))
    kept_all: list[dict] = sel["kept"]
    logger.info("kept 载入 %d 条（%s）", len(kept_all), args.selection_json)

    excluded = [
        {
            "name": f["name"],
            "library": f["library"],
            "reason": EXCLUDED_LIBS[f["library"]],
        }
        for f in kept_all
        if f["library"] in EXCLUDED_LIBS
    ]
    trainable = [f for f in kept_all if f["library"] not in EXCLUDED_LIBS]
    feats_all = [f["name"] for f in trainable]
    if len(feats_all) != len(set(feats_all)):
        raise RuntimeError(
            "kept 特征名重复——筛选去重契约被破坏，先查 factor_selection.json"
        )
    logger.info("可训池 %d 条，排除 %d 条", len(trainable), len(excluded))

    blocks: list[tuple[str, list[str]]] = []
    by_lib: dict[str, list[str]] = defaultdict(list)
    for f in trainable:
        by_lib[f["library"]].append(f["name"])
    for lib in sorted(by_lib):
        blocks.append((lib, by_lib[lib]))
    logger.info("库块：%s", ", ".join(f"{lib}({len(cols)})" for lib, cols in blocks))

    win_start = args.window_start.replace("-", "")
    win_end = args.window_end.replace("-", "")
    labels = build_labels(
        win_start, args.label_end.replace("-", "") or None, args.horizon, args.lag
    )
    sub_start_ts = pd.Timestamp(args.window_end) - pd.DateOffset(months=args.sub_months)
    sub_start = sub_start_ts.strftime("%Y%m%d")
    win_dates = sorted(d for d in labels["date"].unique() if win_start <= d <= win_end)
    n_full = len(win_dates)
    if args.max_dates and n_full > args.max_dates:
        win_dates = win_dates[-args.max_dates :]
    logger.info(
        "主窗日期 %d 期（%s..%s），对照窗起点 %s",
        len(win_dates),
        win_dates[0],
        win_dates[-1],
        sub_start,
    )

    ic_df, n_df = compute_recent_ic(
        win_dates, labels, blocks, feats_all, MIN_SYMBOLS_PER_DATE, args.workers
    )

    # 逐日落档（长表）
    long_rows = []
    for dt in ic_df.index:
        s = ic_df.loc[dt]
        nn = n_df.loc[dt]
        for name in feats_all:
            v = s[name]
            if pd.isna(v):
                continue
            long_rows.append((name, dt, float(v), int(nn[name])))
    recent_path = args.out_dir / "recent_ic.parquet"
    pd.DataFrame(long_rows, columns=["name", "trade_date", "ic", "n_valid"]).to_parquet(
        recent_path, index=False
    )
    logger.info("recent_ic.parquet 落盘：%d 行", len(long_rows))

    # 统计与门槛
    eligible: list[dict] = []
    for f in trainable:
        s12 = ic_df[f["name"]]
        st = _stats(s12)
        if (
            st["n"] < args.min_dates
            or st["mean"] is None
            or abs(st["mean"]) < args.min_abs_ic
        ):
            continue
        sub = ic_df.loc[[d for d in ic_df.index if d >= sub_start], f["name"]]
        st6 = _stats(sub)
        cat = classify_code(f["name"])
        score_key = (
            -abs(st["icir"]) if st["icir"] is not None else 0.0,
            -abs(st6["mean"]) if st6["mean"] is not None else 0.0,
            -abs(f.get("icir") or 0.0),
            f["name"],
        )
        eligible.append(
            {
                "name": f["name"],
                "library": f["library"],
                "sublibrary": f.get("sublibrary"),
                "display_name": f.get("display_name") or f["name"],
                "category": cat,
                "ic_12m": st["mean"],
                "icir_12m": st["icir"],
                "win_12m": st["win"],
                "n_dates": st["n"],
                "ic_6m": st6["mean"],
                "icir_6m": st6["icir"],
                "full_ic": f.get("ic_mean"),
                "full_icir": f.get("icir"),
                "turnover": f.get("turnover"),
                "_sort_key": score_key,
            }
        )
    eligible.sort(key=lambda f: f["_sort_key"])
    logger.info("过门槛 %d 条（可训 %d）", len(eligible), len(trainable))
    if len(eligible) < args.n_target:
        logger.warning(
            "过门槛数 %d < 目标 %d，将全取并说明", len(eligible), args.n_target
        )

    # 方向一致性 sanity（近窗 vs 全史）
    pairs = [
        (f["ic_12m"], f["full_ic"])
        for f in eligible
        if f["ic_12m"] is not None and f["full_ic"] not in (None, 0)
    ]
    sign_agree = (
        float(np.mean([np.sign(a) == np.sign(b) for a, b in pairs])) if pairs else None
    )

    selected, quota_rows = quota_select(eligible, args.n_target)
    kept_final, dropped_corr, swaps = corr_finalize(
        selected, eligible, args.n_target, args.corr_threshold, args.corr_npz
    )
    logger.info(
        "终选 %d 条（冗余剔 %d、补 %d）", len(kept_final), len(dropped_corr), len(swaps)
    )

    # payload（锚库裸名，副库 "库:列"）
    features_payload = [
        f["name"] if f["library"] == ANCHOR_LIB else f"{f['library']}:{f['name']}"
        for f in sorted(kept_final, key=lambda f: f["_sort_key"])
    ]
    libs_used = sorted({f["library"] for f in kept_final})
    clean = [
        {k: v for k, v in f.items() if not k.startswith("_")}
        for f in sorted(kept_final, key=lambda f: f["_sort_key"])
    ]
    out = {
        "generated_at": datetime.now(CST).isoformat(),
        "params": {
            "window_start": args.window_start,
            "window_end": args.window_end,
            "sub_window_start": sub_start,
            "horizon": args.horizon,
            "lag": args.lag,
            "min_dates": args.min_dates,
            "min_abs_ic": args.min_abs_ic,
            "corr_threshold": args.corr_threshold,
            "n_target": args.n_target,
            "anchor_lib": ANCHOR_LIB,
        },
        "funnel": {
            "kept_total": len(kept_all),
            "excluded": len(excluded),
            "trainable": len(trainable),
            "eligible": len(eligible),
            "final": len(kept_final),
        },
        "category_quota": quota_rows,
        "selected": clean,
        "corr_dropped": [
            {k: v for k, v in d.items() if not k.startswith("_")} for d in dropped_corr
        ],
        "corr_swaps": swaps,
        "excluded_non_trainable": excluded,
        "features_payload": features_payload,
        "libraries_used": libs_used,
        "sign_agree_12m_vs_full": sign_agree,
        "train_plan": {
            "anchor_lib": ANCHOR_LIB,
            "job_name": f"T5_Cross{len(kept_final)}_LGB_{tag}",
            "display_name": f"T+5 跨库{len(kept_final)}因子 LightGBM",
            "model_type": "lightgbm",
            "num_boost_round": 2000,
            "early_stopping_rounds": 100,
            "max_time_minutes": 720,
            # 训练窗 = 2022-10-17 起（非 2020）：① 全窗 7.54M 行 → 9.0G/帧，编排器
            # 注释有硬实证「7.7M 行直读尖峰击穿 48G」；本次宿主 available 仅 ~31G
            # （swap 已耗 14G），全窗有真实 OOM 风险。② 本窗 ≈4.6M 行、与最近成功
            # 先例（p2 树探针）同规模，且近期数据更契合「优先近期预测性」的选型口径。
            "split": {
                "train_start": "2022-10-17",
                "train_end": "2025-11-25",
                "valid_start": "2025-12-04",
                "valid_end": "2026-06-15",
                "test_start": "2026-06-25",
                "test_end": args.window_end,
            },
            "label": {"target_horizon_days": args.horizon, "target_mode": "return"},
            "context": {"market": "CN", "benchmark": "SH000300"},
        },
    }
    sel_path = args.out_dir / f"training_selection_{len(kept_final)}.json"
    sel_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("selection JSON 落盘：%s", sel_path)

    report_path = args.out_dir / f"训练选型报告_{tag}.md"
    funnel = out["funnel"]
    write_report(
        report_path,
        args,
        funnel,
        quota_rows,
        sorted(kept_final, key=lambda f: f["_sort_key"]),
        dropped_corr,
        swaps,
        excluded,
        sub_start,
        len(ic_df.index),
        sign_agree,
    )
    logger.info("报告落盘：%s", report_path)

    # 冒烟样例
    sample = clean[:5]
    for f in sample:
        logger.info(
            "样例 %-28s %-18s IC12=%s ICIR12=%s IC6=%s",
            f["name"],
            f["library"],
            _fmt(f["ic_12m"]),
            _fmt(f["icir_12m"], 2),
            _fmt(f["ic_6m"]),
        )
    logger.info("全流程 %.0fs", time.time() - t_all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
