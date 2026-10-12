"""T-FM-07 决策轨迹（``trajectory_store``）契约：

- **结构扫描**：``Loop_N`` 数值排序、五环节节点状态（ok/partial/absent）、
  pkl 数、time_info dict 优先 / 文件 mtime 兜底、缺环与空环如实；
- **归因失败降级**：坏 pkl / 结构不识别 / 超解析上限 → ``has_load_error``
  → ``attribution="task_level"``（逐轮因子置空、因子挂任务级），
  绝不硬分配给某轮、绝不猜测；
- **结局 join**：物化清单终态（materialized / rejected_duplicate /
  rejected_gate / superseded）+ DB 行状态（pending / completed 未提交 /
  failed）+ 无行 → 缺失 None；同名多行取「有清单条目优先，否则最新」；
- **低奖励旗标**（只标位置+事实）：无 selected / 任务失败最深轮 / 全被拒
  （未知 outcome 不立旗）；
- **截断与部分**：loops 保尾部 + ``dropped_loops``；运行中任务 ``partial``。

目录树用真文件 + 真 pickle（合成同名对象）；DB 行与清单用假替身——
本文件钉扫描与组装两层，不碰真库。
"""

from __future__ import annotations

import os
import pickle
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services.engine.alpha_agent import trajectory_store as ts
from backend.shared import safe_pickle as sp


@pytest.fixture(autouse=True)
def _allow_test_module_pickle_root(monkeypatch):
    """本文件合成类（``__module__`` = 本测试模块）放行进白名单。

    生产白名单按真样本钉死为 ``rdagent/datetime/pathlib/collections/
    target_task``（评审 security-F1；机制单源在 ``shared.safe_pickle``，
    2026-10-12 五站点改造后本文件改打共享常量）；这里的合成类扮演真实轨迹里
    ``rdagent.*`` 数据类的角色——放行其模块根模拟同一路径，白名单策略本身由
    :class:`TestDefaultLoaderGuard` 的表驱动单测钉死。
    """
    monkeypatch.setattr(
        sp,
        "ALLOWED_PICKLE_MODULE_ROOTS",
        sp.ALLOWED_PICKLE_MODULE_ROOTS | {__name__.split(".")[0]},
    )


# ── 合成对象（pickle 往返用；类在本模块顶层，同进程可反序列化）──────────


class FakeFactorTask:
    def __init__(self, factor_name: str) -> None:
        self.factor_name = factor_name


class FakeWorkspace:
    """模拟 FactorFBWorkspace：名字在 target_task 上。"""

    def __init__(self, factor_name: str) -> None:
        self.target_task = FakeFactorTask(factor_name)


