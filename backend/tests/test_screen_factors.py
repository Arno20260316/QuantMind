"""因子筛选器：全量扩展（2026-10-15）契约。

背景：筛选器从 7 库扩到 13 库（对齐私人库 4011 全量口径，其中 6 个前视收益
标签列被防御性排除 → 参与面 4005+73）。两件事必须钉死：

1. ``REPORT_DATASETS`` 顺序 == 私人库扫描的 ``discovery.AUTO_PRIORITY`` + 未列
   目录字母序 —— cand_factors 与 factor_defs 有 238 列全同名，两处顺序分叉会让
   「参与面与私人库对齐」静默失效；
2. ``_dedup_first_wins`` 先到先得且**库内矩阵随列同步裁剪** —— 只裁名字不裁
   矩阵，列序错位后所有相关全部算错且无一层会报错。

另锁 features_daily 注册契约：前视收益标签（旧名 return_Nd + 20260930 起改名
future_return_Nd，两代并存于不同分区）必须全部在 meta_cols 排除 —— 漏一列
进了因子轴就会 |IC|≈1 霸榜、经 kept 清单漏进训练面板（P0 泄露）。
"""

from __future__ import annotations

import numpy as np

from backend.scripts.screen_factors import (
    REPORT_DATASETS,
    _dedup_first_wins,
    _gate_candidates,
    _matrix_from_cache,
)
from backend.services.engine.factor_report import datasets as ds
from backend.services.engine.factor_research.discovery import AUTO_PRIORITY, AUTO_SKIP


# ---------- 顺序口径 ----------


def test_report_datasets_order_matches_discovery_priority():
    """顺序 = AUTO_PRIORITY 相对序在前 + 其余按字母序；扫描跳过的目录不得入筛选。"""
    priority_part = [d for d in AUTO_PRIORITY if d in REPORT_DATASETS]
    rest = [d for d in REPORT_DATASETS if d not in AUTO_PRIORITY]
    assert REPORT_DATASETS == priority_part + sorted(rest)
    assert set(REPORT_DATASETS) & AUTO_SKIP == set()


# ---------- 先到先得去重 ----------


def test_first_wins_dedup_drops_later_duplicate_and_slices_matrix():
    """cand_factors 在前：factor_defs 的同名列让位，子矩阵按保留列裁剪到 d×d。"""
    cand = np.array([[1.0, 0.2, 0.3], [0.2, 1.0, 0.4], [0.3, 0.4, 1.0]])
    fd = np.array([[1.0, 0.5], [0.5, 9.9]])  # d×d = 9.9，与 c 行不同以便验裁剪位置
    blocks = [("cand_factors", ["a", "b", "c"], cand), ("factor_defs", ["c", "d"], fd)]

    out, dropped = _dedup_first_wins(blocks)

    assert dropped == {"factor_defs": 1}
    assert out[0][1] == ["a", "b", "c"]
    assert np.array_equal(out[0][2], cand)
    assert out[1][1] == ["d"]  # c 让位
    assert out[1][2].shape == (1, 1)
    # 留下的是 d×d（不是 c×c 的错位残片）
    assert out[1][2][0, 0] == 9.9


def test_first_wins_dedup_keeps_first_of_three_duplicates():
    m1 = np.eye(1)
    m3 = np.eye(2)
    blocks = [("l1", ["x"], m1), ("l2", ["x"], m1), ("l3", ["x", "y"], m3)]

    out, dropped = _dedup_first_wins(blocks)

    assert dropped == {"l2": 1, "l3": 1}
    assert [n for _, nms, _ in out for n in nms] == ["x", "y"]


def test_first_wins_dedup_no_duplicates_passthrough():
    mat = np.eye(2)
    out, dropped = _dedup_first_wins([("a", ["p", "q"], mat)])

    assert dropped == {}
    assert out[0][1] == ["p", "q"]
    assert np.array_equal(out[0][2], mat)


# ---------- 新库注册契约 ----------


def test_features_daily_registered_with_all_label_generations_excluded():
    cfg = ds.DATASETS["features_daily"]

    assert cfg["label_mode"] == "close_fwd"
    assert cfg["dir_parts"] == ("6_ml_datasets", "features_daily")
    meta = set(cfg["meta_cols"])
    # 两代前视收益标签 + 行情/遗留重复列，全部必须排除
    assert {
        "return_1d",
        "return_3d",
        "return_5d",
        "return_10d",
        "return_20d",
        "return_60d",
    } <= meta
    assert {
        "future_return_1d",
        "future_return_3d",
        "future_return_5d",
        "future_return_10d",
        "future_return_20d",
        "future_return_60d",
    } <= meta
    assert {"symbol", "time", "close", "close_val"} <= meta
    # 正常因子列不得误入 meta（漏排 = 因子少算；顺带防「顺手全排除」）
    assert "rsi_6" not in meta
    assert "is_st" not in meta
    assert cfg.get("build_start") == "20160104"


