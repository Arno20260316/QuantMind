"""落库/归档纯逻辑测试（不碰真 PG）：upsert SQL 组装 + 行裁剪 + parquet 落盘。

**为什么会红**（真桥集成实测 2026-10-15）：``_build_upsert_sql`` 的 DO UPDATE
里写了 ``t.col`` 引用目标表，但 INSERT 没声明 ``AS t`` → 首跑即
``missing FROM-clause entry for table "t"``。任务层单测把 ``upsert_rows``
整体打了桩，整条 SQL 从没在测试里被组装过——本文件补上这个洞，别名回归守卫
对全部数据集参数化。
"""

from __future__ import annotations

import re

import pytest

from backend.services.live_trading.services.tdx_ingest.datasets import (
    DATASETS,
    get_dataset_spec,
)
from backend.services.live_trading.services.tdx_ingest.sinks import (
    _archive_path,
    _build_upsert_sql,
    _prepare_rows,
    _write_parquet,
    archive_base,
)

#: 合成列定义（builder 是纯函数，列名不必真实存在；覆盖全部类型分支）。
_SYNTHETIC_COLUMNS = (
    ("stock_code", "text", "NO"),
    ("trade_date", "text", "NO"),
    ("field_code", "text", "NO"),
    ("symbol", "text", "NO"),
    ("now_price", "double precision", "YES"),
    ("raw", "jsonb", "NO"),
    ("units", "text", "NO"),
    ("fetched_at", "timestamp with time zone", "YES"),
)


# ============ upsert SQL 组装 ============


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_upsert_sql_declares_the_t_alias_it_references(spec):
    """凡是出现 ``t.col`` 引用，INSERT 就必须有对应的 ``AS t`` 别名。"""
    sql = _build_upsert_sql(spec, _SYNTHETIC_COLUMNS)
    if re.search(r"\bt\.", sql):
        assert f"INSERT INTO {spec.table} AS t (" in sql, (
            f"{spec.key}: DO UPDATE 引用了 t. 但没有 AS t 别名"
        )
    assert sql.count("ON CONFLICT (") == 1, spec.key
    assert f"ON CONFLICT ({', '.join(spec.pk_cols)})" in sql, spec.key


def test_upsert_sql_set_clause_semantics():
    """三类覆盖语义 + fetched_at 刷新 + PK 列不得进 SET。"""
    spec = get_dataset_spec("pricevol")
    sql = _build_upsert_sql(spec, _SYNTHETIC_COLUMNS)

    assert "now_price = COALESCE(EXCLUDED.now_price, t.now_price)" in sql
    assert "raw = EXCLUDED.raw" in sql, "JSONB 恒覆盖"
    assert (
        "symbol = CASE WHEN EXCLUDED.symbol IS NULL OR EXCLUDED.symbol = '' "
        "THEN t.symbol ELSE EXCLUDED.symbol END" in sql
    ), "NOT NULL 文本空值守卫"
    assert "fetched_at = NOW()" in sql
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    for pk in spec.pk_cols:
        assert not re.search(rf"(^|[, ]){pk} =", set_clause), f"PK {pk} 进了 SET"


def test_upsert_sql_skips_id_column():
    """自增 id 不进 INSERT 列（库自填）。"""
    cols = (("id", "bigint", "NO"), *_SYNTHETIC_COLUMNS)
    sql = _build_upsert_sql(get_dataset_spec("pricevol"), cols)
    insert_cols = sql.split("(", 1)[1].split(")", 1)[0]
    assert "id" not in insert_cols.split(", ")


# ============ 行裁剪/补缺/去重 ============


def _gp_columns():
    return (
        ("stock_code", "text", "NO"),
        ("trade_date", "text", "NO"),
        ("field_code", "text", "NO"),
        ("value_num", "double precision", "YES"),
        ("value_text", "text", "NO"),
        ("units", "text", "NO"),
        ("fetched_at", "timestamp with time zone", "YES"),
    )


def test_prepare_rows_filters_fills_serializes_and_dedupes():
    spec = get_dataset_spec("gp")
    cols = _gp_columns()
    rows = [
        {  # 正常行（含表外字段 + units 自动填充）
            "stock_code": "600036.SH",
            "trade_date": "20261015",
            "field_code": "GP16",
            "value_num": 123.4,
            "junk": "丢弃",
        },
        {  # 同 PK 重复（真桥 range 调用可能重叠）→ 丢弃后者
            "stock_code": "600036.SH",
            "trade_date": "20261015",
            "field_code": "GP16",
            "value_num": 999.9,
        },
        {  # 缺失 NOT NULL 列 → 补 ''；数值缺 → None 留给 COALESCE
            "stock_code": "000001.SZ",
            "trade_date": "20261015",
            "field_code": "GP99",  # 字典外字段码 → units 补 ''
        },
    ]
    out = _prepare_rows(spec, rows, cols)
    assert len(out) == 2, "按 pk 去重"
    assert out[0]["value_num"] == 123.4 and "junk" not in out[0]
    assert out[0]["units"] == "万元", "GP16 单位从 spec.units 自动填充"
    assert out[1]["units"] == "", "字典外字段码 → 空单位"
    assert out[1]["value_text"] == "" and out[1]["value_num"] is None


def test_prepare_rows_jsonb_dict_is_dumped():
    spec = get_dataset_spec("zdt")  # jsonb_cols=("raw",)
    cols = (
        ("stock_code", "text", "NO"),
        ("trade_date", "text", "NO"),
        ("raw", "jsonb", "NO"),
    )
    out = _prepare_rows(
        spec,
        [
            {
                "stock_code": "600036.SH",
                "trade_date": "20261015",
                "raw": {"Now": "42.50"},
            }
        ],
        cols,
    )
    assert out[0]["raw"] == '{"Now": "42.50"}', "sa.text() 下 JSONB 必须手动 dumps"


# ============ 归档 ============


def test_archive_base_env_override(monkeypatch):
    monkeypatch.delenv("QM_TDX_INGEST_ARCHIVE_DIR", raising=False)
    assert str(archive_base()) == "/data/tdx_bridge"
    monkeypatch.setenv("QM_TDX_INGEST_ARCHIVE_DIR", "/tmp/x")
    assert str(archive_base()) == "/tmp/x"


def test_archive_path_partition_forms(monkeypatch, tmp_path):
    monkeypatch.setenv("QM_TDX_INGEST_ARCHIVE_DIR", str(tmp_path))
    spec = get_dataset_spec("pricevol")
    d = _archive_path(spec, "20261015", None)
    assert str(d) == str(tmp_path / "pricevol" / "date=20261015" / "data.parquet")
    r = _archive_path(spec, "20261015", "20261016")
    assert str(r) == str(tmp_path / "pricevol" / "run=20261016" / "data.parquet")


def test_write_parquet_atomic_and_homogeneous(tmp_path):
    spec = get_dataset_spec("pricevol")
    path = tmp_path / "p" / "date=19000101" / "data.parquet"
    rows = [
        {"stock_code": "600036.SH", "trade_date": "19000101", "raw": {"a": 1}},
        {"stock_code": "000001.SZ", "trade_date": "19000101", "raw": ["b"]},
    ]
    n = _write_parquet(spec, rows, path)
    assert n == 2
    import pandas as pd

    df = pd.read_parquet(path)
    assert len(df) == 2
    assert df["raw"].iloc[0] == '{"a": 1}' and df["raw"].iloc[1] == '["b"]'
    assert not path.with_suffix(".parquet.tmp").exists(), "tmp 必须已被原子替换"
