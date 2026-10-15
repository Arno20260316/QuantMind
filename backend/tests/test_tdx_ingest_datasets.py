"""数据集注册表一致性（声明式单一事实源 vs 取数实现 vs 建表契约）。

注册表是「采什么/怎么采/采到哪/何时采」的唯一出处，四条链路各有一份
消费者，任何一处对不上都是运行期炸（且往往是静默的）：

* **取数**：``FETCHERS`` 缺 key → ``fetch_dataset`` 抛 KeyError（整档 error）；
* **upsert**：``pk_cols`` 直接进 ``ON CONFLICT (...)``——与 DDL 的 PRIMARY KEY
  对不上是 SQL 报错；
* **jsonb_cols**：非 JSONB 列拿去 ``json.dumps`` 会存成字符串进错列；
* **档位**：``slots`` 没进 ``all_slots()`` 的档 = 永不执行（观察不到）；
* **单位**：``units`` 是给数值标注口径的（万元/万股/时间），缺=盲数。
"""

from __future__ import annotations

import re

import pytest

from backend.services.live_trading.services.tdx_ingest.datasets import (
    DATASETS,
    DATASETS_BY_KEY,
    SLOT_TARGET_OFFSET,
    all_slots,
    datasets_for_slot,
    get_dataset_spec,
)
from backend.services.live_trading.services.tdx_ingest.fetchers import FETCHERS
from backend.shared.tdx_ingest_contract import TABLES, _CREATE_SQL

_PK_RE = re.compile(r"PRIMARY KEY \(([^)]*)\)")
_JSONB_RE = re.compile(r"^\s{4}(\w+)\s+JSONB", re.M)
_COL_RE = re.compile(
    r"^\s{4}([a-z_][a-z0-9_]*)\s+"
    r"(?:VARCHAR|DOUBLE PRECISION|INTEGER|BIGINT|BIGSERIAL|TEXT|JSONB|TIMESTAMPTZ)",
    re.M,
)


def _ddl() -> dict[str, dict]:
    """契约 DDL → ``{table: {pk, jsonb, columns}}``。"""
    out: dict[str, dict] = {}
    for stmt in _CREATE_SQL.split(";\n"):
        m = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", stmt.strip())
        if not m:
            continue
        pk = _PK_RE.search(stmt)
        out[m.group(1)] = {
            "pk": [c.strip() for c in pk.group(1).split(",")] if pk else [],
            "jsonb": _JSONB_RE.findall(stmt),
            "columns": _COL_RE.findall(stmt),
        }
    return out


DDL = _ddl()

#: 分批调用的数据集（fetchers 按 batch_size 切片）：桥批量纪律 ≤500。
_BATCH_CHUNKED = ("gp", "bk", "zdt", "go")


# ============ 注册表 ↔ 取数实现 ============


def test_fetchers_match_registry_exactly():
    """双向：注册表每个 key 有取数实现；取数实现不得有注册表外的孤儿。"""
    assert set(FETCHERS) == {d.key for d in DATASETS}


def test_keys_unique_and_named():
    keys = [d.key for d in DATASETS]
    assert len(set(keys)) == len(keys)
    assert DATASETS_BY_KEY == {d.key: d for d in DATASETS}
    for d in DATASETS:
        assert d.name and d.method and d.desc, d.key


def test_get_dataset_spec_unknown_raises_with_available():
    with pytest.raises(KeyError) as exc:
        get_dataset_spec("nope")
    assert "nope" in str(exc.value)


def test_spec_is_frozen():
    """冻结 dataclass：运行期改档位只允许改源码，不允许改实例。"""
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        DATASETS[0].slots = ("00:00",)


# ============ 注册表 ↔ 建表契约 ============


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_table_exists_in_contract(spec):
    assert spec.table in TABLES
    assert spec.table in DDL, f"{spec.table} 无 CREATE TABLE"


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_pk_cols_are_exactly_ddl_primary_key(spec):
    """``pk_cols`` 就是 ``ON CONFLICT`` 的仲裁键——必须与 DDL PRIMARY KEY
    逐列同序；不一致的后果是 upsert 直接 SQL 报错（或被别的唯一索引截胡）。"""
    assert list(spec.pk_cols) == DDL[spec.table]["pk"], (
        f"{spec.key}: pk_cols={spec.pk_cols} vs DDL={DDL[spec.table]['pk']}"
    )


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_pk_cols_exist_as_columns(spec):
    cols = set(DDL[spec.table]["columns"])
    missing = [c for c in spec.pk_cols if c not in cols]
    assert not missing, f"{spec.key}: {missing} 不在 {spec.table} 列里"


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_jsonb_cols_are_jsonb_in_ddl(spec):
    jsonb = set(DDL[spec.table]["jsonb"])
    for col in spec.jsonb_cols:
        assert col in jsonb, f"{spec.key}: {col} 在 DDL 里不是 JSONB"


