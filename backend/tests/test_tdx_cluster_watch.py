"""TdxAiData 订阅集群守护（tdx_cluster_watch，2026-10-14）测试。

背景：分片 worker 是独立子进程，此前**没有任何定时任务负责拉起**——容器重建 /
worker 被杀 / SDK 段错误后订阅静默不在，直到有人手动点面板「连通性自检」。
本任务按周期体检集群、缺片即拉起（节流防风暴），并写调度心跳供 C07 判活。

覆盖：
1. U：decide_action 纯函数（up→ok / 缺片→ensure / 节流窗内→wait / 超窗→ensure）；
2. U：常驻循环——缺片拉起一次后状态翻 up 不再重复；心跳每轮写入；
3. U：订阅关/通道关时空转不拉起（心跳照写——体检 C07 靠它区分 off 与 missing）。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.unit
def test_decide_action_pure():
    from backend.services.live_trading.services import tdx_cluster_watch as watch

    now = 1000.0
    # worker 正常 → 不动作
    assert watch.decide_action("up", now=now, last_ensure_ts=None) == "ok"
    # 缺片（down/degraded）且从未拉起 → 拉起
    assert watch.decide_action("down", now=now, last_ensure_ts=None) == "ensure"
    assert watch.decide_action("degraded", now=now, last_ensure_ts=None) == "ensure"
    # 节流窗内 → 等待（worker 持续崩溃时避免拉起风暴把崩溃现场刷没）
    assert watch.decide_action("down", now=now, last_ensure_ts=now - 10.0) == "wait"
    # 超出节流窗 → 再拉起
    old = now - watch.MIN_ENSURE_GAP_S - 1.0
    assert watch.decide_action("down", now=now, last_ensure_ts=old) == "ensure"


class _FakeCluster:
    """台账式假集群：ensure_all 把状态翻成 up（模拟拉起成功）。"""

    def __init__(self, *, initial: str = "down", shards_up: int = 0):
        self.worker = initial
        self.shards_up = shards_up
        self.ensures = 0

    async def status(self, try_start: bool = False) -> dict:
        assert try_start is False, "守护体检必须无副作用（try_start=False）"
        return {"worker": self.worker, "shards_up": self.shards_up}

    async def ensure_all(self) -> dict:
        self.ensures += 1
        self.worker = "up"
        self.shards_up = 8
        return dict.fromkeys(range(8), True)


async def _run_loop(
    monkeypatch, *, fake, enabled=True, subscription=True, seconds=0.08
):
    """跑一小段常驻循环：记录心跳调用，返回 (hb_keys, cluster)。"""
    from backend.services.live_trading.services import tdx_cluster_watch as watch
    from backend.shared import scheduler_registry

    hb_keys: list[str] = []
    monkeypatch.setattr(
        scheduler_registry, "heartbeat", lambda key, **kw: hb_keys.append(key)
    )
    monkeypatch.setattr(watch.config, "is_enabled", lambda: enabled)
    monkeypatch.setattr(watch.config, "subscription_enabled", lambda: subscription)
    monkeypatch.setattr(watch, "default_cluster", lambda: fake)
    monkeypatch.setattr(watch, "WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(watch, "IDLE_SLEEP_S", 0.01)
    # 状态字典是模块级（进程内累计）——清零后再断言本用例增量
    for key in ("checks", "ensures", "last_worker", "last_error"):
        watch.tdx_cluster_watch_status[key] = (
            0 if isinstance(watch.tdx_cluster_watch_status[key], int) else None
        )

    task = asyncio.create_task(watch.run_tdx_cluster_watch_task())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return hb_keys


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watch_loop_ensures_once_then_ok(monkeypatch):
    """缺片 → 拉起一次 → 状态翻 up 后不再重复拉起；心跳每轮写入。"""
    fake = _FakeCluster(initial="down", shards_up=0)
    hb_keys = await _run_loop(monkeypatch, fake=fake)

    assert fake.ensures == 1, "缺片只拉起一次（拉起成功后不得重复 ensure）"
    assert hb_keys, "每轮必须写调度心跳（C07 判活的唯一信号）"
    assert all(k == "tdx_cluster_watch" for k in hb_keys)

    from backend.services.live_trading.services import tdx_cluster_watch as watch

    st = watch.tdx_cluster_watch_status
    assert st["checks"] >= 2, "拉起后至少再体检过一次"
    assert st["ensures"] == 1
    assert st["last_worker"] == "up"
    assert st["last_shards_up"] == 8
    assert st["last_error"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_watch_loop_idle_when_subscription_disabled(monkeypatch):
    """订阅关（或通道关）→ 不体检不拉起，心跳照写（C07 依此判 off 而非 missing）。"""
    fake = _FakeCluster(initial="down")
    hb_keys = await _run_loop(monkeypatch, fake=fake, subscription=False)

    assert fake.ensures == 0, "订阅关闭时绝不拉起 worker"
    assert hb_keys, "空转也必须写心跳"
    assert all(k == "tdx_cluster_watch" for k in hb_keys)

    from backend.services.live_trading.services import tdx_cluster_watch as watch

    assert watch.tdx_cluster_watch_status["last_worker"] == "off"
