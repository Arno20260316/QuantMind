"""``shared/factor_tags.py``：六维标签派生 + 表达式要素抽取（T-FE-07）。

口径纪律（与实现模块 docstring 同源）：
- 缺失一律 ``unknown``，不猜；日频为平台当前事实默认。
- 抽取是字符串级、无 eval、线性；只做「要素级」不冒充 AST 级结构。
"""

from __future__ import annotations

from backend.services.engine.alpha_agent.statistical.complexity import (
    expression_complexity,
)
from backend.shared.factor_tags import (
    TAG_DIMENSIONS,
    derive_tags,
    extract_expression_deps,
)


class TestDeriveTags:
    def test_full_row_yields_all_six_dimensions(self):
        # Arrange：统计派风格行（写侧已有 family/algorithm/horizon）+ 调用方传 maturity
        row = {
            "factor_code": "Ref($close,20)/$close-1",
            "market": "a_share",
            "metadata": {
                "family": "reversal",
                "algorithm": "statistical",
                "horizon": 5,
            },
        }

        # Act
        tags = derive_tags(row, maturity="观察")

        # Assert
        assert set(tags) == set(TAG_DIMENSIONS)
        assert tags["data_source"] == "statistical"
        assert tags["frequency"] == "daily"
        assert tags["family"] == "reversal"
        assert tags["transform"] == "Ref"
        assert tags["target"] == "fwd_ret_5d"
        assert tags["maturity"] == "观察"

    def test_missing_family_and_source_stay_unknown_not_guessed(self):
        row = {"factor_code": "Ref($close,5)/$close-1", "metadata": {}}

        tags = derive_tags(row)

        assert tags["family"] == "unknown"
        assert tags["data_source"] == "unknown"
        assert tags["maturity"] == "unknown"

    def test_fm_prefix_derives_factorminer_source_but_explicit_source_wins(self):
        fm_row = {"factor_code": "fm:ts_mean($close,20)-ts_mean($close,60)"}

        assert derive_tags(fm_row)["data_source"] == "factorminer"

        paper_row = {
            "factor_code": "fm:ts_mean($close,20)",
            "metadata": {"source": "paper110"},
        }
        assert derive_tags(paper_row)["data_source"] == "paper110"

    def test_frequency_defaults_daily_and_explicit_value_wins(self):
        assert derive_tags({"factor_code": "x"})["frequency"] == "daily"
        row = {"factor_code": "x", "metadata": {"frequency": "min_5"}}
        assert derive_tags(row)["frequency"] == "min_5"

    def test_target_defaults_forward_one_day(self):
        assert derive_tags({"factor_code": "x"})["target"] == "fwd_ret_1d"

    def test_family_hint_used_only_when_row_metadata_absent(self):
        row = {"factor_code": "fm:rank($turn_1)"}

        assert derive_tags(row, family_hint="momentum")["family"] == "momentum"

        row_with_meta = {
            "factor_code": "fm:rank($turn_1)",
            "metadata": {"family": "reversal"},
        }
        # 写侧权威优先于读侧推导
        assert (
            derive_tags(row_with_meta, family_hint="momentum")["family"] == "reversal"
        )

    def test_metadata_json_string_accepted(self):
        # PG 行可能是 JSONB 字符串形态（psql 直读场景）
        row = {
            "factor_code": "x",
            "metadata_json": '{"family": "volatility", "source": "rd_agent"}',
        }

        tags = derive_tags(row)

        assert tags["family"] == "volatility"
        assert tags["data_source"] == "rd_agent"


class TestExtractExpressionDeps:
    def test_qlib_infix_fields_operators_windows(self):
        deps = extract_expression_deps("Ref($close,5)/$close-1")

        assert deps["fields"] == ["close"]
        assert deps["operators"] == ["Ref"]
        assert deps["windows"] == [5]

    def test_fm_expression_multiple_operators_dedup_keeps_order(self):
        deps = extract_expression_deps(
            "ts_mean($close,20)-ts_std($close,60)+ts_mean($volume,20)"
        )

        assert deps["fields"] == ["close", "volume"]
        assert deps["operators"] == ["ts_mean", "ts_std"]
        assert deps["windows"] == [20, 60]

    def test_windows_only_from_call_arguments(self):
        # 1e-8 是裸字面量、-1 是常数，不在任何调用参数内 → 不算窗口
        deps = extract_expression_deps("($close/$close)-1+1e-8")

        assert deps["windows"] == []
        assert deps["fields"] == ["close"]
        assert deps["operators"] == []

    def test_nested_call_windows_captured(self):
        deps = extract_expression_deps("ts_rank(ts_mean($close,10),5)")

        assert deps["operators"] == ["ts_rank", "ts_mean"]
        assert deps["windows"] == [10, 5]

    def test_space_before_paren_still_a_call(self):
        deps = extract_expression_deps("ts_mean ($close, 20)")

        assert deps["operators"] == ["ts_mean"]
        assert deps["windows"] == [20]

    def test_garbage_inputs_never_raise(self):
        for bad in [None, "", "   ", "((((", "a,,,)))", "((((((((((x", "$" * 100]:
            deps = extract_expression_deps(bad)
            assert isinstance(deps["fields"], list)

    def test_unclosed_call_tolerated(self):
        deps = extract_expression_deps("ts_mean($close,20")

        assert deps["operators"] == ["ts_mean"]
        assert deps["windows"] == [20]

    def test_pure_function_same_input_same_output(self):
        expr = "Rsquare($close,20)*Resi($close,20)+IfElse($volume>0,$turn_1,0)"

        assert extract_expression_deps(expr) == extract_expression_deps(expr)

    def test_crosscheck_against_complexity_counts(self):
        # 互证（不相等，是子集关系）：算子集合是 op_count 的调用部分，
        # 窗口是全部字面量的子集——两条口径都从同一字符串记账，不应矛盾。
        for expr in [
            "Ref($close,5)/$close-1",
            "ts_mean($close,20)-ts_std($close,60)",
            "Rsquare($close,20)*Resi($close,20)",
        ]:
            deps = extract_expression_deps(expr)
            cx = expression_complexity(expr)
            assert len(deps["operators"]) <= cx.op_count
            assert len(deps["windows"]) <= cx.literal_count

    def test_fractional_window_kept_as_number(self):
        deps = extract_expression_deps("decay_linear($close,2.5)")

        assert deps["windows"] == [2.5]
