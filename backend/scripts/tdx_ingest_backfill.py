#!/usr/bin/env python3
"""通达信桥专业数据历史回填（断点续跑；批次 1 交付）。

    python3 backend/scripts/tdx_ingest_backfill.py --dataset gp \\
        --from 20210101 --to 20261015 [--window-days 1] [--max-minutes 180]

存储口径（用户已定：「PG 近 5 年 + 全历史 parquet」）：

* **全历史一律先落 parquet**：``{archive}/{dataset}/run={批次}w{窗口末}/data.parquet``
  （tmp+``os.replace`` 原子；窗口化分区保证单文件内存有界、断点粒度=窗口）；
* **PG 只收近 5 年**：``trade_date >= --pg-since``（缺省 = 今日-5y，env
  ``TDX_INGEST_PG_SINCE`` 可覆写）的行 upsert；更早的行只进 parquet。

纪律：
* 断点续跑：水位 ``tdx_ingest_cursor(dataset=…, window_key='backfill')``，
  每完成一个窗口推进 ``last_target_date=窗口末``；重跑自动跳过已完成窗口，
  中断的窗口整体重拉（upsert 幂等）。
* ``--window-days`` 缺省 **1**：GP/BK/SC 家族当前被数据包阻塞（blocked），
  多日 range 返回的行是否自带逐日日期**未经实测**——默认逐日保证
  「fallback 日期 = 唯一日期」恒真；实测确认 payload 带日期后再放宽。
* 被阻塞家族（BridgeError kind=blocked）**立即退出码 2**（不做无谓重试）；
  其余桥错退出码 1（水位停在失败窗口前，重跑接续）。
* 磁盘闸：开工前归档卷余量 < ``--min-free-gb``（默认 50）直接退出码 3。
* ``--dry-run``：只列窗口与预估调用数，不碰桥、不写任何存储。

退出码：0=跑完/到点自停，1=桥侧失败，2=数据包未就绪（blocked），3=磁盘不足。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tdx-backfill")

#: 支持的家族（其余数据集无 start/end range 语义或形态未定）
SUPPORTED = ("gp", "bk", "sc")

#: dry-run 的宇宙估计（仅用于调用数预估展示）。
_ASSUMED_UNIVERSE = 5581

_GIB = 1024**3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="通达信桥专业数据历史回填")
    p.add_argument("--dataset", required=True, choices=SUPPORTED)
    p.add_argument("--from", dest="date_from", required=True, help="起点 YYYYMMDD")
    p.add_argument("--to", dest="date_to", required=True, help="终点 YYYYMMDD（含）")
    p.add_argument("--window-days", type=int, default=1, help="每窗口天数（缺省 1）")
    p.add_argument(
        "--max-minutes", type=float, default=0, help="到点自停（0=不限；夜间窗口用）"
    )
    p.add_argument("--pg-since", default="", help="PG 入库下限 YYYYMMDD（缺省今日-5y）")
    p.add_argument("--min-free-gb", type=float, default=50, help="归档卷余量闸")
    p.add_argument("--dry-run", action="store_true", help="只列窗口，不采集")
    return p.parse_args(argv)


def _validate_date(s: str, label: str) -> date:
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        raise SystemExit(f"{label} 非法（要 YYYYMMDD）: {s!r}")
    return datetime.strptime(s, "%Y%m%d").date()


def _default_pg_since() -> str:
    import os

    env = os.getenv("TDX_INGEST_PG_SINCE", "").strip()
    if env:
        return env
    today = date.today()
    try:
        return today.replace(year=today.year - 5).strftime("%Y%m%d")
    except ValueError:  # 2/29
        return (today.replace(year=today.year - 5) + timedelta(days=1)).strftime(
            "%Y%m%d"
        )


def _windows(first: date, last: date, days: int) -> list[tuple[date, date]]:
    """[first, last] 切窗口（左闭右闭，天对齐）。"""
    days = max(1, int(days))
    out: list[tuple[date, date]] = []
    cur = first
    while cur <= last:
        end = min(cur + timedelta(days=days - 1), last)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


def _disk_gate(min_free_gb: float) -> None:
    from backend.services.live_trading.services.tdx_ingest.sinks import archive_base

    base = archive_base()
    probe = base
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_gb = shutil.disk_usage(probe).free / _GIB
    if free_gb < min_free_gb:
        logger.error(
            "磁盘闸：%s 余量 %.1fGB < %.1fGB，中止（--min-free-gb 可调）",
            probe,
            free_gb,
            min_free_gb,
        )
        raise SystemExit(3)
    logger.info("磁盘闸通过：%s 余量 %.1fGB", probe, free_gb)


# ============ 采集主流程 ============


async def _sector_codes(ctx) -> tuple[list[str], dict[str, str]]:
    """BK 实体 = 板块清单（一次拉取，全程复用）。"""
    from backend.services.live_trading.services.tdx_ingest import parsers
    from backend.services.live_trading.services.tdx_ingest.bridge_client import (
        BridgeError,
    )
    from backend.services.live_trading.services.tdx_ingest.fetchers import (
        _value_of,
        call_bridge,
    )

    result = await call_bridge(ctx, "get_sector_list", {"list_type": 1})
    codes: list[str] = []
    names: dict[str, str] = {}
    payload = _value_of(result)
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            code = str(parsers._pick(item, "Code") or "").strip()
            if code:
                codes.append(code)
                names[code] = str(parsers._pick(item, "Name") or "").strip()[:64]
    if not codes:
        raise BridgeError("bridge", "板块清单为空", method="get_sector_list")
    return codes, names


async def _fetch_window(ctx, key: str, w_start: str, w_end: str, sectors) -> list[dict]:
    """一个窗口的取数（分批全量；与日频 fetcher 同方法同字段，仅改 start/end）。"""
    from backend.services.live_trading.services.tdx_ingest import parsers
    from backend.services.live_trading.services.tdx_ingest.datasets import (
        get_dataset_spec,
    )
    from backend.services.live_trading.services.tdx_ingest.fetchers import (
        _chunks,
        _value_of,
        call_bridge,
    )

    spec = get_dataset_spec(key)
    fields = list(spec.fields)

    if key == "sc":
        result = await call_bridge(
            ctx,
            "get_scjy_value",
            {"field_list": fields, "start_time": w_start, "end_time": w_end},
        )
        return parsers.parse_sc_value(_value_of(result), w_end)

    if key == "bk":
        codes, names = sectors
        rows: list[dict] = []
        for chunk in _chunks(codes, spec.batch_size):
            result = await call_bridge(
                ctx,
                "get_bkjy_value",
                {
                    "stock_list": chunk,
                    "field_list": fields,
                    "start_time": w_start,
                    "end_time": w_end,
                },
            )
            for row in parsers.parse_bk_value(_value_of(result), w_end):
                row["bk_name"] = names.get(row["bk_code"], "")
                rows.append(row)
        return rows

    # gp
    codes = await ctx.universe_codes()
    rows = []
    for chunk in _chunks(codes, spec.batch_size):
        result = await call_bridge(
            ctx,
            "get_gpjy_value",
            {
                "stock_list": chunk,
                "field_list": fields,
                "start_time": w_start,
                "end_time": w_end,
            },
        )
        for row in parsers.parse_gp_value(_value_of(result), w_end):
            row["symbol"] = parsers._symbol(row["stock_code"])
            rows.append(row)
    return rows


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    first = _validate_date(args.date_from, "--from")
    last = _validate_date(args.date_to, "--to")
    if first > last:
        raise SystemExit("--from 晚于 --to")

    pg_since = (args.pg_since or _default_pg_since()).strip()
    _validate_date(pg_since, "--pg-since")

    wins = _windows(first, last, args.window_days)
    est_calls = 0
    for _s, _e in wins:
        if args.dataset == "gp":
            est_calls += -(-_ASSUMED_UNIVERSE // 500)  # ceil(宇宙/批量)
        elif args.dataset == "bk":
            est_calls += 2  # 板块 ~587 只 → 2 批（清单 1 次未计）
        else:
            est_calls += 1
    logger.info(
        "回填计划：dataset=%s 窗口 %d 个（%s → %s，window-days=%d）"
        " 预估调用 ≈%d；PG 入库下限 %s",
        args.dataset,
        len(wins),
        first,
        last,
        max(1, args.window_days),
        est_calls,
        pg_since,
    )

    if args.dry_run:
        for s, e in wins[:20]:
            logger.info("  窗口 %s → %s", s.strftime("%Y%m%d"), e.strftime("%Y%m%d"))
        if len(wins) > 20:
            logger.info("  …… 其余 %d 个窗口省略", len(wins) - 20)
        return 0

    _disk_gate(args.min_free_gb)

    from backend.services.live_trading.services.tdx_ingest import sinks
    from backend.services.live_trading.services.tdx_ingest.bridge_client import (
        BridgeError,
        TdxBridgeClient,
    )
    from backend.services.live_trading.services.tdx_ingest.datasets import (
        get_dataset_spec,
    )
    from backend.services.live_trading.services.tdx_ingest.fetchers import FetchContext
    from backend.services.live_trading.services.tdx_ingest.rate_budget import RateBudget
    from backend.shared.tdx_ingest_contract import ensure_tdx_ingest_tables

    ensure_tdx_ingest_tables()
    spec = get_dataset_spec(args.dataset)

    redis_client = None
    try:
        from backend.services.trade.services.daily_pnl_report_task import _redis_client
        from backend.services.trade_shared.deps import get_redis

        redis_client = _redis_client(get_redis())
    except Exception as exc:  # noqa: BLE001 - 预算缺席由桥侧限流兜底
        logger.warning("Redis 不可用（令牌桶降级为放行）: %s", exc)

    budget = RateBudget(redis_client)
    client = TdxBridgeClient()
    started = time.monotonic()
    run_tag = date.today().strftime("%Y%m%d")

    # 断点：已完成窗口（末 ≤ last_target_date）整体跳过
    cursor = await sinks.read_cursor(spec.key, "backfill")
    resume_until = (cursor or {}).get("last_target_date") or ""
    if resume_until:
        logger.info("水位续跑：已完成至 %s，跳过其前窗口", resume_until)
    todo = [(s, e) for s, e in wins if e.strftime("%Y%m%d") > resume_until]
    if not todo:
        logger.info("窗口已全部完成（水位 %s），无事可做", resume_until)
        await client.aclose()
        return 0

    ctx = FetchContext(
        client=client, budget=budget, target_date=todo[0][1].strftime("%Y%m%d")
    )
    done_windows = 0
    rows_pg_total = 0
    rows_arch_total = 0
    calls_before = ctx.calls
    try:
        sectors = None
        if args.dataset == "bk":
            sectors = await _sector_codes(ctx)
            logger.info("板块实体 %d 个", len(sectors[0]))

        for w_start, w_end in todo:
            ws, we = w_start.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")
            t0 = time.monotonic()
            ctx.target_date = we
            calls_mark = ctx.calls
            try:
                rows = await _fetch_window(ctx, spec.key, ws, we, sectors)
            except BridgeError as exc:
                if exc.kind == "blocked":
                    logger.error("家族被阻塞（数据包未下载），退出：%s", exc)
                    await sinks.update_cursor(
                        spec.key, "backfill", status="blocked", error=str(exc)[:2000]
                    )
                    return 2
                await sinks.record_run(
                    dataset=spec.key,
                    slot="backfill",
                    target_date=we,
                    status="error",
                    calls=ctx.calls - calls_mark,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=str(exc)[:2000],
                )
                await sinks.update_cursor(
                    spec.key, "backfill", status="error", error=str(exc)[:2000]
                )
                logger.error("窗口 %s→%s 失败，水位停在其前，重跑接续: %s", ws, we, exc)
                return 1

            rows_pg = [r for r in rows if str(r.get("trade_date") or "") >= pg_since]
            written = await sinks.upsert_rows(spec, rows_pg) if rows_pg else 0
            archive_path, archived = await sinks.write_archive(
                spec, rows, we, run_key=f"{run_tag}w{we}"
            )
            await sinks.record_run(
                dataset=spec.key,
                slot="backfill",
                target_date=we,
                status="ok",
                rows_written=written,
                rows_archived=archived,
                calls=ctx.calls - calls_mark,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            await sinks.update_cursor(
                spec.key,
                "backfill",
                status="ok",
                last_target_date=we,
                rows_delta=written,
            )
            done_windows += 1
            rows_pg_total += written
            rows_arch_total += archived
            logger.info(
                "窗口 %s→%s ok：行 %d（PG %d）→ %s",
                ws,
                we,
                len(rows),
                written,
                archive_path or "(空窗口不产文件)",
            )
            if (
                args.max_minutes
                and (time.monotonic() - started) / 60 >= args.max_minutes
            ):
                logger.info(
                    "--max-minutes 到点自停：已完成 %d/%d 窗口，重跑自动接续",
                    done_windows,
                    len(todo),
                )
                break
    finally:
        await client.aclose()

    logger.info(
        "回填收尾：窗口 %d/%d，桥调用 %d，PG 行 %d，归档行 %d，耗时 %.1f 分钟",
        done_windows,
        len(todo),
        ctx.calls - calls_before,
        rows_pg_total,
        rows_arch_total,
        (time.monotonic() - started) / 60,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
