"""令牌桶行为测试（假 Redis + 假时钟/睡眠；纯逻辑，不碰真桥/真 Redis）。

要钉的三件事，每一件都对应一个实证过的运行期后果：

1. **准入边界**：限额 N = 每分钟恰好放行 N 次；第 N+1 次必须等到下一分钟。
   （曾有的缺陷：到限后不加计数却让调用方比大小放行——限额从未真正生效。）
2. **自适应**：RATE_LIMITED → 指数退避 5→10→20…60s 封顶 + 有效速率减半
   （下限 30）；成功一次按 base//20 缓慢恢复。降速与恢复都是自愈的。
3. **降级**：Redis 缺席（None）或中途异常 → 放行（预算失效由桥侧 600/min
   兜底）——绝不因状态面把整条采集链报成 error。
"""

from __future__ import annotations

import pytest

from backend.services.live_trading.services.tdx_ingest.rate_budget import (
    DEFAULT_RATE_PER_MIN,
    MIN_RATE_PER_MIN,
    RateBudget,
    rate_per_min_from_env,
    seconds_into_minute,
)


class _FakeRedis:
    """按分钟键计数的同步假 Redis（get / pipeline.incr+expire）。"""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def pipeline(self):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._r = redis
        self._ops: list[tuple] = []

    def incr(self, key):
        self._ops.append(("incr", key))

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "incr":
                self._r.store[op[1]] = self._r.store.get(op[1], 0) + 1
                out.append(self._r.store[op[1]])
            else:
                self._r.expires[op[1]] = op[2]
                out.append(True)
        return out


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _sleepers(clock: _Clock, *, advance: bool):
    """假睡眠：记录时长；``advance=True`` 时把假时钟推着走（跨分钟）。"""
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if advance:
            clock.t += seconds

    return _sleep, sleeps


def _budget(redis, rate: int, clock: _Clock, sleep):
    return RateBudget(redis, rate_per_min=rate, sleep=sleep, clock=clock)


# ============ 准入边界 ============


@pytest.mark.asyncio
async def test_within_limit_all_admitted_no_sleep():
    clock = _Clock()
    sleep, sleeps = _sleepers(clock, advance=False)
    budget = _budget(_FakeRedis(), 3, clock, sleep)

    for _ in range(3):
        await budget.acquire()
    assert sleeps == []
    redis = budget._redis
    assert list(redis.store.values()) == [3], "三次放行 = 计数 3"


@pytest.mark.asyncio
async def test_next_call_after_limit_waits_until_next_minute():
    """第 N+1 次必须等待；假睡眠把时钟推过分钟边界后放行。"""
    redis = _FakeRedis()
    clock = _Clock(1000.0)  # 1000 % 60 = 40 → 距下一分钟 20s
    sleep, sleeps = _sleepers(clock, advance=True)
    budget = _budget(redis, 2, clock, sleep)

    await budget.acquire()
    await budget.acquire()
    assert sleeps == []

    await budget.acquire()  # 第 3 次：限额 2 → 必须睡到下一分钟
    assert len(sleeps) == 1
    assert 20.1 <= sleeps[0] <= 20.5, "睡到下一分钟（+100~400ms 抖动）"
    # 跨分钟后新计数键从 0 起 → 放行落在新键上
    assert len(redis.store) == 2 and min(redis.store.values()) == 1


@pytest.mark.asyncio
async def test_stuck_clock_gives_up_after_three_rounds():
    """时钟/Redis 病态（睡醒仍超限）→ 绕 3 轮后放行一次，由桥侧限流兜底。"""
    redis = _FakeRedis()
    key = "qm:tdx_ingest:rate:197001010000"  # epoch 0 所在分钟
    redis.store[key] = 99
    clock = _Clock(0.0)  # 固定在键对应的分钟里
    sleep, sleeps = _sleepers(clock, advance=False)
    budget = _budget(redis, 5, clock, sleep)

    await budget.acquire()
    assert len(sleeps) == 3, "3 轮防呆后第 4 次循环不再睡，直接放行"
    assert redis.store[key] == 99, "超限时不得再 INCR"


@pytest.mark.asyncio
async def test_denied_call_does_not_increment():
    """到限后的等待不应把计数继续推高（否则计数失去「本分钟已用」语义）。"""
    redis = _FakeRedis()
    clock = _Clock(1000.0)
    sleep, sleeps = _sleepers(clock, advance=False)
    budget = _budget(redis, 1, clock, sleep)

    await budget.acquire()  # 计数 1
    key = next(iter(redis.store))
    assert redis.store[key] == 1
    await budget.acquire()  # 超限 → 3 轮后放行，计数不变
    assert redis.store[key] == 1
    assert len(sleeps) == 3


