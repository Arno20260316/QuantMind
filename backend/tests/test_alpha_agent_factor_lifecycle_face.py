"""因子生命周期 API 装配面（``_decorate_factor_rows``）的出口契约（T-FE-06/07）。

评审定案（2026-10-11，code-reviewer HIGH-1 + security-reviewer MEDIUM）：

1. **双出口同裁**：``lifecycle`` 视图与 ``metadata.lifecycle`` 是同源数据的
   两个出口——observing（盲窗内）时读数（``last_eval``/``evidence``）在两个
   出口都必须不可见（旧实现只裁视图字段，metadata 裸奔 = 盲窗没盲）。
2. **fail-closed**：run 台账查询失败 → 证据不足，既不得把 pending 行冒充成
   「实验」（cross-market 回测流不写 ``rd_agents.status``，降级下无法证明
   「无成功回测」），也不得放行任何读数（盲窗终点不可证实 → 一律脱下）。
3. **互不拖累**：验证表查询失败只降级「验证中」展示态，不影响生命周期
   派生与读数可见性；run 台账失败也不 500。

用 ``TestClient`` mini-app（照 ``test_alpha_agent_factor_list_scope.py`` 惯例）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.factor_backtest import store as fb_store
    from backend.services.engine.mining_plugins import validation_store
    from backend.services.engine.routers import alpha_agent as aa
except Exception:  # noqa: BLE001
    aa = None

pytestmark = pytest.mark.skipif(aa is None, reason="依赖不可用（需容器环境）")

_PREFIX = "/api/v1/alpha-agent"

#: 读数哨兵：出现在任何出口都算盲窗泄漏。
_EVAL = {"month_end": "2026-09-30", "n_days": 150, "icir": -0.13, "streak": 3}
_EVIDENCE = {"month_end": "2026-09-30", "n_days": 150, "icir": -0.13, "streak": 3}


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _row(
    fid: str,
    *,
    created_days_ago: int,
    status: str = "pending",
    with_readings: bool = True,
) -> dict:
    meta: dict = {"task_id": "t1"}
    if with_readings:
        meta["lifecycle"] = {
            "last_eval": dict(_EVAL),
            "evidence": dict(_EVIDENCE),
        }
    return {
        "factor_id": fid,
        "factor_name": f"因子{fid}",
        "status": status,
        "created_at": _ago(created_days_ago),
        "metadata": meta,
    }


def _assert_no_readings(factor: dict) -> None:
    life = factor.get("metadata", {}).get("lifecycle") or {}
    assert "last_eval" not in life, (
        f"metadata 出口泄漏 last_eval：{factor['factor_id']}"
    )
    assert "evidence" not in life, f"metadata 出口泄漏 evidence：{factor['factor_id']}"


@pytest.fixture()
def client():
    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request, call_next):
        request.state.user = {"user_id": "u1", "tenant_id": "default"}
        return await call_next(request)

    app.include_router(aa.router)
    return TestClient(app)


@pytest.fixture()
def stubs(monkeypatch):
    """persistence + 两个装饰查询的可编程替身（不碰真库）。"""

    state: dict = {
        "rows": [],
        "first_at": {},
        "first_raises": None,
        "running": set(),
        "running_raises": None,
    }

    async def _first(ids):
        if state["first_raises"] is not None:
            raise state["first_raises"]
        return dict(state["first_at"])

    async def _running(ids):
        if state["running_raises"] is not None:
            raise state["running_raises"]
        return set(state["running"])

    async def _list_factors(**kwargs):
        return [dict(r) for r in state["rows"]]

    async def _factor_scope_stats(**kwargs):
        return {
            "total": len(state["rows"]),
            "high": 0,
            "medium": 0,
            "low": 0,
            "unknown": 0,
        }

    async def _get_factor(factor_id):
        for r in state["rows"]:
            if r["factor_id"] == factor_id:
                return dict(r)
        return None

    monkeypatch.setattr(
        aa,
        "persistence",
        SimpleNamespace(
            list_factors=_list_factors,
            factor_scope_stats=_factor_scope_stats,
            get_factor=_get_factor,
        ),
    )
    monkeypatch.setattr(fb_store, "first_completed_at", _first)
    monkeypatch.setattr(validation_store, "running_factor_ids", _running)
    return state


def _factors(client) -> list[dict]:
    r = client.get(f"{_PREFIX}/factors")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    return body["data"]["factors"]


# ── 双出口同裁（security-MEDIUM） ─────────────────────────────────────


@pytest.mark.unit
def test_observing_masks_readings_on_both_exits(client, stubs):
    stubs["rows"] = [_row("f-obs", created_days_ago=10)]
    stubs["first_at"] = {"f-obs": _ago(10)}  # blind = now+170d → observing

    (factor,) = _factors(client)

    assert factor["lifecycle"]["state"] == "observing"
    assert factor["lifecycle"]["last_eval"] is None
    assert factor["lifecycle"]["maturity"] == "观察"
    assert factor["tags"]["maturity"] == "观察"
    _assert_no_readings(factor)
    # 输入不可变：装饰不得改动持久层行对象（脱敏返回新 dict）
    assert stubs["rows"][0]["metadata"]["lifecycle"]["last_eval"] == _EVAL


@pytest.mark.unit
def test_active_row_keeps_readings_on_both_exits(client, stubs):
    stubs["rows"] = [_row("f-act", created_days_ago=400)]
    stubs["first_at"] = {"f-act": _ago(400)}  # blind 已过 → active

    (factor,) = _factors(client)

    assert factor["lifecycle"]["state"] == "active"
    assert factor["lifecycle"]["last_eval"]["month_end"] == "2026-09-30"
    assert factor["metadata"]["lifecycle"]["last_eval"]["month_end"] == "2026-09-30"


@pytest.mark.unit
def test_detail_endpoint_masks_too(client, stubs):
    stubs["rows"] = [_row("f-obs", created_days_ago=10)]
    stubs["first_at"] = {"f-obs": _ago(10)}

    r = client.get(f"{_PREFIX}/factors/f-obs")
    assert r.status_code == 200
    factor = r.json()["data"]
    assert factor["lifecycle"]["state"] == "observing"
    assert factor["lifecycle"]["last_eval"] is None
    _assert_no_readings(factor)


# ── fail-closed（code-reviewer HIGH-1） ──────────────────────────────


@pytest.mark.unit
def test_run_ledger_failure_is_fail_closed(client, stubs):
    stubs["rows"] = [
        _row("f-pending", created_days_ago=10),
        _row("f-done", created_days_ago=400, status="completed"),
    ]
    stubs["first_raises"] = RuntimeError("run ledger down")

    factors = {f["factor_id"]: f for f in _factors(client)}

    # 证据不足：pending 行不得冒充「实验」——不作声明（lifecycle=None）
    pending = factors["f-pending"]
    assert pending["lifecycle"] is None
    assert pending["tags"]["maturity"] == "unknown"
    _assert_no_readings(pending)

    # status=completed 是行自带的成功证据 → 保留派生；但读数一律脱下
    done = factors["f-done"]
    assert done["lifecycle"]["state"] == "active"
    assert done["lifecycle"]["last_eval"] is None
    _assert_no_readings(done)


@pytest.mark.unit
def test_validation_failure_degrades_only_display_flag(client, stubs):
    stubs["rows"] = [
        _row("f-obs", created_days_ago=10),
        _row("f-new", created_days_ago=10),
    ]
    stubs["first_at"] = {"f-obs": _ago(10)}
    stubs["running_raises"] = RuntimeError("validation table down")

    factors = {f["factor_id"]: f for f in _factors(client)}

    # 生命周期派生不受影响：observing 依旧 observing（且已脱敏）
    obs = factors["f-obs"]
    assert obs["lifecycle"]["state"] == "observing"
    assert obs["lifecycle"]["last_eval"] is None
    _assert_no_readings(obs)
    # 验证查询失败 ≠ 证据降级：无 run 行照常派生「实验」
    assert factors["f-new"]["lifecycle"]["state"] == "experiment"
    assert factors["f-new"]["lifecycle"]["maturity"] == "实验"
