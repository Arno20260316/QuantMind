"""手跑入口（调试/补数）：

    python -m backend.services.live_trading.services.tdx_ingest.manual \
        --once --dataset pricevol [--date 20261015] [--force]

``--date`` 缺省 = 今日（非交易日回退上一交易日）。挂起数据集（zdt）默认
跳过，``--force`` 单跑（盘中复测用）。退出码：0=ok/partial，1=失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

from backend.services.live_trading.services.tdx_ingest.bridge_client import (
    TdxBridgeClient,
)
from backend.services.live_trading.services.tdx_ingest.datasets import (
    DATASETS_BY_KEY,
    get_dataset_spec,
)
from backend.services.live_trading.services.tdx_ingest.rate_budget import RateBudget
from backend.services.live_trading.services.tdx_ingest.task import (
    run_dataset,
    resolve_target,
)
from backend.services.live_trading.services.trading_session import TZ
from backend.services.trade.services.daily_pnl_report_task import (
    _is_trading_day,
    _redis_client,
)

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通达信桥采集手跑")
    parser.add_argument("--once", action="store_true", help="跑一轮（当前唯一模式）")
    parser.add_argument(
        "--dataset",
        required=True,
        choices=sorted(DATASETS_BY_KEY),
        help="数据集 key",
    )
    parser.add_argument(
        "--date", default="", help="目标交易日 YYYYMMDD（缺省=今日/上一交易日）"
    )
    parser.add_argument("--force", action="store_true", help="挂起数据集也单跑")
    return parser.parse_args(argv)


async def _default_target() -> str:
    now = datetime.now(TZ)
    if await _is_trading_day(now.date()):
        return now.date().strftime("%Y%m%d")
    return (await resolve_target("06:30", now)) or now.date().strftime("%Y%m%d")


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    # 表结构自愈（幂等；老库/新库都先确保）
    from backend.shared.tdx_ingest_contract import ensure_tdx_ingest_tables

    ensure_tdx_ingest_tables()

    spec = get_dataset_spec(args.dataset)
    target = args.date.strip() or await _default_target()
    if not (len(target) == 8 and target.isdigit()):
        logger.error("目标日非法: %r", target)
        return 1

    redis_client = None
    try:
        from backend.services.trade_shared.deps import get_redis

        redis_client = _redis_client(get_redis())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用（状态面降级）: %s", exc)

    client = TdxBridgeClient()
    budget = RateBudget(redis_client)
    try:
        result = await run_dataset(
            spec,
            slot="manual",
            target_date=target,
            client=client,
            budget=budget,
            redis_client=redis_client,
            force=args.force,
        )
    finally:
        await client.aclose()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("ok", "partial", "suspended", "blocked") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
