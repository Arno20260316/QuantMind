"""每数据集取数编排：宇宙加载 → 参数构造 → 分批调用 → 解析 → 行集合。

统一走 :func:`call_bridge`：先过 :class:`RateBudget` 令牌桶，成功后回报
``on_success``（速率自愈），RATE_LIMITED 回报 ``on_rate_limited``（退避+降速）
并重试（重试前的等待由 acquire 的惩罚期保证）。``blocked``（数据包家族）与
其余桥侧失败直接上抛，由任务层按数据集状态处理。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from backend.services.live_trading.services.tdx_ingest import parsers
from backend.services.live_trading.services.tdx_ingest.bridge_client import (
    BridgeError,
    TdxBridgeClient,
)
from backend.services.live_trading.services.tdx_ingest.datasets import (
    ALL_A_MARKET_KEY,
    MARKET_KEYS,
    DatasetSpec,
    get_dataset_spec,
)
from backend.services.live_trading.services.tdx_ingest.rate_budget import RateBudget

logger = logging.getLogger(__name__)

#: RATE_LIMITED 在 call_bridge 内的最大尝试次数（含首次）。
_RATE_LIMIT_ATTEMPTS = 3

#: more_info 逐股扫描的进度日志间隔（只跟股票数相关，与桥无关）。
_PROGRESS_EVERY = 500


@dataclass
class FetchContext:
    """一次数据集取数的运行上下文（每轮新建；宇宙码缓存于实例）。"""

    client: TdxBridgeClient
    budget: RateBudget
    target_date: str
    calls: int = 0  # 桥调用次数（含重试，面板的预算证据）
    failures: list[str] = field(default_factory=list)  # 单股/单批软失败记录
    _universe: list[str] | None = field(default=None, repr=False)

    async def universe_codes(self) -> list[str]:
        """全 A 代码（后缀式）。缓存于上下文（一轮内多数据集共享）。"""
        if self._universe is None:
            self._universe = await load_universe_codes(self)
        return self._universe


async def call_bridge(ctx: FetchContext, method: str, params: dict[str, Any]) -> dict:
    """带预算闸 + RATE_LIMITED 退避重试的桥调用（返回 result 字典）。"""
    last: BridgeError | None = None
    for attempt in range(_RATE_LIMIT_ATTEMPTS):
        await ctx.budget.acquire()
        ctx.calls += 1
        try:
            result = await ctx.client.call(method, params)
        except BridgeError as exc:
            if exc.kind == "rate_limited" and attempt < _RATE_LIMIT_ATTEMPTS - 1:
                ctx.budget.on_rate_limited()
                last = exc
                continue
            raise
        ctx.budget.on_success()
        return result
    raise last or BridgeError("rate_limited", f"{method} 连续限流", method=method)


def _value_of(result: dict) -> Any:
    """桥 result → ``Value`` 载荷（``{ErrorId, Value}`` 信封拆封；无信封原样）。"""
    if isinstance(result, dict) and "Value" in result:
        return result.get("Value")
    return result


def _chunks(items: list[str], size: int) -> list[list[str]]:
    size = max(1, int(size or 1))
    return [items[i : i + size] for i in range(0, len(items), size)]


# ============ 宇宙 ============


async def load_universe_codes(ctx: FetchContext) -> list[str]:
    """全 A 代码（后缀式）：PG 最新清单优先，桥直拉兜底。

    15:45 档写 :mod:`tdx_stock_list_snapshot`，其后档位（16:45 more_info /
    17:20 go / 17:30 gp / 23:50 / 06:30）读最新清单——避开了「每次扫描都打桥
    拉清单」的重复调用；首跑无清单时桥直拉。
    """
    codes = await _universe_from_pg()
    if codes:
        return codes
    logger.info("[TdxIngest] PG 无清单，桥直拉全 A（market=%s）", ALL_A_MARKET_KEY)
    result = await call_bridge(
        ctx, "get_stock_list", {"market": ALL_A_MARKET_KEY, "list_type": 1}
    )
    rows = parsers.parse_stock_list(
        _value_of(result), ALL_A_MARKET_KEY, ctx.target_date
    )
    codes = [r["stock_code"] for r in rows]
    if not codes:
        raise BridgeError(
            "bridge", "全 A 清单为空（PG 与桥均无）", method="get_stock_list"
        )
    return codes


async def _universe_from_pg() -> list[str]:
    """PG 最新交易日 market_key='5' 的后缀式代码表（读失败 → 空，走桥兜底）。"""
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session

        async with get_session(read_only=True) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT stock_code, MAX(trade_date) AS d "
                        "FROM tdx_stock_list_snapshot WHERE market_key = :mk "
                        "GROUP BY stock_code"
                    ),
                    {"mk": ALL_A_MARKET_KEY},
                )
            ).all()
        return sorted({str(r[0]) for r in rows if r[0]})
    except Exception as exc:  # noqa: BLE001 - 表未建/库不可达都走桥兜底
        logger.warning("[TdxIngest] 读 PG 清单失败（走桥兜底）: %s", exc)
        return []


# ============ 各数据集取数 ============


async def fetch_pricevol(ctx: FetchContext) -> list[dict]:
    """全 A 收盘快照（1 次调用，实测 3.1s / 5581 行）。

    **stock_list 必传**：探针的 500/全量两档都带显式代码表；空参直接
    bridge 500（实测 2026-10-15）。宇宙走 PG 清单（15:45 档写过则零额外
    调用），首跑回落桥 get_stock_list。
    """
    codes = await ctx.universe_codes()
    result = await call_bridge(ctx, "get_pricevol", {"stock_list": codes})
    return parsers.parse_pricevol(_value_of(result), ctx.target_date)


async def fetch_universe(ctx: FetchContext) -> list[dict]:
    """分类 market 清单全刷（22 次调用）。单类失败不阻断（记 failures），
    全类失败才上抛（与「单股软失败」同哲学）。"""
    rows: list[dict] = []
    had_success = False
    for market_key in MARKET_KEYS:
        try:
            result = await call_bridge(
                ctx, "get_stock_list", {"market": market_key, "list_type": 1}
            )
        except BridgeError as exc:
            if exc.kind == "rate_limited":
                raise
            ctx.failures.append(f"market={market_key}: {exc}")
            continue
        had_success = True
        rows.extend(
            parsers.parse_stock_list(_value_of(result), market_key, ctx.target_date)
        )
    if not had_success:
        raise BridgeError("bridge", "股票清单全部分类失败", method="get_stock_list")
    return rows


async def fetch_more_info(ctx: FetchContext) -> list[dict]:
    """逐股 more_info 全字段（5581 次调用 ≈31min；单股桥错软失败不中断）。"""
    codes = await ctx.universe_codes()
    rows: list[dict] = []
    for i, code in enumerate(codes, 1):
        try:
            result = await call_bridge(ctx, "get_more_info", {"stock_code": code})
        except BridgeError as exc:
            if exc.kind == "rate_limited":
                raise
            ctx.failures.append(f"{code}: {exc}")
            continue
        row = parsers.parse_more_info(_value_of(result), code, ctx.target_date)
        if row is not None:
            rows.append(row)
        if i % _PROGRESS_EVERY == 0:
            logger.info(
                "[TdxIngest] more_info 进度 %d/%d（行 %d，软失败 %d）",
                i,
                len(codes),
                len(rows),
                len(ctx.failures),
            )
    return rows


async def fetch_go(ctx: FetchContext) -> list[dict]:
    """一致预期/一次性数据（GO1–GO47，分批）。"""
    spec = get_dataset_spec("go")
    codes = await ctx.universe_codes()
    rows: list[dict] = []
    for chunk in _chunks(codes, spec.batch_size):
        result = await call_bridge(
            ctx,
            "get_gp_one_data",
            {"stock_list": chunk, "field_list": list(spec.fields)},
        )
        rows.extend(parsers.parse_go_value(_value_of(result), ctx.target_date))
    return rows


async def fetch_gp(ctx: FetchContext) -> list[dict]:
    """个股专业序列（GP01–GP46，分批；start=end=目标日）。"""
    spec = get_dataset_spec("gp")
    codes = await ctx.universe_codes()
    rows: list[dict] = []
    for chunk in _chunks(codes, spec.batch_size):
        result = await call_bridge(
            ctx,
            "get_gpjy_value",
            {
                "stock_list": chunk,
                "field_list": list(spec.fields),
                "start_time": ctx.target_date,
                "end_time": ctx.target_date,
            },
        )
        for row in parsers.parse_gp_value(_value_of(result), ctx.target_date):
            row["symbol"] = parsers._symbol(row["stock_code"])
            rows.append(row)
    return rows


async def fetch_sc(ctx: FetchContext) -> list[dict]:
    """市场级序列（SC01–SC42，无实体维，1 次调用）。"""
    spec = get_dataset_spec("sc")
    result = await call_bridge(
        ctx,
        "get_scjy_value",
        {
            "field_list": list(spec.fields),
            "start_time": ctx.target_date,
            "end_time": ctx.target_date,
        },
    )
    return parsers.parse_sc_value(_value_of(result), ctx.target_date)


async def fetch_bk(ctx: FetchContext) -> list[dict]:
    """板块专业序列（BK5–BK19）：先拉板块清单（含名称），再分批取数。"""
    spec = get_dataset_spec("bk")
    result = await call_bridge(ctx, "get_sector_list", {"list_type": 1})
    sectors = _value_of(result)
    codes: list[str] = []
    names: dict[str, str] = {}
    if isinstance(sectors, list):
        for item in sectors:
            if not isinstance(item, dict):
                continue
            code = str(parsers._pick(item, "Code") or "").strip()
            if not code:
                continue
            codes.append(code)
            names[code] = str(parsers._pick(item, "Name") or "").strip()[:64]
    if not codes:
        raise BridgeError("bridge", "板块清单为空", method="get_sector_list")
    rows: list[dict] = []
    for chunk in _chunks(codes, spec.batch_size):
        result = await call_bridge(
            ctx,
            "get_bkjy_value",
            {
                "stock_list": chunk,
                "field_list": list(spec.fields),
                "start_time": ctx.target_date,
                "end_time": ctx.target_date,
            },
        )
        for row in parsers.parse_bk_value(_value_of(result), ctx.target_date):
            row["bk_name"] = names.get(row["bk_code"], "")
            rows.append(row)
    return rows


async def fetch_zdt(ctx: FetchContext) -> list[dict]:
    """涨跌停状态（分批；当前接口结构性全零，数据集挂起不入库）。"""
    spec = get_dataset_spec("zdt")
    codes = await ctx.universe_codes()
    rows: list[dict] = []
    for chunk in _chunks(codes, spec.batch_size):
        result = await call_bridge(ctx, "get_zdt_data", {"stock_list": chunk})
        rows.extend(parsers.parse_zdt(_value_of(result), ctx.target_date))
    return rows


#: 数据集 key → 取数协程（注册表一致性由测试守：== DATASETS 全 key）。
FETCHERS: dict[str, Callable[[FetchContext], Awaitable[list[dict]]]] = {
    "pricevol": fetch_pricevol,
    "universe": fetch_universe,
    "more_info": fetch_more_info,
    "go": fetch_go,
    "gp": fetch_gp,
    "sc": fetch_sc,
    "bk": fetch_bk,
    "zdt": fetch_zdt,
}


async def fetch_dataset(spec: DatasetSpec, ctx: FetchContext) -> list[dict]:
    """按注册表取数（挂起数据集也可取——是否入库由任务层按 spec.enabled 决定）。"""
    fetcher = FETCHERS.get(spec.key)
    if fetcher is None:
        raise KeyError(f"数据集 {spec.key} 无取数实现")
    return await fetcher(ctx)


__all__ = [
    "FETCHERS",
    "FetchContext",
    "call_bridge",
    "fetch_dataset",
    "load_universe_codes",
]
