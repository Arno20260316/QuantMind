"""挖掘任务去重指纹（移植 P3）——字段表与确定性的契约测试。

指纹是**唯一的去重判据**：它多算一个字段，用户改参数重跑就再也命中不了；
少算一个字段，两个本该不同的实验被并成一个。所以这里钉三件事：

1. **确定性**：同参数恒同指纹（含键序无关、None 与空串等价）；
2. **字段表锁定**：进指纹的字段是**显式清单**——加字段必须是自觉行为
   （``test_field_table_is_explicit`` 会在新增字段时红，提醒同步意图）；
3. **该进的一个不少**：方向/窗口/数据集/算法/频率任一变化必须产生新指纹；
   数据集是**集合**（勾选顺序无关），方向文本除空白外不做归一。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.task_fingerprint import (  # noqa: E402
    canonical_params,
    fingerprint_of,
)

BASE: dict = {
    "algorithm": "rdagent",
    "market": "a_share",
    "universe": "csi300",
    "data_source": "parquet",
    "direction": "动量 × 波动率",
    "direction_mode": "selected",
    "quality_gate_mode": "soft",
    "loop_n": 5,
    "freq": "1min",
    "mining_start": "2024-01-02",
    "mining_end": "2025-06-30",
    "valid_start": None,
    "valid_end": None,
    "datasets": ("features_daily", "l1_factors"),
    "source": "text",
    "doc_id": None,
}


def _fp(**overrides) -> str:
    return fingerprint_of(canonical_params(**{**BASE, **overrides}))


class TestDeterminism:
    def test_same_params_same_fingerprint(self):
        assert _fp() == _fp()

    def test_fingerprint_is_content_hash_not_key_order(self):
        """键序不参与：指纹是内容哈希，不是序列化格式的哈希。"""
        canonical = canonical_params(**BASE)
        shuffled = dict(reversed(list(canonical.items())))
        assert fingerprint_of(shuffled) == fingerprint_of(canonical)

    def test_none_and_blank_are_the_same_thing(self):
        assert _fp(valid_start=None) == _fp(valid_start="")
        assert _fp(doc_id=None) == _fp(doc_id="   ")

    def test_dataset_order_and_duplicates_do_not_matter(self):
        """数据集是集合：勾选顺序/重复勾选不产生「新任务」。"""
        assert _fp(datasets=("l1_factors", "features_daily")) == _fp()
        assert _fp(datasets=("features_daily", "l1_factors", "features_daily")) == _fp()

    def test_direction_whitespace_normalized_but_not_semantics(self):
        assert _fp(direction="  动量 × 波动率 ") == _fp()
        assert _fp(direction="动量×波动率") != _fp(), "换一字就是另一个研究假设"

    def test_canonical_is_strict_json(self):
        json.dumps(canonical_params(**BASE), ensure_ascii=False, allow_nan=False)


class TestFieldTableIsExplicit:
    """进指纹的字段 = 这张表。新增/删除字段时本用例会红——那是**故意的**。"""

    def test_field_table_is_explicit(self):
        assert set(canonical_params(**BASE)) == {
            "algorithm",
            "market",
            "universe",
            "data_source",
            "direction",
            "direction_mode",
            "quality_gate_mode",
            "loop_n",
            "freq",
            "mining_start",
            "mining_end",
            "valid_start",
            "valid_end",
            "datasets",
            "source",
            "doc_id",
        }

    @pytest.mark.parametrize(
        "field,value",
        [
            ("algorithm", "statistical"),
            ("market", "hong_kong"),
            ("universe", "csi500"),
            ("data_source", "pg"),
            ("direction", "反转"),
            ("direction_mode", "random"),
            ("quality_gate_mode", "hard"),
            ("loop_n", 6),
            ("freq", "day"),
            ("mining_start", "2024-02-02"),
            ("mining_end", "2025-07-31"),
            ("valid_start", "2025-08-01"),
            ("valid_end", "2026-01-31"),
            ("datasets", ("features_daily",)),
            ("source", "doc"),
            ("doc_id", "doc-1"),
        ],
    )
    def test_every_field_changes_fingerprint(self, field, value):
        """每个字段都真的参与哈希——漏算字段 = 两种实验被并成一个。"""
        assert _fp(**{field: value}) != _fp(), f"{field} 没有参与指纹"

    def test_loop_n_is_normalized_to_int(self):
        assert canonical_params(**{**BASE, "loop_n": 5})["loop_n"] == 5
        assert fingerprint_of(canonical_params(**{**BASE, "loop_n": 5})) == _fp()
