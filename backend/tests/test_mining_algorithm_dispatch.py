"""挖掘算法派发（移植 P2）——launcher 侧：闸门 / 能力面分支 / 进度契约。

路由的算法闸门（400 口径）在 ``test_alpha_agent_algorithms_api.py``；这里钉
launcher 自己的边（两条入口共用同一份 resolve，缺了这层，直调 launcher 的
调用方——排队补位、QuantBot——就绕过了算法校验）：

1. **闸门在派发前**：未知算法 / 统计派 + 非 A 股 / 统计派 + 分钟 / 统计派 +
   数据集选择，全部在起子进程前抛 ``AlgorithmError``；默认（None）= rdagent
   （迁移前唯一路径，老调用方零改动）。
2. **能力面分支**：``requires_gpu=false`` 不跑 GPU 硬件锁（统计派占用的是
   CPU）；``uses_llm=false`` 不注入用户级 LLM 密钥（没有消费方）。
3. **命令行按能力面裁剪**：统计派不带 ``--loop-n``/``--direction``——它的
   argparse 不认这两个旗标，带上就在解析参数时 exit 2。
4. **进度契约显式声明**：``progress_kind`` 决定读哪个进度面（jsonl 末行 →
   阶段/百分比，全量行 → 时间线）；坏行/半截行不炸、无事件保持启动态。
5. **跨模块合同**：runner 实际写的进度文件名、引擎实际发的阶段 key，必须与
   manifest/launcher 的声明一致（三处任何一处改了都会在这里红）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import launcher as launcher_module  # noqa: E402
from backend.services.engine.alpha_agent.algorithm_registry import (  # noqa: E402
    AlgorithmError,
    load_algorithm,
)
from backend.services.engine.alpha_agent.launcher import (  # noqa: E402
    AlphaAgentLauncher,
    EvolutionTask,
    _build_runner_cmd,
)

STATISTICAL = load_algorithm("statistical")
RDAGENT = load_algorithm("rdagent")


@pytest.fixture(autouse=True)
def _silence_hw_lock(monkeypatch):
    """默认把硬件锁换成 no-op；需要验锁的用例自己再覆盖。

    真机资源不参与单测（CI/开发机配置各异），锁的**是否被调用**才是本文件
    的断言对象。
    """
    from backend.services.engine.alpha_agent import hw_lock

    monkeypatch.setattr(hw_lock, "assert_factor_mining_hardware", lambda: None)


def _launcher(log_dir: Path) -> AlphaAgentLauncher:
    obj = AlphaAgentLauncher.__new__(AlphaAgentLauncher)
    obj._tasks = {}
    obj._log_dir = Path(log_dir)
    obj._draining = False
    return obj


class _LaunchSpy:
    """假 ``_launch``：只记录，不起子进程。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, task: EvolutionTask, **kwargs) -> None:
        self.calls.append({"task_id": task.task_id, **kwargs})


@pytest.fixture
def env(monkeypatch, tmp_path):
    launcher = _launcher(tmp_path)
    spy = _LaunchSpy()
    monkeypatch.setattr(launcher, "_launch", spy)

    async def _noop_create(task, **kwargs):
        return None

    monkeypatch.setattr(launcher, "_db_create", _noop_create)
    return launcher, spy


