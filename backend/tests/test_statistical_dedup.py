"""统计派 · 入选集相关性去重 + 稳定因子身份（Tier 3 / T-S3-03）。

1. **相关性口径**（``ic_stats.daily_rank_corr``）：逐日截面 Spearman 的时间均值——
   完美同序 → 1、逆序 → −1、无重叠日 → NaN（``(NaN, 0)`` 是「没证据」不是「不相关」）；
2. **去重判决**：按 order（|RankIC| 降序）贪心，与任何**已保留者**超阈值即拒、
   warn 带保留、算不出 ρ 的对保留并记 NaN；不补位；max_abs_corr 对称；
3. **稳定身份**（``stable_factor_id``）：同配置恒同 id（md5，跨进程稳定），
   配置间的任何差异（user/market/universe/key/指纹）都换 id；无指纹退回
   任务级身份并如实标 scheme——空指纹不许当作「配置身份」互相幂等覆盖。
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

from backend.services.engine.alpha_agent.statistical import dedup as dd  # noqa: E402
from backend.services.engine.alpha_agent.statistical import ic_stats as ic  # noqa: E402

N_DAYS = 30
N_SYMBOLS = 12


def _index(days: int = N_DAYS, symbols: int = N_SYMBOLS) -> pd.MultiIndex:
    return pd.MultiIndex.from_product(
        [
            pd.bdate_range("2024-01-01", periods=days),
            [f"s{i:02d}" for i in range(symbols)],
        ],
        names=["trade_date", "symbol"],
    )


def _factored(values: np.ndarray) -> pd.Series:
    """逐日相同的一组截面值 → 序列（与同构序列秩相关 1/−1）。"""
    flat = np.tile(np.asarray(values, dtype=float), N_DAYS)
    return pd.Series(flat, index=_index())


def _noise(seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.normal(size=N_DAYS * N_SYMBOLS).astype(float), index=_index()
    )


def _base_with_swaps(swaps: int) -> pd.Series:
    """基准截面 + 每日交换若干相邻位次 → 与基准的秩相关落在 (0, 1) 之间。"""
    values = np.arange(N_SYMBOLS, dtype=float)
    rng = np.random.default_rng(7)
    out = []
    for _day in range(N_DAYS):
        day = values.copy()
        for _ in range(swaps):
            i = int(rng.integers(0, N_SYMBOLS - 1))
            day[i], day[i + 1] = day[i + 1], day[i]
        out.append(day)
    return pd.Series(np.concatenate(out), index=_index())


class TestDailyRankCorr:
    def test_identical_ranks_give_one(self):
        a = _factored(np.arange(N_SYMBOLS))

        rho, n_days = ic.daily_rank_corr(a, a * 2 + 100)

        assert rho == pytest.approx(1.0)
        assert n_days == N_DAYS

    def test_reversed_ranks_give_minus_one(self):
        a = _factored(np.arange(N_SYMBOLS))

        rho, _n = ic.daily_rank_corr(a, -a)

        assert rho == pytest.approx(-1.0)

    def test_no_overlap_is_nan_with_zero_days(self):
        """无重叠日 → (NaN, 0)：没证据 ≠ 不相关（不相关是 0）。"""
        a = _factored(np.arange(N_SYMBOLS))
        b = a.copy()
        b.index = b.index.set_levels(
            b.index.levels[0] + pd.Timedelta(days=365), level=0
        )

        rho, n_days = ic.daily_rank_corr(a, b)

        assert np.isnan(rho) and n_days == 0

    def test_thin_days_excluded_by_min_names(self):
        """单日样本不足的日子不计（与 IC 的 min_names 同一口径）。"""
        a = pd.Series(  # 每日只有 4 个标的
            np.tile(np.arange(4, dtype=float), N_DAYS), index=_index(symbols=4)
        )

        rho, n_days = ic.daily_rank_corr(a, a, min_names=10)

        assert np.isnan(rho) and n_days == 0

    def test_swapped_pairs_land_between(self):
        a = _factored(np.arange(N_SYMBOLS))

        rho, _n = ic.daily_rank_corr(a, _base_with_swaps(3))

        assert 0 < rho < 1


class TestDedupSelected:
    def test_correlated_candidates_dropped_greedily(self):
        base = _factored(np.arange(N_SYMBOLS))
        clone = _factored(np.arange(N_SYMBOLS) * 3.0)  # 秩同序 → ρ=1
        mirror = _factored(-np.arange(N_SYMBOLS))  # 秩逆序 → ρ=−1
        independent = _noise(11)
        factors = {"a": base, "b": clone, "c": mirror, "d": independent}

        report = dd.dedup_selected(factors, order=["a", "b", "c", "d"])

        assert report.kept == ("a", "d")
        dropped = {key: (matched, rho) for key, matched, rho in report.dropped}
        assert dropped["b"] == ("a", pytest.approx(1.0))
        assert dropped["c"] == ("a", pytest.approx(-1.0))
        # |ρ| ≥ reject 用绝对值口径：完全反向也算重复（取负是同一个信息）
        assert report.max_abs_corr("a") == pytest.approx(1.0)
        assert np.isnan(report.max_abs_corr("d")) or report.max_abs_corr("d") < 0.5
        assert report.n_pairs == 3  # b:a, c:a, d:a（被拒者不再当 champion）

    def test_greedy_compares_against_kept_only(self):
        """非传递：b 被 a 挤掉后，c 只与「已保留的 a」比——c 留下。

        这与「对全体入选两两去重」不同，是有意为之：保留集内的两两低相关
        才是目的，被挤掉者不参与比较。
        """
        a = _factored(np.arange(N_SYMBOLS))
        b = _base_with_swaps(2)  # 与 a 高相关
        c = _noise(23)  # 与 a 低相关
        factors = {"a": a, "b": b, "c": c}
        # 手工核对 b 与 a 的相关高于阈值：先单独算
        rho_ab, _ = ic.daily_rank_corr(a, b)
        assert abs(rho_ab) >= dd.DEFAULT_CORR_REJECT  # 前置条件（数据构造）

        report = dd.dedup_selected(factors, order=["a", "b", "c"])

        assert report.kept == ("a", "c")

    def test_warn_band_keeps_with_note(self):
        """ρ ∈ [warn, reject) → 保留 + 警语（阈值全 0/1 使任何有限 ρ 都落带内）。"""
        a = _factored(np.arange(N_SYMBOLS))
        b = _noise(5)
        factors = {"a": a, "b": b}

        report = dd.dedup_selected(factors, order=["a", "b"], warn=0.0, reject=1.0)

        assert report.kept == ("a", "b")
        assert len(report.warnings) == 1
        key_a, key_b, _rho = report.warnings[0]
        assert {key_a, key_b} == {"a", "b"}
        note = report.warn_note("a")
        assert "相关偏高" in note and "b" in note
        assert report.warn_note("b") != "", "警语对称：两侧都能查到"
        assert np.isfinite(report.max_abs_corr("a"))

    def test_uncomputable_pair_kept_with_nan(self):
        """算不出 ρ 的对（无重叠日）按「无证据」保留，不伪造 0 相关。"""
        a = _factored(np.arange(N_SYMBOLS))
        b = a.copy()
        b.index = b.index.set_levels(
            b.index.levels[0] + pd.Timedelta(days=365), level=0
        )

        report = dd.dedup_selected({"a": a, "b": b}, order=["a", "b"])

        assert report.kept == ("a", "b")
        assert report.dropped == ()
        assert np.isnan(report.max_abs_corr("a"))
        assert report.n_pairs == 1
        assert report.warn_note("a") == ""

    def test_single_selection_has_no_pairs(self):
        factors = {"a": _factored(np.arange(N_SYMBOLS))}
        report = dd.dedup_selected(factors, order=["a"])
        assert report.kept == ("a",)
        assert report.n_pairs == 0
        assert np.isnan(report.max_abs_corr("a"))

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            dd.dedup_selected({"a": _factored(np.arange(N_SYMBOLS))}, order=["a", "b"])

    def test_bad_thresholds_rejected(self):
        with pytest.raises(ValueError, match="阈值非法"):
            dd.dedup_selected({}, order=[], warn=0.9, reject=0.8)
        with pytest.raises(ValueError, match="阈值非法"):
            dd.dedup_selected({}, order=[], warn=-0.1, reject=0.9)

    def test_threshold_reported_in_message(self):
        a = _factored(np.arange(N_SYMBOLS))
        b = _factored(np.arange(N_SYMBOLS) * 3.0)

        report = dd.dedup_selected(
            {"a": a, "b": b}, order=["a", "b"], warn=0.2, reject=0.5
        )

        assert report.reject == 0.5
        assert report.dropped[0][0] == "b"


class TestStableFactorId:
    def _kwargs(self, **overrides):
        base = {
            "user_id": "u1", "market": "a_share", "universe": "all_a",
            "candidate_key": "reversal_5d",
            "task_fingerprint": "fp-abc", "task_id": "t1",
        }
        base.update(overrides)
        return base

    def test_same_config_same_id(self):
        first, scheme = dd.stable_factor_id(**self._kwargs())
        second, scheme2 = dd.stable_factor_id(**self._kwargs())

        assert first == second and scheme == scheme2 == dd.SCHEME_CONFIG
        assert len(first) == 32 and all(c in "0123456789abcdef" for c in first)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("user_id", "u2"),
            ("market", "hong_kong"),
            ("universe", "csi300"),
            ("candidate_key", "momentum_20d"),
            ("task_fingerprint", "fp-other"),
        ],
    )
    def test_any_scope_difference_changes_id(self, field, value):
        base, _ = dd.stable_factor_id(**self._kwargs())
        other, _ = dd.stable_factor_id(**self._kwargs(**{field: value}))
        assert base != other, f"{field} 变了 id 还一样——身份口径漏了这个维度"

    def test_task_id_does_not_leak_into_config_identity(self):
        """配置身份下 task_id 不参与哈希：换个任务 id 重跑同配置仍刷新同一行。"""
        base, _ = dd.stable_factor_id(**self._kwargs(task_id="t1"))
        other, _ = dd.stable_factor_id(**self._kwargs(task_id="t2"))
        assert base == other

    def test_missing_fingerprint_falls_back_to_task_scheme(self):
        with_fp, scheme_fp = dd.stable_factor_id(**self._kwargs())
        without, scheme_task = dd.stable_factor_id(
            **self._kwargs(task_fingerprint="", task_id="t1")
        )

        assert scheme_task == dd.SCHEME_TASK
        assert without != with_fp, "缺指纹不许与配置身份撞 id"

    def test_blank_fingerprint_is_treated_as_missing(self):
        blank, scheme = dd.stable_factor_id(**self._kwargs(task_fingerprint="   "))
        none, scheme2 = dd.stable_factor_id(**self._kwargs(task_fingerprint=""))
        assert blank == none and scheme == scheme2 == dd.SCHEME_TASK

    def test_task_scheme_still_distinguishes_tasks(self):
        a, _ = dd.stable_factor_id(**self._kwargs(task_fingerprint="", task_id="t1"))
        b, _ = dd.stable_factor_id(**self._kwargs(task_fingerprint="", task_id="t2"))
        assert a != b
