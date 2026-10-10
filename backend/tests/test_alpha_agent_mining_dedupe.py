"""挖掘任务去重（移植 P3）——同配置活跃任务复用，绝不双开。

用户的坑（每条都真金白银）：双击「开始挖掘」、批量派发里重了方向、重启后
再点一次，都会再建一个**跑不出任何新东西**的同参任务——LLM token、GPU 位、
子进程名额全烧两份。本文件锁住去重契约：

1. **命中即复用**：同用户 × 同指纹的活跃任务（pending/queued/running）直接
   复用既有 task_id，不新建、不排队、不占名额（批量路径回执
   ``deduplicated=True``，status/位次是既有任务的实况）。
2. **终态不拦**：跑完/失败/取消后同配置重跑是正当行为（复盘、数据更新后重挖）。
3. **指纹只含「跑什么」**：方向文本、窗口、数据集、算法、频率进指纹；抽样的
   seed/权重（``direction_meta``）与 LLM 凭据（``llm_overrides``）不进——
   重抽一次抽样、换个 API Key 都不是「新任务」。
4. **重启后仍去重**：指纹随 ``task_state.json`` 落盘，否则「重启一次 → 同配置
   再点一下」就是双开。
5. **竞态兜底**：任务行部分唯一索引撞行 → 撤回本次提交、复用内存里的赢家；
   赢家不在内存（重启前的孤儿行）或本次已被排水启动 → **不撤**（fail-open，
   如实告警，绝不把死任务回执给用户、也绝不杀活任务）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import launcher as launcher_module  # noqa: E402
from backend.services.engine.alpha_agent.algorithm_registry import (  # noqa: E402
    load_algorithm,
    normalize_algorithm,
)
from backend.services.engine.alpha_agent.launcher import (  # noqa: E402
    AlphaAgentLauncher,
    EvolutionTask,
    TaskStatus,
)
from backend.services.engine.alpha_agent.task_store import (  # noqa: E402
    DuplicateActiveTask,
)

#: 假数据版本快照（真实现见 test_data_snapshot；这里只验接线与 JSON 落列）。
_STUB_VERSIONS = {
    "snapshot_at": "2026-01-02T00:00:00Z",
    "market": "a_share",
    "freq": "day",
    "provider": None,
    "datasets": {},
    "minute": None,
    "note": "stub",
    "errors": [],
}


class _RecordingStore:
    """记录调用参数的假 store；``duplicate_id`` 非空时模拟撞去重唯一索引。"""

    def __init__(self, *, duplicate_id: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.duplicate_id = duplicate_id

    async def create_task(self, **kwargs) -> None:
        self.calls.append(("create", kwargs))
        if self.duplicate_id:
            raise DuplicateActiveTask(self.duplicate_id)

    async def update_progress(self, task_id: str, **kwargs) -> None:
        self.calls.append(("progress", task_id, kwargs))

    async def mark_terminal(self, task_id: str, **kwargs) -> None:
        self.calls.append(("terminal", task_id, kwargs))

    async def count_factors(self, task_id: str) -> int:
        self.calls.append(("count", task_id))
        return 0

    def creates(self) -> list[dict]:
        return [kw for kind, kw in self.calls if kind == "create"]


class _LaunchSpy:
    """假 ``_launch``：记调用 + 翻 running（真子进程在单测里不起）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        task: EvolutionTask,
        *,
        loop_n: int,
        seed: str | None = None,
        provider_uri: str | None = None,
        direction: str = "",
        llm_overrides: dict[str, str] | None = None,
    ) -> None:
        self.calls.append(
            {"task_id": task.task_id, "direction": direction, "overrides": llm_overrides}
        )
        task.status = TaskStatus.RUNNING


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """硬件锁与数据版本快照按用例替身：不碰真机 GPU 锁、不扫真盘。"""
    from backend.services.engine.alpha_agent import hw_lock

    monkeypatch.setattr(hw_lock, "assert_factor_mining_hardware", lambda: None)
    monkeypatch.setattr(
        launcher_module,
        "snapshot_data_versions",
        lambda **kw: dict(_STUB_VERSIONS, market=kw.get("market"), freq=kw.get("freq")),
    )


def _launcher(log_dir: Path | None = None) -> AlphaAgentLauncher:
    obj = AlphaAgentLauncher.__new__(AlphaAgentLauncher)
    obj._tasks = {}
    obj._log_dir = (
        Path(log_dir)
        if log_dir is not None
        else Path(tempfile.mkdtemp(prefix="qm-mining-dedupe-"))
    )
    obj._draining = False
    return obj