class TestAlgorithmGate:
    @pytest.mark.asyncio
    async def test_default_resolves_to_rdagent(self, env):
        """不传 algorithm = 迁移前唯一路径：任务行落 rdagent，行为零变化。"""
        launcher, spy = env
        task_id = await launcher.start_evolution("u-1", direction="动量")
        assert launcher._tasks[task_id].algorithm == "rdagent"
        assert len(spy.calls) == 1

    @pytest.mark.asyncio
    async def test_unknown_algorithm_rejected_before_spawn(self, env):
        launcher, spy = env
        with pytest.raises(AlgorithmError, match="不存在"):
            await launcher.start_evolution("u-1", algorithm="quantum")
        assert spy.calls == [] and launcher._tasks == {}

    @pytest.mark.asyncio
    async def test_statistical_resolves_and_persists_key(self, env):
        launcher, spy = env
        task_id = await launcher.start_evolution("u-1", algorithm="Statistical")
        assert launcher._tasks[task_id].algorithm == "statistical", "大小写归一"
        assert len(spy.calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"market": "hong_kong"},
            {"freq": "1min"},
            {"datasets": ["features_daily"]},
        ],
        ids=["market", "freq", "datasets"],
    )
    async def test_statistical_combination_gates(self, env, over):
        """能力面组合违例在 launcher 也拦（直调方绕不过路由的 400 闸）。"""
        launcher, spy = env
        with pytest.raises(AlgorithmError):
            await launcher.start_evolution("u-1", algorithm="statistical", **over)
        assert spy.calls == []

    @pytest.mark.asyncio
    async def test_requires_gpu_algorithm_calls_hw_lock(self, env, monkeypatch):
        """requires_gpu=true 的算法照旧过硬件锁（迁移前行为不变）。"""
        from backend.services.engine.alpha_agent import hw_lock

        calls: list[str] = []

        def _boom() -> None:
            calls.append("locked")
            raise hw_lock.HardwareLockError("本机不满足因子挖掘硬件要求")

        monkeypatch.setattr(hw_lock, "assert_factor_mining_hardware", _boom)
        launcher, spy = env
        with pytest.raises(hw_lock.HardwareLockError):
            await launcher.start_evolution("u-1", algorithm="rdagent")
        assert calls == ["locked"]

    @pytest.mark.asyncio
    async def test_cpu_algorithm_skips_hw_lock(self, env, monkeypatch):
        """统计派纯 CPU：GPU 锁拿它拦是错的——锁被调用即失败。"""
        from backend.services.engine.alpha_agent import hw_lock

        def _boom() -> None:  # pragma: no cover - 走到这里就是 bug
            raise AssertionError("统计派不该跑 GPU 硬件锁")

        monkeypatch.setattr(hw_lock, "assert_factor_mining_hardware", _boom)
        launcher, _spy = env
        task_id = await launcher.start_evolution("u-1", algorithm="statistical")
        assert launcher._tasks[task_id].algorithm == "statistical"

    @pytest.mark.asyncio
    async def test_start_or_queue_shares_the_gate(self, env):
        launcher, _spy = env
        with pytest.raises(AlgorithmError, match="不存在"):
            await launcher.start_or_queue("u-1", algorithm="quantum")
        receipt = await launcher.start_or_queue("u-1", algorithm="statistical")
        assert launcher._tasks[receipt.task_id].algorithm == "statistical"

    @pytest.mark.asyncio
    async def test_status_payload_carries_algorithm_and_progress_kind(self, env):
        """状态面把「跑的是哪个算法、进度面长什么样」如实带给前端。"""
        launcher, _spy = env
        task_id = await launcher.start_evolution("u-1", algorithm="statistical")
        status = await launcher.get_task_status(task_id)
        assert status["algorithm"] == "statistical"
        assert status["progress_kind"] == "jsonl"

    def test_progress_kind_of_falls_back_to_workdir(self):
        """manifest 读不到按迁移前形状兜底：老任务不因注册表故障而 500。"""
        assert launcher_module._progress_kind_of("statistical") == "jsonl"
        assert launcher_module._progress_kind_of("") == "workdir"
        assert launcher_module._progress_kind_of("does-not-exist") == "workdir"
        assert launcher_module._progress_kind_of("../etc/passwd") == "workdir"


class TestRunnerCmd:
    @staticmethod
    def _task(**over) -> EvolutionTask:
        base = {"task_id": "t1", "user_id": "u1", "market": "a_share", "universe": "csi300"}
        base.update(over)
        return EvolutionTask(**base)

    def test_rdagent_cmd_carries_evolution_flags(self, tmp_path):
        cmd = _build_runner_cmd(
            self._task(), RDAGENT, loop_n=5, direction="动量 × 波动率", log_dir=tmp_path
        )
        assert cmd[0] == sys.executable
        assert cmd[1:3] == ["-X", "faulthandler"]
        assert cmd[3].endswith("scripts/alpha_agent/run_rd_agent.py")
        assert "--loop-n" in cmd and cmd[cmd.index("--loop-n") + 1] == "5"
        assert cmd[cmd.index("--direction") + 1] == "动量 × 波动率"

    def test_statistical_cmd_omits_llm_only_flags(self, tmp_path):
        """统计派 argparse 不认 --loop-n/--direction——带上就 exit 2。"""
        cmd = _build_runner_cmd(
            self._task(), STATISTICAL, loop_n=5, direction="会被丢弃", log_dir=tmp_path
        )
        assert cmd[3].endswith("scripts/alpha_agent/run_statistical_mining.py")
        assert "--loop-n" not in cmd and "--direction" not in cmd
        # 共同信封（runner 的 argparse 契约）一个不能少
        for flag, value in (
            ("--task-id", "t1"),
            ("--user-id", "u1"),
            ("--universe", "csi300"),
            ("--market", "a_share"),
            ("--log-dir", str(tmp_path)),
        ):
            assert cmd[cmd.index(flag) + 1] == value


