"""统计派 · 表达式复杂度与简单性因子（T-FE-05）契约测试。

输入是**qlib 中缀表达式字符串**（28 候选的 ``expression`` 形态）。
FM-DSL（``factorminer.dsl``，附录 D 纯函数形式语法）不覆盖中缀算术，
故此处字符串级计数是统计派候选的**范围受限唯一口径**：纯函数、无 eval、
不依赖求值器。简单性因子进入 ``selection_score``（同分取简由排序兜底）。

单调性契约（评审要钉的行为）：
- 有参数空间（literal ≥ 1）时，算子数 ↑ → 因子严格 ↓；
- 有算子（op ≥ 1）时，字面量数 ↑ → 因子严格 ↓；
- 零复杂度（$close 这类叶子）→ 恰为 1.0；无参表达式不缴参数税
  （结构简单性由「同分取简」排序条款兜底，见 engine._rank_eligible）。
"""

from __future__ import annotations

import itertools
import math

import pytest

from backend.services.engine.alpha_agent.statistical import complexity as cplx


class TestExpressionComplexity:
    def test_lone_feature_has_zero_complexity(self):
        cx = cplx.expression_complexity("$close")
        assert (cx.op_count, cx.literal_count) == (0, 0)

    @pytest.mark.parametrize(
        ("expr", "ops", "lits"),
        [
            # 反转形态：1 调用 + 2 中缀；字面量 5 与 -1 的 1
            ("Ref($close,5)/$close-1", 3, 2),
            # 纯函数形式（FM 风格表达式恰可解析时两口径一致的基础）
            ("Corr($close,$volume,20)", 1, 1),
            # 中缀嵌套：Max/Ref 2 调用 + / 与 - 2 中缀；字面量 1,1,20
            ("Max($close/Ref($close,1)-1,20)", 4, 3),
            # 科学计数法：1e-8 的负号不算算子，但算字面量
            (
                "Mean(Abs($close/Ref($close,1)-1)/($amount+1e-8),5)",
                7,
                4,
            ),
            # 纯中缀、零字面量（tier3 的 LATE 夹具形态）
            ("($close-$low)/($high-$low)", 3, 0),
            # 浮点字面量与比较符
            (
                "Power(Mean(Power(Log($high/$low),2),20)/2.772588722239781,0.5)",
                6,
                4,
            ),
            ("If($close<Ref($close,1),$close/Ref($close,1)-1,0)", 6, 4),
            # 标识符内数字不算参数（评审 LOW#2 钉住）：turn_1/turn_20 是字段名
            # 一部分，唯 -1 的 1 是真字面量
            ("$turn_1/$turn_20-1", 2, 1),
            # 同理 Ref20( 是 1 次调用、0 个字面量（数字前缀是词字符）
            ("Ref20($close)", 1, 0),
        ],
    )
    def test_counts_ops_and_literals(self, expr, ops, lits):
        cx = cplx.expression_complexity(expr)
        assert cx.op_count == ops
        assert cx.literal_count == lits

    def test_pure_and_idempotent(self):
        expr = "Ref($close,5)/$close-1"
        assert cplx.expression_complexity(expr) == cplx.expression_complexity(expr)

    def test_all_real_candidates_counted(self):
        """28 个真实候选的表达式全部可计数且 op ≥ 1——计数退化为 0 说明
        口径漏掉了真实语法形态（假全绿防线）。"""
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            candidates as cand,
        )

        for c in cand.CANDIDATES:
            cx = cplx.expression_complexity(c.expression)
            assert cx.op_count >= 1, f"{c.key}: {c.expression!r} 计数为 0"
            assert cx.literal_count >= 0


class TestSimplicityFactor:
    def test_formula_is_pinned(self):
        """公式= 1/(1 + op × log1p(lit))——金样钉住，防重构漂移。"""
        assert cplx.simplicity_factor(3, 2) == pytest.approx(
            1.0 / (1.0 + 3.0 * math.log1p(2))
        )

    def test_zero_complexity_is_exactly_one(self):
        assert cplx.simplicity_factor(0, 0) == 1.0

    def test_strictly_decreasing_in_ops_when_params_exist(self):
        factor = cplx.simplicity_factor
        values = [factor(op, 2) for op in (1, 2, 3, 5)]
        assert all(a > b for a, b in itertools.pairwise(values)), values

    def test_strictly_decreasing_in_literals_when_ops_exist(self):
        factor = cplx.simplicity_factor
        values = [factor(2, lit) for lit in (1, 2, 5, 20)]
        assert all(a > b for a, b in itertools.pairwise(values)), values

    def test_no_params_no_param_tax(self):
        """literal=0 → 表达式没有参数空间可过拟合，不缴参数税（因子=1）；
        其结构复杂度由同分取简排序兜底。"""
        assert cplx.simplicity_factor(5, 0) == 1.0

    def test_bounded_in_unit_interval(self):
        for op in (0, 1, 3, 10):
            for lit in (0, 1, 7, 100):
                f = cplx.simplicity_factor(op, lit)
                assert 0.0 < f <= 1.0, (op, lit, f)
