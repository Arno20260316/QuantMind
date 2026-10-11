"""因子池装载 → 近效自算 IC → 闸门 → 族封顶（因子概率板 P0）。

IC 全部自算（dt 分区截面 × 前向收益），**不依赖各库 factor_series**——alpha_library
的报告已陈旧至 2026-08-27。前向收益由调用方以 qfq 收盘矩阵计算（见 composite.fwd_ranks）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from backend.shared.factor_board import config as C

log = logging.getLogger("factor_board.selection")


def load_candidates(quantdb_root: Path) -> tuple[pd.DataFrame, dict]:
    """组装候选池：kept-362（日频库） + rd_mined 报告。

    返回 (cand, fingerprints)；cand 列：name/lib/sublibrary/display_name/kept_ic/
    kept_icir/turnover/src/group。
    """
    sel_path = quantdb_root / C.SELECTION_JSON_REL
    if not sel_path.exists():
        raise FileNotFoundError(f"联合筛选清单缺失: {sel_path}")
    raw = json.loads(sel_path.read_text(encoding="utf-8"))
    kept = pd.DataFrame(raw["kept"])
    kept = kept[kept["library"].isin(C.DAILY_LIBS)].copy()
    kept = kept.rename(
        columns={"library": "lib", "ic_mean": "kept_ic", "icir": "kept_icir"}
    )
    kept["src"] = "kept"
    cols = [
        "name",
        "lib",
        "sublibrary",
        "display_name",
        "kept_ic",
        "kept_icir",
        "turnover",
        "src",
    ]
    kept = kept.reindex(columns=cols)

    rd_rows: list[dict] = []
    rd_path = quantdb_root / C.RD_REPORT_REL
    rd_fp: dict = {"rd_report": None}
    if rd_path.exists():
        rd_fp["rd_report"] = _file_fingerprint(rd_path)
        for f in json.loads(rd_path.read_text(encoding="utf-8")).get("factors", []):
            icm = f.get("ic_neutral_mean")
            if icm is None:
                icm = f.get("ic_mean")
            if icm is None or abs(icm) < C.MIN_RD_REPORT_IC:
                continue
            rd_rows.append(
                {
                    "name": f["name"],
                    "lib": "rd_mined",
                    "sublibrary": C.RD_SUBLIBRARY,
                    "display_name": f.get("display_name") or f["name"],
                    "kept_ic": icm,
                    "kept_icir": f.get("icir"),
                    "turnover": None,
                    "src": "rd",
                }
            )
    else:
        log.warning("rd_mined 报告缺失，跳过 rd 源: %s", rd_path)

    cand = pd.concat([kept, pd.DataFrame(rd_rows, columns=cols)], ignore_index=True)
    cand["sublibrary"] = cand["sublibrary"].fillna("")
    cand["group"] = cand["lib"] + "/" + cand["sublibrary"].astype(str)
    fp = {
        "selection_json": _file_fingerprint(sel_path),
        **rd_fp,
    }
    return cand.reset_index(drop=True), fp


def _file_fingerprint(path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return {"sha256_16": digest, "mtime": int(path.stat().st_mtime)}


def drop_missing_columns(
    cand: pd.DataFrame, quantdb_root: Path, ref_date: str
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """按 ref_date 分区剔除某库不存在的因子列（分区缺失的整库剔除并告警）。"""
    dropped: dict[str, list[str]] = {}
    dropped_keys: set[tuple[str, str]] = set()
    for lib, g in cand.groupby("lib"):
        p = quantdb_root / "6_ml_datasets" / lib / f"dt={ref_date}" / "data.parquet"
        if not p.exists():
            dropped[lib] = g["name"].tolist()
            dropped_keys.update((lib, n) for n in g["name"])
            log.warning("库 %s 于 %s 无分区，整库剔除 %d 个候选", lib, ref_date, len(g))
            continue
        have = set(pq.ParquetFile(p).schema_arrow.names)
        miss = [n for n in g["name"] if n not in have]
        if miss:
            dropped[f"{lib}/缺列"] = miss
            dropped_keys.update((lib, n) for n in miss)
            log.warning("库 %s 缺列剔除 %d 个: %s", lib, len(miss), miss[:3])
    keep = ~cand.apply(lambda r: (r["lib"], r["name"]) in dropped_keys, axis=1)
    return cand[keep].reset_index(drop=True), dropped


def compute_ic_table(
    quantdb_root: Path,
    cand: pd.DataFrame,
    eval_dates: list[str],
    fwd_ranks: dict[str, pd.Series],
    *,
    horizon: int = 5,
) -> dict[tuple[str, str], list[float]]:
    """逐评估日算每个候选因子对前向 ``horizon`` 日收益的截面 RankIC。

    ``fwd_ranks[dt]`` 为该日前向收益的 rank 序列（index=symbol）。
    """
    acc: dict[tuple[str, str], list[float]] = {
        (lib, n): [] for lib, n in zip(cand["lib"], cand["name"], strict=True)
    }
    by_lib = {lib: g["name"].tolist() for lib, g in cand.groupby("lib")}
    for dt in eval_dates:
        fr = fwd_ranks.get(dt)
        if fr is None or fr.notna().sum() < C.MIN_STOCK_SAMPLE:
            continue
        for lib, names in by_lib.items():
            p = quantdb_root / "6_ml_datasets" / lib / f"dt={dt}" / "data.parquet"
            if not p.exists():
                continue
            d = pd.read_parquet(p, columns=["symbol"] + names).set_index("symbol")
            for n in names:
                v = d[n]
                m = v.notna() & fr.notna()
                val = (
                    v[m].rank().corr(fr[m])
                    if int(m.sum()) >= C.MIN_STOCK_SAMPLE
                    else np.nan
                )
                acc[(lib, n)].append(val)
    return acc


def summarize_ic(
    ic_table: dict[tuple[str, str], list[float]], cand: pd.DataFrame
) -> pd.DataFrame:
    """汇总每个候选的 IC60/胜率/ICIR60（不够 IC_WINDOW-… 天有效 IC 的不可评估）。"""
    meta = cand.drop_duplicates(["lib", "name"]).set_index(["lib", "name"])
    rows = []
    for (lib, n), ics in ic_table.items():
        s = pd.Series(ics, dtype=float).dropna().iloc[-C.IC_WINDOW :]
        if len(s) < C.MIN_IC_DAYS:
            continue
        ic60 = float(s.mean())
        if not math.isfinite(ic60):
            continue
        sd = float(s.std())
        rows.append(
            {
                "lib": lib,
                "name": n,
                "group": meta.loc[(lib, n), "group"],
                "display": meta.loc[(lib, n), "display_name"],
                "kept_ic": float(meta.loc[(lib, n), "kept_ic"]),
                "kept_icir": meta.loc[(lib, n), "kept_icir"],
                "turnover": meta.loc[(lib, n), "turnover"],
                "src": meta.loc[(lib, n), "src"],
                "ic60": ic60,
                "win60": float((np.sign(s) == np.sign(ic60)).mean()),
                "n_days": int(len(s)),
                "icir60": ic60 / sd * math.sqrt(len(s)) if sd > 0 else None,
            }
        )
    return pd.DataFrame(rows)


def gate(stats: pd.DataFrame, min_win: float) -> pd.DataFrame:
    """有效性闸：|IC60|≥门槛 ∧ 方向与联合闸一致 ∧ 胜率≥min_win。"""
    return stats[
        (stats["ic60"].abs() >= C.MIN_ABS_IC60)
        & (np.sign(stats["ic60"]) == np.sign(stats["kept_ic"]))
        & (stats["win60"] >= min_win)
    ]


def select_factors(stats: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """闸门 + 族封顶。返回 (sel, gate_info)；sel 带 ``sgn``（±1）与 ``w``。

    过闸数 <MIN_GATED 时降级胜率至 DEGRADE_WIN_RATE（同 v2 原型出口）。
    """
    gated = gate(stats, C.MIN_WIN_RATE)
    degraded = False
    if len(gated) < C.MIN_GATED:
        gated = gate(stats, C.DEGRADE_WIN_RATE)
        degraded = True
        log.warning(
            "过闸数不足，胜率门槛降级至 %.2f → %d 个", C.DEGRADE_WIN_RATE, len(gated)
        )
    sel = pd.concat(
        [
            g.sort_values("ic60", key=lambda s: s.abs(), ascending=False).head(
                C.RD_FAMILY_CAP if lib == "rd_mined" else C.FAMILY_CAP
            )
            for (lib, _grp), g in gated.groupby(["lib", "group"])
        ]
    ).drop_duplicates(["lib", "name"])
    sel = sel.reset_index(drop=True)
    sel["sgn"] = np.sign(sel["ic60"]).astype(int)
    sel["w"] = sel["ic60"].abs().clip(C.W_CLIP_LO, C.W_CLIP_HI)
    info = {
        "evaluated": int(len(stats)),
        "gated": int(len(gated)),
        "gated_long": int((gated["ic60"] > 0).sum()),
        "gated_short": int((gated["ic60"] < 0).sum()),
        "degraded": degraded,
        "families": int(sel["group"].nunique()),
    }
    return sel, info
