"""挖掘算法 API 面（移植 P2）——目录 / evolve 与 batch 的算法闸门。

launcher 侧的能力面分支在 ``test_mining_algorithm_dispatch.py``；这里钉路由
自己的边：

1. **目录是能力面的唯一数据源**：``GET /algorithms`` 与配置面板段同源于一份
   manifest 摘要——前端按字段（uses_llm/uses_datasets/requires_gpu/stages）
   决定控件显隐，不硬编码算法名。
2. **闸门在位、顺序正确**：未知算法 / 统计派 + 非 A 股市场在烧任何成本之前
   400；默认（空 algorithm）仍走 rdagent + LLM 链路（迁移前行为逐位不变）。
3. **不消费 LLM 的算法不被 412 拦**：统计派不解析用户 LLM 配置、不下发
   overrides（没有消费方就不给密钥，缩小暴露面）。
4. **方向文本显式拒绝**：``uses_llm=false`` 的算法收下方向就是沉默丢弃——
   evolve 与 batch（每条方向必填）都整包 400，不半批派。

纪律：直调路由必须显式传全部 Query 参数（缺省是 ``Query()`` 对象而非真值）。
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
from backend.services.engine.routers import alpha_agent as router_mod  # noqa: E402

CFG = LLMConfig(
    api_key="sk-x", base_url="https://llm.test/v1", model="m1", protocol="openai"
)


class FakeLauncher:
    """单条 evolve + 批量 start_or_queue 双替身（只记录，不起子进程）。"""

    def __init__(self) -> None:
        self.evolve_calls: list[dict] = []
        self.queue_calls: list[dict] = []

    async def start_evolution(self, user_id, **kw):
        self.evolve_calls.append({"user_id": user_id, **kw})
        return "task-single"

    async def start_or_queue(self, user_id, **kw):
        idx = len(self.queue_calls)
        self.queue_calls.append({"user_id": user_id, **kw})
        return SimpleNamespace(
            task_id=f"t{idx}", status="running", queue_position=None,
            deduplicated=False,
        )

    def count_running(self):
        return {"global": 0, "by_user": {}}

    def running_capacity(self):
        from backend.services.engine.alpha_agent.launcher import AlphaAgentLauncher

        return AlphaAgentLauncher.running_capacity(self)


def _wire(monkeypatch, *, llm_outcome=(CFG, "user_profile", None)):
    """接线：假 launcher + 可观测的 LLM 解析器 + 假身份。"""
    launcher = FakeLauncher()
    calls: dict = {}

    monkeypatch.setattr(router_mod, "get_launcher", lambda: launcher)
    monkeypatch.setattr(
        router_mod, "get_authenticated_identity", lambda request: ("u-1", "t-1")
    )
    monkeypatch.setattr(router_mod, "assert_identity_not_spoofed", lambda **kw: None)
    monkeypatch.setattr(
        router_mod, "get_doc_store", lambda: SimpleNamespace()
    )

    async def fake_llm(user_id, tenant_id):
        calls["llm"] = (user_id, tenant_id)
        return llm_outcome

    monkeypatch.setattr(router_mod, "_resolve_effective_llm_config", fake_llm)
    return launcher, calls


def _call(payload=None, **over):
    kwargs = {
        "request": SimpleNamespace(),
        "user_id": None,
        "market": "a_share",
        "universe": "csi300",
        "loop_n": 5,
        "direction": "",
        "directions": [],
        "direction_mode": "selected",
        "num_directions": 1,
        "quality_gate_mode": "",
        "data_source": "",
        "mining_start": "",
        "mining_end": "",
        "valid_start": "",
        "valid_end": "",
        "datasets": None,
        "freq": "",
        # P2 的算法 Query 参数：直调必须显式传（缺省是 Query() 对象）
        "algorithm": "",
    }
    kwargs.update(over)
    return router_mod.start_evolution(payload=payload, **kwargs)


class TestAlgorithmsCatalog:
    @pytest.mark.asyncio
    async def test_catalog_is_capability_surface_source(self) -> None:
        """目录随能力面字段一起下发：前端控件显隐的唯一依据。"""
        out = await router_mod.list_mining_algorithms()
        assert out["code"] == 200
        data = out["data"]
        assert data["default"] == "rdagent"

        by_key = {a["key"]: a for a in data["algorithms"]}
        assert set(by_key) >= {"rdagent", "statistical"}
        for algo in by_key.values():
            assert algo["valid"] is True
            # 每个条目都带齐能力面字段 + runner（前端零硬编码）
            for field in (
                "name",
                "markets",
                "freqs",
                "uses_datasets",
                "uses_llm",
                "requires_gpu",
                "progress_kind",
                "stages",
                "runner",
            ):
                assert field in algo, f"{algo['key']} 缺字段 {field}"

        stat = by_key["statistical"]
        assert stat["uses_llm"] is False
        assert stat["requires_gpu"] is False
        assert stat["uses_datasets"] is False
        assert stat["progress_kind"] == "jsonl"
        # 阶段全集即前端管线渲染依据：Tier 3 的三个阶段（子窗稳定性 → 相关性
        # 去重 → IC 加权合成）必须与引擎 emit 顺序一致地出现在 manifest 里
        assert [s["key"] for s in stat["stages"]] == [
            "data",
            "filter",
            "evaluate",
            "select",
            "stability",
            "dedup",
            "composite",
        ]
        assert stat["markets"] == ["a_share"], "统计派只跑 A 股"

        rd = by_key["rdagent"]
        assert rd["uses_llm"] is True
        assert rd["requires_gpu"] is True
        assert rd["progress_kind"] == "workdir"


class TestEvolveAlgorithmGate:
    @pytest.mark.asyncio
    async def test_unknown_algorithm_400_before_any_cost(self, monkeypatch) -> None:
        launcher, calls = _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            await _call(algorithm="quantum")
        assert ei.value.status_code == 400
        assert "不存在" in ei.value.detail
        assert launcher.evolve_calls == []
        assert "llm" not in calls, "非法算法在 LLM 解析之前就该被拒"

    @pytest.mark.asyncio
    async def test_statistical_skips_llm_412_and_key_injection(
        self, monkeypatch
    ) -> None:
        """统计派不消费 LLM：不解析配置（没配 Key 也能跑）、不下发密钥。"""
        launcher, calls = _wire(monkeypatch, llm_outcome=(None, None, None))
        out = await _call(algorithm="statistical")
        assert out["code"] == 200
        assert out["data"]["algorithm"] == "statistical"
        assert "统计量价挖掘" in out["data"]["message"]
        assert "llm" not in calls, "uses_llm=false 不该碰用户 LLM 配置"
        assert len(launcher.evolve_calls) == 1
        sent = launcher.evolve_calls[0]
        assert sent["algorithm"] == "statistical"
        assert sent["llm_overrides"] is None, "无消费方 → 不注入任何用户密钥"

    @pytest.mark.asyncio
    async def test_default_path_still_requires_llm_key(self, monkeypatch) -> None:
        """空 algorithm = 迁移前默认 rdagent：412 口径逐位不变。"""
        _launcher, calls = _wire(monkeypatch, llm_outcome=(None, None, None))
        with pytest.raises(HTTPException) as ei:
            await _call()
        assert ei.value.status_code == 412
        assert "llm" in calls

    @pytest.mark.asyncio
    async def test_statistical_rejects_direction_text(self, monkeypatch) -> None:
        """方向文本对统计派是沉默丢弃——显式 400，不「收下再忽略」。"""
        launcher, _calls = _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            await _call(algorithm="statistical", direction="动量反转 × 低波动")
        assert ei.value.status_code == 400
        assert "不消费方向文本" in ei.value.detail
        assert launcher.evolve_calls == []

        with pytest.raises(HTTPException) as ei2:
            await _call(algorithm="statistical", directions=["类别方向"])
        assert ei2.value.status_code == 400
        assert launcher.evolve_calls == []

    @pytest.mark.asyncio
    async def test_statistical_rejects_unsupported_market(self, monkeypatch) -> None:
        _launcher, calls = _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            await _call(algorithm="statistical", market="hong_kong")
        assert ei.value.status_code == 400
        assert "不支持市场" in ei.value.detail
        assert "llm" not in calls

    @pytest.mark.asyncio
    async def test_statistical_rejects_dataset_selection(self, monkeypatch) -> None:
        """数据集选择对 uses_datasets=false 的算法同样不许静默吞。"""
        _launcher, _calls = _wire(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            await _call(algorithm="statistical", datasets=["features_daily"])
        assert ei.value.status_code == 400
        assert "不消费数据集选择" in ei.value.detail


class TestBatchAlgorithmGate:
    @pytest.mark.asyncio
    async def test_batch_statistical_rejected_whole_batch(self, monkeypatch) -> None:
        """批量契约每条方向必填——统计派整批 400（不半批派、不静默丢方向）。"""
        launcher, calls = _wire(monkeypatch)
        from backend.services.engine.routers.alpha_agent import MiningBatchRequest

        with pytest.raises(HTTPException) as ei:
            await router_mod.dispatch_mining_batch(
                request=SimpleNamespace(),
                payload=MiningBatchRequest(
                    directions=["方向一", "方向二"], algorithm="statistical"
                ),
            )
        assert ei.value.status_code == 400
        assert "不消费方向文本" in ei.value.detail
        assert launcher.queue_calls == []
        assert "llm" not in calls

    @pytest.mark.asyncio
    async def test_batch_unknown_algorithm_400(self, monkeypatch) -> None:
        launcher, _calls = _wire(monkeypatch)
        from backend.services.engine.routers.alpha_agent import MiningBatchRequest

        with pytest.raises(HTTPException) as ei:
            await router_mod.dispatch_mining_batch(
                request=SimpleNamespace(),
                payload=MiningBatchRequest(directions=["方向一"], algorithm="quantum"),
            )
        assert ei.value.status_code == 400
        assert "不存在" in ei.value.detail
        assert launcher.queue_calls == []
