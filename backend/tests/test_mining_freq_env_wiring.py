"""挖掘频率与任务指纹的任务级 env 接线（移植 P1.5 / P3）：launcher → 子进程。

防的是**接线静默断**：频率标记（``QM_MINING_FREQ``）与窗口是**两条独立注入**
路径（窗口走 ``task_env``，频率单独一条）。任何一条断了都不报错，只表现为
「按日线把分钟任务跑完了」——产出一批自称分钟因子的东西，正是 P1.5 要消灭的
失败态。所以逐条钉：

1. 分钟任务的 Popen env 带 ``QM_MINING_FREQ=1min`` + ``QM_MINING_BAR_SECONDS=60``
   + 显式窗口。bar 秒数是 fork 评估器输出粒度分派的**唯一读数**——漏发时评估器
   按日频裁判分钟输出，与提示词面互补振荡（2026-10-16 实证的零产出病根），
   只烧 token 不报错；
2. 日线任务**不带**该键（未指定 = 日线，任务不留参数痕迹）；
3. 分钟任务即使没有窗口也必须带频率标记 + bar 秒数（子进程由 wrapper 响亮
   失败，而不是悄悄按日线跑）；
4. 任务指纹（``QM_MINING_TASK_FINGERPRINT``）有则原样注入、空则缺席——统计派
   落库身份靠它（P3），注入断了表现为「同配置重跑不幂等、每次新行」，库面
   自查不出来。

Popen 桩复用 ``test_pool_injection_wiring`` 的 harness（立即退出 0、记下 env）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import launcher as launcher_mod  # noqa: E402
from backend.services.engine.alpha_agent.launcher import (  # noqa: E402
    AlphaAgentLauncher,
    EvolutionTask,
    TaskStatus,
)
from backend.services.engine.mining_plugins import pool_service  # noqa: E402
from backend.tests.test_pool_injection_wiring import (  # noqa: E402
    _FakeProc,
    _launcher_for,
)


def _run(tmp_path, monkeypatch, task: EvolutionTask) -> dict:
    """驱动一轮 ``_run_evolution``（Popen 被桩替换），返回子进程 env。"""

    async def fake_prepare(**kwargs):
        return pool_service.PoolInjection(path=None)

    monkeypatch.setattr(pool_service, "prepare_injection", fake_prepare)
    monkeypatch.setattr(launcher_mod.subprocess, "Popen", _FakeProc)

    launcher = _launcher_for(task, tmp_path)
    asyncio.run(
        launcher._run_evolution(
            task, loop_n=1, seed="s", provider_uri="/tmp/x", direction=""
        )
    )
    assert _FakeProc.last is not None
    return _FakeProc.last.env


def _task(**overrides) -> EvolutionTask:
    base: dict = {
        "task_id": "t-freq",
        "user_id": "u-1",
        "market": "a_share",
        "universe": "csi300",
    }
    base.update(overrides)
    return EvolutionTask(**base)


def test_minute_task_env_carries_freq_and_window(tmp_path, monkeypatch):
    task = _task(
        freq="1min",
        mining_start="2026-03-04",
        mining_end="2026-08-25",
    )
    env = _run(tmp_path, monkeypatch, task)

    assert task.status == TaskStatus.COMPLETED
    assert env["QM_MINING_FREQ"] == "1min"
    assert env["QM_MINING_BAR_SECONDS"] == "60"
    assert env["QM_MINING_WINDOW_START"] == "2026-03-04"
    assert env["QM_MINING_WINDOW_END"] == "2026-08-25"
    # 分钟任务不落数据集：键缺席（wrapper 因此不会报"忽略数据集选择"）
    assert "QM_MINING_DATASETS" not in env
    # 分段照日线契约注入（qlib 执行器仍按 day 频跑）
    assert env["QM_QLIB_TEST_END"] == "2026-08-25"


def test_day_task_env_has_no_freq_key(tmp_path, monkeypatch):
    env = _run(tmp_path, monkeypatch, _task())
    assert "QM_MINING_FREQ" not in env
    assert "QM_MINING_BAR_SECONDS" not in env


def test_day_task_with_window_has_no_bar_seconds(tmp_path, monkeypatch):
    """日线任务带自定义窗口也不发频率键（不变量 A：日线路径零痕迹）。"""
    env = _run(
        tmp_path,
        monkeypatch,
        _task(mining_start="2024-01-02", mining_end="2024-06-28"),
    )
    assert env["QM_MINING_WINDOW_START"] == "2024-01-02"
    assert "QM_MINING_FREQ" not in env
    assert "QM_MINING_BAR_SECONDS" not in env


def test_minute_task_without_window_still_carries_freq(tmp_path, monkeypatch):
    """标记必须先到：缺窗口由子进程响亮失败，绝不静默按日线跑完。"""
    env = _run(tmp_path, monkeypatch, _task(freq="1min"))
    assert env["QM_MINING_FREQ"] == "1min"
    assert env["QM_MINING_BAR_SECONDS"] == "60"
    assert "QM_MINING_WINDOW_START" not in env


def test_task_fingerprint_injected_verbatim(tmp_path, monkeypatch):
    """有指纹就原样注入（不重算不改写）——runner 落库身份只认这一个来源。"""
    env = _run(tmp_path, monkeypatch, _task(fingerprint="fp-abc123"))
    assert env["QM_MINING_TASK_FINGERPRINT"] == "fp-abc123"


def test_empty_fingerprint_absent_not_empty_string(tmp_path, monkeypatch):
    """空指纹**键缺席**（不是空串）：runner 因此退回任务级身份；注入空串会把
    不同任务的身份哈希撞成一个（stable_factor_id 对空串走 task 分支，但显式
    空值在 metadata 里冒充「有指纹」）。"""
    env = _run(tmp_path, monkeypatch, _task())
    assert "QM_MINING_TASK_FINGERPRINT" not in env
