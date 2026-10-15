#!/usr/bin/env python3
"""私人因子库全量逐月截面相关矩阵构建（排行榜「去重」按钮的数据源）。

背景：因子研究「排行榜」新增去重切换（与筛选页同口径 |ρ| ≥ 0.9）——筛选页的
``cross_corr.npz`` 只覆盖过门槛候选列（2520），排行榜需要**全量因子**（4011）的
成对相关，故独立构建。

口径（= ``screen_factors.py`` 的联合相关，逐位等价）：
逐月末截面 Spearman = 每月末把 monthly_scores 宽表按列 ``rank(average, na_option=keep)``
→ pairwise-complete Pearson（``min_periods=50``）→ 82 期均值。

实现：4 次 matmul 技巧（N=MᵀM, A=MᵀR0, B=MᵀR0², P=R0ᵀR0），单期 ~6s；
与 pandas ``rank().corr(min_periods=50)`` 逐位对照 max|diff| ≈ 5.7e-15、
|ρ|≥0.9 判定 0 不一致（--verify 金样闸门，不通过拒绝落盘）。

产物（<quantdb>/factor_research_private/cross_corr.npz）:
    matrix        float32 (F,F) 逐月均值相关（对角 1；有效对不足 min_periods 为 NaN）
    names         (F,) 因子 code（= monthly_scores 列名）
    used          参与均值的期数
    generated_at / source_path / source_rows / source_mtime_ns / min_periods（审计）

⚠ 面板重建（build_factor_panel_private.py）后须重跑本脚本；
排行榜按**覆盖回退**——矩阵里没有的因子不参与去重、必保留（不误剔）。

用法（仓库根）:
    python3 backend/scripts/build_factor_panel_corr.py                 # 全量（~10 分钟）
    python3 backend/scripts/build_factor_panel_corr.py --max-months 3 --out /tmp/c.npz  # 冒烟
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.shared.quantdb_paths import resolve_quantdb_dir  # noqa: E402

MIN_PERIODS = 50  # 与 screen_factors.py 联合相关同口径（合约值，勿随意改）
_VERIFY_MAX_DIFF = 1e-9  # 金样断言容差（实测 5.7e-15，留 6 个数量级余量）
_BATCH_ROWS = 8192  # parquet 流式批大小（~130MB float32/批）


def rank_cols(X: np.ndarray) -> np.ndarray:
    """逐列截面 rank（average / na keep），返回 float64。"""
    return (
        pd.DataFrame(X)
        .rank(axis=0, method="average", na_option="keep")
        .to_numpy(dtype=np.float64)
    )


def pairwise_corr(R: np.ndarray, min_periods: int = MIN_PERIODS) -> np.ndarray:
    """已 rank 矩阵的 pairwise-complete Pearson（4 次 matmul），NaN 自动参与掩码。

    等价 pandas ``DataFrame(R).corr(min_periods=min_periods)``；对角置 1
    （所有列都非空的月份 pandas 对角也是 1；去重只用非对角）。
    """
    F = R.shape[1]
    M = np.isfinite(R)
    R0 = np.where(M, R, 0.0)
    R20 = R0 * R0
    Mf = M.astype(np.float64)
    N = Mf.T @ Mf  # 每对有效观测数
    A = Mf.T @ R0  # Σa
    B = Mf.T @ R20  # Σa²
    P = R0.T @ R0  # Σab
    num = N * P - A.T * A
    den = np.sqrt((N * B.T - A.T * A.T) * (N * B - A * A))
    with np.errstate(invalid="ignore", divide="ignore"):
        C = num / den
    C[N < min_periods] = np.nan
    np.fill_diagonal(C, 1.0)
    assert C.shape == (F, F)
    return C


def verify_block(
    R: np.ndarray, C: np.ndarray, cols: np.ndarray, names: list[str]
) -> tuple[float, int, int]:
    """单期抽列金样对照：返回 (max|diff|, |ρ|≥0.9 判定不一致数, 有效/缺失形状不符数)。

    只比非对角（去重不用对角；全 NaN 列的对角 pandas 为 NaN、本实现置 1，属
    设计内形状差异，不算失败）。
    """
    sub_names = [names[i] for i in cols]
    ref = (
        pd.DataFrame(R[:, cols], columns=sub_names)
        .corr(min_periods=MIN_PERIODS)
        .to_numpy()
    )
    mine = C[np.ix_(cols, cols)]
    ref_f = np.isfinite(ref)
    mine_f = np.isfinite(mine)
    off = ~np.eye(len(cols), dtype=bool)
    shape_bad = int(np.count_nonzero((ref_f ^ mine_f) & off))
    both = ref_f & mine_f & off
    max_diff = float(np.abs(mine[both] - ref[both]).max()) if both.any() else 0.0
    edge = ((np.abs(ref) >= 0.9) != (np.abs(mine) >= 0.9)) & both
    return max_diff, int(np.count_nonzero(edge)), shape_bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_src = (
        resolve_quantdb_dir() / "factor_research_private" / "monthly_scores.parquet"
    )
    ap.add_argument(
        "--path", type=Path, default=default_src, help="monthly_scores.parquet 路径"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 npz（默认与 --path 同目录 cross_corr.npz）",
    )
    ap.add_argument(
        "--verify-cols", type=int, default=200, help="金样对照抽样列数（默认 200）"
    )
    ap.add_argument("--no-verify", action="store_true", help="跳过金样自检（不建议）")
    ap.add_argument(
        "--max-months",
        type=int,
        default=0,
        help="只处理前 N 期（冒烟，>0 时不落主产物除 --out）",
    )
    ap.add_argument("--seed", type=int, default=7, help="金样抽样种子")
    args = ap.parse_args()

    src: Path = args.path
    out: Path = args.out or (src.parent / "cross_corr.npz")
    if not src.exists():
        print(f"[ERR] 源文件不存在：{src}")
        return 1

    # 可写性预检：factor_research_private 产物目录属主是 root（容器内构建），
    # 主机直跑会在 8 分钟算完之后才在落盘处炸 PermissionError —— 前置到这里。
    probe = out.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        print(
            f"[ERR] 输出目录不可写：{out.parent}（该目录属主为 root，"
            f"请在容器内以 root 运行：docker exec quantmind python3 {__file__}）"
        )
        return 1

    t_start = time.time()
    td_tbl = pq.read_table(src, columns=["trade_date"])
    td_all = np.asarray(
        pd.to_datetime(td_tbl.column("trade_date").to_numpy()).values,
        dtype="datetime64[ns]",
    )
    dates, counts = np.unique(td_all, return_counts=True)
    n_months = len(dates)
    print(f"源: {src}")
    print(
        f"月份数: {n_months}，行数: {len(td_all)}，首期 {dates[0]}（{counts[0]} 行），末期 {dates[-1]}（{counts[-1]} 行）"
    )

    todo = n_months if args.max_months <= 0 else min(args.max_months, n_months)
    smoke = args.max_months > 0

    pf = pq.ParquetFile(src)
    all_columns: list[str] | None = None
    F = 0
    acc = cnt = None
    verify_month = -1
    verify_cols: np.ndarray | None = None

    buf: list[np.ndarray] = []
    buf_td: list[np.ndarray] = []
    buf_rows = 0
    mi = 0
    target = int(counts[0])
    stop = False

    for batch in pf.iter_batches(batch_size=_BATCH_ROWS):
        df = batch.to_pandas()
        cols_now = [c for c in df.columns if c not in ("trade_date", "symbol")]
        if all_columns is None:
            all_columns = cols_now
            F = len(all_columns)
            acc = np.zeros((F, F), dtype=np.float64)
            cnt = np.zeros((F, F), dtype=np.float64)
            rng = np.random.default_rng(args.seed)
            verify_month = int(rng.integers(0, todo))
            if not args.no_verify:
                k = min(max(args.verify_cols, 2), F)
                verify_cols = np.sort(rng.choice(F, size=k, replace=False))
            print(
                f"因子列: {F}；金样对照期: 第 {verify_month + 1} 期"
                + (
                    f"，抽样 {len(verify_cols)} 列"
                    if verify_cols is not None
                    else "（关闭）"
                )
            )
        elif cols_now != all_columns:
            print("[ERR] 各批列不一致（parquet 列序漂移），中止")
            return 1

        X = df.drop(columns=["trade_date", "symbol"]).to_numpy(dtype=np.float32)
        td_b = np.asarray(
            pd.to_datetime(df["trade_date"]).values, dtype="datetime64[ns]"
        )
        buf.append(X)
        buf_td.append(td_b)
        buf_rows += len(X)

        while buf_rows >= target and not stop:
            big = np.concatenate(buf, axis=0) if len(buf) > 1 else buf[0]
            big_td = np.concatenate(buf_td, axis=0) if len(buf_td) > 1 else buf_td[0]
            Xm, td_m = big[:target], big_td[:target]
            rest, rest_td = big[target:], big_td[target:]
            buf = [rest] if len(rest) else []
            buf_td = [rest_td] if len(rest_td) else []
            buf_rows = len(rest)

            uniq = np.unique(td_m)
            if len(uniq) != 1:
                print(
                    f"[ERR] 第 {mi + 1} 期行块含 {len(uniq)} 个日期（行序假设被破坏），中止"
                )
                return 1

            t0 = time.time()
            R = rank_cols(Xm)
            C = pairwise_corr(R)
            fin = np.isfinite(C)
            acc[fin] += C[fin]
            cnt[fin] += 1.0
            note = ""
            if mi == verify_month and verify_cols is not None:
                max_diff, edge_bad, shape_bad = verify_block(
                    R, C, verify_cols, all_columns
                )
                ok = max_diff <= _VERIFY_MAX_DIFF and edge_bad == 0 and shape_bad == 0
                note = f"  金样 max|diff|={max_diff:.2e} 阈值不一致={edge_bad} 形状不符={shape_bad} {'PASS' if ok else '**FAIL**'}"
                if not ok:
                    print(f"[ERR] 第 {mi + 1} 期金样对照不通过，拒绝落盘")
                    return 1
            print(
                f"[{mi + 1:02d}/{todo}] {str(pd.Timestamp(uniq[0]).date())} "
                f"{target} 行 {time.time() - t0:.1f}s（累计 {(time.time() - t_start) / 60:.1f}min）{note}"
            )
            del R, C, Xm, big
            mi += 1
            if mi >= todo:
                stop = True
                break
            target = int(counts[mi])

    if mi < todo:
        print(f"[ERR] 只处理了 {mi}/{todo} 期（源数据行数不足？），拒绝落盘")
        return 1

    used = int(cnt.max())
    mean = np.full((F, F), np.nan, dtype=np.float64)
    ok = cnt > 0
    mean[ok] = acc[ok] / cnt[ok]
    np.fill_diagonal(mean, 1.0)
    matrix = mean.astype(np.float32)
    n_pairs = int(ok.sum())
    strong = int(((np.abs(matrix) >= 0.9) & ok).sum()) // 2
    print(
        f"完成：{F} 因子 × {todo} 期，均值有效对 {n_pairs}（|ρ|≥0.9 强相关对 {strong}）"
    )

    meta = {
        "used": np.int64(used),
        "min_periods": np.int64(MIN_PERIODS),
        "generated_at": np.asarray(datetime.now(timezone.utc).isoformat()),
        "source_path": np.asarray(str(src)),
        "source_rows": np.int64(len(td_all)),
        "source_mtime_ns": np.int64(src.stat().st_mtime_ns),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as fh:
        # 名字长度由 numpy 自推（**不能**钉 U64：factor_defs 的公式型因子名可超 64 字符，
        # 截断即静默改名 → 排行榜 code 匹配不上 → 永不去重）
        np.savez_compressed(fh, matrix=matrix, names=np.asarray(all_columns), **meta)
    tmp.replace(out)
    print(
        f"落盘：{out}（{out.stat().st_size / 1e6:.1f}MB，总耗时 {(time.time() - t_start) / 60:.1f}min）"
    )
    if smoke:
        print("[注意] --max-months 冒烟模式：产物未含全部月份，勿当正式缓存使用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
