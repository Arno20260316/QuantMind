"""落库契约 ↔ `db_init.sql` 双抄件不漂移（先例 `test_decision_ledger_contract.py`）。

两份 DDL（`tdx_ingest_contract._CREATE_SQL` 与 `db_init.sql` 的通达信段）是
手工誊录的镜像。**只有测试能证明它们没漂**：新装走 db_init.sql、老库自愈走
ensure_*()，任何一侧单改都会让两条安装路径建出不同的表。

镜像判据 = 语句集合双向相等（空白归一化，注释行剔除）；再加幂等
（全 IF NOT EXISTS）与长表兜底列（value_text）这两条口径红线。
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.shared.tdx_ingest_contract import (
    TABLES,
    _CREATE_SQL,
    ensure_tdx_ingest_tables,
    ensure_tdx_ingest_tables_async,
)

_BACKEND = Path(__file__).resolve().parents[1]
_DB_INIT = _BACKEND / "shared" / "db_init.sql"

#: db_init.sql 里通达信段的起始标记（段内到文件尾；由本测试守着是最后一段）。
_SECTION_MARK = "通达信桥数据采集"


def _norm(statement: str) -> str:
    """空白归一化：两份手抄件允许缩进/换行差异，不允许字符差异。"""
    return re.sub(r"\s+", " ", statement).strip()


def _statements(sql_text: str) -> list[str]:
    """SQL 文本 → 归一化语句列表（剔除 ``--`` 注释行，按 ``;`` 切）。"""
    body = "\n".join(
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    )
    return [_norm(s) for s in body.split(";") if s.strip()]


def _contract_statements() -> list[str]:
    return _statements(_CREATE_SQL)


def _db_init_section() -> str:
    text = _DB_INIT.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SECTION_MARK in line:
            return "\n".join(lines[i:])
    raise AssertionError(f"db_init.sql 中找不到通达信段标记 {_SECTION_MARK!r}")


def _db_init_statements() -> list[str]:
    return _statements(_db_init_section())


# ============ 契约自身 ============


def test_tables_count_and_prefix():
    assert len(TABLES) == 20, "数据 13 + 引用 5 + 运维 2（docstring 口径）"
    assert len(set(TABLES)) == len(TABLES), "表名重复"
    assert all(t.startswith("tdx_") for t in TABLES)


def test_create_sql_covers_tables_both_ways():
    """声明了的表必须有 DDL；建了的表必须在 TABLES 里（两个方向都查）。"""
    stmts = _contract_statements()
    ddl_tables = {
        m.group(1)
        for s in stmts
        if (m := re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s))
    }
    assert ddl_tables == set(TABLES)


def test_every_statement_is_idempotent():
    """全 IF NOT EXISTS——老库自愈会无条件重放整段 DDL，非幂等即事故。"""
    for stmt in _contract_statements():
        assert re.match(r"CREATE (TABLE|INDEX) IF NOT EXISTS ", stmt), stmt


def test_long_tables_have_value_text_fallback():
    """长表三张必须带 value_text：GP24/GP34/GP40（时间）/GP41（日期）数字化不了
    时原样落此列——删掉它 = 这些字段静默丢值。"""
    by_table = {}
    for s in _contract_statements():
        if m := re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s):
            by_table[m.group(1)] = s
    for table in ("tdx_gpjy_long", "tdx_bkjy_long", "tdx_scjy_long"):
        assert "value_text TEXT NOT NULL DEFAULT ''" in by_table[table], table


# ============ db_init.sql 镜像（双向） ============


def test_db_init_section_mirrors_contract_verbatim():
    """``_CREATE_SQL`` 的每条语句都必须逐字（除空白）出现在 db_init.sql。"""
    mirror = set(_db_init_statements())
    missing = [s for s in _contract_statements() if s not in mirror]
    assert not missing, "db_init.sql 缺语句（老库/新装将建出不同的表）:\n" + "\n".join(
        missing
    )


def test_db_init_section_has_no_extra_tdx_statements():
    """反向：db_init.sql 通达信段里不得有契约没有的 tdx 语句（单侧加表即红）。"""
    contract = set(_contract_statements())
    extra = [s for s in _db_init_statements() if "tdx_" in s and s not in contract]
    assert not extra, "db_init.sql 多出的 tdx 语句（契约未收录）:\n" + "\n".join(extra)


def test_db_init_has_every_table():
    section = _db_init_section()
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in section, table


# ============ 自愈入口 ============


def test_ensure_entrypoints_exist():
    import inspect

    assert callable(ensure_tdx_ingest_tables)
    assert inspect.iscoroutinefunction(ensure_tdx_ingest_tables_async)
