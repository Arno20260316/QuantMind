"""T-MV-09 自动验证启动器（``validate_spawn.spawn_validation``）契约测试。

钉的是两个 runner（LLM 派/统计派）共享的启动口径：

- ``persisted <= 0`` 不启动（无对象可验）；开关 ``env_flag`` 走全仓唯一读法
  （``backend.shared.env_flags``）：缺省=开，**只有小写 ``true`` 判开**，
  ``0``/``false``/``1``/``yes``/``on``/空串一律判关（不许第二种读法）；
- 验证器脚本缺失、Popen 失败、log 目录缺失——一律**告警不抛**（自动链
  失败不阻断挖掘结果），返回 False；
- ``--h5-path`` 只在路径**真实存在**时附（统计派无 h5，绝不附空路径）；
- Popen 的接线：``cwd=仓库根``、``start_new_session=True``、
  stdout/stderr 都进 ``validate.log``（append，重跑留历史）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import validate_spawn as vsp  # noqa: E402

pytestmark = pytest.mark.unit


def _make_root(tmp_path: Path) -> Path:
    """最小仓库根：只放验证器脚本（spawn 只检查这一个文件面）。"""
    root = tmp_path / "repo"
    scripts = root / "backend" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "mining_factor_validate.py").write_text("# stub\n")
    return root


@pytest.fixture
def popen_recorder(monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    def _fake(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return object()  # 不关心 Popen 句柄（start_new_session 即脱离）

    monkeypatch.setattr(subprocess, "Popen", _fake)
    return calls


@pytest.fixture
def spawned_env(monkeypatch):
    monkeypatch.setenv("RD_AGENT_AUTO_VALIDATE", "true")


class TestGuards:
    def test_persisted_zero_returns_false(self, spawned_env, tmp_path, popen_recorder):
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path,
                persisted=0,
                project_root=_make_root(tmp_path),
            )
            is False
        )
        assert popen_recorder == [], "没落库任何因子 → 不启动（无对象可验）"

    # ⚠️ 1/yes/on 在本仓口径下**不是开**（env_flags 唯一读法），故「设了 1」= 关；
    # 历史分歧值（""/1/yes/on）全部钉成关——防有人照 pydantic 口径改回宽松词表
    @pytest.mark.parametrize(
        "flag", ["0", "false", "FALSE", " no ", "off", "", "1", "yes", "on"]
    )
    def test_off_flag_returns_false(self, monkeypatch, tmp_path, popen_recorder, flag):
        monkeypatch.setenv("RD_AGENT_AUTO_VALIDATE", flag)
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path,
                persisted=3,
                project_root=_make_root(tmp_path),
            )
            is False
        )
        assert popen_recorder == []

    def test_flag_absent_defaults_to_on(self, monkeypatch, tmp_path, popen_recorder):
        monkeypatch.delenv("RD_AGENT_AUTO_VALIDATE", raising=False)
        assert vsp.spawn_validation(
            task_id="t1",
            market="a_share",
            log_dir=tmp_path,
            persisted=3,
            project_root=_make_root(tmp_path),
        )
        assert len(popen_recorder) == 1, "缺省=开（不设置即走 default=True）"

    def test_custom_env_flag_controls_switch(
        self, monkeypatch, tmp_path, popen_recorder
    ):
        monkeypatch.setenv("MY_AUTO_VALIDATE", "off")
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path,
                persisted=1,
                env_flag="MY_AUTO_VALIDATE",
                project_root=_make_root(tmp_path),
            )
            is False
        )
        assert popen_recorder == []

    def test_missing_script_returns_false(self, spawned_env, tmp_path, popen_recorder):
        """:project_root 里没有验证器脚本 → False 且不抛。"""
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path,
                persisted=2,
                project_root=empty_root,
            )
            is False
        )
        assert popen_recorder == []

    def test_popen_failure_returns_false(self, spawned_env, tmp_path, monkeypatch):
        def _boom(cmd, **kwargs):
            raise OSError("no fork for you")

        monkeypatch.setattr(subprocess, "Popen", _boom)
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path,
                persisted=2,
                project_root=_make_root(tmp_path),
            )
            is False
        ), "自动链失败不阻断挖掘结果——只告警"

    def test_missing_log_dir_returns_false(self, spawned_env, tmp_path, popen_recorder):
        assert (
            vsp.spawn_validation(
                task_id="t1",
                market="a_share",
                log_dir=tmp_path / "no_such",
                persisted=2,
                project_root=_make_root(tmp_path),
            )
            is False
        )
        assert popen_recorder == [], "log 目录都开不了 → 不当启动成功"


class TestSpawn:
    def test_spawns_with_expected_cmd_and_wiring(
        self, spawned_env, tmp_path, popen_recorder
    ):
        root = _make_root(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        assert vsp.spawn_validation(
            task_id="t-42",
            market="a_share",
            log_dir=log_dir,
            persisted=2,
            project_root=root,
        )

        ((cmd, kwargs),) = popen_recorder
        script = root / "backend" / "scripts" / "mining_factor_validate.py"
        assert cmd[0] == sys.executable
        assert cmd[1] == str(script)
        assert cmd[2:] == ["--task-id", "t-42", "--market", "a_share"]
        assert kwargs["cwd"] == str(root)
        assert kwargs["start_new_session"] is True, "后台脱离父进程会话"
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["stdout"].mode == "ab", "validate.log append（重跑留历史）"
        assert (log_dir / "validate.log").exists()

    def test_h5_path_attached_only_when_exists(
        self, spawned_env, tmp_path, popen_recorder
    ):
        root = _make_root(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        h5 = tmp_path / "factor.h5"
        h5.write_bytes(b"h5")

        assert vsp.spawn_validation(
            task_id="t-43",
            market="a_share",
            log_dir=log_dir,
            persisted=1,
            h5_path=str(h5),
            project_root=root,
        )
        ((cmd, _kwargs),) = popen_recorder
        assert cmd[-2:] == ["--h5-path", str(h5)]

        popen_recorder.clear()
        assert vsp.spawn_validation(
            task_id="t-44",
            market="a_share",
            log_dir=log_dir,
            persisted=1,
            h5_path=str(tmp_path / "gone.h5"),
            project_root=root,
        )
        ((cmd2, _kwargs2),) = popen_recorder
        assert "--h5-path" not in cmd2, (
            "不存在的 h5 绝不附（统计派无 h5 的空路径不许骗验证器）"
        )

    def test_log_appends_not_truncates(self, spawned_env, tmp_path, popen_recorder):
        root = _make_root(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "validate.log"
        log_file.write_bytes(b"older run\n")

        assert vsp.spawn_validation(
            task_id="t-45",
            market="a_share",
            log_dir=log_dir,
            persisted=1,
            project_root=root,
        )
        assert log_file.read_bytes() == b"older run\n"
