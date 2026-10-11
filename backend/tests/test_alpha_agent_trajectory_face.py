"""任务决策轨迹 API 面（``GET /api/v1/alpha-agent/tasks/{task_id}/trajectory``，T-FM-07）。

钉五件事：

1. **鉴权**：无身份 → 401；有身份 → ``{"code": 200, "data": ...}``；
2. **归属收口**：任务行按 JWT 身份取 PG（``get_task(task_id, user_id=…)``），
   他人/不存在一律 404，不泄露存在性；
3. **两类 404 detail 可区分**：任务不存在 vs 任务存在但日志目录缺失；
4. **组装直通**：`load_trajectory` 收 (task 行, log_dir)，载荷原样出信封；
5. **非法 task_id 形状**：``resolve_task_log_dir`` 抛 ValueError → 404（防御纵深，
   不是 500、不落盘）。

扫描/组装是 ``test_trajectory_store.py`` 的职责；本文件只钉路由接线，用桩。

用 ``TestClient`` mini-app（照 ``test_alpha_agent_direction_quota_face.py`` 惯例）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.alpha_agent import task_store
    from backend.services.engine.alpha_agent import trajectory_store as ts
    from backend.services.engine.routers import alpha_agent as aa
except Exception:  # noqa: BLE001
    aa = None

pytestmark = pytest.mark.skipif(aa is None, reason="依赖不可用（需容器环境）")

_PREFIX = "/api/v1/alpha-agent"
_TASK = {
    "task_id": "d9a6e6a16a0445de",
    "user_id": "u1",
    "direction": "概念热度轮动",
    "status": "completed",
    "market": "a_share",
}


def _make_client(with_identity: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        if with_identity:
            request.state.user = {"user_id": "u1", "tenant_id": "default"}
        return await call_next(request)

    app.include_router(aa.router)
    return TestClient(app)


class _FakeStore:
    def __init__(self, task: dict | None) -> None:
        self._task = task
        self.calls: list[tuple[str, str | None]] = []

    async def get_task(self, task_id, *, user_id=None):
        self.calls.append((task_id, user_id))
        return self._task


def _wire(monkeypatch, *, task: dict | None, log_dir: Path) -> _FakeStore:
    store = _FakeStore(task)
    monkeypatch.setattr(task_store, "get_mining_task_store", lambda: store)
    monkeypatch.setattr(ts, "resolve_task_log_dir", lambda task_id, **kw: log_dir)
    return store


@pytest.mark.unit
def test_requires_identity():
    r = _make_client(with_identity=False).get(f"{_PREFIX}/tasks/x/trajectory")
    assert r.status_code == 401


@pytest.mark.unit
def test_unknown_task_404_not_found(monkeypatch, tmp_path):
    store = _wire(monkeypatch, task=None, log_dir=tmp_path)
    r = _make_client().get(f"{_PREFIX}/tasks/abc123/trajectory")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]
    assert store.calls == [("abc123", "u1")], "归属按 JWT 身份收口"


@pytest.mark.unit
def test_missing_log_dir_404_distinguishable(monkeypatch, tmp_path):
    _wire(monkeypatch, task=dict(_TASK), log_dir=tmp_path / "missing")
    r = _make_client().get(f"{_PREFIX}/tasks/{_TASK['task_id']}/trajectory")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "trajectory not found" in detail, "与「任务不存在」可区分"
    assert "log dir missing" in detail


@pytest.mark.unit
def test_unsafe_task_id_shape_404(monkeypatch, tmp_path):
    store = _wire(monkeypatch, task=dict(_TASK), log_dir=tmp_path)
    monkeypatch.setattr(
        ts,
        "resolve_task_log_dir",
        lambda task_id, **kw: (_ for _ in ()).throw(ValueError("unsafe")),
    )
    r = _make_client().get(f"{_PREFIX}/tasks/{_TASK['task_id']}/trajectory")
    assert r.status_code == 404
    assert "trajectory not found" in r.json()["detail"]
    assert store.calls, "任务行先于路径解析（PG 归属是第一道闸）"


@pytest.mark.unit
def test_envelope_and_payload_passthrough(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _wire(monkeypatch, task=dict(_TASK), log_dir=log_dir)

    seen: dict = {}

    async def fake_load(*, task, log_dir=None, now=None):
        seen["task"] = task
        seen["log_dir"] = log_dir
        scan = ts.WorkspaceScan(
            loops=(), dropped_loops=0, total_loops=0, has_load_error=False
        )
        return ts.build_trajectory(task=task, scan=scan)

    monkeypatch.setattr(ts, "load_trajectory", fake_load)

    r = _make_client().get(f"{_PREFIX}/tasks/{_TASK['task_id']}/trajectory")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["task_id"] == _TASK["task_id"]
    assert data["direction"] == "概念热度轮动"
    assert data["attribution"] == ts.ATTRIBUTION_LOOP
    assert data["loops"] == []
    assert data["partial"] is False
    assert seen["task"]["task_id"] == _TASK["task_id"]
    assert seen["log_dir"] == log_dir, "日志目录经 resolve 后直通组装"
