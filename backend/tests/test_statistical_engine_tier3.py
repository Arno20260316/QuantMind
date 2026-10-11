"""统计派挖掘引擎 · Tier 3 体系化（折间符号稳定性 / 相关性去重 / IC 加权合成）。

用**可控收益通道**的合成面板把三条机制钉在行为上（不碰 qlib / QuantDB）：

- **昨日收益通道**（β 弱而稳，全程）：供「一路都稳」的候选（early / clone）；
- **日内位置通道**（γ 前弱负后强正，尾折一折独大）：供「折间符号翻脸」的
  候选（late）——早期折稳定反向、末折爆发，整窗 |RankIC| 反而最大，正是
  折间符号一致性闸要拦的形态（2026-10-11 起取代滚动再选择率当闸）；
- **纯噪声通道**（noise 列）：供「无预测力但排在末位」的递补候选（weak）。

钉死的契约：

1. **折间符号一致性闸**：late 折间一致率 0.25 < 0.75 → 拒（``折间符号不稳``）；
   把闸关掉 late 立刻当选（整窗最强）——行为随开关翻转，闸不是装饰。early
   折间全同号 → 当选；它的滚动再选择命中率恰为 0/3 也照样当选——审计量不进闸
   的实证（旧闸按率判决时正是把 early 误杀、放行 late，与 csi300 实跑
   19/28 候选 0/3 同形态）。**T-FE-04（NW t）后 FDR 也独立拦下 late**：
   尾折爆发的 IC 序列自相关强，朴素 t 的假显著被修正（p≈0.2 不过 BH-FDR）
   ——同一形态被两道闸独立识别；因此「闸关后当选」的隔离实验改为**同时关
   FDR**，单独考察稳定性闸；
2. **证据诚实**：late 的逐折 IC 首折反向、末折强正（证据与判决对得上）；
   闸关闭时稳定性字段如实留 NaN/空（不伪造 0）；
3. **相关性去重 + 递补**：秩完全同序的两个候选（|ρ|≈1）后者被去重挤掉、拒因
   写明「与谁相关」；随后候选**递补**入席（Top-K 是席位，不是先到先得）；
   ``result.dedup`` 全量记账；
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
from backend.services.engine.alpha_agent.statistical import complexity as cplx  # noqa: E402
from backend.services.engine.alpha_agent.statistical import costs as sc  # noqa: E402
from backend.services.engine.alpha_agent.statistical import engine as eng  # noqa: E402
from backend.services.engine.alpha_agent.statistical import serialize as ser  # noqa: E402

N_SYMBOLS = 40
N_DAYS = 120
MINING_START_IDX = 8
MINING_END_IDX = 79  # OOS: 80..119
VALID_START_IDX = 80

BETA = 0.05  # 昨日收益通道系数（全程激活，弱而稳：RankIC ≈ β）
EARLY_GAMMA = -0.006  # 日内位置通道在前段的弱负系数（折 1..3 稳定反向）
LATE_GAMMA = 0.3  # 日内位置通道在尾折的强正系数（一折独大，反超整窗）
#: 翻转日 = 63 → 第 63..79 日的前向收益带上尾折信号（t 日的 late 预测
#: ret[t+1]），恰好落在挖掘窗（8..79）第 4 折内：折 1..3 的 late 信号是
#: EARLY_GAMMA 的稳定弱负——尾折 |IC| 必须 > 3× 前段 |IC|，整窗才翻正，
#: 折间一致率才落到 0.25（1/4 折与全窗同号）。
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
        gamma = LATE_GAMMA if t >= LATE_START_IDX else EARLY_GAMMA
        signal = gamma * (pos[t - 1] - 0.5) if t > 0 else 0.0
        ret[t] = BETA * prev + signal + eps[t]
        prev = ret[t]
    close = 20.0 * np.cumprod(1 + ret, axis=0)
    half = close * 0.01 * (1 + rng.random((N_DAYS, N_SYMBOLS)))
    # noise 的抽样追加在最后：不挪动上面 eps/pos/half 的随机流，既有常数标定不失效
    noise = rng.normal(size=(N_DAYS, N_SYMBOLS))
    low = close - pos * 2 * half
    high = low + 2 * half
    index = pd.MultiIndex.from_product(
        [dates, _symbols()], names=["trade_date", "symbol"]
    )
    flat = lambda m: np.asarray(m, dtype=float).reshape(-1)  # noqa: E731
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


def _fake(
    key: str, compute, expression: str, *, min_history: int = 2
) -> cand.Candidate:
    return cand.Candidate(
        key=key,
        name=key,
        family="test",
        formulation=f"{key} (测试用)",
        source="test",
        expression=expression,
        min_history=min_history,
        compute=compute,
    )


def _registry(monkeypatch, *candidates: cand.Candidate) -> None:
    monkeypatch.setattr(cand, "CANDIDATES", tuple(candidates))
    monkeypatch.setattr(cand, "CANDIDATE_BY_KEY", {c.key: c for c in candidates})


EARLY = _fake("early", _yesterday_return, YESTERDAY_EXPR)
CLONE = _fake(
    "clone", lambda frame: _yesterday_return(frame) * 2 + 1e-4, YESTERDAY_EXPR
)
LATE = _fake(
    "late",
    _intraday_position,
    "($close-$low)/($high-$low)",
)
WEAK = _fake("weak", lambda frame: frame["noise"], "$noise")


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
        "top_k": 1,
        "horizons": (1,),
        "min_valid_days": 5,
        "oos_min_days": 5,
        "min_names_per_day": 5,
        "min_fold_days": 5,
    }
    base.update(overrides)
    return eng.StatisticalConfig(**base)


def _run(
    panel, metadata, cfg, *, with_oos: bool = False, registry=(), regime_by_month=None
):
    mining_start, mining_end, valid_start, valid_end = _window_names()
    windows = {"valid_start": valid_start, "valid_end": valid_end} if with_oos else {}
    return eng.run_statistical_mining(
        mining_start,
        mining_end,
        cfg=cfg,
        price_panel=panel,
        metadata=metadata,
        regime_by_month=regime_by_month,
        **windows,
    )


def _report(result, key) -> eng.CandidateReport:
    return next(r for r in result.candidates if r.key == key)


class TestFoldConsistencyGate:
    def test_late_burst_candidate_gated_and_switch_flips_outcome(self, monkeypatch):
        """late 整窗最强但折间符号翻脸（尾折单骑救主）→ 闸拦下；关闸后它当选。

        完整行为闭环：闸开 = early（折间全同号）当选、late 带「折间符号不稳」
        拒因（T-FE-04 后连 FDR 都过不了：NW 修正把自相关序列的假显著打回，
        双闸独立拦同一形态）；闸关 = late（整窗 |RankIC| 更大）当选——闸
        不是装饰。early 的滚动再选择命中率恰为 0/3（三个检查点全被 late 的
        累计 |IC| 压过）也照样当选：审计量不进闸的实证——旧闸按率判决时正是
        误杀 early、放行 late（与 2026-10-11 csi300 实跑 19/28 候选 0/3
        同形态）。
        """
        panel = _make_panel()
        meta = _metadata(panel)

        _registry(monkeypatch, EARLY, LATE)
        gated = _run(panel, meta, _cfg(require_stability=True))
        assert gated.selected == ("early",), "窗尾爆发不足以当选"
        late = _report(gated, "late")
        assert "折间符号不稳" in late.reject_reason
        assert late.fdr_pass is False and "BH-FDR" in late.reject_reason, (
            "T-FE-04（NW 修正）后 FDR 也独立拦下它：尾折爆发的 IC 序列自相关"
            "极强，朴素 t 的假显著（同数据旧口径下 p<q 放行）被修正为 p≈0.2"
            "——同一形态被两道闸独立识别"
        )
        assert len(late.fold_ics) == 4 and late.reselect_checkpoints == 3, (
            "折结构足够：这是可判定状态下的判决（不是「折太薄不判」）"
        )
        assert late.fold_sign_consistency == pytest.approx(0.25)
        assert (
            late.fold_sign_consistency
            < eng.StatisticalConfig().min_fold_sign_consistency
        )
        assert late.fold_ics[0] < 0 < late.fold_ics[-1], (
            "逐折证据要与判决对得上：首折稳定反向、尾折强正"
        )
        assert abs(late.fold_ics[-1]) > abs(late.fold_ics[0])
        early = _report(gated, "early")
        assert early.selected is True
        assert early.fold_sign_consistency == pytest.approx(1.0), "折间全同号 → 过闸"
        # 审计量照记：early 命中 0/3、late 命中 3/3——旧闸按率判决恰好反向
        assert early.reselect_hits == 0 and early.reselect_checkpoints == 3
        assert late.reselect_rate == pytest.approx(1.0)
        # 选择分 = |RankIC| × 折间一致性 × 简单性因子（T-FE-05）：即便将来
        # 两闸都过，排序也不单向看幅度；同分同稳定下，结构越简越靠前
        assert late.selection_score == pytest.approx(
            abs(late.rank_ic_mean)
            * 0.25
            * cplx.simplicity_factor(late.op_count, late.literal_count)
        )
        assert early.selection_score == pytest.approx(
            abs(early.rank_ic_mean)
            * cplx.simplicity_factor(early.op_count, early.literal_count)
        )

        # 单闸隔离（稳定性关、FDR 关）：只剩「整窗 |RankIC| 排序」——late
        # 当选，证明它输掉 gated 局不是别的原因。
        _registry(monkeypatch, EARLY, LATE)
        ungated = _run(panel, meta, _cfg(require_stability=False, require_fdr=False))
        assert ungated.selected == ("late",), (
            "只剩稳定性闸：关了它整窗最强当选——闸不是装饰"
        )

        # 单闸隔离（FDR 关、稳定性开）：late 被稳定性闸**独立**拦下——FDR
        # 不是唯一拦它的闸（两道闸各自可拦，各有一个不依赖对方的实证）。
        _registry(monkeypatch, EARLY, LATE)
        stab_only = _run(panel, meta, _cfg(require_stability=True, require_fdr=False))
        assert stab_only.selected == ("early",), "FDR 关了稳定性闸照样拦下 late"
        late_only = _report(stab_only, "late")
        assert "折间符号不稳" in late_only.reject_reason
        assert "BH-FDR" not in late_only.reject_reason, "本局 FDR 未启用，拒因不该提它"

    def test_disabled_stability_leaves_evidence_empty_not_zero(self, monkeypatch):
        """闸关闭时稳定性字段如实留空：没有算过的证据不许显示成 0。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, LATE)

        result = _run(
            panel, _metadata(panel), _cfg(require_stability=False, require_dedup=False)
        )

        early = _report(result, "early")
        assert early.fold_ics == ()
        assert np.isnan(early.fold_sign_consistency)
        assert np.isnan(early.reselect_rate)
        assert early.reselect_hits == 0 and early.reselect_checkpoints == 0


