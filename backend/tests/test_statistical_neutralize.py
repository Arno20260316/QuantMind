"""统计派中性化（移植 P2 / Tier 2）——金样算术 + 正交性不变量。

契约：

1. **行业内去均值** 是逐日截面的精确算术（手算金样）；跨日不做任何混合；
   行业缺失样本 → NaN 并计进报告；
2. **行业+市值回归残差** 在「因子 = 行业效应 + a·ln市值」的构造数据上残差≈0；
   残差对每日设计矩阵正交（列和≈0、与 ln 市值相关≈0）；size≤0/缺失 → NaN；
   日样本不足 → 整日 NaN 且 ``n_days_skipped`` 计数；
3. ``close × float_shares`` 与显式 ``size`` 两条入口等价。
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

from backend.services.engine.alpha_agent.statistical import neutralize as nz  # noqa: E402


def _panel(rows: list[tuple[str, str, float, str, float]]) -> pd.DataFrame:
    """(date, symbol, factor, industry, size) → MultiIndex (trade_date, symbol) 帧。"""
    frame = pd.DataFrame(rows, columns=["trade_date", "symbol", "factor", "industry", "size"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame.set_index(["trade_date", "symbol"]).sort_index()


# ── 行业内去均值 ─────────────────────────────────────────────────────


class TestIndustryDemean:
    def test_exact_arithmetic_per_day(self):
        panel = _panel(
            [
                ("2024-01-01", "A1", 10.0, "银行", 1e9),
                ("2024-01-01", "A2", 20.0, "银行", 1e9),
                ("2024-01-01", "B1", 30.0, "地产", 1e9),
                # 次日：同因子值，行业构成不同 → 结果不同（逐日口径）
                ("2024-01-02", "A1", 10.0, "银行", 1e9),
                ("2024-01-02", "B1", 30.0, "地产", 1e9),
                ("2024-01-02", "B2", 50.0, "地产", 1e9),
            ]
        )
        out, report = nz.industry_demean(panel["factor"], panel["industry"])
        assert out.loc[("2024-01-01", "A1")] == pytest.approx(-5.0)
        assert out.loc[("2024-01-01", "A2")] == pytest.approx(5.0)
        assert out.loc[("2024-01-01", "B1")] == pytest.approx(0.0)
        assert out.loc[("2024-01-02", "A1")] == pytest.approx(0.0)
        assert out.loc[("2024-01-02", "B1")] == pytest.approx(-10.0)
        assert out.loc[("2024-01-02", "B2")] == pytest.approx(10.0)
        assert report.n_input == 6 and report.n_output == 6
        assert report.n_missing_meta == 0 and report.n_days_skipped == 0

    def test_missing_industry_is_nan_and_counted(self):
        panel = _panel(
            [
                ("2024-01-01", "A1", 10.0, "银行", 1e9),
                ("2024-01-01", "A2", 20.0, "银行", 1e9),
                ("2024-01-01", "X", 99.0, None, 1e9),  # 行业缺失
            ]
        )
        out, report = nz.industry_demean(panel["factor"], panel["industry"])
        assert np.isnan(out.loc[("2024-01-01", "X")])
        assert report.n_missing_meta == 1 and report.n_output == 2

    def test_single_name_industry_becomes_zero(self):
        panel = _panel(
            [
                ("2024-01-01", "A1", 10.0, "银行", 1e9),
                ("2024-01-01", "A2", 20.0, "银行", 1e9),
                ("2024-01-01", "S", 7.0, "独苗", 1e9),
            ]
        )
        out, _ = nz.industry_demean(panel["factor"], panel["industry"])
        assert out.loc[("2024-01-01", "S")] == pytest.approx(0.0)


# ── 行业 + 市值回归残差 ──────────────────────────────────────────────


def _synthetic_two_days(n_per_day: int = 40) -> pd.DataFrame:
    """因子 = 行业效应 + 0.5·ln(size) + 微小噪声（噪声让残差有内容但不为 0）。"""
    rng = np.random.default_rng(11)
    rows = []
    for day in ("2024-01-01", "2024-01-02"):
        for i in range(n_per_day):
            industry = ["银行", "地产", "医药"][i % 3]
            size = float(10 ** rng.uniform(8.5, 11.0))
            effect = {"银行": 1.0, "地产": -2.0, "医药": 0.5}[industry]
            noise = rng.normal(0, 1e-6)
            rows.append((day, f"S{i:02d}", effect + 0.5 * np.log(size) + noise, industry, size))
    return _panel(rows)


class TestIndustrySizeResidual:
    def test_linear_structure_fully_absorbed(self):
        panel = _synthetic_two_days()
        out, report = nz.industry_size_residual(
            panel["factor"], panel["industry"], size=panel["size"]
        )
        assert out.notna().all()
        assert np.abs(out.to_numpy()).max() < 1e-4, "线性结构应被回归吸收（只剩噪声）"
        assert report.n_output == len(panel) and report.n_days_skipped == 0

    def test_residual_orthogonal_to_regressors_within_day(self):
        panel = _synthetic_two_days()
        out, _ = nz.industry_size_residual(
            panel["factor"], panel["industry"], size=panel["size"]
        )
        log_size = np.log(panel["size"])
        for day in panel.index.get_level_values(0).unique():
            sub = out.xs(day)
            assert abs(sub.sum()) < 1e-6, "对截距正交 → 组内和≈0"
            assert abs(np.corrcoef(sub, log_size.xs(day))[0, 1]) < 1e-6, "对 ln 市值正交"
            for industry in panel["industry"].xs(day).unique():
                assert abs(sub[panel["industry"].xs(day) == industry].sum()) < 1e-6

    def test_invalid_size_is_nan_and_counted(self):
        panel = _synthetic_two_days()
        panel.iloc[0, panel.columns.get_loc("size")] = 0.0  # size ≤ 0
        out, report = nz.industry_size_residual(
            panel["factor"], panel["industry"], size=panel["size"]
        )
        assert np.isnan(out.iloc[0]) and report.n_missing_meta == 1

    def test_small_day_skipped_and_counted(self):
        rows = [
            ("2024-01-01", "A1", 1.0, "银行", 1e9),
            ("2024-01-01", "A2", 2.0, "银行", 2e9),
        ]
        rows += [
            ("2024-01-03", f"S{i:02d}", float(i), ["银行", "地产"][i % 2], 10 ** (9 + i % 3))
            for i in range(30)
        ]
        frame = pd.DataFrame(rows, columns=["trade_date", "symbol", "factor", "industry", "size"])
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        panel = frame.set_index(["trade_date", "symbol"]).sort_index()
        out, report = nz.industry_size_residual(
            panel["factor"], panel["industry"], size=panel["size"]
        )
        small_day = out.xs(pd.Timestamp("2024-01-01"))
        assert small_day.isna().all(), "2 只票的日子装不下回归 → 整日 NaN"
        assert report.n_days_skipped == 1
        assert out.xs(pd.Timestamp("2024-01-03")).notna().all()

    def test_close_times_shares_equals_explicit_size(self):
        panel = _synthetic_two_days()
        close = pd.Series(np.sqrt(panel["size"]), index=panel.index)  # close² = size
        shares = pd.Series(np.sqrt(panel["size"]), index=panel.index)
        via_product, _ = nz.industry_size_residual(
            panel["factor"], panel["industry"], close=close, float_shares=shares
        )
        via_size, _ = nz.industry_size_residual(
            panel["factor"], panel["industry"], size=panel["size"]
        )
        pd.testing.assert_series_equal(via_product, via_size, check_names=False)

    def test_missing_inputs_raise(self):
        panel = _synthetic_two_days()
        with pytest.raises(ValueError, match="size"):
            nz.industry_size_residual(panel["factor"], panel["industry"])
