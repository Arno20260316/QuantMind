"""入库闸门模式 quality_gate_mode（T-MV-05）——解析链 / 覆盖语义 / 路由接线。

纯单测（无库）：假 launcher 收 kwargs，断言请求意图原样透传；门禁覆盖语义
直接对 ``run_gates`` 打。

防线：

1. **解析链单源**：请求 > env ``ALPHA_GATE_MODE`` > None（不覆盖，回落逐门禁
   配置）。非法请求显式 ValueError（路由转 400）——静默当「未指定」会让用户
   以为硬闸已开却走了软闸；非法 env 只告警忽略（运维错字不拦业务）。
2. **mode_override 只覆盖 mode**：enabled/threshold 仍走逐门禁配置——运维
   用 ``QM_MINING_GATES_DISABLED`` 关掉的闸不因覆盖复活；「off」传进
   ``run_gates`` 是契约错误（调用方该在 off 时直接跳过），必炸。
3. **路由接线**：evolve（query + body 覆盖）与 /mining/batch 都把注册值原样
   透传给 launcher（落任务行 = 请求意图，不冻结入池时的 env）；非法 → 400，
   且在任何 LLM/市场校验之前拒。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.llm_client import LLMConfig  # noqa: E402
from backend.services.engine.mining_plugins import GateContext, run_gates  # noqa: E402
from backend.services.engine.mining_plugins.config import (  # noqa: E402
    normalize_gate_mode,
    resolve_admission_gate_mode,
)
from backend.services.engine.routers import alpha_agent as router_mod  # noqa: E402

CFG = LLMConfig(
    api_key="sk-x", base_url="https://llm.test/v1", model="m1", protocol="openai"
)


# ── 解析链（config）──────────────────────────────────────────────────


class TestNormalizeGateMode:
    def test_blank_and_none_mean_unspecified(self):
        assert normalize_gate_mode(None) == ""
        assert normalize_gate_mode("") == ""
        assert normalize_gate_mode("   ") == ""

    @pytest.mark.parametrize("value", ["off", "OFF", " Soft ", "hard"])
    def test_valid_modes_case_insensitive(self, value):
        assert normalize_gate_mode(value) == value.strip().lower()

    @pytest.mark.parametrize("value", ["banana", "strict", "1", "soft,hard", "关"])
    def test_invalid_raises_not_silently_ignored(self, value):
        with pytest.raises(ValueError):
            normalize_gate_mode(value)


class TestResolveAdmissionGateMode:
    def test_request_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("ALPHA_GATE_MODE", "off")
        assert resolve_admission_gate_mode("hard") == "hard"

    def test_env_fallback_when_request_blank(self, monkeypatch):
        monkeypatch.setenv("ALPHA_GATE_MODE", "hard")
        assert resolve_admission_gate_mode("") == "hard"
        assert resolve_admission_gate_mode(None) == "hard"

    def test_neither_layer_returns_none_not_soft(self, monkeypatch):
        """两层都没指定 → None（不覆盖逐门禁配置）——绝不强制 soft，
        否则会压掉 QM_MINING_GATES_MODE=strict / yaml 的运维升级。"""
        monkeypatch.delenv("ALPHA_GATE_MODE", raising=False)
        assert resolve_admission_gate_mode(None) is None
        assert resolve_admission_gate_mode("") is None

    def test_env_off_explicit(self, monkeypatch):
        monkeypatch.setenv("ALPHA_GATE_MODE", "off")
        assert resolve_admission_gate_mode(None) == "off"

    def test_invalid_env_ignored_falls_to_default(self, monkeypatch):
        monkeypatch.setenv("ALPHA_GATE_MODE", "banana")
        assert resolve_admission_gate_mode("") is None

    def test_invalid_request_raises_even_with_valid_env(self, monkeypatch):
        monkeypatch.setenv("ALPHA_GATE_MODE", "hard")
        with pytest.raises(ValueError):
            resolve_admission_gate_mode("banana")


# ── run_gates 覆盖语义（registry）────────────────────────────────────


def _fail_pfs_ctx() -> GateContext:
    """pfs=0.5 低于 0.9 → pfs_floor fail；其余门禁指标缺失 → skipped。"""
    return GateContext(
        factor_id="f-1", market="a_share", universe="", metrics={"pfs": 0.5}
    )


class TestRunGatesOverride:
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("QM_MINING_GATES_MODE", raising=False)
        monkeypatch.delenv("QM_MINING_GATES_DISABLED", raising=False)

    def test_default_soft_does_not_reject(self, monkeypatch):
        self._clean_env(monkeypatch)
        decision = run_gates(_fail_pfs_ctx())
        by_key = {o.key: o for o in decision.outcomes}
        assert decision.rejected is False
        assert by_key["pfs_floor"].status == "fail"
        assert by_key["pfs_floor"].mode == "soft"

    def test_hard_override_rejects_and_relabels_mode(self, monkeypatch):
        self._clean_env(monkeypatch)
        decision = run_gates(_fail_pfs_ctx(), "hard")
        by_key = {o.key: o for o in decision.outcomes}
        assert decision.rejected is True
        assert by_key["pfs_floor"].mode == "hard"
        assert all(o.mode == "hard" for o in decision.outcomes), (
            "覆盖必须对全部已执行门禁生效（留痕模式与判定一致）"
        )

    def test_soft_override_downgrades_env_strict(self, monkeypatch):
        """env strict（运维全局升级）被请求级 soft 覆盖——请求级优先。"""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("QM_MINING_GATES_MODE", "strict")
        decision = run_gates(_fail_pfs_ctx(), "soft")
        assert decision.rejected is False
        assert all(o.mode == "soft" for o in decision.outcomes)

    def test_override_does_not_revive_disabled_gate(self, monkeypatch):
        """enabled 仍走逐门禁配置：env 关掉的闸不因 hard 覆盖复活。"""
        self._clean_env(monkeypatch)
        monkeypatch.setenv("QM_MINING_GATES_DISABLED", "pfs_floor")
        decision = run_gates(_fail_pfs_ctx(), "hard")
        assert decision.rejected is False
        assert "pfs_floor" not in {o.key for o in decision.outcomes}

    def test_off_is_contract_error(self, monkeypatch):
        self._clean_env(monkeypatch)
        with pytest.raises(ValueError):
            run_gates(_fail_pfs_ctx(), "off")

    def test_unknown_override_raises(self, monkeypatch):
        self._clean_env(monkeypatch)
        with pytest.raises(ValueError):
            run_gates(_fail_pfs_ctx(), "banana")


# ── 路由接线（evolve / mining-batch → launcher）──────────────────────


class FakeLauncher:
    """start_evolution（evolve 单条）+ start_or_queue（批量环）双替身。"""

    def __init__(self) -> None:
        self.started: dict | None = None
        self.calls: list[dict] = []

    def count_running(self):
        return {"global": 0, "by_user": {}}

    def running_capacity(self):
        from backend.services.engine.alpha_agent.launcher import AlphaAgentLauncher

        return AlphaAgentLauncher.running_capacity(self)

    async def start_evolution(self, user_id, **kw):
        self.started = {"user_id": user_id, **kw}
        return "task-single"

    async def start_or_queue(self, user_id, **kw):
        idx = len(self.calls)
        self.calls.append({"user_id": user_id, **kw})
        return SimpleNamespace(
            task_id=f"t{idx}", status="running", queue_position=None,
            deduplicated=False,
        )


class FakeStore:
    async def count_by_direction(self, *, user_id, market, directions):
        return {}


def _wire(monkeypatch, launcher: FakeLauncher):
    from backend.services.engine.alpha_agent import task_store as task_store_mod

    monkeypatch.setattr(task_store_mod, "get_mining_task_store", lambda: FakeStore())
    monkeypatch.setattr(router_mod, "get_launcher", lambda: launcher)
    monkeypatch.setattr(
        router_mod, "get_authenticated_identity", lambda req: ("u-1", "t-1")
    )
    monkeypatch.setattr(router_mod, "assert_identity_not_spoofed", lambda **kw: None)

    async def fake_llm(user_id, tenant_id):
        return CFG, "user_profile", None

    monkeypatch.setattr(router_mod, "_resolve_effective_llm_config", fake_llm)


def _call_evolve(payload=None, **over):
    kwargs = {
        "request": SimpleNamespace(),
        "user_id": None,
        "market": "a_share",
        "universe": "csi300",
        "loop_n": 5,
        "direction": "",
        "directions": [],
        "direction_mode": "selected",
        # 直调必须显式传全部 Query 参数：缺省值是 Query() 对象不是 ""
        "num_directions": 1,
        "quality_gate_mode": "",
        "data_source": "",
        # P1/P1.5 的挖掘配置 Query 参数同样必须显式传：缺省是 Query() 对象，
        # 会在归一函数里以 TypeError/ValueError 爆掉（datasets 不可迭代 / freq 非法值）
        "mining_start": "",
        "mining_end": "",
        "valid_start": "",
        "valid_end": "",
        "datasets": None,
        "freq": "",
        # P2 的算法 Query 参数同样必须显式传（缺省是 Query() 对象）
        "algorithm": "",
    }
    kwargs.update(over)
    return router_mod.start_evolution(payload=payload, **kwargs)


async def _call_batch(payload):
    return await router_mod.dispatch_mining_batch(
        request=SimpleNamespace(), payload=payload
    )


class TestEvolveWiring:
    @pytest.mark.asyncio
    async def test_query_mode_passed_through(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_evolve(direction="方向", quality_gate_mode="hard")
        assert launcher.started["quality_gate_mode"] == "hard"

    @pytest.mark.asyncio
    async def test_unspecified_passes_none(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_evolve(direction="方向")
        assert launcher.started["quality_gate_mode"] is None, (
            "未指定必须落 None（任务行 NULL），绝不代填 soft"
        )

    @pytest.mark.asyncio
    async def test_body_overrides_query(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_evolve(
            payload=router_mod.EvolveRequest(quality_gate_mode="soft"),
            direction="方向",
            quality_gate_mode="hard",
        )
        assert launcher.started["quality_gate_mode"] == "soft"

    @pytest.mark.asyncio
    async def test_off_passed_through(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_evolve(direction="方向", quality_gate_mode="off")
        assert launcher.started["quality_gate_mode"] == "off"

    @pytest.mark.asyncio
    async def test_multi_dispatch_carries_mode_per_item(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_evolve(
            directions=["方向A", "方向B"],
            num_directions=2,
            quality_gate_mode="hard",
        )
        assert len(launcher.calls) == 2
        assert all(c["quality_gate_mode"] == "hard" for c in launcher.calls)

    @pytest.mark.asyncio
    async def test_invalid_mode_rejected_before_anything_else(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)

        async def boom(user_id, tenant_id):
            raise AssertionError("非法闸门模式必须在 LLM 解析之前被拒")

        monkeypatch.setattr(router_mod, "_resolve_effective_llm_config", boom)
        with pytest.raises(HTTPException) as ei:
            await _call_evolve(direction="方向", quality_gate_mode="banana")
        assert ei.value.status_code == 400
        assert "quality_gate_mode" in str(ei.value.detail)
        assert launcher.started is None

    @pytest.mark.asyncio
    async def test_invalid_mode_in_body_rejected(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        with pytest.raises(HTTPException) as ei:
            await _call_evolve(
                payload=router_mod.EvolveRequest(quality_gate_mode="strict"),
                direction="方向",
            )
        assert ei.value.status_code == 400


class TestBatchWiring:
    @pytest.mark.asyncio
    async def test_mode_passed_to_every_item(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_batch(
            router_mod.MiningBatchRequest(
                directions=["甲", "乙"], quality_gate_mode="off"
            )
        )
        assert [c["quality_gate_mode"] for c in launcher.calls] == ["off", "off"]

    @pytest.mark.asyncio
    async def test_unspecified_passes_none(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        await _call_batch(router_mod.MiningBatchRequest(directions=["甲"]))
        assert launcher.calls[0]["quality_gate_mode"] is None

    @pytest.mark.asyncio
    async def test_invalid_mode_whole_batch_400(self, monkeypatch):
        launcher = FakeLauncher()
        _wire(monkeypatch, launcher)
        with pytest.raises(HTTPException) as ei:
            await _call_batch(
                router_mod.MiningBatchRequest(
                    directions=["甲"], quality_gate_mode="banana"
                )
            )
        assert ei.value.status_code == 400
        assert launcher.calls == [], "整包先拒——一条都没派"
