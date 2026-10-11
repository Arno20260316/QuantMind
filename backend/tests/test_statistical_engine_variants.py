"""统计派引擎 · 变体残差与冷却名单（T-FE-09）——集成契约。

用合成面板 + 受控父子注册表把三件事钉死：

1. **残差在环内同管线计算**：子变体（即使与父本 ρ<0.3，走增量闸会
   ``no_basis``）也拿到对父本的残差观测（``variant_of``/``variant_residual``）；
   ρ≥0.3 与 <0.3 的父子走同一条口径（真实 ρ 落 ``max_basis_abs_corr``）。
2. **冷却名单语义**：``cooled_keys`` 的候选本轮不评估（``cooled_keys``
   原样回传留痕）；其子变体逐周期记 ``no_baseline_in_run``——绝不拿旧值
   顶替、绝不冒充「无冗余」。
3. **默认零变化**：不带冷却参数时行为与旧实现一致（既有引擎测试套
   全绿即 BLOCK 不变量）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import candidates as cand  # noqa: E402
from backend.services.engine.alpha_agent.statistical import variant_ledger as vl  # noqa: E402
from backend.tests.test_statistical_engine import (  # noqa: E402
    _cfg,
    _fake,
    _intraday_position,
    _make_panel,
    _metadata,
    _noise,
    _report,
    _run,
    _yesterday_return,
)

TWO_HORIZONS = (1, 5)


def _mix(frame):
    """父本信号 + 正交的日内位置通道（样本内有预测力、与父本低相关）。"""
    return _yesterday_return(frame) + 0.5 * _intraday_position(frame)


FAKE = (
    _fake("p_rev", _yesterday_return),
    _fake("c_clone", _yesterday_return),  # 克隆子：残差恒 0
    _fake("c_mix", _mix),  # 部分冗余：残差保留独立信号
    _fake("c_low", _noise),  # 噪声子：残差 ICIR 低（可冷却判据的原料）
)
FAKE_BASELINE = {"c_clone": "p_rev", "c_mix": "p_rev", "c_low": "p_rev"}


@pytest.fixture
def variant_registry(monkeypatch):
    monkeypatch.setattr(cand, "CANDIDATES", FAKE)
    monkeypatch.setattr(cand, "CANDIDATE_BY_KEY", {c.key: c for c in FAKE})
    monkeypatch.setattr(vl, "BASELINE_BY_KEY", dict(FAKE_BASELINE))
    monkeypatch.setattr(vl, "EDIT_TYPE_BY_KEY", dict.fromkeys(FAKE_BASELINE, "调窗"))
    return FAKE


def _err(result, key) -> dict:
    """best horizon 上的残差观测。"""
    report = _report(result, key)
    by_h = {int(e["horizon"]): e for e in report.variant_residual}
    assert report.best_horizon is not None
    return by_h[report.best_horizon]


class TestVariantResidual:
    def test_child_gets_residual_versus_parent(self, variant_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))

        mix = _report(result, "c_mix")
        assert mix.variant_of == "p_rev"
        assert len(mix.variant_residual) == 2, "全周期观测（不只 best）"
        entry = _err(result, "c_mix")
        assert entry["status"] == "ok"
        assert entry["basis"] == ["p_rev"] and entry["n_basis"] == 1
        assert entry["ic_ir"] is not None and entry["ic_ir"] > 0.3
        # 真实 ρ（0.13 量级 < 0.3）：增量闸口径会判 no_basis，台账口径要的
        # 正是这条残差——绝不伪造 1.0 的审计字段
        assert 0.0 < entry["max_basis_abs_corr"] < 0.3

    def test_clone_child_is_unverifiable_not_coolable_material(self, variant_registry):
        """克隆子残差恒 0 → insufficient_days（宁缺勿凑；冷却判据不会拿它开刀）。"""
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))

        clone = _report(result, "c_clone")
        assert clone.variant_of == "p_rev"
        for entry in clone.variant_residual:
            assert entry["status"] == "insufficient_days"
            assert entry["ic_ir"] is None
            assert entry["max_basis_abs_corr"] == pytest.approx(1.0, abs=1e-9)

    def test_root_has_no_variant_fields(self, variant_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))
        root = _report(result, "p_rev")
        assert root.variant_of is None
        assert root.variant_residual == ()

    def test_observation_shape_is_ledger_ready(self, variant_registry):
        """引擎出口能直接构造台账观测（make_observation 不抛、horizons 对齐）。"""
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))
        mix = _report(result, "c_mix")
        obs = vl.make_observation(
            variant_key=mix.key,
            universe=result.universe,
            window_start=result.window["mining_start"],
            window_end=result.window["mining_end"],
            run_id="test_run",
            horizons=list(mix.variant_residual),
            best_horizon=mix.best_horizon,
        )
        assert obs["baseline_key"] == "p_rev"
        assert [int(h["horizon"]) for h in obs["horizons"]] == list(TWO_HORIZONS)


class TestCooledKeys:
    def test_cooled_candidate_is_skipped_and_echoed(self, variant_registry):
        panel = _make_panel()
        result = _run(
            panel,
            _metadata(panel),
            cfg=_cfg(top_k=1, horizons=TWO_HORIZONS),
            cooled_keys=frozenset({"c_low"}),
        )
        assert result.cooled_keys == ("c_low",)
        assert [r.key for r in result.candidates] == ["p_rev", "c_clone", "c_mix"]

    def test_cooled_parent_gives_no_baseline_in_run(self, variant_registry):
        """父本被冷却 → 子变体逐周期 no_baseline_in_run（不判、不拿旧值顶替）。"""
        panel = _make_panel()
        result = _run(
            panel,
            _metadata(panel),
            cfg=_cfg(top_k=1, horizons=TWO_HORIZONS),
            cooled_keys=frozenset({"p_rev"}),
        )
        assert result.cooled_keys == ("p_rev",)
        assert "p_rev" not in [r.key for r in result.candidates]
        for key in ("c_clone", "c_mix", "c_low"):
            report = _report(result, key)
            assert report.variant_of == "p_rev"
            assert len(report.variant_residual) == len(TWO_HORIZONS)
            for entry in report.variant_residual:
                assert entry["status"] == "no_baseline_in_run"
                assert entry["ic_ir"] is None and entry["n_days"] == 0

    def test_cooled_keys_not_in_registry_are_ignored(self, variant_registry):
        """台账里的陈旧键（注册表已无此候选）不炸、不产生幻影行。"""
        panel = _make_panel()
        result = _run(
            panel,
            _metadata(panel),
            cfg=_cfg(top_k=1, horizons=TWO_HORIZONS),
            cooled_keys=frozenset({"ghost_variant"}),
        )
        assert result.cooled_keys == ()
        assert len(result.candidates) == len(FAKE)

    def test_default_run_has_no_cooling(self, variant_registry):
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))
        assert result.cooled_keys == ()
        assert len(result.candidates) == len(FAKE)


class TestCoolingJudgementFeed:
    def test_low_icir_child_trips_cooling_pipeline(self, variant_registry):
        """端到端：c_low 残差 ICIR ≈ 0.1 < 0.3 且 n_days≥40 → 高置信失败 →
        台账冷却；克隆子 insufficient_days 不冷却（非对称否决的原料侧）。"""
        panel = _make_panel()
        result = _run(panel, _metadata(panel), cfg=_cfg(top_k=1, horizons=TWO_HORIZONS))

        observations = [
            vl.make_observation(
                variant_key=r.key,
                universe=result.universe,
                window_start=result.window["mining_start"],
                window_end=result.window["mining_end"],
                run_id="test_run",
                horizons=list(r.variant_residual),
                best_horizon=r.best_horizon,
            )
            for r in result.candidates
            if r.variant_of is not None
        ]
        ledger, _ = vl.decide_cooling(
            vl.upsert_observations(vl.empty_ledger(), observations)
        )
        cooled = vl.active_cooled_keys(ledger, universe=result.universe)
        assert "c_low" in cooled, "低 ICIR 高置信失败 → 进冷却"
        assert "c_clone" not in cooled, "不可验证永不判失败"
        assert "c_mix" not in cooled, "高 ICIR 通过 → 不冷却"
