"""统计派挖掘引擎 · Tier 3 体系化（滚动再选择 / 相关性去重 / IC 加权合成）。

用**可控收益通道**的合成面板把三条机制钉在行为上（不碰 qlib / QuantDB）：

- **昨日收益通道**（β 弱而稳，全程）：供「一路都强」的候选（early / clone）；
- **日内位置通道**（γ 强而短，恰好只在最后一折激活）：供「只有窗尾一段强」的
  候选（late）——它的整窗 |RankIC| 反而最大，正是滚动再选择要拦的形态。

钉死的契约：

1. **滚动再选择闸**：late 在检查点 2/3 的累计 |IC|≈0（命中率塌到 1/3）→
   拒（``滚动再选择不稳定``）；把闸关掉 late 立刻当选（最强）——行为随开关
   翻转，闸不是装饰。early 全程稳定 → 命中率 ≥ 阈值 → 当选；
2. **证据诚实**：late 的逐折 IC 末折强于首折（证据与判决对得上）；闸关闭时
   稳定性字段如实留 NaN/空（不伪造 0）；
3. **相关性去重**：秩完全同序的两个候选（|ρ|≈1）在 Top-K 内被去重挤掉后者，
   拒因写明「与谁相关」；``result.dedup`` 全量记账；不补位；
4. **IC 加权合成**：去重关掉后两个同信号分量 → 合成方案（权重各半、单行
   表达式、min_history 含 z 窗）；报告经 serialize 后严格 JSON 可序列化
   （NaN → null），result.json 面不因裸 NaN 整份读不出来。
"""

from __future__ import annotations

import json
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
from backend.services.engine.alpha_agent.statistical import serialize as ser  # noqa: E402

N_SYMBOLS = 40
N_DAYS = 120
MINING_START_IDX = 8
MINING_END_IDX = 79   # OOS: 80..119
VALID_START_IDX = 80

BETA = 0.1          # 昨日收益通道系数（全程激活，弱而稳：RankIC ≈ β）
LATE_GAMMA = 0.12   # 日内位置通道系数（只在最后一折激活，强而短）
#: 激活日 = 62 → 第 62..79 日的前向收益带信号（t 日的 late 预测 ret[t+1]），
#: 恰好落在挖掘窗（8..79）第 4 折内：折 1..3 的 late 信号严格为零。
LATE_START_IDX = 63


def _symbols(n: int = N_SYMBOLS) -> list[str]:
    return [f"sh600{i:03d}" for i in range(n)]


def _make_panel(*, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=N_DAYS)
    eps = rng.normal(0, 0.012, size=(N_DAYS, N_SYMBOLS))
    pos = rng.uniform(0, 1, size=(N_DAYS, N_SYMBOLS))
    ret = np.zeros_like(eps)
    prev = np.zeros(N_SYMBOLS)
    for t in range(N_DAYS):
        gamma = LATE_GAMMA if t >= LATE_START_IDX else 0.0
        signal = gamma * (pos[t - 1] - 0.5) if t > 0 else 0.0
        ret[t] = BETA * prev + signal + eps[t]
        prev = ret[t]
    close = 20.0 * np.cumprod(1 + ret, axis=0)
    half = close * 0.01 * (1 + rng.random((N_DAYS, N_SYMBOLS)))
    low = close - pos * 2 * half
    high = low + 2 * half
    index = pd.MultiIndex.from_product([dates, _symbols()], names=["trade_date", "symbol"])
    flat = lambda m: np.asarray(m, dtype=float).reshape(-1)  # noqa: E731
    return pd.DataFrame(
        {
            "open": flat(close * 0.999),
            "high": flat(high),
            "low": flat(low),
            "close": flat(close),
            "volume": 1e6,
            "amount": 1e8,
        },
        index=index,
    ).sort_index()


def _metadata(panel: pd.DataFrame) -> pd.DataFrame:
    symbols = sorted(panel.index.get_level_values(1).unique())
    return pd.DataFrame(
        {
            "symbol_raw": symbols,
            "name": symbols,
            "listing_date": pd.Timestamp("2001-01-01"),
            "is_st": False,
            "float_shares": 1e5,
        },
        index=pd.Index([s[2:] for s in symbols], name="symbol_digits"),
    )


