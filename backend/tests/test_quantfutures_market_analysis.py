"""QuantFutures（期货）市场分析的数据层单元 + 集成测试。

直连本地 quantfutures parquet（与 `test_quanthk_market_analysis.py`、
`test_quantus_market_analysis.py` 同款零 mock 模式）。

断言取向：**量级与健康度**（防停更 / 防口径失真 / 防 0 冒充缺失），
而不是精确数值 —— 数据每天在变，精确断言会天天红。
"""

import pytest

from backend.services.api.market_analysis_futures.feed import (
    base as fb,
)
from backend.services.api.market_analysis_futures.feed import (
    inventory,
    overview,
    positions,
    sectors,
    spread,
    term,
)
from backend.shared.futures_universe import FUTURES_META, SECTORS, symbol_meta


def _require_data() -> None:
    if not fb._avail():
        pytest.skip("QuantFutures 数据目录不可用")


# ---- 基座：目录 / 交易日 / 截面 ----


def test_kline_partition_dates_recent():
    """主力连续分区应延续到近期（防同步停更）。"""
    _require_data()
    dates = fb._trading_days(None, 5)
    assert dates, "主力连续分区为空"
    assert dates[0] >= "20261001", f"数据停更：最新分区 {dates[0]}"


def test_complete_partition_semantics():
    """「完整交易日」判定 = 国内品种数达标；最新分区本身可以是不完整分区。"""
    _require_data()
    full = fb._full_trade_days(3)
    assert full, "找不到任何完整交易日"
    latest_partition = fb._latest_trade_date()
    assert latest_partition is not None
    assert full[0] <= latest_partition
    for d in full:
        assert fb._complete_partition(d), f"完整交易日 {d} 复查失败"


def test_full_days_are_newest_first_unique():
    _require_data()
    days = fb._full_trade_days(5)
    assert days == sorted(days, reverse=True)
    assert len(set(days)) == len(days)


def test_cross_section_shape_and_columns():
    """截面含三层 symbol（.CN/.FUT/SGE），且列齐。"""
    _require_data()
    trade_date, snap = fb._cross_section()
    assert trade_date, "截面为空"
    assert not snap.empty
    assert snap["symbol"].nunique() == len(snap), "截面 symbol 未去重"
    syms = snap["symbol"].astype(str)
    assert (syms.str.endswith(".CN")).sum() >= fb.MIN_CN_SYMBOLS
    assert (syms.str.endswith(".FUT")).sum() >= 10
    for col in ("close", "pct_change", "oi_change"):
        assert col in snap.columns


def test_enrich_nominal_amounts_use_multiplier():
    """名义口径 = 价 × 量/仓 × 乘数（RB 乘数 10：turnover / (close×volume) == 10）。"""
    _require_data()
    _, snap = fb._cross_section()
    df = fb._enrich(snap)
    rb = df[df["symbol"] == "RB0.CN"]
    if rb.empty or rb["volume"].isna().all():
        pytest.skip("RB0.CN 无数据")
    r = rb.iloc[0]
    expect = (
        float(r["close"]) * float(r["volume"]) * symbol_meta("RB0.CN")["multiplier"]
    )
    assert abs(float(r["turnover"]) - expect) / expect < 1e-9
    if r["position"] == r["position"]:  # 非 NaN
        expect_oi = (
            float(r["close"])
            * float(r["position"])
            * symbol_meta("RB0.CN")["multiplier"]
        )
        assert abs(float(r["oi_amount"]) - expect_oi) / expect_oi < 1e-9


# ---- 出口口径：缺失必须是 None 而不是 0 ----


def test_num_none_for_missing():
    assert fb._num(float("nan")) is None
    assert fb._num(None) is None
    assert fb._num(1.23456) == 1.23
    assert fb._num(1234.5, 0) == 1234.0


def test_international_rows_honest_volume_and_oi():
    """国际品种：持仓一律无源 → null；无乘数 → 名义额 null。
    成交量个别源提供（照实展示），无源品种的 0 必须被诚实闸置回 null，
    绝不落 0 冒名「零成交」。"""
    _require_data()
    out = overview.get_ranking("change", None, 100)
    rows = out.get("gainers", []) + out.get("losers", [])
    intl = [r for r in rows if r["symbol"].endswith(".FUT")]
    assert intl, "涨跌榜中应包含国际品种"
    for r in intl:
        assert r["oi_wan"] is None, f"{r['symbol']} 国际品种持仓应显 —"
        assert r["turnover_yi"] is None, f"{r['symbol']} 国际品种无乘数应显 —"
        if r["volume_wan"] is not None:
            assert r["volume_wan"] > 0, (
                f"{r['symbol']} 无源成交量显示为 0（诚实闸失效）"
            )


