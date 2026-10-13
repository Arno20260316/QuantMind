"""alpha 重试脚本的行为测试（``scripts/alpha_refresh_retry.sh``）。

为什么值得测：这是 2026-10-13 事故的**唯一自动**补救面。它跑在凌晨、没人看着，
所以「该不该动手」和「补完有没有真的把下游接上」这两件事都不许靠肉眼确认。
两种失败形态最要命，各锁一组用例：

* **假设性自愈**：alpha 补新了但 CUSTOM / 备选池没接上 → 报告说「自愈完成」而池照样缺
  （2026-10-13 的成因之一就是「以为链路会自愈」）；
* **空跑却喊人**：链本来就好却推 QQ → 值班的人几天内就会开始忽略这条告警。

跑法（脚本是纯 bash，测试用假 docker / 假刷新脚本 / 假池脚本替换掉所有外部依赖）::

    python3 -m pytest scripts/tests/test_alpha_refresh_retry.py -q

不连 PG、不碰真数据、不真的推 QQ。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "alpha_refresh_retry.sh"

KLINE_DT = "20261012"
ALPHA_OLD = "20261009"
ALPHA_NEW = "20261012"


@pytest.fixture
def rig(tmp_path):
    """搭一套假仓库：假分区目录 + 假刷新脚本 + 假 docker + 假池脚本 + 假推送。"""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "logs" / "keepers").mkdir(parents=True)
    (
        root / "data" / "quantdb" / "1_kline_data" / "daily_forward" / f"dt={KLINE_DT}"
    ).mkdir(parents=True)
    (
        root
        / "data"
        / "quantdb"
        / "6_ml_datasets"
        / "alpha_library"
        / f"dt={ALPHA_OLD}"
    ).mkdir(parents=True)

    alpha_root = root / "data" / "quantdb" / "6_ml_datasets" / "alpha_library"
    bins = tmp_path / "bin"
    bins.mkdir()

    # 假刷新脚本：把「被调用」记进文件，并按 FAKE_ALPHA_WRITE 决定是否补出新分区。
    refresh = root / "scripts" / "alpha_library_refresh.sh"
    refresh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            echo "refresh-call" >> "$FAKE_REFRESH_CALLS"
            if [ -n "${FAKE_ALPHA_WRITE:-}" ]; then
                mkdir -p "$QM_ROOT/data/quantdb/6_ml_datasets/alpha_library/dt=$FAKE_ALPHA_WRITE"
            fi
            exit "${FAKE_REFRESH_RC:-0}"
            """
        ),
        encoding="utf-8",
    )
    refresh.chmod(0o755)

    # 假 docker：吞掉 heredoc，记录调用，并按 FAKE_DOCKER_RC 决定成败。
    docker = bins / "docker"
    docker.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            cat > /dev/null
            echo "docker-call $*" >> "$FAKE_DOCKER_CALLS"
            echo "OK: CUSTOM rebuild mode=incremental written=1 n_factors=1456 elapsed=3.2s libs={'alpha360': 1}"
            exit "${FAKE_DOCKER_RC:-0}"
            """
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)

    # 假池脚本：记录调用并照 FAKE_PICKS_RC 退出。
    picks = root / "scripts" / "postmarket_pipeline.py"
    picks.write_text(
        textwrap.dedent(
            """\
            import os
            open(os.environ["FAKE_PICKS_CALLS"], "a").write("picks-call\\n")
            raise SystemExit(int(os.environ.get("FAKE_PICKS_RC", "0")))
            """
        ),
        encoding="utf-8",
    )

    # 假 push_notify：把标题记进文件（真 push_notify 要凭据，测试不许碰）。
    notify = root / "scripts" / "push_notify.py"
    notify.write_text(
        textwrap.dedent(
            """\
            import os
            def notify(title, content):
                with open(os.environ["FAKE_NOTIFY_OUT"], "a", encoding="utf-8") as f:
                    f.write(title + "\\n")
            """
        ),
        encoding="utf-8",
    )

    calls = {
        "refresh": tmp_path / "refresh.calls",
        "docker": tmp_path / "docker.calls",
        "picks": tmp_path / "picks.calls",
        "notify": tmp_path / "notify.calls",
        "log": root / "logs" / "keepers" / "alpha_refresh_retry.log",
    }
    env = {
        **os.environ,
        "QM_ROOT": str(root),
        "PYTHON_BIN": sys.executable,
        "DOCKER_BIN": str(docker),
        "QM_RETRY_LOCK": str(tmp_path / "retry.lock"),
        "FAKE_REFRESH_CALLS": str(calls["refresh"]),
        "FAKE_DOCKER_CALLS": str(calls["docker"]),
        "FAKE_PICKS_CALLS": str(calls["picks"]),
        "FAKE_NOTIFY_OUT": str(calls["notify"]),
    }
    for var in (
        "FAKE_ALPHA_WRITE",
        "FAKE_REFRESH_RC",
        "FAKE_DOCKER_RC",
        "FAKE_PICKS_RC",
    ):
        env.pop(var, None)
    return {
        "root": root,
        "alpha_root": alpha_root,
        "calls": calls,
        "env": env,
        "tmp": tmp_path,
    }


def _run(rig, *args, **overrides):
    env = {**rig["env"], **overrides}
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc


def _calls(rig, name) -> list[str]:
    """假脚本每次调用**写一行**；按行取（`split()` 会把一整行参数拆开）。"""
    p = rig["calls"][name]
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _notifies(rig) -> list[str]:
    p = rig["calls"]["notify"]
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


class TestNoActionNeeded:
    def test_already_latest_exits_quietly(self, rig):
        """alpha 已到日K 那天 → 不调刷新、不接下游、不推 QQ。"""
        (rig["alpha_root"] / f"dt={ALPHA_NEW}").mkdir()

        proc = _run(rig)

        assert proc.returncode == 0
        assert not rig["calls"]["refresh"].exists()
        assert not rig["calls"]["docker"].exists()
        assert not rig["calls"]["picks"].exists()
        assert _notifies(rig) == []
        assert "无事：已最新" in proc.stdout or "无事：已最新" in rig["calls"][
            "log"
        ].read_text(encoding="utf-8")

    def test_empty_kline_partitions_exits_quietly(self, rig, tmp_path):
        """日K 分区目录为空 → 无从判断，静默退出（不猜、不刷）。"""
        for d in (
            rig["root"] / "data" / "quantdb" / "1_kline_data" / "daily_forward"
        ).iterdir():
            d.rmdir()

        proc = _run(rig)

        assert proc.returncode == 0
        assert not rig["calls"]["refresh"].exists()
        assert _notifies(rig) == []

    def test_dry_run_does_not_refresh(self, rig):
        """--dry-run：判断照做、动作全免（含下游与推送）。"""
        proc = _run(rig, "--dry-run")

        assert proc.returncode == 0
        assert not rig["calls"]["refresh"].exists()
        assert not rig["calls"]["docker"].exists()
        assert _notifies(rig) == []


class TestSelfHealHappyPath:
    def test_refresh_then_downstream_then_green_notice(self, rig):
        """补新成功 → CUSTOM 重建 → 池重算 → 🟢 一条。**缺任何一步都不算自愈**。"""
        proc = _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)

        assert proc.returncode == 0
        assert _calls(rig, "refresh") == ["refresh-call"]
        assert len(_calls(rig, "docker")) == 1
        assert _calls(rig, "picks") == ["picks-call"]
        assert len(_notifies(rig)) == 1
        assert "🟢" in _notifies(rig)[0]

    def test_downstream_failure_is_not_reported_as_healed(self, rig):
        """CUSTOM 重建失败 → 不许说「自愈完成」，退非零并推 🟠。"""
        proc = _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW, FAKE_DOCKER_RC="1")

        assert proc.returncode == 1
        assert _calls(rig, "picks") == [
            "picks-call"
        ]  # 池仍要试（CUSTOM 失败不必然拦池）
        titles = _notifies(rig)
        assert len(titles) == 1
        assert "🟠" in titles[0] and "🟢" not in titles[0]

    def test_picks_failure_is_not_reported_as_healed(self, rig):
        proc = _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW, FAKE_PICKS_RC="3")

        assert proc.returncode == 1
        assert "🟠" in _notifies(rig)[0]


class TestRefreshStillStale:
    def test_stale_after_refresh_logs_only_by_default(self, rig):
        """刷新跑完仍滞后（内存不够等）→ 默认只记日志，不推（盘前告警自有其人）。"""
        proc = _run(rig)  # 假刷新脚本不写新分区

        assert proc.returncode == 1
        assert _calls(rig, "refresh") == ["refresh-call"]
        assert not rig["calls"]["docker"].exists()
        assert _notifies(rig) == []
        assert "alpha 仍滞后" in rig["calls"]["log"].read_text(encoding="utf-8")

    def test_final_slot_alerts_on_failure(self, rig):
        """最后一档（QM_RETRY_FINAL=1）失败要喊人——否则盘前才发现。"""
        proc = _run(rig, QM_RETRY_FINAL="1")

        assert proc.returncode == 1
        assert len(_notifies(rig)) == 1
        assert "🟠" in _notifies(rig)[0]

    def test_notify_can_be_muted(self, rig):
        proc = _run(rig, QM_RETRY_FINAL="1", QM_RETRY_NOTIFY="0")

        assert proc.returncode == 1
        assert _notifies(rig) == []


class TestReentrancy:
    def test_second_instance_skips_when_lock_held(self, rig):
        """两档 cron 撞在一个还没跑完的窗口 → 后到的必须让位，不能并发去抢内存。"""
        lock = rig["env"]["QM_RETRY_LOCK"]
        env = {**rig["env"], "FAKE_ALPHA_WRITE": ALPHA_NEW}

        def holder():
            return subprocess.run(
                ["flock", lock, "bash", "-c", "sleep 3"],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

        with ThreadPoolExecutor() as pool:
            fut = pool.submit(holder)
            import time

            time.sleep(0.8)  # 让 holder 先拿到锁
            proc = _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)
            fut.result()

        assert proc.returncode == 0
        assert not rig["calls"]["refresh"].exists()
        assert "上一次重试仍在运行" in rig["calls"]["log"].read_text(encoding="utf-8")


class TestScriptShape:
    def test_is_valid_bash(self):
        proc = subprocess.run(
            ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