def _write_pkl(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(obj, fh)


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _time_info(loop_dir: Path, rel: str, start: str, end: str) -> None:
    _write_pkl(
        loop_dir / rel / "s1" / "t.pkl",
        {"start_time": _dt(start), "end_time": _dt(end)},
    )


def _make_tree(root: Path, *, corrupt: bool = False) -> Path:
    """三循环合成树：

    - ``Loop_0``：五环节齐备（dict 计时；实验提 2 个因子、编码实现其中 1 个）；
    - ``Loop_1``：进行中（仅假设+实验，无 time_info → mtime 兜底）；
    - ``Loop_2``：空目录（全部 absent）。
    """
    root.mkdir(parents=True, exist_ok=True)

    l0 = root / "Loop_0"
    _write_pkl(
        l0 / "direct_exp_gen/hypothesis generation/s1/h.pkl", {"hypothesis": "x"}
    )
    _write_pkl(
        l0 / "direct_exp_gen/experiment generation/s1/e.pkl",
        [FakeFactorTask("f_ok"), FakeFactorTask("f_dup")],
    )
    _time_info(
        l0, "direct_exp_gen/time_info", "2026-10-09 10:00:00", "2026-10-09 10:05:00"
    )
    _write_pkl(l0 / "coding/coder result/s1/c.pkl", [FakeWorkspace("f_ok")])
    _time_info(l0, "coding/time_info", "2026-10-09 10:05:00", "2026-10-09 10:20:00")
    _time_info(l0, "running/time_info", "2026-10-09 10:20:00", "2026-10-09 10:30:00")
    _write_pkl(l0 / "feedback/feedback/s1/f.pkl", {"decision": True})
    _time_info(l0, "feedback/time_info", "2026-10-09 10:30:00", "2026-10-09 10:31:00")

    l1 = root / "Loop_1"
    _write_pkl(
        l1 / "direct_exp_gen/hypothesis generation/s1/h.pkl", {"hypothesis": "y"}
    )
    _write_pkl(
        l1 / "direct_exp_gen/experiment generation/s1/e.pkl",
        [FakeFactorTask("f_pend")],
    )

    (root / "Loop_2").mkdir(parents=True, exist_ok=True)

    if corrupt:
        bad = root / "Loop_2"
        (bad / "direct_exp_gen/experiment generation/s1").mkdir(
            parents=True, exist_ok=True
        )
        (bad / "direct_exp_gen/experiment generation/s1/broken.pkl").write_bytes(
            b"\x80\x04not-a-pickle"
        )
    return root


# ── 结构扫描 ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestScanWorkspace:
    def test_loop_order_nodes_and_pkl_counts(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root)

        assert [lr.loop for lr in scan.loops] == [0, 1, 2]
        assert scan.dropped_loops == 0
        assert scan.total_loops == 3
        assert scan.has_load_error is False

        l0 = scan.loops[0]
        by_phase = {n.phase: n for n in l0.nodes}
        assert [n.phase for n in l0.nodes] == list(ts.PHASES)
        assert all(by_phase[p].status == ts.STATUS_OK for p in ts.PHASES)
        assert by_phase["hypothesis"].pkl_count == 1
        assert by_phase["experiment"].pkl_count == 1
        assert by_phase["coding"].pkl_count == 1
        assert by_phase["running"].pkl_count == 0, (
            "running 无独立载荷（time_info 即证据）"
        )
        assert by_phase["feedback"].pkl_count == 1
        assert by_phase["feedback"].duration_s == 60.0
        assert by_phase["hypothesis"].duration_s == 300.0
        # direct_exp_gen 一步内含假设+实验：共用该步 time_info，区间相同
        assert by_phase["hypothesis"].start == by_phase["experiment"].start
        assert by_phase["hypothesis"].end == by_phase["experiment"].end
        assert l0.proposed_names == ("f_ok", "f_dup")
        assert l0.coder_names == ("f_ok",)

    def test_partial_and_empty_loops(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root)

        l1 = {n.phase: n for n in scan.loops[1].nodes}
        assert l1["hypothesis"].status == ts.STATUS_OK
        assert l1["hypothesis"].pkl_count == 1
        # 无 time_info → mtime 兜底（单文件 → 零时长，如实）
        assert l1["hypothesis"].start is not None
        assert l1["hypothesis"].duration_s == 0.0
        assert l1["coding"].status == ts.STATUS_ABSENT
        assert l1["running"].status == ts.STATUS_ABSENT
        assert l1["feedback"].status == ts.STATUS_ABSENT
        assert l1["coding"].start is None and l1["coding"].pkl_count == 0

        l2 = {n.phase: n for n in scan.loops[2].nodes}
        assert all(n.status == ts.STATUS_ABSENT for n in l2.values())

    def test_keeps_tail_when_truncated(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        (root / "Loop_3").mkdir()
        scan = ts.scan_workspace(root, max_loops=2)
        assert [lr.loop for lr in scan.loops] == [2, 3], "保尾部（深轮更近失败点）"
        assert scan.dropped_loops == 2
        assert scan.total_loops == 4

    def test_string_time_info_parsed_and_garbage_falls_back_to_mtime(self, tmp_path):
        root = tmp_path / "logs"
        l0 = root / "Loop_0"
        _write_pkl(l0 / "direct_exp_gen/hypothesis generation/s1/h.pkl", {"h": 1})
        _write_pkl(
            l0 / "direct_exp_gen/experiment generation/s1/e.pkl",
            [FakeFactorTask("f_a")],
        )
        _write_pkl(
            l0 / "direct_exp_gen/time_info/s1/t.pkl",
            {
                "start_time": "2026-10-09 10:00:00+00:00",
                "end_time": "2026-10-09 10:02:00+00:00",
            },
        )
        _write_pkl(l0 / "coding/coder result/s1/c.pkl", [FakeWorkspace("f_a")])
        # 计时串不可解析 → 视作无 dict，走 mtime 兜底（不猜时间）
        _write_pkl(
            l0 / "coding/time_info/s1/t.pkl",
            {"start_time": "not-a-time", "end_time": "also-bad"},
        )

        nodes = {n.phase: n for n in ts.scan_workspace(root).loops[0].nodes}
        assert nodes["hypothesis"].status == ts.STATUS_OK
        assert nodes["hypothesis"].duration_s == 120.0, "字符串时间与 datetime 同解析"
        assert nodes["coding"].status == ts.STATUS_OK
        assert nodes["coding"].start is not None, "坏计时串退回 mtime 兜底"

    def test_naive_datetime_treated_as_utc(self, tmp_path):
        root = tmp_path / "logs"
        l0 = root / "Loop_0"
        _write_pkl(
            l0 / "running/time_info/s1/t.pkl",
            {
                "start_time": datetime(2026, 10, 9, 10),
                "end_time": datetime(2026, 10, 9, 10, 1),
            },
        )
        node = ts.scan_workspace(root).loops[0].nodes
        running = next(n for n in node if n.phase == "running")
        assert running.status == ts.STATUS_OK
        assert running.start is not None and running.start.tzinfo is not None
        assert running.duration_s == 60.0


# ── pkl 读取与因子名提取 ──────────────────────────────────────────────


@pytest.mark.unit
class TestLoadAndExtract:
    def test_extract_names_ok_and_failure_shapes(self):
        names, ok = ts.extract_factor_names([FakeFactorTask("a"), FakeWorkspace("b")])
        assert ok is True and names == ["a", "b"], (
            "实验（FactorTask）与编码（Workspace）同口径"
        )
        assert ts.extract_factor_names([]) == ([], True), "空提案是合法事实"
        assert ts.extract_factor_names(None) == ([], False)
        assert ts.extract_factor_names({"weird": 1}) == ([], False)
        names, ok = ts.extract_factor_names([FakeFactorTask("a"), FakeFactorTask("a")])
        assert names == ["a"], "同名去重保序"

    def test_broken_pickle_degrades_not_raises(self, tmp_path):
        root = _make_tree(tmp_path / "logs", corrupt=True)
        scan = ts.scan_workspace(root)
        assert scan.has_load_error is True
        l2 = {n.phase: n for n in scan.loops[2].nodes}
        assert l2["experiment"].pkl_count == 1, "坏文件仍计入 pkl 数（存在性事实）"
        assert scan.loops[2].proposed_names == ()

    def test_loader_exception_contained(self, tmp_path):
        def boom(_path):
            raise RuntimeError("loader down")

        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root, loader=boom)
        assert scan.has_load_error is True
        assert scan.loops[0].proposed_names == ()
        # 结构骨架不受影响
        assert {n.phase for n in scan.loops[0].nodes} == set(ts.PHASES)

    def test_oversize_payload_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ts, "MAX_LOAD_FILES_PER_PHASE", 0)
        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root)
        assert scan.has_load_error is True, "超解析上限按归因失败降级，不静默丢名"


