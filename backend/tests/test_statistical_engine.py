"""统计派挖掘引擎（移植 P2 / Tier 1+2）——全流水线闸门契约。

用**合成面板**（收益过程已知）把每条闸门钉死，不碰 qlib / QuantDB：

1. **暖机与区间**：候选 min_history 之前的样本一律不进 IC（面板头部裁掉）；
   取数范围 = 两区间并集（验证区间的数据必须真取到）；区间重叠会标记；
2. **选择链路**：样本内不足 → 拒；BH-FDR → 拒；样本外符号反转 / 衰减过大 →
   拒；过闸者按 |RankIC| 取 Top-K，过闸未入选记「未入选 Top-K」——每个候选
   都有拒因，无静默落榜；
3. **证据**：入选者的分组单调性 τ 与顶底价差必须真算出来（强信号因子 τ→1）；
4. **降级透明**：行业/流通股本元数据缺失时中性化降级为 none/industry，且
   ``neutralize_mode`` 记为**实际生效口径**；FDR 可关。

合成数据：收益过程 ``ret[t+1] = β·ret[t] + ε`` 控制「昨日收益」因子的预测力；
β>0 → 正 IC、<0 → 负 IC；IS 段与 OOS 段可分别设 β（构造样本外符号反转）。
因子 = 昨日收益（面板另附一列 iid 噪声供「无预测力」候选使用）。
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

from backend.services.engine.alpha_agent.statistical import candidates as cand  # noqa: E402
from backend.services.engine.alpha_agent.statistical import engine as eng  # noqa: E402

N_SYMBOLS = 40
N_DAYS = 120
MINING_START_IDX = 5
MINING_END_IDX = 79   # OOS: 80..119
VALID_START_IDX = 80


def _symbols(n: int = N_SYMBOLS) -> list[str]:
    return [f"sh600{i:03d}" for i in range(n)]


SEED = 3  # 噪声因子的 p 值判定在此种子上被钉住（见 test_end_to_end_gates_and_reasons）
FLIP_GAMMA = 0.03  # 日内位置通道的预测系数（IS +、OOS −）


def _make_panel(*, seed: int = SEED) -> pd.DataFrame:
    """一条价格路径、两条正交信息通道（候选只能从 OHLCV 取数，故不能各挂一条价路）：

    - **昨日收益通道**：``ret[t+1] = 0.6·ret[t] + …``——全程一致，供 strong；
    - **日内收盘位置通道**：``ret[t+1] += γ·(pos[t]−0.5)``，γ 在 IS 段 +、OOS 段 −，
      供 flip（位置可取负数预测力）；pos 由 high/low/close 精确反解：
      ``(close−low)/(high−low) = pos``。
    """
    rng = np.random.default_rng(seed)
    symbols = _symbols()
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    eps = rng.normal(0, 0.012, size=(N_DAYS, N_SYMBOLS))
    pos = rng.uniform(0, 1, size=(N_DAYS, N_SYMBOLS))
    noise = rng.normal(size=(N_DAYS, N_SYMBOLS))
    ret = np.zeros_like(eps)
    prev = np.zeros(N_SYMBOLS)
    for t in range(N_DAYS):
        gamma = FLIP_GAMMA if t < VALID_START_IDX else -FLIP_GAMMA
        signal = gamma * (pos[t - 1] - 0.5) if t > 0 else 0.0
        ret[t] = 0.6 * prev + signal + eps[t]
        prev = ret[t]
    close = 20.0 * np.cumprod(1 + ret, axis=0)
    half = close * 0.01 * (1 + rng.random((N_DAYS, N_SYMBOLS)))  # 日内半幅
    low = close - pos * 2 * half
    high = low + 2 * half
    index = pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "symbol"])
    flat = lambda m: np.asarray(m, dtype=float).reshape(-1)  # noqa: E731 展平与 from_product 同序
    return pd.DataFrame(
        {
            "open": flat(close * 0.999),
            "high": flat(high),
            "low": flat(low),
            "close": flat(close),
            "volume": 1e6,
            "amount": 1e8,
            "noise": flat(noise),
        },
        index=index,
    ).sort_index()


def _metadata(panel: pd.DataFrame, *, with_industry: bool = True) -> pd.DataFrame:
    symbols = sorted(panel.index.get_level_values(1).unique())
    data = {
        "symbol_raw": symbols,
        "name": symbols,
        "listing_date": pd.Timestamp("2001-01-01"),
        "is_st": False,
        "float_shares": 1e5,
    }
    if with_industry:
        data["industry"] = ["银行" if i % 2 else "白酒" for i in range(len(symbols))]
    return pd.DataFrame(data, index=pd.Index([s[2:] for s in symbols], name="symbol_digits"))


def _yesterday_return(frame: pd.DataFrame) -> pd.Series:
    return frame["close"] / frame["close"].shift(1) - 1


def _intraday_position(frame: pd.DataFrame) -> pd.Series:
    """日内收盘位置：样本内正 IC、样本外反转（面板的 flip 通道）。"""
    return (frame["close"] - frame["low"]) / (frame["high"] - frame["low"])


def _noise(frame: pd.DataFrame) -> pd.Series:
    return frame["noise"]


def _all_nan(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(np.nan, index=frame.index)


def _fake(key: str, compute, *, min_history: int = 2) -> cand.Candidate:
    return cand.Candidate(
        key=key,
        name=key,
        family="reversal",
        formulation=f"{key} (测试用)",
        source="test",
        expression="$close",
        min_history=min_history,
        compute=compute,
    )


FAKE_CANDIDATES = (
    _fake("strong", _yesterday_return),
    _fake("flip", _intraday_position),
    _fake("weak", _noise),
    _fake("no_history", _yesterday_return, min_history=9999),
    _fake("dead", _all_nan),
)


@pytest.fixture
def fake_registry(monkeypatch):
    """把引擎的候选集换成 5 个受控候选（真实 32 个由 candidates 测试覆盖）。"""
    monkeypatch.setattr(cand, "CANDIDATES", FAKE_CANDIDATES)
    monkeypatch.setattr(
        cand, "CANDIDATE_BY_KEY", {c.key: c for c in FAKE_CANDIDATES}
    )
    return FAKE_CANDIDATES


def _window_names():
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    fmt = "%Y-%m-%d"
    return (
        dates[MINING_START_IDX].strftime(fmt),
        dates[MINING_END_IDX].strftime(fmt),
        dates[VALID_START_IDX].strftime(fmt),
        dates[N_DAYS - 1].strftime(fmt),
    )


def _cfg(**overrides) -> eng.StatisticalConfig:
    """合成面板很小：门槛按比例收（真实默认值由 StatisticalConfig 自己持有）。"""
    base = {
        "top_k": 1, "horizons": (1, 5), "min_valid_days": 5, "oos_min_days": 5,
        "min_names_per_day": 5,
    }
    base.update(overrides)
    return eng.StatisticalConfig(**base)


def _run(panel, metadata, cfg=None, *, with_oos: bool = True, **kw):
    mining_start, mining_end, valid_start, valid_end = _window_names()
    windows = {"valid_start": valid_start, "valid_end": valid_end} if with_oos else {}
    return eng.run_statistical_mining(
        mining_start, mining_end, cfg=cfg or _cfg(),
        price_panel=panel, metadata=metadata, **windows, **kw,
    )


def _report(result, key) -> eng.CandidateReport:
    return next(r for r in result.candidates if r.key == key)


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"horizons": ()}, "horizons"),
            ({"horizons": (0, 5)}, "horizons"),
            ({"top_k": 0}, "top_k"),
            ({"quantiles": 1}, "quantiles"),
            ({"neutralize": "size"}, "neutralize"),
            ({"min_fold_sign_consistency": 0.0}, "min_fold_sign_consistency"),
            ({"min_fold_sign_consistency": 1.5}, "min_fold_sign_consistency"),
        ],
    )
    def test_bad_config_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            eng.StatisticalConfig(**kwargs)


class TestSelectionGates:
    def test_end_to_end_gates_and_reasons(self, fake_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel))

        assert result.selected == ("strong",), "只有全程一致的强信号入选"
        assert _report(result, "strong").selected is True
        assert "符号反转" in _report(result, "flip").reject_reason
        assert "BH-FDR" in _report(result, "weak").reject_reason
        assert _report(result, "no_history").reject_reason == "样本内有效日不足"
        assert _report(result, "dead").reject_reason == "样本内有效日不足"
        assert all(r.reject_reason for r in result.candidates if not r.selected), (
            "未入选者必须有拒因，不许静默落榜"
        )

    def test_selected_has_quantile_evidence(self, fake_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel))
        strong = _report(result, "strong")
        assert strong.quantile_monotonicity > 0.8, "强信号的分组收益应单调"
        assert strong.spread_mean > 0, "高分组收益应高于低分组"
        assert strong.oos_verified is True and strong.oos_horizon == 1
        assert abs(_report(result, "weak").oos_rank_ic) < 0.2, "噪声因子样本外仍是噪声"

    def test_fdr_switch_decides_weak_candidate(self, fake_registry):
        """同一份数据只拨 FDR 开关：无预测力候选的去留必须跟着变。

        关掉样本外闸（不配验证区间）是因为噪声因子的样本外符号本就一半概率翻——
        那是闸门**该**拦的，会盖住这里要单独验的 FDR 变量。稳定性闸同理关掉：
        噪声因子的折间符号本就近抛硬币（本种子 2/4 折同号），新闸取折间一致性，
        会先把 weak 拒掉，同样盖住 FDR 变量（Tier 3 闸门行为由 tier3 专项钉住）。
        """
        panel = _make_panel()
        off = _run(panel, _metadata(panel),
                   cfg=_cfg(top_k=3, horizons=(1,), require_fdr=False, require_stability=False),
                   with_oos=False)
        assert "weak" in off.selected, "FDR 关掉后无预测力候选也过闸（Top-K 内）"
        assert "BH-FDR" not in _report(off, "weak").reject_reason

        on = _run(panel, _metadata(panel),
                  cfg=_cfg(top_k=3, horizons=(1,), require_stability=False),
                  with_oos=False)
        assert "weak" not in on.selected
        assert "BH-FDR" in _report(on, "weak").reject_reason

    def test_top_k_overflow_gets_reason(self, fake_registry):
        """本测试只验 Top-K 溢出拒因：稳定性闸单独关掉（Tier 3 闸门的行为
        由 test_statistical_engine_tier3.py 钉；开着会让噪声候选先得稳定性
        拒因，盖住这里要验的「未入选 Top-K」）。"""
        panel = _make_panel()
        cfg = _cfg(top_k=1, horizons=(1,), require_fdr=False, require_stability=False)
        result = _run(panel, _metadata(panel), cfg=cfg, with_oos=False)
        assert result.selected == ("strong",), "|RankIC| 更大者拿唯一席位"
        assert "未入选 Top-K" in _report(result, "weak").reject_reason
        assert "未入选 Top-K" in _report(result, "flip").reject_reason
        assert _report(result, "weak").best_horizon is not None, "落榜也要留已算出的统计量"

    def test_no_validation_window_skips_oos_gate(self, fake_registry):
        panel = _make_panel()
        result = _run(
            panel, _metadata(panel),
            cfg=_cfg(top_k=1, horizons=(1,), require_stability=False),
            with_oos=False,
        )
        assert result.selected == ("strong",)
        assert not any(r.oos_verified for r in result.candidates)
        flip = _report(result, "flip")
        assert flip.selected is False and "符号反转" not in flip.reject_reason, (
            "未配验证区间时不该拿样本外符号说事（它过闸，只是输给 |RankIC|）"
        )


class TestWarmupAndWindows:
    def test_min_history_before_mining_start_excluded(self, fake_registry):
        # min_history=9999 → 没有任何样本可用 → 样本内有效日不足
        panel = _make_panel()
        result = _run(panel, _metadata(panel))
        assert _report(result, "no_history").horizons[0].n_days == 0

    def test_fetch_span_covers_validation_window(self, fake_registry, monkeypatch):
        seen = {}

        def loader(instruments, fields, start, end):
            seen.update(start=start, end=end)
            return _empty_qlib_frame()

        # 标的池也走替身：本测试只验取数区间，不碰 qlib 磁盘；候选收成 1 个
        # 只读标准列的（替身 loader 的帧里没有 test 专用列）
        monkeypatch.setattr(
            eng._panel, "qlib_instruments",
            lambda market="all", qlib_dir=None: ["sh600000"],
        )
        plain = _fake("plain", _yesterday_return)
        monkeypatch.setattr(cand, "CANDIDATES", (plain,))
        monkeypatch.setattr(cand, "CANDIDATE_BY_KEY", {"plain": plain})

        mining_start, mining_end, valid_start, valid_end = _window_names()
        eng.run_statistical_mining(
            mining_start, mining_end, valid_start=valid_start, valid_end=valid_end,
            cfg=_cfg(top_k=1, horizons=(1,)),
            metadata=_metadata(_make_panel()), loader=loader,
            calendar=pd.bdate_range("2022-06-01", periods=400),
        )
        assert seen["end"] == valid_end, "验证区间止日必须取到（否则样本外闸整批误杀）"
        assert seen["start"] < mining_start, "起点按 MAX_MIN_HISTORY 交易日向前推"

    def test_universe_reaches_panel_and_report(self, fake_registry, monkeypatch):
        """任务选了哪个池子就真在哪个池子上算：universe → 面板 market，
        并原样进审计报告（落库行/报告不许拿请求参数冒充实际口径）。"""
        seen = {}

        def fake_panel(start, end, **kw):
            seen.update(start=start, end=end, **kw)
            return _make_panel()

        monkeypatch.setattr(eng._panel, "load_price_panel", fake_panel)
        panel = _make_panel()
        mining_start, mining_end, *_ = _window_names()

        result = eng.run_statistical_mining(
            mining_start, mining_end, cfg=_cfg(horizons=(1,)),
            metadata=_metadata(panel), universe="csi300",
        )

        assert seen["market"] == "csi300", "universe 必须传到取数层，不是只记一笔"
        assert result.universe == "csi300"

    def test_blank_universe_falls_back_to_all_a(self, fake_registry, monkeypatch):
        seen = {}

        def fake_panel(start, end, **kw):
            seen.update(**kw)
            return _make_panel()

        monkeypatch.setattr(eng._panel, "load_price_panel", fake_panel)
        panel = _make_panel()
        mining_start, mining_end, *_ = _window_names()

        result = eng.run_statistical_mining(
            mining_start, mining_end, cfg=_cfg(horizons=(1,)),
            metadata=_metadata(panel), universe="   ",
        )

        assert seen["market"] == "all_a"
        assert result.universe == "all_a"

    def test_overlap_flag_and_warning(self, fake_registry):
        panel = _make_panel()
        dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
        notes = []
        mining_start, mining_end, _, valid_end = _window_names()
        result = eng.run_statistical_mining(
            mining_start, mining_end,
            valid_start=dates[40].strftime("%Y-%m-%d"), valid_end=valid_end,
            cfg=_cfg(top_k=1, horizons=(1,)),
            price_panel=panel, metadata=_metadata(panel),
            progress=lambda stage, detail: notes.append(detail),
        )
        assert result.window["validation_overlaps_mining"] is True
        assert any("重叠" in note for note in notes)

    def test_window_and_shape_recorded(self, fake_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel))
        assert result.panel_rows == len(panel)
        assert result.n_symbols == N_SYMBOLS
        assert result.window["mining_start"][:4] == "2023"
        assert result.neutralize_mode == "industry"
        assert result.neutralize_report is not None


class TestNeutralizeDowngrade:
    def _capture(self):
        notes = []
        return notes, lambda stage, detail: notes.append(detail)

    def test_missing_industry_degrades_to_none(self, fake_registry):
        panel = _make_panel()
        notes, emit = self._capture()
        result = _run(panel, _metadata(panel, with_industry=False), progress=emit)
        assert result.neutralize_mode == "none"
        assert result.neutralize_report is None
        assert any("降级" in note for note in notes), "降级必须出声"

    def test_industry_size_without_shares_degrades_to_industry(self, fake_registry):
        panel = _make_panel()
        meta = _metadata(panel).drop(columns=["float_shares"])
        notes, emit = self._capture()
        result = _run(panel, meta, cfg=_cfg(neutralize="industry_size"), progress=emit)
        assert result.neutralize_mode == "industry"
        assert any("降级" in note for note in notes)

    def test_industry_size_applied_when_available(self, fake_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(neutralize="industry_size"))
        assert result.neutralize_mode == "industry_size"
        assert result.selected == ("strong",), "强信号在中性化后仍应胜出"


class TestRealRegistrySmoke:
    def test_all_32_candidates_reported(self, monkeypatch):
        """真实 32 候选小面板冒烟：全量报告 + 拒因完备 + 不抛异常。"""
        symbols = _symbols(12)
        dates = pd.bdate_range("2023-01-02", periods=300)
        rng = np.random.default_rng(11)
        ret = rng.normal(0, 0.012, size=(len(dates), len(symbols)))
        close = 20.0 * np.cumprod(1 + ret, axis=0)
        index = pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "symbol"])
        flat = lambda m: np.asarray(m, dtype=float).reshape(-1)  # noqa: E731
        panel = pd.DataFrame(
            {
                "open": flat(close * 0.999), "high": flat(close * 1.01),
                "low": flat(close * 0.99), "close": flat(close),
                "volume": 1e6, "amount": 1e8,
            },
            index=index,
        ).sort_index()
        meta = _metadata(panel)
        result = eng.run_statistical_mining(
            dates[255].strftime("%Y-%m-%d"), dates[269].strftime("%Y-%m-%d"),
            valid_start=dates[270].strftime("%Y-%m-%d"), valid_end=dates[299].strftime("%Y-%m-%d"),
            cfg=_cfg(top_k=3, min_valid_days=3, oos_min_days=3),
            price_panel=panel, metadata=meta,
        )
        assert len(result.candidates) == len(cand.CANDIDATES) == 32
        assert {r.key for r in result.candidates} == {c.key for c in cand.CANDIDATES}
        assert set(result.selected) <= {r.key for r in result.candidates}
        for report in result.candidates:
            assert report.reject_reason or report.selected, f"{report.key} 无交代"
        assert result.filter_report.n_kept > 0


# ── 取数替身 ─────────────────────────────────────────────────────────


def _empty_qlib_frame() -> pd.DataFrame:
    """qlib 风格空帧（loader 替身用：只验参数，不看内容）。"""
    idx = pd.MultiIndex.from_product(
        [["sh600000"], pd.DatetimeIndex(["2023-01-02"])], names=["instrument", "datetime"]
    )
    return pd.DataFrame(
        {f"${c}": [1.0] for c in ("open", "high", "low", "close", "volume", "amount")},
        index=idx,
    )
