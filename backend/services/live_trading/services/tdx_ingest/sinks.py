"""落库与归档：类型化 upsert + parquet 原子归档 + 运行台账/水位。

upsert 口径（列类型从 information_schema 现查，缓存于进程）
----------------------------------------------------------
* **数值列** ``COALESCE(EXCLUDED.v, 表.v)``——空值不覆盖旧值（非空覆盖语义，
  迟到发布的数据 23:50/06:30 补采时能盖住 17:30 的空行，同时不会把已有值
  冲成 NULL）；
* **文本列（NOT NULL）** 空串/NULL 不覆盖旧值（``CASE`` 守卫）；可空文本
  ``COALESCE``；
* **JSONB 列** ``raw`` 恒覆盖（最新快照为准）；
* ``units`` 长表列由 :attr:`DatasetSpec.units` 按字段码自动填充；
* ``fetched_at`` 恒 ``NOW()``（DO UPDATE 时刷新）。

``sa.text()`` 下 JSONB 需手动 ``json.dumps``（先例 tdx_l2_capture_task:530）。
executemany 的 rowcount 恒 -1（asyncpg 陷阱）→ 以 ``len(rows)`` 计。

归档：``{base}/{dataset}/date=YYYYMMDD/data.parquet``（回填用 ``run=`` 分区），
tmp 文件 + ``os.replace`` 原子落盘；写 parquet 走 ``asyncio.to_thread``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.live_trading.services.tdx_ingest.datasets import DatasetSpec

logger = logging.getLogger(__name__)

#: 单次 executemany 的行数上限。
_UPSERT_CHUNK = 500

#: INSERT 时排除的列：自增主键（库填）/ fetched_at（DEFAULT NOW()，DO UPDATE 时刷）。
_INSERT_SKIP = frozenset({"id", "fetched_at"})

#: 「数值」类列类型（COALESCE 覆盖语义）。
_NUMERIC_TYPES = frozenset(
    {"double precision", "real", "integer", "bigint", "smallint", "numeric"}
)

#: 列信息缓存：table → ((name, data_type, is_nullable), …)。
_COLUMNS_CACHE: dict[str, tuple[tuple[str, str, str], ...]] = {}

_COLUMNS_SQL = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
)


def archive_base() -> Path:
    """归档根目录（env 可覆写；默认 /data/tdx_bridge，data 卷大盘）。"""
    env = str(os.getenv("QM_TDX_INGEST_ARCHIVE_DIR") or "").strip()
    return Path(env) if env else Path("/data/tdx_bridge")


async def _columns(session, table: str) -> tuple[tuple[str, str, str], ...]:
    """列定义（进程内缓存；契约 ensure 启动期已跑，运行期 schema 不变）。"""
    cached = _COLUMNS_CACHE.get(table)
    if cached is not None:
        return cached
    from sqlalchemy import text

    rows = (await session.execute(text(_COLUMNS_SQL), {"t": table})).all()
    cols = tuple((str(r[0]), str(r[1]), str(r[2])) for r in rows)
    if cols:
        _COLUMNS_CACHE[table] = cols
    return cols


def _prepare_rows(
    spec: DatasetSpec, rows: list[dict], columns: tuple[tuple[str, str, str], ...]
) -> list[dict]:
    """行裁剪/补缺/去重/JSONB 序列化（输出可直接 executemany）。"""
    col_names = [c for c, _, _ in columns]
    col_set = set(col_names)
    units_map = spec.units or {}

    prepared: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        r = {k: v for k, v in row.items() if k in col_set}
        if "units" in col_set and units_map and not r.get("units"):
            fc = str(r.get("field_code") or "").strip().upper()
            r["units"] = units_map.get(fc, "")
        for name, dtype, nullable in columns:
            if name in _INSERT_SKIP or name in r:
                continue
            if nullable == "NO":
                r[name] = "{}" if dtype == "jsonb" else ""
            else:
                r[name] = None
        key = tuple(r.get(c) for c in spec.pk_cols)
        if key in seen:
            continue
        seen.add(key)
        for jcol in spec.jsonb_cols:
            v = r.get(jcol)
            if v is not None and not isinstance(v, str):
                r[jcol] = json.dumps(v, ensure_ascii=False)
        prepared.append(r)
    return prepared


def _build_upsert_sql(
    spec: DatasetSpec, columns: tuple[tuple[str, str, str], ...]
) -> str:
    col_names = [c for c, _, _ in columns if c not in _INSERT_SKIP]
    pk = set(spec.pk_cols)
    sets: list[str] = []
    for name, dtype, nullable in columns:
        if name in _INSERT_SKIP or name in pk:
            continue
        if dtype == "jsonb":
            sets.append(f"{name} = EXCLUDED.{name}")
        elif dtype in _NUMERIC_TYPES:
            sets.append(f"{name} = COALESCE(EXCLUDED.{name}, t.{name})")
        elif nullable == "NO":
            sets.append(
                f"{name} = CASE WHEN EXCLUDED.{name} IS NULL OR EXCLUDED.{name} = '' "
                f"THEN t.{name} ELSE EXCLUDED.{name} END"
            )
        else:
            sets.append(f"{name} = COALESCE(EXCLUDED.{name}, t.{name})")
    if any(c == "fetched_at" for c, _, _ in columns):
        sets.append("fetched_at = NOW()")
    cols_sql = ", ".join(col_names)
    ph_sql = ", ".join(f":{c}" for c in col_names)
    # ``AS t`` 是 DO UPDATE SET 里 ``t.col`` 引用的目标表别名（PG 语法）；
    # 漏了它 = 首次真跑 `missing FROM-clause entry for table "t"`（真桥集成实测）。
    return (
        f"INSERT INTO {spec.table} AS t ({cols_sql}) VALUES ({ph_sql}) "
        f"ON CONFLICT ({', '.join(spec.pk_cols)}) DO UPDATE SET {', '.join(sets)}"
    )


async def upsert_rows(spec: DatasetSpec, rows: list[dict]) -> int:
    """类型化 upsert；返回**提交的行数**（executemany rowcount=-1 陷阱）。"""
    if not rows:
        return 0
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    async with get_session() as session:
        columns = await _columns(session, spec.table)
        if not columns:
            raise RuntimeError(f"表 {spec.table} 不存在（契约 ensure 未跑?）")
        prepared = _prepare_rows(spec, rows, columns)
        if not prepared:
            return 0
        sql = _build_upsert_sql(spec, columns)
        for i in range(0, len(prepared), _UPSERT_CHUNK):
            await session.execute(text(sql), prepared[i : i + _UPSERT_CHUNK])
    return len(prepared)


# ============ parquet 归档 ============


def _archive_path(spec: DatasetSpec, target_date: str, run_key: str | None) -> Path:
    partition = f"run={run_key}" if run_key else f"date={target_date}"
    return archive_base() / spec.key / partition / "data.parquet"


def _write_parquet(spec: DatasetSpec, rows: list[dict], path: Path) -> int:
    """pandas → parquet（tmp + os.replace 原子落盘）；返回归档行数。"""
    import pandas as pd

    # dict/list 值（raw JSONB 等）→ JSON 字符串（parquet 列类型必须同质）
    norm: list[dict] = []
    for row in rows:
        item = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                item[k] = json.dumps(v, ensure_ascii=False)
            else:
                item[k] = v
        norm.append(item)
    df = pd.DataFrame(norm)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return int(len(df))


async def write_archive(
    spec: DatasetSpec, rows: list[dict], target_date: str, *, run_key: str | None = None
) -> tuple[str, int]:
    """原子写 parquet；空行集不产文件。返回 ``(路径, 行数)``（失败上抛）。"""
    if not rows:
        return "", 0
    path = _archive_path(spec, target_date, run_key)
    written = await asyncio.to_thread(_write_parquet, spec, rows, path)
    return str(path), written


# ============ 运行台账 / 水位 ============


async def record_run(
    *,
    dataset: str,
    slot: str,
    target_date: str,
    status: str,
    rows_written: int = 0,
    rows_archived: int = 0,
    calls: int = 0,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    """tdx_ingest_runs 台账一行（best-effort，失败只告警）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        async with get_session() as session:
            await session.execute(
                text(
                    "INSERT INTO tdx_ingest_runs (dataset, slot, target_date, status, "
                    "rows_written, rows_archived, calls, duration_ms, error, started_at, "
                    "finished_at) VALUES (:ds, :slot, :td, :st, :rw, :ra, :c, :ms, "
                    ":err, :started, :finished)"
                ),
                {
                    "ds": dataset,
                    "slot": slot,
                    "td": target_date,
                    "st": status,
                    "rw": int(rows_written),
                    "ra": int(rows_archived),
                    "c": int(calls),
                    "ms": int(duration_ms),
                    "err": (error or "")[:2000],
                    "started": datetime.now(timezone.utc),
                    "finished": datetime.now(timezone.utc),
                },
            )
    except Exception as exc:  # noqa: BLE001 - 台账失败不阻断采集
        logger.warning("[TdxIngest] 运行台账写入失败: %s", exc)


