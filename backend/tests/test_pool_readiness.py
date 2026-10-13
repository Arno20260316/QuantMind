"""候选池就绪判据的单测（``backend/shared/pool_readiness.py``）。

这条判据有三个读者（宿主盘前告警、后台状态卡接口、排障的人），三处各写一份必然漂移，
所以判据只此一份。本测试盯三件事：

* **段序与首断点**：五段固定顺序，``blocked_stage`` 是**第一个**不就绪的段——
  只报「数据集旧了」不够，2026-10-13 实锤是 alpha 缺分区 → CUSTOM fail-closed 拒写，
  两段必须各自可见；
* **「断链」与「按设计清空」不许混**：方向闸判看空 → 池文件在、0 行，这是**正常**的
  （10-12 复盘「强烈看空 -4.8/11」），报成故障会天天误报，告警随即被忽略；
* **缺就缺，不许谎报**：上游没数据时下游段点不能说「就绪」。
"""

from __future__ import annotations

from pathlib import Path

from backend.shared.pool_readiness import (
    STAGE_KEYS,
    PoolSnapshot,
    assess,
    is_bearish_direction,
    latest_partition,
    pool_snapshot,
)

TARGET = "20261013"
KLINE = "20261012"


def _assess(
    *,
    kline=KLINE,
    alpha=KLINE,
    custom=KLINE,
    inference=("20261009", KLINE),
    pool=PoolSnapshot(
        exists=True, readable=True, rows=30, direction="看多", source="p"
    ),
):
    return assess(
        target=TARGET,
        kline_dt=kline,
        alpha_dt=alpha,
        custom_dt=custom,
        inference_dates=frozenset(inference),
        pool=pool,
    )


def _stage(report, key):
    return next(s for s in report.stages if s.key == key)


class TestStageOrder:
    def test_stage_keys_are_fixed_and_complete(self):
        """段序是契约：前端/告警文案按它排版，顺序变了两个读者都会错位。"""
        report = _assess()
        assert tuple(s.key for s in report.stages) == STAGE_KEYS
        assert STAGE_KEYS == (
            "kline",
            "alpha_library",
            "custom_dataset",
            "inference",
            "pool",
        )


class TestHealthy:
    def test_full_chain_is_ok_with_no_blocker(self):
        report = _assess()
        assert report.ok is True
        assert report.blocked_stage is None
        assert report.by_design is False
        assert all(s.ok for s in report.stages)
        assert report.target == TARGET
        assert report.kline_dt == KLINE


class TestFirstBlocker:
    def test_alpha_behind_blocks_at_alpha_not_custom(self):
        """2026-10-13 的形状：alpha 缺 20261012。首断点必须是 alpha_library。"""
        report = _assess(alpha="20261009")
        assert report.blocked_stage == "alpha_library"
        assert _stage(report, "alpha_library").ok is False
        assert "20261009" in _stage(report, "alpha_library").detail
        assert report.ok is False

    def test_custom_behind_blocks_at_custom_when_alpha_fresh(self):
        report = _assess(custom="20261009")
        assert report.blocked_stage == "custom_dataset"
        assert _stage(report, "alpha_library").ok is True

    def test_missing_inference_batch_blocks_at_inference(self):
        """推理批按 data_trade_date 精确匹配（pick_candidates 就是这么查的）。"""
        report = _assess(inference=("20261009",))
        assert report.blocked_stage == "inference"
        assert _stage(report, "inference").ok is False

    def test_no_inference_at_all_blocks_at_inference(self):
        report = _assess(inference=())
        assert report.blocked_stage == "inference"

    def test_missing_pool_file_blocks_at_pool(self):
        report = _assess(pool=PoolSnapshot(False, False, 0, "", ""))
        assert report.blocked_stage == "pool"
        assert "缺" in _stage(report, "pool").detail

    def test_missing_kline_partition_blocks_at_kline_and_does_not_claim_ready(self):
        """上游没有日K时，下游段不能说自己就绪（链是串行的，别填假数据）。"""
        report = _assess(kline=None)
        assert report.blocked_stage == "kline"
        assert report.ok is False
        assert _stage(report, "alpha_library").ok is False
        assert _stage(report, "alpha_library").detail.endswith("（上游未就绪）")
        assert _stage(report, "pool").ok is False

    def test_one_missing_upstream_stage_marks_every_later_stage(self):
        """中间一段断了，它之后的**每一段**都要落成不就绪——否则会出现
        「CUSTOM ✓ 而推理 ✗」这种自相矛盾的链条。"""
        report = _assess(alpha="20261009")
        assert _stage(report, "alpha_library").ok is False
        for key in ("custom_dataset", "inference", "pool"):
            assert _stage(report, key).ok is False, key

    def test_by_design_not_claimed_when_chain_is_broken(self):
        """链中段断了、池文件恰好是方向闸残留时，不许说「按设计清空」。"""
        report = _assess(
            alpha="20261009", pool=PoolSnapshot(True, True, 0, "看空", "p")
        )
        assert report.by_design is False
        assert report.blocked_stage == "alpha_library"


