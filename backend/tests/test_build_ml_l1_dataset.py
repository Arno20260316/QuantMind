"""build_ml_l1_dataset 增量合并语义回归测试。

背景（2026-10-15）：daily_forward 按品种滚动落盘，同一交易日分区可能先
只有少量品种（实测凌晨 2 行）、晚间才补齐（117 行）。旧实现「已有分区
一律跳过」把这些先行分区永久冻结（近 90 分区仅 12 完整）。_merge_partition
必须：新行覆盖同 symbol 旧行、保留旧有其余品种、新 ⊆ 旧 时幂等。
"""

from __future__ import annotations

import pandas as pd

from backend.scripts.build_ml_l1_dataset import _merge_partition


def _df(symbols: list[str], close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"symbol": symbols, "close": close, "vol": 1.0})


def test_merge_completes_partial_partition():
    """2 行先行分区 + 117 行全量 → 完整截面（old 是 new 子集）。"""
    old = _df(["FCPO", "RSS3"], [1.0, 2.0])
    new = _df([f"S{i:03d}" for i in range(115)] + ["FCPO", "RSS3"],
              [0.0] * 115 + [1.5, 2.5])
    merged = _merge_partition(old, new)

    assert len(merged) == 117
    assert merged["symbol"].is_unique
    # 同 symbol 以新行为准
    assert float(merged.loc[merged["symbol"] == "FCPO", "close"].iloc[0]) == 1.5


def test_merge_keeps_old_symbols_not_in_new():
    """新行不含的旧品种必须保留（滚动落盘时新批可能缺隔夜品种）。"""
    old = _df(["CU", "AL"], [10.0, 20.0])
    new = _df(["CU"], [11.0])
    merged = _merge_partition(old, new)

    assert set(merged["symbol"]) == {"CU", "AL"}
    assert float(merged.loc[merged["symbol"] == "CU", "close"].iloc[0]) == 11.0
    assert float(merged.loc[merged["symbol"] == "AL", "close"].iloc[0]) == 20.0


def test_merge_idempotent_when_new_subset_of_old():
    """new ⊆ old 且值相同 → 行数不变（调用方据此跳过无谓重写）。"""
    old = _df(["CU", "AL", "ZN"], [10.0, 20.0, 30.0])
    new = _df(["CU", "AL"], [10.0, 20.0])
    merged = _merge_partition(old, new)

    assert len(merged) == len(old)


def test_merge_empty_new_returns_old_unchanged():
    old = _df(["CU"], [10.0])
    merged = _merge_partition(old, pd.DataFrame(columns=["symbol", "close", "vol"]))
    assert len(merged) == 1
