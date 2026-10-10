"""统计派 IC 统计核心（移植 P2 / Tier 1）——金样与对拍。

契约：

1. **前向收益**按 symbol 内 bar 序 shift（停牌缺 bar 自动跨过），close-to-close；
2. **逐日截面 IC** = Pearson；**RankIC** = 双向秩后 Pearson（与 scipy.spearmanr
   同值）；单日样本 <10 剔除；零方差/全 NaN 日自然剔除；
3. **汇总**：ICIR=mean/std（日频）；t=ICIR·√n；p 用 Student-t(df=n−1)；
   有效日 <40 → ``None``（不硬凑）；
4. **分组收益**：等份 rank 分组，报组均、顶底价差 t 与单调性 τ；
5. **BH-FDR**：标准步进，NaN 项不计入 m 也不判显著。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import ic_stats as ics  # noqa: E402


def _panel(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """(date, symbol, close) → MultiIndex 面板（open/high/low 复制 close 即可）。"""
    frame = pd.DataFrame(rows, columns=["trade_date", "symbol", "close"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.set_index(["trade_date", "symbol"]).sort_index()
    for col in ("open", "high", "low"):
        frame[col] = frame["close"]
    frame["volume"] = 1.0
    frame["amount"] = 1.0
    return frame


# ── 前向收益 ─────────────────────────────────────────────────────────


class TestForwardReturns:
    def test_close_to_close_per_symbol(self):
        panel = _panel(
            [
                ("2024-01-01", "A", 10.0),
                ("2024-01-02", "A", 11.0),
                ("2024-01-03", "A", 12.1),
                ("2024-01-01", "B", 20.0),
                ("2024-01-02", "B", 18.0),
                ("2024-01-03", "B", 18.0),
            ]
        )
        fwd = ics.forward_returns(panel, 1)
        assert fwd.loc[("2024-01-01", "A")] == pytest.approx(0.1)  # 11/10-1
        assert fwd.loc[("2024-01-02", "A")] == pytest.approx(0.1)  # 12.1/11-1
        assert np.isnan(fwd.loc[("2024-01-03", "A")]), "末端无标签"
        assert fwd.loc[("2024-01-01", "B")] == pytest.approx(-0.1)  # 18/20-1
        assert fwd.loc[("2024-01-02", "B")] == pytest.approx(0.0)

    def test_horizon_two_and_suspension_gap(self):
        panel = _panel(
            [
                ("2024-01-01", "A", 10.0),
                # 01-02 停牌缺 bar
                ("2024-01-03", "A", 11.0),
                ("2024-01-04", "A", 12.0),
            ]
        )
        fwd = ics.forward_returns(panel, 2)
        # 停牌跳过：01-01 的 2-bar 前向 = 01-04/01-01（bar 序而非自然日）
        assert fwd.loc[("2024-01-01", "A")] == pytest.approx(0.2)

    def test_invalid_horizon(self):
        with pytest.raises(ValueError):
            ics.forward_returns(_panel([("2024-01-01", "A", 1.0)]), 0)


# ── 逐日 IC ──────────────────────────────────────────────────────────


class TestDailyIc:
    def test_perfect_positive_and_negative(self):
        dates = pd.date_range("2024-01-01", periods=3)
        rows = []
        for day in dates:
            for s in range(12):
                rows.append((day.strftime("%Y-%m-%d"), f"S{s:02d}", 10.0 + s))
        panel = _panel(rows)
        # 构造标签与因子同序：后一日 close = 常数 * 因子值
        factor = panel["close"]
        fwd = pd.Series(
            [0.05 * s for s in range(12)] * 3,
            index=panel.index,
            dtype=float,
        )
        daily = ics.daily_ic(factor, fwd)
        assert len(daily) == 3
        assert (daily["ic"] > 0.999).all() and (daily["rank_ic"] > 0.999).all()
        assert (daily["n"] == 12).all()

        neg = ics.daily_ic(-factor, fwd)
        assert (neg["ic"] < -0.999).all()

    def test_matches_scipy_on_random_day(self):
        rng = np.random.default_rng(7)
        n = 40
        factor = pd.Series(rng.normal(size=n))
        fwd = pd.Series(rng.normal(size=n))
        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-01"]), [f"S{i:02d}" for i in range(n)]],
            names=["trade_date", "symbol"],
        )
        factor.index = idx
        fwd.index = idx
        daily = ics.daily_ic(factor, fwd)
        assert daily.loc[pd.Timestamp("2024-01-01"), "ic"] == pytest.approx(
            sps.pearsonr(factor, fwd).statistic
        )
        assert daily.loc[pd.Timestamp("2024-01-01"), "rank_ic"] == pytest.approx(
            sps.spearmanr(factor, fwd).statistic
        )

    def test_min_names_day_dropped(self):
        idx_small = pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-01"]), [f"S{i}" for i in range(9)]],
            names=["trade_date", "symbol"],
        )
        idx_big = pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-02"]), [f"S{i}" for i in range(11)]],
            names=["trade_date", "symbol"],
        )
        idx = idx_small.append(idx_big)
        factor = pd.Series(np.arange(len(idx), dtype=float), index=idx)
        fwd = (factor * 2).rename("r")
        daily = ics.daily_ic(factor, fwd)
        assert list(daily.index) == [pd.Timestamp("2024-01-02")], "9 只的日子整日剔除"

    def test_zero_variance_day_dropped(self):
        idx = pd.MultiIndex.from_product(
            [pd.to_datetime(["2024-01-01"]), [f"S{i}" for i in range(12)]],
            names=["trade_date", "symbol"],
        )
        factor = pd.Series(5.0, index=idx)  # 常数截面 → 零方差
        fwd = pd.Series(np.arange(12, dtype=float), index=idx)
        assert ics.daily_ic(factor, fwd).empty


# ── 汇总统计 ─────────────────────────────────────────────────────────


def _daily_from(rank_ics: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rank_ics))
    return pd.DataFrame(
        {"ic": rank_ics, "rank_ic": rank_ics, "n": 50}, index=idx
    )


class TestSummarize:
    def test_min_days_gate(self):
        assert ics.summarize_ic(_daily_from([0.05] * 39)) is None
        assert ics.summarize_ic(_daily_from([0.05] * 40)) is not None

    def test_icir_t_and_p_math(self):
        values = [0.02, 0.04, 0.01, 0.05, 0.03] * 8  # 40 天
        summary = ics.summarize_ic(_daily_from(values))
        arr = np.asarray(values)
        mean, std = arr.mean(), arr.std(ddof=1)
        icir = mean / std
        t_stat = icir * np.sqrt(40)
        assert summary.rank_ic_mean == pytest.approx(mean)
        assert summary.icir == pytest.approx(icir)
        assert summary.rank_ic_t == pytest.approx(t_stat)
        assert summary.rank_ic_p == pytest.approx(2 * sps.t.sf(abs(t_stat), 39))
        assert summary.rank_ic_win_rate == 1.0


# ── 分组收益 ─────────────────────────────────────────────────────────


class TestQuantile:
    def test_monotone_panel(self):
        """收益严格随因子值递增 → 组均递增、τ≈1、价差>0（逐日微扰让价差有方差）。"""
        rows, labels = [], []
        for d in range(5):
            day = f"2024-01-0{d + 1}"
            for s in range(50):
                rows.append((day, f"S{s:02d}", float(s)))
                labels.append((0.001 + 0.00001 * d) * s)
        panel = _panel(rows)
        factor = panel["close"]  # close 即因子值（rows 里已放 float(s)）
        fwd = pd.Series(labels, index=panel.index)
        summary = ics.quantile_returns(factor, fwd, quantiles=5)
        assert summary is not None and summary.n_days == 5
        means = summary.quantile_means
        assert means == tuple(sorted(means)), "组均必须单调递增"
        # 每日顶底价差 = 40 * (0.001 + 0.00001*d)，均值即期望
        assert summary.spread_mean == pytest.approx(
            np.mean([40 * (0.001 + 0.00001 * d) for d in range(5)]), abs=1e-9
        )
        assert summary.monotonicity_tau == pytest.approx(1.0)
        assert summary.spread_t > 0

    def test_reversed_spread_negative(self):
        rows, labels = [], []
        for d in range(3):
            day = f"2024-01-0{d + 1}"
            for s in range(40):
                rows.append((day, f"S{s:02d}", float(s)))
                labels.append(1.0 - 0.001 * s)  # 反向
        panel = _panel(rows)
        factor = panel["close"]
        summary = ics.quantile_returns(factor, pd.Series(labels, index=panel.index))
        assert summary.spread_mean < 0 and summary.monotonicity_tau == pytest.approx(-1.0)

    def test_ties_do_not_crash(self):
        rows, labels = [], []
        for d in range(2):
            day = f"2024-01-0{d + 1}"
            for s in range(30):  # 一半并列
                rows.append((day, f"S{s:02d}", 1.0 if s < 15 else 2.0))
                labels.append(0.01 * s)
        panel = _panel(rows)
        factor = panel["close"]
        summary = ics.quantile_returns(factor, pd.Series(labels, index=panel.index))
        assert summary is not None, "并列值 rank(first) 法可分组，不许炸"


# ── BH-FDR ───────────────────────────────────────────────────────────


class TestBhFdr:
    def test_standard_step_up(self):
        p = [0.01, 0.02, 0.03, 0.04]
        assert ics.bh_fdr(p, q=0.10) == [True] * 4, "0.04 ≤ 0.10*4/4 全过"
        p2 = [0.03, 0.01, 0.5, 0.9]
        assert ics.bh_fdr(p2, q=0.10) == [True, True, False, False]

    def test_nan_excluded_from_m_and_never_significant(self):
        # m=2 有效项：阈值 rank1=0.05, rank2=0.10
        assert ics.bh_fdr([0.04, float("nan"), 0.09], q=0.10) == [
            True,
            False,
            True,
        ]
        assert ics.bh_fdr([float("nan")]) == [False]

    def test_empty(self):
        assert ics.bh_fdr([]) == []