@pytest.mark.unit
class TestDefaultLoaderGuard:
    """默认读取器的告警降级策略（机制 = 白名单反序列化 + 有界读，评审
    security-F1/F4，2026-10-12；机制单源已迁至 ``shared.safe_pickle``——
    本类钉「策略层不抛穿 + 拒绝即不执行」的端到端行为，白名单表驱动单测见
    ``test_safe_pickle.py``）。

    载荷 pkl 由挖掘子进程写入（该子进程按设计执行 LLM 生成代码）——主进程
    反序列化必须只放行展示所需的数据类；白名单外的 ``__reduce__`` 类路径
    （``os.system`` 等）在 ``find_class`` 层即拒，绝不触发调用。
    """

    def test_policy_table_module_roots_and_builtins(self):
        allow = sp.is_allowed_pickle_class
        # 真样本实测引用集（任务 d9a6e6a16a0445de，2026-10-12）
        assert allow("rdagent.core.proposal", "Hypothesis") is True
        assert (
            allow(
                "rdagent.scenarios.qlib.experiment.factor_experiment",
                "FactorFBWorkspace",
            )
            is True
        )
        assert allow("datetime", "datetime") is True
        assert allow("datetime", "timedelta") is True
        assert allow("pathlib", "PosixPath") is True
        assert allow("builtins", "dict") is True
        assert allow("builtins", "NoneType") is True
        # 可执行名 / 白名单外模块一律拒绝
        assert allow("builtins", "eval") is False
        assert allow("builtins", "exec") is False
        assert allow("builtins", "getattr") is False
        assert allow("os", "system") is False
        assert allow("subprocess", "Popen") is False
        assert allow("posix", "system") is False
        assert allow("rdagentx", "Hypothesis") is False, "前缀边界不得放宽"
        assert allow("", "x") is False

    def test_whitelisted_root_roundtrips(self, tmp_path):
        p = tmp_path / "ok.pkl"
        _write_pkl(p, FakeFactorTask("f_a"))
        loaded = ts.default_load_pickle(p)
        assert isinstance(loaded, FakeFactorTask)
        assert loaded.factor_name == "f_a"

    def test_unlisted_root_rejected(self, tmp_path, monkeypatch):
        # 把白名单还原为生产五根：本模块合成类应被拒（机制真的在把关）
        monkeypatch.setattr(
            sp,
            "ALLOWED_PICKLE_MODULE_ROOTS",
            frozenset({"rdagent", "datetime", "pathlib", "collections", "target_task"}),
        )
        p = tmp_path / "sneaky.pkl"
        _write_pkl(p, FakeFactorTask("f_a"))
        assert ts.default_load_pickle(p) is None

    def test_datetime_roundtrip_allowed(self, tmp_path):
        p = tmp_path / "t.pkl"
        _write_pkl(p, {"start_time": _dt("2026-10-09 10:00:00")})
        loaded = ts.default_load_pickle(p)
        assert isinstance(loaded, dict)
        assert loaded["start_time"] == _dt("2026-10-09 10:00:00")

    def test_reduce_os_system_payload_rejected_and_not_executed(self, tmp_path):
        target = tmp_path / "pwned"

        class Evil:
            def __reduce__(self):
                return (os.system, (f"touch {target}",))

        p = tmp_path / "evil.pkl"
        _write_pkl(p, Evil())
        assert ts.default_load_pickle(p) is None, "find_class 层拒绝（posix.system）"
        assert not target.exists(), "拒绝即未执行——目标文件不得出现"

    def test_builtins_eval_payload_rejected(self, tmp_path):
        # 载荷直接是 eval 本身：GLOBAL builtins eval → 白名单 builtins 名之外
        p = tmp_path / "eval.pkl"
        p.write_bytes(pickle.dumps(eval))
        assert ts.default_load_pickle(p) is None

    def test_oversize_bytes_skipped_without_parsing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "MAX_PICKLE_BYTES", 64)
        p = tmp_path / "big.pkl"
        _write_pkl(p, [FakeFactorTask(f"f{i}") for i in range(200)])
        assert ts.default_load_pickle(p) is None, "超上限单次有界读后即弃（不再解析）"

    def test_glob_files_limit_truncates(self, tmp_path):
        for i in range(10):
            (tmp_path / f"f{i}.pkl").write_bytes(b"x")
        got = ts._glob_files(tmp_path, "*.pkl", limit=3)
        assert len(got) == 3
        assert got == sorted(got), "截断也保持确定性排序"


