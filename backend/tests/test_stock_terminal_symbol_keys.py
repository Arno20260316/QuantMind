"""候选信号页 `symbol` 键形归一化回归测试（2026-10-13 空列表 bug）。

`engine_signal_scores.symbol` 同日可并存三种写法：prefix（``SH600036``，自定义市场/
日频批量）、suffix（``600036.SH``，盘中实时）、bare（``600036``）。`/stock-terminal/list`
的消费端（``df["Symbol"].str.split(".")[0]`` / ``trend_map.get(bare)``）一律是**裸六位码**
口径，而写侧历史上按字面 ``symbol`` 建键 → 键形错配 → 整页分数/方向/趋势全 miss
（候选信号列表空、唯一原因是默认 ``filters={side:'BUY'}`` 命中空集）。

同源的第二处缺陷：``market`` 列有第三个值 ``'CUSTOM'``（自定义市场按 a_share 适配器
+ SSE 日历 + SH000300 基准构建，落库 symbol 100% A 股形态）。2026-10-13 全市场带
BUY/SELL 的行**只**存在于 CUSTOM 桶，把它当非 A 股滤掉 → 列表整片为空。

本测试用**合成模型**（两个 run_id → 同一 model_id，横跨两个 trade_date）夯入三形代码，
断言三种写法都能在列表里命中 ``fusion`` / ``side`` / ``trend``，且三个市场口径
（``CN`` / ``CUSTOM`` / NULL）都进得来。用合成模型是因为真实推理数据在历史日同样存在
（``run_id IN (SELECT ... WHERE model_id = :m)`` 会把真实行滤掉），直接改真实行会污染
生产账本。
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]

# 两个交易日：分区存在（dt=20261009 / dt=20261012），universe 可解析。
# 恰好两个日期 → _trend_map 走 s0 is None 分支 → 标签「上升」（三日递增会是「连续上升」）。
_DAY1 = date(2026, 10, 9)
_DAY2 = date(2026, 10, 12)

# (symbol 原文, day1 分数, day2 分数, market) —— 覆盖 prefix / suffix / bare 三形
# 与 CN / CUSTOM / NULL 三个市场口径（NULL = 老库 market 列未回填）
_ROWS = (
    ("SH600036", 0.30, 0.90, "CUSTOM"),  # prefix：自定义市场桶写法
    ("601398.SH", 0.20, 0.80, "CN"),  # suffix：盘中实时推理写法
    ("000001", 0.10, 0.70, None),  # bare：纯六位码 + market 未回填
)
# 同一只股（000001）的**前缀形重复行**，字面排序恒在数字形之后、created_at 更旧。
# 旧实现按字面 symbol 去重 + Python 后写覆盖，等价于「前缀形恒胜」，会把 0.70 换成 0.99。
_STALE_DUP = ("SZ000001", 0.99)
_EXPECT_BARE = {  # 三形归一后的裸码（断言用）
    "SH600036": "600036",
    "601398.SH": "601398",
    "000001": "000001",
}


def _call_kwargs(**overrides):
    """构造直呼路由所需的完整 kwargs。

    绕过 FastAPI 的 DI 直调路由函数时，**未显式传入的参数会保留 ``Query(...)``
    对象本身**（不是它声明的默认值），于是 ``if q and q.strip()`` 会炸在
    ``'Query' object has no attribute 'strip'``。这里从签名里把 FastAPI 会注入的
    真实缺省值取出来，与真实 HTTP 请求「不传该 query 参数」等价；
    只覆盖 Query 参数（``Depends`` 由调用方显式传）。
    """
    import inspect

    from fastapi.params import Query as _Query

    from backend.services.api.routers.stock_terminal import list_stocks

    kwargs = {}
    for name, param in inspect.signature(list_stocks).parameters.items():
        if isinstance(param.default, _Query):
            kwargs[name] = param.default.default
    kwargs.update(overrides)
    return kwargs


async def _db_ready() -> bool:
    from sqlalchemy import text

    from backend.shared.database_manager_v2 import close_database, get_session

    try:
        async with get_session(read_only=True) as probe:
            await probe.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        await close_database()
        try:
            async with get_session(read_only=True) as probe:
                await probe.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False


@pytest.mark.asyncio
async def test_list_hits_all_three_symbol_forms():
    try:
        from sqlalchemy import text

        from backend.shared.database_manager_v2 import get_session
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"依赖不可用: {exc}")

    if not await _db_ready():
        pytest.skip("DB 不可用")

    from backend.services.api.routers.stock_terminal import list_stocks

    tenant = "default"
    model_id = f"pytest-symkeys-{uuid.uuid4().hex[:8]}"
    run1, run2 = (str(uuid.uuid4()) for _ in range(2))

    try:
        async with get_session() as session:
            for run_id, day in ((run1, _DAY1), (run2, _DAY2)):
                await session.execute(
                    text(
                        "INSERT INTO qm_model_inference_runs "
                        "(run_id, tenant_id, user_id, model_id, data_trade_date, "
                        "prediction_trade_date, status, signals_count, fallback_used, "
                        "created_at, updated_at) VALUES "
                        "(:rid, :t, '1', :mid, :d, :d, 'completed', 3, false, now(), now())"
                    ),
                    {"rid": run_id, "t": tenant, "mid": model_id, "d": day},
                )
            for run_id, day, idx in ((run1, _DAY1, 1), (run2, _DAY2, 2)):
                for sym, s1, s2, mkt in _ROWS:
                    await session.execute(
                        text(
                            "INSERT INTO engine_signal_scores "
                            "(run_id, tenant_id, user_id, trade_date, symbol, fusion_score, "
                            "signal_side, model_version, source, market, created_at) VALUES "
                            "(:rid, :t, '1', :d, :sym, :fus, 'BUY', 'inference_script', "
                            "'batch', :mkt, now())"
                        ),
                        {
                            "rid": run_id,
                            "t": tenant,
                            "d": day,
                            "sym": sym,
                            "fus": s1 if idx == 1 else s2,
                            "mkt": mkt,
                        },
                    )
            # 只在该 model 的第二个交易日夯入「字面更后但更旧」的前缀重复行（回归靶子）
            await session.execute(
                text(
                    "INSERT INTO engine_signal_scores "
                    "(run_id, tenant_id, user_id, trade_date, symbol, fusion_score, "
                    "signal_side, model_version, source, market, created_at) VALUES "
                    "(:rid, :t, '1', :d, :sym, :fus, 'BUY', 'inference_script', "
                    "'batch', 'CUSTOM', now() - interval '1 hour')"
                ),
                {
                    "rid": run2,
                    "t": tenant,
                    "d": _DAY2,
                    "sym": _STALE_DUP[0],
                    "fus": _STALE_DUP[1],
                },
            )
            await session.commit()

        resp = await list_stocks(
            **_call_kwargs(
                market="ALL",
                date=_DAY2.isoformat(),
                model=model_id,
                side="BUY",
                page=1,
                page_size=50,
                current_user={"tenant_id": tenant, "user_id": "1"},
            )
        )
        data = resp["data"]

        # 三形代码全部命中（键形错配时这里是 0）。
        # 前缀重复行折进 000001，故只数 → 仍是 3（旧实现下它会另算一只 → 4）。
        assert data["total"] == len(_ROWS), f"total={data['total']}，三形未全部命中"
        assert data["signal_rows"] == len(_ROWS), (
            "signal_rows 应是该模型当日有分数的只数（前缀重复行须折进同裸码）"
        )

        items = {str(it["symbol"]).split(".")[0]: it for it in data["items"]}
        assert set(items) == set(_EXPECT_BARE.values())

        for item in items.values():
            assert item["fusion"] is not None, "分数未命中（键形错配）"
            assert item["side"] == "BUY", "信号方向未命中"
            assert item["trend"] == "上升", f"趋势未命中：{item['trend']!r}"
            assert item["signal_date"] == _DAY2.isoformat()

        # 分数正确落到对应票（prefix/suffix/bare 三形各自归位，未串行）
        assert items["600036"]["fusion"] == pytest.approx(0.90)
        assert items["601398"]["fusion"] == pytest.approx(0.80)
        # 字面更后但更旧的前缀重复行**不得**胜出（旧实现这里是 0.99）
        assert items["000001"]["fusion"] == pytest.approx(0.70), (
            "前缀形按字面序恒胜 —— 去重键未折到裸六位"
        )

        # 带 model 的请求也必须回选项（原 bug：只有不带 model 才回，前端「越筛越少」）
        assert data["models"], "带 model 的请求 models 不应为空"
    finally:
        from backend.shared.database_manager_v2 import (
            close_database,
            get_session as _gs,
        )

        async with _gs() as session:
            await session.execute(
                text("DELETE FROM engine_signal_scores WHERE run_id IN (:a, :b)"),
                {"a": run1, "b": run2},
            )
            await session.execute(
                text("DELETE FROM qm_model_inference_runs WHERE run_id IN (:a, :b)"),
                {"a": run1, "b": run2},
            )
            await session.commit()
        await close_database()


@pytest.mark.unit
def test_bare_symbol_folds_all_three_forms_and_rejects_non_a_share():
    from backend.services.api.routers.stock_terminal import _bare_symbol

    # 三形归一 → 裸六位码
    assert _bare_symbol("600036") == "600036"
    assert _bare_symbol("SH600036") == "600036"
    assert _bare_symbol("600036.SH") == "600036"
    assert _bare_symbol("sh600036") == "600036"  # 大小写不敏感
    assert _bare_symbol("000001") == "000001"
    assert _bare_symbol("SZ000001") == "000001"
    assert _bare_symbol("000001.SZ") == "000001"

    # 非 A 股 / 非法输入 → None（不能臆造裸码）
    assert _bare_symbol("AAPL") is None
    assert _bare_symbol("00700.HK") is None
    assert _bare_symbol("SH600036.SH") is None  # 双市场段
    assert _bare_symbol("") is None
    assert _bare_symbol(None) is None


@pytest.mark.unit
def test_symbol_keys_wiring_source_guards():
    src = (_BACKEND / "services/api/routers/stock_terminal.py").read_text(
        encoding="utf-8"
    )
    # 唯一归一化实现：复用 signal_scores.normalize_a_share_symbol，本模块只做去前缀
    assert (
        "from backend.shared.signal_scores import normalize_a_share_symbol, score_freq_of"
        in src
    )
    assert "def _bare_symbol(raw" in src
    assert "prefix = normalize_a_share_symbol(raw)" in src
    # 写侧按裸码建键（键形错配的根因）；事实错误的旧注释必须已删
    assert "_bare_symbol(str(r[0]))" in src
    assert "symbol 为纯数字" not in src
    # 趋势表键同样归一到裸码（含前缀形）
    assert "_bare_symbol(sym) or str(sym)" in src
    # 市场口径与去重键必须与 signal_scores 同一常量（不得再内联字面 CN 谓词）
    assert "A_SHARE_MARKET_SQL as _market_sql" in src
    assert "SYMBOL_FOLD_KEY as _fold_key" in src
    assert "market = 'CN'" not in src
    # 模型选项无条件回传（带 model 的请求也要回）
    assert 'models": model_options' in src
    assert '"signal_rows": len(score_info)' in src
