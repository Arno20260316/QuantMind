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
import re
import shutil
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

    # 真 `host_env.py` 拷进假仓库（**不写替身**）：脚本调它解析 PG 地址，而它按自己
    # 所在目录推仓库根 → 拷进来之后读的就是假仓库的 `.env`。这样测到的是真解析器。
    for name in ("host_env.py",):
        shutil.copyfile(SCRIPT.parent / name, root / "scripts" / name)

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

    # 假池脚本：记录调用、把**当时看到的 POSTGRES_HOST** 也记下来（PG 缺省那条判据
    # 只能这么验：脚本体里看不见，只有下游进程的环境才是真凭据），并照 FAKE_PICKS_RC 退出。
    picks = root / "scripts" / "postmarket_pipeline.py"
    picks.write_text(
        textwrap.dedent(
            """\
            import os
            open(os.environ["FAKE_PICKS_CALLS"], "a").write("picks-call\\n")
            open(os.environ["FAKE_PICKS_ENV"], "w").write(
                "POSTGRES_HOST=" + os.environ.get("POSTGRES_HOST", "<unset>") + "\\n"
            )
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
        "picks_env": tmp_path / "picks.env",
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
        "FAKE_PICKS_ENV": str(calls["picks_env"]),
        "FAKE_NOTIFY_OUT": str(calls["notify"]),
    }
    for var in (
        "FAKE_ALPHA_WRITE",
        "FAKE_REFRESH_RC",
        "FAKE_DOCKER_RC",
        "FAKE_PICKS_RC",
        # 宿主环境若有这条，会盖掉「脚本自己补 POSTGRES_HOST」的观察点
        # （跑测试的人多半就在 cron 同款 shell 里）。那两条用例显式设好的值不受影响。
        "POSTGRES_HOST",
    ):
        env.pop(var, None)
    # `.env` 是 PG 缺省那条判据的**唯一事实源**（QM_DB_BIND）：默认给一份「未设」的，
    # 只有专门验它的用例才写。不写就不存在，脚本走 no-export 分支。
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

    def test_container_step_uses_container_python_not_host_path(self, rig):
        """容器内那步不许复用宿主的绝对解释器路径。

        2026-10-13 实跑事故：`quantmind` 镜像只有 `/usr/local/bin/python3`，
        脚本却把宿主用的 `/usr/bin/python3` 原样传进 `docker exec`（PYTHON_BIN
        当时是两者共用一个变量），CUSTOM 重建以
        `rc=127 exec: "/usr/bin/python3": no such file or directory` 失败，
        整条自愈链停在「alpha 已补但下游未走完」——**此前只跑过 --dry-run，
        没走过真实 docker 路径**，所以这个 127 一直没被任何测试挡住。
        """
        _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)

        docker_calls = _calls(rig, "docker")
        assert len(docker_calls) == 1
        assert "quantmind python3 -" in docker_calls[0], docker_calls[0]
        assert "/usr/bin/python3" not in docker_calls[0], (
            "容器内解释器写成了宿主的绝对路径——镜像里没有它，会 rc=127："
            f"{docker_calls[0]}"
        )


class TestPostgresHostResolution:
    """PG 地址缺省：手工调用（无 crontab 头部）时不许连回环 5432。

    2026-10-13 首次手工实跑 `postmarket_pipeline --picks-only` 撞
    `Connection refused`（127.0.0.1:5432 无监听）——crontab 头部给了 `POSTGRES_HOST`，
    手工跑没有。脚本因此要自己从 `.env` 的 `QM_DB_BIND` 取（那份就是「DB 发布到哪」的
    唯一事实源），**且不许把 IP 写死在脚本体里**（写死会在换网段时静默连错地址）。
    """

    def test_explicit_postgres_host_is_respected(self, rig):
        """显式设过 → 原封不动传下去（crontab 那条路径的行为不许被改动）。"""
        _run(
            rig,
            FAKE_ALPHA_WRITE=ALPHA_NEW,
            POSTGRES_HOST="10.9.9.9",
        )

        assert "POSTGRES_HOST=10.9.9.9" in rig["calls"]["picks_env"].read_text(
            encoding="utf-8"
        )

    def test_unset_falls_back_to_env_qm_db_bind(self, rig):
        """没设 + `.env` 有 QM_DB_BIND → 用它（脚本不该要求人工记得导出）。"""
        (rig["root"] / ".env").write_text(
            'DB_HOST=db\nQM_DB_BIND="10.1.2.3"\nQM_OTHER="x"\n', encoding="utf-8"
        )

        _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)

        assert "POSTGRES_HOST=10.1.2.3" in rig["calls"]["picks_env"].read_text(
            encoding="utf-8"
        )

    def test_loopback_qm_db_bind_is_not_exported(self, rig):
        """`.env` 里 QM_DB_BIND 就是回环 → 不 export（现状缺省，不假装知道地址）。"""
        (rig["root"] / ".env").write_text("QM_DB_BIND=127.0.0.1\n", encoding="utf-8")

        _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)

        assert "POSTGRES_HOST=<unset>" in rig["calls"]["picks_env"].read_text(
            encoding="utf-8"
        )

    def test_no_env_file_leaves_host_unset(self, rig):
        """`.env` 也没有 → 不猜，保持缺省（endpoint 变了要人工看日志）。"""
        _run(rig, FAKE_ALPHA_WRITE=ALPHA_NEW)

        assert "POSTGRES_HOST=<unset>" in rig["calls"]["picks_env"].read_text(
            encoding="utf-8"
        )

    def test_no_hardcoded_lan_ip_in_script(self):
        """脚本体里不许出现写死的 LAN IP（应经 `.env` 的 QM_DB_BIND 解析）。"""
        body = SCRIPT.read_text(encoding="utf-8")
        assert not re.search(r"192\.168\.\d+\.\d+", body), (
            "脚本里写死了 LAN IP——地址属于部署配置，应走 .env 的 QM_DB_BIND"
        )


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
