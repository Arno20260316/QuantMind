"""补池就绪状态卡接口（``GET /api/v1/admin/data-platform/pool-readiness``）的行为测试。

盯三件事：

1. **信封**：前端共享服务读 ``resp.data.data``，信封不对整页空白（与 ``/freshness`` 同风格）。
2. **段序与首断点**：状态卡要回答「断在哪一段」，段序是契约——前端按它排版。2026-10-13 的
   实况是 alpha 缺分区，接口必须把 ``blocked_stage`` 指到 ``alpha_library``，而不是笼统的
   「没有池」。
3. **探针失败不许谎报就绪**：PG 读不到时该段判不就绪（``ok=False``），不能说「没查到=没问题」。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.api.user_app.middleware.auth import require_admin
from backend.shared.pool_readiness import PoolSnapshot, Readiness, StageReport

PREFIX = "/api/v1/admin/data-platform"


def _stub_admin():
    return {"tenant_id": "default", "user_id": "10000001", "is_admin": True}


@pytest.fixture
def client(monkeypatch):
    """把 ``collect`` 换成替身，只测路由层（判据本体在 test_pool_readiness.py）。"""
    from backend.services.api.routers.admin import data_platform

    app = FastAPI()
    # 前缀照 ``backend/services/api/routers/admin/__init__.py`` 的真实挂法：
    # admin_router(prefix="/api/v1/admin") → data_platform_router(prefix="/data-platform")。
    app.include_router(data_platform.router, prefix=PREFIX)
    app.dependency_overrides[require_admin] = _stub_admin
    return TestClient(app), data_platform


def _ok_report() -> Readiness:
    stages = tuple(
        StageReport(key=k, label=k, ok=True, detail="ok", by_design=(k == "pool"))
        for k in ("kline", "alpha_library", "custom_dataset", "inference", "pool")
    )
    return Readiness(
        target="20261013",
        kline_dt="20261012",
        ok=True,
        by_design=True,
        blocked_stage=None,
        stages=stages,
    )


def test_envelope_and_stage_contract(client, monkeypatch):
    c, mod = client
    monkeypatch.setattr(
        mod, "_pool_readiness", lambda market, target=None: _ok_report()
    )

    resp = c.get(f"{PREFIX}/pool-readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["ok"] is True
    assert data["by_design"] is True
    assert data["blocked_stage"] is None
    assert [s["key"] for s in data["stages"]] == [
        "kline",
        "alpha_library",
        "custom_dataset",
        "inference",
        "pool",
    ]
    assert data["kline_dt"] == "20261012"
    assert data["target"] == "20261013"
    # 前端要能直接排版：每段必须自带 label 与 detail 原话
    assert all(s["label"] and s["detail"] for s in data["stages"])


def test_blocked_stage_is_reported_for_alpha_break(client, monkeypatch):
    """2026-10-13 实况：alpha 缺分区 → 首断点必须是 alpha_library。"""
    c, mod = client
    broken = Readiness(
        target="20261013",
        kline_dt="20261012",
        ok=False,
        by_design=False,
        blocked_stage="alpha_library",
        stages=(
            StageReport("kline", "日K分区", True, "最新 dt=20261012"),
            StageReport(
                "alpha_library",
                "alpha 因子库",
                False,
                "最新 dt=20261009，日K dt=20261012",
            ),
            StageReport(
                "custom_dataset",
                "自定义因子集",
                False,
                "最新 dt=20261009（上游未就绪）",
            ),
            StageReport(
                "inference",
                "当日推理批次",
                False,
                "data_trade_date=20261012 的已完成批次：无",
            ),
            StageReport(
                "pool", "候选池", False, "缺 20261013 的候选池文件（上游未就绪）"
            ),
        ),
    )
    monkeypatch.setattr(mod, "_pool_readiness", lambda market, target=None: broken)

    data = c.get(f"{PREFIX}/pool-readiness").json()["data"]
    assert data["ok"] is False
    assert data["blocked_stage"] == "alpha_library"
    assert data["blocked_label"]  # 界面直接显示「alpha 因子库」这类人话
    pool_stage = next(s for s in data["stages"] if s["key"] == "pool")
    assert pool_stage["ok"] is False


def test_probe_failure_yields_500_not_fake_ready(client, monkeypatch):
    """判据探针炸了 → 500（前端出错误态），**绝不**回一个「就绪」的信封。"""
    c, mod = client

    def boom(market, target=None):
        raise RuntimeError("pg down")

    monkeypatch.setattr(mod, "_pool_readiness", boom)
    resp = c.get(f"{PREFIX}/pool-readiness")
    assert resp.status_code == 500
    assert "pg down" in resp.json()["detail"]


def test_collect_default_path_uses_shared_judgement(monkeypatch):
    """默认路径必须落在共享判据上（不许路由层自己再写一套「有没有池」）。"""
    from backend.services.api.routers.admin import data_platform

    calls = []

    def fake_collect(**kw):
        calls.append(kw)
        return _ok_report()

    monkeypatch.setattr(
        "backend.shared.pool_readiness.collect", fake_collect, raising=True
    )
    report = data_platform._pool_readiness("A")
    assert isinstance(report, Readiness)
    # 不给 target 时**原样**把 None 交给共享判据：目标交易日由
    # pool_readiness.target_trade_day 按日历推（路由层不许自己算日期——两处两套必漂移）。
    assert "target" in calls[0]
    assert calls[0]["target"] is None
    assert callable(calls[0]["inference_probe"])

    # 给了 target 则原样转发（排障时按指定日查）。
    data_platform._pool_readiness("A", "20261013")
    assert calls[1]["target"] == "20261013"


def test_market_other_than_a_is_rejected(client):
    c, _ = client
    resp = c.get(f"{PREFIX}/pool-readiness", params={"market": "US"})
    assert resp.status_code == 400
