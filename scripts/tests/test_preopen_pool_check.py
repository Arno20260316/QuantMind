"""盘前补池检查脚本的单测（``scripts/preopen_pool_check.py``）。

为什么值得测：这是 2026-10-13 事故唯一能在**开盘前**喊人的那一道。它一旦把
「方向闸按设计清空」报成故障（10-12 复盘判强烈看空，健康链跑出来也是 0 候选），
值班的人几天内就会开始忽略这条告警；而反过来漏报就是又一次整天没有池。

跑法（scripts/ 是 bind mount；判据依赖 ``backend.*``，走仓库根导入）::

    python3 -m pytest scripts/tests/ -q

盯四件事：三态判据、当天去重、目标日解析（交易日历→工作日启发式的降级要**显式标注**）、
``--dry-run`` 绝不产生副作用。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根（backend.*）

import preopen_pool_check as ppc  # noqa: E402
from backend.shared.pool_readiness import (  # noqa: E402
    PoolSnapshot,
    Readiness,
    StageReport,
    assess,
    target_trade_day,
)

TARGET = "20261013"


def _report(
    *, alpha="20261012", pool=PoolSnapshot(True, True, 30, "看多", "p")
) -> Readiness:
    return assess(
        target=TARGET,
        kline_dt="20261012",
        alpha_dt=alpha,
        custom_dt="20261012",
        inference_dates={"20261012"},
        pool=pool,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把日志/去重标记目录指到 tmp，并把判据与推送换成替身。"""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(ppc, "LOG", tmp_path / "log")
    monkeypatch.setattr(ppc, "SENT_DIR", tmp_path / "sent")
    monkeypatch.setattr(
        ppc, "_send", lambda title, body, dry_run=False: sent.append((title, body))
    )
    # PG 地址解析是**宿主侧环境动作**（会写 os.environ）：测试里一律短路，
    # 免得跑测试的人的环境反过来影响用例（专测它的用例自己再覆盖）。
    monkeypatch.setattr(ppc, "_resolve_pg_host", lambda: "")
    return {"sent": sent, "tmp": tmp_path, "monkeypatch": monkeypatch}


def _run(env, argv, report):
    """跑一次 main()：固定判据替身 + 固定 argv。"""
    env["monkeypatch"].setattr(
        ppc, "_judge", lambda: (lambda **kw: report, lambda: None)
    )
    env["monkeypatch"].setattr(sys, "argv", ["preopen_pool_check.py", *argv])
    return ppc.main()


class TestProbeFailureIsDistinguished:
    """探针**没跑成** ≠ 链断了——两者在报告面上都是第 ④ 段「无」。

    2026-10-13 手工跑（宿主没设 ``POSTGRES_HOST``）就据此推了一条「候选池未就绪」，
    而链其实是通的（方向闸按设计清空）。告警必须把这两种情形分开说。
    """

    def test_probe_error_is_named_in_alert_and_log(self, env, monkeypatch):
        from backend.shared.pool_readiness import assess

        def boom(_kline_dt):
            raise RuntimeError("connection refused to 127.0.0.1:5432")

        def collect_with_real_probe(*, target=None, inference_probe=None):
            # 真 ``collect`` 会把探针异常吞成空集（这正是问题所在）——这里复刻它的
            # 消费者行为，同时让 ``_guarded_probe`` 真的包一次坏探针。
            try:
                inference_probe("20261012")
            except Exception:  # noqa: BLE001
                pass
            return assess(
                target=TARGET,
                kline_dt="20261012",
                alpha_dt="20261012",
                custom_dt="20261012",
                inference_dates=frozenset(),
                pool=PoolSnapshot(False, False, 0, "", ""),
            )

        monkeypatch.setattr(
            ppc, "_judge", lambda: (collect_with_real_probe, lambda: boom)
        )
        monkeypatch.setattr(sys, "argv", ["preopen_pool_check.py", "--target", TARGET])

        rc = ppc.main()

        assert rc == 2
        title, body = env["sent"][0]
        assert "候选池未就绪" in title
        assert "探针" in body and "没跑成" in body
        assert "不一定是链真的断了" in body
        assert "connection refused" in body

    def test_healthy_chain_with_dead_probe_says_so_in_log(
        self, env, monkeypatch, capsys
    ):
        """链按设计清空（ok）但探针是坏的 → rc=0，日志里仍要说清第 ④ 段为何是空的。"""

        def boom(_kline_dt):
            raise RuntimeError("PG down")

        def collect(*, target=None, inference_probe=None):
            try:
                inference_probe("20261012")  # 真 collect 会这么调，异常被它吞掉
            except Exception:  # noqa: BLE001
                pass
            return _report()

        env["monkeypatch"].setattr(ppc, "_judge", lambda: (collect, lambda: boom))
        env["monkeypatch"].setattr(
            sys, "argv", ["preopen_pool_check.py", "--target", TARGET]
        )

        assert ppc.main() == 0
        out = capsys.readouterr().out
        assert "探针" in out and "PG down" in out


