"""通达信桥采集真桥验收（批次 1）——实拉 → 解析 → 落库 → 归档 → 自清理。

设计约束（先例 ``test_tdx_hot_set_feed.py``：真桥取数 + 立即清理）：

* **哨兵目标日 ``19000101``**：所有测试行都挂在永不可能的日期上，一眼可辨，
  按日精确清理（真账不受污染）；
* 归档目录经 ``QM_TDX_INGEST_ARCHIVE_DIR`` 改到 tmp_path（不落 /data）；
* 令牌桶用 ``RateBudget(None)``（无 Redis 放行）——真桥 600/min 限流兜底；
* 台账行用 ``slot='test'``（列宽 VARCHAR(8)，production 档位是 HH:MM）；
* **每个用例恰好一次 ``asyncio.run``**：asyncpg 连接池按创建它的 event loop
  归属，跨 ``asyncio.run`` 复用会撞 "Event loop is closed"——所以 PG 检查、
  清理与 ``close_database()``（注销本 loop 的引擎）全部放进同一条 flow。

GP 家族用例是**两态自适应**的：数据包未下载 → blocked(table_list)；数据包
就位 → 解析产物必须落库。两种环境都算通过，但形态必须自洽——防的是「数据包
来了采集链静默空转」这类无感退化。
"""

from __future__ import annotations

import asyncio

import pytest

#: 永不可能的交易日：测试行锚点（PG 行 / 归档分区都用它，清理按它删）。
SENTINEL_DATE = "19000101"

#: 测试台账 slot（VARCHAR(8)；production 是 HH:MM，manual 是手动触发）。
TEST_SLOT = "test"


def _ensure_tables() -> None:
    """同步 psycopg2 路径（与 event loop 无关，可在 flow 外先跑）。"""
    from backend.shared.tdx_ingest_contract import ensure_tdx_ingest_tables

    assert ensure_tdx_ingest_tables(), "契约建表失败（PG 不可达？）"


async def _run_dataset_cli(key: str) -> dict:
    """真桥单数据集一轮（客户端在这里建、保证关）。"""
    from backend.services.live_trading.services.tdx_ingest.bridge_client import (
        TdxBridgeClient,
    )
    from backend.services.live_trading.services.tdx_ingest.datasets import (
        get_dataset_spec,
    )
    from backend.services.live_trading.services.tdx_ingest.rate_budget import RateBudget
    from backend.services.live_trading.services.tdx_ingest.task import run_dataset

    client = TdxBridgeClient()
    try:
        return await run_dataset(
            get_dataset_spec(key),
            slot=TEST_SLOT,
            target_date=SENTINEL_DATE,
            client=client,
            budget=RateBudget(None),
            redis_client=None,
        )
    finally:
        await client.aclose()


async def _count(table: str) -> int:
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    async with get_session(read_only=True) as session:
        return int(
            (
                await session.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE trade_date = :d"),
                    {"d": SENTINEL_DATE},
                )
            ).scalar()
            or 0
        )


async def _sample_pricevol() -> list[tuple]:
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    async with get_session(read_only=True) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT stock_code, symbol, now_price, last_close, volume_hand "
                    "FROM tdx_pricevol_daily WHERE trade_date = :d "
                    "ORDER BY stock_code LIMIT 5"
                ),
                {"d": SENTINEL_DATE},
            )
        ).all()
    return [tuple(r) for r in rows]


async def _one_price(stock_code: str) -> float | None:
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import get_session

    async with get_session(read_only=True) as session:
        raw = (
            await session.execute(
                text(
                    "SELECT now_price FROM tdx_pricevol_daily "
                    "WHERE trade_date = :d AND stock_code = :c"
                ),
                {"d": SENTINEL_DATE, "c": stock_code},
            )
        ).scalar()
    return float(raw) if raw is not None else None


