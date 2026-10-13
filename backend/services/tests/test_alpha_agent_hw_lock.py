import pytest

from backend.services.engine.alpha_agent.hw_lock import (
    DEFAULT_MINING_MEMORY_RESERVE_GB,
    DEFAULT_MINING_TASK_MEMORY_GB,
    HardwareLockError,
    UNCLAMPED_ADVISORY_CAPACITY,
    advisory_running_capacity,
    assert_factor_mining_hardware,
    probe_available_memory_gb,
    probe_hardware,
)


def test_probe_fails_when_below_cpu_or_memory(monkeypatch):
    monkeypatch.delenv("ALPHA_AGENT_SKIP_HW_LOCK", raising=False)
    monkeypatch.setenv("ALPHA_AGENT_MIN_CPU_CORES", "8")
    monkeypatch.setenv("ALPHA_AGENT_MIN_MEMORY_GB", "32")
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_cpu_cores", lambda: 4
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_memory_bytes",
        lambda: 16_000_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_cpu_cores", lambda: None
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_bytes", lambda: None
    )

    probe = probe_hardware()
    assert probe.ok is False
    assert "4 核" in probe.message
    assert "16.0 GB" in probe.message
    with pytest.raises(HardwareLockError, match="低于最低门槛"):
        assert_factor_mining_hardware()


def test_probe_passes_8_core_32gb_machine(monkeypatch):
    monkeypatch.delenv("ALPHA_AGENT_SKIP_HW_LOCK", raising=False)
    monkeypatch.setenv("ALPHA_AGENT_MIN_CPU_CORES", "8")
    monkeypatch.setenv("ALPHA_AGENT_MIN_MEMORY_GB", "32")
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_cpu_cores", lambda: 8
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_memory_bytes",
        lambda: 32_000_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_cpu_cores", lambda: None
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_bytes", lambda: None
    )

    probe = assert_factor_mining_hardware()
    assert probe.ok is True
    assert probe.cpu_cores == 8


def test_cgroup_limit_is_stricter_than_host(monkeypatch):
    monkeypatch.delenv("ALPHA_AGENT_SKIP_HW_LOCK", raising=False)
    monkeypatch.setenv("ALPHA_AGENT_MIN_CPU_CORES", "8")
    monkeypatch.setenv("ALPHA_AGENT_MIN_MEMORY_GB", "32")
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_cpu_cores", lambda: 16
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_memory_bytes",
        lambda: 64_000_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_cpu_cores", lambda: 4.0
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_bytes",
        lambda: 8_000_000_000,
    )

    probe = probe_hardware()
    assert probe.cpu_cores == 4
    assert probe.memory_bytes == 8_000_000_000
    assert probe.ok is False


def test_skip_env_bypasses_lock(monkeypatch):
    monkeypatch.setenv("ALPHA_AGENT_SKIP_HW_LOCK", "1")
    monkeypatch.setenv("ALPHA_AGENT_MIN_CPU_CORES", "8")
    monkeypatch.setenv("ALPHA_AGENT_MIN_MEMORY_GB", "32")
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_cpu_cores", lambda: 2
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_memory_bytes",
        lambda: 4_000_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_cpu_cores", lambda: None
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_bytes", lambda: None
    )

    probe = assert_factor_mining_hardware()
    assert probe.ok is True
    assert "已跳过" in probe.message


# ── 可用内存闸（第二把尺子）：2026-10-13 OOM 事故 ──────────────────────
#
# 总闸（上面几个用例）管「这台机器够不够格」；下面这些管「现在还能同时跑几路」。
# 事故：总量尺子对 64.9GB 机器一路绿灯，4 路并发时每路子进程 anon-rss 撑到
# 20–30GB → 内核 OOM Killer 挨个 SIGKILL（`Process exited with code -9`）。


def _stub_available(monkeypatch, gb: float) -> None:
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_available_bytes",
        lambda: int(gb * 1_000_000_000),
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_available_bytes",
        lambda: None,
    )
    monkeypatch.delenv("ALPHA_AGENT_MIN_CPU_CORES", raising=False)
    monkeypatch.delenv("ALPHA_AGENT_MIN_MEMORY_GB", raising=False)


def test_probe_available_memory_reads_available_not_total(monkeypatch) -> None:
    """可用量尺子读 available/MemAvailable，不是 total——OOM 前只有它会掉。"""
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_available_bytes",
        lambda: 7_500_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_available_bytes",
        lambda: None,
    )
    assert probe_available_memory_gb() == 7.5


def test_probe_available_memory_takes_stricter_cgroup(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._host_available_bytes",
        lambda: 40_000_000_000,
    )
    monkeypatch.setattr(
        "backend.services.engine.alpha_agent.hw_lock._cgroup_memory_available_bytes",
        lambda: 6_000_000_000,
    )
    assert probe_available_memory_gb() == 6.0


def test_advisory_capacity_two_lanes_when_memory_plentiful(monkeypatch) -> None:
    """正常态（用户口径）：内存够 → 2 路并挖，绝不放大到配置值以上。"""
    _stub_available(monkeypatch, 60.0)
    monkeypatch.delenv("ALPHA_AGENT_SKIP_MEMORY_GATE", raising=False)
    assert DEFAULT_MINING_TASK_MEMORY_GB * 2 + DEFAULT_MINING_MEMORY_RESERVE_GB <= 60.0

    lanes, reason = advisory_running_capacity(2)
    assert lanes == 2 and "≥ 2×" in reason

    # 配置只给 1 路时不许被放大
    assert advisory_running_capacity(1)[0] == 1


def test_advisory_capacity_falls_back_to_one_lane(monkeypatch) -> None:
    """内存不足 → 1 路串行（不是拒绝提交，也不是 0）。"""
    monkeypatch.delenv("ALPHA_AGENT_SKIP_MEMORY_GATE", raising=False)
    _stub_available(monkeypatch, 25.0)  # 2×14+10=38 > 25，1×14+10=24 ≤ 25
    lanes, reason = advisory_running_capacity(4)
    assert lanes == 1 and "1×" in reason

    _stub_available(monkeypatch, 2.0)  # 连一路都不够 → 仍是 1 路串行
    lanes, reason = advisory_running_capacity(4)
    assert lanes == 1 and "按 1 路串行" in reason


def test_advisory_capacity_skip_and_unknown_memory(monkeypatch) -> None:
    _stub_available(monkeypatch, 1.0)
    monkeypatch.setenv("ALPHA_AGENT_SKIP_MEMORY_GATE", "1")
    assert advisory_running_capacity(4)[0] == UNCLAMPED_ADVISORY_CAPACITY

    monkeypatch.delenv("ALPHA_AGENT_SKIP_MEMORY_GATE", raising=False)
    _stub_available(monkeypatch, 0.0)  # 探测不到 → 不缩
    assert advisory_running_capacity(4)[0] == UNCLAMPED_ADVISORY_CAPACITY


def test_advisory_capacity_thresholds_are_env_tunable(monkeypatch) -> None:
    monkeypatch.delenv("ALPHA_AGENT_SKIP_MEMORY_GATE", raising=False)
    _stub_available(monkeypatch, 25.0)
    assert advisory_running_capacity(2)[0] == 1

    monkeypatch.setenv("ALPHA_AGENT_TASK_MEMORY_GB", "8")
    monkeypatch.setenv("ALPHA_AGENT_MEMORY_RESERVE_GB", "2")
    assert advisory_running_capacity(2)[0] == 2, "2×8 + 2 = 18 ≤ 25"
