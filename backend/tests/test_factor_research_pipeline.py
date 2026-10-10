"""毕业管道状态（pipeline_state）与一键毕业入口（start_promote）契约（2026-10-09）。

用户的原始问题「新挖到的因子怎么进研究与报告」暴露的是一整条链上**每一站都
不可见**：RD-Agent → PG → 物化 CUSTOM → 毕业桥镜像到 CN → 重建快照。
本测试锁死管道状态判据必须与毕业桥**同一实现**（plan_sync/scan_partitions），
否则会出现「面板说已同步、毕业桥还在报待拷贝」的假状态。
"""

from __future__ import annotations

import shutil

from backend.services.engine.factor_research import service

DATASET = "rd_mined"


def _write_partition(root, dt: str, cols: list[str]) -> object:
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = root / f"dt={dt}"
    d.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"symbol": ["S1", "S2"], "date": [dt, dt]})
    for c in cols:
        df[c] = np.array([1.0, 2.0])
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), d / "data.parquet")
    return d / "data.parquet"


def _custom_root(tmp) -> "object":
    return tmp / "custom" / "6_ml_datasets" / DATASET


def _cn_root(tmp) -> "object":
    return tmp / "quantdb" / "6_ml_datasets" / DATASET


def _point_env(monkeypatch, tmp) -> None:
    monkeypatch.setenv("QM_QUANTCUSTOM_DATA_DIR", str(tmp / "custom"))
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp / "quantdb"))


def test_pipeline_pending_counts_new_custom_factors(monkeypatch, tmp_path):
    """用户现场形状：CUSTOM 有 3 个因子、CN 只有 2 个 → 必须自曝 1 个待毕业。"""
    _point_env(monkeypatch, tmp_path)
    _write_partition(_custom_root(tmp_path), "20240102", ["rd_a", "rd_b", "rd_c"])
    _write_partition(_cn_root(tmp_path), "20240102", ["rd_a", "rd_b"])

    st = service.pipeline_state()

    assert st["state"] == "pending"
    assert st["pending_factors"] == 1
    assert st["pending_factor_names"] == ["rd_c"]
    assert st["custom_n_factors"] == 3 and st["cn_n_factors"] == 2
    assert st["custom_last_dt"] == "20240102"


def test_pipeline_synced_after_copy2_mirror(monkeypatch, tmp_path):
    """毕业桥用 shutil.copy2（保 mtime）→ 指纹全等 → 状态必须是 synced，不再催毕业。"""
    _point_env(monkeypatch, tmp_path)
    src_file = _write_partition(_custom_root(tmp_path), "20240102", ["rd_a", "rd_b"])
    dst_dir = _cn_root(tmp_path) / "dt=20240102"
    dst_dir.mkdir(parents=True)
    shutil.copy2(src_file, dst_dir / "data.parquet")

    st = service.pipeline_state()

    assert st["state"] == "synced"
    assert st["pending_factors"] == 0
    assert st["pending_partitions"] == 0


def test_pipeline_empty_when_custom_has_no_output(monkeypatch, tmp_path):
    """未启用挖掘的部署：CUSTOM 空 → state=empty（前端隐藏该段，不报假差异）。"""
    _point_env(monkeypatch, tmp_path)
    (tmp_path / "custom").mkdir()

    st = service.pipeline_state()

    assert st["state"] == "empty"
    assert st["custom_n_factors"] is None


def test_start_promote_runs_bridge_with_register_but_not_publish(monkeypatch, tmp_path):
    calls: dict = {}

    class FakeProc:
        pid = 5151

    def fake_popen(cmd, **kw):
        calls["cmd"] = list(cmd)
        return FakeProc()

    monkeypatch.setattr(service.store, "artifact_dir", lambda _ds: tmp_path)
    monkeypatch.setattr(service, "_promote_running", lambda _d: None)
    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)

    out = service.start_promote()

    assert out["started"] is True and out["pid"] == 5151
    assert "promote_rd_mined" in " ".join(calls["cmd"])
    assert "--register" in calls["cmd"]
    # 发布 CN 训练目录是显式闸门，一键毕业绝不允许顺手带过
    assert "--publish" not in calls["cmd"]
    assert (tmp_path / "promote.pid").read_text().strip() == "5151"


def test_start_promote_short_circuits_when_running(monkeypatch, tmp_path):
    def no_popen(*a, **k):
        raise AssertionError("已在毕业时不得再启动第二个进程")

    monkeypatch.setattr(service.store, "artifact_dir", lambda _ds: tmp_path)
    monkeypatch.setattr(service, "_promote_running", lambda _d: 999)
    monkeypatch.setattr(service.subprocess, "Popen", no_popen)

    out = service.start_promote()

    assert out == {"started": False, "running": True, "pid": 999}