def _install_store(monkeypatch, *, duplicate_id: str | None = None) -> _RecordingStore:
    store = _RecordingStore(duplicate_id=duplicate_id)
    monkeypatch.setattr(launcher_module, "_task_store", lambda: store)
    return store


def _install_launch(monkeypatch, launcher: AlphaAgentLauncher) -> _LaunchSpy:
    spy = _LaunchSpy()
    monkeypatch.setattr(launcher, "_launch", spy)
    return spy


# ── evolve 单条：命中即复用 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_config_reuses_active_task(monkeypatch) -> None:
    """双击「开始挖掘」：第二次拿到同一个 task_id，绝不建第二个任务。"""
    store = _install_store(monkeypatch)
    launcher = _launcher()
    spy = _install_launch(monkeypatch, launcher)

    first = await launcher.start_evolution("u-1", direction="动量 × 波动率", loop_n=3)
    second = await launcher.start_evolution("u-1", direction="动量 × 波动率", loop_n=3)

    assert second == first
    assert len(launcher._tasks) == 1
    assert len(store.creates()) == 1, "复用不该再落第二行"
    assert len(spy.calls) == 1, "复用不该再起第二个子进程"


@pytest.mark.asyncio
async def test_different_params_create_new_tasks(monkeypatch) -> None:
    """方向/轮数/股票池任一不同 = 另一个研究假设：照常新建。"""
    _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    base = await launcher.start_evolution("u-1", direction="动量", loop_n=3)
    other_direction = await launcher.start_evolution("u-1", direction="反转", loop_n=3)
    other_loops = await launcher.start_evolution("u-1", direction="动量", loop_n=5)
    other_universe = await launcher.start_evolution(
        "u-1", direction="动量", loop_n=3, universe="csi500"
    )

    assert len({base, other_direction, other_loops, other_universe}) == 4


@pytest.mark.asyncio
async def test_sampling_evidence_and_llm_keys_not_in_fingerprint(monkeypatch) -> None:
    """``direction_meta``（抽样凭证）与 ``llm_overrides``（凭据）不进指纹。"""
    _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    first = await launcher.start_evolution(
        "u-1",
        direction="动量",
        direction_mode="random",
        direction_meta='{"seed": 1, "picked": "动量"}',
        llm_overrides={"DEEPSEEK_API_KEY": "sk-a"},
    )
    second = await launcher.start_evolution(
        "u-1",
        direction="动量",
        direction_mode="random",
        direction_meta='{"seed": 2, "picked": "动量"}',
        llm_overrides={"DEEPSEEK_API_KEY": "sk-b"},
    )

    assert second == first, "重抽一次抽样/换一把 Key 都不是「新任务」"


@pytest.mark.asyncio
async def test_terminal_task_does_not_block_rerun(monkeypatch) -> None:
    """任务收终态后同配置重跑正当（复盘 / 数据更新后重挖）——不拦。"""
    _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    first = await launcher.start_evolution("u-1", direction="动量")
    launcher._tasks[first].status = TaskStatus.COMPLETED

    second = await launcher.start_evolution("u-1", direction="动量")

    assert second != first
    assert len(launcher._tasks) == 2


@pytest.mark.asyncio
async def test_other_user_same_config_not_deduped(monkeypatch) -> None:
    """指纹按 (用户, 配置) 判：别人的同配置任务不是我的重复提交。"""
    _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    mine = await launcher.start_evolution("u-1", direction="动量")
    theirs = await launcher.start_evolution("u-2", direction="动量")

    assert mine != theirs


# ── 批量路径：回执带既有任务实况 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_start_or_queue_reuses_running_task(monkeypatch) -> None:
    _install_store(monkeypatch)
    launcher = _launcher()
    spy = _install_launch(monkeypatch, launcher)

    first = await launcher.start_or_queue("u-1", direction="动量")
    again = await launcher.start_or_queue("u-1", direction="动量")

    assert again.deduplicated is True
    assert again.task_id == first.task_id
    assert again.status == "running"
    assert again.queue_position is None
    assert len(spy.calls) == 1 and len(launcher._tasks) == 1


@pytest.mark.asyncio
async def test_start_or_queue_reuses_queued_task_with_its_position(monkeypatch) -> None:
    """排队中的同配置任务也算「在路上」：回执给它在队列里的真实位次。"""
    monkeypatch.setenv("ALPHA_AGENT_MAX_RUNNING_PER_USER", "1")
    _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    await launcher.start_or_queue("u-1", direction="占位任务")  # 占满唯一名额
    queued = await launcher.start_or_queue("u-1", direction="动量")
    assert queued.status == "queued" and queued.queue_position == 1

    again = await launcher.start_or_queue("u-1", direction="动量")

    assert again.deduplicated is True
    assert (again.task_id, again.status, again.queue_position) == (
        queued.task_id,
        "queued",
        1,
    )
    assert len(launcher._tasks) == 2, "复用的排队任务没有再造第三个"


