"""方向配额预览 API 面（``GET /api/v1/alpha-agent/direction-quota``，T-FE-10）。

钉四件事：

1. **鉴权与信封**：无身份 → 401；有身份 → ``{"code": 200, "data": ...}``；
2. **参数直通**：鉴权身份（非 query）决定 user_id；``market`` / ``directions``
   / ``n`` 直通 ``preview``（默认 ``a_share`` / 空词表 / ``DEFAULT_PREVIEW_N``，
   候选词表剥空白、弃空串）；
3. **同闸上限**：``n`` 与 /evolve 的 num_directions 同闸（1..10），越界 422；
4. **失败如实降级**：读取失败 → 200 + ``available=false`` + 稳定文案
   （不是接口错误，不 5xx，不泄内部错误原文）。

打分/配额组装是 ``test_direction_quota.py`` 的职责；本文件只钉路由接线，
``preview`` 用桩（不碰真库），失败分支走真 ``preview`` 但桩掉 ``_load_inputs``。

用 ``TestClient`` mini-app（照 ``test_alpha_agent_experience_memory_face.py`` 惯例）。
"""

from __future__ import annotations

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.alpha_agent import direction_quota as dq
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
    """桩掉 preview：记 kwargs、返回最小可序列化组装。"""

    async def fake_preview(**kwargs):
        calls.append(kwargs)
        return {
            "available": True,
            "market": kwargs.get("market"),
            "n": kwargs.get("n"),
            "quota": {"explore": 0.3, "exploit": 0.5, "mutate": 0.2},
            "slots": {"explore": 1, "exploit": 1, "mutate": 0},
            "saturated_threshold": 2,
            "recent_success_tasks": 5,
            "directions": [],
            "tiers": None,
            "recent_success_dirs": [],
            "error": None,
        }

    calls: list[dict] = []
    monkeypatch.setattr(dq, "preview", fake_preview)
    return calls


@pytest.mark.unit
def test_requires_identity():
    r = _make_client(with_identity=False).get(f"{_PREFIX}/direction-quota")
    assert r.status_code == 401


@pytest.mark.unit
def test_envelope_and_defaults(client, calls):
    r = client.get(f"{_PREFIX}/direction-quota")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert calls == [
        {"user_id": "u1", "market": "a_share", "directions": [], "n": 3}
    ], "默认参数直通组装（DEFAULT_PREVIEW_N=3、空词表）"

    data = body["data"]
    assert {
        "available",
        "market",
        "n",
        "quota",
        "slots",
        "saturated_threshold",
        "recent_success_tasks",
        "directions",
        "tiers",
        "recent_success_dirs",
        "error",
    } <= set(data)
    assert data["market"] == "a_share"


@pytest.mark.unit
def test_directions_and_n_pass_through(client, calls):
    r = client.get(
        f"{_PREFIX}/direction-quota",
        params=[
            ("market", "us_stock"),
            ("n", "5"),
            ("directions", " 甲 "),
            ("directions", ""),
            ("directions", "乙"),
        ],
    )
    assert r.status_code == 200
    assert calls == [
        {"user_id": "u1", "market": "us_stock", "directions": ["甲", "乙"], "n": 5}
    ], "候选词表剥空白、弃空串，market/n 原样直通"


@pytest.mark.unit
@pytest.mark.parametrize("bad_n", ["0", "11"])
def test_n_uses_same_gate_as_evolve(client, bad_n):
    r = client.get(f"{_PREFIX}/direction-quota", params={"n": bad_n})
    assert r.status_code == 422, "n 与 /evolve num_directions 同闸（1..10）"


@pytest.mark.unit
def test_reader_failure_degrades_to_unavailable_not_5xx(client, monkeypatch):
    """真 preview + 桩掉输入读取：200 + available=false + 稳定文案。"""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("db down: secret dsn")

    monkeypatch.setattr(dq, "_load_inputs", boom)
    r = client.get(f"{_PREFIX}/direction-quota", params={"directions": ["甲"], "n": 2})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False
    assert "配额预览暂不可用" in data["error"]
    assert "db down" not in data["error"], "内部错误原文只进日志不上屏"
    assert data["directions"] == []
