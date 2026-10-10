"""任务级数据版本快照（移植 P3）——「这份 IC 是在哪份数据上算的」。

数据日更之后回看历史任务，必须能**从任务行本身**回答当时的覆盖区间与版本
戳；拿今天的覆盖去解释昨天的结果就是自欺。本文件锁：

1. **版本判据 = 盘上事实**：``dt=YYYYMMDD`` 分区名的 min/max + 最新分区内文件
   的最大 mtime（与 h5 缓存新鲜度读的是同一份事实，不与交易日历混用）；
2. **分钟口径另算**：日线覆盖 2016→2026 与 1min 断层不是一回事，分钟任务
   另带 provider 的可用连续块/断层；
3. **Fail-open**：任何一项读不到都进 ``errors`` 如实上屏，绝不拦任务创建，
   也不写假的。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import data_snapshot as snap  # noqa: E402
from backend.services.engine.alpha_agent import launcher as launcher_module  # noqa: E402
from backend.services.engine.alpha_agent.launcher import MinuteScope  # noqa: E402
from backend.services.engine.alpha_agent.mining_config import Window  # noqa: E402

_KLINE = "1_kline_data/daily_forward"


def _partition(base: Path, day: str, *, mtime: float | None = None) -> Path:
    """造一个 ``dt=YYYYMMDD`` 分区目录（可选钉住文件 mtime）。"""
    part = base / f"dt={day}"
    part.mkdir(parents=True, exist_ok=True)
    blob = part / "part-0.parquet"
    blob.write_bytes(b"x")
    if mtime is not None:
        os.utime(blob, (mtime, mtime))
    return part


class TestPartitionFacts:
    def test_reads_coverage_and_latest_version_stamp(self, tmp_path: Path) -> None:
        base = tmp_path / "ds"
        _partition(base, "20240102", mtime=1_700_000_000)
        _partition(base, "20240103", mtime=1_700_086_400)

        facts = snap.partition_facts(base)

        assert facts is not None
        # 覆盖区间取分区名 min/max——不是交易日历，是盘上真有的分区
        assert (facts["start"], facts["end"]) == ("2024-01-02", "2024-01-03")
        assert facts["latest_partition"] == "20240103"
        # 版本戳 = 最新分区内文件的最大 mtime（UTC ISO，带 Z）
        assert facts["latest_mtime"] == "2023-11-15T22:13:20Z"

    def test_ignores_non_partition_dirs(self, tmp_path: Path) -> None:
        base = tmp_path / "ds"
        _partition(base, "20240102")
        (base / "_tmp").mkdir()
        (base / "dt=2024").mkdir()  # 分区名不合法（长度/数字）
        (base / "dt=abcdefgh").mkdir()

        facts = snap.partition_facts(base)

        assert facts is not None
        assert (facts["start"], facts["end"]) == ("2024-01-02", "2024-01-02")

    def test_missing_or_empty_dir_is_none(self, tmp_path: Path) -> None:
        assert snap.partition_facts(tmp_path / "nope") is None
        empty = tmp_path / "empty"
        empty.mkdir()
        assert snap.partition_facts(empty) is None

    def test_empty_latest_partition_falls_back_to_dir_mtime(self, tmp_path: Path) -> None:
        """最新分区里还没有文件（同步写到一半）：用目录 mtime，不编一个时间。"""
        base = tmp_path / "ds"
        _partition(base, "20240102", mtime=1_700_000_000)
        (base / "dt=20240103").mkdir()

        facts = snap.partition_facts(base)

        assert facts is not None and facts["latest_partition"] == "20240103"
        assert facts["latest_mtime"] is not None


class TestSnapshotDataVersions:
    def test_non_a_share_records_note_not_fake_versions(self) -> None:
        out = snap.snapshot_data_versions(
            market="hong_kong", freq=None, datasets=("features_daily",)
        )

        assert out["provider"] is None and out["datasets"] == {}
        assert "hong_kong" in out["note"]
        assert out["errors"] == []
        assert out["freq"] == "day"

    def test_a_share_records_kline_and_each_dataset(self, tmp_path: Path) -> None:
        _partition(tmp_path / _KLINE, "20240102")
        _partition(tmp_path / _KLINE, "20240105")
        _partition(tmp_path / "6_ml_datasets/features_daily", "20240104")

        out = snap.snapshot_data_versions(
            market="a_share",
            freq=None,
            datasets=("features_daily", "l1_factors"),
            quantdb_dir=str(tmp_path),
        )

        assert out["provider"]["name"] == "quantdb"
        assert out["provider"]["dir"] == str(tmp_path)
        assert out["provider"]["kline"]["end"] == "2024-01-05"
        assert set(out["datasets"]) == {"features_daily"}
        assert out["datasets"]["features_daily"]["start"] == "2024-01-04"
        # 读不到的数据集如实进 errors —— 不静默、不编
        assert any("l1_factors" in e for e in out["errors"])
        assert out["snapshot_at"].endswith("Z")

    def test_unavailable_quantdb_dir_is_an_error_not_a_crash(self, tmp_path: Path) -> None:
        out = snap.snapshot_data_versions(
            market="a_share",
            freq=None,
            datasets=(),
            quantdb_dir=str(tmp_path / "missing"),
        )

        assert any("无可用分区" in e for e in out["errors"])
        assert out["datasets"] == {}

    def test_resolver_blowup_is_swallowed(self, monkeypatch) -> None:
        """目录解析炸了也不拦任务创建：快照是审计面，不是主链。"""

        def _boom() -> str:
            raise RuntimeError("quantdb 配置损坏")

        monkeypatch.setattr(snap, "_resolve_quantdb_dir", _boom)

        out = snap.snapshot_data_versions(market="a_share", freq=None, datasets=())

        assert out["provider"] is None and out["datasets"] == {}
        assert any("QuantDB" in e for e in out["errors"])

    def test_captured_at_is_the_snapshot_stamp(self) -> None:
        moment = datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc)
        out = snap.snapshot_data_versions(
            market="hong_kong", freq=None, datasets=(), captured_at=moment
        )

        assert out["snapshot_at"] == "2026-03-01T08:30:00Z"

    def test_snapshot_is_strict_json(self, tmp_path: Path) -> None:
        _partition(tmp_path / _KLINE, "20240102")
        out = snap.snapshot_data_versions(
            market="a_share", freq=None, datasets=(), quantdb_dir=str(tmp_path)
        )
        json.dumps(out, ensure_ascii=False, allow_nan=False)


class TestMinuteFacts:
    def test_minute_block_absent_when_provider_unbuilt(self, monkeypatch) -> None:
        monkeypatch.setattr(launcher_module, "minute_scope", lambda: None)

        out = snap.snapshot_data_versions(market="a_share", freq="1min", datasets=())

        assert out["minute"] is None
        assert any("分钟数据面未构建" in e for e in out["errors"])

    def test_minute_block_records_usable_block_and_gaps(self, monkeypatch) -> None:
        """分钟口径另算：provider 全区间 + 末端连续块 + 断层。"""
        scope = MinuteScope(
            usable=Window(date(2026, 3, 5), date(2026, 8, 25)),
            coverage=Window(date(2021, 8, 2), date(2026, 8, 25)),
            days=118,
            gaps=((date(2026, 1, 6), date(2026, 3, 4)),),
        )
        monkeypatch.setattr(launcher_module, "minute_scope", lambda: scope)

        out = snap.snapshot_data_versions(market="a_share", freq="1min", datasets=())

        assert out["minute"] == {
            "coverage": {"start": "2021-08-02", "end": "2026-08-25"},
            "usable": {"start": "2026-03-05", "end": "2026-08-25"},
            "usable_days": 118,
            "gaps": [{"start": "2026-01-06", "end": "2026-03-04"}],
        }


class TestLauncherWiring:
    """快照函数与 launcher 的接线：提交即快照，落任务行（真实现，不打桩）。"""

    @pytest.mark.asyncio
    async def test_created_task_carries_real_snapshot(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from backend.services.engine.alpha_agent.launcher import AlphaAgentLauncher
        from backend.services.engine.alpha_agent.task_store import DuplicateActiveTask

        captured: dict = {}

        class _Store:
            async def create_task(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(launcher_module, "_task_store", lambda: _Store())
        monkeypatch.setattr(
            launcher_module,
            "snapshot_data_versions",
            snap.snapshot_data_versions,
        )
        monkeypatch.setattr(snap, "_resolve_quantdb_dir", lambda: str(tmp_path))
        _partition(tmp_path / _KLINE, "20240102")

        launcher = AlphaAgentLauncher.__new__(AlphaAgentLauncher)
        launcher._tasks = {}
        launcher._log_dir = tmp_path / "logs"
        launcher._draining = False
        monkeypatch.setattr(launcher, "_launch", lambda *a, **kw: None)

        task_id = await launcher.start_evolution("u-1", direction="动量")

        versions = json.loads(captured["data_versions"])
        assert versions["provider"]["kline"]["end"] == "2024-01-02"
        assert versions["snapshot_at"].endswith("Z")
        assert captured["fingerprint"] == launcher._tasks[task_id].fingerprint
        assert DuplicateActiveTask  # 契约类仍可导入（store 侧抛它、launcher 接它）