def test_cand_factors_registered_as_close_fwd():
    cfg = ds.DATASETS["cand_factors"]

    assert cfg["label_mode"] == "close_fwd"
    assert cfg["dir_parts"] == ("6_ml_datasets", "cand_factors")
    assert cfg["library_rule"] == "fixed:cand_factors"
    assert cfg.get("build_start") == "20160104"


def test_l1_batch1_registered_as_close_fwd_donor():
    cfg = ds.DATASETS["l1_batch1_factors"]

    assert cfg["label_mode"] == "close_fwd_donor"
    assert cfg["donor_parts"] == ("6_ml_datasets", "l1_factors")
    assert cfg["library_rule"] == "fixed:l1_batch1_factors"
    assert cfg.get("build_start") == "20160219"


def test_library_of_new_datasets_maps_every_column_to_itself():
    assert ds.library_of("cand_factors", "feat_any") == "cand_factors"
    assert ds.library_of("features_daily", "rsi_6") == "features_daily"
    assert ds.library_of("l1_batch1_factors", "lu_days_20") == "l1_batch1_factors"


# ---------- 质量门槛（纯函数） ----------


def test_gate_candidates_boundary_abs_and_input_order():
    """恰在门槛上（==）算过；绝对值口径；候选沿输入序（= 矩阵列序）。"""
    metrics = {
        "a": {"ic_mean": 0.02, "icir": 0.2},  # 恰在门槛 → 过
        "b": {"ic_mean": -0.05, "icir": -0.9},  # 负号取绝对值 → 过
        "c": {"ic_mean": 0.0199, "icir": 0.5},  # |IC| 差一点 → 剔
        "d": {"ic_mean": 0.5, "icir": 0.199},  # |ICIR| 差一点 → 剔
    }
    candidates, gated = _gate_candidates(["d", "c", "a", "b"], metrics, 0.02, 0.2)

    assert candidates == ["a", "b"]
    assert set(gated) == {"c", "d"}
    assert "IC" in gated["c"]
    assert "ICIR" in gated["d"]


def test_gate_candidates_missing_metrics_key_equals_none():
    """metrics 条目缺 ic_mean/icir 键与 None 等价（未计算或退化 → 剔除并记原因）。"""
    candidates, gated = _gate_candidates(
        ["x", "y"], {"x": {}, "y": {"ic_mean": 0.03}}, 0.02, 0.2
    )

    assert candidates == []
    assert gated == {
        "x": "无 IC/ICIR 指标（未计算或退化）",
        "y": "无 IC/ICIR 指标（未计算或退化）",
    }


# ---------- 联合相关缓存复用（纯函数） ----------


def test_matrix_from_cache_exact_match_passes_through():
    mat = np.array([[1.0, 0.3], [0.3, 1.0]])
    assert _matrix_from_cache(["a", "b"], mat, ["a", "b"]) is mat


def test_matrix_from_cache_subset_slices_by_name_not_position():
    cached = ["a", "b", "c"]
    mat = np.array([[1.0, 9.1, 9.2], [9.1, 1.0, 9.3], [9.2, 9.3, 1.0]])
    # 候选序与缓存序不同 → 必须按名字取位（[c, a] → 索引 [2, 0]）
    out = _matrix_from_cache(cached, mat, ["c", "a"])

    assert out.shape == (2, 2)
    assert out[0, 0] == 1.0 and out[1, 1] == 1.0
    assert out[0, 1] == 9.2  # c×a，不是 c×b


def test_matrix_from_cache_empty_candidates_slice_to_zero():
    out = _matrix_from_cache(["a", "b"], np.eye(2), [])
    assert out.shape == (0, 0)


def test_matrix_from_cache_rejects_missing_name_and_bad_shape():
    mat = np.eye(2)
    assert _matrix_from_cache(["a", "b"], mat, ["a", "z"]) is None  # 未覆盖候选列
    assert _matrix_from_cache(["a", "b"], np.eye(3), ["a", "b"]) is None  # 形状不符
