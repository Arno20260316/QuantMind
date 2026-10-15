"""基准模块（``backend/shared/benchmark.py``）市场分层单测（2026-10-16）。

锁死：指数目录按市场解析（CN 走存量候选表；非 CN 走市场根）、取数失败时
FUTURES 这类「本就没有该目录」的市场给出区别于「同步缺失」的报错、非法市场
严格拒绝、以及 ``resolve_benchmark`` 能按市场链在非 CN 根上取到指数。
"""
from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.shared import benchmark as BM


def _write_index(root, market_dir: str, symbol: str, n: int = 600) -> None:
    d = root / market_dir / "1_kline_data" / "index_daily" / "dt=20260105"
    d.mkdir(parents=True)
    days = pd.date_range("2020-01-01", periods=n, freq="B")
    pq.write_table(pa.table({
        "symbol": [symbol] * n,
        "time": pa.array(days, type=pa.timestamp("ns")),
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0 + i * 0.01 for i in range(n)],
        "volume": [1e6] * n, "amount": [1e8] * n,
    }), d / "data.parquet")


# ─────────────────────────── 目录解析 ───────────────────────────

def test_cn_指数目录走存量候选表(tmp_path, monkeypatch):
    root = tmp_path / "quantdb"
    (root / "2_base_sector").mkdir(parents=True)  # 候选表要求目录非空
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(root))

    assert BM.index_daily_dir() == root / "1_kline_data" / "index_daily"
    assert BM.index_daily_dir("CN") == BM.index_daily_dir()  # 缺省即 CN


def test_非cn指数目录走市场根(tmp_path, monkeypatch):
    hk = tmp_path / "quanthk"
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(hk))

    assert BM.index_daily_dir("HK") == hk / "1_kline_data" / "index_daily"
    assert BM.index_daily_dir("hk") == BM.index_daily_dir("HK")  # 大小写归一


def test_非法市场严格拒绝而不是落_cn(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "quantdb"))
    with pytest.raises(ValueError, match="未知市场"):
        BM.index_daily_dir("XX")


# ─────────────────────────── 取数 ───────────────────────────

def test_hk指数取数与基准链(tmp_path, monkeypatch):
    hk = tmp_path / "quanthk"
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(hk))
    _write_index(hk, "", "HSI.HK")

    df = BM.load_index_frame(columns=("close",), symbols=["HSI.HK"], market="HK")
    assert len(df) == 600 and df["symbol"].unique().tolist() == ["HSI.HK"]

    sym, frame = BM.resolve_benchmark(columns=("open", "close"),
                                      chain=("HSI.HK",), market="HK")
    assert sym == "HSI.HK" and len(frame) == 600


def test_无指数目录的市场报错指明该市场无目录(tmp_path, monkeypatch):
    fut = tmp_path / "quantfutures"
    fut.mkdir()
    monkeypatch.setenv("QM_QUANTFUTURES_DATA_DIR", str(fut))

    with pytest.raises(FileNotFoundError, match="无指数日线目录"):
        BM.load_index_frame(columns=("close",), symbols=["A0.CN"], market="FUTURES")
