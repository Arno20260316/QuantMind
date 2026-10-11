"""市场状态 · 月聚合（T-FE-03 报告「分市场状态 IC」的月份标签来源）。

输入 = ``build_state_series`` 输出（生效日 → 日状态），月标签取生效日前 7 位；
月状态 = 当月生效日多数票，平票取 ``tie_break``（默认 neutral——平票时挑
「最中间」的档，不虚报牛/熊）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.shared import market_regime as mr  # noqa: E402


def _days(month: str, states: list[str]) -> dict[str, str]:
    """给某月按工作日序生成生效日→状态映射（日期字符串连续，仅月份参与聚合）。"""
    return {f"{month}-{day:02d}": state for day, state in enumerate(states, start=1)}


class TestMonthlyStates:
    def test_majority_wins(self):
        series = {**_days("2024-01", ["bull"] * 12), **_days("2024-02", ["bear"] * 9)}
        out = mr.monthly_states(series)
        assert out == {"2024-01": "bull", "2024-02": "bear"}

    def test_majority_over_minority_within_month(self):
        states = ["bull"] * 12 + ["bear"] * 10
        out = mr.monthly_states(_days("2024-01", states))
        assert out == {"2024-01": "bull"}, "12>10 → 多数票"

    def test_tie_breaks_to_neutral_by_default(self):
        states = ["bull"] * 10 + ["bear"] * 10
        out = mr.monthly_states(_days("2024-01", states))
        assert out == {"2024-01": "neutral"}, "平票不虚报牛/熊"

    def test_tie_break_override(self):
        states = ["bull"] * 10 + ["bear"] * 10
        out = mr.monthly_states(_days("2024-01", states), tie_break="bear")
        assert out == {"2024-01": "bear"}

    def test_three_way_tie_breaks_to_neutral(self):
        states = ["bull"] * 7 + ["bear"] * 7 + ["neutral"] * 7
        assert mr.monthly_states(_days("2024-01", states)) == {"2024-01": "neutral"}

    def test_empty_input(self):
        assert mr.monthly_states({}) == {}

    def test_unknown_state_raises(self):
        """自家产物带未知状态 = 口径错误，立即报错而不是静默丢弃。"""
        with pytest.raises(ValueError, match="unknown_state"):
            mr.monthly_states({"2024-01-05": "unknown_state"})

    def test_invalid_tie_break_raises(self):
        with pytest.raises(ValueError, match="tie_break"):
            mr.monthly_states({}, tie_break="sideways")