class TestThreeStates:
    def test_healthy_pool_is_silent(self, env):
        rc = _run(env, ["--target", TARGET], _report())
        assert rc == 0
        assert env["sent"] == []

    def test_bearish_clearing_is_silent(self, env):
        """方向闸清空 = 正常，**不推**（推了就是天天误报）。"""
        report = _report(pool=PoolSnapshot(True, True, 0, "强烈看空", "p"))
        rc = _run(env, ["--target", TARGET], report)
        assert rc == 0
        assert env["sent"] == []

    def test_alpha_break_alerts_with_blocked_stage(self, env):
        rc = _run(env, ["--target", TARGET], _report(alpha="20261009"))
        assert rc == 2
        assert len(env["sent"]) == 1
        title, body = env["sent"][0]
        assert "候选池未就绪" in title
        assert "alpha 因子库" in body
        assert "20261013" in body

    def test_missing_pool_file_alerts(self, env):
        rc = _run(
            env,
            ["--target", TARGET],
            _report(pool=PoolSnapshot(False, False, 0, "", "")),
        )
        assert rc == 2
        assert len(env["sent"]) == 1

    def test_zero_rows_with_bullish_direction_alerts(self, env):
        """0 候选 + 看多 = 真故障（不是方向闸的锅）。"""
        rc = _run(
            env,
            ["--target", TARGET],
            _report(pool=PoolSnapshot(True, True, 0, "看多", "p")),
        )
        assert rc == 2
        assert len(env["sent"]) == 1


class TestDedupe:
    def test_alerts_once_per_target_day(self, env):
        assert _run(env, ["--target", TARGET], _report(alpha="20261009")) == 2
        assert len(env["sent"]) == 1
        # 第二次（同一目标日）只记日志
        assert _run(env, ["--target", TARGET], _report(alpha="20261009")) == 0
        assert len(env["sent"]) == 1

    def test_force_bypasses_dedupe(self, env):
        _run(env, ["--target", TARGET], _report(alpha="20261009"))
        assert (
            _run(env, ["--target", TARGET, "--force"], _report(alpha="20261009")) == 2
        )
        assert len(env["sent"]) == 2

    def test_healthy_day_does_not_write_dedupe_flag(self, env):
        _run(env, ["--target", TARGET], _report())
        assert not (env["tmp"] / "sent" / f"{TARGET}.flag").exists()


class TestDryRun:
    def test_dry_run_does_not_send_or_mark(self, env, monkeypatch):
        sent: list = []
        monkeypatch.setattr(
            ppc,
            "_send",
            lambda title, body, dry_run=False: sent.append((title, dry_run)),
        )
        rc = _run(env, ["--target", TARGET, "--dry-run"], _report(alpha="20261009"))
        assert rc == 2
        # dry-run 时 _send 被调用但 dry_run=True，且**不写**去重标记
        assert sent and sent[0][1] is True
        assert not (env["tmp"] / "sent" / f"{TARGET}.flag").exists()

    def test_dry_run_keeps_alerting(self, env):
        _run(env, ["--target", TARGET, "--dry-run"], _report(alpha="20261009"))
        assert (
            _run(env, ["--target", TARGET, "--dry-run"], _report(alpha="20261009")) == 2
        )


class TestJudgeFailure:
    def test_judge_exception_alerts_itself(self, env):
        """判据本身炸了也是事故：必须喊人，且不假装链通。"""

        def boom(**kw):
            raise RuntimeError("disk gone")

        env["monkeypatch"].setattr(ppc, "_judge", lambda: (boom, lambda: None))
        env["monkeypatch"].setattr(sys, "argv", ["preopen_pool_check.py"])
        assert ppc.main() == 1
        assert len(env["sent"]) == 1
        assert "disk gone" in env["sent"][0][1]


class TestTargetTradeDay:
    def test_trading_day_maps_to_itself(self):
        td = target_trade_day(date(2026, 10, 13))  # 周二
        assert td.day == "20261013"
        assert td.source
        assert td.degraded is False

    def test_weekend_maps_to_next_trading_day(self):
        td = target_trade_day(date(2026, 10, 17))  # 周六
        assert td.day == "20261019"  # 周一
        assert td.degraded is False

    def test_heuristic_fallback_is_flagged_degraded(self, monkeypatch):
        """日历全不可用 → 工作日启发式，且**必须**标 degraded（长假会判错）。

        日历模块可能已被前面的用例缓存进 ``sys.modules``——只改 ``__import__`` 拦不住
        （拿到的是缓存模块），得同时清缓存、拦 ``importlib.import_module``。
        """
        import builtins
        import importlib

        for name in ("backend.shared.trading_calendar", "scripts.trading_cal"):
            sys.modules.pop(name, None)

        real_import = builtins.__import__
        real_import_module = importlib.import_module

        def fake_import(name, *a, **kw):
            if "trading_calendar" in name or name.startswith("scripts."):
                raise ImportError(f"blocked {name}")
            return real_import(name, *a, **kw)

        def fake_import_module(name, *a, **kw):
            if name.startswith("scripts."):
                raise ImportError(f"blocked {name}")
            return real_import_module(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        td = target_trade_day(date(2026, 10, 17))  # 周六
        assert td.day == "20261019"
        assert td.degraded is True
        assert td.source == "weekday-heuristic"
