"""统计派候选因子族（移植 P2 / Tier 2）——目录契约 + qlib 表达式逐点等价对拍。

契约：

1. **目录**：28 个候选，key 唯一且 snake_case，族标签齐全，原实现 7 个候选
   全部在列（同 formulation）；``min_history`` 逐 key 金样（暖机口径改动即红）；
   表达式全目录唯一（重复表达式会静默并成一列）。
2. **pandas 实现 == qlib 表达式**（本节是重点）：同一份 **qlib 取出的** OHLCV
   面板上，``compute`` 与表达式求值逐点比对（rtol=1e-4 容忍 float32 源数据的
   舍入；结构性差异——窗口/暖机/ddof/算子选错——远超此量级必然被抓）。
   表达式是落库与回测的形态，pandas 是算 IC 的形态，两者不许各说各话。
3. **面板路径**：``compute_panel`` 按 symbol 分组、逐 symbol 结果与单算一致；
   缺列即报错（不静默出 NaN）。

qlib 数据目录可用 ``QM_TEST_QLIB_DIR`` 覆盖；目录不存在（或 qlib 未装）时
等价性测试整组 skip——目录契约部分在任何环境都必须绿。
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import candidates as cand  # noqa: E402

QLIB_DIR = os.environ.get("QM_TEST_QLIB_DIR", "/data/qlib/cn_data")
INSTRUMENTS = ("sh600000", "sz000001")
FETCH_FIELDS = ("$open", "$high", "$low", "$close", "$volume", "$amount")
FETCH_START = "2021-01-01"
FETCH_END = "2026-06-30"

#: 原实现 7 个候选（迁移时必须同式保留）
ORIGINAL_SEVEN = (
    "reversal_5d",
    "momentum_20d",
    "volume_spike_20d",
    "amount_burst_20d",
    "amihud_5d",
    "range_5d",
    "gap_drift_5d",
)

#: 暖机 bar 数金样（= 表达式最大回看 + 首日可用性；改动即契约变更）
EXPECTED_MIN_HISTORY = {
    "reversal_5d": 5,
    "reversal_10d": 10,
    "reversal_20d": 20,
    "reversal_60d": 60,
    "momentum_20d": 20,
    "momentum_60d": 60,
    "momentum_120d": 120,
    "high_prox_250d": 250,
    "max_ret_20d": 21,
    "skew_20d": 21,
    "vol_20d": 21,
    "vol_60d": 61,
    "downside_vol_20d": 21,
    "parkinson_20d": 20,
    "atr_14": 15,
    "amihud_5d": 6,
    "amihud_20d": 21,
    "volume_spike_20d": 20,
    "amount_burst_20d": 20,
    "volume_cv_20d": 20,
    "pv_corr_20d": 20,
    "pv_corr_60d": 60,
    "range_5d": 5,
    "range_20d": 20,
    "gap_drift_5d": 6,
    "overnight_ret_20d": 21,
    "intraday_ret_20d": 20,
    "close_position_20d": 20,
}


def _frame(rows: list[tuple[float, ...]]) -> pd.DataFrame:
    """(open, high, low, close, volume, amount) 元组序列 → 单 symbol 帧。"""
    frame = pd.DataFrame(rows, columns=list(cand.REQUIRED_COLUMNS))
    frame.index = pd.date_range("2024-01-01", periods=len(rows), name="trade_date")
    return frame


# ── 目录契约 ─────────────────────────────────────────────────────────


class TestCatalog:
    def test_exact_set_and_key_style(self):
        keys = [c.key for c in cand.CANDIDATES]
        assert len(keys) == 28, "候选集是显式清单，增删要同步改测试（金样纪律）"
        assert len(set(keys)) == len(keys), "key 必须唯一"
        assert set(keys) == set(EXPECTED_MIN_HISTORY)
        for key in keys:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key

    def test_original_seven_preserved(self):
        assert set(ORIGINAL_SEVEN) <= set(cand.CANDIDATE_BY_KEY)

    def test_family_labels_cover_catalog(self):
        families = {c.family for c in cand.CANDIDATES}
        assert families == set(cand.FAMILY_LABELS), "族标签与目录必须一一对上"
        for c in cand.CANDIDATES:
            assert c.name and c.formulation and c.source, c.key

    @pytest.mark.parametrize("key", sorted(EXPECTED_MIN_HISTORY))
    def test_min_history_golden(self, key):
        assert cand.CANDIDATE_BY_KEY[key].min_history == EXPECTED_MIN_HISTORY[key]

    def test_max_min_history_is_the_max(self):
        assert cand.MAX_MIN_HISTORY == max(EXPECTED_MIN_HISTORY.values())

    def test_expressions_unique_and_dollar_fielded(self):
        seen: dict[str, str] = {}
        for c in cand.CANDIDATES:
            assert "$" in c.expression, f"{c.key} 未引用行情字段"
            assert c.expression not in seen, (
                f"{c.key} 与 {seen.get(c.expression)} 表达式重复——落库列会静默合并"
            )
            seen[c.expression] = c.key


# ── 面板路径 ─────────────────────────────────────────────────────────


class TestComputePanel:
    def test_compute_panel_matches_per_symbol_compute(self):
        rows_a = [
            (10, 10.5, 9.9, 10.2, 1000, 1e4),
            (10.2, 10.6, 10.0, 10.1, 1200, 1.2e4),
            (10.1, 10.4, 9.8, 10.3, 900, 9e3),
            (10.3, 10.9, 10.2, 10.8, 1500, 1.6e4),
            (10.8, 11.0, 10.5, 10.9, 1100, 1.2e4),
            (10.9, 11.2, 10.7, 11.1, 1300, 1.4e4),
            (11.1, 11.5, 11.0, 11.4, 1700, 1.9e4),
        ]
        rows_b = [(x * 2 for x in row) for row in rows_a]  # 独立 symbol
        frame_a, frame_b = _frame(rows_a), _frame(rows_b)
        frame_b.index = frame_a.index  # type: ignore[assignment]

        panel = pd.concat(
            {"A": frame_a, "B": frame_b}, names=["symbol"]
        ).swaplevel(0, 1).sort_index()
        panel.index.names = ["trade_date", "symbol"]

        candidate = cand.CANDIDATE_BY_KEY["reversal_5d"]
        out = cand.compute_panel(panel, candidate)
        pd.testing.assert_series_equal(
            out.xs("A", level=1), candidate.compute(frame_a).rename("reversal_5d"),
            check_names=False,
        )
        pd.testing.assert_series_equal(
            out.xs("B", level=1), candidate.compute(frame_b).rename("reversal_5d"),
            check_names=False,
        )

    def test_single_symbol_panel_returns_series(self):
        """回归：单组面板曾让 groupby.apply 返回 DataFrame，rename 炸在 Index(key)。"""
        frame = _frame(
            [(10.0, 10.5, 9.5, 10.0, 100, 1e3), (10.2, 10.8, 9.9, 10.6, 120, 1.3e3)]
        )
        panel = frame.copy()
        panel["symbol"] = "sh600000"
        panel = panel.set_index("symbol", append=True).reorder_levels([0, 1])
        panel.index.names = ["trade_date", "symbol"]
        for candidate in cand.CANDIDATES:
            out = cand.compute_panel(panel, candidate)
            assert isinstance(out, pd.Series), candidate.key
            assert out.name == candidate.key
            assert out.index.equals(panel.index), candidate.key
            np.testing.assert_allclose(
                out.to_numpy(), candidate.compute(frame).to_numpy(),
                rtol=0, atol=0, equal_nan=True,
            )

    def test_non_series_compute_rejected(self, monkeypatch):
        bad = cand.Candidate(
            key="bad", name="bad", family="reversal", formulation="x",
            source="t", expression="$close", min_history=1,
            compute=lambda frame: frame[["close"]],
        )
        frame = _frame([(10.0, 10.5, 9.5, 10.0, 100, 1e3)])
        panel = frame.copy()
        panel["symbol"] = "A"
        panel = panel.set_index("symbol", append=True)
        with pytest.raises(TypeError, match="必须返回 Series"):
            cand.compute_panel(panel, bad)

    def test_missing_columns_raise(self):
        frame = _frame([(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)]).drop(columns=["amount"])
        panel = frame.copy()
        panel["symbol"] = "A"
        panel = panel.set_index("symbol", append=True)
        with pytest.raises(ValueError, match="amount"):
            cand.compute_panel(panel, cand.CANDIDATE_BY_KEY["amihud_5d"])

    def test_two_bar_frame_does_not_crash(self):
        frame = _frame([(10.0, 10.5, 9.5, 10.0, 100, 1e3), (10.0, 10.8, 9.8, 10.5, 120, 1.3e3)])
        for candidate in cand.CANDIDATES:
            values = candidate.compute(frame)
            assert len(values) == 2 and values.index.equals(frame.index), candidate.key


# ── qlib 表达式等价（集成） ──────────────────────────────────────────


@lru_cache(maxsize=1)
def _qlib_panel() -> pd.DataFrame:
    """一次取齐：双标的 × (OHLCV+amount + 全 28 表达式)，索引 (trade_date, symbol)。"""
    import qlib  # noqa: PLC0415
    import qlib.data as qd  # noqa: PLC0415
    from qlib.constant import REG_CN  # noqa: PLC0415

    qlib.init(provider_uri=QLIB_DIR, region=REG_CN)
    fields = list(FETCH_FIELDS) + [c.expression for c in cand.CANDIDATES]
    raw = qd.D.features(list(INSTRUMENTS), fields, start_time=FETCH_START, end_time=FETCH_END)
    parts = []
    for inst in INSTRUMENTS:
        part = raw.xs(inst, level=0).copy()
        part.index.name = "trade_date"
        part["symbol"] = inst
        parts.append(part)
    panel = pd.concat(parts).set_index("symbol", append=True).sort_index()
    panel.index.names = ["trade_date", "symbol"]
    return panel


@pytest.fixture(scope="module")
def qlib_panel() -> pd.DataFrame:
    if not Path(QLIB_DIR).is_dir():
        pytest.skip(f"qlib 数据目录不存在: {QLIB_DIR}（QM_TEST_QLIB_DIR 可覆盖）")
    try:
        panel = _qlib_panel()
    except ImportError as exc:  # qlib 未装
        pytest.skip(f"qlib 不可用: {exc}")
    assert not panel.empty, "qlib 取数返回空面板"
    return panel


def _renamed(panel: pd.DataFrame) -> pd.DataFrame:
    return panel[list(FETCH_FIELDS)].rename(
        columns={f"${name}": name for name in cand.REQUIRED_COLUMNS}
    )


class TestQlibExpressionEquivalence:
    """同一输入面板上，pandas 实现与 qlib 表达式必须逐点一致。

    比对区从每 symbol 的第 ``min_history`` 根 bar 起：qlib 表达式求值会自动
    向前加载扩展历史（``get_extended_window_size``），取数窗头部的暖机段
    两边本就该不同——真正要对齐的契约是「**窗口满员后逐点相等**」。引擎因此
    必须按 ``min_history`` 预取暖机，保证挖掘区间的每个日期上窗都是满的。
    """

    @pytest.mark.parametrize("key", [c.key for c in cand.CANDIDATES])
    def test_expression_matches_compute(self, qlib_panel, key):
        candidate = cand.CANDIDATE_BY_KEY[key]
        expected = qlib_panel[candidate.expression].astype(float)
        actual = cand.compute_panel(_renamed(qlib_panel), candidate).astype(float)

        assert actual.index.equals(expected.index)
        position = qlib_panel.groupby(level=1).cumcount().to_numpy()
        region = position >= candidate.min_history
        assert int(region.sum()) > 500, f"有效对比点太少（{int(region.sum())}）"
        assert (
            actual.isna().to_numpy()[region].tolist()
            == expected.isna().to_numpy()[region].tolist()
        ), f"{key}: 暖机后 NaN 位置仍分叉——窗口/算子语义不一致"
        np.testing.assert_allclose(
            actual.to_numpy()[region],
            expected.to_numpy()[region],
            rtol=1e-4,
            atol=1e-7,
            err_msg=f"{key}: pandas 实现与 qlib 表达式不等价",
        )

    def test_expression_columns_did_not_collapse(self, qlib_panel):
        """重复表达式会在 D.features 返回里并列——取数前已由唯一性测试把关。"""
        assert len(cand.CANDIDATES) == 28
        assert all(c.expression in qlib_panel.columns for c in cand.CANDIDATES)