async def _cleanup() -> None:
    """删全部测试痕迹：哨兵日数据行 + test 台账 + 哨兵日水位（条件删）。"""
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import close_database, get_session

    try:
        async with get_session() as session:
            await session.execute(
                text("DELETE FROM tdx_pricevol_daily WHERE trade_date = :d"),
                {"d": SENTINEL_DATE},
            )
            await session.execute(
                text("DELETE FROM tdx_gpjy_long WHERE trade_date = :d"),
                {"d": SENTINEL_DATE},
            )
            await session.execute(
                text("DELETE FROM tdx_ingest_runs WHERE slot = :s"), {"s": TEST_SLOT}
            )
            # 水位行：只删「最后目标日 = 哨兵日」的（生产 daily 水位可能已存在，
            # 非哨兵的绝不动）
            await session.execute(
                text(
                    "DELETE FROM tdx_ingest_cursor WHERE window_key = 'daily' "
                    "AND last_target_date = :d"
                ),
                {"d": SENTINEL_DATE},
            )
    finally:
        # 注销本 loop 的引擎池：下一个用例的 asyncio.run 会重建（否则撞
        # "attached to a different loop"）
        await close_database()


@pytest.mark.integration
def test_pricevol_roundtrip_real_bridge(monkeypatch, tmp_path):
    """全市场收盘快照：真实调用 → 全 A 行落 PG + parquet 归档。

    断言链 = 台账（rows_written）== PG 实count == parquet 行数（三处一致），
    外加双列口径抽查（symbol=前缀式 与 stock_code=后缀式 互推）。
    """
    monkeypatch.setenv("QM_TDX_INGEST_ARCHIVE_DIR", str(tmp_path))
    _ensure_tables()

    async def _flow() -> dict:
        try:
            res = await _run_dataset_cli("pricevol")
            assert res["status"] == "ok", res
            # 1 次 get_pricevol；PG 无清单时 +1 次 get_stock_list 拉宇宙
            assert 1 <= res["calls"] <= 2, f"pricevol 调用数异常: {res}"
            assert res["rows_written"] > 100, f"全 A 快照行数异常: {res}"
            assert res["done"] is True and res["error"] == ""

            pg_rows = await _count("tdx_pricevol_daily")
            assert pg_rows == res["rows_written"], "PG 行数 != 台账 rows_written"

            sample = await _sample_pricevol()
            assert sample, "PG 里没有哨兵日测试行"
            from backend.shared.stock_utils import StockCodeUtil

            for stock_code, symbol, _now, _lc, _vol in sample:
                assert symbol == StockCodeUtil.to_prefix(stock_code), (
                    stock_code,
                    symbol,
                )
                assert stock_code.endswith((".SH", ".SZ", ".BJ")), stock_code
            # 活跃票抽查（招行每日成交；挂掉的票 Now 可为 0，不作普适断言）
            cmb = await _one_price("600036.SH")
            assert cmb is not None and cmb > 0, "招行快照缺失或价格异常"
            return res
        finally:
            await _cleanup()

    res = asyncio.run(_flow())

    path = tmp_path / "pricevol" / f"date={SENTINEL_DATE}" / "data.parquet"
    assert path.exists(), f"归档缺失: {path}"
    import pandas as pd

    df = pd.read_parquet(path)
    assert len(df) == res["rows_archived"] == res["rows_written"]
    assert {"stock_code", "symbol", "trade_date"} <= set(df.columns)


@pytest.mark.integration
def test_gp_family_two_state_contract_real_bridge(monkeypatch, tmp_path):
    """GP 家族两态自适应：blocked(table_list) 或 数据包就位后正常落库。

    当前实测态 = blocked（探针 07：`json has no table_list`，客户端数据包未
    下载）。本用例两态都通过但形态必须自洽——数据包落地当天即自动转第二态，
    不需要改测试。
    """
    monkeypatch.setenv("QM_TDX_INGEST_ARCHIVE_DIR", str(tmp_path))
    _ensure_tables()

    async def _flow() -> dict:
        try:
            res = await _run_dataset_cli("gp")
            if res["status"] == "blocked":
                assert "table_list" in res["error"], res
                assert res["rows_written"] == 0
                assert res["done"] is True, "blocked 是终结态（不重试不告警）"
            else:
                assert res["status"] in ("ok", "partial"), res
                pg_rows = await _count("tdx_gpjy_long")
                assert pg_rows == res["rows_written"], "GP 落库行数与台账不符"
            return res
        finally:
            await _cleanup()

    asyncio.run(_flow())
