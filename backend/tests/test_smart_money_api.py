"""聪明钱 API 契约测试（/api/v1/market-analysis/smart-money/*）。

钉住的行为：
- JWT 保护（无凭证 401）；
- 无产物（latest.json 缺失）→ 404 detail="smart_money_not_built"（overview/stocks/
  branches/dates/track-record/event-study/seats 统一收敛）；evidence 生命周期独立
  （验证先于构建，单独判定）；
- overview = latest 统计块 + trends 合并（items 不透出）+ 北向边界文案；
- stocks：筛选（event/industry/min_score）+ 分页（offset/limit，total=筛选后总数）；
  ``?date=`` 历史快照命中最近 ≤date 分区、as_of 标注真实快照日、条目富化现算；
  早于最早分区 → 404 smart_money_no_snapshot、格式错 → 400 date_invalid；
- stock/{symbol}：前后缀/裸码三写法同命中；未上榜 200（on_board=false）；
  时间线读原始 parquet、北向序列分数 → 百分点；最近一次龙虎榜席位构成 +
  近期大宗明细 + 事件族引用；
- branches：window 5|20 两路径（5 窗外的营业部剔除），非法 window 422；
- seats：window/class 过滤 + sort（win_rate 窗内样本不足沉底 / net 全史净买），
  非法参数 400；track-record / event-study 原样透传；
- dates：scores 分区日期 YYYY-MM-DD 升序；
- 缺失字段 null 原样透传（绝不补 0）。
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
        self.product = (
            tmp_path / "product" / "smart_money"
        )  # QM_MARKET_SNAPSHOT_DIR/smart_money
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
        "pillars": [
            {
                "name": "inst_net_ratio_5d",
                "label": "机构净买占比·5日",
                "unit": "ratio",
                "direction": 1,
            }
        ],
        "summary": {"pool_size": 2, "covered_mean": 1.0},
        "overview": {"lhb": {"date": TODAY_S, "n_stocks": 2}},
        "items": [
            _item("SH600036", "测试甲", 83.33),
            _item("SZ000001", "测试乙", 66.67),
        ],
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


@pytest.mark.parametrize(
    "path",
    [
        "overview",
        "stocks",
        "branches",
        "dates",
        "evidence",
        "track-record",
        "event-study",
        "seats",
    ],
)
def test_无产物404统一码(client, path):
    r = client.get(f"{BASE}/{path}")
    assert r.status_code == 404
    assert r.json()["detail"] == "smart_money_not_built"


# ---------------------------------------------------------------- overview


def test_overview合并趋势且不透出items(client, env):
    _write_latest(env)
    _write_json(
        env.product,
        "trends.json",
        {
            "as_of": TODAY_S,
            "generated_at": "x",
            "lhb": [{"date": TODAY_S, "lhb_n_stocks": 2}],
            "margin": [{"date": TODAY_S, "margin_net_5d_yi": None}],
        },
    )

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


def test_stocks筛选与分页(client, env):
    _write_latest(env)
    # event= = 最近事件类型等值（两条 item 最近事件均为 lhb）
    assert client.get(f"{BASE}/stocks", params={"event": "lhb"}).json()["total"] == 2
    assert client.get(f"{BASE}/stocks", params={"event": "survey"}).json()["total"] == 0
    r = client.get(f"{BASE}/stocks", params={"event": "nope"})
    assert r.status_code == 400 and r.json()["detail"] == "event_invalid"
    # industry 精确匹配
    assert (
        client.get(f"{BASE}/stocks", params={"industry": "银行"}).json()["total"] == 2
    )
    assert (
        client.get(f"{BASE}/stocks", params={"industry": "白酒"}).json()["total"] == 0
    )
    # min_score 下限
    b = client.get(f"{BASE}/stocks", params={"min_score": 70}).json()
    assert b["total"] == 1 and b["items"][0]["symbol"] == "SH600036"
    # offset/limit（total 为筛选后总数，不受分页影响）
    b2 = client.get(f"{BASE}/stocks", params={"offset": 1, "limit": 1}).json()
    assert b2["total"] == 2
    assert [i["symbol"] for i in b2["items"]] == ["SZ000001"]
    assert b2["offset"] == 1 and b2["requested_date"] is None


def _write_snapshot_raw(env: _Env) -> pd.DatetimeIndex:
    """历史快照夹具：10 日行情日历 + 两张评分分区（cal5/cal7）+ 机构席位事件。"""
    cal = pd.bdate_range(end=TODAY, periods=10)
    for d in cal:
        (env.qdb / "1_kline_data" / "daily_forward" / f"dt={d:%Y%m%d}").mkdir(
            parents=True
        )
    for d, score in ((cal[5], 90.0), (cal[7], 10.0)):
        dd = env.product / "scores" / f"dt={d:%Y%m%d}"
        dd.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "symbol": "600036.SH",
                    "score": score,
                    "covered": 1,
                    "n_pillars": 1,
                    "pct_inst_net_ratio_5d": 100.0,
                    "val_inst_net_ratio_5d": 0.05,
                }
            ]
        ).to_parquet(dd / "data.parquet")
    pd.DataFrame(
        [
            {
                "trade_date": cal[5],
                "symbol": "600036.SH",
                "reason": "x",
                "inst_net_buy": 3e7,
                "market_amount": 1e9,
            }
        ]
    ).to_parquet(env.raw / "lhb_inst.parquet")
    return cal


def test_stocks日期路径命中最近分区并现算富化(client, env):
    _write_latest(env)
    cal = _write_snapshot_raw(env)

    # Act：请求 cal[6]（无分区日）→ 命中 cal[5]
    r = client.get(f"{BASE}/stocks", params={"date": cal[6].strftime("%Y-%m-%d")})

    # Assert
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == cal[5].strftime("%Y-%m-%d")  # 真实快照日（≤ 请求日）
    assert body["requested_date"] == cal[6].strftime("%Y-%m-%d")
    assert body["total"] == 1
    it = body["items"][0]
    assert it["symbol"] == "SH600036"
    assert it["score"] == pytest.approx(90.0)
    assert it["inst_net_5d"] == pytest.approx(3e7)  # 该日 5 日窗现算
    assert it["latest_event"] == {
        "type": "inst",
        "date": cal[5].strftime("%Y-%m-%d"),
    }
    assert body["summary"]["pool_size"] == 1

    # 精确命中 cal[7]：得分换批、5 日窗仍含 cal[5] 事件
    b2 = client.get(
        f"{BASE}/stocks", params={"date": cal[7].strftime("%Y-%m-%d")}
    ).json()
    assert b2["as_of"] == cal[7].strftime("%Y-%m-%d")
    assert b2["items"][0]["score"] == pytest.approx(10.0)
    assert b2["items"][0]["inst_net_5d"] == pytest.approx(3e7)


def test_stocks日期路径边界404与400(client, env):
    _write_latest(env)
    cal = _write_snapshot_raw(env)

    # 早于最早分区（cal5）→ 404
    r = client.get(f"{BASE}/stocks", params={"date": cal[0].strftime("%Y-%m-%d")})
    assert r.status_code == 404
    assert r.json()["detail"] == "smart_money_no_snapshot"
    # 格式错（非 YYYY-MM-DD）→ 400（防 pandas 把 20261014 解析成 epoch 纳秒）
    r2 = client.get(f"{BASE}/stocks", params={"date": "2026-1-1"})
    assert r2.status_code == 400 and r2.json()["detail"] == "date_invalid"
    r3 = client.get(f"{BASE}/stocks", params={"date": "20261014"})
    assert r3.status_code == 400 and r3.json()["detail"] == "date_invalid"


# ---------------------------------------------------------------- stock 档案


def _write_stock_raw(env: _Env) -> None:
    pd.DataFrame(
        [
            {
                "trade_date": TODAY - pd.Timedelta(days=2),
                "symbol": "600036.SH",
                "reason": "日涨幅偏离值达7%",
                "lhb_net_buy": 1e7,
                "net_ratio": 0.03,
            }
        ]
    ).to_parquet(env.raw / "lhb_detail.parquet")
    for q, pct, rd in (("2025Q4", 0.01, "2025-12-31"), ("2026Q1", 0.015, "2026-03-31")):
        d = env.qdb / "2_base_sector" / "hsgt_north" / f"quarter={q}"
        d.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "symbol": "600036.SH",
                    "stock_name": "测试甲",
                    "holding_quantity": 1,
                    "holding_percentage": pct,
                    "report_date": rd,
                }
            ]
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
    assert body["timeline"][0]["date"] == (TODAY - pd.Timedelta(days=2)).strftime(
        "%Y-%m-%d"
    )
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
    # 扩展段：无原始数据时同样为空（不臆造）
    assert body["seats"] is None and body["dzjy"] == []
    assert body["study_ref"]["families"] == []


def test_stock席位构成与大宗明细(client, env):
    _write_latest(env)
    _write_stock_raw(env)  # 提供 lhb 时间线（study_ref 引族）
    d1, d2 = TODAY - pd.Timedelta(days=2), TODAY - pd.Timedelta(days=1)
    pd.DataFrame(
        [
            # 前一日 → 非最近，不得出现
            {
                "trade_date": d1,
                "symbol": "600036.SH",
                "side": "buy",
                "seat_name": "机构专用",
                "buy_amount": 5e7,
                "sell_amount": 0.0,
                "net_amount": 5e7,
                "buy_ratio": 0.05,
                "sell_ratio": 0.0,
            },
            # 最近一日：买卖两榜 + 不相关标的（不得混入）
            {
                "trade_date": d2,
                "symbol": "600036.SH",
                "side": "buy",
                "seat_name": "机构专用",
                "buy_amount": 6e7,
                "sell_amount": 0.0,
                "net_amount": 6e7,
                "buy_ratio": 0.06,
                "sell_ratio": 0.0,
            },
            {
                "trade_date": d2,
                "symbol": "600036.SH",
                "side": "sell",
                "seat_name": "华鑫证券上海分公司",
                "buy_amount": 0.0,
                "sell_amount": 2e7,
                "net_amount": -2e7,
                "buy_ratio": 0.0,
                "sell_ratio": 0.02,
            },
            {
                "trade_date": d2,
                "symbol": "999999.SZ",
                "side": "buy",
                "seat_name": "别的席位",
                "buy_amount": 1e9,
                "sell_amount": 0.0,
                "net_amount": 1e9,
                "buy_ratio": 0.5,
                "sell_ratio": 0.0,
            },
        ]
    ).to_parquet(env.raw / "lhb_seat.parquet")
    dz_rows = [
        {
            "trade_date": TODAY - pd.Timedelta(days=i + 1),
            "symbol": "600036.SH",
            "deal_price": 10.0,
            "close": 10.2,
            "premium": -0.02,
            "volume": 1e6,
            "amount": 1e7,
            "amount_ratio": 0.001,
            "seat_buyer": "机构专用" if i == 0 else "某营业部",
            "seat_seller": "某营业部",
        }
        for i in range(21)  # 21 笔 → 只回最近 20
    ]
    pd.DataFrame(dz_rows).to_parquet(env.raw / "dzjy_detail.parquet")

    # Act
    r = client.get(f"{BASE}/stock/SH600036")

    # Assert
    assert r.status_code == 200
    body = r.json()
    seats = body["seats"]
    assert seats["date"] == d2.strftime("%Y-%m-%d")  # 只取最近一次
    assert len(seats["seats"]) == 2  # 该股该日 2 席（不含无关标的）
    assert seats["seats"][0]["side"] == "buy"  # buy 榜在前
    by_name = {s["seat_name"]: s for s in seats["seats"]}
    assert by_name["机构专用"]["class"] == "inst"
    assert by_name["机构专用"]["buy_amount"] == pytest.approx(6e7)
    assert by_name["华鑫证券上海分公司"]["class"] == "branch"
    assert by_name["华鑫证券上海分公司"]["net_amount"] == pytest.approx(-2e7)

    dz = body["dzjy"]
    assert len(dz) == 20  # 截断到 20 笔
    assert dz[0]["date"] == (TODAY - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert dz[-1]["date"] == (TODAY - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    assert dz[0]["buyer_is_inst"] is True and dz[0]["seller_is_inst"] is False
    assert dz[1]["buyer_is_inst"] is False

    assert body["study_ref"]["families"] == ["龙虎榜"]  # 时间线含 lhb
    assert body["study_ref"]["endpoint"].endswith("/smart-money/event-study")


# ---------------------------------------------------------------- branches


def _write_branch_raw(env: _Env) -> pd.DatetimeIndex:
    cal = pd.bdate_range(end=TODAY, periods=30)
    for d in cal:
        (env.qdb / "1_kline_data" / "daily_forward" / f"dt={d:%Y%m%d}").mkdir(
            parents=True
        )
    rows = [
        {
            "trade_date": cal[-10],
            "branch_name": "远窗部",
            "net_amount": 9e8,
            "buy_amount": 9.5e8,
            "sell_amount": 5e7,
            "stocks_bought": "远",
        },
        {
            "trade_date": cal[-1],
            "branch_name": "次近部",
            "net_amount": 5e8,
            "buy_amount": 6e8,
            "sell_amount": 1e8,
            "stocks_bought": "丙",
        },
        {
            "trade_date": cal[-2],
            "branch_name": "近窗部",
            "net_amount": 1e8,
            "buy_amount": 1.2e8,
            "sell_amount": 2e7,
            "stocks_bought": "乙",
        },
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
    assert [b["branch_name"] for b in r20.json()["branches"]] == [
        "远窗部",
        "次近部",
        "近窗部",
    ]
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

    _write_json(
        env.product,
        "evidence.json",
        {
            "admitted": ["inst_net_ratio_5d"],
            "signals": [],
            "marker": "ev-ok",
        },
    )

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


# ---------------------------------------------------------------- track-record / event-study


def test_track_record与event_study透传(client, env):
    _write_latest(env)
    _write_json(env.product, "track_record.json", {"marker": "tr-ok", "recent": []})
    _write_json(env.product, "event_study.json", {"marker": "es-ok", "families": {}})

    assert client.get(f"{BASE}/track-record").json()["marker"] == "tr-ok"
    assert client.get(f"{BASE}/event-study").json()["marker"] == "es-ok"


# ---------------------------------------------------------------- seats


def _seats_payload() -> dict:
    def w(n: int, wr: float) -> dict:
        return {"n": n, "win_rate": wr, "avg_excess_bp": 100.0}

    return {
        "as_of": TODAY_S,
        "generated_at": "2026-10-16T12:00:00+00:00",
        "horizon": 5,
        "min_trades": 5,
        "windows": ["20", "60", "all"],
        "baseline_win_rate": {"20": 0.5, "60": 0.55, "all": 0.52},
        "n_events": 10,
        "classes": [{"class": "inst", "label": "机构专用"}],
        "seats": [
            {
                "seat_name": "甲席位",
                "class": "inst",
                "class_label": "机构专用",
                "total_net_yi": 3.0,
                "last_seen": "2026-10-10",
                "windows": {"20": w(8, 0.75), "60": w(8, 0.75), "all": w(8, 0.75)},
            },
            {
                "seat_name": "乙席位",
                "class": "inst",
                "class_label": "机构专用",
                "total_net_yi": 9.0,
                "last_seen": "2026-10-12",
                "windows": {"20": w(1, 1.0), "60": w(1, 1.0), "all": w(1, 1.0)},
            },
            {
                "seat_name": "丙席位",
                "class": "branch",
                "class_label": "券商席位",
                "total_net_yi": 1.0,
                "last_seen": "2026-10-09",
                "windows": {"20": w(9, 0.6), "60": w(9, 0.6), "all": w(9, 0.6)},
            },
        ],
    }


def test_seats窗排序与样本不足沉底(client, env):
    _write_latest(env)
    _write_json(env.product, "seats.json", _seats_payload())

    # Act：win_rate 排序（window=20）
    body = client.get(
        f"{BASE}/seats", params={"window": "20", "sort": "win_rate"}
    ).json()

    # Assert：乙 n=1 < min_trades(5) → 沉底（1 笔 100% 不冒充聪明）；
    # 甲 0.75 > 丙 0.6
    assert [i["seat_name"] for i in body["items"]] == ["甲席位", "丙席位", "乙席位"]
    assert body["window"] == "20" and body["sort"] == "win_rate"
    assert body["baseline_win_rate"] == 0.5  # 选中窗的 baseline（非全史）
    assert body["total"] == 3 and body["n_events"] == 10

    # sort=net：全史净买额（窗口无关）
    b2 = client.get(f"{BASE}/seats", params={"window": "all", "sort": "net"}).json()
    assert [i["seat_name"] for i in b2["items"]] == ["乙席位", "甲席位", "丙席位"]


def test_seats类过滤与limit与非法参数(client, env):
    _write_latest(env)
    _write_json(env.product, "seats.json", _seats_payload())

    b = client.get(f"{BASE}/seats", params={"class": "inst"}).json()
    assert [i["seat_name"] for i in b["items"]] == ["甲席位", "乙席位"]
    assert b["total"] == 2 and b["class"] == "inst"

    b2 = client.get(f"{BASE}/seats", params={"limit": 1}).json()
    assert len(b2["items"]) == 1 and b2["total"] == 3

    for params, code in (
        ({"window": "7"}, "window_invalid"),
        ({"sort": "x"}, "sort_invalid"),
        ({"class": "x"}, "class_invalid"),
    ):
        r = client.get(f"{BASE}/seats", params=params)
        assert r.status_code == 400
        assert r.json()["detail"] == code