class TestDedupGate:
    def test_correlated_duplicate_dropped_with_reason(self, monkeypatch):
        """秩完全同序的 clone 被去重挤掉：拒因写明「与 early 高度相关」。

        clone 与 early 的 IC 相同（同信息、同秩）——两个都过闸，选择期值级
        去重只留先者；候选池只有这两个，凑不满 K 就是不满（递补无人，见
        ``test_backfill_after_dedup_drop`` 验递补本身）。
        """
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel,
            _metadata(panel),
            _cfg(top_k=2, require_fdr=False),
        )

        assert result.selected == ("early",)
        clone = _report(result, "clone")
        assert "高度相关" in clone.reject_reason and "early" in clone.reject_reason
        assert result.dedup is not None
        assert result.dedup.kept == ("early",)
        ((dropped_key, matched_key, rho),) = result.dedup.dropped
        assert (dropped_key, matched_key) == ("clone", "early")
        assert abs(rho) >= 0.9
        assert abs(_report(result, "early").max_abs_corr - abs(rho)) < 1e-12, (
            "max_abs_corr 与 dropped 记录同一份 ρ（对称口径）"
        )

    def test_backfill_after_dedup_drop(self, monkeypatch):
        """去重挤掉的席位由后续候选**递补**：Top-K 是席位，不是先到先得。

        weak 排在 clone 之后（|IC| 微正、纯噪声），正常轮不到它；clone 与
        early 秩全同（ρ=1.00）被去重挤掉后，贪心步行继续走到 weak 递补入席。
        稳定性/FDR 闸关掉：递补候选是噪声，开着会被闸先拒、盖住去重变量。
        """
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE, WEAK)

        result = _run(
            panel,
            _metadata(panel),
            _cfg(top_k=2, require_fdr=False, require_stability=False),
        )

        early = _report(result, "early")
        clone = _report(result, "clone")
        weak = _report(result, "weak")
        assert abs(weak.rank_ic_mean) < abs(early.rank_ic_mean), (
            "前置：weak 的 |IC| 低于 early——它是排后面的递补者，不是并列胜者"
        )
        assert result.selected == ("early", "weak"), "clone 让出的席位由 weak 递补"
        assert weak.selected is True and weak.reject_reason == ""
        assert "高度相关" in clone.reject_reason and "early" in clone.reject_reason
        assert result.dedup is not None
        assert result.dedup.kept == ("early", "weak")
        ((dropped_key, matched_key, rho),) = result.dedup.dropped
        assert (dropped_key, matched_key) == ("clone", "early")
        assert abs(rho) == pytest.approx(1.0)

    def test_dedup_disabled_keeps_both(self, monkeypatch):
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel,
            _metadata(panel),
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
            panel,
            _metadata(panel),
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
        assert plan.eligible is True, (
            "未配验证区间：样本外闸不拦（报告标 oos_verified=False）"
        )

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
            panel,
            _metadata(panel),
            _cfg(
                top_k=2,
                require_fdr=False,
                require_dedup=False,
                composite_enabled=False,
            ),
        )

        assert set(result.selected) == {"early", "clone"}, "前置：两个分量都入选"
        assert result.composite is None


