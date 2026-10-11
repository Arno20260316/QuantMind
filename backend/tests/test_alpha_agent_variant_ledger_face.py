"""变体残差台账 API 面（``GET /api/v1/alpha-agent/variant-ledger``，T-FE-09）。

只读投影钉三件事：

1. **缺席不是错误**：台账文件不存在 → 200 + 空骨架 ``available=false``
   （32 行恒在，「缺失一律 —」），绝不 404。
2. **池键控**：残差是池内口径——观测与冷却都按 ``universe`` 过滤；不给池
   则全量原样带出、``cooling_active=None``（「生效」不可判定就不判）。
3. **缺失一律 None**：无观测行的 ``latest``/``latest_stat`` 是 None
   （前端落「—」），绝不显 0。
4. **投影零句柄**：``run_id``（统计派任务 id）在观测与冷却判据两层都不
   回传（评审 security-F2）；台账是平台级共享事实池，不按用户过滤。

用 ``TestClient`` mini-app（照 ``test_alpha_agent_factor_lifecycle_face.py`` 惯例）。
"""

from __future__ import annotations

import json

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.alpha_agent.statistical import variant_ledger as vl
    from backend.services.engine.routers import alpha_agent as aa
except Exception:  # noqa: BLE001
    aa = None

pytestmark = pytest.mark.skipif(aa is None, reason="依赖不可用（需容器环境）")

_PREFIX = "/api/v1/alpha-agent"


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
def ledger_path(tmp_path, monkeypatch):
    path = tmp_path / "variant_ledger.json"
    monkeypatch.setenv("QM_VARIANT_LEDGER_PATH", str(path))
    return path


def _seed(
    path,
    *,
    key: str = "reversal_60d",
    universe: str = "all_a",
    ic_ir: float = 0.1,
    n_days: int = 60,
) -> None:
    """种一条高置信失败观测并执行判定（→ 该池生效冷却）。"""
    obs = {
        "variant_key": key,
        "universe": universe,
        "window_start": "2024-01-01",
        "window_end": "2024-12-31",
        "run_id": "r1",
        "horizons": [
            {
                "horizon": 5,
                "status": "ok",
                "ic_ir": ic_ir,
                "ic_mean": 0.01,
                "n_days": n_days,
                "basis": [vl.BASELINE_BY_KEY[key]],
                "n_basis": 1,
                "max_basis_abs_corr": 0.42,
            }
        ],
        "best_horizon": 5,
        "at": "2026-01-01T00:00:00Z",
    }
    ledger = vl.upsert_observations(vl.empty_ledger(), [obs])
    ledger, updates = vl.decide_cooling(ledger)
    assert any(u["action"] == "cooled" for u in updates), "种子前提：已入冷却"
    vl.save_ledger(path, ledger)


def _get(client, **params) -> dict:
    r = client.get(f"{_PREFIX}/variant-ledger", params=params)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    return body["data"]


def _row(data: dict, key: str) -> dict:
    return next(v for v in data["variants"] if v["key"] == key)


# ── 缺席不是错误 ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_ledger_returns_empty_skeleton(client, ledger_path):
    data = _get(client, universe="all_a")

    assert data["available"] is False
    assert len(data["variants"]) == 32, "骨架恒 32 行（注册序），前台表格稳定"
    assert data["cooling"] == [] and data["cooling_active"] == []
    row = _row(data, "reversal_60d")
    assert row["latest"] is None and row["latest_stat"] is None
    assert row["observations_count"] == 0
    assert row["baseline_key"] == "reversal_20d" and row["edit_type"] == "调窗"
    assert row["coolable"] is True
    root = _row(data, "reversal_5d")
    assert root["baseline_key"] is None and root["coolable"] is False


# ── 池键控（观测与冷却同裁） ──────────────────────────────────────────


@pytest.mark.unit
def test_seeded_ledger_matches_pool(client, ledger_path):
    _seed(ledger_path)
    data = _get(client, universe="all_a")

    assert data["available"] is True
    row = _row(data, "reversal_60d")
    assert row["observations_count"] == 1
    assert row["latest"]["window_end"] == "2024-12-31"
    assert row["latest_stat"]["status"] == "ok"
    assert row["latest_stat"]["ic_ir"] == pytest.approx(0.1)
    assert row["cooling_active"] is True
    assert data["cooling_active"] == ["reversal_60d"]
    assert data["constants"]["cool_icir_floor"] == 0.3
    # 投影零句柄（评审 security-F2）：run_id 不随观测/冷却判据出网
    assert "run_id" not in row["latest"]
    assert row["cooling"], "本用例应真有冷却条目"
    assert "run_id" not in row["cooling"][0]
    assert "run_id" not in row["cooling"][0]["evidence"]
    assert "run_id" not in data["cooling"][0]["evidence"]
    assert '"run_id"' not in json.dumps(data), "整包零句柄"


@pytest.mark.unit
def test_other_pool_sees_no_evidence(client, ledger_path):
    """换池请求不得看到别池的观测与冷却（跨池证据不可比）。"""
    _seed(ledger_path, universe="all_a")
    data = _get(client, universe="csi300")

    row = _row(data, "reversal_60d")
    assert row["observations_count"] == 0
    assert row["latest"] is None
    assert row["cooling"] == []
    assert row["cooling_active"] is False
    assert data["cooling"] == [] and data["cooling_active"] == []


@pytest.mark.unit
def test_no_universe_is_full_view_without_verdict(client, ledger_path):
    _seed(ledger_path)
    data = _get(client)

    row = _row(data, "reversal_60d")
    assert row["cooling_active"] is None, "池不明时「生效」不可判定 → None 不臆造"
    assert data["cooling_active"] is None
    assert len(row["cooling"]) == 1, "全量原样带出（条目自带池标签）"
    assert row["cooling"][0]["universe"] == "all_a"