def _yesterday_return(frame: pd.DataFrame) -> pd.Series:
    return frame["close"] / frame["close"].shift(1) - 1


YESTERDAY_EXPR = "$close/Ref($close,1)-1"


def _intraday_position(frame: pd.DataFrame) -> pd.Series:
    return (frame["close"] - frame["low"]) / (frame["high"] - frame["low"])


def _fake(key: str, compute, expression: str, *, min_history: int = 2) -> cand.Candidate:
    return cand.Candidate(
        key=key, name=key, family="test", formulation=f"{key} (测试用)",
        source="test", expression=expression, min_history=min_history, compute=compute,
    )


def _registry(monkeypatch, *candidates: cand.Candidate) -> None:
    monkeypatch.setattr(cand, "CANDIDATES", tuple(candidates))
    monkeypatch.setattr(cand, "CANDIDATE_BY_KEY", {c.key: c for c in candidates})


EARLY = _fake("early", _yesterday_return, YESTERDAY_EXPR)
CLONE = _fake("clone", lambda frame: _yesterday_return(frame) * 2 + 1e-4, YESTERDAY_EXPR)
LATE = _fake(
    "late", _intraday_position,
    "($close-$low)/($high-$low)",
)


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
    """小面板：门槛按比例收，折门槛收到 5 天（4 折 × ~18 天，判定照常可判）。"""
    base = {
        "top_k": 1, "horizons": (1,), "min_valid_days": 5, "oos_min_days": 5,
        "min_names_per_day": 5, "min_fold_days": 5,
    }
    base.update(overrides)
    return eng.StatisticalConfig(**base)


def _run(panel, metadata, cfg, *, with_oos: bool = False, registry=()):
    mining_start, mining_end, valid_start, valid_end = _window_names()
    windows = {"valid_start": valid_start, "valid_end": valid_end} if with_oos else {}
    return eng.run_statistical_mining(
        mining_start, mining_end, cfg=cfg,
        price_panel=panel, metadata=metadata, **windows,
    )


def _report(result, key) -> eng.CandidateReport:
    return next(r for r in result.candidates if r.key == key)