def test_overview_international_turnover_is_null_not_zero():
    """国际/上金所组汇总：成交额/持仓额/增减仓全缺 → null
    （历史缺陷：sum() 跳过 NaN 给 0.0，且 fmt_yi(NaN) 也返回 0.0——
    期货模块改用本地的 `_yi`，缺失一律 None）。"""
    _require_data()
    ov = overview.get_overview()
    intl = ov.get("international", {})
    assert intl, "国际组分统计缺失"
    assert intl["turnover_yi"] is None, "国际品种无量仓数据，成交额应为 null 而非 0"
    assert intl["oi_amount_yi"] is None
    assert intl["oi_change_wan"] is None
    sge = ov.get("sge", {})
    if sge.get("total"):
        assert sge["turnover_yi"] is None
        assert sge["oi_amount_yi"] is None


# ---- Tab1 行情总览 ----


def test_overview_totals_consistency():
    _require_data()
    ov = overview.get_overview()
    assert ov["trade_date"]
    t, d, i, s = (
        ov["totals"],
        ov["domestic"],
        ov["international"],
        ov["sge"],
    )
    assert t["total"] == d["total"] + i["total"] + s["total"]
    assert t["up"] == d["up"] + i["up"] + s["up"]
    assert d["total"] >= fb.MIN_CN_SYMBOLS
    if t["median_pct"] is not None:
        assert -20 < t["median_pct"] < 20, "全市场中位涨幅离群（口径失真？）"


def test_ranking_change_both_sides_sorted():
    _require_data()
    out = overview.get_ranking("change", None, 10)
    gainers, losers = out["gainers"], out["losers"]
    assert gainers and losers
    gp = [r["pct_change"] for r in gainers if r["pct_change"] is not None]
    lp = [r["pct_change"] for r in losers if r["pct_change"] is not None]
    assert gp == sorted(gp, reverse=True)
    assert lp == sorted(lp)


def test_ranking_invalid_kind_raises():
    with pytest.raises(ValueError):
        overview.get_ranking("bogus", None, 10)


def test_ranking_sector_filter():
    _require_data()
    out = overview.get_ranking("turnover", "precious", 20)
    for r in out["rows"]:
        assert r["sector"] == "precious"


# ---- Tab2 板块热力 ----


def test_heatmap_covers_universe():
    _require_data()
    hm = sectors.get_heatmap()
    assert hm["trade_date"]
    items = hm["items"]
    assert len(items) >= fb.MIN_CN_SYMBOLS, "热力图品种数异常偏少"
    assert sum(1 for it in items if it["has_oi"]) >= fb.MIN_CN_SYMBOLS
    for it in items[:50]:
        assert it["sector_name"]


def test_sector_stats_domestic_only():
    _require_data()
    st = sectors.get_sector_stats()
    rows = st["sectors"]
    assert rows, "板块统计为空"
    assert sum(r["count"] for r in rows) <= len(FUTURES_META)
    meds = [r["median_pct"] for r in rows if r["median_pct"] is not None]
    assert meds == sorted(meds, reverse=True), "板块应按中位涨幅降序"


# ---- Tab3 期限结构 ----


def test_term_structure_curve_sorted_and_main():
    _require_data()
    ts = term.get_term_structure("AU0")
    assert ts["stem"] == "AU"
    assert ts["curve"], "AU 近远月曲线为空"
    yms = [c["ym"] for c in ts["curve"]]
    assert yms == sorted(yms), "曲线未按到期月升序"
    assert len(ts["curve"]) < 40, "曲线混入了已到期合约（新鲜度闸失效？）"
    assert ts["main_contract"] and ts["main_contract"].endswith(".CN")
    if ts["structure"] is not None:
        assert ts["structure"] in {"contango", "backwardation", "flat"}


def test_term_structure_unknown_stem_is_honest():
    _require_data()
    ts = term.get_term_structure("ZZZ")
    assert ts["curve"] == []
    assert ts["main_contract"] is None
    assert ts["name"] == "ZZZ"


def test_term_ranking_sorted_desc():
    _require_data()
    out = term.get_term_ranking(20)
    assert out["rows"], "期限结构榜为空"
    vals = [r["near_far_spread_pct"] for r in out["rows"]]
    assert vals == sorted(vals, reverse=True)
    for r in out["rows"]:
        assert r["near_contract"].endswith(".CN")
        assert r["far_contract"].endswith(".CN")


# ---- Tab4 持仓分析 ----