# ── 竞态兜底：部分唯一索引 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_conflict_withdraws_and_reuses_memory_winner(monkeypatch) -> None:
    """并发竞态：落行撞唯一索引 → 撤回本次提交、复用内存里的赢家。"""
    winner = EvolutionTask(
        task_id="win-1",
        user_id="u-1",
        status=TaskStatus.RUNNING,
        direction="动量",
    )
    store = _install_store(monkeypatch, duplicate_id=winner.task_id)
    launcher = _launcher()
    launcher._tasks[winner.task_id] = winner
    spy = _install_launch(monkeypatch, launcher)

    task_id = await launcher.start_evolution("u-1", direction="动量")

    assert task_id == "win-1"
    assert list(launcher._tasks) == ["win-1"], "败者必须从内存表撤回"
    assert spy.calls == [], "撤回的提交绝不起子进程"
    assert len(store.creates()) == 1


@pytest.mark.asyncio
async def test_index_conflict_without_memory_winner_keeps_submission(monkeypatch) -> None:
    """撞行但内存没有赢家（重启前的孤儿行）：不撤回，本次照常跑（fail-open）。

    复用行不通：那会回执一个本进程根本没有的死任务。宁可多跑一个、记录行
    缺席（告警），也绝不把死任务的 id 交给用户。
    """
    _install_store(monkeypatch, duplicate_id="ghost-task")
    launcher = _launcher()
    spy = _install_launch(monkeypatch, launcher)

    task_id = await launcher.start_evolution("u-1", direction="动量")

    assert task_id != "ghost-task"
    assert task_id in launcher._tasks
    assert len(spy.calls) == 1


# ── 任务行留档：指纹 + 两个快照 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_row_carries_fingerprint_and_snapshots(monkeypatch) -> None:
    """任务行必须能自证「同的参数指纹」与「哪份数据」——审计面就在这两列。"""
    store = _install_store(monkeypatch)
    launcher = _launcher()
    _install_launch(monkeypatch, launcher)

    task_id = await launcher.start_evolution(
        "u-1", direction="动量", loop_n=3, mining_start="2024-01-02", mining_end="2025-06-30"
    )
    task = launcher._tasks[task_id]
    (row,) = store.creates()

    assert row["fingerprint"] == task.fingerprint != ""
    assert row["algorithm"] == task.algorithm
    assert row["algorithm_version"] == load_algorithm(
        normalize_algorithm(task.algorithm)
    ).version
    # params_snapshot 与指纹同源：落列的字典逐字段就是哈希的那份内容
    params = json.loads(row["params_snapshot"])
    assert params == task.params_snapshot
    assert params["direction"] == "动量"
    assert params["mining_start"] == "2024-01-02"
    assert params["loop_n"] == 3
    assert json.loads(row["data_versions"])["snapshot_at"] == "2026-01-02T00:00:00Z"


# ── 重启：指纹随盘 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fingerprint_survives_restart(tmp_path: Path, monkeypatch) -> None:
    """重启后幸存的排队任务仍参与去重——否则「重启一次再点一下」就是双开。"""
    monkeypatch.setenv("ALPHA_AGENT_MAX_RUNNING_PER_USER", "1")
    _install_store(monkeypatch)
    writer = _launcher(tmp_path)
    _install_launch(monkeypatch, writer)

    await writer.start_or_queue("u-1", direction="占位任务")
    queued = await writer.start_or_queue("u-1", direction="动量")
    assert queued.status == "queued"

    reader = _launcher(tmp_path)
    reader._load_tasks()
    _install_launch(monkeypatch, reader)

    again = await reader.start_or_queue("u-1", direction="动量")

    assert reader._tasks[queued.task_id].fingerprint != "", "指纹必须随盘"
    assert again.deduplicated is True
    assert again.task_id == queued.task_id


def test_load_tasks_without_fingerprint_field_is_unknown(tmp_path: Path) -> None:
    """本特性之前写盘的 state 没有指纹字段：''=未知，不参与去重（不伪装成无重复）。"""
    launcher = _launcher(tmp_path)
    state = tmp_path / "t-old" / "task_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "task_id": "t-old",
                "user_id": "u-1",
                "status": "queued",
                "direction": "动量",
            }
        ),
        encoding="utf-8",
    )

    launcher._load_tasks()

    assert launcher._tasks["t-old"].fingerprint == ""
    assert launcher.find_active_duplicate("u-1", "") is None
