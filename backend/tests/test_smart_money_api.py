"""聪明钱 API 六端点契约测试（/api/v1/market-analysis/smart-money/*）。

钉住的行为：
- JWT 保护（无凭证 401）；
- 无产物（latest.json 缺失）→ 404 detail="smart_money_not_built"（overview/stocks/
  branches/dates 统一收敛）；evidence 生命周期独立（验证先于构建，单独判定）；
- overview = latest 统计块 + trends 合并（items 不透出）；stocks limit 截断 +
  total 全量；缺失字段 null 原样透传（绝不补 0）；
- stock/{symbol}：前后缀/裸码三写法同命中；未上榜 200（on_board=false）；
  时间线读原始 parquet、北向序列分数 → 百分点；
- branches：window 5|20 两路径（5 窗外的营业部剔除），非法 window 422；
- dates：scores 分区日期 YYYY-MM-DD 升序。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.api.market_analysis import router as mar
from backend.services.api.user_app.middleware.auth import get_current_user
from backend.shared.smart_money import datasets

USER = {"tenant_id": "default", "user_id": "10000001", "sub": "10000001"}
BASE = "/api/v1/market-analysis/smart-money"
TODAY = pd.Timestamp.today().normalize()
TODAY_S = TODAY.strftime("%Y-%m-%d")


class _Env:
    def __init__(self, tmp_path: Path):
        self.product = tmp_path / "product" / "smart_money"  # QM_MARKET_SNAPSHOT_DIR/smart_money
        self.qdb = tmp_path / "quantdb"
        self.raw = self.qdb / "2_base_sector" / "smart_money"
        self.product.mkdir(parents=True)
        self.raw.mkdir(parents=True)  # 让 QM_QUANTDB_DATA_DIR 非空（resolve 才采纳）


@pytest.fixture()
def env(tmp_path, monkeypatch) -> _Env:
    e = _Env(tmp_path)
    datasets._cache.clear()
    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path / "product"))
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(e.qdb))
    return e


@pytest.fixture()
def client(env) -> TestClient:
    app = FastAPI()
    app.include_router(mar.router)
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    return TestClient(app)


@pytest.fixture()
def raw_client(env) -> TestClient:
    app = FastAPI()
    app.include_router(mar.router)
    return TestClient(app)


def _write_json(dir_: Path, name: str, payload) -> None:
    (dir_ / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _item(symbol: str, name: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "industry": "银行",
        "score": score,
        "covered": 1,
        "n_pillars": 1,
        "pct": {"inst_net_ratio_5d": 100.0},
        "val": {"inst_net_ratio_5d": 0.1},
        "inst_net_5d": 3e7,
        "lhb_net_5d": None,  # 缺失 null：端点必须原样透传，绝不补 0
        "margin_net_5d": None,
        "north_q_delta_pp": 1.0,
        "latest_event": {"type": "lhb", "date": TODAY_S},
    }


def _latest(**overrides) -> dict:
    payload = {
        "as_of": TODAY_S,
        "generated_at": "2026-10-16T12:00:00+00:00",
        "admitted": ["inst_net_ratio_5d"],
        "pillars": [{"name": "inst_net_ratio_5d", "label": "机构净买占比·5日", "unit": "ratio", "direction": 1}],
        "summary": {"pool_size": 2, "covered_mean": 1.0},
        "overview": {"lhb": {"date": TODAY_S, "n_stocks": 2}},
        "items": [_item("SH600036", "测试甲", 83.33), _item("SZ000001", "测试乙", 66.67)],
        "freshness": {"sources": {}, "margin_last": None, "kline_last": None},
        "partial": False,
    }
    payload.update(overrides)
    return payload


def _write_latest(env: _Env, **overrides) -> None:
    _write_json(env.product, "latest.json", _latest(**overrides))


# ---------------------------------------------------------------- 认证 / 404

def test_无凭证401(raw_client):
    assert raw_client.get(f"{BASE}/overview").status_code == 401


@pytest.mark.parametrize("path", ["overview", "stocks", "branches", "dates", "evidence"])
def test_无产物404统一码(client, path):
    r = client.get(f"{BASE}/{path}")
    assert r.status_code == 404
    assert r.json()["detail"] == "smart_money_not_built"


# ---------------------------------------------------------------- overview

def test_overview合并趋势且不透出items(client, env):
    _write_latest(env)
    _write_json(env.product, "trends.json", {
        "as_of": TODAY_S, "generated_at": "x", "lhb": [{"date": TODAY_S, "lhb_n_stocks": 2}],
        "margin": [{"date": TODAY_S, "margin_net_5d_yi": None}],
    })

    r = client.get(f"{BASE}/overview")

    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == TODAY_S
    assert body["summary"]["pool_size"] == 2
    assert body["overview"]["lhb"]["n_stocks"] == 2
    assert body["trends"]["lhb"][0]["lhb_n_stocks"] == 2
    assert "as_of" not in body["trends"]  # trends 头部字段不混入序列体
    assert body["partial"] is False
    assert "items" not in body  # 选股榜走 /stocks


# ---------------------------------------------------------------- stocks

def test_stocks_limit截断与total全量(client, env):
    _write_latest(env)

    r = client.get(f"{BASE}/stocks", params={"limit": 1})

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["symbol"] == "SH600036"
    assert body["items"][0]["lhb_net_5d"] is None  # null 原样透传
    assert body["pillars"][0]["label"] == "机构净买占比·5日"


def test_stocks_limit越界422(client, env):
    _write_latest(env)
    assert client.get(f"{BASE}/stocks", params={"limit": 0}).status_code == 422
    assert client.get(f"{BASE}/stocks", params={"limit": 501}).status_code == 422


# ---------------------------------------------------------------- stock 档案

def _write_stock_raw(env: _Env) -> None:
    pd.DataFrame(
        [{"trade_date": TODAY - pd.Timedelta(days=2), "symbol": "600036.SH",
          "reason": "日涨幅偏离值达7%", "lhb_net_buy": 1e7, "net_ratio": 0.03}]
    ).to_parquet(env.raw / "lhb_detail.parquet")
    for q, pct, rd in (("2025Q4", 0.01, "2025-12-31"), ("2026Q1", 0.015, "2026-03-31")):
        d = env.qdb / "2_base_sector" / "hsgt_north" / f"quarter={q}"
        d.mkdir(parents=True)
        pd.DataFrame(
            [{"symbol": "600036.SH", "stock_name": "测试甲", "holding_quantity": 1,
              "holding_percentage": pct, "report_date": rd}]
        ).to_parquet(d / "data.parquet")


@pytest.mark.parametrize("symbol", ["SH600036", "600036.SH", "600036"])
def test_stock档案三写法同命中(client, env, symbol):
    _write_latest(env)
    _write_stock_raw(env)

    r = client.get(f"{BASE}/stock/{symbol}")

    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SH600036"
    assert body["on_board"] is True
    assert body["item"]["score"] == pytest.approx(83.33)
    assert body["name"] == "测试甲"
    assert [e["type"] for e in body["timeline"]] == ["lhb"]
    assert body["timeline"][0]["date"] == (TODAY - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    # 北向：分数 → 百分点；环比差逐季
    north = body["north"]
    assert north[0]["quarter"] == "2025Q4" and north[0]["pct_pp"] == pytest.approx(1.0)
    assert north[0]["chg_pp"] is None  # 首季无前值
    assert north[1]["pct_pp"] == pytest.approx(1.5)
    assert north[1]["chg_pp"] == pytest.approx(0.5)


def test_stock未上榜仍200空档案(client, env):
    _write_latest(env)

    r = client.get(f"{BASE}/stock/SZ000002")

    assert r.status_code == 200
    body = r.json()
    assert body["on_board"] is False
    assert body["item"] is None
    assert body["timeline"] == [] and body["north"] == []


# ---------------------------------------------------------------- branches

def _write_branch_raw(env: _Env) -> pd.DatetimeIndex:
    cal = pd.bdate_range(end=TODAY, periods=30)
    for d in cal:
        (env.qdb / "1_kline_data" / "daily_forward" / f"dt={d:%Y%m%d}").mkdir(parents=True)
    rows = [
        {"trade_date": cal[-10], "branch_name": "远窗部", "net_amount": 9e8,
         "buy_amount": 9.5e8, "sell_amount": 5e7, "stocks_bought": "远"},
        {"trade_date": cal[-1], "branch_name": "次近部", "net_amount": 5e8,
         "buy_amount": 6e8, "sell_amount": 1e8, "stocks_bought": "丙"},
        {"trade_date": cal[-2], "branch_name": "近窗部", "net_amount": 1e8,
         "buy_amount": 1.2e8, "sell_amount": 2e7, "stocks_bought": "乙"},
    ]
    pd.DataFrame(rows).to_parquet(env.raw / "lhb_branch.parquet")
    return cal


def test_branches窗口5与20的聚合边界(client, env):
    _write_latest(env)
    _write_branch_raw(env)

    r5 = client.get(f"{BASE}/branches", params={"window": 5})
    r20 = client.get(f"{BASE}/branches", params={"window": 20})

    assert r5.status_code == 200 and r20.status_code == 200
    assert [b["branch_name"] for b in r5.json()["branches"]] == ["次近部", "近窗部"]
    assert [b["branch_name"] for b in r20.json()["branches"]] == ["远窗部", "次近部", "近窗部"]
    assert r20.json()["branches"][0]["net_amount"] == pytest.approx(9e8)
    assert r5.json()["window"] == 5 and r20.json()["window"] == 20


def test_branches非法窗口400(client, env):
    # 照本 router 惯例（period_invalid 先例）：int 入参 + handler 白名单校验
    _write_latest(env)
    r = client.get(f"{BASE}/branches", params={"window": 7})
    assert r.status_code == 400
    assert r.json()["detail"] == "window_invalid"


# ---------------------------------------------------------------- evidence / dates

def test_evidence生命周期独立于构建(client, env):
    # 未构建（无 latest）但验证已跑：evidence 照常 200（验证先于构建）
    r0 = client.get(f"{BASE}/evidence")
    assert r0.status_code == 404 and r0.json()["detail"] == "smart_money_not_built"

    _write_json(env.product, "evidence.json", {
        "admitted": ["inst_net_ratio_5d"], "signals": [], "marker": "ev-ok",
    })

    r = client.get(f"{BASE}/evidence")
    assert r.status_code == 200
    assert r.json()["marker"] == "ev-ok"
    assert r.json()["admitted"] == ["inst_net_ratio_5d"]


def test_dates列出评分分区升序(client, env):
    _write_latest(env)
    for d in ("20261014", "20261015"):
        dd = env.product / "scores" / f"dt={d}"
        dd.mkdir(parents=True)
        pd.DataFrame([{"symbol": "A.SH", "score": 1.0}]).to_parquet(dd / "data.parquet")

    r = client.get(f"{BASE}/dates")

    assert r.status_code == 200
    body = r.json()
    assert body["dates"] == ["2026-10-14", "2026-10-15"]
    assert body["as_of"] == TODAY_S
