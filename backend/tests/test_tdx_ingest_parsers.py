"""通达信桥采集解析器金样测试（批次 1，2026-10-15）。

金样出处 = 批次 0 实桥探针样本（``data/tdx_bridge/_probe/`` 的 ``Value`` 载荷
原样截取，落 ``backend/tests/fixtures/``）：

* ``tdx_ingest_pricevol_sample.json``（探针 16，3 股）
* ``tdx_ingest_more_info_sample.json``（探针 32，单股 88 字段）
* ``tdx_ingest_zdt_sample.json``（探针 14，3 股全零体）
* ``tdx_ingest_stock_list_sample.json``（探针 01，market=5 前 8 条，**纯代码列表**）
* ``tdx_ingest_sector_list_sample.json``（探针 18 前 5 条，``{Code, Name}``）

GP/BK/SC/GO 家族被客户端数据包挡着（探针 07/19/21/35 全 ``success:false``），
没有真实样本——用同族方法实证 + 文档口径推演的**合成形态**钉住通用递归解析器的
契约。数据包就位后探针实测若有新形态，在此补金样即可（采集链零改动）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.live_trading.services.tdx_ingest.parsers import (
    num,
    parse_bk_value,
    parse_go_value,
    parse_gp_value,
    parse_jy_value,
    parse_more_info,
    parse_pricevol,
    parse_sc_value,
    parse_stock_list,
    parse_zdt,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

TARGET = "20261015"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ============ num：空 → NULL，不填 0 ============


def test_num_empty_marks_are_none_not_zero():
    """空/占位文本一律 None——「不填 0」是解析层铁律（0 是有语义的真值）。"""
    for raw in ("", "  ", "--", "-", "~", "null", "NaN", None, True, "abc", []):
        assert num(raw) is None, raw


def test_num_real_zero_and_numbers():
    assert num("0") == 0.0
    assert num(0) == 0.0
    assert num("1,234.56") == pytest.approx(1234.56)
    assert num(-3.5) == pytest.approx(-3.5)
    assert num(" 42 ") == pytest.approx(42.0)
    assert num(float("nan")) is None


# ============ pricevol（探针 16 金样） ============


def test_pricevol_golden_three_stocks():
    rows = parse_pricevol(_load("tdx_ingest_pricevol_sample.json"), TARGET)
    by = {r["stock_code"]: r for r in rows}
    assert set(by) == {"000001.SZ", "300750.SZ", "600036.SH"}

    r = by["600036.SH"]
    assert r["last_close"] == pytest.approx(42.67)
    assert r["now_price"] == pytest.approx(42.50)
    assert r["volume_hand"] == pytest.approx(658550)
    # zaf 派生 = (Now - LastClose) / LastClose * 100
    assert r["zaf"] == pytest.approx((42.50 - 42.67) / 42.67 * 100.0)
    assert r["symbol"] == "SH600036"  # 前缀式（PG/前端口径）
    assert r["trade_date"] == TARGET


def test_pricevol_last_close_zero_no_division():
    rows = parse_pricevol(
        {"600036.SH": {"LastClose": "0", "Now": "1.00", "Volume": "10"}}, TARGET
    )
    assert rows[0]["last_close"] == 0.0
    assert rows[0]["zaf"] is None  # 除零 → NULL，不是 0


def test_pricevol_garbage_keys_dropped():
    rows = parse_pricevol(
        {"600036.SH": {"LastClose": "1", "Now": "2", "Volume": "3"}, "note": "x"},
        TARGET,
    )
    assert [r["stock_code"] for r in rows] == ["600036.SH"]


# ============ more_info（探针 32 金样） ============


def test_more_info_golden_hot_columns_and_raw():
    row = parse_more_info(
        _load("tdx_ingest_more_info_sample.json"), "600036.SH", TARGET
    )
    assert row is not None
    assert row["zaf"] == pytest.approx(-0.16)
    assert row["f_hsl"] == pytest.approx(0.10)
    assert row["zjl"] == pytest.approx(5510.46)
    assert row["zjl_hb"] == pytest.approx(-4125.97)
    assert row["ltsz"] == pytest.approx(8787.93)
    assert row["zsz"] == pytest.approx(10743.65)
    assert row["zt_price"] == pytest.approx(46.94)
    assert row["dt_price"] == pytest.approx(38.40)
    assert row["hq_date"] == "20261015"
    # raw = 全量 88 字段原样（大小写混合键 fHSL/Zjl_HB 都在）
    assert isinstance(row["raw"], dict) and len(row["raw"]) == 88
    assert row["raw"]["HqDate"] == "20261015"
    assert row["symbol"] == "SH600036"


def test_more_info_empty_is_none():
    assert parse_more_info({}, "600036.SH", TARGET) is None
    assert parse_more_info(None, "600036.SH", TARGET) is None
    assert parse_more_info("oops", "600036.SH", TARGET) is None


# ============ zdt（探针 14 金样；当前结构性全零） ============


def test_zdt_golden_all_zero_body_parsed_faithfully():
    rows = parse_zdt(_load("tdx_ingest_zdt_sample.json"), TARGET)
    assert len(rows) == 3
    r = {x["stock_code"]: x for x in rows}["000001.SZ"]
    assert r["zdt_status_now"] == 0
    assert r["zdt_status_ori"] == 0
    assert r["fd_vol_max_zt"] == 0.0
    assert r["vol_zt"] == 0.0
    # 时间字段按字符串保留（"0" 是「无发生」的占位，不是可计算数值）
    assert r["first_time_zt"] == "0"
    assert r["last_time_dt"] == "0"
    assert r["raw"]["ZDTStatusNow"] == "0"
    assert r["trade_date"] == TARGET


# ============ stock_list 两种形态 ============


def test_stock_list_flat_shape_list_type_0():
    """list_type=0 实证形态：纯代码字符串列表（market=5 全 A 即此形态）。"""
    rows = parse_stock_list(_load("tdx_ingest_stock_list_sample.json"), "5", TARGET)
    assert [r["stock_code"] for r in rows][:3] == [
        "000001.SZ",
        "000002.SZ",
        "000006.SZ",
    ]
    assert all(r["name"] == "" for r in rows)
    assert all(r["market_key"] == "5" and r["trade_date"] == TARGET for r in rows)
    assert rows[0]["symbol"] == "SZ000001"


def test_stock_list_named_shape_list_type_1():
    """list_type=1 文档形态：``{Code, Name}``（用板块清单样本做同构金样）。"""
    rows = parse_stock_list(
        _load("tdx_ingest_sector_list_sample.json"), "sector", TARGET
    )
    assert rows[0]["stock_code"] == "880081.SH"
    assert rows[0]["name"] == "轮动趋势"


def test_stock_list_rejects_garbage():
    rows = parse_stock_list(
        ["600036.SH", "abc", 123, None, "", "BADCODE.XX"], "5", TARGET
    )
    assert [r["stock_code"] for r in rows] == ["600036.SH"]


# ============ 通用专业序列递归（合成形态；GP/BK/SC/GO 同源） ============

_GP_FIELDS = ("GP01", "GP02", "GP16", "GP24", "GP41")


def test_jy_entity_major_scalar_and_pair():
    value = {
        "600036.SH": {"GP16": "123.4", "GP02": ["1.5", "2.5"], "GP01": ""},
        "000001.SZ": {"GP16": 7},
    }
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    by = {(r["stock_code"], r["field_code"]): r for r in rows}
    # 空值（GP01=""）不落行；有值的都落
    assert set(by) == {
        ("600036.SH", "GP02"),
        ("600036.SH", "GP16"),
        ("000001.SZ", "GP16"),
    }
    assert by[("600036.SH", "GP16")]["v1"] == pytest.approx(123.4)
    assert by[("600036.SH", "GP16")]["v2"] is None
    assert by[("600036.SH", "GP16")]["value_text"] == ""
    assert by[("600036.SH", "GP02")]["v1"] == pytest.approx(1.5)
    assert by[("600036.SH", "GP02")]["v2"] == pytest.approx(2.5)
    assert all(r["trade_date"] == TARGET for r in rows)


def test_jy_entity_date_field_three_levels():
    value = {"600036.SH": {"20261015": {"GP16": 1.0}, "20261014": {"GP16": 2.0}}}
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    assert {(r["trade_date"], r["v1"]) for r in rows} == {
        ("20261015", 1.0),
        ("20261014", 2.0),
    }
    assert all(r["stock_code"] == "600036.SH" for r in rows)


def test_jy_field_major_needs_entity():
    """字段维 ``{字段: {日期: 值}}``：有实体列的表**拒收**（宁可缺，不可错列）。"""
    value = {"GP16": {"20261015": 5.5, "20261014": 4.5}}
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    assert rows == []

    # 无实体维（SC 市场级）同形态正常收
    rows = parse_jy_value(
        {"SC01": {"20261015": 123}},
        fields=("SC01",),
        default_date=TARGET,
        entity_col=None,
    )
    assert rows == [
        {
            "trade_date": "20261015",
            "field_code": "SC01",
            "v1": 123.0,
            "v2": None,
            "value_text": "",
        }
    ]


def test_jy_date_major_no_entity():
    value = {"20261015": {"SC01": 100, "SC02": None}, "20261014": {"SC01": 90}}
    rows = parse_jy_value(
        value, fields=("SC01", "SC02"), default_date=TARGET, entity_col=None
    )
    assert {(r["trade_date"], r["v1"]) for r in rows} == {
        ("20261015", 100.0),
        ("20261014", 90.0),
    }


def test_jy_date_major_with_entity():
    value = {"20261015": {"600036.SH": {"GP16": 5.5}}}
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600036.SH"
    assert rows[0]["trade_date"] == "20261015"
    assert rows[0]["v1"] == pytest.approx(5.5)


def test_jy_row_dict_list():
    value = [
        {"Code": "600036.SH", "Date": "20261015", "GP16": "5.5"},
        {"Code": "600036.SH", "Date": "20261014", "GP24": ["09:31", "245.6"]},
    ]
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    by = {(r["trade_date"], r["field_code"]): r for r in rows}
    assert by[("20261015", "GP16")]["v1"] == pytest.approx(5.5)
    # 混合值对（时间 + 数值）放不下双值列 → 原样 JSON 进 value_text（不猜）
    mixed = by[("20261014", "GP24")]
    assert mixed["v1"] is None and mixed["v2"] is None
    assert json.loads(mixed["value_text"]) == ["09:31", "245.6"]


def test_jy_text_scalar_to_value_text():
    rows = parse_jy_value(
        {"600036.SH": {"GP41": "2026-10-15", "GP16": "1.0"}},
        fields=_GP_FIELDS,
        default_date=TARGET,
        entity_col="stock_code",
    )
    by = {r["field_code"]: r for r in rows}
    # 非数字文本原样留痕（"2026-10-15" 数字化不了 → value_text）
    assert by["GP41"]["v1"] is None
    assert by["GP41"]["value_text"] == "2026-10-15"
    assert by["GP16"]["v1"] == pytest.approx(1.0)


def test_jy_unknown_meta_keys_ignored():
    value = {"600036.SH": {"GP16": 1.0, "Foo": "bar", "Note": {"x": 1}}}
    rows = parse_jy_value(
        value, fields=_GP_FIELDS, default_date=TARGET, entity_col="stock_code"
    )
    assert [r["field_code"] for r in rows] == ["GP16"]


def test_jy_default_date_fallback():
    rows = parse_jy_value(
        {"600036.SH": {"GP16": 1.0}},
        fields=_GP_FIELDS,
        default_date=TARGET,
        entity_col="stock_code",
    )
    assert rows[0]["trade_date"] == TARGET


def test_gp_bk_sc_wrappers_entity_columns():
    gp = parse_gp_value({"600036.SH": {"GP16": 1.0}}, TARGET)
    assert all("stock_code" in r for r in gp)
    bk = parse_bk_value({"880081.SH": {"BK5": 2.0}}, TARGET)
    assert bk[0]["bk_code"] == "880081.SH" and "stock_code" not in bk[0]
    sc = parse_sc_value({"20261015": {"SC01": 100}}, TARGET)
    assert "stock_code" not in sc[0] and "bk_code" not in sc[0]


# ============ go（gp_one_data 行式映射） ============


def test_go_value_num_and_text_mapping():
    rows = parse_go_value(
        {
            "600036.SH": {
                "GO3": ["1.5", "2.5"],
                "GO26": "20261231",
                "GO29": "12家",
            }
        },
        TARGET,
    )
    by = {r["field_code"]: r for r in rows}
    # 值对 → value_num 取 v1，value_text 记 "v1,v2"（可回溯）
    assert by["GO3"]["value_num"] == pytest.approx(1.5)
    assert by["GO3"]["value_text"] == "1.5,2.5"
    # 日期型数字（8 位）无损进 value_num
    assert by["GO26"]["value_num"] == pytest.approx(20261231.0)
    # 纯文本 → value_num NULL，原样 value_text
    assert by["GO29"]["value_num"] is None
    assert by["GO29"]["value_text"] == "12家"
    assert by["GO3"]["symbol"] == "SH600036"