class TestReselectGate:
    def test_late_burst_candidate_gated_and_switch_flips_outcome(
        self, monkeypatch
    ):
        """late 整窗最强但集中在窗尾 → 闸拦下；关闸后它当选。

        这是滚动再选择的完整行为闭环：闸开 = early（一路稳）当选、late 带
        「滚动再选择不稳定」拒因；闸关 = late（整窗 |RankIC| 更大）当选。
        """
        panel = _make_panel()
        meta = _metadata(panel)

        _registry(monkeypatch, EARLY, LATE)
        gated = _run(panel, meta, _cfg(require_stability=True))
        assert gated.selected == ("early",), "窗尾爆发不足以当选"
        late = _report(gated, "late")
        assert "滚动再选择不稳定" in late.reject_reason
        assert len(late.fold_ics) == 4 and late.reselect_checkpoints == 3, (
            "折结构足够：这是可判定状态下的判决（不是「折太薄不判」）"
        )
        assert late.reselect_rate < eng.StatisticalConfig().reselect_min_rate
        assert late.reselect_hits <= 1, "只有末检查点可能命中"
        assert (
            abs(late.fold_ics[-1]) > abs(late.fold_ics[0])
        ), "逐折证据要与判决对得上：末折强、首折弱"
        early = _report(gated, "early")
        assert early.selected is True
        assert early.reselect_rate >= eng.StatisticalConfig().reselect_min_rate

        _registry(monkeypatch, EARLY, LATE)
        ungated = _run(panel, meta, _cfg(require_stability=False))
        assert ungated.selected == ("late",), "关闸后整窗最强当选——闸不是装饰"

    def test_disabled_stability_leaves_evidence_empty_not_zero(self, monkeypatch):
        """闸关闭时稳定性字段如实留空：没有算过的证据不许显示成 0。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, LATE)

        result = _run(
            panel, _metadata(panel), _cfg(require_stability=False, require_dedup=False)
        )

        early = _report(result, "early")
        assert early.fold_ics == ()
        assert np.isnan(early.reselect_rate)
        assert early.reselect_hits == 0 and early.reselect_checkpoints == 0


class TestDedupGate:
    def test_correlated_duplicate_dropped_with_reason(self, monkeypatch):
        """秩完全同序的 clone 被去重挤掉：拒因写明「与 early 高度相关」。

        clone 与 early 的 IC 相同（同信息、同秩）——两个都过闸进 Top-K，
        值级去重只留先者；不补位（没有第三个因子递补）。
        """
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel, _metadata(panel),
            _cfg(top_k=2, require_fdr=False),
        )

        assert result.selected == ("early",)
        clone = _report(result, "clone")
        assert "高度相关" in clone.reject_reason and "early" in clone.reject_reason
        assert result.dedup is not None
        assert result.dedup.kept == ("early",)
        (dropped_key, matched_key, rho), = result.dedup.dropped
        assert (dropped_key, matched_key) == ("clone", "early")
        assert abs(rho) >= 0.9
        assert abs(_report(result, "early").max_abs_corr - abs(rho)) < 1e-12, (
            "max_abs_corr 与 dropped 记录同一份 ρ（对称口径）"
        )

    def test_dedup_disabled_keeps_both(self, monkeypatch):
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel, _metadata(panel),
            _cfg(top_k=2, require_fdr=False, require_dedup=False),
        )

        assert set(result.selected) == {"early", "clone"}
        assert result.dedup is None, "未启用去重 → 无报告（不是空报告）"


class TestComposite:
    def test_composite_built_and_serialized(self, monkeypatch):
        """两个同信号分量 → 权重各半的合成因子；报告严格 JSON 可序列化。

        去重开关关掉（两个分量秩同序，去重开着只会留一个）：这里验的是
        合成链路本身，去重判决由 ``TestDedupGate`` 单独钉。
        """
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel, _metadata(panel),
            _cfg(top_k=2, require_fdr=False, require_dedup=False, composite_z_window=4),
        )

        assert set(result.selected) == {"early", "clone"}
        plan = result.composite
        assert plan is not None
        assert plan.components == ("early", "clone")
        assert plan.weights[0] == pytest.approx(0.5)
        assert plan.weights[1] == pytest.approx(0.5)
        assert plan.horizon in (1, 5)
        assert plan.min_history == 2 + (4 - 1), "min_history = 分量回看 + (z 窗 − 1)"
        assert "\n" not in plan.expression and "Std(" in plan.expression
        assert not plan.expression.startswith("+")
        assert plan.rank_ic_mean > 0, "同向信号合成后不该翻号"
        assert plan.eligible is True, "未配验证区间：样本外闸不拦（报告标 oos_verified=False）"

        audit = ser.result_to_dict(result)
        assert audit["composite"]["components"] == ["early", "clone"]
        assert audit["dedup"] is None
        # 严格 JSON：NaN 语义在 serialize 后落成 null，不许是裸 NaN 字面量
        text = json.dumps(ser.json_safe(audit), allow_nan=False, ensure_ascii=False)
        assert json.loads(text)["composite"]["z_window"] == 4

    def test_composite_skipped_with_single_selection(self, monkeypatch):
        panel = _make_panel()
        _registry(monkeypatch, EARLY)

        result = _run(panel, _metadata(panel), _cfg(top_k=2))

        assert result.selected == ("early",)
        assert result.composite is None, "分量不足不合成（不是空合成）"

    def test_composite_disabled_switch(self, monkeypatch):
        """去重关掉、两个分量都在，只拨合成开关：合成必须跟着开关消失。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel, _metadata(panel),
            _cfg(
                top_k=2, require_fdr=False, require_dedup=False,
                composite_enabled=False,
            ),
        )

        assert set(result.selected) == {"early", "clone"}, "前置：两个分量都入选"
        assert result.composite is None
