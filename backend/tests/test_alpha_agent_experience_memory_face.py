"""经验记忆 API 面（``GET /api/v1/alpha-agent/experience-memory``，T-FM-04）。

钉三件事：

1. **信封与鉴权**：无身份 → 401；有身份 → ``{"code": 200, "data": ...}``；
2. **本用户 × 本市场口径**：鉴权身份（非 query）决定 user_id，``market``
   query 直通组装（默认 ``a_share``），两者真到 ``load_memory``；
3. **直出可 JSON 序列化**：``to_dict`` snake_case 契约键（reason_buckets /
   saturated_directions / direction_stats / sources），全空 → ``available=false``
   也是 200（「没跑过」不是接口错误）。

DB 取数面是 ``test_experience_memory.py`` 的职责；本文件只钉路由接线，
``load_memory`` 用桩（不碰真库）。

用 ``TestClient`` mini-app（照 ``test_alpha_agent_variant_ledger_face.py`` 惯例）。
"""

from __future__ import annotations

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.alpha_agent import experience_memory as em
    from backend.services.engine.routers import alpha_agent as aa
except Exception:  # noqa: BLE001
    aa = None

pytestmark = pytest.mark.skipif(aa is None, reason="依赖不可用（需容器环境）")

_PREFIX = "/api/v1/alpha-agent"


def _make_client(with_identity: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        if with_identity:
            request.state.user = {"user_id": "u1", "tenant_id": "default"}
        return await call_next(request)

    app.include_router(aa.router)
    return TestClient(app)


@pytest.fixture()
def client():
    return _make_client()


@pytest.fixture()
def calls(monkeypatch):
    """桩掉 load_memory：记 kwargs、返回固定组装。"""

    async def fake_load(**kwargs):
        calls.append(kwargs)
        return em.build_memory(
            market=kwargs.get("market") or "",
            tasks=[
                {
                    "task_id": "t1",
                    "direction": "尾盘资金流强度",
                    "status": "completed",
                    "factor_count": 12,
                }
            ],
        )

    calls: list[dict] = []
    monkeypatch.setattr(em, "load_memory", fake_load)
    return calls


@pytest.mark.unit
def test_requires_identity():
    r = _make_client(with_identity=False).get(f"{_PREFIX}/experience-memory")
    assert r.status_code == 401


@pytest.mark.unit
def test_envelope_and_default_market(client, calls):
    r = client.get(f"{_PREFIX}/experience-memory")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert calls == [{"user_id": "u1", "market": "a_share"}], "默认市场直通组装"

    data = body["data"]
    assert {
        "available",
        "market",
        "generated_at",
        "reason_buckets",
        "saturated_directions",
        "direction_stats",
        "sources",
    } <= set(data)
    assert data["market"] == "a_share"
    assert data["direction_stats"][0]["direction"] == "尾盘资金流强度"


@pytest.mark.unit
def test_market_param_reaches_assembly(client, calls):
    r = client.get(f"{_PREFIX}/experience-memory", params={"market": "csi300"})
    assert r.status_code == 200
    assert r.json()["data"]["market"] == "csi300"
    assert calls == [{"user_id": "u1", "market": "csi300"}]


@pytest.mark.unit
def test_empty_sources_still_200(client, monkeypatch):
    async def fake_load(**_kwargs):
        return em.build_memory(market="a_share")

    monkeypatch.setattr(em, "load_memory", fake_load)
    r = client.get(f"{_PREFIX}/experience-memory")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False, "全空如实降级，不 404 不报错"
    assert data["reason_buckets"] == [] and data["saturated_directions"] == []
