"""T-FM-01 runner（``scripts/alpha_agent/run_paper_factor_test.py``）冒烟测试。

runner 本体在容器里对真 qlib 数据跑（csi300 × 4 年）；这里在 venv 离线跑
**全流程结构**：注入假日历/假标的池/假 loader（与 ``test_statistical_panel``
同一套注入约定），喂合成面板，钉住：

- 取数字段 = 6 行情字段 + ``$factor``（一次取齐）、起点经 ``warmup_start``；
- 报告结构：110 论文因子 + 32 仓库候选、两侧去重段、跨库相关段、
  算子谱 39 个、复杂度分布；
- ``json_safe`` 之后是**严格 JSON**（不许裸 NaN/Infinity——Postgres json 列
  与 JSON.parse 都拒收）；
- vwap 单位守卫：amount 比例错 → ``SystemExit``（vwap 类 34 条因子静默
  失真的防线）；
- ``_selection_score`` 与引擎同式（折结构不足不加权）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import (  # noqa: E402
    panel as pn,
)
from backend.services.engine.alpha_agent.statistical.serialize import json_safe  # noqa: E402

RUNNER_PATH = ROOT / "scripts" / "alpha_agent" / "run_paper_factor_test.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("_runner_under_test", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


N_DAYS = 320  # > 仓库候选 MAX_MIN_HISTORY(250)，保证两侧都有可评估因子
SYMBOLS = [f"sh6000{i:02d}" for i in range(1, 13)]  # 12 只 > 单日最少 10 只
CAL = pd.bdate_range("2023-01-02", periods=N_DAYS)
START, END = CAL[0].strftime("%Y-%m-%d"), CAL[-1].strftime("%Y-%m-%d")

#: 掩码金样的两个埋点：一字板日（close/prev−1=+50% ≥ 10%−0.002 → 剔除）与
#: 停牌日（volume=0 → 剔除）。去掉 trade mask（如漏接 keep_common）时这两个
#: 格子回流量池 → 金样 RankIC/n_days 整体移动，测试即红。
JUMP_DAY = N_DAYS - 40  # sh600001（第 0 列）单日 +50%
SUSPEND_DAY = N_DAYS - 36  # sh600002（第 1 列）volume=0


def _synthetic_raw() -> pd.DataFrame:
    """qlib 取数形状的合成行情：vwap 恰在 [low, high] 内（覆盖率 1.0）。"""
    rng = np.random.default_rng(11)
    n_cols = len(SYMBOLS)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, (N_DAYS, n_cols)), axis=0))
    close[JUMP_DAY:, 0] *= 1.5  # 跳变当日触发涨跌停锁定；此后恢复随机游走
    open_ = close * (1 + rng.normal(0, 0.003, close.shape))
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = np.full(close.shape, 1e6)
    volume[SUSPEND_DAY, 1] = 0.0  # 停牌一日
    amount = ((high + low) / 2) * volume / 1e4  # vm = amount×1e4/volume = 中位价
    factor = np.ones(close.shape)
    index = pd.MultiIndex.from_product([SYMBOLS, CAL], names=["instrument", "datetime"])
    # 注意 ravel 顺序：from_product([SYMBOLS, CAL]) 是标的优先（位置 = 标的i×N天+日j），
    # 而字段数组是 (天, 标的) 行优先——必须 .T.ravel() 才逐标对齐；直拍会得到
    # 「每个标的=12 条随机游走拼接」的乱序面板（曾在结构断言下静默通过）
    return pd.DataFrame(
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


@pytest.fixture()
def runner(monkeypatch):
    module = _load_runner()
    monkeypatch.setattr(pn, "qlib_calendar", lambda start, end, *, qlib_dir=None: CAL)
    monkeypatch.setattr(
        pn, "qlib_instruments", lambda market="all", *, qlib_dir=None: list(SYMBOLS)
    )
    return module


def _load_bundle(mod):
    seen: dict = {}

    def loader(instruments, fields, start, end):
        seen.update(instruments=instruments, fields=fields, start=start, end=end)
        return _synthetic_raw()

    price, factor, fetch_start = mod.load_bundle(
        START, END, market="csi300", warmup_bars=74, loader=loader
    )
    return price, factor, fetch_start, seen


@pytest.fixture(scope="module")
def report():
    """跑一次全流程，结构测试与数值金样共用（module 作用域，~25s 只跑一次）。"""
    module = _load_runner()
    # module 作用域拿不到 monkeypatch fixture，直接原地打补丁并在结束时还原
    original = (pn.qlib_calendar, pn.qlib_instruments)
    pn.qlib_calendar = lambda start, end, *, qlib_dir=None: CAL
    pn.qlib_instruments = lambda market="all", *, qlib_dir=None: list(SYMBOLS)
    try:
        price, factor, fetch_start, _seen = _load_bundle(module)
        return module.run_paper_test(
            price,
            factor,
            start=START,
            end=END,
            universe="csi300",
            horizons=(1, 5),
            folds=4,
            min_fold_days=5,
            fetch_start=fetch_start.strftime("%Y-%m-%d"),
            warmup_bars=74,
        )
    finally:
        pn.qlib_calendar, pn.qlib_instruments = original


class TestLoadBundle:
    def test_fields_and_warmup_start(self, runner):
        _price, factor, fetch_start, seen = _load_bundle(runner)
        assert seen["fields"] == [*pn.PRICE_FIELDS, "$factor"]  # 一次取齐
        assert seen["start"] == CAL[0].strftime("%Y-%m-%d")
        assert seen["end"] == END
        assert seen["instruments"] == SYMBOLS
        assert fetch_start == CAL[0]  # 日历从窗口起点取 → warmup 自然钳位
        assert factor.notna().all()


class TestRunPaperTestSmoke:
    def test_paper_side_is_110_with_expression_and_complexity(self, report):
        factors = report["paper"]["factors"]
        assert len(factors) == 110
        assert {f["key"] for f in factors} == {f"fm{i:03d}" for i in range(1, 111)}
        for item in factors:
            assert item["expression"]
            assert item["complexity"]["node_count"] >= 1
            assert item["min_history"] >= 1

    def test_house_side_is_32_with_metadata(self, report):
        factors = report["house"]["factors"]
        assert len(factors) == 32
        for item in factors:
            assert item["name"] and item["expression"]

    def test_both_dedup_sections_present(self, report):
        for side in ("paper", "house"):
            dedup = report[side]["dedup"]
            assert isinstance(dedup["kept"], list)
            assert isinstance(dedup["dropped"], list)
            assert len(dedup["kept"]) <= dedup["top_k"]

    def test_cross_corr_covers_all_paper_factors(self, report):
        cross = report["cross"]
        assert len(cross["per_paper"]) == 110
        assert all(
            set(info) == {"rho", "against"} for info in cross["per_paper"].values()
        )

    def test_vwap_convention_recorded_and_inside_range(self, report):
        assert report["vwap_coverage"] >= 0.99
        assert "amount/volume" in report["vwap_convention"]

    def test_operator_spectrum_has_all_39(self, report):
        assert len(report["operator_spectrum"]) == 39

    def test_serialized_report_is_strict_json(self, report):
        text = json.dumps(json_safe(report), ensure_ascii=False)
        assert "NaN" not in text
        assert "Infinity" not in text
        assert "-Infinity" not in text
        loaded = json.loads(text)
        assert loaded["task"] == "T-FM-01"
        assert len(loaded["paper"]["factors"]) == 110

    def test_markdown_renders(self, report):
        module = _load_runner()
        md = module.render_markdown(json_safe(report))
        assert md.startswith("# T-FM-01")
        assert "论文侧 110 因子" in md
        assert "仓库侧 32 候选" in md


class TestVwapGuard:
    def test_unit_mismatch_raises_system_exit(self, runner):
        price, factor, _fetch, _seen = _load_bundle(runner)
        bad = price.copy()
        bad["amount"] = bad["amount"] * 1e3  # 单位口径错：vwap 整体出界
        with pytest.raises(SystemExit, match="vwap"):
            runner.run_paper_test(
                bad,
                factor,
                start=START,
                end=END,
                universe="csi300",
                horizons=(1,),
                folds=2,
                min_fold_days=3,
            )


class TestSelectionScore:
    def test_weighted_by_sign_consistency_only_when_eligible(self, runner):
        best = {"rank_ic_mean": 0.04}
        assert runner._selection_score(best, 0.5, eligible=True) == pytest.approx(0.02)
        # 折结构不足 → 不加权（没有证据不罚，也不假装稳定）
        assert runner._selection_score(best, 0.5, eligible=False) == pytest.approx(0.04)
        assert runner._selection_score(
            best, float("nan"), eligible=True
        ) == pytest.approx(0.04)
        assert np.isnan(runner._selection_score(None, 1.0, eligible=True))


class TestNumericGolden:
    """数值金样：掩码 → 因子 → 前向收益 → 日 IC 整链的锚（评审 F7）。

    合成面板埋了 1 个一字板格 + 1 个停牌格（见 ``_synthetic_raw``）。断线对照：

    - 漏接 ``keep_common``（tradability 掩码）→ 两个埋点格回流量池，RankIC 位移；
    - 漏接 ``position >= min_history`` → 暖机日（滚动算子 min_periods=1 早给值）
      进池 → ``n_days`` 大幅上移；
    - ``fwd``/日 IC 索引对齐漂移 → 两者同时位移。

    改口径或改合成数据必须连本表按意图重签（与 researchScore 金样同纪律）。
    """

    def _by_horizon(self, report, side, key):
        item = next(f for f in report[side]["factors"] if f["key"] == key)
        return {h["horizon"]: h for h in item["horizons"]}

    def test_tradability_mask_bites_in_synthetic(self, report):
        filt = report["tradability"]["filter"]
        assert filt["n_suspended"] == 1  # sh600002 停牌一日
        assert filt["n_limit_locked"] == 1  # sh600001 一字板 +50%
        assert filt["n_kept"] == 3838  # 320 天 × 12 只 − 2

    def test_fm001_golden(self, report):
        by_h = self._by_horizon(report, "paper", "fm001")
        assert by_h[1]["n_days"] == 270
        assert by_h[1]["rank_ic_mean"] == pytest.approx(-0.039735530744824665, rel=1e-4)
        assert by_h[5]["n_days"] == 266
        assert by_h[5]["rank_ic_mean"] == pytest.approx(-0.06675136470588297, rel=1e-4)

    def test_fm009_golden(self, report):
        by_h = self._by_horizon(report, "paper", "fm009")
        assert by_h[1]["n_days"] == 305
        assert by_h[1]["rank_ic_mean"] == pytest.approx(0.008896736882089118, rel=1e-4)
        assert by_h[5]["n_days"] == 301
        assert by_h[5]["rank_ic_mean"] == pytest.approx(
            -0.0074873834582919584, rel=1e-4
        )

    def test_house_momentum_120d_golden(self, report):
        by_h = self._by_horizon(report, "house", "momentum_120d")
        assert by_h[1]["n_days"] == 199
        assert by_h[1]["rank_ic_mean"] == pytest.approx(-0.027114593948764806, rel=1e-4)