# ── 结局 join 与组装 ──────────────────────────────────────────────────


MANIFEST = {
    "id_mat": {"status": "materialized", "column": "rd_f_mat", "name": "f_mat"},
    "id_dup": {
        "status": "rejected_duplicate",
        "corr": 0.91,
        "corr_against": "rd_mom_20",
        "name": "f_dup",
    },
    "id_gate": {
        "status": "rejected_gate",
        "name": "f_gate",
        "gates": {
            "rejected": True,
            "gates": [
                {
                    "key": "turnover_cap",
                    "mode": "hard",
                    "status": "fail",
                    "message": "换手过高（成本侵蚀）",
                },
                {
                    "key": "pfs_floor",
                    "mode": "soft",
                    "status": "skipped",
                    "message": "PFS 缺失（未回测或旧口径），不判不拦",
                },
            ],
        },
    },
    "id_super": {
        "status": "materialized",
        "superseded_by": "rd_new_x",
        "name": "f_super",
    },
}

DB_ROWS = [
    {"factor_id": "id_mat", "factor_name": "f_mat", "status": "completed"},
    {"factor_id": "id_dup", "factor_name": "f_dup", "status": "completed"},
    {"factor_id": "id_pend", "factor_name": "f_pend", "status": "pending"},
    {"factor_id": "id_comp", "factor_name": "f_comp", "status": "completed"},
    {"factor_id": "id_gate", "factor_name": "f_gate", "status": "completed"},
    {"factor_id": "id_super", "factor_name": "f_super", "status": "completed"},
    {"factor_id": "id_fail", "factor_name": "f_fail", "status": "failed"},
]

