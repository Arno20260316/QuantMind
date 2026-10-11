"""统计派 · 换手/扣费证据（T-FE-03）——口径契约。

三条口径全部有来源，不另立炉灶：

1. **多头谓词** = 截面 rank(pct) ≥ ``LONG_RANK_PCT_THRESHOLD``（0.7，前 30%）
   ——与回测路径（``mining_plugins.evaluators.turnover_cost``）同一谓词、
   同一常量对象；跨实现对拍测试（``test_matches_backtest_turnover_formula``）
   在随机数据上钉死两份实现逐位一致；
2. **换手** = ``|S_t∖S_{t−1}|/|S_t|``，首日无「昨日名单」= NaN 不计成本
   （AlphaEval 同行为）；
3. **扣费净 IC** = 当日**新进多头名单**的股票从 1 日前向收益里扣 ``cost_rate``
   （双边，单源 ``mining_plugins.config.get_cost_rate``：env > yaml >
   factor_research.COST_RATE），再与因子值算逐日 RankIC 的均值——答案
   「这个因子的预测力扛不扛得住它自己触发的交易」。
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

from backend.services.engine.alpha_agent.statistical import costs as sc  # noqa: E402
from backend.services.engine.alpha_agent.statistical import ic_stats as ics  # noqa: E402

N_SYMBOLS = 20
N_DAYS = 30


def _series(values: np.ndarray) -> pd.Series:
    """矩阵 (day, symbol) → MultiIndex (trade_date, symbol) 序列。"""
    dates = pd.bdate_range("2024-01-01", periods=values.shape[0])
    symbols = [f"S{i:02d}" for i in range(values.shape[1])]
    idx = pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "symbol"])
    return pd.Series(values.reshape(-1), index=idx)


def _stable_factor() -> pd.Series:
    """因子值 = 标的编号（每天截面排序恒定）→ 多头名单天天不变。"""
    grid = np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1))
    return _series(grid)


def _rotating_factor() -> pd.Series:
    """偶数日 = 编号、奇数日 = 负编号 → 多头名单每日完全换血。"""
    base = np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1))
    base[1::2] *= -1
    return _series(base)


class TestTurnover:
    def test_stable_long_has_zero_turnover_after_first_day(self):
        turnover = sc.daily_turnover_long(_stable_factor())
        assert np.isnan(turnover.iloc[0]), "首日无昨日名单——NaN 不是 0"
        rest = turnover.iloc[1:]
        assert (rest == 0.0).all(), "名单不变 ⇒ 换手 0"
        assert turnover.index.name == "trade_date"

    def test_full_rotation_has_full_turnover(self):
        turnover = sc.daily_turnover_long(_rotating_factor()).iloc[1:]
        assert turnover.to_numpy() == pytest.approx(1.0), "整组换血 ⇒ |S∖prev|/|S| = 1"

    def test_day_below_min_names_dropped(self):
        factor = _stable_factor()
        # 砍到只剩 5 只 —— 低于 min_names=10 的日子整日剔除（与 IC 同口径）
        small = factor[factor.index.get_level_values(1) < "S05"]
        assert sc.daily_turnover_long(small).empty

    def test_matches_backtest_turnover_formula(self):
        """跨实现对拍：与回测路径 ``daily_long_ret_and_turnover`` 逐位一致。

        两份实现若漂移（谓词/分母/首日口径），这里必须红——统计派报告与
        回测页的换手数字不允许各说各话。
        """
        from backend.services.engine.mining_plugins.evaluators.turnover_cost import (
            daily_long_ret_and_turnover,
        )

        rng = np.random.default_rng(11)
        days = pd.bdate_range("2024-01-01", periods=N_DAYS)
        symbols = [f"S{i:02d}" for i in range(N_SYMBOLS)]
        rows = []
        for d in days:
            for s in symbols:
                rows.append((d, s, float(rng.normal()), float(rng.normal()) * 0.01))
        paired = pd.DataFrame(rows, columns=["datetime", "symbol", "factor", "ret"])
        _ret, turnover = daily_long_ret_and_turnover(paired)

        factor = paired.set_index(["datetime", "symbol"])["factor"]
        factor.index = factor.index.set_names(["trade_date", "symbol"])
        mine = sc.daily_turnover_long(factor)
        both = pd.concat({"backtest": turnover, "report": mine}, axis=1)
        assert both.notna().all(axis=1).any(), "对拍必须有可比行"
        agree = both.dropna()
        assert len(agree) >= 20
        assert agree["backtest"].to_numpy() == pytest.approx(
            agree["report"].to_numpy(), rel=1e-12
        )


class TestRebalanceSuggestion:
    """ρ₁ → 调仓频率建议：IC 半衰期 ln2/−ln ρ₁ 向上取整（T-FE-03 第二列）。"""

    def test_half_life_math(self):
        # rho=0.9 → ln2/0.10536 = 6.58 → 7；rho=0.8 → 3.11 → 4
        assert sc.suggest_rebalance_days(0.9) == 7
        assert sc.suggest_rebalance_days(0.8) == 4
        assert sc.suggest_rebalance_days(0.6) == 2

    def test_decay_within_one_day_means_daily(self):
        # ρ₁ ≤ 0.5 ⇒ 半衰期 ≤ 1 天 ⇒ 建议每日重排（下限 1）
        assert sc.suggest_rebalance_days(0.5) == 1
        assert sc.suggest_rebalance_days(0.25) == 1

    def test_no_persistence_means_daily(self):
        assert sc.suggest_rebalance_days(0.0) == 1
        assert sc.suggest_rebalance_days(-0.3) == 1

    def test_persistence_monotone(self):
        days = [sc.suggest_rebalance_days(r) for r in (0.1, 0.3, 0.6, 0.8)]
        assert days == sorted(days), "越持续 ⇒ 建议频率越低（间隔越长）"

    def test_extreme_persistence_capped(self):
        # ρ→1：半衰期发散——封顶（防御浮点 ρ=1 的除零/溢出），不是 None
        assert sc.suggest_rebalance_days(0.999) == sc.MAX_REBALANCE_DAYS
        assert sc.suggest_rebalance_days(1.0) == sc.MAX_REBALANCE_DAYS

    def test_nonfinite_returns_none_not_one(self):
        """NaN/Inf = 没有自相关证据 ⇒ 不出建议（None），不是「按日频」的 1。"""
        assert sc.suggest_rebalance_days(float("nan")) is None
        assert sc.suggest_rebalance_days(float("inf")) is None


class TestNetIc:
    def test_no_fresh_entries_equals_gross(self):
        """名单稳定的因子：没人新进 ⇒ 无费可扣 ⇒ 净 IC == 毛 IC。"""
        factor = _stable_factor()
        fwd = _series(np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1)) * 0.001)
        gross = ics.summarize_ic(ics.daily_ic(factor, fwd), min_days=5)
        net_mean, n_days = sc.net_rank_ic(factor, fwd, cost_rate=0.002)
        assert n_days == gross.n_days
        assert net_mean == pytest.approx(gross.rank_ic_mean)

    def test_rotation_is_penalized(self):
        """每日整组换血的因子：新进腿被扣费 ⇒ 净 IC 显著低于毛 IC。"""
        factor = _rotating_factor()
        fwd = _series(np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1)) * 0.001)
        gross = ics.summarize_ic(ics.daily_ic(factor, fwd), min_days=5)
        net_mean, n_days = sc.net_rank_ic(factor, fwd, cost_rate=0.002)
        assert n_days >= 5
        assert net_mean < gross.rank_ic_mean - 1e-6, "扣费必须咬得动"

    def test_cost_rate_zero_equals_gross(self):
        factor = _rotating_factor()
        fwd = _series(np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1)) * 0.001)
        gross = ics.summarize_ic(ics.daily_ic(factor, fwd), min_days=5)
        net_mean, _n = sc.net_rank_ic(factor, fwd, cost_rate=0.0)
        assert net_mean == pytest.approx(gross.rank_ic_mean)

    def test_insufficient_days_returns_nan_not_zero(self):
        factor = _stable_factor()
        fwd = _series(np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1)) * 0.001)
        tiny = factor[factor.index.get_level_values(0) < pd.Timestamp("2024-01-05")]
        net_mean, n_days = sc.net_rank_ic(tiny, fwd, cost_rate=0.002, min_days=40)
        assert n_days == 4, "实际天数如实报——审计要能区分「不够算」与「算出来不显著」"
        assert np.isnan(net_mean), "证据不足是 NaN，不是 0"

    def test_cost_rate_defaults_to_single_source(self):
        """不传 cost_rate ⇒ 走 mining_plugins.config.get_cost_rate（一源）。"""
        from backend.services.engine.mining_plugins.config import get_cost_rate

        factor = _stable_factor()
        fwd = _series(np.tile(np.arange(N_SYMBOLS, dtype=float), (N_DAYS, 1)) * 0.001)
        with_default, _ = sc.net_rank_ic(factor, fwd)
        explicit, _ = sc.net_rank_ic(factor, fwd, cost_rate=get_cost_rate())
        assert with_default == pytest.approx(explicit)
