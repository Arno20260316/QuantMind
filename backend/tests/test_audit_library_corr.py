"""T-FM-03 值级审计脚本（``audit_library_corr``）契约测试。

审计报告的两处消费路径对位置正确性要求不同：

- ``_block_stats``：先按块取子矩阵再排序输出 top——**过滤 NaN 后必须与
  「对清单」联合过滤**，否则 argsort 的位置会被错接到别的列名上（曾出过
  「数值对、名字错」的 top 表：0.9998 的对被标成库里其实是 -0.16 的列）；
- ``_reentry_simulation``：直接位置索引（flatnonzero），无此风险。

本文件钉死这两条路径的名字-数值配对正确性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.scripts.audit_library_corr import (  # noqa: E402
    _block_stats,
    _reentry_simulation,
)


def _mat(cols: list[str], entries: dict[tuple[str, str], float]) -> np.ndarray:
    """按 (a, b) 名对构建对称矩阵，未给定位置为 NaN（模拟覆盖不齐）。"""
    idx = {c: i for i, c in enumerate(cols)}
    mat = np.full((len(cols), len(cols)), np.nan)
    for (a, b), value in entries.items():
        mat[idx[a], idx[b]] = value
        mat[idx[b], idx[a]] = value
    return mat


class TestBlockStats:
    def test_top_names_match_values_with_nan_gaps(self):
        # rd_a/l1_x=0.9（最大）、rd_b/l1_y=0.5；rd_a/l1_y=0.3；rd_b/l1_x 缺失。
        cols = ["rd_a", "rd_b", "l1_x", "l1_y"]
        mat = _mat(
            cols, {("rd_a", "l1_x"): 0.9, ("rd_a", "l1_y"): 0.3, ("rd_b", "l1_y"): 0.5}
        )
        stats = _block_stats(cols, mat, "rd", "l1")
        assert stats["n_pairs"] == 3  # 缺失对不计
        top = stats["top"]
        assert top[0] == {"a": "rd_a", "b": "l1_x", "abs_rho": 0.9}
        assert top[1] == {"a": "rd_b", "b": "l1_y", "abs_rho": 0.5}
        assert top[2] == {"a": "rd_a", "b": "l1_y", "abs_rho": 0.3}

    def test_within_block_pairs_and_names(self):
        cols = ["rd_a", "rd_b", "rd_c"]
        mat = _mat(cols, {("rd_a", "rd_c"): 0.8, ("rd_b", "rd_c"): 0.6})
        stats = _block_stats(cols, mat, "rd", "rd")
        assert stats["n_pairs"] == 2
        assert stats["top"][0] == {"a": "rd_a", "b": "rd_c", "abs_rho": 0.8}
        assert stats["top"][1] == {"a": "rd_b", "b": "rd_c", "abs_rho": 0.6}

    def test_negative_uses_abs_for_ranking(self):
        cols = ["rd_a", "l1_x", "l1_y"]
        mat = _mat(cols, {("rd_a", "l1_x"): -0.9, ("rd_a", "l1_y"): 0.5})
        stats = _block_stats(cols, mat, "rd", "l1")
        assert stats["top"][0]["b"] == "l1_x"
        assert stats["top"][0]["abs_rho"] == 0.9

    def test_empty_block(self):
        cols = ["rd_a", "l1_x"]
        stats = _block_stats(cols, np.full((2, 2), np.nan), "rd", "l1")
        assert stats["n_pairs"] == 0
        assert stats["top"] == []


class TestReentrySimulation:
    def test_hits_use_named_positions(self):
        cols = ["rd_a", "rd_b", "l1_x", "l1_y"]
        mat = _mat(
            cols,
            {("rd_a", "l1_x"): 0.6, ("rd_a", "l1_y"): 0.45, ("rd_b", "l1_x"): 0.95},
        )
        sim = _reentry_simulation(cols, mat)
        row = {r["column"]: r for r in sim["per_column"]}
        assert row["rd_a"]["n_hits_0.5"] == 1
        assert row["rd_a"]["hits_0.5"][0]["against"] == "l1_x"
        assert row["rd_b"]["hits_0.5"][0]["against"] == "l1_x"
        assert sim["buckets"]["0.5"][1] == 2  # 两列各单命中
        assert sim["buckets"]["0.9"][1] == 1  # rd_b/l1_x=0.95 单命中
