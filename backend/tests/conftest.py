"""引擎域单元测试的公共隔离。

**可用内存闸的默认替身**（2026-10-13）：``launcher.running_capacity()`` 现在会按
**当前可用内存**收敛并发路数（内存吃紧 → 1 路串行，见
``hw_lock.advisory_running_capacity``）。若让单测读真机 ``/proc/meminfo``，同一份
代码在开发机（内存宽裕）与 CI（内存窄）上会得到不同并发上限，断言随宿主漂移——
单测必须在任何机器上都确定性通过。

所以这里默认把可用内存钉成「宽裕」，让容量断言只反映**配置**。要测收敛行为
本身的用例，自行 ``monkeypatch.setattr(hw_lock, "probe_available_memory_gb", ...)``
覆盖这个替身（用例级 patch 在 autouse fixture 之后生效）。
"""

from __future__ import annotations

import pytest

#: 「内存宽裕」替身值（GB）：大于任何用例配置的 路数 × 单任务 + 预留。
_PLENTIFUL_MEMORY_GB = 512.0


@pytest.fixture(autouse=True)
def _stub_available_memory(monkeypatch):
    from backend.services.engine.alpha_agent import hw_lock

    monkeypatch.setattr(
        hw_lock, "probe_available_memory_gb", lambda: _PLENTIFUL_MEMORY_GB
    )
