"""晋升流程列表被参数兜底路由遮蔽的回归（2026-10-09 修）。

症状：推理中心「晋升流程」工作区报 `Model not found`。
根因：`model_training_router`（prefix `/api/v1/models`）先于
`model_rollouts_router` 注册，且含参数兜底 `GET /{model_id}` →
`GET /api/v1/models/rollouts` 被当成 model_id="rollouts" 查模型 →
404 "Model not found"，rollouts 列表永远到不了自己的 handler。

口径（FastAPI 按注册顺序匹配，先中先赢）：**具体前缀的 router**
（rollouts / rolling）必须先于带 `/{model_id}` 兜底的 model_training 注册。
本测试在真实 app（main.py 的 include 顺序）上打一次请求，把这个顺序钉死。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_rollouts_list_not_shadowed_by_model_id_catchall(monkeypatch):
    # 整包导入 = 使用 main.py 真实的 include_router 顺序
    from backend.services.api.main import app
    from backend.services.api.routers import model_rollouts as mro
    from backend.services.api.user_app.middleware.auth import get_current_user

    async def _fake_list(**kwargs):
        return []

    monkeypatch.setattr(mro.model_rollout_service, "list_rollouts", _fake_list)
    app.dependency_overrides[get_current_user] = lambda: {
        "tenant_id": "default",
        "user_id": "10000001",
        "sub": "10000001",
    }
    try:
        resp = TestClient(app).get(
            "/api/v1/models/rollouts", params={"market": "CN"}
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    # 修前：404（被 model_training 的 GET /{model_id} 抢走，报 "Model not found"）
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}