TASK = {
    "task_id": "t" * 16,
    "direction": "概念热度轮动",
    "status": "completed",
    "market": "a_share",
    "user_id": "u1",
}


def _nodes(statuses: dict[str, str] | None = None) -> tuple[ts.PhaseNode, ...]:
    statuses = statuses or {}
    return tuple(
        ts.PhaseNode(
            phase=p,
            status=statuses.get(p, ts.STATUS_OK),
            start=None,
            end=None,
            duration_s=None,
            pkl_count=1,
        )
        for p in ts.PHASES
    )


def _loop_scan(
    loop: int,
    *,
    proposed: tuple[str, ...],
    coder: tuple[str, ...] = (),
    coder_ok: bool = True,
    statuses: dict[str, str] | None = None,
) -> ts.LoopScan:
    return ts.LoopScan(
        loop=loop,
        nodes=_nodes(statuses),
        proposed_names=proposed,
        coder_names=coder,
        coder_ok=coder_ok,
        has_load_error=False,
    )


def _scan(*loops: ts.LoopScan, load_error: bool = False) -> ts.WorkspaceScan:
    return ts.WorkspaceScan(
        loops=tuple(loops),
        dropped_loops=0,
        total_loops=len(loops),
        has_load_error=load_error,
    )


def _fates(payload: ts.TaskTrajectory, loop_idx: int) -> dict[str, ts.FactorFate]:
    loop = next(lr for lr in payload.loops if lr.loop == loop_idx)
    return {f.name: f for f in loop.factors}