class TestJsonlProgress:
    """progress_kind=jsonl 的进度读取器（统计派运行中的进度面）。"""

    @staticmethod
    def _events(log_dir: Path, *stages_with_detail) -> Path:
        path = log_dir / AlphaAgentLauncher._JSONL_PROGRESS_FILE
        lines = [
            json.dumps(
                {"stage": s, "detail": d, "ts": ts, "task_id": "t1"},
                ensure_ascii=False,
            )
            for (s, d, ts) in stages_with_detail
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_stage_label_pct_and_timeline(self, tmp_path):
        self._events(
            tmp_path,
            ("data", "读取行情面板", 1000.0),
            ("filter", "A 股可交易性过滤", 1060.0),
            ("evaluate", "逐候选计算", 1120.0),
        )
        task = EvolutionTask(task_id="t1", user_id="u1", progress_pct=2)
        _launcher(tmp_path)._update_jsonl_progress(task, tmp_path, STATISTICAL)

        # evaluate 是 7 阶段里的第 3 个：3/7 = 42.857… → 42（截断）
        assert task.phase == "evaluate"
        assert task.progress_pct == 42
        assert task.progress == "截面检验 — 逐候选计算", "标签来自 manifest，不是 key"
        phases = task.timeline[0]["phases"]
        # 时间线声明 manifest 全阶段：未见过的阶段以 pending 补齐（前端据此
        # 画出完整 7 阶段管线，而不是见到几个画几个）。
        assert [p["key"] for p in phases] == [
            "data",
            "filter",
            "evaluate",
            "select",
            "stability",
            "dedup",
            "composite",
        ]
        assert [p["label"] for p in phases] == [
            "取数",
            "可交易性过滤",
            "截面检验",
            "入库",
            "子窗稳定性",
            "相关性去重",
            "IC 加权合成",
        ]
        assert [p["status"] for p in phases] == [
            "completed",
            "completed",
            "running",
            "pending",
            "pending",
            "pending",
            "pending",
        ]
        assert task.timeline[0]["label"] == STATISTICAL.name
        # 时刻来自事件 ts（epoch → ISO UTC），不是读文件时刻。前两阶段由
        # 「下一阶段的首个事件」收口（顺序执行，边界事件=结束证据）；
        # 末阶段仍在跑 → 无 end_time、无 duration（不伪造收尾时刻）。
        assert phases[0]["start_time"] == "1970-01-01T00:16:40Z"
        assert phases[0]["end_time"] == "1970-01-01T00:17:40Z"
        assert phases[1]["end_time"] == "1970-01-01T00:18:40Z"
        assert phases[2]["end_time"] is None
        assert [p["duration_s"] for p in phases] == [
            60.0, 60.0, None, None, None, None, None,
        ]
        assert phases[3]["start_time"] is None and phases[3]["detail"] == ""
        assert task.timeline[0]["phases"][3]["duration_s"] is None

    def test_repeat_stage_event_becomes_its_end(self, tmp_path):
        """同阶段多次上报（evaluate 逐候选）：末次事件即阶段终点，且不被后一
        阶段的边界事件覆盖（它比边界更贴近真实收尾）。"""
        self._events(
            tmp_path,
            ("evaluate", "候选 1/2", 1000.0),
            ("evaluate", "候选 2/2", 1040.0),
            ("select", "Top-K 入库", 1090.0),
        )
        task = EvolutionTask(task_id="t1", user_id="u1")
        _launcher(tmp_path)._update_jsonl_progress(task, tmp_path, STATISTICAL)
        phases = {p["key"]: p for p in task.timeline[0]["phases"]}
        evaluate = phases["evaluate"]
        assert evaluate["start_time"] == "1970-01-01T00:16:40Z", "首事件即起点"
        assert evaluate["end_time"] == "1970-01-01T00:17:20Z"
        assert evaluate["duration_s"] == 40.0
        assert evaluate["detail"] == "候选 2/2"
        assert phases["data"]["status"] == "pending", "未上报的阶段不因它者而变"

    def test_finished_closes_last_phase(self, tmp_path):
        # 用末阶段（composite）上报：只有它才走到封顶语义
        self._events(tmp_path, ("composite", "IC 加权合成", 2000.0))
        task = EvolutionTask(task_id="t1", user_id="u1")
        _launcher(tmp_path)._update_jsonl_progress(
            task, tmp_path, STATISTICAL, finished=True
        )
        phases = {p["key"]: p for p in task.timeline[0]["phases"]}
        last = phases["composite"]
        assert last["status"] == "completed"
        assert phases["data"]["status"] == "pending", "finished 只收口已见到的那一段"
        assert task.timeline[0]["status"] == "completed"
        assert task.progress_pct == 99, "100 只由收尾路径置，读取器封顶 99"
        assert last["end_time"] is None, "无收尾事件时刻 → 不伪造 end_time"

    def test_broken_and_partial_lines_ignored(self, tmp_path):
        """正在写入的半截行 + 垃圾行都不许炸读取器（心跳每 3s 就要读一次）。"""
        good = json.dumps({"stage": "data", "detail": "读取", "ts": 1000.0})
        (tmp_path / AlphaAgentLauncher._JSONL_PROGRESS_FILE).write_text(
            good + "\n" + '{"stage": "fil\n' + "not json\n",
            encoding="utf-8",
        )
        task = EvolutionTask(task_id="t1", user_id="u1")
        _launcher(tmp_path)._update_jsonl_progress(task, tmp_path, STATISTICAL)
        assert task.phase == "data"
        phases = task.timeline[0]["phases"]
        assert [p["key"] for p in phases] == [
            "data",
            "filter",
            "evaluate",
            "select",
            "stability",
            "dedup",
            "composite",
        ]
        assert phases[0]["status"] == "running"
        assert [p["status"] for p in phases[1:]] == ["pending"] * 6

    def test_no_events_keeps_startup_state(self, tmp_path):
        task = EvolutionTask(task_id="t1", user_id="u1", progress_pct=2,
                             progress="正在启动因子挖掘...")
        _launcher(tmp_path)._update_jsonl_progress(task, tmp_path, STATISTICAL)
        assert task.progress_pct == 2 and task.timeline == []

    def test_unknown_stage_shows_key_without_fake_pct(self, tmp_path):
        """manifest 与引擎脱节的阶段：如实显示 key，不假装知道进度百分比。"""
        self._events(tmp_path, ("mystery", "引擎新阶段", 1000.0))
        task = EvolutionTask(task_id="t1", user_id="u1", progress_pct=2)
        _launcher(tmp_path)._update_jsonl_progress(task, tmp_path, STATISTICAL)
        assert task.progress_pct == 2, "未知阶段不推进进度"
        assert task.progress.startswith("mystery")
        phases = task.timeline[0]["phases"]
        # 声明阶段以 manifest 顺序在前，未知阶段如实追加在最后（不装作认识它）
        assert [p["key"] for p in phases] == [
            "data",
            "filter",
            "evaluate",
            "select",
            "stability",
            "dedup",
            "composite",
            "mystery",
        ]
        assert phases[-1]["status"] == "running" and phases[-1]["label"] == "mystery"

    def test_refresh_progress_dispatches_by_manifest(self, tmp_path, monkeypatch):
        """读取器按 manifest 的 progress_kind 分派，不嗅探文件猜。"""
        calls: list[str] = []
        monkeypatch.setattr(
            AlphaAgentLauncher, "_update_jsonl_progress",
            lambda self, t, d, a, **kw: calls.append(f"jsonl:{a.key}"),
        )
        monkeypatch.setattr(
            AlphaAgentLauncher, "_update_progress",
            lambda self, t, d: calls.append("workdir"),
        )
        launcher = _launcher(tmp_path)
        task = EvolutionTask(task_id="t1", user_id="u1")
        launcher._refresh_progress(task, tmp_path, STATISTICAL)
        launcher._refresh_progress(task, tmp_path, RDAGENT)
        assert calls == ["jsonl:statistical", "workdir"]


class TestCrossModuleContracts:
    def test_runner_writes_the_file_launcher_reads(self):
        text = (
            ROOT / "scripts" / "alpha_agent" / "run_statistical_mining.py"
        ).read_text(encoding="utf-8")
        assert AlphaAgentLauncher._JSONL_PROGRESS_FILE in text, (
            "runner 的进度文件名必须与 launcher 读取的文件名一致（契约漂移=进度面消失）"
        )

    def test_engine_emit_stages_match_manifest(self):
        """统计引擎实际 emit 的阶段 key ⊆ manifest 声明的 stages（含顺序无关）。"""
        text = (
            ROOT
            / "backend"
            / "services"
            / "engine"
            / "alpha_agent"
            / "statistical"
            / "engine.py"
        ).read_text(encoding="utf-8")
        emitted = set(re.findall(r'emit\(\s*"([a-z_]+)"', text))
        declared = {s.key for s in STATISTICAL.stages}
        assert emitted, "engine 里找不到 emit 调用（正则失配？）"
        assert emitted <= declared, f"引擎发了未声明阶段 {emitted - declared}"

    def test_declared_progress_kinds(self):
        assert RDAGENT.progress_kind == "workdir"
        assert STATISTICAL.progress_kind == "jsonl"
