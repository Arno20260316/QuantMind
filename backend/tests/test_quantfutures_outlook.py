"""QuantFutures 前瞻板与九域扩展（P1）——纯函数单测 + 直连数据健康度。

直连本地 quantfutures parquet（与 `test_quantfutures_market_analysis.py`
同款零 mock 模式）。断言取向：**数学口径正确性**（合成规则 / carry 年化 /
分位 / 到期日查表）与**量级健康度**，不锁精确数值——数据每天在变。
"""

import math

import pandas as pd
import pytest

from backend.services.api.market_analysis_futures.feed import (
    basis,
    inventory,
    members,
    outlook,
    overview,
    term,
)
from backend.services.api.market_analysis_futures.feed import (
    base as fb,
)


def _require_data() -> None:
    if not fb._avail():
        pytest.skip("QuantFutures 数据目录不可用")


# ---- 纯函数：carry 年化与月差 ----


def test_months_between():
    assert term._months_between("2610", "2710") == 12
    assert term._months_between("2612", "2701") == 1
    assert term._months_between("2701", "2612") == -1
    assert term._months_between("abc", "2610") is None


def test_annualize_carry_math():
    """年化 carry：backwardation 正、contango 负、非法输入 None（绝不落 0）。"""
    back = term._annualize_carry(110.0, 100.0, 12)
    assert back is not None and back > 0
    assert back == pytest.approx(math.log(1.1) * 365.0 / (12 * 30.44), rel=1e-9)
    contango = term._annualize_carry(100.0, 110.0, 12)
    assert contango is not None and contango < 0
    assert term._annualize_carry(100.0, 100.0, 12) == pytest.approx(0.0, abs=1e-12)

    assert term._annualize_carry(110.0, 100.0, 0) is None  # 月差 < 1 不出值
    assert term._annualize_carry(110.0, 100.0, None) is None
    assert term._annualize_carry(0.0, 100.0, 12) is None  # 非正价格


def test_est_expiry_defaults_to_15th():
    assert term._est_expiry(2610.0) == "2026-10-15"
    assert term._est_expiry(2701.0) == "2027-01-15"


# ---- 纯函数：分位与新鲜度 ----


def test_rank_pct_monotone_and_nan():
    p = fb._rank_pct(pd.Series([1.0, 2.0, 3.0, 4.0]))
    assert list(p) == [25.0, 50.0, 75.0, 100.0]
    ties = fb._rank_pct(pd.Series([1.0, 2.0, 2.0, 3.0]))
    assert list(ties) == [25.0, 62.5, 62.5, 100.0]  # 秩平均
    nan = fb._rank_pct(pd.Series([1.0, float("nan"), 3.0]))
    assert nan.iloc[0] == 50.0 and pd.isna(nan.iloc[1]) and nan.iloc[2] == 100.0


def test_freshness_tiers():
    today = pd.Timestamp.now().normalize()
    assert fb._freshness(today.strftime("%Y-%m-%d")) == "fresh"
    assert (
        fb._freshness((today - pd.Timedelta(days=10)).strftime("%Y-%m-%d")) == "stale"
    )
    assert (
        fb._freshness((today - pd.Timedelta(days=30)).strftime("%Y-%m-%d"))
        == "unavailable"
    )
    assert fb._freshness(None) == "unavailable"


# ---- 直连：前瞻板合成规则 ----


def test_outlook_composite_is_equal_weight_mean_of_available_pcts():
    """合成 = 可用因子分位等权均值（缺失剔除即权重重归一）；< 2 因子 → null。"""
    _require_data()
    o = outlook.get_outlook()
    assert o["rows"], "前瞻板行缺失"
    for r in o["rows"]:
        pcts = [
            r["factors"][k]["pct"]
            for k in outlook.COMPOSITE_FACTORS
            if r["factors"][k]["pct"] is not None
        ]
        assert r["coverage"] == len(pcts), f"{r['symbol']} coverage 计数不一致"
        if len(pcts) >= outlook.MIN_COVERAGE:
            assert r["composite"] == pytest.approx(
                round(sum(pcts) / len(pcts), 1), abs=0.051
            ), f"{r['symbol']} composite 与等权均值不符"
        else:
            assert r["composite"] is None, f"{r['symbol']} 覆盖不足却出了 composite"


def test_outlook_tiers_follow_quantiles():
    _require_data()
    o = outlook.get_outlook()
    q = o["composite_quantiles"]
    assert q["q33"] is not None and q["q67"] is not None
    assert q["q33"] < q["q67"], "q33 应小于 q67"
    for r in o["rows"]:
        c, t = r["composite"], r["tier"]
        if c is None:
            assert t is None
        elif c > q["q67"] + 0.1:
            assert t == "leading", f"{r['symbol']} {c} > q67 应为 leading 实为 {t}"
        elif c < q["q33"] - 0.1:
            assert t == "lagging", f"{r['symbol']} {c} < q33 应为 lagging 实为 {t}"


