"""``shared.safe_pickle`` 契约（五站点受限反序列化改造配套，2026-10-12）：

- **白名单表**：模块根（rdagent / datetime / pathlib / collections /
  target_task）+ builtins 数据名双层收口；可执行名与白名单外模块一律拒绝，
  前缀相似不感染（``rdagentx`` / ``target_taskx`` 不过）；常量按加载时刻
  实时读取（测试可整体替换，证明不是冻结副本）。
- **拒绝即不执行**：``os.system`` / ``builtins.eval`` 类 ``__reduce__`` 载荷
  在 ``find_class`` 层抛 ``UnpicklingError``，副作用绝不发生。
- **有界读取**：``read(MAX+1)``；恰好等长可解析、超 1 字节即
  ``PayloadTooLargeError``。
- **抛异常契约**：``OSError``（文件缺失）/ 解析失败原样上抛——降级策略归
  调用方；展示链路的「告警 + None」策略另测
  （``test_trajectory_store.TestDefaultLoaderGuard``）。
- **站点已接线**：launcher ``_load_pkl`` 与 wrapper ``_extract_factors``
  遇劫持载荷按失败降级且不执行（防回归回裸 ``pickle.load``）。
"""

from __future__ import annotations

import collections
import os
import pickle
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.shared import safe_pickle as sp


def _write(path: Path, obj: object) -> None:
    path.write_bytes(pickle.dumps(obj))


class _Evil:
    """``__reduce__`` 劫持载荷：反序列化即触发 ``os.system``。"""

    def __init__(self, target: Path) -> None:
        self._target = target

    def __reduce__(self):
        return (os.system, (f"touch {self._target}",))


@pytest.mark.unit
class TestWhitelistTable:
    def test_module_roots_measured_set(self):
        # 真样本全体普查（2026-10-12；五站点 glob 读取集 1619 文件）引用类全集
        assert sp.is_allowed_pickle_class("rdagent.core.experiment", "RunningInfo")
        assert sp.is_allowed_pickle_class(
            "rdagent.components.coder.factor_coder.factor", "FactorFBWorkspace"
        )
        assert sp.is_allowed_pickle_class("rdagent.core.proposal", "HypothesisFeedback")
        assert sp.is_allowed_pickle_class("target_task", "FactorTask"), (
            "coder result 载荷的 workspace.target_task 依赖此根（51 文件实测）"
        )
        assert sp.is_allowed_pickle_class("datetime", "datetime")
        assert sp.is_allowed_pickle_class("datetime", "timedelta")
        assert sp.is_allowed_pickle_class("pathlib", "PosixPath")
        assert sp.is_allowed_pickle_class("collections", "OrderedDict")

    def test_executable_and_foreign_roots_rejected(self):
        assert not sp.is_allowed_pickle_class("os", "system")
        assert not sp.is_allowed_pickle_class("subprocess", "Popen")
        assert not sp.is_allowed_pickle_class("posix", "system")
        assert not sp.is_allowed_pickle_class("builtins", "eval")
        assert not sp.is_allowed_pickle_class("builtins", "exec")
        assert not sp.is_allowed_pickle_class("builtins", "getattr")
        assert not sp.is_allowed_pickle_class("builtins", "__import__")
        assert not sp.is_allowed_pickle_class("rdagentx", "Hypothesis"), (
            "前缀边界不得放宽"
        )
        assert not sp.is_allowed_pickle_class("target_taskx", "FactorTask")
        assert not sp.is_allowed_pickle_class("", "x")

    def test_builtins_table_exactly_data_names(self):
        assert all(
            sp.is_allowed_pickle_class("builtins", name)
            for name in sp.ALLOWED_PICKLE_BUILTINS
        )
        assert sp.ALLOWED_PICKLE_BUILTINS == frozenset(
            {
                "NoneType",
                "bool",
                "bytearray",
                "bytes",
                "complex",
                "dict",
                "float",
                "frozenset",
                "int",
                "list",
                "object",
                "range",
                "set",
                "slice",
                "str",
                "tuple",
            }
        ), "builtins 白名单是审阅过的定值，扩容必须显式改这里"


