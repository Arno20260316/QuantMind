"""挖掘任务失败行的可读性契约：``Process exited with code N`` → 加线索。

2026-10-13 OOM 事故：5 条任务行只写 `Process exited with code -9`，用户完全
看不出是内存打满被内核杀（`-9` = SIGKILL）。这里钉住三条：

1. `-9` 必须抬出 OOM 线索（可操作：少开并行 / 降单任务占用）；
2. 其它负码抬出信号名（如 SIGTERM → `-15`），别把一切负码都说成 OOM；
3. 非负码（正常退出码）不加料——`exit 1` 前面挂一句"被信号终止"是误导。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import launcher as launcher_mod  # noqa: E402
from backend.services.engine.alpha_agent.launcher import (  # noqa: E402
    EvolutionTask,
    TaskStatus,
    _exit_code_hint,
)
from backend.services.engine.mining_plugins import pool_service  # noqa: E402
from backend.tests.test_pool_injection_wiring import _launcher_for  # noqa: E402


def test_sigkill_carries_oom_hint() -> None:
    hint = _exit_code_hint(-9)
    assert "OOM" in hint or "内存" in hint
    assert "并行" in hint, "线索必须可操作（告诉用户下一步做什么）"


def test_other_negative_codes_name_the_signal() -> None:
    import signal

    assert "SIGTERM" in _exit_code_hint(-signal.SIGTERM)
    assert "SIGKILL" not in _exit_code_hint(-signal.SIGTERM), "别把每个负码都说成 OOM"


def test_non_negative_codes_get_no_hint() -> None:
    assert _exit_code_hint(0) == ""
    assert _exit_code_hint(1) == ""


class _ExitingProc:
    """以给定码立即退出的 Popen 桩。"""

    exit_code = 0
    last: _ExitingProc | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.env = kwargs.get("env") or {}
        self.pid = 4242
        self.returncode = type(self).exit_code
        type(self).last = self

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _run_failing_task(tmp_path, monkeypatch, exit_code: int) -> EvolutionTask:
    task = EvolutionTask(
        task_id="t-oom", user_id="u-1", market="a_share", universe="csi300"
    )

    async def fake_prepare(**_kwargs):
        return pool_service.PoolInjection(path=None)

    monkeypatch.setattr(pool_service, "prepare_injection", fake_prepare)

    class _Proc(_ExitingProc):
        pass

    _Proc.exit_code = exit_code
    monkeypatch.setattr(launcher_mod.subprocess, "Popen", _Proc)

    launcher = _launcher_for(task, tmp_path)
    asyncio.run(
        launcher._run_evolution(
            task, loop_n=1, seed="s", provider_uri="/tmp/x", direction=""
        )
    )
    return task


@pytest.mark.parametrize(
    ("exit_code", "marker"),
    [(-9, "内存"), (-15, "SIGTERM")],
)
def test_failed_task_row_explains_the_kill(tmp_path, monkeypatch, exit_code, marker):
    task = _run_failing_task(tmp_path, monkeypatch, exit_code)

    assert task.status == TaskStatus.FAILED
    assert f"Process exited with code {exit_code}" in task.error_message
    assert marker in task.error_message