# ============ 字段与单位字典 ============


@pytest.mark.parametrize("spec", DATASETS, ids=lambda d: d.key)
def test_units_exactly_cover_fields(spec):
    """有 field_list 的数据集，单位字典必须逐字段覆盖（缺一个 = 那列是盲数）。"""
    if not spec.fields and not spec.units:
        return
    assert set(spec.units) == set(spec.fields), (
        f"{spec.key}: 单位字典与字段集不齐 "
        f"（缺 {sorted(set(spec.fields) - set(spec.units))}，"
        f"多 {sorted(set(spec.units) - set(spec.fields))}）"
    )
    assert all(v for v in spec.units.values()), f"{spec.key}: 空单位"


def test_blocked_family_specs_send_field_list():
    """数据包家族（GP/BK/SC/GO）调用带 field_list——没有字段清单的请求
    在解锁后也取不到数据，注册表里必须先备好。"""
    blocked = [d for d in DATASETS if d.blocked_family]
    assert {d.key for d in blocked} == {"gp", "bk", "sc", "go"}
    for d in blocked:
        assert d.fields, d.key


def test_suspended_flag_and_reason_biconditional():
    """挂起（enabled=False）⇔ 必有原因文本——挂起必须可解释（面板直接展示）。"""
    for d in DATASETS:
        assert (not d.enabled) == bool(d.suspended_reason), d.key
    assert {d.key for d in DATASETS if not d.enabled} == {"zdt"}


# ============ 档位表 ============


def test_slot_format():
    for d in DATASETS:
        assert d.slots, d.key
        for slot in d.slots:
            assert re.fullmatch(r"\d{2}:\d{2}", slot), f"{d.key}: {slot}"
            hh, mm = (int(x) for x in slot.split(":"))
            assert 0 <= hh <= 23 and 0 <= mm <= 59, f"{d.key}: {slot}"


def test_all_slots_is_exact_final_schedule():
    """工程文档 §3.3 档位终表：改档期必须同时改本断言（有意识的决定）。"""
    assert all_slots() == (
        "15:31",
        "15:45",
        "16:45",
        "17:20",
        "17:30",
        "23:50",
        "06:30",
    )


def test_datasets_for_slot_consistent():
    for slot in all_slots():
        specs = datasets_for_slot(slot)
        assert specs, slot
        for d in specs:
            assert slot in d.slots, (slot, d.key)
    # 全档位并集 = 全数据集（没有数据集落在档位表外——否则永不执行）
    covered = {d.key for slot in all_slots() for d in datasets_for_slot(slot)}
    assert covered == {d.key for d in DATASETS}


def test_slot_target_offset_keys_are_real_slots():
    for slot in SLOT_TARGET_OFFSET:
        assert slot in all_slots(), f"目标日偏移挂在不存在档位 {slot}"


def test_0630_is_the_only_offset_slot():
    """当前只有 06:30 早间补采用上一交易日；新增偏移必须显式改这里。"""
    assert SLOT_TARGET_OFFSET == {"06:30": -1}


# ============ 桥批量纪律与单次调用口径 ============


def test_chunked_batch_sizes_within_cap():
    for d in DATASETS:
        if d.key in _BATCH_CHUNKED:
            assert 1 <= d.batch_size <= 500, f"{d.key}: {d.batch_size}"


def test_batch_size_positive_and_est_calls_positive():
    for d in DATASETS:
        assert d.batch_size >= 1, d.key
        assert d.est_calls >= 1, d.key


def test_pricevol_is_single_whole_market_call():
    """pricevol 实测 1 次调用拿全 A（3.1s）——batch_size 是「整场」声明，
    不是分批参数；若有天它变分批，档位与预算都要重估。"""
    spec = get_dataset_spec("pricevol")
    assert spec.est_calls == 1
    assert spec.batch_size >= 5000
