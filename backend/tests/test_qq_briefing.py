"""QQ 情报速览（backend/shared/qq_briefing.py）单元测试。

口径（2026-10-12 用户拍板）：非持仓情报只进 3 档合集（08:35/11:35/15:15）；
卡片式排版；利空/风险优先；利空优先排序、去重、20h 新鲜度上限、
读失败返回 None（调用方不得落 done）。
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

_NOW = 1_800_000_000.0  # 固定时钟：纯函数测试不依赖真实时间


class FakeRedis:
    """够用的内存 Redis 替身：list + pipeline + 可注入故障。"""

    def __init__(self, fail_on: str | None = None) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.kv: dict[str, str] = {}
        self.deleted: list[str] = []
        self.fail_on = fail_on

    def _maybe_fail(self, op: str) -> None:
        if self.fail_on == op:
            raise RuntimeError(f"injected failure: {op}")

    def rpush(self, key: str, value: str) -> int:
        self._maybe_fail("rpush")
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def ltrim(self, key: str, start: int, stop: int) -> bool:
        self._maybe_fail("ltrim")
        items = self.lists.get(key, [])
        self.lists[key] = items[start:] if stop == -1 else items[start : stop + 1]
        return True

    def expire(self, key: str, ttl: int) -> bool:
        self._maybe_fail("expire")
        self.ttls[key] = int(ttl)
        return True

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        self._maybe_fail("lrange")
        items = self.lists.get(key, [])
        if stop == -1:
            return items[start:]
        return items[start : stop + 1]

    def delete(self, key: str) -> int:
        self._maybe_fail("delete")
        self.deleted.append(key)
        existed = key in self.lists
        self.lists.pop(key, None)
        return 1 if existed else 0

    def exists(self, key: str) -> int:
        self._maybe_fail("exists")
        return 1 if key in self.kv else 0

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._maybe_fail("set")
        self.kv[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    def pipeline(self) -> FakeRedis:
        return self

    def execute(self) -> list[bool]:
        return [True]


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """替身 notify_async：收集 (title, body, channel)，不出网。"""
    calls: list[tuple[str, str, str]] = []

    import backend.shared.qq_notify as qq_notify

    def fake_notify_async(
        title: str, content: str = "", channel: str = "default"
    ) -> None:
        calls.append((title, content, channel))

    monkeypatch.setattr(qq_notify, "notify_async", fake_notify_async)
    return calls


# ── 档期判定 ──────────────────────────────────────────────────────────


def test_latest_due_slot_boundaries():
    from backend.shared.qq_briefing import latest_due_slot

    assert latest_due_slot((6, 0)) is None
    assert latest_due_slot((8, 34)) is None
    assert latest_due_slot((8, 35)).key == "morning"
    assert latest_due_slot((11, 34)).key == "morning"
    assert latest_due_slot((11, 35)).key == "noon"
    assert latest_due_slot((15, 14)).key == "noon"
    assert latest_due_slot((15, 15)).key == "close"
    assert latest_due_slot((23, 59)).key == "close"


def test_latest_due_slot_returns_single_latest_slot():
    """16:00 启动只认盘后一档——不会把早/午/盘后三档连着补发。"""
    from backend.shared.qq_briefing import latest_due_slot

    assert latest_due_slot((16, 0)).key == "close"


# ── 条目构造 ──────────────────────────────────────────────────────────


def test_build_entry_priority_mapping():
    from backend.shared.qq_briefing import build_entry

    assert build_entry(line="a", alert_type="news:risk_event")["pri"] == 0
    assert build_entry(line="a", alert_type="news:negative")["pri"] == 1
    assert (
        build_entry(line="a", alert_type="anomaly:price_surge", direction="down")["pri"]
        == 1
    )
    assert build_entry(line="a", alert_type="anomaly:price_surge")["pri"] == 2
    assert build_entry(line="a", alert_type="news:positive")["pri"] == 3
    assert build_entry(line="a", direction="up")["pri"] == 3
    assert build_entry(line="a", alert_type="regime")["pri"] == 4


def test_build_entry_strips_and_defaults_ts():
    from backend.shared.qq_briefing import build_entry

    entry = build_entry(line="  x  ", alert_type="news:negative")
    assert entry["line"] == "x"
    assert entry["ts"] > 0


# ── 入队 ──────────────────────────────────────────────────────────────


def test_enqueue_briefing_pushes_json_with_trim_and_ttl():
    from backend.shared.qq_briefing import buffer_key, enqueue_briefing

    client = FakeRedis()
    ok = enqueue_briefing(
        client, "CN", line="▼ 贵州茅台(600519) 批价下调传闻", alert_type="news:negative"
    )
    assert ok is True
    key = buffer_key("CN")
    assert len(client.lists[key]) == 1
    item = json.loads(client.lists[key][0])
    assert item["line"] == "▼ 贵州茅台(600519) 批价下调传闻"
    assert item["pri"] == 1
    assert client.ttls[key] > 0


def test_enqueue_briefing_blank_line_is_noop():
    from backend.shared.qq_briefing import enqueue_briefing

    client = FakeRedis()
    assert enqueue_briefing(client, "CN", line="   ") is False
    assert client.lists == {}


def test_enqueue_briefing_never_raises_on_redis_failure():
    from backend.shared.qq_briefing import enqueue_briefing

    client = FakeRedis(fail_on="rpush")
    assert enqueue_briefing(client, "CN", line="x", alert_type="news:positive") is False


# ── 卡片组合 ──────────────────────────────────────────────────────────


def _entries(*specs: tuple[str, str, str, float]) -> list[dict]:
    """(line, alert_type, direction, ts) → 条目列表。"""
    from backend.shared.qq_briefing import build_entry

    return [
        build_entry(line=line, alert_type=atype, direction=direction, ts=ts)
        for line, atype, direction, ts in specs
    ]


def test_compose_card_shape_matches_selected_design():
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, compose_briefing

    entries = _entries(
        ("▼ 贵州茅台(600519) 批价下调传闻 · 利空", "news:negative", "down", _NOW - 10),
        ("▲ 宁德时代(300750) 海外储能大单 · 利好", "news:positive", "up", _NOW - 20),
        ("◆ 半导体 出口管制新规落地 · 风险", "news:risk_event", "down", _NOW - 30),
    )
    slot = _slot(BRIEFING_SLOTS[1])  # 午间
    composed = compose_briefing(
        market="CN", market_label="A股", slot=slot, entries=entries, now_ts=_NOW
    )
    assert composed is not None
    title, body = composed
    assert title == "📰 情报速览 · A股 · 午间 · 11:35 · 3条"
    lines = body.split("\n")
    assert lines[0] == "━" * 14
    assert lines[4] == "━" * 14
    # 利空/风险优先：风险 → 利空 → 利好
    assert lines[1] == "◆ 半导体 出口管制新规落地 · 风险"
    assert lines[2] == "▼ 贵州茅台(600519) 批价下调传闻 · 利空"
    assert lines[3] == "▲ 宁德时代(300750) 海外储能大单 · 利好"
    assert lines[5] == "交易台 › 实时情报"


def test_compose_folds_beyond_max_lines():
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, compose_briefing

    entries = _entries(
        *[(f"• 条目{i}", "news:negative", "down", _NOW - i) for i in range(24)]
    )
    composed = compose_briefing(
        market="CN",
        market_label="A股",
        slot=_slot(BRIEFING_SLOTS[1]),
        entries=entries,
        now_ts=_NOW,
    )
    assert composed is not None
    title, body = composed
    assert title.endswith("24条")
    assert body.count("条目") == 15
    assert body.endswith("另有 9 条 · 交易台 › 实时情报")


def test_compose_dedupes_identical_lines_keeping_newest():
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, compose_briefing

    entries = _entries(
        ("▼ 同一行", "news:negative", "down", _NOW - 100),
        ("▼ 同一行", "news:negative", "down", _NOW - 5),
    )
    composed = compose_briefing(
        market="CN",
        market_label="A股",
        slot=_slot(BRIEFING_SLOTS[1]),
        entries=entries,
        now_ts=_NOW,
    )
    assert composed is not None
    title, body = composed
    assert title.endswith("1条")
    assert body.count("同一行") == 1


def test_compose_drops_stale_entries_and_returns_none_when_all_stale():
    from backend.shared.qq_briefing import (
        BRIEFING_MAX_AGE_S,
        BRIEFING_SLOTS,
        _slot,
        compose_briefing,
    )

    stale = _entries(("▼ 旧闻", "news:negative", "down", _NOW - BRIEFING_MAX_AGE_S - 1))
    assert (
        compose_briefing(
            market="CN",
            market_label="A股",
            slot=_slot(BRIEFING_SLOTS[0]),
            entries=stale,
            now_ts=_NOW,
        )
        is None
    )
    mixed = _entries(
        ("▼ 旧闻", "news:negative", "down", _NOW - BRIEFING_MAX_AGE_S - 1),
        ("▲ 新讯", "news:positive", "up", _NOW - 60),
    )
    composed = compose_briefing(
        market="CN",
        market_label="A股",
        slot=_slot(BRIEFING_SLOTS[0]),
        entries=mixed,
        now_ts=_NOW,
    )
    assert composed is not None
    assert "旧闻" not in composed[1]
    assert composed[0].endswith("1条")


def test_compose_non_cn_market_gets_label_in_title():
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, compose_briefing

    entries = _entries(("▲ 腾讯(00700) 回购", "news:positive", "up", _NOW - 5))
    composed = compose_briefing(
        market="HK",
        market_label="港股",
        slot=_slot(BRIEFING_SLOTS[2]),
        entries=entries,
        now_ts=_NOW,
    )
    assert composed is not None
    assert composed[0] == "📰 情报速览 · 港股 · 盘后 · 15:15 · 1条"


# ── 冲刷 ──────────────────────────────────────────────────────────────


def test_flush_market_sends_card_clears_buffer_and_counts(sent):
    from backend.shared.qq_briefing import (
        BRIEFING_SLOTS,
        _slot,
        buffer_key,
        enqueue_briefing,
        flush_market,
    )

    client = FakeRedis()
    enqueue_briefing(
        client, "CN", line="▼ 贵州茅台(600519) 批价下调传闻", alert_type="news:negative"
    )
    enqueue_briefing(
        client, "CN", line="▲ 宁德时代(300750) 大单", alert_type="news:positive"
    )
    sent_count = flush_market(client, "CN", _slot(BRIEFING_SLOTS[1]))
    assert sent_count == 2
    assert len(sent) == 1
    title, body, channel = sent[0]
    assert title.startswith("📰 情报速览 · 午间")
    assert title.endswith("2条")
    assert channel == "default"
    assert buffer_key("CN") in client.deleted
    assert client.lists.get(buffer_key("CN")) in (None, [])


def test_flush_market_cn_card_carries_market_label(sent):
    """2026-10-16 拍板：CN 卡也带市场词（production 经 market_labels 传「A股」）。"""
    from backend.shared.qq_briefing import (
        BRIEFING_SLOTS,
        _slot,
        enqueue_briefing,
        flush_market,
    )

    client = FakeRedis()
    enqueue_briefing(client, "CN", line="▼ 贵州茅台(600519) 批价下调传闻")
    sent_count = flush_market(
        client, "CN", _slot(BRIEFING_SLOTS[1]), market_label="A股"
    )
    assert sent_count == 1
    title = sent[0][0]
    assert title.startswith("📰 情报速览 · A股 · 午间")


def test_flush_market_empty_buffer_returns_zero_and_sends_nothing(sent):
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, flush_market

    assert flush_market(FakeRedis(), "CN", _slot(BRIEFING_SLOTS[0])) == 0
    assert sent == []


def test_flush_market_read_failure_returns_none_and_keeps_buffer():
    """读失败 ≠ 没有：返回 None，调用方不得落 done，缓冲原样保留重试。"""
    from backend.shared.qq_briefing import BRIEFING_SLOTS, _slot, flush_market

    client = FakeRedis(fail_on="lrange")
    client.lists["qm:qq:briefing:cn"] = ["{}"]
    assert flush_market(client, "CN", _slot(BRIEFING_SLOTS[0])) is None
    assert "qm:qq:briefing:cn" not in client.deleted


def test_flush_market_stale_only_clears_without_sending(sent):
    from backend.shared.qq_briefing import (
        BRIEFING_MAX_AGE_S,
        BRIEFING_SLOTS,
        _slot,
        enqueue_briefing,
        flush_market,
    )

    client = FakeRedis()
    enqueue_briefing(
        client,
        "CN",
        line="▼ 周末旧闻",
        alert_type="news:negative",
        ts=_NOW - BRIEFING_MAX_AGE_S - 60,
    )
    assert flush_market(client, "CN", _slot(BRIEFING_SLOTS[0]), now_ts=_NOW) == 0
    assert sent == []


def test_flush_market_hk_uses_market_channel(sent):
    from backend.shared.qq_briefing import (
        BRIEFING_SLOTS,
        _slot,
        enqueue_briefing,
        flush_market,
    )

    client = FakeRedis()
    enqueue_briefing(
        client, "HK", line="▲ 腾讯(00700) 回购", alert_type="news:positive"
    )
    assert (
        flush_market(client, "HK", _slot(BRIEFING_SLOTS[2]), market_label="港股") == 1
    )
    assert sent[0][2] == "hk"


# ── 键空间 ────────────────────────────────────────────────────────────


def test_key_layout():
    from backend.shared.qq_briefing import buffer_key, done_key

    assert buffer_key("CN") == "qm:qq:briefing:cn"
    assert buffer_key("hk") == "qm:qq:briefing:hk"
    assert done_key("CN", "20261012", "noon") == "qm:qq:briefing:done:cn:20261012:noon"


# ── 派发循环（单周期，duty 同款 asyncio.run 直测）──────────────────────


def _cycle_env(monkeypatch: pytest.MonkeyPatch, client: FakeRedis, sent: list):
    """环境装配：get_redis → FakeRedis；flush_market 替身收集 (market, slot)。"""
    import asyncio
    from types import SimpleNamespace

    import backend.services.trade.services.intel_briefing_task as ibt
    import backend.services.trade_shared.deps as deps

    calls: list[tuple[str, object]] = []

    def fake_flush(c, market, slot, **kw):
        calls.append((market, slot))
        return sent.pop(0) if sent else 1

    monkeypatch.setattr(deps, "get_redis", lambda: SimpleNamespace(client=client))
    monkeypatch.setattr(ibt, "flush_market", fake_flush)

    def run_cycle(now):
        return asyncio.run(ibt.run_intel_briefing_cycle(now, True))

    return calls, run_cycle


def test_cycle_flushes_three_markets_and_sets_done(monkeypatch):
    from datetime import datetime

    from backend.shared.qq_briefing import done_key

    client = FakeRedis()
    calls, run_cycle = _cycle_env(monkeypatch, client, sent=[1, 0, 0])
    assert run_cycle(datetime(2026, 10, 12, 11, 35)) == 3
    assert [m for m, _ in calls] == ["CN", "HK", "US"]
    assert client.kv[done_key("CN", "20261012", "noon")] == "1"
    assert client.kv[done_key("HK", "20261012", "noon")] == "1"
    assert client.kv[done_key("US", "20261012", "noon")] == "1"
    # 第二遍：done 键已在，不再冲刷（防重复档期）
    assert run_cycle(datetime(2026, 10, 12, 11, 36)) == 0
    assert len(calls) == 3


def test_cycle_read_failure_keeps_retrying_without_done_key(monkeypatch):
    from datetime import datetime

    from backend.shared.qq_briefing import done_key

    client = FakeRedis()
    # HK 冲刷读失败（None）→ 不落 done；下个周期重试
    calls, run_cycle = _cycle_env(monkeypatch, client, sent=[1, None, 0, 1])
    assert run_cycle(datetime(2026, 10, 12, 11, 35)) == 2  # HK 读失败不计
    assert done_key("HK", "20261012", "noon") not in client.kv
    assert done_key("CN", "20261012", "noon") in client.kv
    assert run_cycle(datetime(2026, 10, 12, 11, 36)) == 1  # HK 重试成功
    assert [m for m, _ in calls] == ["CN", "HK", "US", "HK"]
    assert done_key("HK", "20261012", "noon") in client.kv


def test_cycle_before_first_slot_or_non_trading_day_does_nothing(monkeypatch):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace

    import backend.services.trade.services.intel_briefing_task as ibt
    import backend.services.trade_shared.deps as deps

    client = FakeRedis()
    calls: list = []
    monkeypatch.setattr(deps, "get_redis", lambda: SimpleNamespace(client=client))
    monkeypatch.setattr(ibt, "flush_market", lambda c, m, s, **kw: calls.append(m))
    # 未到第一档：直接返回，不碰 redis
    assert (
        asyncio.run(ibt.run_intel_briefing_cycle(datetime(2026, 10, 12, 8, 0), True))
        == 0
    )
    # 非交易日：到档也不发
    assert (
        asyncio.run(ibt.run_intel_briefing_cycle(datetime(2026, 10, 12, 11, 40), False))
        == 0
    )
    assert calls == []


def test_cycle_exists_failure_skips_market_without_flushing(monkeypatch):
    from datetime import datetime

    client = FakeRedis(fail_on="exists")
    calls, run_cycle = _cycle_env(monkeypatch, client, sent=[1, 1, 1])
    assert run_cycle(datetime(2026, 10, 12, 11, 35)) == 0
    # done 读不到 = 不敢盲冲：整批跳过，绝不重发
    assert calls == []