@pytest.mark.unit
class TestLoadPickle:
    def test_roundtrip_allowed_types(self, tmp_path):
        p = tmp_path / "ok.pkl"
        _write(
            p,
            {
                "start_time": datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc),
                "path": Path("/x/y"),
                "ordered": collections.OrderedDict(a=1),
            },
        )
        loaded = sp.load_pickle(p)
        assert loaded["start_time"] == datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
        assert loaded["path"] == Path("/x/y")
        assert loaded["ordered"] == collections.OrderedDict(a=1)

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sp.load_pickle(tmp_path / "nope.pkl")

    def test_corrupt_payload_raises(self, tmp_path):
        p = tmp_path / "bad.pkl"
        blob = pickle.dumps({"a": 1})
        p.write_bytes(blob[: len(blob) - 2])
        with pytest.raises((pickle.UnpicklingError, EOFError, ValueError)):
            sp.load_pickle(p)

    def test_bounded_read_boundary(self, tmp_path, monkeypatch):
        p = tmp_path / "b.pkl"
        blob = pickle.dumps({"k": "v"})
        p.write_bytes(blob)
        # 恰好等长：可解析（上限是「≤」）
        monkeypatch.setattr(sp, "MAX_PICKLE_BYTES", len(blob))
        assert sp.load_pickle(p) == {"k": "v"}
        # 超 1 字节：单次读上限 +1 后即弃，不解析
        monkeypatch.setattr(sp, "MAX_PICKLE_BYTES", len(blob) - 1)
        with pytest.raises(sp.PayloadTooLargeError):
            sp.load_pickle(p)

    def test_table_consulted_at_load_time(self, tmp_path, monkeypatch):
        # 机制不冻结副本：替换共享常量即改变加载行为
        monkeypatch.setattr(sp, "ALLOWED_PICKLE_MODULE_ROOTS", frozenset())
        p = tmp_path / "dt.pkl"
        _write(p, datetime(2026, 1, 1))
        with pytest.raises(pickle.UnpicklingError):
            sp.load_pickle(p)
        # builtins 表同理：类引用（GLOBAL builtins.dict）也要过表
        monkeypatch.setattr(sp, "ALLOWED_PICKLE_BUILTINS", frozenset())
        q = tmp_path / "cls.pkl"
        q.write_bytes(pickle.dumps(dict))
        with pytest.raises(pickle.UnpicklingError):
            sp.load_pickle(q)

    def test_reduce_os_system_payload_rejected_and_not_executed(self, tmp_path):
        target = tmp_path / "pwned"
        p = tmp_path / "evil.pkl"
        _write(p, _Evil(target))
        with pytest.raises(pickle.UnpicklingError):
            sp.load_pickle(p)
        assert not target.exists(), "拒绝即未执行——目标文件不得出现"

    def test_builtins_eval_payload_rejected(self, tmp_path):
        p = tmp_path / "eval.pkl"
        p.write_bytes(pickle.dumps(eval))
        with pytest.raises(pickle.UnpicklingError):
            sp.load_pickle(p)


@pytest.mark.unit
class TestLoaderSitesWired:
    """五站点改造的防回归：站点必须走受限机制，劫持载荷降级且不执行。"""

    def test_launcher_load_pkl_degrades_on_hijack(self, tmp_path):
        from backend.services.engine.alpha_agent.launcher import AlphaAgentLauncher

        target = tmp_path / "pwned_launcher"
        p = tmp_path / "evil.pkl"
        _write(p, _Evil(target))
        assert AlphaAgentLauncher._load_pkl(p) is None
        assert not target.exists()

    def test_wrapper_extract_factors_skips_hijack_without_executing(self, tmp_path):
        from backend.services.engine.rd_agent.rd_loop_wrapper import RDLoopWrapper

        target = tmp_path / "pwned_wrapper"
        evil_dir = tmp_path / "Loop_0" / "coder result" / "s1"
        evil_dir.mkdir(parents=True)
        _write(evil_dir / "evil.pkl", _Evil(target))
        wrapper = RDLoopWrapper.__new__(RDLoopWrapper)  # 跳过 __init__（不取 adapter）
        wrapper.market = "a_share"
        factors = wrapper._extract_factors(str(tmp_path))
        assert factors == [], "劫持载荷按读取失败降级（无代码可提）"
        assert not target.exists()

    def test_no_raw_pickle_load_left_in_hot_sites(self):
        """静态防回归：两个热点文件不得再出现裸 ``pickle.load / loads``。"""
        import backend.services.engine.alpha_agent.launcher as launcher_mod
        import backend.services.engine.rd_agent.rd_loop_wrapper as wrapper_mod

        for mod in (launcher_mod, wrapper_mod):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert not re.search(r"pickle\.load", src), (
                f"{mod.__name__} 残留裸 pickle.load（读取站点必须走 shared.safe_pickle）"
            )
