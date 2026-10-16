"""港股聪明钱 API 契约测试（/api/v1/market-analysis-hk/smart-money/*）。

钉住的行为：
- JWT 保护（无凭证 401）；
- 无产物 → 六端点统一 404 detail="hk_smart_money_not_built"（evidence 独立判定）；
- overview：类型面板富化（入柱标记 + 该类最强 |h10 IC| 变体）；trends 透传但不
  重复 as_of/generated_at；
- types：全网格 h5/h10/h20 结构与 horizon 校验（非法 400）；
- seats：窗口/类型/最少事件过滤 + 三种排序 + 收缩字段缺失（NaN→null）+
  last_event_date ISO 化；非法参数 400；
- consensus：limit 截断 + total 全量 + 零入柱诚实空态（evidence_only=true）；
- stock：代码三写法归一 4 位+.HK；时间线含嫌疑/续存标记；统计口径 = episode
  首日 ∧ ¬嫌疑；南向持仓直读 hsgt_south；无事件股仍 200；
- evidence：原样透传（协议/标定/排除/对账/警告）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.api.market_analysis_hk import router as hkr
from backend.services.api.user_app.middleware.auth import get_current_user
from backend.shared.hk_smart_money import datasets as ds
from backend.shared.hk_smart_money import events as ev

USER = {"tenant_id": "default", "user_id": "10000001", "sub": "10000001"}
BASE = "/api/v1/market-analysis-hk/smart-money"
D1, D2, D3 = "20261013", "20261014", "20261015"


class _Env:
    def __init__(self, tmp_path: Path):
        self.product = tmp_path / "product" / "hk_smart_money"
        self.quanthk = tmp_path / "quanthk"
        self.product.mkdir(parents=True)
        self.quanthk.mkdir(parents=True)


def _write_json(dir_: Path, name: str, payload) -> None:
    (dir_ / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _event_row(
    dt, sym, pid, pname, cat, direction, value, *, exc10, lead, suspect=False
):
    return {
        "dt": dt,
        "symbol": sym,
        "stock_name": "測試甲" if sym == "0001.HK" else "測試乙",
        "participant_id": pid,
        "participant_name": pname,
        "category": cat,
        "kind": "broker",
        "direction": direction,
        "delta_qty": int(value / 10),
        "delta_value": float(value),
        "delta_pct": 0.1,
        "price": 10.0,
        "day_amount": 100_000_000.0,
        "transfer_suspect": suspect,
        "episode_lead": lead,
        "fwd_exc_5": exc10 / 2,
        "fwd_exc_10": exc10,
        "fwd_exc_20": exc10 * 2,
    }


@pytest.fixture()
def env(tmp_path, monkeypatch) -> _Env:
    e = _Env(tmp_path)
    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path / "product"))
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(e.quanthk))
    monkeypatch.setattr(ds, "latest_south_date", lambda: D3)
    return e


@pytest.fixture()
def client(env) -> TestClient:
    app = FastAPI()
    app.include_router(hkr.router)
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    return TestClient(app)


@pytest.fixture()
def raw_client(env) -> TestClient:
    app = FastAPI()
    app.include_router(hkr.router)
    return TestClient(app)


@pytest.fixture()
def seeded(env) -> _Env:
    """产物 + 事件仓 + 南向分区的合成全量。"""
    p = env.product
    _write_json(
        p,
        "latest",
        {
            "as_of": "2026-10-15",
            "generated_at": "2026-10-16T00:00:00+00:00",
            "version": 1,
            "evidence_only": False,
            "admitted": ["hk::net5_z"],
            "directions": {"hk::net5_z": -1},
            "partial": False,
            "freshness": {
                "ccass_date": "2026-10-15",
                "south_date": "2026-10-15",
                "kline_date": "2026-10-15",
                "ccass_lag_days": 0,
                "partial": False,
            },
            "categories": [
                {
                    "category": "hk",
                    "label": "港资",
                    "holding_yi": 100.0,
                    "holding_date": "2026-10-15",
                    "n_symbols": 5,
                    "net5_yi": 1.0,
                    "net5_days": 5,
                    "net20_yi": 2.0,
                    "net20_days": 20,
                },
                {
                    "category": "southbound",
                    "label": "内资·港股通",
                    "holding_yi": 5000.0,
                    "holding_date": "2026-10-15",
                    "n_symbols": 100,
                    "net5_yi": -3.0,
                    "net5_days": 5,
                    "net20_yi": 10.0,
                    "net20_days": 20,
                },
            ],
            "counts_30d": {
                "days": 30,
                "events": 100,
                "transfer_suspect": 5,
                "first_seen": 10,
                "exit_boundary": 7,
            },
            "summary": {"scores_date": None, "pool_size": None, "covered_mean": None},
        },
    )
    _write_json(
        p,
        "trends",
        {
            "as_of": "2026-10-15",
            "generated_at": "2026-10-16T00:00:00+00:00",
            "window": 60,
            "categories": {
                "hk": [{"date": "2026-10-15", "net_yi": 1.0, "n_symbols": 5}],
                "southbound": [
                    {"date": "2026-10-15", "net_yi": -3.0, "n_symbols": 100}
                ],
            },
        },
    )
    _write_json(
        p,
        "consensus",
        {
            "as_of": "2026-10-15",
            "generated_at": "2026-10-16T00:00:00+00:00",
            "adopted": ["hk::net5_z"],
            "directions": {"hk::net5_z": -1},
            "evidence_only": False,
            "items": [
                {
                    "symbol": f"000{i}.HK",
                    "name": f"票{i}",
                    "score": 100.0 - i,
                    "covered": 1,
                    "n_pillars": 2,
                    "pct": {"hk::net5_z": 90.0},
                    "val": {"hk::net5_z": 0.01},
                }
                for i in range(1, 7)
            ],
        },
    )
    _write_json(
        p,
        "evidence",
        {
            "validated_at": "2026-10-16T00:00:00+00:00",
            "version": 1,
            "window": {
                "eval_start": "2024-12-31",
                "eval_end": "2026-10-15",
                "warmup_days": 25,
                "event_days": 3,
                "event_rows": 6,
            },
            "method": {"gate": "双过闸"},
            "admit_horizon": 10,
            "signals": [
                {
                    "name": "hk::net5_z",
                    "category": "hk",
                    "category_label": "港资",
                    "variant": "net5_z",
                    "variant_label": "净流入强度·5日z20",
                    "coverage": {"avg_names_per_day": 100.0, "days": 400},
                    "direction": -1,
                    "admitted": True,
                    "decision": "过闸",
                    "results": {
                        "5": {"rank_ic": -0.01, "tail_ok": False},
                        "10": {"rank_ic": -0.012, "tail_ok": True, "n_days": 400},
                        "20": {"rank_ic": -0.008, "tail_ok": False},
                    },
                    "by_year": {"10": {"2026": {"rank_ic": -0.012}}},
                },
                {
                    "name": "hk::net5_tt",
                    "category": "hk",
                    "category_label": "港资",
                    "variant": "net5_tt",
                    "variant_label": "净流入占比·5日",
                    "direction": 1,
                    "admitted": False,
                    "decision": "未过闸",
                    "results": {"10": {"rank_ic": 0.005, "tail_ok": False}},
                },
                {
                    "name": "southbound::chg5",
                    "category": "southbound",
                    "category_label": "内资·港股通",
                    "variant": "chg5",
                    "variant_label": "持股变化·5日",
                    "direction": 1,
                    "admitted": False,
                    "decision": "未过闸",
                    "results": {"10": {"rank_ic": None, "tail_ok": False}},
                },
            ],
            "correlation": {"pairs": [], "collapsed": []},
            "calibration": {
                "sample_days": 30,
                "sample_start": "2026-09-03",
                "sample_end": "2026-10-15",
                "grid": [{"min_value_hkd": 1e6, "min_pct": 0.05, "in_target": False}],
                "selected": {"min_value_hkd": 1e6, "min_pct": 0.05, "in_target": False},
            },
            "exclusions": {"days": 3, "events": 6, "transfer_suspect": 1},
            "reconciliation": {
                "as_of": "2026-10-15",
                "a_seat_qty_sum": 100.0,
                "south_qty_sum": 98.0,
                "ratio": 0.98,
            },
            "composite": None,
            "admitted": ["hk::net5_z"],
            "evidence_only": False,
            "warnings": ["样例警告"],
        },
    )

    # 席位榜：19 列与真实 schema 同构（NaN/None → null 契约在内）
    seats = pd.DataFrame(
        [
            {
                "participant_id": "C1",
                "participant_name": "測試甲",
                "category": "hk",
                "kind": "broker",
                "window": "60",
                "n_events": 10,
                "n_buy": 6,
                "n_sell": 4,
                "n_exc10": 10,
                "avg_exc_bp": 50.0,
                "hit": 0.7,
                "buy_hit": 0.8,
                "sell_hit": 0.5,
                "t": 2.5,
                "shrunk_exc_bp": 45.0,
                "shrunk_hit": 0.6,
                "stability": True,
                "eligible": True,
                "last_event_date": "20261015",
            },
            {
                "participant_id": "C2",
                "participant_name": "測試乙",
                "category": "us_eu",
                "kind": "custodian",
                "window": "60",
                "n_events": 3,
                "n_buy": 1,
                "n_sell": 2,
                "n_exc10": 3,
                "avg_exc_bp": -20.0,
                "hit": 0.33,
                "buy_hit": 0.0,
                "sell_hit": 0.5,
                "t": -0.5,
                "shrunk_exc_bp": -10.0,
                "shrunk_hit": 0.9,
                "stability": False,
                "eligible": False,
                "last_event_date": "20261014",
            },
            {
                "participant_id": "C3",
                "participant_name": "測試丙",
                "category": "hk",
                "kind": "broker",
                "window": "60",
                "n_events": 8,
                "n_buy": 8,
                "n_sell": 0,
                "n_exc10": 8,
                "avg_exc_bp": 30.0,
                "hit": 0.6,
                "buy_hit": 0.6,
                "sell_hit": None,
                "t": 1.2,
                "shrunk_exc_bp": 35.0,
                "shrunk_hit": 0.55,
                "stability": True,
                "eligible": True,
                "last_event_date": "20261015",
            },
            {
                "participant_id": "C4",
                "participant_name": "測試丁",
                "category": "hk",
                "kind": "broker",
                "window": "20",
                "n_events": 12,
                "n_buy": 2,
                "n_sell": 10,
                "n_exc10": 12,
                "avg_exc_bp": 5.0,
                "hit": 0.5,
                "buy_hit": 0.5,
                "sell_hit": 0.5,
                "t": 0.1,
                "shrunk_exc_bp": 4.0,
                "shrunk_hit": 0.7,
                "stability": False,
                "eligible": True,
                "last_event_date": "20261015",
            },
            {
                "participant_id": "C5",
                "participant_name": "測試戊",
                "category": "cn_broker",
                "kind": "broker",
                "window": "60",
                "n_events": 6,
                "n_buy": 6,
                "n_sell": 0,
                "n_exc10": 0,
                "avg_exc_bp": float("nan"),
                "hit": float("nan"),
                "buy_hit": float("nan"),
                "sell_hit": float("nan"),
                "t": float("nan"),
                "shrunk_exc_bp": float("nan"),
                "shrunk_hit": float("nan"),
                "stability": float("nan"),
                "eligible": True,
                "last_event_date": "20261015",
            },
            {
                "participant_id": "C6",
                "participant_name": "測試己",
                "category": "hk",
                "kind": "broker",
                "window": "all",
                "n_events": 20,
                "n_buy": 10,
                "n_sell": 10,
                "n_exc10": 20,
                "avg_exc_bp": 10.0,
                "hit": 0.55,
                "buy_hit": 0.6,
                "sell_hit": 0.5,
                "t": 1.0,
                "shrunk_exc_bp": 10.0,
                "shrunk_hit": 0.545,
                "stability": True,
                "eligible": True,
                "last_event_date": "20261015",
            },
        ]
    )
    seats.to_parquet(p / "seats.parquet", index=False)

    # 事件仓三日（0001 四类合格事件 + 嫌疑 + 续存 + 0002 干扰行）
    day1 = pd.DataFrame(
        [
            _event_row(
                D1, "0001.HK", "P1", "席位一", "hk", 1, 2_000_000, exc10=0.01, lead=1
            ),
            _event_row(
                D1, "0001.HK", "P2", "席位二", "hk", -1, -1_000_000, exc10=-0.02, lead=1
            ),
            _event_row(
                D1,
                "0001.HK",
                "P3",
                "席位三",
                "hk",
                1,
                500_000,
                exc10=0.5,
                lead=1,
                suspect=True,
            ),
            _event_row(
                D1, "0001.HK", "P1", "席位一", "hk", 1, 100_000, exc10=0.9, lead=0
            ),
        ]
    )
    day2 = pd.DataFrame(
        [
            _event_row(
                D2,
                "0001.HK",
                "P1",
                "席位一",
                "cn_broker",
                1,
                3_000_000,
                exc10=0.03,
                lead=1,
            )
        ]
    )
    day3 = pd.DataFrame(
        [
            _event_row(
                D3,
                "0001.HK",
                "P2",
                "席位二",
                "us_eu",
                -1,
                -2_000_000,
                exc10=0.02,
                lead=1,
            ),
            _event_row(
                D3, "0002.HK", "P9", "席位九", "hk", 1, 9_999_999, exc10=0.9, lead=1
            ),
        ]
    )
    empty_flows = pd.DataFrame(
        columns=[
            "dt",
            "symbol",
            "category",
            "net_value",
            "net_qty",
            "n_up",
            "n_dn",
            "n_active",
        ]
    )
    empty_hold = pd.DataFrame(
        columns=["dt", "symbol", "category", "qty_sum", "n_seats"]
    )
    for dt, evs in ((D1, day1), (D2, day2), (D3, day3)):
        ev.write_day_artifacts(
            dt,
            events=evs,
            flows=empty_flows,
            universe=pd.DataFrame({"symbol": ["0001.HK", "0002.HK"]}),
            holdings=empty_hold,
        )
    hold3 = pd.DataFrame(
        [
            {
                "dt": D3,
                "symbol": "0001.HK",
                "category": "hk",
                "qty_sum": 1000,
                "n_seats": 2,
            },
            {
                "dt": D3,
                "symbol": "0001.HK",
                "category": "cn_broker",
                "qty_sum": 500,
                "n_seats": 1,
            },
            {
                "dt": D3,
                "symbol": "0002.HK",
                "category": "hk",
                "qty_sum": 9,
                "n_seats": 1,
            },
        ]
    )
    ev.write_day_artifacts(
        D3,
        events=day3,
        flows=empty_flows,
        universe=pd.DataFrame({"symbol": ["0001.HK", "0002.HK"]}),
        holdings=hold3,
    )

    # 南向分区（quanthk 侧直读）
    sp = env.quanthk / "2_base_sector" / "hsgt_south" / f"dt={D3}"
    sp.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "0001.HK",
                "stock_name": "測試甲",
                "holding_quantity": 7777,
                "holding_percentage": 1.5,
            },
        ]
    ).to_parquet(sp / "data.parquet", index=False)
    return env


# ---------------------------------------------------------------- 鉴权与空态


@pytest.mark.parametrize(
    "path",
    [
        "/overview",
        "/types",
        "/seats",
        "/consensus",
        "/stock/0001.HK",
        "/evidence",
    ],
)
def test_无凭证401(raw_client, path):
    assert raw_client.get(f"{BASE}{path}").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/overview",
        "/types",
        "/seats",
        "/consensus",
        "/stock/0001.HK",
        "/evidence",
    ],
)
def test_无产物404统一码(client, path):
    r = client.get(f"{BASE}{path}")
    assert r.status_code == 404
    assert r.json()["detail"] == "hk_smart_money_not_built"


# ---------------------------------------------------------------- overview


def test_overview类型面板富化与趋势透传(client, seeded):
    d = client.get(f"{BASE}/overview").json()
    assert d["as_of"] == "2026-10-15"
    assert d["admitted"] == ["hk::net5_z"] and d["directions"] == {"hk::net5_z": -1}
    assert d["evidence_only"] is False
    assert d["freshness"]["partial"] is False
    by = {c["category"]: c for c in d["categories"]}
    # 入柱标记按类别；最强 |h10 IC| 变体（hk::net5_z 0.012 > net5_tt 0.005）
    assert by["hk"]["admitted"] is True
    assert by["hk"]["best"]["variant"] == "net5_z"
    assert by["hk"]["best"]["h10_ic"] == -0.012
    assert by["hk"]["best"]["admitted"] is True
    assert by["southbound"]["admitted"] is False
    # 该类全部变体 h10 rank_ic=None（不可评）→ 无「最强」可称，best=None（不伪造 0）
    assert by["southbound"]["best"] is None
    # 趋势透传但不重复 as_of/generated_at
    assert d["trends"]["window"] == 60
    assert "as_of" not in d["trends"] and "generated_at" not in d["trends"]
    assert d["counts_30d"]["transfer_suspect"] == 5
    assert "notes" in d and d["notes"]["hit"]


# ---------------------------------------------------------------- types


def test_types全网格与horizon校验(client, seeded):
    d = client.get(f"{BASE}/types").json()
    assert d["horizon"] == "10" and d["admit_horizon"] == 10
    assert d["calibration"]["sample_days"] == 30
    names = [s["name"] for s in d["signals"]]
    assert names == ["hk::net5_z", "hk::net5_tt", "southbound::chg5"]
    z = next(s for s in d["signals"] if s["name"] == "hk::net5_z")
    assert set(z["results"]) == {"5", "10", "20"}
    assert z["results"]["10"]["rank_ic"] == -0.012
    assert z["results"]["5"]["tail_ok"] is False
    tt = next(s for s in d["signals"] if s["name"] == "hk::net5_tt")
    assert tt["results"]["5"] is None  # 缺该 horizon 的 results → null 不伪造
    assert client.get(f"{BASE}/types?horizon=99").status_code == 400
    assert client.get(f"{BASE}/types?horizon=5").json()["horizon"] == "5"


# ---------------------------------------------------------------- seats


def test_seats默认窗排序与样本过滤(client, seeded):
    d = client.get(f"{BASE}/seats").json()
    assert d["window"] == "60" and d["sort"] == "shrunk_hit" and d["min_events"] == 5
    # 60 窗 + n>=5：C1(0.6) > C3(0.55) > C5(NaN 沉底)；C2(3 事件) 剔除
    assert [it["participant_id"] for it in d["items"]] == ["C1", "C3", "C5"]
    assert d["total"] == 3
    c1 = d["items"][0]
    assert c1["category_label"] == "港资"
    assert c1["last_event_date"] == "2026-10-15"
    assert c1["stability"] is True
    c5 = d["items"][2]
    assert c5["shrunk_hit"] is None and c5["avg_exc_bp"] is None  # NaN → null
    assert c5["stability"] is None
    assert d["horizon"] == 10


def test_seats窗口类别排序参数(client, seeded):
    d = client.get(f"{BASE}/seats?window=20").json()
    assert [it["participant_id"] for it in d["items"]] == ["C4"]
    d = client.get(f"{BASE}/seats?window=0").json()
    assert d["window"] == "all"
    assert [it["participant_id"] for it in d["items"]] == ["C6"]
    d = client.get(f"{BASE}/seats?category=hk").json()
    assert [it["participant_id"] for it in d["items"]] == ["C1", "C3"]
    d = client.get(f"{BASE}/seats?min_events=1").json()
    assert d["items"][0]["participant_id"] == "C2"  # 0.9 最高但 n=3
    d = client.get(f"{BASE}/seats?sort=n").json()
    assert d["items"][0]["participant_id"] == "C1"  # 10 事件最多
    d = client.get(f"{BASE}/seats?sort=avg_exc").json()
    assert d["items"][0]["participant_id"] == "C1"  # 50bp 最高
    d = client.get(f"{BASE}/seats?limit=5").json()
    assert len(d["items"]) == 3  # 少于 limit 不补行


@pytest.mark.parametrize(
    "qs",
    ["window=99", "category=bad", "sort=bad", "limit=101", "limit=2", "min_events=0"],
)
def test_seats非法参数(client, seeded, qs):
    assert client.get(f"{BASE}/seats?{qs}").status_code in (400, 422)


# ---------------------------------------------------------------- consensus


def test_consensus透传与limit截断(client, seeded):
    d = client.get(f"{BASE}/consensus").json()
    assert d["adopted"] == ["hk::net5_z"] and d["evidence_only"] is False
    assert d["total"] == 6 and len(d["items"]) == 6
    assert d["items"][0]["symbol"] == "0001.HK"
    d5 = client.get(f"{BASE}/consensus?limit=5").json()
    assert d5["total"] == 6 and len(d5["items"]) == 5


def test_consensus零入柱诚实空态(client, env):
    _write_json(
        env.product,
        "consensus",
        {
            "as_of": "2026-10-15",
            "generated_at": "2026-10-16T00:00:00+00:00",
            "adopted": [],
            "directions": {},
            "evidence_only": True,
            "items": [],
        },
    )
    d = client.get(f"{BASE}/consensus").json()
    assert d["evidence_only"] is True
    assert d["items"] == [] and d["total"] == 0 and d["adopted"] == []


# ---------------------------------------------------------------- stock


@pytest.mark.parametrize("sym", ["0001.HK", "0001", "1"])
def test_stock代码三写法同命中(client, seeded, sym):
    d = client.get(f"{BASE}/stock/{sym}").json()
    assert d["symbol"] == "0001.HK"
    assert d["name"] == "測試甲"
    assert d["window"]["days"] == 60


def test_stock时间线统计持仓与南向(client, seeded):
    d = client.get(f"{BASE}/stock/0001.HK").json()
    tl = d["timeline"]
    # 三日全量时间线（含嫌疑/续存标记行），dt desc + |金额| desc
    assert len(tl) == 6
    assert tl[0]["date"] == "2026-10-15" and tl[0]["delta_value"] == -2_000_000
    assert tl[1]["date"] == "2026-10-14"
    assert tl[2]["delta_value"] == 2_000_000  # D1 内按 |金额| 降序
    assert tl[3]["delta_value"] == -1_000_000
    suspect_row = next(r for r in tl if r["transfer_suspect"])
    assert suspect_row["delta_value"] == 500_000
    cont_row = next(
        r for r in tl if not r["first_of_episode"] and not r["transfer_suspect"]
    )
    assert cont_row["delta_value"] == 100_000
    assert tl[0]["value_yi"] == -0.02
    assert tl[0]["pct_of_amount"] == -0.02
    assert tl[0]["fwd_exc_10_bp"] == 200.0
    # 0002 行不混入
    assert all(r["participant_id"] != "P9" for r in tl)

    st = d["stats"]
    assert st["days"] == 60 and st["events"] == 4  # 嫌疑与续存出统计
    assert st["n_buy"] == 2 and st["n_sell"] == 2
    assert st["net_value_yi"] == 0.02  # 2+3−1−2 = 2M
    assert st["avg_exc_10_bp"] == 100.0  # mean(100,−200,300,200)bp
    assert st["buy_hit"] == 1.0
    assert st["sell_hit"] == 0.5  # D3 卖后 +200bp 为反例
    by = {c["category"]: c for c in st["by_category"]}
    assert by["hk"]["net_value_yi"] == 0.01  # 2M − 1M
    assert by["hk"]["avg_exc_10_bp_buy"] == 100.0
    assert by["hk"]["avg_exc_10_bp_sell"] == -200.0
    assert (
        by["cn_broker"]["n_sell"] == 0 and by["cn_broker"]["avg_exc_10_bp_sell"] is None
    )
    assert by["us_eu"]["net_value_yi"] == -0.02

    hold = {h["category"]: h for h in d["holdings"]}
    assert hold["hk"]["qty_sum"] == 1000 and hold["hk"]["n_seats"] == 2
    assert hold["cn_broker"]["qty_sum"] == 500
    assert hold["southbound"]["qty_sum"] == 7777  # hsgt_south 直读


def test_stock无事件股仍200零态(client, seeded):
    d = client.get(f"{BASE}/stock/9999.HK").json()
    assert d["symbol"] == "9999.HK" and d["name"] is None
    assert d["timeline"] == [] and d["holdings"] == []
    assert d["stats"]["events"] == 0 and d["stats"]["net_value_yi"] is None


def test_stock非法代码400与days越界422(client, seeded):
    assert client.get(f"{BASE}/stock/abc").status_code == 400
    assert client.get(f"{BASE}/stock/0001.HK?days=10").status_code == 422
    assert client.get(f"{BASE}/stock/0001.HK?days=200").status_code == 422


# ---------------------------------------------------------------- evidence


def test_evidence原样透传(client, seeded):
    d = client.get(f"{BASE}/evidence").json()
    assert d["admitted"] == ["hk::net5_z"]
    assert d["calibration"]["selected"]["in_target"] is False
    assert d["reconciliation"]["ratio"] == 0.98
    assert d["warnings"] == ["样例警告"]
    assert d["composite"] is None
    assert d["signals"][0]["by_year"]["10"]["2026"]["rank_ic"] == -0.012
