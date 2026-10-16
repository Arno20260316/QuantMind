"""fork 补丁 A7：输出粒度评估器按挖掘频率分派（FORK_DIVERGENCE.md · A7）。

背景（2026-10-16 实证）：评估器硬编码「1min 间隔 = 一定错」，而分钟挖掘的
提示词要求分钟输出——两个裁判互补振荡 → evo 轮耗尽、零产出。修复 =
``FactorDatetimeFreqEvaluator`` 按 ``QM_MINING_BAR_SECONDS`` 分派：

- 缺省/0（日频任务）：与上游**逐字一致**（回归不变量 A）；
- >0（分钟任务）：要求输出时间戳含该间隔；纯日频输出判错并给出重算指引。

依赖 rd-agent 导入链（dill/LLM 栈）——容器内真跑：
``docker exec -w /app quantmind python -m pytest backend/tests/test_factor_eval_freq_dispatch.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_RD_AGENT = Path(__file__).resolve().parents[2] / "rd-agent"
if str(_RD_AGENT) not in sys.path:
    sys.path.insert(0, str(_RD_AGENT))

pytest.importorskip("dill", reason="rd-agent 依赖未就绪（容器内运行真断言）")
_eva = pytest.importorskip("rdagent.components.coder.factor_coder.eva_utils")

_FreqEvaluator = _eva.FactorDatetimeFreqEvaluator


class _FakeWorkspace:
    """评估器只经 ``execute()`` 取 DataFrame——给个最小替身。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def execute(self):
        return None, self._frame


def _minute_frame() -> pd.DataFrame:
    idx = pd.MultiIndex.from_product(
        [
            pd.date_range("2026-01-05 09:31", periods=3, freq="1min"),
            ["sh600036"],
        ],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"factor": [1.0, 2.0, 3.0]}, index=idx)


def _daily_frame() -> pd.DataFrame:
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-05", periods=3, freq="1D"), ["sh600036"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"factor": [1.0, 2.0, 3.0]}, index=idx)


@pytest.mark.unit
def test_minute_mode_passes_on_minute_output(monkeypatch):
    monkeypatch.setenv("QM_MINING_BAR_SECONDS", "60")
    text, ok = _FreqEvaluator().evaluate(_FakeWorkspace(_minute_frame()), None)
    assert ok is True
    assert "60s" in text


@pytest.mark.unit
def test_minute_mode_fails_on_daily_output(monkeypatch):
    monkeypatch.setenv("QM_MINING_BAR_SECONDS", "60")
    text, ok = _FreqEvaluator().evaluate(_FakeWorkspace(_daily_frame()), None)
    assert ok is False
    assert "intraday" in text


@pytest.mark.unit
def test_day_mode_upstream_behavior_byte_identical(monkeypatch):
    """缺省日频：1min 输出判「not daily…definitely wrong」，日频输出通过。"""
    monkeypatch.delenv("QM_MINING_BAR_SECONDS", raising=False)
    text, ok = _FreqEvaluator().evaluate(_FakeWorkspace(_minute_frame()), None)
    assert ok is False
    assert "not daily" in text
    assert "definitely wrong" in text

    text, ok = _FreqEvaluator().evaluate(_FakeWorkspace(_daily_frame()), None)
    assert ok is True
    assert text == "The generated dataframe is daily."


@pytest.mark.unit
def test_bar_seconds_env_default_invalid_and_negative(monkeypatch):
    monkeypatch.delenv("QM_MINING_BAR_SECONDS", raising=False)
    assert _eva._expected_bar_seconds() == 0
    for bad in ("abc", "", "-5"):
        monkeypatch.setenv("QM_MINING_BAR_SECONDS", bad)
        assert _eva._expected_bar_seconds() == 0
    monkeypatch.setenv("QM_MINING_BAR_SECONDS", "300")
    assert _eva._expected_bar_seconds() == 300


@pytest.mark.unit
def test_alias_keeps_upstream_class_name():
    assert _eva.FactorDatetimeDailyEvaluator is _FreqEvaluator
