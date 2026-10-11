"""FM-DSL 求值器（``factorminer/evaluator.py``）语义契约测试。

宽面（index=交易日升序、columns=标的）上的 pandas 求值，语义口径是本仓
唯一实现（因子报告引用它）：
- 单序列滚动算子 min_periods=1（与候选库/qlib 口径一致）；Std ddof=1；
- Corr/Cov/Slope/Resi/Rsquare/WMA 需满窗（统计量在小窗上退化，2 点回归
  残差恒 0、R² 恒 1——会给暖机段伪造信号）；
- EMA α=2/(n+1) adjust=False；WMA 权重 1..n（最新最大）；
- TsRank = 窗内末值平均秩/有效根数；TsArgMax = 距窗内最大值的根数（并列取
  最早一次出现）；CsRank = 当日截面 pct 秩；
- IfElse 条件 >0 为真（NaN→假）；比较/逻辑算子输出 1.0/0.0；Log 自然对数
  （≤0→NaN）；SignedPower = 符号×|x|^p；
- 顶层出口 inf/−inf→NaN。
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import dsl  # noqa: E402
from backend.services.engine.alpha_agent.factorminer import evaluator as ev  # noqa: E402

INDEX = pd.bdate_range("2024-01-01", periods=6)
COLUMNS = ["a", "b"]


def _frames(**cols) -> dict[str, pd.DataFrame]:
    """按列给值的宽面组（每列形状 (6, 2)，[date, symbol]）；kwarg 名即字段名
    （``close=`` → 键 ``$close``）。"""
    return {
        f"${name}": pd.DataFrame(
            np.asarray(v, dtype=float), index=INDEX, columns=COLUMNS
        )
        for name, v in cols.items()
    }


def _ev(expr: str, **cols) -> pd.DataFrame:
    result = ev.evaluate(dsl.parse(expr), _frames(**cols))
    assert isinstance(result, pd.DataFrame)
    return result


_LIN_A = [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50], [6, 60]]  # 线性：斜率 1 / 10


class TestElementwise:
    def test_arithmetic_and_scalar_mixing(self):
        got = _ev("Sub($close, 1)", close=_LIN_A)
        assert got.iloc[0].tolist() == [0.0, 9.0]

        got = _ev("Div(Mul($close, 2), $open)", close=_LIN_A, open=_LIN_A)
        assert got.iloc[3].tolist() == [2.0, 2.0]

        got = _ev("Sub(1, $close)", close=_LIN_A)  # 标量在左
        assert got.iloc[0].tolist() == [0.0, -9.0]

    def test_comparisons_yield_floats(self):
        got = _ev("Greater($close, 3)", close=_LIN_A)
        assert got.iloc[2].tolist() == [0.0, 1.0]

        got = _ev("Less($close, 3)", close=_LIN_A)
        assert got.iloc[1].tolist() == [1.0, 0.0]
        assert got.iloc[2].tolist() == [0.0, 0.0]  # 3 < 3 为假

        got = _ev("Eq($close, $open)", close=_LIN_A, open=_LIN_A)
        assert (got == 1.0).all().all()

    def test_logic_ops_use_positive_as_true(self):
        got = _ev(
            "And(Greater($close, 1), Greater($open, 15))", close=_LIN_A, open=_LIN_A
        )
        assert got.iloc[0].tolist() == [0.0, 0.0]  # close=1 不 >1
        assert got.iloc[1].tolist() == [0.0, 1.0]  # 列 a 的 open=2 不 >15

        got = _ev(
            "Or(Greater($close, 5), Greater($open, 55))", close=_LIN_A, open=_LIN_A
        )
        assert got.iloc[4].tolist() == [0.0, 1.0]  # 列 b 的 close=50 >5
        assert got.iloc[5].tolist() == [1.0, 1.0]

    def test_min2_max2_elementwise(self):
        close = [[5, 1], [2, 2], [3, 3], [4, 4], [5, 5], [6, 6]]
        open_ = [[1, 9], [2, 2], [3, 3], [4, 4], [5, 5], [6, 6]]
        got = _ev("Min2($close, $open)", close=close, open=open_)
        assert got.iloc[0].tolist() == [1.0, 1.0]
        got = _ev("Max2($close, $open)", close=close, open=open_)
        assert got.iloc[0].tolist() == [5.0, 9.0]


class TestUnary:
    def test_neg_abs_sign(self):
        got = _ev("Neg($close)", close=_LIN_A)
        assert got.iloc[0].tolist() == [-1.0, -10.0]
        got = _ev("Abs(Neg($close))", close=_LIN_A)
        assert got.iloc[0].tolist() == [1.0, 10.0]
        got = _ev("Sign(Sub($close, 3))", close=_LIN_A)
        assert got.iloc[1].tolist() == [-1.0, 1.0]
        assert got.iloc[2].tolist() == [0.0, 1.0]

    def test_log_natural_and_nonpositive_nan(self):
        close = [
            [0.0, 1.0],
            [np.e, np.e],
            [2.0, 8.0],
            [3.0, 3.0],
            [4.0, 4.0],
            [5.0, 5.0],
        ]
        got = _ev("Log($close)", close=close)
        assert np.isnan(got.iloc[0, 0])
        assert got.iloc[0, 1] == pytest.approx(0.0)
        assert got.iloc[1, 0] == pytest.approx(1.0)
        assert got.iloc[2, 1] == pytest.approx(np.log(8.0))

    def test_signed_power(self):
        v = [[-2.0, 4.0], [3.0, -1.0], [1.0, 1.0], [2.0, 2.0], [1.0, 1.0], [1.0, 1.0]]
        got = _ev("SignedPower($close, 2)", close=v)
        assert got.iloc[0, 0] == pytest.approx(-4.0)  # 负号的立方次方：sign·|x|^2
        assert got.iloc[1, 1] == pytest.approx(-1.0)
        got = _ev("SignedPower($close, 0.5)", close=v)
        assert got.iloc[0, 1] == pytest.approx(2.0)

    def test_csrank_is_daily_pct_rank(self):
        close = [[1, 10], [1, 10], [2, 1], [3, 1], [4, 1], [5, 1]]
        got = _ev("CsRank($close)", close=close)
        assert got.iloc[0].tolist() == [0.5, 1.0]
        assert got.iloc[3].tolist() == [1.0, 0.5]

    def test_csrank_skips_nan_cells(self):
        close = [[1, np.nan], [2, 2], [3, 3], [4, 4], [5, 5], [6, 6]]
        got = _ev("CsRank($close)", close=close)
        assert got.iloc[0, 0] == pytest.approx(1.0)  # 当日只有 1 个有效标的


class TestWindows:
    def test_rolling_mean_min_periods_one(self):
        got = _ev("Mean($close, 3)", close=_LIN_A)
        assert got.iloc[0].tolist() == [1.0, 10.0]  # 窗口未满 → 用什么算什么
        assert got.iloc[1].tolist() == [1.5, 15.0]
        assert got.iloc[3].tolist() == [3.0, 30.0]

    def test_rolling_std_ddof_one(self):
        got = _ev("Std($close, 2)", close=_LIN_A)
        assert np.isnan(got.iloc[0]).all()  # 单点样本方差无定义（ddof=1）
        assert got.iloc[1, 0] == pytest.approx(np.sqrt(0.5))

    def test_rolling_skew_kurt_med_match_pandas(self):
        v = [[1, 6], [2, 5], [3, 4], [4, 1], [9, 2], [1, 3]]
        frame = pd.DataFrame(np.asarray(v, float), index=INDEX, columns=COLUMNS)
        got = _ev("Skew($close, 3)", close=v)
        assert got.iloc[3, 0] == pytest.approx(
            frame["a"].rolling(3, min_periods=3).skew().iloc[3]
        )
        got = _ev("Kurt($close, 4)", close=v)
        assert got.iloc[3, 1] == pytest.approx(
            frame["b"].rolling(4, min_periods=4).kurt().iloc[3]
        )
        got = _ev("Med($close, 3)", close=v)
        assert got.iloc[2, 0] == pytest.approx(2.0)

    def test_rolling_min_max(self):
        v = [[3, 1], [1, 5], [2, 2], [4, 4], [5, 5], [6, 6]]
        got = _ev("Min($close, 2)", close=v)
        assert got.iloc[1].tolist() == [1.0, 1.0]
        got = _ev("TsMax($close, 3)", close=v)
        assert got.iloc[2].tolist() == [3.0, 5.0]

    def test_tsrank_percentile_of_last_value(self):
        v = [[1, 1], [3, 2], [2, 3], [4, 4], [5, 5], [6, 6]]
        got = _ev("TsRank($close, 3)", close=v)
        assert got.iloc[0, 0] == pytest.approx(1.0)
        assert got.iloc[1, 0] == pytest.approx(1.0)
        assert got.iloc[2, 0] == pytest.approx(2 / 3)  # [1,3,2] 中 2 的平均秩 2/3

    def test_tsargmax_bars_since_max_earliest_tie(self):
        v = [[1, 0], [3, 0], [2, 0], [3, 0], [1, 0], [1, 0]]
        got = _ev("TsArgMax($close, 3)", close=v)
        assert np.isnan(got.iloc[0, 0]) or got.iloc[0, 0] == 0.0
        assert got.iloc[1, 0] == pytest.approx(0.0)
        assert got.iloc[2, 0] == pytest.approx(1.0)
        # 并列 3（row1 与 row3）取最早一次出现 → 距最大 2 根
        assert got.iloc[3, 0] == pytest.approx(2.0)

    def test_ema_alpha_two_over_n_plus_one(self):
        got = _ev("EMA($close, 2)", close=_LIN_A)
        alpha = 2 / 3
        assert got.iloc[0, 0] == pytest.approx(1.0)
        expect = alpha * 2 + (1 - alpha) * 1.0
        assert got.iloc[1, 0] == pytest.approx(expect)
        expect = alpha * 3 + (1 - alpha) * expect
        assert got.iloc[2, 0] == pytest.approx(expect)

    def test_sma_is_simple_rolling_mean(self):
        got = _ev("SMA($close, 3)", close=_LIN_A)
        mean = _ev("Mean($close, 3)", close=_LIN_A)
        pd.testing.assert_frame_equal(got, mean)

    def test_wma_weights_one_to_n_full_window_only(self):
        got = _ev("WMA($close, 3)", close=_LIN_A)
        assert np.isnan(got.iloc[1]).all()  # 满窗才给值
        assert got.iloc[2, 0] == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)
        assert got.iloc[2, 1] == pytest.approx((1 * 10 + 2 * 20 + 3 * 30) / 6)

    def test_corr_cov_full_window(self):
        close = [[1, 3], [2, 2], [3, 1], [4, 1], [5, 2], [6, 3]]
        mirror = [[6, 1], [5, 2], [4, 3], [3, 3], [2, 2], [1, 1]]
        got = _ev("Corr($close, $open, 3)", close=close, open=mirror)
        assert np.isnan(got.iloc[1, 0])  # 满窗才给值
        assert got.iloc[2, 0] == pytest.approx(-1.0)  # [1,2,3] vs [6,5,4]
        assert got.iloc[5, 0] == pytest.approx(-1.0)  # [4,5,6] vs [3,2,1]
        got = _ev("Cov($close, $open, 3)", close=close, open=close)
        assert np.isnan(got.iloc[1, 0])
        assert got.iloc[2, 0] == pytest.approx(1.0)  # var([1,2,3], ddof=1)

    def test_slope_resi_rsquare_on_line(self):
        got = _ev("Slope($close, 3)", close=_LIN_A)
        assert np.isnan(got.iloc[1]).all()
        assert got.iloc[5].tolist() == pytest.approx([1.0, 10.0])
        got = _ev("Resi($close, 3)", close=_LIN_A)
        assert got.iloc[5].tolist() == pytest.approx([0.0, 0.0])
        got = _ev("Rsquare($close, 3)", close=_LIN_A)
        assert got.iloc[5].tolist() == pytest.approx([1.0, 1.0])

    def test_slope_matches_polyfit_on_nonlinear(self):
        v = [[1, 5], [3, 1], [2, 4], [6, 2], [4, 9], [5, 3]]
        got = _ev("Slope($close, 3)", close=v)
        expect = np.polyfit(np.arange(3), [1.0, 3.0, 2.0], 1)[0]
        assert got.iloc[2, 0] == pytest.approx(expect)

    def test_resi_is_last_point_residual(self):
        v = [[1, 0], [3, 0], [2, 0], [4, 0], [5, 0], [6, 0]]
        got = _ev("Resi($close, 3)", close=v)
        beta, alpha = np.polyfit(np.arange(3), [1.0, 3.0, 2.0], 1)
        assert got.iloc[2, 0] == pytest.approx(2.0 - (alpha + beta * 2))

    def test_delay_and_delta(self):
        got = _ev("Delay($close, 2)", close=_LIN_A)
        assert np.isnan(got.iloc[1]).all()
        assert got.iloc[2].tolist() == [1.0, 10.0]
        got = _ev("Delta($close, 2)", close=_LIN_A)
        assert got.iloc[2].tolist() == pytest.approx([2.0, 20.0])


class TestConditional:
    def test_ifelse_selects_by_condition(self):
        got = _ev("IfElse(Greater($close, 3), 1, -1)", close=_LIN_A)
        assert got.iloc[1].tolist() == [-1.0, 1.0]  # 列 b 的 close=20 >3
        assert got.iloc[4].tolist() == [1.0, 1.0]

    def test_nan_condition_takes_else_branch(self):
        close = [[np.nan, 10], [2, 20], [3, 30], [4, 40], [5, 50], [6, 60]]
        got = _ev("IfElse(Greater($close, 3), 1, -1)", close=close)
        assert got.iloc[0].tolist() == [-1.0, 1.0]  # NaN 与 10 各走 else/if

    def test_ifelse_branches_may_be_frames(self):
        got = _ev(
            "IfElse(Greater($close, 3), $open, Neg($open))", close=_LIN_A, open=_LIN_A
        )
        assert got.iloc[0].tolist() == [-1.0, 10.0]
        assert got.iloc[5].tolist() == [6.0, 60.0]


class TestGuards:
    def test_missing_feature_frame_raises_keyerror(self):
        with pytest.raises(KeyError, match=r"\$vwap"):
            ev.evaluate(dsl.parse("$vwap"), _frames(close=_LIN_A))

    def test_mismatched_frames_rejected(self):
        frames = _frames(close=_LIN_A)
        frames["$open"] = pd.DataFrame(
            np.zeros((5, 2)), index=INDEX[:5], columns=COLUMNS
        )
        with pytest.raises(ValueError, match="同形"):
            ev.evaluate(dsl.parse("Sub($close, $open)"), frames)

    def test_series_ops_require_frames(self):
        with pytest.raises(ValueError, match="宽面"):
            ev.evaluate(dsl.parse("CsRank(1)"), _frames(close=_LIN_A))
        with pytest.raises(ValueError, match="宽面"):
            ev.evaluate(dsl.parse("Mean(1, 5)"), _frames(close=_LIN_A))

    def test_top_level_infs_become_nan(self):
        """除零的 inf 若留在截面里会排到第一名——顶层出口一律清成 NaN。"""
        close = [[1, 10], [2, 20], [3, 30], [4, 40], [5, 50], [6, 60]]
        got = _ev("Div(1, Sub($close, 1))", close=close)
        assert np.isnan(got.iloc[0, 0])  # 1/0 → inf → NaN
        assert got.iloc[1, 0] == pytest.approx(1.0)

    def test_every_registry_operator_is_implemented(self):
        missing = ev.missing_implementations()
        assert missing == [], f"注册了但没有求值实现的算子：{missing}"

    def test_tsargmax_all_nan_window_warns_nothing(self):
        """全 NaN 窗的 nanmax 走 warnings 模块（np.errstate 管不住）——runs 里
        暖机段每格一条 RuntimeWarning；这里把警告升级成错误，验证已显式静音。"""
        close = [
            [np.nan, 1.0],
            [np.nan, 2.0],
            [np.nan, 3.0],
            [1.0, 4.0],
            [2.0, 5.0],
            [3.0, 6.0],
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            got = _ev("TsArgMax($close, 3)", close=close)
        assert np.isnan(got.iloc[2, 0])  # 全 NaN 窗 → NaN
        assert got.iloc[3, 0] == pytest.approx(0.0)  # 首根有效值即窗内最大
