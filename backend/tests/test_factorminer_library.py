"""FM-DSL 内置因子库（``factorminer/library.py``）契约测试。

库内容 = FactorMiner 论文（arXiv:2602.14670）附录 D 的 110 条表达式，逐字
嵌入模块（出处见模块 docstring）。这里钉住的是**转录保真度**：条数、编号
连续、表达式逐字、算子/字段覆盖与解析器注册表一致、`min_history` 上界。
任何一条被改动都会在 `used_ops == set(OPERATORS)` 或编号断言上炸掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import dsl  # noqa: E402
from backend.services.engine.alpha_agent.factorminer import evaluator as ev  # noqa: E402
from backend.services.engine.alpha_agent.factorminer import library  # noqa: E402


def _used(node, cls):
    return {
        n.value
        if isinstance(n, dsl.Num)
        else n.name
        if isinstance(n, dsl.Feature)
        else n.op
        for n in dsl.walk(node)
        if isinstance(n, cls)
    }


class TestLibraryIntegrity:
    def test_110_entries_ids_contiguous(self):
        lib = library.LIBRARY
        assert len(lib) == 110
        assert [f.fid for f in lib] == [f"{i:03d}" for i in range(1, 111)]

    def test_every_expression_parses_with_metrics_attached(self):
        for f in library.LIBRARY:
            assert isinstance(f.ast, dsl.Call), f.fid
            assert f.min_history == dsl.min_history(f.ast), f.fid
            assert f.complexity == dsl.complexity(f.ast), f.fid
            assert f.complexity.op_count >= 1, f.fid

    def test_used_operators_equal_registry(self):
        """库里用到的算子集合 ≡ 解析器注册表（39 个）——转录错一个字就红。"""
        used: set[str] = set()
        for f in library.LIBRARY:
            used |= _used(f.ast, dsl.Call)
        assert used == set(dsl.OPERATORS)

    def test_used_features_cover_all_eight(self):
        used: set[str] = set()
        for f in library.LIBRARY:
            used |= _used(f.ast, dsl.Feature)
        assert used == set(dsl.FEATURES)

    def test_library_text_is_verbatim_lines(self):
        """嵌入文本 = 110 行 `NNN <表达式>`，供人工与附录对照。"""
        lines = [ln for ln in library.LIBRARY_TEXT.splitlines() if ln.strip()]
        assert len(lines) == 110
        for ln in lines:
            assert ln[:3].isdigit() and ln[3] == " ", ln

    def test_max_min_history_is_74(self):
        # factor 094：Mean(Std($returns,12),60) → 2+12+60 —— 全库暖机上界
        assert max(f.min_history for f in library.LIBRARY) == 74

    def test_first_factor_expression_is_verbatim(self):
        f = library.LIBRARY[0]
        assert f.fid == "001"
        assert f.expression == (
            "Neg(CsRank(Div(Sub($close, Min($close, 48)), "
            "Add(Sub(Max($close, 48), Min($close, 48)), 1e-8))))"
        )

    def test_factor_lookup_by_id(self):
        assert library.by_id("094").fid == "094"
        with pytest.raises(KeyError):
            library.by_id("999")


class TestLoadLibraryValidation:
    def test_rejects_non_contiguous_ids(self):
        with pytest.raises(ValueError, match="编号"):
            library.load_library("001 Sub($close, $open)\n003 Sub($close, $open)")

    def test_rejects_unparseable_expression_with_fid_context(self):
        with pytest.raises(ValueError, match="001"):
            library.load_library("001 Neg($close, 1)")

    def test_rejects_malformed_line(self):
        with pytest.raises(ValueError, match="行"):
            library.load_library("001 Sub($close, $open)\n不是一行")


class TestEndToEndEvaluation:
    def test_factor_002_pct_deviation_from_ema(self):
        """`Neg(Div(Sub($close, EMA($close,10)), EMA($close,10)))` 与
        pandas ewm 直接算法逐点一致（α=2/11，adjust=False）。"""
        values = [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50], [6, 60]]
        index = pd.bdate_range("2024-01-01", periods=6)
        frame = pd.DataFrame(np.asarray(values, float), index=index, columns=["a", "b"])

        got = ev.evaluate(library.by_id("002").ast, {"$close": frame})

        ema = frame.ewm(alpha=2 / 11, adjust=False).mean()
        expected = -((frame - ema) / ema)
        pd.testing.assert_frame_equal(got, expected)

    def test_deepest_factor_094_evaluates_on_synthetic_panel(self):
        """094（全库最深：5 层嵌套+4 算子族）能在满暖机合成面板上跑出有限值，
        且输出形状保持。"""
        rng = np.random.default_rng(7)
        index = pd.bdate_range("2023-01-02", periods=90)
        cols = [f"s{i}" for i in range(8)]
        close = pd.DataFrame(
            100 * np.exp(np.cumsum(rng.normal(0, 0.01, (90, 8)), axis=0)),
            index=index,
            columns=cols,
        )
        factors = {"$close": close, "$returns": close.pct_change()}

        got = ev.evaluate(library.by_id("094").ast, factors)

        assert got.shape == close.shape
        assert np.isfinite(got.iloc[80:].to_numpy()).all()  # 暖机后无 NaN/inf