@pytest.mark.unit
class TestOutcomeJoin:
    def test_outcome_mapping_vocabulary(self):
        scan = _scan(
            _loop_scan(
                0,
                proposed=(
                    "f_mat",
                    "f_dup",
                    "f_pend",
                    "f_comp",
                    "f_gate",
                    "f_super",
                    "f_fail",
                    "f_unknown",
                ),
                coder=("f_mat",),
            )
        )
        traj = ts.build_trajectory(
            task=TASK, scan=scan, db_rows=DB_ROWS, manifest=MANIFEST
        )
        fates = _fates(traj, 0)

        assert fates["f_mat"].outcome == ts.OUTCOME_MATERIALIZED
        assert fates["f_mat"].reason is None

        assert fates["f_dup"].outcome == ts.OUTCOME_REJECTED_DUPLICATE
        assert "rd_mom_20" in fates["f_dup"].reason
        assert "0.910" in fates["f_dup"].reason

        assert fates["f_gate"].outcome == ts.OUTCOME_REJECTED_GATE
        assert fates["f_gate"].reason == "换手过高（成本侵蚀）", "只有 hard+fail 是拒因"

        assert fates["f_super"].outcome == ts.OUTCOME_SUPERSEDED
        assert "rd_new_x" in fates["f_super"].reason

        assert fates["f_pend"].outcome == ts.OUTCOME_PENDING
        assert fates["f_comp"].outcome is None
        assert "未提交物化" in fates["f_comp"].reason
        assert fates["f_fail"].outcome is None
        assert fates["f_fail"].reason == "因子计算失败"
        assert fates["f_unknown"].outcome is None
        assert fates["f_unknown"].reason is None, "无行 = 缺失，不编说明"

    def test_same_name_picks_manifest_linked_row(self):
        rows = [
            {"factor_id": "id_x1", "factor_name": "f_mat", "status": "failed"},
            {"factor_id": "id_mat", "factor_name": "f_mat", "status": "completed"},
        ]
        scan = _scan(_loop_scan(0, proposed=("f_mat",)))
        traj = ts.build_trajectory(
            task=TASK, scan=scan, db_rows=rows, manifest=MANIFEST
        )
        fate = _fates(traj, 0)["f_mat"]
        assert fate.outcome == ts.OUTCOME_MATERIALIZED, "有清单条目的行优先"

    def test_duplicate_reason_without_corr_against(self):
        rows = [{"factor_id": "id_d2", "factor_name": "f_d2", "status": "completed"}]
        scan = _scan(_loop_scan(0, proposed=("f_d2",)))
        traj = ts.build_trajectory(
            task=TASK,
            scan=scan,
            db_rows=rows,
            manifest={"id_d2": {"status": "rejected_duplicate"}},
        )
        fate = _fates(traj, 0)["f_d2"]
        assert fate.outcome == ts.OUTCOME_REJECTED_DUPLICATE
        assert fate.reason == "值级重复（库内已有高度相关列）"

    def test_selected_from_coder_membership(self):
        scan = _scan(
            _loop_scan(0, proposed=("a", "b"), coder=("a",)),
            _loop_scan(1, proposed=("c",), coder=(), coder_ok=False),
        )
        traj = ts.build_trajectory(task=TASK, scan=scan)
        f0 = _fates(traj, 0)
        assert f0["a"].selected is True and f0["b"].selected is False
        f1 = _fates(traj, 1)
        assert f1["c"].selected is None, "编码清单解析失败 = 未知，不判 False"


@pytest.mark.unit
class TestLowRewardFlags:
    def test_no_selected_flag(self):
        scan = _scan(_loop_scan(0, proposed=("a", "b"), coder=()))
        traj = ts.build_trajectory(task=TASK, scan=scan)
        loop = traj.loops[0]
        assert loop.flagged is True
        assert ts.FLAG_NO_SELECTED in loop.flags
        assert loop.flag_reason == ts.FLAG_TEXTS[ts.FLAG_NO_SELECTED]

    def test_no_selected_not_raised_when_unknown(self):
        scan = _scan(_loop_scan(0, proposed=("a",), coder_ok=False))
        loop = ts.build_trajectory(task=TASK, scan=scan).loops[0]
        assert loop.flagged is False, "selected 未知不立旗"

    def test_task_failed_flags_deepest_loop(self):
        scan = _scan(
            _loop_scan(0, proposed=("a",), coder=("a",)),
            _loop_scan(1, proposed=("b",), coder=("b",)),
        )
        traj = ts.build_trajectory(task={**TASK, "status": "failed"}, scan=scan)
        assert ts.FLAG_TASK_FAILED_HERE not in traj.loops[0].flags
        assert ts.FLAG_TASK_FAILED_HERE in traj.loops[1].flags

    def test_all_rejected_flag(self):
        scan = _scan(
            _loop_scan(0, proposed=("f_dup", "f_gate"), coder=("f_dup", "f_gate")),
            _loop_scan(1, proposed=("f_dup", "f_mat"), coder=("f_mat",)),
            _loop_scan(2, proposed=("f_dup", "f_unknown"), coder=("f_dup",)),
        )
        traj = ts.build_trajectory(
            task=TASK, scan=scan, db_rows=DB_ROWS, manifest=MANIFEST
        )
        assert ts.FLAG_ALL_REJECTED in traj.loops[0].flags
        assert ts.FLAG_ALL_REJECTED not in traj.loops[1].flags, "混入物化即不立旗"
        assert ts.FLAG_ALL_REJECTED not in traj.loops[2].flags, "未知 outcome 不立旗"

    def test_flags_join_reason_with_position_words(self):
        scan = _scan(_loop_scan(0, proposed=("f_dup",), coder=()))
        traj = ts.build_trajectory(
            task={**TASK, "status": "failed"},
            scan=scan,
            db_rows=DB_ROWS,
            manifest=MANIFEST,
        )
        loop = traj.loops[0]
        assert set(loop.flags) == {
            ts.FLAG_NO_SELECTED,
            ts.FLAG_TASK_FAILED_HERE,
            ts.FLAG_ALL_REJECTED,
        }
        assert "；" in loop.flag_reason
        assert all(t in loop.flag_reason for t in ts.FLAG_TEXTS.values())