class TestByDesignClearing:
    def test_bearish_clearing_is_not_a_break(self):
        report = _assess(
            pool=PoolSnapshot(True, True, 0, "强烈看空", "/x/20261013_picks.json")
        )
        assert report.by_design is True
        assert report.ok is True
        assert report.blocked_stage is None
        assert _stage(report, "pool").by_design is True

    def test_zero_rows_without_bearish_direction_is_a_break(self):
        report = _assess(pool=PoolSnapshot(True, True, 0, "看多", "p"))
        assert report.by_design is False
        assert report.blocked_stage == "pool"

    def test_unreadable_pool_file_is_a_break_not_a_clearing(self):
        report = _assess(pool=PoolSnapshot(True, False, 0, "", "p"))
        assert report.by_design is False
        assert report.blocked_stage == "pool"

    def test_is_bearish_direction_matches_strong_and_plain_only(self):
        assert is_bearish_direction("看空") is True
        assert is_bearish_direction("强烈看空") is True
        assert is_bearish_direction("复盘判强烈看空（-3.95/11）→ 清空选股") is True
        assert is_bearish_direction("看多") is False
        assert is_bearish_direction("震荡") is False
        assert is_bearish_direction("—") is False
        assert is_bearish_direction("") is False


class TestLatestPartition:
    def test_picks_max_dt_and_ignores_other_dirs(self, tmp_path: Path):
        root = tmp_path / "alpha_library"
        root.mkdir()
        for name in ("dt=20261008", "dt=20261009", "dt=20261012", "report"):
            (root / name).mkdir()
        (root / "meta.json").write_text("{}", encoding="utf-8")
        assert latest_partition(root) == "20261012"

    def test_missing_root_returns_none(self, tmp_path: Path):
        assert latest_partition(tmp_path / "nope") is None

    def test_empty_root_returns_none(self, tmp_path: Path):
        root = tmp_path / "empty"
        root.mkdir()
        assert latest_partition(root) is None


class TestPoolSnapshot:
    def test_absent_file(self, tmp_path: Path):
        snap = pool_snapshot(TARGET, reports_dir=tmp_path)
        assert snap.exists is False
        assert snap.readable is False
        assert snap.rows == 0

    def test_reads_candidates_generation_and_direction(self, tmp_path: Path):
        picks = tmp_path / "stock_picks"
        picks.mkdir()
        (picks / f"{TARGET}_picks.json").write_text(
            '{"buy_date": "2026-10-13", "candidates": [{"symbol": "600036"}],'
            ' "market_direction": {"direction": "看多", "total_score": 2.5}}',
            encoding="utf-8",
        )
        snap = pool_snapshot(TARGET, reports_dir=tmp_path)
        assert snap.exists is True
        assert snap.readable is True
        assert snap.rows == 1
        assert snap.direction == "看多"

    def test_cleared_pool_falls_back_to_gate_text(self, tmp_path: Path):
        """老池没有 market_direction 时，方向从 gate 文案里取（同一套「看空」子串）。"""
        picks = tmp_path / "stock_picks"
        picks.mkdir()
        (picks / f"{TARGET}_picks.json").write_text(
            '{"candidates": [], "gate": "复盘判看空（-2.5/11，置信★★★★★）→ 清空选股，不推荐"}',
            encoding="utf-8",
        )
        snap = pool_snapshot(TARGET, reports_dir=tmp_path)
        assert snap.exists is True
        assert snap.rows == 0
        assert is_bearish_direction(snap.direction) is True

    def test_unreadable_file_is_marked_not_absent(self, tmp_path: Path):
        picks = tmp_path / "stock_picks"
        picks.mkdir()
        (picks / f"{TARGET}_picks.json").write_text("{not json", encoding="utf-8")
        snap = pool_snapshot(TARGET, reports_dir=tmp_path)
        assert snap.exists is True
        assert snap.readable is False


