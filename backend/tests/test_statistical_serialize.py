"""统计派 · 结果序列化（审计产物唯一形状）——落盘契约与类型转换测试。

runner 的收尾动作就是 ``json.dumps(result_to_dict(result))`` 落
``mining_report.json``；引擎结果里混着 numpy 标量 / Timestamp / 嵌套
dataclass，不转换会在运行时炸（``np.float64`` 不是 JSON 类型）。这里钉：

1. **整棵树可序列化**：真实跑一遍引擎后的结果能直接 ``json.dumps``，
   不是抽查字段；
2. **字段完整**：未入选者的拒因在、"filter.n_kept" 这个 property 被显式
   补上（asdict 拿不到的正是过滤报告的主读数）；
3. **类型转换**：`jsonable` 对 numpy / Timestamp / array / tuple 各归各的
   JSON 原生类型。

序列化只此一处实现（runner 落盘、任务详情、API 出参共用），所以它的
契约测试也钉在**真实引擎输出**上，而不是手搭的 dataclass 假体。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import serialize as ser  # noqa: E402
from backend.tests.test_statistical_engine import (  # noqa: E402
    _cfg,
    _make_panel,
    _metadata,
    _run,
)


@pytest.fixture(scope="module")
def audit() -> dict:
    """真实跑一遍引擎（真实 28 候选目录）→ 审计字典。"""
    panel = _make_panel()
    result = _run(panel, _metadata(panel), cfg=_cfg(top_k=3))
    return ser.result_to_dict(result)


class TestEngineResultIsJsonDumpable:
    def test_whole_tree_dumps(self, audit):
        text = json.dumps(audit, ensure_ascii=False)
        assert json.loads(text)  # 往返可解析

    def test_no_numpy_or_timestamp_left(self, audit):
        """全树扫描：不许残留 numpy 类型或 Timestamp（json.dumps 的隐形地雷）。"""

        def scan(node, path="$"):
            if isinstance(node, dict):
                for k, v in node.items():
                    assert isinstance(k, str), f"{path} 的键不是 str: {k!r}"
                    scan(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    scan(v, f"{path}[{i}]")
            else:
                assert not isinstance(
                    node, (np.generic, np.ndarray, pd.Timestamp)
                ), f"{path} 残留非 JSON 类型: {type(node).__name__}"

        scan(audit)

    def test_filter_report_keeps_n_kept(self, audit):
        """n_kept 是 property，asdict 拿不到——必须被显式补进 filter（主读数）。"""
        assert isinstance(audit["filter"]["n_kept"], int)
        assert audit["filter"]["n_kept"] >= 0

    def test_every_candidate_has_verdict(self, audit):
        """每个候选：入选或带拒因，二者必居其一（无静默落榜）。"""
        assert len(audit["candidates"]) == 28
        for item in audit["candidates"]:
            has_reason = bool(item.get("reject_reason"))
            assert item["selected"] != has_reason, item["key"]

    def test_window_and_horizons_present(self, audit):
        assert set(audit["window"]) >= {"mining_start", "mining_end"}
        # _cfg 的 horizons=(1,5)：序列化后必须是 JSON 数组、保持声明顺序
        assert list(audit["horizons"]) == [1, 5]
        assert audit["algorithm"] == "statistical"
        assert audit["panel_rows"] > 0 and audit["n_symbols"] > 0
        # 标的池进审计：报告自己要能说清「这份 IC 是在哪个池子上算的」
        assert audit["universe"] == "all_a"  # _run 不传 universe → 默认池


class TestJsonablePrimitives:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (np.float64(1.5), 1.5),
            (np.int64(3), 3),
            (np.True_, True),
            (np.array([1.0, 2.0]), [1.0, 2.0]),
            (pd.Timestamp("2024-03-01"), "2024-03-01T00:00:00"),
            ((1, 2), [1, 2]),
        ],
    )
    def test_scalar_conversions(self, value, expected):
        out = ser.jsonable(value)
        assert out == expected
        assert type(out) is type(expected), f"{type(value)} → {type(out)}"


class TestJsonSafe:
    """``json_safe`` = ``jsonable`` + 非有限浮点 → null（JSON 无 NaN）。"""

    def test_nan_and_inf_become_none_at_any_depth(self):
        raw = {
            "top": float("nan"),
            "np": np.float64("inf"),
            "nested": [{"a": float("-inf"), "b": np.float64(1.5)}],
            "meta": pd.Timestamp("2024-03-01"),
        }
        out = ser.json_safe(raw)
        assert out == {
            "top": None,
            "np": None,
            "nested": [{"a": None, "b": 1.5}],
            "meta": "2024-03-01T00:00:00",
        }

    def test_output_is_strict_json(self):
        import json  # noqa: PLC0415

        text = json.dumps(ser.json_safe({"x": np.float64("nan")}), allow_nan=False)
        assert json.loads(text) == {"x": None}