@pytest.mark.unit
class TestBuildDegradation:
    def test_task_level_attribution_on_load_error(self, tmp_path):
        root = _make_tree(tmp_path / "logs", corrupt=True)
        scan = ts.scan_workspace(root)
        traj = ts.build_trajectory(
            task=TASK, scan=scan, db_rows=DB_ROWS, manifest=MANIFEST
        )
        assert traj.attribution == ts.ATTRIBUTION_TASK_LEVEL
        assert all(lr.factors == () for lr in traj.loops), "不硬分配给某轮"
        names = [f.name for f in traj.factors]
        assert names == [
            "f_mat",
            "f_dup",
            "f_pend",
            "f_comp",
            "f_gate",
            "f_super",
            "f_fail",
        ], "任务级清单来自 DB 行（去重保序）"
        fates = {f.name: f for f in traj.factors}
        assert fates["f_mat"].outcome == ts.OUTCOME_MATERIALIZED
        assert all(f.selected is None for f in traj.factors), (
            "无编码归因 = selected 未知"
        )

    def test_normal_attribution_loop_level(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root)
        traj = ts.build_trajectory(task=TASK, scan=scan)
        assert traj.attribution == ts.ATTRIBUTION_LOOP
        assert traj.factors == ()
        f0 = _fates(traj, 0)
        assert set(f0) == {"f_ok", "f_dup"}
        assert f0["f_ok"].selected is True

    def test_partial_and_truncated_flags(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        (root / "Loop_3").mkdir()
        scan = ts.scan_workspace(root, max_loops=2)
        traj = ts.build_trajectory(task={**TASK, "status": "running"}, scan=scan)
        assert traj.partial is True
        assert traj.truncated is True
        assert traj.dropped_loops == 2
        assert [lr.loop for lr in traj.loops] == [2, 3]

    def test_to_dict_envelope_keys(self, tmp_path):
        root = _make_tree(tmp_path / "logs")
        scan = ts.scan_workspace(root)
        data = ts.build_trajectory(task=TASK, scan=scan).to_dict()
        assert {
            "task_id",
            "direction",
            "status",
            "market",
            "partial",
            "attribution",
            "loops",
            "factors",
            "truncated",
            "dropped_loops",
            "generated_at",
        } <= set(data)
        loop = data["loops"][0]
        assert {"loop", "nodes", "factors", "flagged", "flags", "flag_reason"} <= set(
            loop
        )
        node = loop["nodes"][0]
        assert {"phase", "status", "start", "end", "duration_s", "pkl_count"} <= set(
            node
        )
        assert node["start"].endswith("Z"), "时间戳 aware UTC 带 Z"


@pytest.mark.unit
class TestResolveLogDir:
    def test_task_id_shape_guard(self, tmp_path):
        assert ts.resolve_task_log_dir("a1b2", root=tmp_path) == tmp_path / "a1b2"
        for bad in ("../etc", "a/b", "", "x" * 65):
            with pytest.raises(ValueError):
                ts.resolve_task_log_dir(bad, root=tmp_path)