async def update_cursor(
    dataset: str,
    window_key: str,
    *,
    status: str,
    last_target_date: str = "",
    rows_delta: int = 0,
    cursor: dict | None = None,
    error: str = "",
) -> None:
    """水位/状态 upsert（回填断点 + blocked 记忆；best-effort）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        async with get_session() as session:
            await session.execute(
                text(
                    "INSERT INTO tdx_ingest_cursor (dataset, window_key, cursor, status, "
                    "last_error, last_target_date, rows_total, updated_at) VALUES "
                    "(:ds, :wk, :cur, :st, :err, :ltd, :delta, NOW()) "
                    "ON CONFLICT (dataset, window_key) DO UPDATE SET "
                    "cursor = EXCLUDED.cursor, status = EXCLUDED.status, "
                    "last_error = EXCLUDED.last_error, "
                    "last_target_date = EXCLUDED.last_target_date, "
                    "rows_total = tdx_ingest_cursor.rows_total + EXCLUDED.rows_total, "
                    "updated_at = NOW()"
                ),
                {
                    "ds": dataset,
                    "wk": window_key,
                    "cur": json.dumps(cursor or {}, ensure_ascii=False),
                    "st": status,
                    "err": (error or "")[:2000],
                    "ltd": last_target_date,
                    "delta": int(rows_delta),
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TdxIngest] 水位写入失败: %s", exc)


async def read_cursor(dataset: str, window_key: str) -> dict | None:
    """读水位（回填断点续跑用；读失败 → None，调用方从头上限重跑）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        async with get_session(read_only=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT cursor, status, last_target_date, rows_total "
                        "FROM tdx_ingest_cursor WHERE dataset = :ds AND window_key = :wk"
                    ),
                    {"ds": dataset, "wk": window_key},
                )
            ).first()
        if row is None:
            return None
        cur = row[0]
        if isinstance(cur, str):
            cur = json.loads(cur or "{}")
        return {
            "cursor": cur or {},
            "status": str(row[1] or ""),
            "last_target_date": str(row[2] or ""),
            "rows_total": int(row[3] or 0),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TdxIngest] 水位读取失败: %s", exc)
        return None


