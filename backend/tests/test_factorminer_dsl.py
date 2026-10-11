"""FM-DSL 解析器（``factorminer/dsl.py``）契约测试。

论文附录 D 的 110 条表达式所用语法的**白名单制**解析：算子/字段/窗口/
数值全部在解析期校验，禁 eval（既往教训：检索式因子库把 eval() 当求值器
= RCE）。这里钉三件事：

1. 合法表达式的 AST 形状（冻结 dataclass）；
2. 一切非法形态在**解析期**被拒（未知算子/错元数/非法窗口/未知字段/尾随
   记号/非有限数值）；
3. 静态分析辅助（``min_history``/``complexity``/``walk``）——T-FM-01 求值
   暖机、T-FM-06 结构原创性闸、T-FE-05 复杂度惩罚都吃这些。
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import dsl  # noqa: E402


class TestParse:
    def test_feature_and_number(self):
        assert dsl.parse("$close") == dsl.Feature("$close")
        assert dsl.parse("1e-8") == dsl.Num(1e-8)
        assert dsl.parse("0.0001") == dsl.Num(0.0001)

    def test_call_shape_and_nesting(self):
        node = dsl.parse("Neg(Mul(CsRank($close), 2))")

        assert node == dsl.Call(
            "Neg",
            (
                dsl.Call(
                    "Mul", (dsl.Call("CsRank", (dsl.Feature("$close"),)), dsl.Num(2.0))
                ),
            ),
        )

    def test_whitespace_tolerated(self):
        assert dsl.parse("  Sub( $close ,\t$open )  ") == dsl.Call(
            "Sub", (dsl.Feature("$close"), dsl.Feature("$open"))
        )

    def test_negative_number_literal_inside_call(self):
        assert dsl.parse("Sub($close, -1)") == dsl.Call(
            "Sub", (dsl.Feature("$close"), dsl.Num(-1.0))
        )

    @pytest.mark.parametrize(
        "text,fragment",
        [
            ("Foo($close)", "未知算子"),
            ("Neg($close, 1)", "元数"),
            ("Mean($close)", "元数"),
            ("Corr($close, $volume)", "元数"),
            ("IfElse($close, 1)", "元数"),
            ("$foo", "未知字段"),
            ("Mean($close, $volume)", "字面量"),
            ("Mean($close, 1.5)", "整数"),
            ("Mean($close, 0)", "正整数"),
            ("Delay($close, -1)", "正整数"),
            ("Neg($close) extra", "多余"),
            ("Neg()", "元数"),
            ("Neg($close", "右括号"),
            ("", "空表达式"),
        ],
    )
    def test_invalid_expressions_rejected_at_parse(self, text, fragment):
        with pytest.raises(ValueError, match=fragment):
            dsl.parse(text)

    def test_overflowing_number_is_rejected(self):
        """1e999 解析成 inf——非有限数不许进 AST（inf 会污染一切统计量）。"""
        with pytest.raises(ValueError, match="有限"):
            dsl.parse("Mul($close, 1e999)")

    def test_oversized_window_rejected(self):
        """窗口字面量超上限在解析期拒（否则求值期按 n 分配缓冲 → MemoryError）。"""
        assert dsl.parse(f"Mean($close, {dsl.MAX_WINDOW})")  # 上界本身合法
        with pytest.raises(ValueError, match="超出上限"):
            dsl.parse(f"Mean($close, {dsl.MAX_WINDOW + 1})")

    def test_excessive_nesting_rejected(self):
        """千层嵌套是 RecursionError（非契约异常）；深度上限内是 ValueError。"""
        deep = "Neg(" * (dsl.MAX_DEPTH + 1) + "$close" + ")" * (dsl.MAX_DEPTH + 1)
        with pytest.raises(ValueError, match="嵌套过深"):
            dsl.parse(deep)
        exact = "Neg(" * dsl.MAX_DEPTH + "$close" + ")" * dsl.MAX_DEPTH
        assert dsl.parse(exact)  # 恰为上限仍合法

    def test_signed_power_exponent_may_be_fractional(self):
        node = dsl.parse("SignedPower($returns, 0.5)")
        assert node.args[1] == dsl.Num(0.5)

    def test_ast_is_frozen(self):
        node = dsl.parse("Neg($close)")
        with pytest.raises(FrozenInstanceError):
            node.op = "Abs"  # type: ignore[misc]


class TestRegistryMatchesPaper:
    """39 个算子 = 附录 D 全库实测清单（扫描 2026-10-11），一个不多一个不少。"""

    PAPER_ARITY = {
        "Abs": 1,
        "CsRank": 1,
        "Log": 1,
        "Neg": 1,
        "Sign": 1,
        "Add": 2,
        "And": 2,
        "Div": 2,
        "Eq": 2,
        "Greater": 2,
        "Less": 2,
        "Max2": 2,
        "Min2": 2,
        "Mul": 2,
        "Or": 2,
        "Sub": 2,
        "Delay": 2,
        "Delta": 2,
        "EMA": 2,
        "Kurt": 2,
        "Max": 2,
        "Mean": 2,
        "Med": 2,
        "Min": 2,
        "Resi": 2,
        "Rsquare": 2,
        "Skew": 2,
        "Slope": 2,
        "SMA": 2,
        "Std": 2,
        "TsArgMax": 2,
        "TsMax": 2,
        "TsMin": 2,
        "TsRank": 2,
        "WMA": 2,
        "SignedPower": 2,
        "Corr": 3,
        "Cov": 3,
        "IfElse": 3,
    }

    def test_operator_set_and_arity_exact(self):
        got = {name: spec.arity for name, spec in dsl.OPERATORS.items()}
        assert got == self.PAPER_ARITY

    def test_feature_whitelist_exact(self):
        assert dsl.FEATURES == frozenset(
            {"$open", "$high", "$low", "$close", "$volume", "$amt", "$vwap", "$returns"}
        )


class TestMinHistory:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$close", 1),
            ("$returns", 2),  # 收益率需要前一日收盘
            ("Delay($close, 5)", 6),
            ("Delta($volume, 3)", 4),
            ("Mean($close, 24)", 25),
            ("Mean(Delay($close, 5), 24)", 30),
            ("Corr($close, $volume, 12)", 13),
            ("Mean(Std($returns, 12), 48)", 62),
            ("TsArgMax(SignedPower($returns, 2), 5)", 7),
            ("Resi($close, 12)", 13),
            ("SignedPower($returns, 0.5)", 2),
        ],
    )
    def test_min_history_is_conservative(self, text, expected):
        """保守口径：宁多取一根暖机 bar，不许欠（欠暖机会让首日窗口不满、
        与「表达式在 qlib 里带扩展历史求值」不等价）。"""
        assert dsl.min_history(dsl.parse(text)) == expected


class TestComplexity:
    def test_metrics_on_known_tree(self):
        node = dsl.parse("Neg(Mul(CsRank($returns), CsRank(Std($returns, 12))))")

        cx = dsl.complexity(node)

        # 节点：5 个调用 + 2 个字段 + 1 个字面量 = 8；深度 = 根→叶最长链
        assert cx.node_count == 8
        assert cx.op_count == 5
        assert cx.depth == 5
        assert cx.literal_count == 1
        assert cx.max_window == 12

    def test_leaf_only_tree(self):
        cx = dsl.complexity(dsl.parse("$close"))
        assert (cx.node_count, cx.op_count, cx.depth, cx.max_window) == (1, 0, 1, 0)

    def test_walk_visits_every_node(self):
        node = dsl.parse("Add($close, $open)")
        assert list(dsl.walk(node)) == [
            node,
            dsl.Feature("$close"),
            dsl.Feature("$open"),
        ]
