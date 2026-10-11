"""资金流全景 v2 契约测试：panorama / sector / stock 三端点 + 证据产物 IO。

分两类：
- 纯逻辑单测（合成数据，随处可跑）：占位剔除、方向筛选、链接符号、徽章选取、
  原子写与 schema 校验；
- 契约集成测（容器内真实 QuantDB）：4 层结构、topK、value≥0、窗口求和与独立重算
  一致、证据产物 新鲜/陈旧/缺失/体系不适配 四态。
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services.api.market_analysis import flow_panorama, quantdb_feed
from backend.shared import flow_evidence


@pytest.fixture(autouse=True)
def _clear_feed_cache():
    """每个用例起止都清缓存：证据用例 monkeypatch 证据目录，防止结果串味。"""
    quantdb_feed.clear_cache()
    yield
    quantdb_feed.clear_cache()


# ---------------------------------------------------------------------------
# 纯逻辑单测
# ---------------------------------------------------------------------------


def test_placeholder_symbols_by_dt_only_flags_sharing_day():
    """占位常量只在共享发生的当天被剔除，跨日巧合不误伤。"""
    rows = []
    for i in range(30):
        rows.append(
            {"symbol": f"{i:06d}.SZ", "dt": 20260101, "flow_net_amount": 12345.6789}
        )
    rows.append({"symbol": "999999.SZ", "dt": 20260102, "flow_net_amount": 98765.4321})
    hist = pd.DataFrame(rows)
    bad = quantdb_feed.placeholder_symbols_by_dt(hist)
    assert set(bad.keys()) == {"20260101"}
    assert len(bad["20260101"]) == 30
    assert "999999.SZ" not in bad["20260101"]


def test_select_sectors_split_6_4_and_filters():
    agg = pd.DataFrame(
        {"main": [10.0, 8.0, 6.0, 4.0, 2.0, 1.0, -3.0, -7.0, -9.0]},
        index=[f"涨{i}" for i in range(6)] + [f"跌{i}" for i in range(3)],
    )
    # all = 流入 6 名额 + 流出 4 名额；负板块只 3 个 → 共 9 个，流出按 main 升序（最负在前）
    all_sel = flow_panorama._select_sectors(agg, "all", 10)
    assert all_sel == [f"涨{i}" for i in range(6)] + ["跌2", "跌1", "跌0"]

    inflow = flow_panorama._select_sectors(agg, "inflow", 3)
    assert inflow == ["涨0", "涨1", "涨2"]
    outflow = flow_panorama._select_sectors(agg, "outflow", 2)
    assert outflow == ["跌2", "跌1"]  # 最负优先


def test_mk_link_non_negative_value_and_sign():
    up = flow_panorama._mk_link("a", "b", 123.45)
    assert (
        up["value"] == pytest.approx(123.45)
        and up["sign"] == 1
        and up["net"] == pytest.approx(123.45)
    )
    down = flow_panorama._mk_link("a", "b", -99.0)
    assert (
        down["value"] == pytest.approx(99.0)
        and down["sign"] == -1
        and down["net"] == pytest.approx(-99.0)
    )
    assert flow_panorama._mk_link("a", "b", 0.0) is None  # 零头边不画


def _evidence_doc(min_n=500):
    stats = {
        "n": 6235,
        "excess_bp": 28.4,
        "excess_daily_bp": 3.5,
        "hit": 0.561,
        "base_hit": 0.54,
        "yearly": {"2024": {"n": 100, "excess_bp": -9.0}},
    }
    exec_stats = dict(stats, n=6120, excess_bp=22.1)
    return {
        "schema": flow_evidence.EVIDENCE_SCHEMA,
        "window": ["20240806", "20261009"],
        "min_cond_n": min_n,
        "conditions": {
            "streak_5": {
                "level": 5,
                "gate": {"pass": True},
                "fwd": {"3": dict(stats, n=2119)},
                "exec": {"3": exec_stats},
            },
            "streak_3": {
                "level": 3,
                "gate": {"pass": True},
                "fwd": {"3": stats},
                "exec": {"3": exec_stats},
            },
            "streak_2": {"level": 2, "gate": {"pass": False}, "fwd": {"3": stats}},
        },
    }


def test_pick_badge_levels_gating_and_min_n():
    doc = _evidence_doc()
    # streak=4 → 取最高满足且过闸的档位（5 不满足 → 3）
    badge = flow_evidence.pick_badge(4, doc)
    assert badge is not None and badge["level"] == 3
    assert badge["fwd"]["n"] == 6235 and badge["exec"]["n"] == 6120
    assert badge["window"] == ["20240806", "20261009"]
    # streak=2 → 仅剩的 streak_2 未过闸 → None
    assert flow_evidence.pick_badge(2, doc) is None
    # streak=6 → 档位 5
    assert flow_evidence.pick_badge(6, doc)["level"] == 5
    # 样本不足 → None
    assert flow_evidence.pick_badge(4, _evidence_doc(min_n=100000)) is None
    # 非 int / 缺失 → None
    assert flow_evidence.pick_badge(None, doc) is None
    assert flow_evidence.pick_badge(4, None) is None


def test_pick_badge_requires_gate_and_stats_no_fabrication():
    doc = _evidence_doc()
    doc["conditions"]["streak_3"]["gate"]["pass"] = False
    badge = flow_evidence.pick_badge(3, doc)
    assert badge is None or badge["level"] != 3  # 未过闸的档位不得上屏


def test_write_json_atomic_and_schema_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_FLOW_EVIDENCE_DIR", str(tmp_path))
    flow_evidence.write_json_atomic(
        tmp_path / "latest.json",
        {
            "schema": flow_evidence.LATEST_SCHEMA,
            "trade_date": "2026-10-09",
            "sectors": {"半导体": {"streak": 4}},
        },
    )
    doc = flow_evidence.load_latest()
    assert (
        doc is not None
        and flow_evidence.norm_trade_date(doc["trade_date"]) == "20261009"
    )
    assert flow_evidence.streak_map(doc)["半导体"]["streak"] == 4
    # schema 不符 → None
    flow_evidence.write_json_atomic(tmp_path / "evidence.json", {"schema": "bogus"})
    assert flow_evidence.load_evidence() is None
    assert flow_evidence.resolve_evidence_dir() == tmp_path


# ---------------------------------------------------------------------------
# 契约集成测（真实 QuantDB，容器内运行）
# ---------------------------------------------------------------------------


def test_panorama_structure_four_layers_and_contract():
    pan = flow_panorama.get_panorama(period="1d", stocks_per_sector=3)
    assert pan["available"] is True, pan.get("meta")
    assert pan["trade_date"] and pan["trade_date_raw"]

    nodes = {n["name"]: n for n in pan["nodes"]}
    kinds = {n["kind"] for n in pan["nodes"]}
    assert {"entity", "size", "sector", "stock"} <= kinds
    layer_of = {"entity": 1, "size": 2, "sector": 3, "stock": 4}
    for n in pan["nodes"]:
        assert n["layer"] == layer_of[n["kind"]], n

    assert flow_panorama.MAIN_NODE in nodes and flow_panorama.RETAIL_NODE in nodes

    # 链接契约：端点必须存在、value=|net|≥0、sign 与 net 同号
    for link in pan["links"]:
        assert link["source"] in nodes and link["target"] in nodes, link
        assert link["value"] >= 0
        assert link["value"] == pytest.approx(abs(link["net"]))
        if link["net"] > 0:
            assert link["sign"] == 1
        elif link["net"] < 0:
            assert link["sign"] == -1

    # 每板块个股数 ≤ stocks_per_sector
    per_sector: dict[str, int] = {}
    for n in pan["nodes"]:
        if n["kind"] == "stock":
            per_sector[n["sector"]] = per_sector.get(n["sector"], 0) + 1
    assert per_sector, "桑基应包含个股层"
    assert all(v <= 3 for v in per_sector.values())

    # 个股节点带板块内 rank（C 级），不带资金流预测徽章
    stock_nodes = [n for n in pan["nodes"] if n["kind"] == "stock"]
    for n in stock_nodes:
        assert 1 <= n["inner_rank"] <= n["inner_total"]
        assert n["key"].startswith(("SH", "SZ", "BJ"))
        assert "evidence" not in n

    # 板块 master 榜排序 = 延续状态驱动：命中(≥2)在前，其后按净额降序
    rows = pan["sectors"]
    assert len(rows) >= 20
    keys = [
        (0 if (r["streak"] or 0) >= 2 else 1, -(r["streak"] or 0), -r["net_main"])
        for r in rows
    ]
    assert keys == sorted(keys)


def test_panorama_direction_filters_sign():
    inflow = flow_panorama.get_panorama(period="1d", direction="inflow", top_sectors=5)
    assert inflow["available"] is True
    sector_nodes = [n for n in inflow["nodes"] if n["kind"] == "sector"]
    assert sector_nodes and all(n["sign"] == 1 for n in sector_nodes)

    outflow = flow_panorama.get_panorama(
        period="1d", direction="outflow", top_sectors=5
    )
    sector_nodes = [n for n in outflow["nodes"] if n["kind"] == "sector"]
    assert sector_nodes and all(n["sign"] == -1 for n in sector_nodes)


def test_panorama_window_sum_matches_independent_recompute():
    """5 日窗口：任选一板块，用同源取数独立重算成分主力净额之和应精确一致。"""
    pan = flow_panorama.get_panorama(period="5d")
    assert pan["available"] is True
    row = pan["sectors"][0]

    days = flow_panorama._l2_dates_upto(None, 5)
    assert days and days[0] == pan["trade_date_raw"]
    flow = flow_panorama._clean_flow(quantdb_feed._load_l2_flow(days))
    member_df = flow_panorama._member_frame(
        quantdb_feed._sector_members(), flow_panorama.SECTOR_SET_TYPE["industry2"]
    )
    syms = set(member_df[member_df["sector_name"] == row["name"]]["symbol"])
    expected = float(flow[flow["symbol"].isin(syms)]["main"].sum())
    assert row["net_main"] == pytest.approx(
        expected, abs=max(1.0, abs(expected) * 1e-9)
    )


def test_sector_detail_members_contract():
    pan = flow_panorama.get_panorama(period="1d")
    name = pan["sectors"][0]["name"]
    detail = flow_panorama.get_sector_detail(name=name, top=30)
    assert detail is not None and detail["available"] is True
    assert detail["name"] == name
    summary = detail["summary"]
    for field in (
        "net_main",
        "net_ratio",
        "super",
        "large",
        "medium",
        "small",
        "breadth",
        "pct_change",
        "members",
        "streak",
        "evidence",
    ):
        assert field in summary
    assert summary["net_main"] == pytest.approx(pan["sectors"][0]["net_main"], abs=1.0)

    rows = detail["members"]
    assert rows and len(rows) <= 30
    totals = [r["inner_total"] for r in rows]
    assert len(set(totals)) == 1 and totals[0] >= len(rows)
    ranks = [r["inner_rank"] for r in rows]
    # rank 为窗口净额全序名次：截断后仍唯一，且落在 1..total
    assert len(set(ranks)) == len(ranks)
    assert all(1 <= r <= totals[0] for r in ranks)
    abs_vals = [abs(r["net_main"]) for r in rows]
    assert abs_vals == sorted(abs_vals, reverse=True)
    for r in rows:
        assert r["symbol"].startswith(("SH", "SZ", "BJ"))
        if r["share"] is not None:
            # share = 成员净额 / 板块净额（两值皆 2 位小数舍入，容差放宽）
            assert r["share"] == pytest.approx(
                r["net_main"] / summary["net_main"], abs=2e-3
            )
    assert len(detail["trend"]) >= 1 and len(detail["trend"]) <= 20
    assert detail["trend"] == sorted(detail["trend"], key=lambda x: x["date"])


def test_stock_detail_series_and_attribution_roundtrip():
    pan = flow_panorama.get_panorama(period="1d", stocks_per_sector=5)
    stock_node = next(n for n in pan["nodes"] if n["kind"] == "stock")
    detail = flow_panorama.get_stock_detail(symbol=stock_node["key"], days=20)
    assert detail is not None and detail["available"] is True
    assert detail["symbol"] == stock_node["key"]
    assert isinstance(detail["name"], str)

    series = detail["series"]
    assert 1 <= len(series) <= 20
    assert [s["date"] for s in series] == sorted(s["date"] for s in series)
    for s in series:
        if s["main"] is not None:
            # main/retail 由五档求和派生，各自 4 位小数舍入 → 容差 2e-4（亿元）
            assert s["main"] == pytest.approx(s["super"] + s["large"], abs=2e-4)
            assert s["retail"] == pytest.approx(s["medium"] + s["small"], abs=2e-4)

    # windows 与 series 自洽：1d 窗口 = 序列最后一行（亿元 → 元 容差 1 万元）
    w1 = detail["windows"]["1d"]
    assert w1 is not None
    assert w1["net_main"] == pytest.approx(series[-1]["main"] * 1e8, abs=1e4)

    # 板块归因：能「返回板块」——含该股当前所在板块且 rank/total 合法
    attrib = detail["attribution"]
    assert attrib, "个股应至少归属一个板块"
    for a in attrib:
        assert 1 <= a["inner_rank"] <= a["inner_total"]
        assert "stock_net_main" in a
    assert any(a["type"] == "行业板块(二级)" for a in attrib)


def test_stock_detail_invalid_symbol_returns_none():
    assert flow_panorama.get_stock_detail(symbol="NOTACODE") is None
    assert flow_panorama.get_stock_detail(symbol="") is None


def test_panorama_evidence_gating_fresh_stale_missing(tmp_path, monkeypatch):
    """证据四态：缺失(not_built) / 陈旧(stale) / 新鲜(available) / 体系不适配(not_applicable)。"""
    monkeypatch.setenv("QM_FLOW_EVIDENCE_DIR", str(tmp_path))
    latest_day = quantdb_feed._latest_l2_date()
    assert latest_day
    dash = f"{latest_day[:4]}-{latest_day[4:6]}-{latest_day[6:]}"

    # 1) 缺失
    pan = flow_panorama.get_panorama(period="1d")
    assert pan["meta"]["evidence"]["reason"] == "not_built"
    assert all(r["streak"] is None and r["evidence"] is None for r in pan["sectors"])

    # 2) 体系不适配（概念体系不适用行业二级证据）
    pan_c = flow_panorama.get_panorama(period="1d", sector_set="concept")
    assert pan_c["meta"]["evidence"]["reason"] == "not_applicable"
    assert all(r["streak"] is None for r in pan_c["sectors"])

    # 3) 陈旧：产物 trade_date 落后 → 不得冒充当日
    flow_evidence.write_json_atomic(
        tmp_path / "latest.json",
        {
            "schema": flow_evidence.LATEST_SCHEMA,
            "trade_date": "2020-01-01",
            "sectors": {"半导体": {"streak": 4}},
        },
    )
    quantdb_feed.clear_cache()  # 步骤 1 已缓存同 key 结果，写产物后须重算
    assert (
        flow_panorama.get_panorama(period="1d")["meta"]["evidence"]["reason"] == "stale"
    )

    # 4) 新鲜：匹配合法 streak 落到板块行与桑基节点，徽章带 n 与分年
    target_name = pan["sectors"][0]["name"]
    flow_evidence.write_json_atomic(
        tmp_path / "latest.json",
        {
            "schema": flow_evidence.LATEST_SCHEMA,
            "trade_date": dash,
            "sectors": {target_name: {"streak": 4}},
        },
    )
    flow_evidence.write_json_atomic(tmp_path / "evidence.json", _evidence_doc())

    quantdb_feed.clear_cache()
    pan2 = flow_panorama.get_panorama(period="1d")
    assert pan2["meta"]["evidence"]["available"] is True
    row = next(r for r in pan2["sectors"] if r["name"] == target_name)
    assert row["streak"] == 4
    assert row["evidence"] is not None and row["evidence"]["level"] == 3
    assert row["evidence"]["fwd"]["n"] == 6235
    assert "yearly" in row["evidence"]["fwd"]

    detail = flow_panorama.get_sector_detail(name=target_name)
    assert detail["summary"]["streak"] == 4
    assert detail["summary"]["evidence"]["level"] == 3

    # 其他板块无 streak 数据 → 保持 null（不编造）
    others = [r for r in pan2["sectors"] if r["name"] != target_name]
    assert all(r["streak"] is None for r in others)
