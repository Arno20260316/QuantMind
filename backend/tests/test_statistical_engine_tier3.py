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
from backend.services.engine.alpha_agent.statistical import engine as eng  # noqa: E402
from backend.services.engine.alpha_agent.statistical import serialize as ser  # noqa: E402

N_SYMBOLS = 40
N_DAYS = 120
MINING_START_IDX = 8
MINING_END_IDX = 79   # OOS: 80..119
VALID_START_IDX = 80

BETA = 0.05         # 昨日收益通道系数（全程激活，弱而稳：RankIC ≈ β）
EARLY_GAMMA = -0.006  # 日内位置通道在前段的弱负系数（折 1..3 稳定反向）
LATE_GAMMA = 0.3      # 日内位置通道在尾折的强正系数（一折独大，反超整窗）
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


class TestFoldConsistencyGate:
    def test_late_burst_candidate_gated_and_switch_flips_outcome(
        self, monkeypatch
    ):
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
        # 选择分 = |RankIC| × 折间一致性：即便将来两闸都过，排序也不单向看幅度
        assert late.selection_score == pytest.approx(abs(late.rank_ic_mean) * 0.25)
        assert early.selection_score == pytest.approx(abs(early.rank_ic_mean))

        # 单闸隔离（稳定性关、FDR 关）：只剩「整窗 |RankIC| 排序」——late
        # 当选，证明它输掉 gated 局不是别的原因。
        _registry(monkeypatch, EARLY, LATE)
        ungated = _run(panel, meta, _cfg(require_stability=False, require_fdr=False))
        assert ungated.selected == ("late",), "只剩稳定性闸：关了它整窗最强当选——闸不是装饰"

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

    def test_backfill_after_dedup_drop(self, monkeypatch):
        """去重挤掉的席位由后续候选**递补**：Top-K 是席位，不是先到先得。

        weak 排在 clone 之后（|IC| 微正、纯噪声），正常轮不到它；clone 与
        early 秩全同（ρ=1.00）被去重挤掉后，贪心步行继续走到 weak 递补入席。
        稳定性/FDR 闸关掉：递补候选是噪声，开着会被闸先拒、盖住去重变量。
        """
        panel = _make_panel()
        _registry(monkeypatch, EARLY, CLONE, WEAK)

        result = _run(
            panel, _metadata(panel),
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
        (dropped_key, matched_key, rho), = result.dedup.dropped
        assert (dropped_key, matched_key) == ("clone", "early")
        assert abs(rho) == pytest.approx(1.0)

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
