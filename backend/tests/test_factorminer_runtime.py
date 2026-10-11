"""FM 生产运行时（``factorminer/runtime.py``）契约测试。

runner 的取数/组帧管线收敛到此模块（因子池 ``fm:`` 表达式走同一实现），
这里在 venv 离线（注入假日历/假 loader，与 ``test_paper_factor_runner``
同一套注入约定）钉四件事：

- 取数窗口 = 窗口起点回退 ``min_history`` 根（loader 反查 fetch_start）——
  窗口内滚动窗全部满窗，与 qlib 表达式路径的自动扩史等价；
- 输出 = 窗口内**全网格**长序列（含窗口中新上市标的的行），且暖机不足的
  格置 NaN（FM 求值器 min_periods=1，不掩会让新标的头部提前出值）；
- vwap 口径自适应（×factor / 裸值 / 并列取 ×factor）与覆盖率下限
  （``VwapConventionError``——runner 翻成 SystemExit 的那条防线）；
- 空取数抛专门「取数为空」（先于 vwap 覆盖率检查——否则「没取到数」
  会被误诊成「单位口径有问题」）；
- ``$returns`` 走长面板价差法（首根 NaN，停牌缺口=整段收益）。
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

from backend.services.engine.alpha_agent.factorminer import runtime  # noqa: E402
from backend.services.engine.alpha_agent.statistical import (  # noqa: E402
    panel as panel_mod,
)

N_DAYS = 140
SYMBOLS = ["sh600001", "sh600002", "sh600003", "sh600004"]
NEW_SYMBOL = "sh600004"  # 窗口内（第 80 根）才上市
NEW_LIST_IDX = 80
CAL = pd.bdate_range("2023-01-02", periods=N_DAYS)
WIN_START_IDX, WIN_END_IDX = 50, 109
START = CAL[WIN_START_IDX].strftime("%Y-%m-%d")
END = CAL[WIN_END_IDX].strftime("%Y-%m-%d")
N_SYMBOLS = len(SYMBOLS)


def _synthetic_raw(factor_const: float = 1.0, amount_mult: float = 1.0) -> pd.DataFrame:
    """qlib 取数形状的合成行情（vwap 候选恰在 [low, high] 内，可调倍率）。

    amount = 中价×volume/1e4×mult ⇒ vm = 中价×mult；factor 常数可让
    ``vm×factor`` 与裸 ``vm`` 的覆盖率按测试意图翻转。
    """
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (N_DAYS, N_SYMBOLS)), axis=0))
    open_ = close * (1 + rng.normal(0, 0.002, close.shape))
    high = np.maximum(open_, close) * 1.003
    low = np.minimum(open_, close) * 0.997
    volume = np.full(close.shape, 1e6)
    amount = ((high + low) / 2) * volume / 1e4 * amount_mult
    factor = np.full(close.shape, factor_const)
    index = pd.MultiIndex.from_product([SYMBOLS, CAL], names=["instrument", "datetime"])
    # 注意 ravel 顺序：from_product 是标的优先，字段数组 (天, 标的) 行优先 →
    # 必须 .T.ravel() 才逐标对齐（同 test_paper_factor_runner 的埋点说明）
    raw = pd.DataFrame(
        {
            "$open": open_.T.ravel(),
            "$high": high.T.ravel(),
            "$low": low.T.ravel(),
            "$close": close.T.ravel(),
            "$volume": volume.T.ravel(),
            "$amount": amount.T.ravel(),
            "$factor": factor.T.ravel(),
        },
        index=index,
    )
    # 窗口中新上市：上市日前无行（qlib 缺行语义）
    date_level = raw.index.get_level_values(1)
    inst_level = raw.index.get_level_values(0)
    return raw[~((inst_level == NEW_SYMBOL) & (date_level < CAL[NEW_LIST_IDX]))]


def _make_loader(raw: pd.DataFrame):
    seen: dict = {}

    def loader(instruments, fields, start, end):
        seen.update(
            instruments=list(instruments),
            fields=list(fields),
            start=start,
            end=end,
        )
        dates = raw.index.get_level_values(1)
        return raw[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]

    return loader, seen


def _calendar_fn(start: str, end: str) -> pd.DatetimeIndex:
    return CAL[(CAL >= pd.Timestamp(start)) & (CAL <= pd.Timestamp(end))]


class TestEvaluateWindow:
    def test_fetch_start_backs_off_min_history(self):
        raw = _synthetic_raw()
        loader, seen = _make_loader(raw)

        runtime.evaluate_window(
            "Mean($close, 5)",
            instruments=SYMBOLS,
            start=START,
            end=END,
            loader=loader,
            calendar_fn=_calendar_fn,
        )

        # Mean($close, 5) 的 min_history = 6 → 取数起点 = 窗口首根回退 6 根
        assert seen["start"] == CAL[WIN_START_IDX - 6].strftime("%Y-%m-%d")
        assert seen["end"] == END
        assert seen["fields"] == [*panel_mod.PRICE_FIELDS, "$factor"]
        assert seen["instruments"] == SYMBOLS

    def test_full_grid_with_warmup_mask_on_new_listing(self):
        raw = _synthetic_raw()
        loader, _seen = _make_loader(raw)

        out = runtime.evaluate_window(
            "Mean($close, 5)",
            instruments=SYMBOLS,
            start=START,
            end=END,
            loader=loader,
            calendar_fn=_calendar_fn,
        )

        assert out.index.names == ["trade_date", "symbol"]
        window_days = WIN_END_IDX - WIN_START_IDX + 1
        # 旧标的：窗口首日 position 恰 = 暖机 6 → 全窗口每格有值
        for sym in SYMBOLS:
            if sym == NEW_SYMBOL:
                continue
            block = out.xs(sym, level="symbol")
            assert len(block) == window_days
            assert block.notna().all()
        # 新上市标的：自上市日起进网格，position<6 的 6 格 NaN、之后有值
        block = out.xs(NEW_SYMBOL, level="symbol")
        assert len(block) == WIN_END_IDX - NEW_LIST_IDX + 1
        values = block.to_numpy()
        assert np.isnan(values[:6]).all()
        assert not np.isnan(values[6:]).any()
        assert len(out) == window_days * (N_SYMBOLS - 1) + (
            WIN_END_IDX - NEW_LIST_IDX + 1
        )

    def test_fm_prefix_stripped_same_result(self):
        raw = _synthetic_raw()
        loader_a, _ = _make_loader(raw)
        loader_b, _ = _make_loader(raw)

        plain = runtime.evaluate_window(
            "Mean($close, 5)",
            instruments=SYMBOLS,
            start=START,
            end=END,
            loader=loader_a,
            calendar_fn=_calendar_fn,
        )
        prefixed = runtime.evaluate_window(
            "fm:Mean($close, 5)",
            instruments=SYMBOLS,
            start=START,
            end=END,
            loader=loader_b,
            calendar_fn=_calendar_fn,
        )

        pd.testing.assert_series_equal(plain, prefixed)

    def test_values_match_field_arithmetic(self):
        raw = _synthetic_raw()
        loader, _seen = _make_loader(raw)

        out = runtime.evaluate_window(
            "Neg($close)",
            instruments=SYMBOLS,
            start=START,
            end=END,
            loader=loader,
            calendar_fn=_calendar_fn,
        )

        close = panel_mod.normalize_panel(raw)["close"]
        expected = (-close).reindex(out.index)
        joined = pd.DataFrame({"out": out, "exp": expected}).dropna()
        assert len(joined) > 150
        np.testing.assert_allclose(
            joined["out"].to_numpy(), joined["exp"].to_numpy(), rtol=1e-12, atol=1e-9
        )

    def test_vwap_guard_raises(self):
        raw = _synthetic_raw(amount_mult=1e3)  # 单位口径错：vwap 整体出界
        loader, _seen = _make_loader(raw)

        with pytest.raises(runtime.VwapConventionError, match="vwap"):
            runtime.evaluate_window(
                "Mean($close, 5)",
                instruments=SYMBOLS,
                start=START,
                end=END,
                loader=loader,
                calendar_fn=_calendar_fn,
            )

    def test_empty_instruments_raises(self):
        with pytest.raises(ValueError, match="标的池为空"):
            runtime.evaluate_window(
                "Mean($close, 5)",
                instruments=[],
                start=START,
                end=END,
                loader=lambda *a: pd.DataFrame(),
                calendar_fn=_calendar_fn,
            )

    def test_empty_fetch_raises_before_vwap_misdiagnosis(self):
        # 取数整片为空（标的池形态错等）：老实现会滑到 vwap 覆盖率检查，
        # 报「单位口径有问题」——把「没取到数」误诊成 vwap 错。必须先抛
        # 专门的「取数为空」，且不能是 VwapConventionError。
        with pytest.raises(ValueError, match="取数为空") as excinfo:
            runtime.evaluate_window(
                "Mean($close, 5)",
                instruments=SYMBOLS,
                start=START,
                end=END,
                loader=lambda *a: pd.DataFrame(),
                calendar_fn=_calendar_fn,
            )
        assert not isinstance(excinfo.value, runtime.VwapConventionError)

    def test_reversed_window_raises(self):
        with pytest.raises(ValueError, match="窗口颠倒"):
            runtime.evaluate_window(
                "Mean($close, 5)",
                instruments=SYMBOLS,
                start=END,
                end=START,
                loader=lambda *a: pd.DataFrame(),
                calendar_fn=_calendar_fn,
            )


class TestBuildFieldFrames:
    @staticmethod
    def _panel_and_factor(factor_const: float, amount_mult: float):
        raw = _synthetic_raw(factor_const=factor_const, amount_mult=amount_mult)
        price = panel_mod.normalize_panel(raw)
        factor = runtime.normalize_single(raw, "$factor")
        return price, factor

    def test_convention_raw_when_factor_out_of_range(self):
        # vm 恰为中价（在线），factor=2 → vm×factor 出界 → 选裸值
        price, factor = self._panel_and_factor(factor_const=2.0, amount_mult=1.0)
        _frames, info = runtime.build_field_frames(price, factor)
        assert info["vwap_convention"] == "amount/volume"
        assert info["vwap_coverage"] >= runtime.VWAP_COVERAGE_FLOOR

    def test_convention_factor_when_raw_out_of_range(self):
        # vm = 中价×2（裸值出界），factor=0.5 → ×factor 回到中价 → 选 ×factor
        price, factor = self._panel_and_factor(factor_const=0.5, amount_mult=2.0)
        _frames, info = runtime.build_field_frames(price, factor)
        assert info["vwap_convention"] == "amount/volume×$factor"
        assert info["vwap_coverage_with_factor"] >= runtime.VWAP_COVERAGE_FLOOR

    def test_convention_tie_picks_factor_side(self):
        price, factor = self._panel_and_factor(factor_const=1.0, amount_mult=1.0)
        _frames, info = runtime.build_field_frames(price, factor)
        assert info["vwap_convention"] == "amount/volume×$factor"

    def test_vwap_floor_violation_raises(self):
        price, factor = self._panel_and_factor(factor_const=1.0, amount_mult=1e3)
        with pytest.raises(runtime.VwapConventionError, match="覆盖率过低"):
            runtime.build_field_frames(price, factor)

    def test_returns_uses_long_gap_semantics(self):
        price, factor = self._panel_and_factor(factor_const=1.0, amount_mult=1.0)
        frames, _info = runtime.build_field_frames(price, factor)
        returns = frames["$returns"]
        close = frames["$close"]
        # 首根全 NaN（无前收）；第二根 = close_1/close_0 − 1
        assert returns.iloc[0].isna().all()
        expected = close.iloc[1] / close.iloc[0] - 1.0
        pd.testing.assert_series_equal(returns.iloc[1], expected, check_names=False)
