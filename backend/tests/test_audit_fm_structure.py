"""T-FM-06 结构审计脚本（``audit_fm_structure``）报告渲染契约测试。

钉一件事：交叉证据行引用的值级阈值（``cross_tfm01.reject/warn``）来自
**外部** T-FM-01 报告 JSON——旧报告可能缺键或为 null。渲染必须走
``_fmt_threshold`` 兜底为「—」，不得让 ``:g`` 对 None 抛 TypeError
把整份审计报告搞崩（T-FM-03/06 评审 LOW-4）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_AUDIT_PATH = ROOT / "scripts" / "alpha_agent" / "audit_fm_structure.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "_audit_fm_structure_under_test", _AUDIT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def audit():
    return _load_audit()


def _min_result(cross_over: dict | None = None) -> dict:
    """`_render_md` 需要的最小完整 payload（空表也走全路径渲染）。"""
    cross = {
        "report_path": "T-FM-01_x.json",
        "dropped": [],
        "warnings": [],
        "recall_grid": {},
    }
    if cross_over:
        cross.update(cross_over)
    return {
        "generated_at": "2026-10-11",
        "census": {
            "n_pairs_total": 10,
            "clones": [],
            "variant_clusters": [],
            "cosine_distribution": {
                "n_nonzero": 0,
                "p50": 0,
                "p90": 0,
                "p95": 0,
                "p99": 0,
                "max": 0,
            },
            "near_grid": {},
        },
        "cross_tfm01": cross,
        "conclusion": "结论",
    }


class TestFmtThreshold:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.5, "0.5"),
            (0.4, "0.4"),
            (0, "0"),
            (None, "—"),
            ("0.5", "—"),
        ],
    )
    def test_formats(self, audit, value, expected):
        # Arrange / Act
        out = audit._fmt_threshold(value)
        # Assert
        assert out == expected


class TestRenderMdThresholdLine:
    def test_absent_thresholds_render_dash_not_raise(self, audit):
        # Arrange：旧报告缺 reject/warn 键（None 语义）
        result = _min_result()
        # Act
        text = audit._render_md(result)
        # Assert
        assert "拒 |ρ|≥— / 警 —" in text

    def test_numeric_thresholds_rendered(self, audit):
        # Arrange
        result = _min_result(cross_over={"reject": 0.5, "warn": 0.4})
        # Act
        text = audit._render_md(result)
        # Assert
        assert "拒 |ρ|≥0.5 / 警 0.4" in text
