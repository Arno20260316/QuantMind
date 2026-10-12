"""派发前行情 on-demand 刷新（``tdx_quote_feed.refresh_symbols_now``）单测。

背景（2026-10-12 决策轮 22/22 拒 ``l3.stale_quote``）：热集轮转 ~100s 才轮到同一只
票一圈，而风控时效门 ``max_age_s=5``——池票快照在提交时刻必然判「陈旧」。修复面 =
决策腿提交前逐只直取桥快照写标准键（``decision_executor.execute_batch`` 已接线）。

本文件钉住该函数自己的契约（全部离线：桥与 Redis 写入都是替身）：

1. **桥未配置 = 立即空返**（OSS 默认无桥，不该刷日志也不该逐只重试）；
2. **归一/去重/截断**：前缀式、后缀式、大小写重复入参 → 桥只见后缀式且只查一次；
3. **逐只失败隔离**：某只报错/映射失败/超时 → 只记那一只 False，整批继续；
4. **两道时延护栏**：单只 2s（``wait_for``）+ 整批 8s（deadline）——桥活着但
   客户端挂起时不许把派发卡死；
5. **节流**：只查一只不睡；多只之间按 ``ON_DEMAND_PACING_S`` 让桥喘气。

``feed_status`` 是模块级累积计数器——用例一律断言**增量**，不依赖绝对值。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.services.live_trading.services import tdx_quote_feed as feed

_HEALTHY = {
    "Now": "10.00",
    "Open": "9.80",
    "Max": "10.10",
    "Min": "9.70",
    "LastClose": "9.90",
    "Volume": "12345",
    "Amount": "123450.0",
}


class _FakePusher:
    """``tdx_pusher`` 替身：记录调用；按标的安排返回/异常/挂起。"""

    HANG = object()  # results 值 = HANG → 该只永不返回（等 wait_for 掐）

    def __init__(self, *, enabled: bool = True, results: dict | None = None) -> None:
        self.enabled = enabled
        self.calls: list[tuple[str, dict]] = []
        self._results = results or {}

    async def tdx_call(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        planned = self._results.get(params.get("stock_code"), None)
        if isinstance(planned, Exception):
            raise planned
        if planned is self.HANG:
            await asyncio.sleep(60)
        if planned is None:
            return dict(_HEALTHY)
        return planned


def _record_writes(monkeypatch) -> list[tuple[str, dict]]:
    written: list[tuple[str, dict]] = []

    async def _fake_write(prefix: str, snap: dict) -> bool:
        written.append((prefix, snap))
        return True

    monkeypatch.setattr(feed, "_write_snapshot", _fake_write)
    return written


def _counters() -> dict:
    return {
        k: feed.feed_status.get(k)
        for k in ("on_demand_calls", "on_demand_ok", "on_demand_last_at")
    }


# ── 1. 桥未配置：立即空返 ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_bridge_returns_empty_without_any_call(monkeypatch) -> None:
    pusher = _FakePusher(enabled=False)
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    written = _record_writes(monkeypatch)

    result = await feed.refresh_symbols_now(["600036.SH", "000001.SZ"])

    assert result == {}
    assert pusher.calls == []
    assert written == []
    assert "未配置" in (feed.feed_status.get("on_demand_last_error") or "")


# ── 2. 归一 / 去重 / 截断 / 成功写入 ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normalizes_dedupes_and_writes_prefix_key(monkeypatch) -> None:
    pusher = _FakePusher()
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    written = _record_writes(monkeypatch)
    before = _counters()

    # 同一只票的四种写法 + 空串/None：桥只该被问一次，问的是后缀式
    result = await feed.refresh_symbols_now(
        ["SH600036", "600036.SH", "sh600036", "", "   ", None]
    )

    assert [c[1]["stock_code"] for c in pusher.calls] == ["600036.SH"]
    assert [c[0] for c in pusher.calls] == ["get_market_snapshot"]
    assert result == {"600036.SH": True}
    # 标准键写的是**前缀式**（与风控 _quote_snapshot 的读口径一致）
    assert len(written) == 1
    prefix, snap = written[0]
    assert prefix == "SH600036"
    assert snap["Now"] == pytest.approx(10.0) and snap["PreClose"] == pytest.approx(9.9)
    assert "timestamp" in snap
    after = _counters()
    assert after["on_demand_calls"] == before["on_demand_calls"] + 1
    assert after["on_demand_ok"] == before["on_demand_ok"] + 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cap_truncates_to_max_symbols(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_MAX_SYMBOLS", 3)
    monkeypatch.setattr(feed, "ON_DEMAND_PACING_S", 0)
    pusher = _FakePusher()
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    _record_writes(monkeypatch)

    result = await feed.refresh_symbols_now(
        ["600036.SH", "000001.SZ", "600519.SH", "300750.SZ", "601318.SH"]
    )

    assert len(result) == 3 and len(pusher.calls) == 3
    assert list(result) == ["600036.SH", "000001.SZ", "600519.SH"]


# ── 3. 逐只失败隔离 ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_symbol_failure_does_not_stop_the_batch(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_PACING_S", 0)
    pusher = _FakePusher(results={"600036.SH": RuntimeError("桥 500")})
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    written = _record_writes(monkeypatch)
    before = _counters()

    result = await feed.refresh_symbols_now(["600036.SH", "000001.SZ"])

    assert result == {"600036.SH": False, "000001.SZ": True}
    assert len(pusher.calls) == 2  # 第二只照查，不受第一只失败影响
    assert [w[0] for w in written] == ["SZ000001"]
    after = _counters()
    assert after["on_demand_calls"] == before["on_demand_calls"] + 2
    assert after["on_demand_ok"] == before["on_demand_ok"] + 1
    assert "600036.SH" in (feed.feed_status.get("on_demand_last_error") or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unmappable_payload_is_false_and_writes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_PACING_S", 0)
    pusher = _FakePusher(results={"600036.SH": {"foo": 1}})  # 关键字段缺失
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    written = _record_writes(monkeypatch)

    result = await feed.refresh_symbols_now(["600036.SH"])

    assert result == {"600036.SH": False}
    assert written == []  # 映射失败绝不写空/脏快照（宁缺毋假）
    assert "映射失败" in (feed.feed_status.get("on_demand_last_error") or "")


# ── 4. 时延护栏 ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_call_timeout_isolates_a_hung_symbol(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_CALL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(feed, "ON_DEMAND_PACING_S", 0)
    pusher = _FakePusher(results={"600036.SH": _FakePusher.HANG})
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    _record_writes(monkeypatch)

    t0 = time.monotonic()
    result = await feed.refresh_symbols_now(["600036.SH", "000001.SZ"])
    elapsed = time.monotonic() - t0

    assert result == {"600036.SH": False, "000001.SZ": True}
    assert elapsed < 1.0  # 挂起的那只被 wait_for 掐掉，没拖死整批


@pytest.mark.unit
@pytest.mark.asyncio
async def test_total_budget_zero_skips_everything(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_TOTAL_BUDGET_S", 0.0)
    pusher = _FakePusher()
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    _record_writes(monkeypatch)

    result = await feed.refresh_symbols_now(["600036.SH", "000001.SZ", "600519.SH"])

    assert result == {}
    assert pusher.calls == []  # 预算在一开始就已用尽 → 一只都不许查
    assert "跳过剩余 3 只" in (feed.feed_status.get("on_demand_last_error") or "")


# ── 5. 节流 ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pacing_breathes_between_symbols_only(monkeypatch) -> None:
    monkeypatch.setattr(feed, "ON_DEMAND_PACING_S", 0.03)
    pusher = _FakePusher()
    monkeypatch.setattr(feed, "tdx_pusher", pusher)
    _record_writes(monkeypatch)

    t0 = time.monotonic()
    result = await feed.refresh_symbols_now(["600036.SH", "000001.SZ", "600519.SH"])
    elapsed = time.monotonic() - t0

    assert len(result) == 3
    # 3 只 → 只睡 2 次间隔（0.06s），最后一只查完不睡
    assert elapsed >= 0.045
    assert elapsed < 2.0