# ============ 自适应降速 ============


def test_rate_limited_halves_and_backs_off_exponentially():
    clock = _Clock(1000.0)
    sleep, _ = _sleepers(clock, advance=False)
    budget = _budget(_FakeRedis(), 180, clock, sleep)

    budget.on_rate_limited()
    assert budget.effective_rate == 90
    assert budget._penalty_until - clock.t == pytest.approx(5.0)

    budget.on_rate_limited()
    assert budget.effective_rate == 45
    assert budget._penalty_until - clock.t == pytest.approx(10.0)

    budget.on_rate_limited()
    assert budget.effective_rate == 30, "减半到 30 为止"
    assert budget._penalty_until - clock.t == pytest.approx(20.0)

    budget.on_rate_limited()
    assert budget.effective_rate == 30, "下限不再减"
    assert budget._penalty_until - clock.t == pytest.approx(40.0)

    budget.on_rate_limited()
    assert budget._penalty_until - clock.t == pytest.approx(60.0), "退避 60s 封顶"


def test_success_recovers_gradually_then_caps_at_base():
    clock = _Clock()
    sleep, _ = _sleepers(clock, advance=False)
    budget = _budget(_FakeRedis(), 180, clock, sleep)

    budget.on_rate_limited()
    assert budget.effective_rate == 90
    budget.on_success()
    assert budget.effective_rate == 99, "恢复步长 = base//20 = 9"

    for _ in range(50):
        budget.on_success()
    assert budget.effective_rate == 180 == budget.base_rate, "封顶在基准"


def test_success_clears_limited_streak():
    clock = _Clock()
    sleep, _ = _sleepers(clock, advance=False)
    budget = _budget(_FakeRedis(), 180, clock, sleep)

    budget.on_rate_limited()
    budget.on_success()
    budget.on_rate_limited()
    assert budget._penalty_until - clock.t == pytest.approx(5.0), "连击已清零"


@pytest.mark.asyncio
async def test_acquire_waits_out_penalty_window():
    redis = _FakeRedis()
    clock = _Clock(1000.0)
    sleep, sleeps = _sleepers(clock, advance=True)
    budget = _budget(redis, 180, clock, sleep)

    budget.on_rate_limited()  # penalty 5s
    await budget.acquire()
    assert sleeps and sleeps[0] == pytest.approx(5.0)
    assert redis.store, "退避结束后正常走计数路径"


# ============ 降级路径 ============


@pytest.mark.asyncio
async def test_redis_none_is_pass_through():
    """trade 启动期拿不到 Redis：预算缺席 → 放行（桥侧限流兜底）。"""
    clock = _Clock()
    sleep, sleeps = _sleepers(clock, advance=False)
    budget = _budget(None, 3, clock, sleep)

    for _ in range(10):
        await budget.acquire()
    assert sleeps == []


@pytest.mark.asyncio
async def test_redis_exception_fails_open():
    """Redis 中途挂掉（连接错）→ 放行一次并告警，不把采集报成 error。"""

    class _Broken:
        def get(self, key):
            raise ConnectionError("redis down")

    clock = _Clock()
    sleep, sleeps = _sleepers(clock, advance=False)
    budget = _budget(_Broken(), 3, clock, sleep)

    await budget.acquire()  # 不抛
    assert sleeps == []


# ============ env 与纯函数 ============


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_RATE_PER_MIN),
        ("", DEFAULT_RATE_PER_MIN),
        ("abc", DEFAULT_RATE_PER_MIN),
        ("180", 180),
        ("30", 30),
        ("0", MIN_RATE_PER_MIN),  # 低于下限 → 收敛到 30
        ("5", MIN_RATE_PER_MIN),
        ("9999", 600),  # 高于 600（桥上限）→ 收敛到 600
    ],
)
def test_rate_per_min_from_env_clamps(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("TDX_INGEST_RATE_PER_MIN", raising=False)
    else:
        monkeypatch.setenv("TDX_INGEST_RATE_PER_MIN", raw)
    assert rate_per_min_from_env() == expected


def test_seconds_into_minute():
    assert seconds_into_minute(1000.0) == pytest.approx(40.0)
    assert seconds_into_minute(60.0) == pytest.approx(0.0)
