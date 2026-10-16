"""港股聪明钱 · 数据面单元测试（合成 parquet / monkeypatch，不读真实分区）。

见证：防前视收益口径（T+1 入场 + clip + 全市场中位 exc）/ 南向完整表语义
（捕获日缺席=0、缺口 ffill、首捕前 NaN）/ ccass 去重与 .HK 过滤 / hsgt_south
假清仓净化 / 产物目录三段解析与 JSON 原子写（NaN 即拒）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.shared.hk_smart_money import datasets as ds


# ---------------------------------------------------------------- 防前视收益


def test_forward_matrices_clip_and_market_median_exc():
    days = pd.bdate_range("2026-01-05", periods=25)
    close = pd.DataFrame(index=days)
    close["A.HK"] = [10.0] * 20 + [30.0] * 5
    close["B.HK"] = 10.0
    close["C.HK"] = [10.0] * 20 + [1.0] * 5
    fwd = ds.forward_matrices(close)
    assert set(fwd) == {5, 10, 20}
    ret, exc = fwd[10]
    t = days[10]
    assert ret.loc[t, "A.HK"] == 1.0  # 原始 +200% → clip +100%
    assert ret.loc[t, "C.HK"] == -0.5  # 原始 −90% → clip −50%
    assert ret.loc[t, "B.HK"] == 0.0
    # exc = fwd − 当日全体有效中位（此处中位 0）
    assert exc.loc[t, "A.HK"] == 1.0
    assert exc.loc[t, "C.HK"] == -0.5
    assert np.isnan(ret.loc[days[20], "A.HK"])  # 未来不足 h 日 → NaN


# ---------------------------------------------------------------- 南向完整表


def test_south_qty_matrix_complete_table_semantics(monkeypatch):
    cal = pd.bdate_range("2026-01-01", periods=7)
    symbols = pd.Index(["0001.HK", "0002.HK"])

    def _fake_south(dt: str) -> pd.DataFrame:
        rows = {
            "20260105": [("0001.HK", 100), ("0002.HK", 50)],
            "20260106": [("0001.HK", 150)],  # 0002 缺席 = 当日无持仓 → 0
            "20260108": [("0001.HK", 140), ("0002.HK", 10)],  # 0107 无捕获
        }[dt]
        return pd.DataFrame(rows, columns=["symbol", "holding_quantity"])

    monkeypatch.setattr(ds, "south_dates", lambda: ("20260105", "20260106", "20260108"))
    monkeypatch.setattr(ds, "load_south", _fake_south)
    m = ds.south_qty_matrix(cal, symbols)
    assert list(m.columns) == ["0001.HK", "0002.HK"]
    assert m.loc[cal[0]].isna().all()  # 首次捕获之前
    assert m.loc[cal[2], "0001.HK"] == 100
    assert m.loc[cal[3], "0002.HK"] == 0.0  # 捕获日缺席 = 0（非 NaN）
    assert m.loc[cal[4], "0002.HK"] == 0.0  # 缺口日 ffill
    assert m.loc[cal[5], "0002.HK"] == 10
    assert m.loc[cal[6], "0001.HK"] == 140  # 末次捕获后 ffill


# ---------------------------------------------------------------- 原始面读取


def _write_parquet(root: Path, rel: str, dt: str, rows: list[dict]) -> None:
    path = root / rel / f"dt={dt}" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_load_ccass_dedupe_hk_filter_dtype(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "data_dir", lambda: tmp_path)
    _write_parquet(
        tmp_path,
        ds.CCASS_REL,
        "20260105",
        [
            {
                "stock_code": "0001.HK",
                "stock_name": "A",
                "participant_id": "C1",
                "participant_name": "X",
                "holding_quantity": 100,
            },
            {  # 同 (席位, 票) 重复 → keep last
                "stock_code": "0001.HK",
                "stock_name": "A",
                "participant_id": "C1",
                "participant_name": "X",
                "holding_quantity": 200,
            },
            {  # 非 .HK 剔除
                "stock_code": "600036.SH",
                "stock_name": "B",
                "participant_id": "C2",
                "participant_name": "Y",
                "holding_quantity": 50,
            },
            {
                "stock_code": "0002.HK",
                "stock_name": "C",
                "participant_id": "C2",
                "participant_name": "Y",
                "holding_quantity": 300,
            },
        ],
    )
    df = ds.load_ccass("20260105")
    assert len(df) == 2
    assert set(df["stock_code"]) == {"0001.HK", "0002.HK"}
    assert int(df[df["participant_id"] == "C1"].iloc[0]["holding_quantity"]) == 200
    assert df["holding_quantity"].dtype == "int64"


def test_load_south_sanitize_and_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "data_dir", lambda: tmp_path)
    _write_parquet(
        tmp_path,
        ds.SOUTH_REL,
        "20260105",
        [
            {
                "symbol": "0001.HK",
                "stock_name": "A",
                "holding_quantity": 2_000_000,
                "holding_percentage": 0.0,
            },
            {  # 重复 → keep last（pct=1.2 存活行）
                "symbol": "0001.HK",
                "stock_name": "A",
                "holding_quantity": 2_500_000,
                "holding_percentage": 1.2,
            },
            {  # 小额 0% 合法
                "symbol": "0002.HK",
                "stock_name": "B",
                "holding_quantity": 500_000,
                "holding_percentage": 0.0,
            },
            {  # pct=0 但 qty>1e6 = 披露缺失 → NaN，防假清仓
                "symbol": "0003.HK",
                "stock_name": "C",
                "holding_quantity": 2_000_000,
                "holding_percentage": 0.0,
            },
        ],
    )
    out = ds.load_south("20260105")
    assert len(out) == 3
    by = out.set_index("symbol")
    assert int(by.loc["0001.HK", "holding_quantity"]) == 2_500_000
    assert by.loc["0001.HK", "holding_percentage"] == 1.2
    assert by.loc["0002.HK", "holding_percentage"] == 0.0
    assert pd.isna(by.loc["0003.HK", "holding_percentage"])


# ---------------------------------------------------------------- 产物面


def test_day_artifacts_writer_reader_seam(tmp_path, monkeypatch):
    """写读缝回归（2026-10-16 事故）：flows 必须从 events/dt=*/flows.parquet 读回。

    写侧只经 events.write_day_artifacts、读侧只经 datasets.load_*，任一侧改布局
    而另一侧未同步即红。事故形态：读侧曾按顶层 flows/ 目录找，永远空 → validate
    在 compute_all_signals 才炸（事件仓 466 分区全白建）。
    """
    from backend.shared.hk_smart_money import events as ev

    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path))
    dt = "20260105"
    ev.write_day_artifacts(
        dt,
        events=pd.DataFrame({"dt": [dt], "participant_id": ["C1"]}),
        flows=pd.DataFrame({"dt": [dt], "category": ["x"], "net_value": [1.0]}),
        universe=pd.DataFrame({"symbol": ["0001.HK"]}),
        holdings=pd.DataFrame({"dt": [dt], "symbol": ["0001.HK"], "qty_sum": [9]}),
    )
    assert ds.event_dates() == (dt,)
    assert ds.flow_dates() == (dt,)  # 顶层 flows/ 目录不存在，flows 随日分区
    assert len(ds.load_flows()) == 1
    assert len(ds.load_flows(dates=[dt])) == 1
    assert len(ds.load_events()) == 1
    assert len(ds.load_universe()) == 1
    assert len(ds.load_holdings()) == 1
    assert ds.load_flows().iloc[0]["net_value"] == 1.0
    # dt 必须回读为 YYYYMMDD 字符串：DuckDB 默认把 dt=YYYYMMDD 目录名当 hive
    # 分区、按 BIGINT 覆盖/注入 dt 列（2026-10-16 第三事故），此处钉死
    assert list(ds.load_flows()["dt"]) == [dt]
    assert list(ds.load_events()["dt"]) == [dt]
    assert list(ds.load_holdings()["dt"]) == [dt]


def test_empty_first_partition_schema_readable(tmp_path, monkeypatch):
    """空首分区 schema 回归（2026-10-16 第二事故）：首日 prev=None → events/flows
    为空帧，修复前 object 列落成 parquet null 型，DuckDB 多文件读（取首文件
    schema）对后续 VARCHAR 列 cast 报错。写侧自描述 + 读侧 union_by_name 双保险。
    """
    from backend.shared.hk_smart_money import events as ev

    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path))
    ev.write_day_artifacts(
        "20260105",
        events=pd.DataFrame(columns=["dt", "symbol", "participant_id"]),
        flows=pd.DataFrame(columns=["dt", "symbol", "category", "net_value"]),
        universe=pd.DataFrame({"symbol": ["0001.HK"]}),
        holdings=pd.DataFrame({"dt": ["20260105"], "symbol": ["0001.HK"]}),
    )
    ev.write_day_artifacts(
        "20260106",
        events=pd.DataFrame({"dt": ["20260106"], "symbol": ["0001.HK"]}),
        flows=pd.DataFrame(
            {"dt": ["20260106"], "symbol": ["0001.HK"], "category": ["x"]}
        ),
        universe=pd.DataFrame({"symbol": ["0001.HK"]}),
        holdings=pd.DataFrame({"dt": ["20260106"], "symbol": ["0001.HK"]}),
    )
    flows = ds.load_flows()  # 修复前：VARCHAR→NULL cast 报错
    assert list(flows["symbol"]) == ["0001.HK"]
    events = ds.load_events()
    assert list(events["symbol"]) == ["0001.HK"]
    # 写侧自描述：空分区列非 null 型（读单文件也不炸）
    one = ds.load_flows(dates=["20260105"])
    assert one.empty
    assert list(one.columns) == ["dt", "symbol", "category", "net_value"]


def test_load_flows_missing_dates_returns_empty(tmp_path, monkeypatch):
    """域外日期静默跳过（dates 显式传入但无分区）。"""
    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path))
    assert ds.load_flows(dates=["20260105"]).empty
    assert ds.flow_dates() == ()
    assert ds.load_events().empty


def test_product_dir_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path))
    assert ds.product_dir() == tmp_path / "hk_smart_money"
    monkeypatch.delenv("QM_MARKET_SNAPSHOT_DIR")
    monkeypatch.setenv("QUANTMINT_HOST_MODE", "1")
    assert (
        ds.product_dir() == Path.cwd() / "data" / "market-analysis" / "hk_smart_money"
    )


def test_product_json_roundtrip_and_nan_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_MARKET_SNAPSHOT_DIR", str(tmp_path))
    assert ds.load_product_json("meta") is None
    ds.write_json_atomic("meta", {"as_of": "20260105", "n": 3})
    assert ds.load_product_json("meta") == {"as_of": "20260105", "n": 3}
    with pytest.raises(ValueError):
        ds.write_json_atomic("bad", {"x": float("nan")})
