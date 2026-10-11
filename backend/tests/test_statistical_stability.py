"""统计派 · 子窗稳定性 / 滚动再选择 / IC 半衰期（Tier 3 / T-S3-01/02）。

纯函数层测试，不碰引擎与 qlib。钉住的契约：

1. **半衰期**：``|RankIC(h)| = a·exp(−λh)`` 上 ``half_life = ln2/λ`` 精确可复；
   异号周期点不参与拟合（混号的对数衰减没有意义）；λ ≤ 0 = 「不衰减」，
   半衰期如实 NaN，不硬凑一个大数；
2. **折划分**：位置等分（不是自然日），单调不降；日期数少于折数时也不越界；
3. **再选择**：并集日历分折 → 展开窗检查点重排 Top-K 的命中率；折内有效日
   不足 → ``eligible=False`` 且 note 说明（**不拒**，没证据不冒充结论）；
   无有效 IC 日的候选如实记 note；并列按 key 定序（判决可复现）。
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

from backend.services.engine.alpha_agent.statistical import stability as st  # noqa: E402


def _series(values, start: str = "2024-01-01") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


class TestHalfLife:
    def test_exponential_decay_recovered_exactly(self):
        """IC(h) = 0.1·2^(−h/5)：对数线性拟合应精确还原 half_life=5。"""
        horizons = (1, 2, 4, 8, 16)
        ics = [0.1 * 2 ** (-h / 5) for h in horizons]

        half_life, decay = st.ic_half_life(horizons, ics)

        assert half_life == pytest.approx(5.0, rel=1e-9)
        assert decay == pytest.approx(np.log(2) / 5, rel=1e-9)

    def test_sign_mixed_points_excluded(self):
        """|IC| 最大的周期是负号时，同号点只剩它自己 → 拟合不成出 NaN。

        拿异号点混入拟合 = 对「+0.04 与 −0.9」之间做对数衰减，数字毫无意义。
        """
        half_life, decay = st.ic_half_life((1, 5, 10), (0.05, 0.04, -0.9))

        assert np.isnan(half_life) and np.isnan(decay)

    def test_non_decaying_series_reports_nan_half_life(self):
        """IC 随周期上升（λ ≤ 0）= 「不衰减」：半衰期 NaN，λ 如实带符号。"""
        half_life, decay = st.ic_half_life((1, 5, 10), (0.01, 0.2, 0.4))

        assert np.isnan(half_life)
        assert decay < 0, "λ ≤ 0 是「不衰减」的读数，不是算不出"

    def test_too_few_points_is_nan(self):
        assert np.all(np.isnan(st.ic_half_life((1, 5), (0.05, 0.02))))

    def test_nan_ics_are_filtered_not_imputed(self):
        """NaN 周期（样本不足）不算点：过滤后不足 3 点就如实出 NaN。"""
        half_life, decay = st.ic_half_life((1, 5, 10), (np.nan, 0.05, 0.02))

        assert np.isnan(half_life) and np.isnan(decay)

    def test_min_points_is_configurable(self):
        half_life, _decay = st.ic_half_life((1, 5), (0.06, 0.03), min_points=2)

        assert np.isfinite(half_life)


class TestFoldBoundaries:
    def test_even_split(self):
        dates = pd.bdate_range("2024-01-01", periods=8)

        folds = st.fold_boundaries(dates, 4)

        assert folds.tolist() == [0, 0, 1, 1, 2, 2, 3, 3]

    def test_uneven_split_keeps_all_positions_in_range(self):
        dates = pd.bdate_range("2024-01-01", periods=10)

        folds = st.fold_boundaries(dates, 4)

        assert folds.tolist() == [0, 0, 0, 1, 1, 1, 2, 2, 3, 3]
        assert folds.max() < 4

    def test_fewer_dates_than_folds_does_not_overflow(self):
        """日期数 < 折数：空折在尾部，折号仍全部 < folds（不许越界索引）。"""
        dates = pd.bdate_range("2024-01-01", periods=3)

        folds = st.fold_boundaries(dates, 4)

        assert folds.tolist() == [0, 1, 2]
        assert folds.max() < 4

    def test_monotonic_non_decreasing(self):
        dates = pd.bdate_range("2024-01-01", periods=37)
        folds = st.fold_boundaries(dates, 4)
        assert (np.diff(folds) >= 0).all()


class TestReselectStatistics:
    def test_late_burst_candidate_gets_low_rate(self):
        """全程稳定者命中 2/3、窗尾爆发者只有 1/3——审计量的典型形态。

        steady 在前两个检查点稳赢；**末检查点的累计窗口就是整窗**，tail 的
        整窗 |均值|（0.3×20/80 = 0.075）反超 steady（0.05）——「整窗最强」
        但只有末段发力。命中率是审计量（2026-10-11 起不当闸：它是跨候选抢
        K 席的相对量，实跑会刀锋误伤；闸取折间符号一致性，见 stability.py），
        命中低照记、随报告上屏供人审。
        """
        steady = _series([0.05] * 80)
        tail = _series([0.0] * 60 + [0.3] * 20)

        stats = st.reselect_statistics(
            {"steady": steady, "tail": tail}, folds=4, top_k=1, min_fold_days=10
        )

        assert stats["steady"].reselect_hits == 2
        assert stats["steady"].reselect_rate == pytest.approx(2 / 3)
        assert stats["steady"].eligible is True

        # 检查点 2/3 上 tail 的累计均值为 0（窗尾才发力）→ 只可能在检查点 4 命中
        assert stats["tail"].reselect_hits == 1
        assert stats["tail"].reselect_rate == pytest.approx(1 / 3)
        assert stats["tail"].eligible is True

    def test_fold_ics_and_sign_consistency(self):
        steady = _series([0.05] * 80)
        flip = _series([0.04] * 40 + [-0.06] * 40)

        stats = st.reselect_statistics(
            {"steady": steady, "flip": flip}, folds=4, top_k=1, min_fold_days=10
        )

        assert stats["steady"].fold_ics == pytest.approx((0.05,) * 4)
        assert stats["steady"].sign_consistency == pytest.approx(1.0)
        assert stats["flip"].fold_ics == pytest.approx((0.04, 0.04, -0.06, -0.06))
        assert stats["flip"].sign_consistency == pytest.approx(0.5)

    def test_thin_folds_are_ineligible_with_note(self):
        """折内有效日不足 → 不判稳定性（eligible=False），note 说明原因。

        闸不响 ≠ 通过：引擎只拒「可判定且率低」，不可判定如实上屏。
        """
        short = _series([0.05] * 12)  # 4 折 × 3 天 < min_fold_days

        stats = st.reselect_statistics(
            {"short": short}, folds=4, top_k=1, min_fold_days=10
        )

        assert stats["short"].eligible is False
        assert "折内有效日不足" in stats["short"].note

    def test_empty_series_records_reason(self):
        stats = st.reselect_statistics(
            {"gone": pd.Series(dtype=float)}, folds=4, top_k=1, min_fold_days=5
        )

        assert stats["gone"].eligible is False
        assert stats["gone"].note == "无有效 IC 日"
        assert np.isnan(stats["gone"].reselect_rate)
        assert stats["gone"].reselect_checkpoints == 3
        assert stats["gone"].fold_ics == ()

    def test_union_calendar_covers_partial_candidates(self):
        """并集日历分折：只覆盖一半日期的候选也在同一批折边界上判定。"""
        full = _series([0.05] * 80)
        half = _series([0.05] * 40)

        stats = st.reselect_statistics(
            {"full": full, "half": half}, folds=4, top_k=2, min_fold_days=5
        )

        assert len(stats["full"].fold_ics) == 4
        assert len(stats["half"].fold_ics) == 4
        assert stats["half"].eligible is False, "后半段没有有效日 → 折内有效日不足"
        assert np.isnan(stats["half"].fold_ics[3])

    def test_ties_break_by_key_deterministically(self):
        """两个并列值：Top-K 判决按 key 定序——同一份输入必须给同一份报告。"""
        aaa = _series([0.05] * 80)
        zzz = _series([0.05] * 80)

        stats = st.reselect_statistics(
            {"zzz": zzz, "aaa": aaa}, folds=4, top_k=1, min_fold_days=10
        )

        assert stats["aaa"].reselect_hits == 3
        assert stats["zzz"].reselect_hits == 0

    def test_single_checkpoint_when_two_folds(self):
        steady = _series([0.05] * 40)
        weak = _series([0.01] * 40)

        stats = st.reselect_statistics(
            {"steady": steady, "weak": weak}, folds=2, top_k=1, min_fold_days=5
        )

        assert stats["steady"].reselect_checkpoints == 1
        assert stats["steady"].reselect_hits == 1
        assert stats["weak"].reselect_hits == 0

    def test_bad_params_rejected(self):
        with pytest.raises(ValueError, match="folds"):
            st.reselect_statistics({}, folds=1, top_k=1)
        with pytest.raises(ValueError, match="top_k"):
            st.reselect_statistics({}, folds=4, top_k=0)
