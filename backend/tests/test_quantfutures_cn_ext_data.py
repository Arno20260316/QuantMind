"""QuantFutures 国内扩展数据集（二批）测试 —— 归一化单测 + 落盘健康度（零 mock）。

归一化单测零网络（构造源侧原始帧直测）；落盘断言直连
`data/quantfutures/2_base_sector/` 产物（目录缺失即 skip，与
`test_quantfutures_market_analysis.py` 同款零 mock 模式）。

断言取向：量级与健康度（防停更 / 防口径失真 / 防 0 冒充缺失），
不锁精确数值——数据每天在变。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.scripts.akshare_futures_cn_ext import (
    BASE_DIR,
    _clean_num,
    _czce_contract_sina,
    _norm_meta_czce,
    _norm_rule,
    _norm_settle_czce,
    _norm_settle_shfe,
    _norm_spot,
    _variety_venue_map,
)

# ---------------------------------------------------------------------------
# 归一化单测（零网络）
# ---------------------------------------------------------------------------


def test_clean_num_strips_separators_and_sign():
    out = _clean_num(pd.Series(["8,000.00", "±8", "", "20"]))
    assert out.iloc[0] == 8000.0
    assert out.iloc[1] == 8.0
    assert pd.isna(out.iloc[2])
    assert out.iloc[3] == 20.0


def test_czce_contract_code_to_sina_4digit_year():
    assert _czce_contract_sina("AP610") == "AP2610"
    assert _czce_contract_sina("SR701") == "SR2701"
    assert _czce_contract_sina("AP2610") == "AP2610"  # 已是 4 位年：原样
    assert _czce_contract_sina("rb2701") == "rb2701"  # SHFE 小写：原样


def test_norm_settle_czce_percent_comma_sign():
    raw = pd.DataFrame(
        [
            {
                "date": "20261014",
                "symbol": "AP610",
                "variety": "AP",
                "settle_price": "7,896.00",
                "is_single_market": "N",
                "single_market_days": "0",
                "margin_ratio": "20",
                "limit_ratio": "±8",
                "trade_fee": "5.00",
                "fee_type": "绝对值",
                "delivery_fee": "0.00",
                "close_today_fee": "10.00",
                "position_limit": "20",
                "trade_limit": "",
            },
        ]
    )
    out = _norm_settle_czce(raw, "CZCE", "20261014")
    row = out.iloc[0]
    assert row["venue"] == "CZCE"
    assert row["time"] == pd.Timestamp("2026-10-14")
    assert row["settle_price"] == pytest.approx(7896.0)
    assert row["margin_rate"] == pytest.approx(0.20)  # 百分数 → 小数
    assert row["limit_rate"] == pytest.approx(0.08)  # ±8 → 0.08
    assert row["trade_fee_abs"] == pytest.approx(5.0)
    assert row["close_today_fee_abs"] == pytest.approx(10.0)
    assert "margin_ratio" not in out.columns


def test_norm_settle_shfe_keeps_fraction_ratios():
    raw = pd.DataFrame(
        [
            {
                "date": "20261014",
                "symbol": "cu2610",
                "variety": "cu",
                "settle_price": 111610.0,
                "spec_long_margin_ratio": 0.2,
                "hedge_long_margin_ratio": 0.2,
                "spec_short_margin_ratio": 0.2,
                "hedge_short_margin_ratio": 0.2,
                "trade_fee_ratio": 0.05,
                "close_today_fee_ratio": 0.025,
                "is_close_today": 0,
            },
        ]
    )
    out = _norm_settle_shfe(raw, "SHFE", "20261014")
    row = out.iloc[0]
    assert row["margin_spec_long"] == pytest.approx(0.2)  # 源侧即小数，不再除 100
    assert row["settle_price"] == pytest.approx(111610.0)


def test_norm_rule_percent_to_fraction():
    raw = pd.DataFrame(
        [
            {
                "交易所": "上期所",
                "品种": "铜",
                "代码": "CU",
                "交易保证金比例": 16.0,
                "涨跌停板幅度": 7.0,
                "合约乘数": 5,
                "最小变动价位": 10.0,
                "限价单每笔最大下单手数": 500,
                "特殊合约参数调整": "",
                "调整备注": "",
            },
        ]
    )
    out = _norm_rule(raw, "20261014")
    row = out.iloc[0]
    assert row["exchange"] == "上期所"
    assert row["margin_rate"] == pytest.approx(0.16)
    assert row["limit_rate"] == pytest.approx(0.07)
    assert row["multiplier"] == 5
    assert row["time"] == pd.Timestamp("2026-10-14")


def test_norm_meta_czce_prefix_matches_dynamic_columns():
    # CZCE 列名带「待国家公布…」动态后缀，归一化按前缀匹配
    raw = pd.DataFrame(
        [
            {
                "产品名称": "鲜苹果期货",
                "合约代码": "AP610",
                "产品代码": "AP",
                "到期时间待国家公布2025年节假日安排后进行调整": "2026-10-21 15:00 北京时间",
                "最后交易日待国家公布2025年节假日安排后进行调整": "2026-10-15",
                "结算方式": "实物",
            },
        ]
    )
    out = _norm_meta_czce(raw, "CZCE", "20261014")
    row = out.iloc[0]
    assert row["symbol"] == "AP610"
    assert row["name"] == "鲜苹果期货"
    assert row["expire_date"] == "2026-10-21 15:00 北京时间"
    assert row["last_trade_date"] == "2026-10-15"
    assert row["venue"] == "CZCE"


def test_norm_spot_date_to_time_and_numeric():
    raw = pd.DataFrame(
        [
            {
                "date": "20261009",
                "symbol": "RB",
                "spot_price": "3,172.0",
                "near_contract": "rb2610",
                "near_contract_price": "3005.0",
                "dominant_contract": "rb2701",
                "dominant_contract_price": "3080.0",
                "near_month": "2610",
                "dominant_month": "2701",
                "near_basis": "-167.0",
                "dom_basis": "-92.0",
                "near_basis_rate": "-0.0526",
                "dom_basis_rate": "-0.0299",
            },
        ]
    )
    out = _norm_spot(raw)
    row = out.iloc[0]
    assert row["time"] == pd.Timestamp("2026-10-09")
    assert row["spot_price"] == pytest.approx(3172.0)
    assert row["dom_basis"] == pytest.approx(-92.0)
    assert row["dominant_contract"] == "rb2701"
    assert "date" not in out.columns


def test_variety_venue_map_covers_shfe_czce():
    m = _variety_venue_map()
    assert m.get("RB") == "SHFE"
    assert m.get("AP") == "CZCE"
    assert m.get("CU") == "SHFE"
    assert m.get("SR") == "CZCE"


def test_ccidx_rename_targets_are_unique():
    # 源侧「涨跌/涨跌幅」与 dailyIncreaseAndDecrease* 是孪生列，若都映射到
    # change/pct_change 会产生重名列（to_numeric 炸 arg must be a list...）
    from backend.scripts.akshare_futures_cn_ext import _CCIDX_REN

    targets = list(_CCIDX_REN.values())
    assert len(targets) == len(set(targets)), "ccidx 重名映射会产孪生列"


def test_date_str_handles_mixed_forms():
    import datetime

    from backend.scripts.akshare_futures_cn_ext import _date_str

    s = pd.Series([datetime.date(2026, 10, 16), "2026-10-21 15:00 北京时间", None, ""])
    out = _date_str(s)
    assert out.iloc[0] == "2026-10-16"
    assert out.iloc[1] == "2026-10-21"
    assert pd.isna(out.iloc[2])
    assert pd.isna(out.iloc[3])


def test_parse_hog_time_year_week_and_date_forms():
    from backend.scripts.akshare_futures_cn_ext import _parse_hog_time

    out = _parse_hog_time(pd.Series(["2012", "2018年第01周", "2026-05-28"]))
    assert out.iloc[0] == pd.Timestamp("2012-01-01")  # 年度值落年初
    assert out.iloc[1] == pd.Timestamp("2018-01-01")  # 2018-W01-1
    assert out.iloc[2] == pd.Timestamp("2026-05-28")


def test_norm_hog_frame_wide_melt_multi_metrics():
    from backend.scripts.akshare_futures_cn_ext import _norm_hog_frame

    raw = pd.DataFrame(
        {
            "周期": ["2009", "2010"],
            "能繁母猪存栏": [4910.0, 4750.0],
            "猪肉产量": [4932.85, 5138.44],
        }
    )
    out = _norm_hog_frame(raw, "supply", "生猪产能")
    assert set(out["series"]) == {"生猪产能·能繁母猪存栏", "生猪产能·猪肉产量"}
    assert len(out) == 4
    assert (out["family"] == "supply").all()


def test_norm_hog_frame_single_nonstandard_value_column():
    from backend.scripts.akshare_futures_cn_ext import _norm_hog_frame

    raw = pd.DataFrame({"date": [pd.Timestamp("2026-05-28")], "benzhou": [2.96]})
    out = _norm_hog_frame(raw, "supply", "育肥猪")
    assert out.iloc[0]["value"] == pytest.approx(2.96)
    assert out.iloc[0]["series"] == "育肥猪"


# ---------------------------------------------------------------------------
# _VenueBreaker 熔断器（回填 2026 春节事故回归：休市日空帧曾把四所全部振出）
# ---------------------------------------------------------------------------


def _breaker():
    from backend.scripts.akshare_futures_cn_ext import _VenueBreaker

    return _VenueBreaker(("SHFE", "CZCE"))


def test_breaker_holiday_empties_never_trip():
    # 连续休市（空帧）无论多少天都不能触发熔断
    b = _breaker()
    for idx in range(10):
        b.on_empty("SHFE")
        assert not b.skip("SHFE", idx)
    assert b.cooling(10) == []


def test_breaker_error_streak_trips_and_cools_then_reprobes():
    b = _breaker()
    triggers = [b.on_error("SHFE", idx) for idx in range(5)]
    assert triggers == [False, False, False, False, True]  # 第 5 次触发
    # 触发于 idx=4，冷却 20 天：idx 5..23 跳过，idx 24（=4+20）恢复可探
    assert b.skip("SHFE", 23)
    assert not b.skip("SHFE", 24)
    assert b.cooling(10) == ["SHFE"]


def test_breaker_success_resets_error_streak():
    b = _breaker()
    for idx in range(4):
        b.on_error("SHFE", idx)
    b.on_success("SHFE")  # 连败中断
    for idx in range(4):
        assert not b.on_error("SHFE", 10 + idx)
    assert b.cooling(20) == []


def test_breaker_empty_resets_error_streak():
    # 春节等连休场景：异常中夹休市日（空帧）不应累计到熔断
    b = _breaker()
    for idx in range(4):
        b.on_error("SHFE", idx)
    b.on_empty("SHFE")
    for idx in range(4):
        assert not b.on_error("SHFE", 10 + idx)
    assert b.cooling(20) == []


# ---------------------------------------------------------------------------
# 落盘健康度（零 mock；目录缺失即 skip）
# ---------------------------------------------------------------------------


def _dir_ok(p: Path) -> bool:
    return p.is_dir() and any(p.iterdir())


def _skip_unless(p: Path) -> None:
    if not _dir_ok(p):
        pytest.skip(f"QuantFutures 数据目录不可用: {p}")


def _latest_partition(ds_dir: Path) -> Path | None:
    # 只认真正带 data.parquet 的分区（防半写残留空目录被判成最新分区）
    parts = sorted(
        x for x in ds_dir.glob("dt=*") if x.is_dir() and (x / "data.parquet").is_file()
    )
    return parts[-1] if parts else None


def _load_latest(ds: str) -> pd.DataFrame:
    ds_dir = BASE_DIR / ds
    _skip_unless(ds_dir)
    part = _latest_partition(ds_dir)
    assert part is not None, f"{ds} 无分区"
    f = part / "data.parquet"
    assert f.is_file(), f"{ds} 最新分区缺 data.parquet: {part}"
    return pd.read_parquet(f)


def test_spot_basis_health():
    df = _load_latest("spot_basis")
    assert len(df) >= 20, "单日现货基差行数过低"
    for col in (
        "time",
        "symbol",
        "spot_price",
        "dominant_contract",
        "near_basis_rate",
        "dom_basis_rate",
    ):
        assert col in df.columns, f"spot_basis 缺列 {col}"
    assert (df["spot_price"] > 0).mean() >= 0.9
    # 基差率量级健康（防单位错位：分数 vs 百分数）。留 5% 余量给源侧噪声——
    # 个别品种生意社现货口径与盘面脱钩（实测 SH 烧碱 1.77），属上游数据问题。
    rate = df["dom_basis_rate"].dropna()
    assert len(rate) >= 10
    assert (rate.abs() < 0.5).mean() >= 0.95, "基差率量级异常（疑似 ×100 口径漂移）"


def test_settle_params_health():
    df = _load_latest("settle_params")
    assert len(df) >= 150, "结算参数行数过低（应含三所合约）"
    assert set(df["venue"].unique()) <= {"SHFE", "INE", "CZCE"}
    settles = df["settle_price"].dropna()
    assert (settles > 0).mean() >= 0.8, "结算价健康度过低"
    # 保证金率一律小数口径（0, 1]
    shfe = df.loc[df["venue"] == "SHFE", "margin_spec_long"].dropna()
    czce = df.loc[df["venue"] == "CZCE", "margin_rate"].dropna()
    assert len(shfe) > 0 and len(czce) > 0
    assert (shfe.gt(0) & shfe.le(1)).mean() >= 0.99
    assert (czce.gt(0) & czce.le(1)).mean() >= 0.99, (
        "CZCE 保证金率疑似未归一（百分数残留）"
    )


def test_trade_rule_health():
    df = _load_latest("trade_rule")
    assert len(df) >= 100, "交易规则行数过低"
    mult = df["multiplier"].dropna()
    assert len(mult) > 0 and (mult > 0).all()
    mr = df["margin_rate"].dropna()
    assert (mr.gt(0) & mr.le(1)).mean() >= 0.9, "保证金比例疑似未归一"


def test_exchange_daily_health():
    df = _load_latest("exchange_daily")
    assert len(df) >= 150, "官方日行情行数过低（应含四所全合约）"
    assert set(df["venue"].unique()) <= {"SHFE", "INE", "CZCE", "CFFEX"}
    assert (df["settle"].dropna() > 0).mean() >= 0.8
    to = df["turnover"].dropna()
    assert len(to) > 0 and (to >= 0).all(), "成交额不应为负"


def test_contract_meta_health():
    df = _load_latest("contract_meta")
    assert len(df) >= 150, "合约信息行数过低"
    assert set(df["venue"].unique()) <= {"SHFE", "INE", "CZCE", "CFFEX"}
    assert df["symbol"].notna().all()


def test_inventory_em_health():
    ds_dir = BASE_DIR / "inventory_em"
    _skip_unless(ds_dir)
    files = sorted(ds_dir.glob("*.parquet"))
    assert len(files) >= 50, f"库存品种文件过少: {len(files)}"
    for f in files[:3]:
        df = pd.read_parquet(f)
        assert {"time", "inventory"} <= set(df.columns)
        inv = df["inventory"].dropna()
        assert len(inv) >= 30 and (inv >= 0).all()


def test_ccidx_index_health():
    f = BASE_DIR / "ccidx_index" / "data.parquet"
    if not f.is_file():
        pytest.skip("ccidx_index 未落盘")
    df = pd.read_parquet(f)
    assert len(df) >= 900, "中证商品指数行数过低（应含 2022-10 起历史）"
    assert set(df["index_slug"].unique()) == {"commodity", "price"}
    assert (df["settle"].dropna() > 0).mean() >= 0.95


def test_fees_info_health():
    f = BASE_DIR / "fees_info" / "data.parquet"
    if not f.is_file():
        pytest.skip("fees_info 未落盘")
    df = pd.read_parquet(f)
    assert len(df) >= 500, "费率表行数过低"
    mult = df["multiplier"].dropna()
    assert (mult > 0).all()


def test_hog_chain_health():
    ds_dir = BASE_DIR / "hog_chain"
    _skip_unless(ds_dir)
    for family, min_rows in (("core", 100), ("cost", 100), ("supply", 30)):
        f = ds_dir / f"{family}.parquet"
        assert f.is_file(), f"hog_chain 缺 {family}.parquet"
        df = pd.read_parquet(f)
        assert len(df) >= min_rows, f"hog_chain {family} 行数过低"
        assert (df["family"] == family).all()
        assert df["value"].notna().mean() >= 0.9


def test_member_rank_sina_health():
    df = _load_latest("member_rank_sina")
    assert len(df) >= 50, "席位排名行数过低"
    assert set(df["side"].unique()) <= {"long", "short", "volume"}
    assert set(df["venue"].unique()) <= {"SHFE", "CZCE"}
    assert (df["size"].dropna() >= 0).all()