def test_cftc_structure():
    _require_data()
    out = positions.get_cftc(10)
    spec = out["speculative"]
    assert spec["report_date"], "CFTC 数据缺失（extra 任务未跑？）"
    nets = [abs(r["net"]) for r in spec["rows"] if r["net"] is not None]
    assert nets == sorted(nets, reverse=True), "CFTC 榜应按 |净仓位| 降序"
    for r in spec["rows"]:
        if r["long"] is not None and r["short"] is not None and r["net"] is not None:
            assert abs((r["long"] - r["short"]) - r["net"]) < 1e-6


def test_oi_changes_quadrants_consistent():
    _require_data()
    out = positions.get_oi_changes(1, 30)
    assert out["trade_date"], "增减仓无数据"
    assert out["base_date"]
    quad = out["quadrants"]
    assert sum(quad.values()) > 0
    rows = out["rows"]
    changes = [
        abs(r["oi_amount_change_yi"])
        for r in rows
        if r["oi_amount_change_yi"] is not None
    ]
    assert changes == sorted(changes, reverse=True)
    for r in rows:
        if r["quadrant"] is None:
            continue
        pct, d_oi = r["pct_change_n"], r["oi_change_wan"]
        if pct is None or d_oi is None:
            continue
        if abs(d_oi) < 0.01:  # 万手口径四舍五入到 0 附近，符号不可判
            continue
        if d_oi >= 0:
            assert r["quadrant"] == ("build_long" if pct >= 0 else "build_short")
        else:
            assert r["quadrant"] == ("short_covering" if pct >= 0 else "long_unwind")


def test_quadrant_mapping_matches_textbook():
    """四象限教材口径纯函数闸：涨+减仓=空头回补 / 跌+减仓=多头离场
    （曾与 docstring 相反实现，靠本测试锁死方向）。"""
    from backend.services.api.market_analysis_futures.feed.positions import _quadrant

    assert _quadrant(1.5, 100) == "build_long"  # 增仓上涨
    assert _quadrant(-1.5, 100) == "build_short"  # 增仓下跌
    assert _quadrant(1.5, -100) == "short_covering"  # 减仓上涨（空头买平）
    assert _quadrant(-1.5, -100) == "long_unwind"  # 减仓下跌（多头卖平）
    assert _quadrant(1.5, 0) == "build_long"  # 零变化按增仓侧
    assert _quadrant(float("nan"), 1) is None
    assert _quadrant(1.0, float("nan")) is None


# ---- Tab5 仓单库存 ----


def test_receipts_coverage_truthful():
    _require_data()
    out = inventory.get_receipts()
    assert out["latest_date"], "仓单数据缺失"
    assert out["variety_count"] > 0
    assert out["venues"], "最新日期无收录交易所"
    known = {"SHFE", "DCE", "CZCE", "GFEX", "INE"}
    assert set(out["venues"]) <= known
    for r in out["rows"][:10]:
        assert r["receipt_qty_wan"] is not None


def test_receipts_variety_history():
    _require_data()
    out = inventory.get_receipts("SR", 10)
    h = out["history"]
    assert h is not None
    # 回归闸：循环变量曾覆盖同名参数，令任意品种都返回末位品种（ZC）历史
    assert h["variety"] == "SR", f"请求 SR 却返回 {h['variety']} 历史"
    if h["points"]:
        dates = [p["date"] for p in h["points"]]
        assert dates == sorted(dates), "仓单历史未按日期升序"
        assert h["latest_qty_wan"] is not None
    # 无品种参数时不带任何品种历史（曾凭空带出 ZC）
    assert inventory.get_receipts(None, 10)["history"] is None


# ---- Tab6 内外盘与期现价差 ----


def test_cross_market_pairs_shape():
    _require_data()
    out = spread.get_cross_market(30)
    pairs = out["pairs"]
    assert len(pairs) == 9
    keys = {p["key"] for p in pairs}
    assert {"au_gc", "ag_si", "cu_hg", "sc_cl"} <= keys
    fx = out.get("fx")
    for p in pairs:
        assert p["formula"] and p["tax"], "每组必须自带折算/税制说明"
        assert p["domestic_symbol"] and p["intl_symbol"]
        if p["points"]:
            dates = [pt["date"] for pt in p["points"]]
            assert dates == sorted(dates)
            if fx and fx.get("rate"):
                # 折算价必须为正且与原生价同方向（数量级健康度）
                assert p["intl_close_cny"] and p["intl_close_cny"] > 0
            if p["spread_pct"] is not None:
                # 真实内外盘价差（含税制差）不会逼近 ±50%：
                # 越过此带基本是单位口径失真（如美分/磅当美元/磅 → -99%）
                assert abs(p["spread_pct"]) < 50, (
                    f"{p['key']} 价差率 {p['spread_pct']}% 超出健康带，检查单位换算"
                )


