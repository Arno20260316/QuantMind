"""因子概率板只读端点测试（/api/v1/market-analysis/factor-board，P1）。

口径（细案 §6.3）：
- JWT 保护：无凭证 401；
- 200：读 JSON 原样返回（字段透传）+ 附 available_dates；指定 date 命中该日文件；
- 404：全无产物 / 指定日无文件（detail 带 available_dates 供纠正）；
- 409：文件损坏 / 顶层非对象 / content decision_date 与请求日不符；
- 422：date 非 YYYY-MM-DD；
- 目录解析 = QM_FACTOR_BOARD_DIR（与产板 config.resolve_board_dir 同源）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.api.market_analysis import router as mar
from backend.services.api.user_app.middleware.auth import get_current_user

USER = {"tenant_id": "default", "user_id": "10000001", "sub": "10000001"}
BASE = "/api/v1/market-analysis/factor-board"


def _board(decision: str = "2026-10-12", trade: str = "2026-10-09") -> dict:
    """最小透传样本：端点只读不校验 schema，故意留 marker 键验原样。"""
    return {
        "schema_version": "factor_board/1.0",
        "meta": {"decision_date": decision, "trade_date": trade},
        "factors": [],
        "marker": "passthrough-ok",
    }


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(mar.router)
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    return TestClient(app)


@pytest.fixture()
def raw_client():
    app = FastAPI()
    app.include_router(mar.router)
    return TestClient(app)


@pytest.fixture()
def board_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(tmp_path))
    return tmp_path


def _write(dir_: Path, name: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (dir_ / name).write_text(text, encoding="utf-8")


# Arrange-Act-Assert

def test_requires_jwt_without_credentials(raw_client, board_dir):
    assert raw_client.get(BASE).status_code == 401


def test_returns_404_with_dates_when_no_product(client, board_dir):
    r = client.get(BASE)
    assert r.status_code == 404
    assert r.json()["detail"]["available_dates"] == []


def test_latest_passthrough_with_available_dates(client, board_dir):
    _write(board_dir, "2026-10-12.json", _board())
    _write(board_dir, "latest.json", _board())

    r = client.get(BASE)

    assert r.status_code == 200
    body = r.json()
    assert body["marker"] == "passthrough-ok"
    assert body["schema_version"] == "factor_board/1.0"
    assert body["available_dates"] == ["2026-10-12"]


def test_specific_date_hit(client, board_dir):
    _write(board_dir, "2026-10-10.json", _board(decision="2026-10-10"))
    _write(board_dir, "2026-10-12.json", _board())
    _write(board_dir, "latest.json", _board())

    r = client.get(BASE, params={"date": "2026-10-10"})

    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["decision_date"] == "2026-10-10"
    assert body["available_dates"] == ["2026-10-12", "2026-10-10"]


def test_unknown_date_404_lists_available(client, board_dir):
    _write(board_dir, "2026-10-12.json", _board())
    _write(board_dir, "latest.json", _board())

    r = client.get(BASE, params={"date": "2026-10-01"})

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "2026-10-01" in detail["message"]
    assert detail["available_dates"] == ["2026-10-12"]


def test_corrupt_latest_returns_409(client, board_dir):
    _write(board_dir, "latest.json", "{ not json")

    r = client.get(BASE)

    assert r.status_code == 409
    assert "损坏" in r.json()["detail"]


def test_non_object_latest_returns_409(client, board_dir):
    _write(board_dir, "latest.json", "[1, 2, 3]")

    r = client.get(BASE)

    assert r.status_code == 409
    assert "非对象" in r.json()["detail"]


def test_decision_date_mismatch_returns_409(client, board_dir):
    _write(board_dir, "2026-10-12.json", _board(decision="2026-10-11"))

    r = client.get(BASE, params={"date": "2026-10-12"})

    assert r.status_code == 409
    assert "decision_date" in r.json()["detail"]


def test_bad_date_format_returns_422(client, board_dir):
    r = client.get(BASE, params={"date": "2026/10/12"})
    assert r.status_code == 422