def test_outlook_rows_sorted_with_null_last():
    _require_data()
    rows = outlook.get_outlook()["rows"]
    vals = [r["composite"] for r in rows]
    notnull = [v for v in vals if v is not None]
    assert notnull == sorted(notnull, reverse=True), "composite 未降序"
    first_none = next((i for i, v in enumerate(vals) if v is None), len(vals))
    assert all(v is None for v in vals[first_none:]), "null 后混有非空 composite"


def test_outlook_schema_and_universe():
    """schema：六因子键齐备；member_net 纯事实无分位、不入合成；权重标称。"""
    _require_data()
    o = outlook.get_outlook()
    assert o["universe_size"] >= 60, f"宇宙过小：{o['universe_size']}"
    assert o["covered_size"] >= 50
    assert o["covered_size"] <= o["universe_size"]
    assert set(o["weights"]) == set(outlook.COMPOSITE_FACTORS)
    assert sum(o["weights"].values()) == pytest.approx(100.0, abs=0.5)
    for r in o["rows"]:
        f = r["factors"]
        assert set(f) == set(outlook.COMPOSITE_FACTORS) | {"member_net"}
        assert f["member_net"]["pct"] is None
        for k in outlook.COMPOSITE_FACTORS:
            pct = f[k]["pct"]
            if pct is not None:
                assert 0.0 <= pct <= 100.0, f"{r['symbol']}.{k} 分位越界 {pct}"


def test_outlook_factor_meta_complete():
    for k in outlook.COMPOSITE_FACTORS:
        meta = outlook.FACTOR_META[k]
        for field in ("name", "ref", "unit", "direction", "definition", "source"):
            assert meta.get(field), f"factor_meta[{k}].{field} 缺失"
    assert outlook.FACTOR_META["member_net"]["ref"] == "fact"


# ---- 直连：term 到期日查表（回归闸） ----


def test_term_expiry_meta_key_match():
    """回归闸：曲线合约是全码（AU2610.CN）、contract_meta 键是无后缀码
    （AU2610）——曾直接以全码查表全部 miss，真到期日被「当月 15 日」估计
    顶替（AU2611 显示 11-15 而真值 11-16）。"""
    _require_data()
    em = fb._contract_expiry()
    if em.empty:
        pytest.skip("contract_meta 不可用")
    meta = {
        str(s): str(e)
        for s, e in zip(em["symbol"], em["expiry"], strict=False)
        if isinstance(s, str) and isinstance(e, str) and e
    }
    t = term.get_term_structure("AU0")
    if not t["curve"]:
        pytest.skip("AU0 无曲线")
    matched = 0
    for row in t["curve"]:
        code = str(row["contract"]).replace(".CN", "")
        if code in meta:
            matched += 1
            assert row["expiry_estimated"] is False, f"{code} 在 meta 中却标估计"
            assert row["expiry_date"] == meta[code], f"{code} 到期日未取真值"
    assert matched >= 1, "AU 曲线无合约命中 contract_meta（键格式回归？）"


def test_term_dce_expiry_all_estimated():
    """DCE 无 contract_meta → 全走估计口径并如实标注（不得冒充真值）。"""
    _require_data()
    t = term.get_term_structure("M0")
    if not t["curve"]:
        pytest.skip("M0 无曲线")
    assert all(r["expiry_estimated"] for r in t["curve"])


def test_term_carry_fields_and_window():
    _require_data()
    t = term.get_term_structure("AU0")
    if not t["curve"]:
        pytest.skip("AU0 无曲线")
    assert t["carry_annual_pct"] is not None
    assert t["carry_months"] is None or t["carry_months"] >= 1
    assert t["near_days_to_expiry"] is not None
    # 合约窗口健康：P0 扩至未来 12 个月后 AU 应能看到 ~9 个月外的合约
    yms = [str(r["ym"]) for r in t["curve"] if r.get("ym")]
    assert yms and max(yms) >= "2706", (
        f"合约窗口疑似回退：max ym {max(yms) if yms else None}"
    )


def test_term_ranking_carry_pct():
    _require_data()
    rank = term.get_term_ranking(40)
    assert rank["rows"], "期限结构榜为空"
    spreads = [r["near_far_spread_pct"] for r in rank["rows"]]
    assert spreads == sorted(spreads, reverse=True), "未按价差率降序"
    for r in rank["rows"]:
        assert "carry_pct" in r
        if r["carry_annual_pct"] is not None:
            assert r["carry_pct"] is None or 0.0 <= r["carry_pct"] <= 100.0


# ---- 直连：basis / members / inventory_em ----