def test_copper_pair_treats_cents_per_lb():
    """COMEX 铜源侧按**美分/磅**报价（HG.FUT close≈660），折算必须 ÷100。

    2026-10-15 实机截图回归闸：按美元/磅换算时折算价放大 100 倍，
    铜价差率恒 ≈ -99.00%（假价差）。
    """
    pair = next(p for p in spread._CROSS_PAIRS if p["key"] == "cu_hg")
    assert pair["intl_unit"] == "美分/磅"
    conv = pair["to_cny"](661.35, 6.7253)  # 典型盘面：661 美分/磅、汇率 6.7
    # 正确 ≈ 110.8k 元/吨（与沪铜 111k 同量级）；漏 ÷100 则为 1,108 万
    assert 100_000 < conv < 125_000, f"美分/磅未按 ÷100 处理？conv={conv}"


def test_silver_pair_applies_import_vat():
    """沪银盘面含 13% 增值税（投资金免税不覆盖白银）：折算须 ×1.13。

    不调税时对国际银价虚增 ~13 个百分点假溢价（沪银含税 vs 伦银不含税的
    税制差被当成价差）——与有色到岸口径自相矛盾。
    """
    pair = next(p for p in spread._CROSS_PAIRS if p["key"] == "ag_si")
    raw = 55.76 * 6.7253 * 1000 / 31.1035  # ≈ 12,056 元/千克（未调税）
    conv = pair["to_cny"](55.76, 6.7253)
    assert conv > raw * 1.10, f"未加 13% 增值税？conv={conv}（未调税={raw:.0f}）"
    assert conv < raw * 1.20
    assert "增值税" in pair["tax"]


def test_precious_basis_arithmetic():
    _require_data()
    out = spread.get_precious_basis(30)
    pairs = out["pairs"]
    assert len(pairs) == 2
    for p in pairs:
        if p["basis"] is None:
            continue
        expect = round(float(p["spot_close"]) - float(p["fut_close"]), 2)
        assert abs(p["basis"] - expect) < 0.02
        if p["basis_pct"] is not None and p["state"] is not None:
            if p["basis_pct"] > 0.05:
                assert p["state"] == "premium"
            elif p["basis_pct"] < -0.05:
                assert p["state"] == "discount"


# ---- 纯函数：合约K线列映射（2026-10-15 修复的回归闸） ----


def test_norm_contract_kline_maps_hold_settle():
    """akshare futures_zh_daily_sina 返回英文列 hold/settle，必须落为
    open_interest/settlement（此前从未落盘导致期限结构无持仓量可用）。"""
    import pandas as pd

    from backend.scripts.akshare_futures_extra import _norm_contract_kline

    df = pd.DataFrame(
        {
            "date": ["2026-10-15"],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [100],
            "hold": [50],
            "settle": [1.4],
        }
    )
    out = _norm_contract_kline(df, "XX2612.CN")
    assert "open_interest" in out.columns
    assert out["open_interest"].iloc[0] == 50
    assert "settlement" in out.columns
    assert out["settlement"].iloc[0] == 1.4


def test_norm_daily_keeps_position_settlement():
    """主力连续同步同样必须保留 position/settlement（回填前的缺陷回归闸）。"""
    import pandas as pd

    from backend.scripts.akshare_futures_sync import _normalise_daily

    df = pd.DataFrame(
        {
            "日期": ["2026-10-15"],
            "开盘价": [1.0],
            "最高价": [2.0],
            "最低价": [0.5],
            "收盘价": [1.5],
            "成交量": [100],
            "持仓量": [50],
            "动态结算价": [1.4],
        }
    )
    out = _normalise_daily(df, "XX0.CN")
    assert "position" in out.columns and out["position"].iloc[0] == 50
    assert "settlement" in out.columns and out["settlement"].iloc[0] == 1.4


# ---- 品种宇宙与缓存 ----


def test_universe_meta_complete():
    assert len(FUTURES_META) == 122
    assert set(SECTORS) == {
        "precious",
        "nonferrous",
        "ferrous",
        "energy",
        "chemical",
        "agri",
        "financial",
        "green",
        "other",
    }
    for sym in ("RB0.CN", "AU0.CN", "CL.FUT", "Au99.99"):
        m = symbol_meta(sym)
        assert m["name"] and m["sector"] in SECTORS
    # 国内品种必须有乘数（名义额计算的前提）
    domestic = [s for s in FUTURES_META if s.endswith(".CN")]
    missing = [s for s in domestic if FUTURES_META[s]["multiplier"] is None]
    assert not missing, f"国内品种缺合约乘数：{missing[:5]}"


def test_cache_roundtrip():
    _require_data()
    a = overview.get_overview()
    fb.clear_cache_futures()
    b = overview.get_overview()
    assert a["trade_date"] == b["trade_date"]
    assert (a["totals"] or {}).get("total") == (b["totals"] or {}).get("total")
