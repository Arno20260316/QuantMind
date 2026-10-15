"""排行榜「去重」纯函数（与筛选页同口径）：|ρ| ≥ 阈值的同源因子只保留更靠前者。

口径来源 = ``backend/scripts/screen_factors.py`` 的 ``greedy_direct_corr``：
按给定顺序（筛选页 = 强度序；排行榜 = 榜单序）逐个检查已保留集合，命中
``|ρ| ≥ 阈值`` 即剔除，``duplicate_of`` 记已保留中 |ρ| 最大者；矩阵未覆盖的因子
不参与去重（**覆盖回退**：必保留，不误剔）。**直接贪心**（非传递闭包），
与筛选页逐字同源。

相关矩阵 = 逐月截面 Spearman（rank → pairwise-complete Pearson, min_periods=50）
82 期均值：
- private：``cross_corr.npz``（``backend/scripts/build_factor_panel_corr.py`` 构建，全量因子）；
- classic：``corr.parquet`` 长表（build_factor_research 生成，覆盖 73/82 个名字）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def greedy_dedup(
    codes: list[str],
    names: list[str],
    matrix: np.ndarray,
    threshold: float,
) -> tuple[list[str], list[str], dict[str, str]]:
    """按 ``codes`` 给定序贪心去重（先到先得）。

    Args:
        codes: 判定序（排行榜 = 榜单序：先见者优先保留）。
        names: 矩阵行列名（``matrix[i, j]`` 对应 ``names[i]``/``names[j]``）。
        matrix: (F, F) 相关矩阵（可含 NaN；``codes`` 里矩阵未覆盖者必保留）。
        threshold: 剔除阈值（``|ρ| ≥ threshold`` → 剔除），须 > 0。

    Returns:
        ``(kept, dropped, duplicate_of)``：kept = 保留 code（输入序）；
        dropped = 被剔 code（输入序）；duplicate_of = 被剔 code → 最相关保留者。
    """
    if threshold <= 0:
        raise ValueError("threshold 必须 > 0（筛选页口径为 |ρ| ≥ 0.9）")
    pos = {n: i for i, n in enumerate(names)}
    kept: list[str] = []
    covered_codes: list[str] = []  # 已保留且被矩阵覆盖者，与 kept_idx 同位
    kept_idx = np.empty(len(matrix), dtype=np.intp)
    n_kept = 0
    dropped: list[str] = []
    duplicate_of: dict[str, str] = {}

    for code in codes:
        i = pos.get(code)
        if i is None:  # 矩阵未覆盖 → 不参与去重，必保留
            kept.append(code)
            continue
        best_j = -1
        if n_kept:
            rho = np.abs(matrix[i, kept_idx[:n_kept]])
            hits = np.flatnonzero(np.isfinite(rho) & (rho >= threshold))
            if hits.size:  # 命中中取 |ρ| 最大者（并列取先见者，同筛选页 max(key=)）
                best_j = int(hits[np.argmax(rho[hits])])
        if best_j >= 0:
            dropped.append(code)
            duplicate_of[code] = covered_codes[best_j]
        else:
            kept.append(code)
            covered_codes.append(code)
            kept_idx[n_kept] = i
            n_kept += 1
    return kept, dropped, duplicate_of


def matrix_from_pairs(pairs: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    """经典库 ``corr.parquet`` 长表（factor_a, factor_b, corr）→ (names, 对称方阵)。

    长表只存单方向 + 对角（C(n,2)+n 行），这里镜像补齐；未出现的对为 NaN。
    """
    names = sorted(set(pairs["factor_a"]) | set(pairs["factor_b"]))
    pos = {n: i for i, n in enumerate(names)}
    m = np.full((len(names), len(names)), np.nan)
    ia = pairs["factor_a"].map(pos).to_numpy(dtype=np.intp)
    ib = pairs["factor_b"].map(pos).to_numpy(dtype=np.intp)
    v = pairs["corr"].to_numpy(dtype=np.float64)
    m[ia, ib] = v
    m[ib, ia] = v
    np.fill_diagonal(m, 1.0)
    return names, m