class TestTradeEvidence:
    """T-FE-03：报告补三列——分状态 IC（月标签）/ 自相关→调仓建议 / 换手·扣费净 IC。"""

    #: 月状态表覆盖整个挖掘窗（2023-01..04）；05/06 多出的月份从不被查到——
    #: join 只有 IC 日出现的月份才参与，多给键无害
    MONTHS = {
        "2023-01": "bull",
        "2023-02": "bear",
        "2023-03": "neutral",
        "2023-04": "bull",
        "2023-05": "neutral",
        "2023-06": "bear",
    }

    def test_regime_evidence_splits_by_month_label(self):
        """白盒：月标签 join 精确——缺标签的 IC 日不进任何桶（缺失如实计）。

        弱区谓词沿用 ``regime_buckets.bucket_of``（mean≤0 ∧ n≥15 唯一实现），
        不按「因子是新的」另立门槛。
        """
        daily = pd.DataFrame(
            {
                "ic": [0.1, 0.2, -0.3, 0.05, 0.4],
                "rank_ic": [0.1, 0.2, -0.3, 0.05, 0.4],
                "n": [40, 40, 40, 40, 40],
            },
            index=pd.DatetimeIndex(
                ["2023-01-03", "2023-01-04", "2023-02-01", "2023-02-02", "2023-03-01"]
            ).rename("trade_date"),
        )
        stats = eng._regime_evidence(daily, {"2023-01": "bull", "2023-02": "bear"})
        by_state = {s.state: s for s in stats}
        assert [s.state for s in stats] == ["bull", "neutral", "bear"], "档序 = STATES"
        assert by_state["bull"].n_days == 2
        assert by_state["bull"].rank_ic_mean == pytest.approx(0.15)
        assert by_state["bear"].n_days == 2
        assert by_state["bear"].rank_ic_mean == pytest.approx(-0.125)
        assert by_state["neutral"].n_days == 0
        assert np.isnan(by_state["neutral"].rank_ic_mean), "空桶是 NaN，不是 0"
        assert by_state["bear"].weak is False, (
            "mean<0 但 n=2<15：弱区谓词与 regime_buckets 同门槛，不下结论"
        )
        assert eng._regime_evidence(daily, None) == (), "无时间线 → 空，不编造"

    def test_selected_gets_trade_evidence_rejected_gets_defaults(self, monkeypatch):
        """入选者拿全三列；未入选 = 缺省值（因子值懒算纪律——没算就没有数字）。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, WEAK)

        result = _run(panel, _metadata(panel), _cfg(), regime_by_month=self.MONTHS)

        early = _report(result, "early")
        assert early.selected
        assert np.isfinite(early.factor_autocorr)
        assert early.factor_autocorr_days > 0, "配对日数是 ρ₁ 的证据面，随行可见"
        assert early.rebalance_days == sc.suggest_rebalance_days(early.factor_autocorr)
        assert 0.0 <= early.turnover_daily <= 1.0
        assert early.ann_turnover == pytest.approx(early.turnover_daily * sc.ANNUALIZE)
        assert early.ic_net_days > 0 and np.isfinite(early.ic_net_mean)
        assert [s.state for s in early.regime_ic] == ["bull", "neutral", "bear"]
        best_stat = next(h for h in early.horizons if h.horizon == early.best_horizon)
        assert sum(s.n_days for s in early.regime_ic) == best_stat.n_days, (
            "月表覆盖整个挖掘窗 → 每个 IC 日都落到某一桶，n_days 合计对得上"
        )

        weak = _report(result, "weak")
        assert not weak.selected
        assert np.isnan(weak.factor_autocorr)
        assert weak.factor_autocorr_days == 0
        assert weak.rebalance_days is None
        assert np.isnan(weak.turnover_daily) and np.isnan(weak.ann_turnover)
        assert weak.ic_net_days == 0 and np.isnan(weak.ic_net_mean)
        assert weak.regime_ic == (), "未入选：因子值没算，三列一律缺省"

    def test_regime_absent_other_columns_still_filled(self, monkeypatch):
        """拿不到 regime 时间线（runner 降级）：只有 regime_ic 留空——另外两列
        照算，降级不牵连。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, WEAK)

        result = _run(panel, _metadata(panel), _cfg())

        early = _report(result, "early")
        assert early.regime_ic == ()
        assert np.isfinite(early.factor_autocorr)
        assert np.isfinite(early.turnover_daily)
        assert early.ic_net_days > 0

    def test_composite_carries_trade_evidence(self, monkeypatch):
        """合成因子（真正的可交易产出）同带三列——不是只有单候选有。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE)

        result = _run(
            panel,
            _metadata(panel),
            _cfg(top_k=2, require_fdr=False, require_dedup=False, composite_z_window=4),
            regime_by_month=self.MONTHS,
        )

        plan = result.composite
        assert plan is not None
        assert np.isfinite(plan.factor_autocorr)
        assert 0.0 <= plan.turnover_daily <= 1.0
        assert [s.state for s in plan.regime_ic] == ["bull", "neutral", "bear"]
        best_stat = next(h for h in plan.horizons if h.horizon == plan.best_horizon)
        assert sum(s.n_days for s in plan.regime_ic) == best_stat.n_days


class TestTradeEvidenceNumericPins:
    """评审 M5a：ρ₁ 的方向与证据闸钉**数值**（不只是 isfinite）——

    - 截面恒序（每天排序完全相同）→ ρ₁ ≈ +1 → 建议封顶 ``MAX_REBALANCE_DAYS``；
    - 逐日完全反转 → ρ₁ ≈ −1 → 建议 1（别囤隔夜名单）；
    - 配对日数 < ``min_valid_days`` → NaN/None 降级，天数如实可见（M2 闸的
      行为锚点：2 天面板也能凑出一个 |ρ₁|=1，没有天数闸就是假精度）。
    """

    @staticmethod
    def _pin_factor(day_values: list[list[float]]) -> pd.Series:
        n = len(day_values[0])
        days = pd.bdate_range("2023-01-02", periods=len(day_values))
        idx = pd.MultiIndex.from_product(
            [days, [f"sh600{i:03d}" for i in range(n)]],
            names=["trade_date", "symbol"],
        )
        return pd.Series([v for row in day_values for v in row], index=idx)

    @staticmethod
    def _evidence(factor: pd.Series, cfg: eng.StatisticalConfig) -> dict:
        fwd = pd.Series(0.01, index=factor.index)
        daily = pd.DataFrame(
            {"ic": [0.1], "rank_ic": [0.1], "n": [40]},
            index=pd.DatetimeIndex(["2023-01-03"], name="trade_date"),
        )
        return eng._trade_evidence(
            factor,
            best_horizon=1,
            daily=daily,
            fwd={1: fwd},
            in_is=np.ones(len(fwd), dtype=bool),
            cfg=cfg,
            regime_by_month=None,
        )

    def test_constant_cross_sectional_order_pins_autocorr_one(self):
        v = [float(i) for i in range(6)]
        factor = self._pin_factor([v] * 6)
        info = self._evidence(factor, _cfg())

        assert info["factor_autocorr"] == pytest.approx(1.0, abs=1e-12)
        assert info["factor_autocorr_days"] == 5, "6 天 → 5 个 lag-1 配对日"
        assert info["rebalance_days"] == sc.MAX_REBALANCE_DAYS, (
            "ρ₁=1（恒序）→ 半衰期发散，建议封顶——发散量不出建议"
        )

    def test_daily_full_reversal_pins_autocorr_minus_one(self):
        v = [float(i) for i in range(6)]
        factor = self._pin_factor([v, v[::-1]] * 3)
        info = self._evidence(factor, _cfg())

        assert info["factor_autocorr"] == pytest.approx(-1.0, abs=1e-12)
        assert info["factor_autocorr_days"] == 5
        assert info["rebalance_days"] == 1, "ρ₁≤0（无持续性）→ 每日重排"

    def test_below_min_days_degrades_and_reports_pair_count(self):
        """2 天面板：恰 1 个配对日 < min_valid_days=5 → NaN/None；天数=1
        如实落字段——「算不出」与「算出来恰好如此」分得开。"""
        v = [float(i) for i in range(6)]
        factor = self._pin_factor([v] * 2)
        info = self._evidence(factor, _cfg())

        assert info["factor_autocorr_days"] == 1
        assert np.isnan(info["factor_autocorr"]), "证据不足不许出 ±1 的假精度"
        assert info["rebalance_days"] is None


class TestComplexityPenalty:
    """T-FE-05：复杂度进选择分——同 IC/稳定下，表达式越简越靠前。

    口径 = ``complexity.py``（范围受限字符串计数：FM-DSL 解析不了统计派
    的中缀算术）；简单性因子 = 1/(1 + op×log1p(lit)) 只乘不拒（排序偏好，
    不是闸）；同分再比结构（``_rank_eligible`` 排序键尾两项）。
    """

    def test_selection_score_multiplies_simplicity(self):
        best = eng.HorizonStats(
            horizon=1,
            n_days=60,
            ic_mean=0.03,
            rank_ic_mean=-0.04,
            icir=-0.8,
            rank_ic_t=-2.5,
            rank_ic_p=0.015,
            rank_ic_win_rate=0.42,
        )
        assert eng._selection_score(best, None, 0.5) == pytest.approx(0.02)
        # 不给简单性（旧调用口径）→ 权重 1，行为与 T-FE-05 前一致
        assert eng._selection_score(best, None) == pytest.approx(0.04)

    def test_rank_eligible_prefers_simpler_on_tie(self, monkeypatch):
        import dataclasses  # noqa: PLC0415

        panel = _make_panel()
        _registry(monkeypatch, EARLY, WEAK)
        result = _run(panel, _metadata(panel), _cfg())
        base = _report(result, "early")

        def _tie(key, *, ops, lits):
            return dataclasses.replace(
                base,
                key=key,
                selection_score=0.5,
                rank_ic_mean=0.04,
                reject_reason="",
                op_count=ops,
                literal_count=lits,
            )

        assert eng._rank_eligible(
            [_tie("complex", ops=9, lits=6), _tie("simple", ops=1, lits=0)]
        ) == ["simple", "complex"], "同分同 |IC|：算子少的靠前"
        # 分量次序：算子数相同再看字面量数
        assert eng._rank_eligible(
            [_tie("more_lits", ops=3, lits=8), _tie("fewer_lits", ops=3, lits=1)]
        ) == ["fewer_lits", "more_lits"]

    def test_draft_carries_complexity_counts_from_expression(self, monkeypatch):
        """草稿上的 (op_count, literal_count) 是**表达式字符串**的单源计数——
        与落库 metadata 同口径，改口径必须同时过 complexity 单元测试。"""
        panel = _make_panel()
        _registry(monkeypatch, EARLY, WEAK)
        result = _run(panel, _metadata(panel), _cfg())

        early = _report(result, "early")
        expected = cplx.expression_complexity(YESTERDAY_EXPR)
        assert (early.op_count, early.literal_count) == (
            expected.op_count,
            expected.literal_count,
        )
        assert 0.0 < cplx.simplicity_factor(early.op_count, early.literal_count) <= 1.0