# ── 事实收集 ───────────────────────────────────────────────────────────
# collect 是「盘前告警脚本」与「后台状态卡」共用的取数口。两种坏法都要锁：**谎报就绪**
# （探针炸了却算通过）和**日期判错**（日期来源不进报告面，人按错的日期去追池文件）。


def _write_partition(root: Path, rel: str, *days: str) -> None:
    for d in days:
        (root / rel / f"dt={d}").mkdir(parents=True, exist_ok=True)


def _write_pool(reports: Path, day: str, body: str) -> None:
    picks = reports / "stock_picks"
    picks.mkdir(parents=True, exist_ok=True)
    (picks / f"{day}_picks.json").write_text(body, encoding="utf-8")


class TestCollect:
    def test_healthy_chain_from_disk_and_probe(self, tmp_path: Path):
        from backend.shared.pool_readiness import collect

        qdb, cst, rep = tmp_path / "qdb", tmp_path / "cst", tmp_path / "rep"
        _write_partition(qdb, "1_kline_data/daily_forward", KLINE)
        _write_partition(qdb, "6_ml_datasets/alpha_library", KLINE)
        _write_partition(cst, "6_ml_datasets/l1_factors", KLINE)
        _write_pool(rep, TARGET, '{"candidates": [{"symbol": "600036"}]}')

        report = collect(
            target=TARGET,
            data_root=qdb,
            custom_root=cst,
            reports_dir=rep,
            inference_probe=lambda _d: {KLINE},
        )

        assert report.ok is True
        assert report.blocked_stage is None
        assert report.kline_dt == KLINE
        assert report.target_source == "explicit"

    def test_exploding_probe_is_not_counted_as_ready(self, tmp_path: Path):
        """探针抛异常 ≠ 有批次——**不许**静默算通过（本仓反复出事的形态）。"""
        from backend.shared.pool_readiness import collect

        qdb, cst, rep = tmp_path / "qdb", tmp_path / "cst", tmp_path / "rep"
        _write_partition(qdb, "1_kline_data/daily_forward", KLINE)
        _write_partition(qdb, "6_ml_datasets/alpha_library", KLINE)
        _write_partition(cst, "6_ml_datasets/l1_factors", KLINE)
        _write_pool(rep, TARGET, '{"candidates": [{"symbol": "600036"}]}')

        def boom(_d):
            raise RuntimeError("pg down")

        report = collect(
            target=TARGET,
            data_root=qdb,
            custom_root=cst,
            reports_dir=rep,
            inference_probe=boom,
        )

        assert report.ok is False
        assert report.blocked_stage == "inference"
        assert "（上游未就绪）" not in _stage(report, "inference").detail

    def test_no_probe_means_inference_stage_unavailable(self, tmp_path: Path):
        from backend.shared.pool_readiness import collect

        qdb, cst, rep = tmp_path / "qdb", tmp_path / "cst", tmp_path / "rep"
        _write_partition(qdb, "1_kline_data/daily_forward", KLINE)
        _write_partition(qdb, "6_ml_datasets/alpha_library", KLINE)
        _write_partition(cst, "6_ml_datasets/l1_factors", KLINE)
        _write_pool(rep, TARGET, '{"candidates": [{"symbol": "600036"}]}')

        report = collect(target=TARGET, data_root=qdb, custom_root=cst, reports_dir=rep)
        assert report.blocked_stage == "inference"

    def test_derived_target_day_is_recorded_with_its_source(self, tmp_path: Path):
        """不给 target → 按日历推，**并把「谁定的这个日期」写进报告面**。"""
        from backend.shared.pool_readiness import collect

        qdb, cst, rep = tmp_path / "qdb", tmp_path / "cst", tmp_path / "rep"
        report = collect(data_root=qdb, custom_root=cst, reports_dir=rep)

        assert report.target_source != "explicit"
        assert report.target_source  # 非空：人得知道该不该信这一次判定
        assert len(report.target) == 8
        assert report.blocked_stage == "kline"  # 空目录 → 第一段就断