def test_basis_health_and_history():
    _require_data()
    b = basis.get_basis(limit=120)
    assert b["variety_count"] >= 40, f"基差品种过少：{b['variety_count']}"
    for r in b["rows"]:
        if r["dom_pct_250"] is not None:
            assert 0.0 <= r["dom_pct_250"] <= 100.0, f"{r['symbol']} 分位越界"
    top = b["rows"][0]["symbol"]
    h = basis.get_basis(top)["history"]
    assert h is not None and h["points"], f"{top} 单品种历史缺失"
    pt = h["points"][-1]
    assert pt["date"] and "dom_basis_rate_pct" in pt


def test_members_health_and_net_consistency():
    _require_data()
    m = members.get_members(limit=120)
    assert m["variety_count"] >= 20, f"席位品种过少：{m['variety_count']}"
    assert m["as_of"]
    for r in m["rows"]:
        if None not in (r["long_wan"], r["short_wan"], r["net_wan"]):
            assert r["net_wan"] == pytest.approx(
                r["long_wan"] - r["short_wan"], abs=0.02
            ), f"{r['symbol']} 净额与多空不符"
        if r["top5_long_share_pct"] is not None:
            assert 0.0 <= r["top5_long_share_pct"] <= 100.0


def test_members_board_for_covered_variety():
    _require_data()
    m = members.get_members("RB")
    if m["board"] is None:
        pytest.skip("RB 无席位明细")
    assert m["board"]["long"] and m["board"]["short"]
    assert m["net_trend"], "逐日净多趋势缺失"
    ranks = [x["rank"] for x in m["board"]["long"] if x["rank"] is not None]
    assert ranks == sorted(ranks)


def test_inventory_em_health_and_cycle_consistency():
    _require_data()
    inv = inventory.get_inventory_em(limit=120)
    assert inv["variety_count"] >= 50, f"东财库存品种过少：{inv['variety_count']}"
    assert inv["rows"], "库存行缺失"
    oos = 0
    for r in inv["rows"]:
        if r["out_of_sync"]:
            oos += 1
        chg, cycle = r["chg_20d_pct"], r["cycle"]
        if chg is not None and cycle is not None:
            if chg > 0.5:
                assert cycle == "build"
            elif chg < -0.5:
                assert cycle == "draw"
            else:
                assert cycle == "flat"
    assert inv["fresh_count"] == inv["variety_count"] - oos, (
        "fresh 计数与 out_of_sync 不一致"
    )


def test_inventory_em_history_shape():
    _require_data()
    inv = inventory.get_inventory_em("RB")
    h = inv["history"]
    if h is None:
        pytest.skip("RB 无东财库存历史")
    assert h["points"], "RB 历史为空"
    assert {"date", "inventory", "delta"} <= set(h["points"][-1])


# ---- 直连：overview 大盘基准与官方成交额 ----


def test_overview_benchmark_and_market_turnover():
    _require_data()
    ov = overview.get_overview()
    bm = ov.get("benchmark")
    if bm is not None:
        assert bm["close"] and bm["close"] > 0
        assert len(bm["history"]) >= 30, "基准历史过短"
    mt = ov.get("market_turnover")
    if mt is not None:
        # 国内期货日成交额万亿级；< 1000 亿必为单位口径回归（万元未换算）
        assert mt["total_yi"] is None or mt["total_yi"] > 1000, (
            f"成交额量级异常：{mt['total_yi']}"
        )
        assert set(mt["by_venue_yi"]) >= {"SHFE", "CZCE", "CFFEX", "INE"}


# ---- 直连：品种画像贯通 ----


def test_variety_profile_sections():
    """RB 六段贯通：前瞻行/期限曲线/carry 历史/基差/库存/席位。"""
    _require_data()
    v = outlook.get_variety("RB")
    assert v["symbol"] == "RB" and v["symbol_cn"] == "RB0.CN"
    assert v["outlook"] is not None, "RB 无前瞻行"
    assert v["outlook"]["coverage"] >= 2, "RB 可用因子 < 2"
    assert len(v["term"]["curve"]) >= 3, "期限曲线过短"
    assert len(v["carry_history"]) >= 100, "carry 历史过短"
    assert len(v["price_oi"]) >= 60, "价量历史过短"
    assert v["basis"]["history"] and v["basis"]["history"]["points"]
    assert v["inventory"]["history"] and v["inventory"]["history"]["points"]
    assert v["members"]["board"] is not None


def test_variety_normalization():
    """stem 归一：'rb' / 'RB0' / 'RB0.CN' 同一结果。"""
    _require_data()
    assert outlook._norm_stem("rb") == "RB"
    assert outlook._norm_stem("RB0") == "RB"
    assert outlook._norm_stem("RB0.CN") == "RB"