# ============ 状态面（路由用） ============


async def table_stats(table: str) -> dict[str, Any]:
    """单表行数 + 最新业务日（状态面板；读失败 → 空 dict）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        date_col = {
            "tdx_scjy_long": "trade_date",
            "tdx_stock_list_snapshot": "trade_date",
            "tdx_trading_dates": "trade_date",
        }.get(table, "trade_date")
        async with get_session(read_only=True) as session:
            total = (
                await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar() or 0
            latest = (
                await session.execute(text(f"SELECT MAX({date_col}) FROM {table}"))
            ).scalar()
        return {"rows": int(total), "latest_date": str(latest or "")}
    except Exception as exc:  # noqa: BLE001 - 表未建/读失败不炸面板
        logger.debug("[TdxIngest] 表统计失败 %s: %s", table, exc)
        return {"rows": 0, "latest_date": ""}


async def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """最近运行台账（状态面板；读失败 → 空表，不炸面板）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        async with get_session(read_only=True) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT dataset, slot, target_date, status, rows_written, "
                        "rows_archived, calls, duration_ms, error, finished_at "
                        "FROM tdx_ingest_runs ORDER BY id DESC LIMIT :n"
                    ),
                    {"n": int(limit)},
                )
            ).all()
        return [
            {
                "dataset": str(r[0] or ""),
                "slot": str(r[1] or ""),
                "target_date": str(r[2] or ""),
                "status": str(r[3] or ""),
                "rows_written": int(r[4] or 0),
                "rows_archived": int(r[5] or 0),
                "calls": int(r[6] or 0),
                "duration_ms": int(r[7] or 0),
                "error": str(r[8] or ""),
                "finished_at": r[9].isoformat() if r[9] else "",
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001 - 台账表未建/读失败不炸面板
        logger.debug("[TdxIngest] 运行台账读取失败: %s", exc)
        return []


__all__ = [
    "archive_base",
    "read_cursor",
    "recent_runs",
    "record_run",
    "table_stats",
    "update_cursor",
    "upsert_rows",
    "write_archive",
]