class TestTargetTradeDay:
    def test_weekend_rolls_forward_and_is_not_degraded_when_calendar_works(self):
        from datetime import date

        from backend.shared.pool_readiness import target_trade_day

        td = target_trade_day(date(2026, 10, 17))  # 周六
        assert td.day == "20261019"  # 周一
        assert td.degraded is False
        assert td.source != "weekday-heuristic"

    def test_heuristic_fallback_is_flagged_degraded(self, monkeypatch):
        """日历全不可用 → 工作日启发式，且**必须**标 degraded（长假会判错）。"""
        import builtins
        import importlib
        import sys
        from datetime import date

        from backend.shared.pool_readiness import target_trade_day

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


class TestPgInferenceProbe:
    def test_probe_normalizes_dates_and_closes_connection(self):
        from backend.shared.pool_readiness import pg_inference_probe

        state = {"closed": False, "sql": ""}

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql):
                state["sql"] = sql

            def fetchall(self):
                return [("2026-10-12",), ("2026-10-09",), ("2026-10-12",)]

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                state["closed"] = True

        probe = pg_inference_probe(connect=lambda: _Conn())
        got = probe(KLINE)

        assert got == {"20261012", "20261009"}
        assert state["closed"] is True
        assert "status = 'completed'" in state["sql"]

    def test_probe_closes_connection_even_when_query_fails(self):
        """查挂了也必须关连接——宿主 cron 每天跑，漏一条连接就是慢性耗尽。"""
        import pytest

        from backend.shared.pool_readiness import pg_inference_probe

        state = {"closed": False}

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, _sql):
                raise RuntimeError("boom")

            def fetchall(self):
                return []

        class _Conn:
            def cursor(self):
                return _Cur()

            def close(self):
                state["closed"] = True

        probe = pg_inference_probe(connect=lambda: _Conn())
        with pytest.raises(RuntimeError):
            probe(KLINE)
        assert state["closed"] is True


class TestRootsAndPathResolution:
    def test_explicit_root_wins(self, tmp_path: Path):
        from backend.shared.pool_readiness import quantcustom_root, quantdb_root

        assert quantdb_root(tmp_path / "a") == tmp_path / "a"
        assert quantcustom_root(tmp_path / "b") == tmp_path / "b"

    def test_env_override_used_when_no_explicit_root(self, tmp_path: Path, monkeypatch):
        from backend.shared.pool_readiness import quantdb_root

        monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "env-qdb"))
        assert quantdb_root() == tmp_path / "env-qdb"

    def test_pool_snapshot_without_reports_dir_uses_shared_pool_path(self, monkeypatch):
        """不给 reports_dir → 走 decision_context_source.pool_path（唯一出处），
        而不是本模块再写一套后缀表。"""
        from backend.shared.pool_readiness import pool_snapshot

        called = {}

        def fake_pool_path(day):
            called["day"] = day
            return None

        monkeypatch.setattr(
            "backend.shared.decision_context_source.pool_path", fake_pool_path
        )
        snap = pool_snapshot(TARGET)
        assert called["day"] == TARGET
        assert snap.exists is False

    def test_pool_snapshot_legacy_picks_key_and_agent_suffix(self, tmp_path: Path):
        """两代 schema / 两种后缀都要认：只认一种会把在产的池静默读成空池。"""
        picks = tmp_path / "stock_picks"
        picks.mkdir()
        (picks / f"{TARGET}_agent_picks.json").write_text(
            '{"picks": [{"code": "600036"}, {"code": "000001"}],'
            ' "market_direction": {"direction": "看多"}}',
            encoding="utf-8",
        )
        snap = pool_snapshot(TARGET, reports_dir=tmp_path)
        assert snap.rows == 2
        assert snap.direction == "看多"
