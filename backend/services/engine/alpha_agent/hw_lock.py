"""因子挖掘硬件门槛：低于 8 核 / 32GB 直接拒绝，避免 RD-Agent 把整机拖死。

**两把不同的尺子**（2026-10-13 OOM 事故后补第二把）：

- :func:`probe_hardware` / :func:`assert_factor_mining_hardware` 量的是**总量**，
  回答「这台机器够不够格跑挖掘」——开机时判一次，与当前负载无关；
- :func:`advisory_running_capacity` 量的是**可用量**，回答「**现在**能同时跑几路
  RD-Agent」——每次读并发上限时判（内存吃紧就往下降，最低 1 路串行）。

事故复盘：总量尺子对 64.9GB 的机器一路绿灯（远超 32GB 门槛），但 4 路并发时
每个 RD-Agent 子进程 anon-rss 撑到 20–30GB，内核 OOM Killer 挨个 SIGKILL
（子进程退出码 -9，任务行显示 `Process exited with code -9`），同时 swap 被吃满
→ 整机「假卡死」。总量尺子看不见这件事，可用量尺子看得见。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MIN_CPU_CORES = 8
DEFAULT_MIN_MEMORY_GB = 32.0
_UNLIMITED_CGROUP_MEMORY = 1 << 62

#: 内存充裕时的挖掘并行路数（用户口径：2 路并挖）。这就是「正常态」上限。
DEFAULT_ADVISORY_RUNNING_CAPACITY = 2

#: 单个挖掘子进程的经验占用（GB）。实测峰值 anon-rss 20–30GB（一路跑到底的上界），
#: 但 RD-Agent 大部分时间在等 LLM，平均占用低得多。准入判据用 14GB 是**能起几路**
#: 的口径，不是单路的上界——上界由并行路数本身兜。
DEFAULT_MINING_TASK_MEMORY_GB = 14.0

#: 留给非挖掘部分的内存（GB）：PG / Redis / engine / celery / 桌面 + 页缓存。
#: 可用内存低于 `路数 × 任务占用 + 预留` 时下调并行路数（现役任务继续跑完）。
DEFAULT_MINING_MEMORY_RESERVE_GB = 10.0

ENV_MINING_TASK_MEMORY_GB = "ALPHA_AGENT_TASK_MEMORY_GB"
ENV_MINING_MEMORY_RESERVE_GB = "ALPHA_AGENT_MEMORY_RESERVE_GB"
ENV_SKIP_MEMORY_GATE = "ALPHA_AGENT_SKIP_MEMORY_GATE"

#: 「不缩减」哨兵：探测不到可用内存 / 显式关闸时返回它，并发上限原样生效。
UNCLAMPED_ADVISORY_CAPACITY = 1 << 30


class HardwareLockError(RuntimeError):
    """当前机器不满足因子挖掘最低硬件要求。"""


@dataclass(frozen=True)
class HardwareProbe:
    cpu_cores: int
    memory_bytes: int
    min_cpu_cores: int
    min_memory_gb: float
    ok: bool
    message: str

    @property
    def memory_gb(self) -> float:
        return self.memory_bytes / 1_000_000_000


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _cgroup_cpu_cores() -> float | None:
    raw = _read_text("/sys/fs/cgroup/cpu.max")
    if raw:
        parts = raw.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                return int(parts[0]) / int(parts[1])
            except (TypeError, ValueError, ZeroDivisionError):
                return None
    quota = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and period:
        try:
            q, p = int(quota), int(period)
            if q > 0 and p > 0:
                return q / p
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def _cgroup_memory_bytes() -> int | None:
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        raw = _read_text(path)
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < _UNLIMITED_CGROUP_MEMORY:
            return value
    return None


def _host_cpu_cores() -> int:
    try:
        import psutil

        n = psutil.cpu_count(logical=True)
        if n:
            return int(n)
    except Exception:
        pass
    return int(os.cpu_count() or 1)


def _host_memory_bytes() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:
        pass
    raw = _read_text("/proc/meminfo")
    if raw:
        for line in raw.splitlines():
            if line.startswith("MemTotal:"):
                try:
                    return int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    break
    return 0


def _host_available_bytes() -> int:
    """当前可用内存字节数（含可回收页缓存）；探测不到返回 0。

    与 :func:`_host_memory_bytes`（总量）是两个量：这里读的是 ``available`` /
    ``MemAvailable``，OOM 前它会先掉下来，总量纹丝不动。
    """
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        pass
    raw = _read_text("/proc/meminfo")
    if raw:
        for line in raw.splitlines():
            if line.startswith("MemAvailable:"):
                try:
                    return int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    break
    return 0


def _cgroup_memory_available_bytes() -> int | None:
    """cgroup 内还剩下多少配额（无限制/读不到 → None）。"""
    limit = _cgroup_memory_bytes()
    if limit is None:
        return None
    for path in (
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    ):
        raw = _read_text(path)
        if not raw:
            continue
        try:
            used = int(raw)
        except ValueError:
            continue
        return max(0, limit - used)
    return None


def probe_available_memory_gb() -> float:
    """当前可用内存（GB）：宿主可用与 cgroup 剩余取更严者；探测不到 → 0.0。"""
    available = _host_available_bytes()
    cgroup_available = _cgroup_memory_available_bytes()
    if cgroup_available is not None:
        available = (
            min(available, cgroup_available) if available > 0 else cgroup_available
        )
    return available / 1_000_000_000


def _task_memory_gb() -> float:
    try:
        return max(
            1.0,
            float(os.getenv(ENV_MINING_TASK_MEMORY_GB, DEFAULT_MINING_TASK_MEMORY_GB)),
        )
    except (TypeError, ValueError):
        return DEFAULT_MINING_TASK_MEMORY_GB


def _memory_reserve_gb() -> float:
    try:
        return max(
            0.0,
            float(
                os.getenv(
                    ENV_MINING_MEMORY_RESERVE_GB, DEFAULT_MINING_MEMORY_RESERVE_GB
                )
            ),
        )
    except (TypeError, ValueError):
        return DEFAULT_MINING_MEMORY_RESERVE_GB


def advisory_running_capacity(desired: int) -> tuple[int, str]:
    """按**可用内存**建议当前能并行几路挖掘，返回 ``(路数, 理由)``。

    规则（严于 ``desired`` 时才缩，绝不放大——上限由 env/默认值说了算）：

    - ``可用 >= desired × 单任务 + 预留`` → ``desired``（正常态：2 路并挖）；
    - ``可用 >= 1 × 单任务 + 预留``     → 降到能塞下的路数（通常 1）；
    - 更低                              → 仍是 **1**——「内存不足就按 1 个挖」，
      不是「一个都不挖」：现役任务跑完后串行推进，而不是把队列冻死。

    探测不到可用内存 / 显式关闸 → :data:`UNCLAMPED_ADVISORY_CAPACITY`（不缩）。
    """
    desired = max(1, int(desired))
    if _env_flag(ENV_SKIP_MEMORY_GATE):
        return UNCLAMPED_ADVISORY_CAPACITY, f"内存闸已跳过（{ENV_SKIP_MEMORY_GATE}）"
    available_gb = probe_available_memory_gb()
    if available_gb <= 0:
        return UNCLAMPED_ADVISORY_CAPACITY, "可用内存探测不到，按配置上限放行"
    task_gb = _task_memory_gb()
    reserve_gb = _memory_reserve_gb()
    for lanes in range(desired, 0, -1):
        need_gb = lanes * task_gb + reserve_gb
        if available_gb >= need_gb:
            reason = (
                f"可用内存 {available_gb:.1f}GB ≥ {lanes}×{task_gb:.0f}GB + "
                f"预留 {reserve_gb:.0f}GB"
            )
            return lanes, reason
    return (
        1,
        f"可用内存偏低（{available_gb:.1f}GB < {task_gb:.0f}GB + 预留 {reserve_gb:.0f}GB）："
        "按 1 路串行挖掘",
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _min_cpu_cores() -> int:
    try:
        return max(
            1, int(os.getenv("ALPHA_AGENT_MIN_CPU_CORES", DEFAULT_MIN_CPU_CORES))
        )
    except (TypeError, ValueError):
        return DEFAULT_MIN_CPU_CORES


def _min_memory_gb() -> float:
    try:
        return max(
            1.0, float(os.getenv("ALPHA_AGENT_MIN_MEMORY_GB", DEFAULT_MIN_MEMORY_GB))
        )
    except (TypeError, ValueError):
        return DEFAULT_MIN_MEMORY_GB


def probe_hardware() -> HardwareProbe:
    """采集可见 CPU/内存（宿主机与 cgroup 限额取更严者）。"""
    cpu = _host_cpu_cores()
    cgroup_cpu = _cgroup_cpu_cores()
    if cgroup_cpu is not None:
        cpu = max(1, min(cpu, math.floor(cgroup_cpu)))

    memory = _host_memory_bytes()
    cgroup_mem = _cgroup_memory_bytes()
    if cgroup_mem is not None:
        memory = min(memory, cgroup_mem) if memory > 0 else cgroup_mem

    min_cpu = _min_cpu_cores()
    min_mem_gb = _min_memory_gb()
    memory_gb = memory / 1_000_000_000
    skipped = _env_flag("ALPHA_AGENT_SKIP_HW_LOCK")
    ok = skipped or (cpu >= min_cpu and memory_gb >= min_mem_gb)
    if skipped:
        message = f"硬件锁已跳过（ALPHA_AGENT_SKIP_HW_LOCK）：当前 {cpu} 核 / {memory_gb:.1f} GB"
    elif ok:
        message = f"硬件满足因子挖掘门槛：{cpu} 核 / {memory_gb:.1f} GB（要求 {min_cpu} 核 / {min_mem_gb:.0f} GB）"
    else:
        message = (
            f"因子挖掘已中止：当前机器 {cpu} 核 / {memory_gb:.1f} GB，"
            f"低于最低门槛 {min_cpu} 核 / {min_mem_gb:.0f} GB。"
            "继续运行会导致整机卡死。"
        )
    return HardwareProbe(
        cpu_cores=cpu,
        memory_bytes=memory,
        min_cpu_cores=min_cpu,
        min_memory_gb=min_mem_gb,
        ok=ok,
        message=message,
    )


def assert_factor_mining_hardware() -> HardwareProbe:
    """不满足门槛则抛 HardwareLockError，满足则返回探测结果。"""
    probe = probe_hardware()
    if probe.ok:
        logger.info("[alpha-agent] %s", probe.message)
        return probe
    logger.error("[alpha-agent] %s", probe.message)
    raise HardwareLockError(probe.message)
