"""物化面频率参数化：分钟值折叠 + 数据面分派 + provenance（细案 §5.5/§6）。

覆盖（TDD：先红后绿；宿主机纯 pandas 可跑，不读 /data）：

1. ``_collapse_intraday`` 黄金样：每交易日最后一根**有效** bar——尾盘 NaN
   回退、±inf 剔除、乱序输入、午休缺口、集合竞价 bar、跨代码写法归并；
2. 粒度闸：全零点时间戳（日频输出穿分钟外衣）显式拒绝；全 NaN → 空值；
3. ``_to_canonical``：分钟折叠挂 ``attrs["native_rows"]``；日频分支与旧实现
   逐字一致（回归不变量 A）；
4. ``_row_native_freq`` / ``_row_mining_window`` / ``_provenance_fields`` /
   ``_exec_timeout``：provenance 读取与缺省；
5. ``_resolve_face``：日频委托 ``_resolve_h5``；分钟 --h5-path 优先、缺窗口
   响亮失败（不变量 C：绝不日频兜底）。
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.scripts import rd_mined_materialize as RM


def _frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """``(timestamp, instrument, value)`` → 执行输出形状（两层级 MultiIndex）。"""
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(ts), inst) for ts, inst, _ in rows],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({"factor": [v for _, _, v in rows]}, index=idx)


# ── 1. _collapse_intraday 黄金样 ──────────────────────────────────────


@pytest.mark.unit
def test_collapse_takes_last_valid_bar_not_last_bar():
    """收盘 bar NaN（停牌/未撮合）→ 回退到当日最后一根**有效** bar。"""
    frame = _frame(
        [
            ("2026-01-05 09:31:00", "600036.SH", 1.0),
            ("2026-01-05 09:32:00", "600036.SH", 2.0),
            ("2026-01-05 15:00:00", "600036.SH", float("nan")),
        ]
    )
    collapsed, native_rows = RM._collapse_intraday(frame.iloc[:, 0])
    assert collapsed.loc[("2026-01-05", "SH600036")] == 2.0
    assert native_rows == 2
    assert collapsed.index.names == ["trade_date", "symbol"]


@pytest.mark.unit
def test_collapse_ignores_lunch_gap_and_sorts_unsorted_input():
    frame = _frame(
        [
            ("2026-01-05 14:59:00", "600036.SH", 5.0),
            ("2026-01-05 09:31:00", "600036.SH", 3.0),
            ("2026-01-06 09:31:00", "600036.SH", 9.0),
        ]
    )
    collapsed, native_rows = RM._collapse_intraday(frame.iloc[:, 0])
    assert collapsed.loc[("2026-01-05", "SH600036")] == 5.0  # 乱序输入不吃亏
    assert collapsed.loc[("2026-01-06", "SH600036")] == 9.0
    assert native_rows == 3


@pytest.mark.unit
def test_collapse_drops_inf_before_last():
    """±inf（除零产物）与 NaN 同罪：尾盘 inf 不遮蔽前一有效 bar。"""
    frame = _frame(
        [
            ("2026-01-05 14:59:00", "600036.SH", 7.0),
            ("2026-01-05 15:00:00", "600036.SH", float("inf")),
            ("2026-01-05 09:31:00", "600036.SH", float("-inf")),
        ]
    )
    collapsed, native_rows = RM._collapse_intraday(frame.iloc[:, 0])
    assert collapsed.loc[("2026-01-05", "SH600036")] == 7.0
    assert native_rows == 1


@pytest.mark.unit
def test_collapse_keeps_auction_bar_as_valid_bar():
    """集合竞价 bar（09:25）是有效 bar——某日只有它时用它，不特殊剔除。"""
    frame = _frame([("2026-01-05 09:25:00", "600036.SH", 4.2)])
    collapsed, native_rows = RM._collapse_intraday(frame.iloc[:, 0])
    assert collapsed.loc[("2026-01-05", "SH600036")] == 4.2
    assert native_rows == 1


@pytest.mark.unit
def test_collapse_merges_symbol_spellings():
    """同标的后缀式/前缀式写法归并到同一 (date, symbol) 组，取时间后者。"""
    frame = _frame(
        [
            ("2026-01-05 09:31:00", "600036.SH", 1.0),
            ("2026-01-05 15:00:00", "SH600036", 8.0),
        ]
    )
    collapsed, _ = RM._collapse_intraday(frame.iloc[:, 0])
    assert len(collapsed) == 1
    assert collapsed.loc[("2026-01-05", "SH600036")] == 8.0


@pytest.mark.unit
def test_collapse_rejects_all_midnight_timestamps():
    """日频时间戳穿分钟外衣 → 显式拒绝（不变量 C 的物化侧哨兵）。"""
    frame = _frame(
        [
            ("2026-01-05 00:00:00", "600036.SH", 1.0),
            ("2026-01-06 00:00:00", "600036.SH", 2.0),
        ]
    )
    with pytest.raises(ValueError, match="纯日频时间戳"):
        RM._collapse_intraday(frame.iloc[:, 0])


@pytest.mark.unit
def test_collapse_all_nan_returns_empty_and_zero_rows():
    frame = _frame(
        [
            ("2026-01-05 09:31:00", "600036.SH", float("nan")),
            ("2026-01-05 09:32:00", "600036.SH", float("nan")),
        ]
    )
    collapsed, native_rows = RM._collapse_intraday(frame.iloc[:, 0])
    assert collapsed.empty
    assert native_rows == 0


@pytest.mark.unit
def test_collapse_rejects_wrong_index_shape():
    series = pd.Series([1.0], index=["2026-01-05"])
    with pytest.raises(ValueError, match="MultiIndex"):
        RM._collapse_intraday(series)


# ── 2. _to_canonical 分派 ─────────────────────────────────────────────


@pytest.mark.unit
def test_to_canonical_minute_folds_and_tags_native_rows():
    frame = _frame(
        [
            ("2026-01-05 09:31:00", "600036.SH", 1.0),
            ("2026-01-05 15:00:00", "600036.SH", 2.5),
        ]
    )
    out = RM._to_canonical(frame, native_freq="1min")
    assert out.loc[("2026-01-05", "SH600036")] == 2.5
    assert out.attrs["native_rows"] == 2


@pytest.mark.unit
def test_to_canonical_day_path_unchanged_without_attrs():
    """日频分支 = 旧实现：dedup(keep=last) + inf→nan + dropna，不挂 attrs。"""
    frame = _frame(
        [
            ("2026-01-05 00:00:00", "600036.SH", 1.0),
            ("2026-01-05 00:00:00", "600036.SH", 2.0),  # 重复键取后者（旧行为）
            ("2026-01-06 00:00:00", "600036.SH", float("inf")),  # inf 清掉
            ("2026-01-07 00:00:00", "600036.SH", float("nan")),  # NaN 清掉
        ]
    )
    out = RM._to_canonical(frame)
    assert list(out.index) == [("2026-01-05", "SH600036")]
    assert out.iloc[0] == 2.0
    assert "native_rows" not in out.attrs


@pytest.mark.unit
def test_to_canonical_default_is_day():
    frame = _frame([("2026-01-05 00:00:00", "600036.SH", 1.0)])
    out = RM._to_canonical(frame)
    assert out.iloc[0] == 1.0


# ── 3. provenance 读取 ────────────────────────────────────────────────


@pytest.mark.unit
def test_row_native_freq_defaults_and_invalid():
    assert RM._row_native_freq({}) == "day"
    assert RM._row_native_freq({"native_freq": None}) == "day"
    assert RM._row_native_freq({"native_freq": "1MIN"}) == "1min"
    assert RM._row_native_freq({"native_freq": "5min"}) == "day"  # 词表外不猜
    assert RM._row_native_freq({"metadata": {"native_freq": "1min"}}) == "1min"


@pytest.mark.unit
def test_row_mining_window_from_flat_keys():
    row = {"mining_window_start": "2026-01-05", "mining_window_end": "2026-01-06"}
    assert RM._row_mining_window(row) == (date(2026, 1, 5), date(2026, 1, 6))


@pytest.mark.unit
def test_row_mining_window_task_state_fallback(tmp_path, monkeypatch):
    task_dir = tmp_path / "alpha_agent_logs" / "task-abc"
    task_dir.mkdir(parents=True)
    (task_dir / "task_state.json").write_text(
        json.dumps({"mining_start": "2026-01-05", "mining_end": "2026-01-06"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        RM, "_alpha_agent_log_dir", lambda: tmp_path / "alpha_agent_logs"
    )
    row = {"task_id": "task-abc"}
    assert RM._row_mining_window(row) == (date(2026, 1, 5), date(2026, 1, 6))


@pytest.mark.unit
def test_row_mining_window_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(RM, "_alpha_agent_log_dir", lambda: tmp_path / "nonexistent")
    assert RM._row_mining_window({"task_id": "missing"}) is None
    assert RM._row_mining_window({}) is None
    bad = {"mining_window_start": "not-a-date", "mining_window_end": "2026-01-06"}
    assert RM._row_mining_window(bad) is None


@pytest.mark.unit
def test_provenance_fields_day_minute_and_error():
    assert RM._provenance_fields("day", None) == {"native_freq": "day"}
    assert RM._provenance_fields("1min", 1234) == {
        "native_freq": "1min",
        "value_collapse": "last",
        "native_rows": 1234,
    }
    # error 条目：值没算出来（native_rows=None）也要带折叠口径
    assert RM._provenance_fields("1min", None) == {
        "native_freq": "1min",
        "value_collapse": "last",
    }


@pytest.mark.unit
def test_exec_timeout_minute_floor():
    assert RM._exec_timeout({"native_freq": "day"}, 900) == 900
    assert RM._exec_timeout({"native_freq": "1min"}, 900) == RM._EXEC_TIMEOUT_MINUTE_S
    assert RM._exec_timeout({"native_freq": "1min"}, 3600) == 3600


# ── 4. 数据面分派 ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_face_day_delegates_to_resolve_h5(monkeypatch, tmp_path):
    sentinel = tmp_path / "daily.h5"
    monkeypatch.setattr(RM, "_resolve_h5", lambda p: sentinel)
    assert RM._resolve_face("/explicit.h5", {"native_freq": "day"}) == sentinel
    assert RM._resolve_face(None, {}) == sentinel


@pytest.mark.unit
def test_resolve_face_minute_prefers_valid_h5_path(tmp_path):
    h5 = tmp_path / "minute_pv.h5"
    h5.write_bytes(RM._HDF5_MAGIC + b"payload")
    row = {"native_freq": "1min"}
    assert RM._resolve_face(str(h5), row) == h5


@pytest.mark.unit
def test_resolve_face_minute_invalid_h5_path_needs_window(monkeypatch, tmp_path):
    """--h5-path 不在且无窗口 provenance → 响亮失败，绝不日频兜底。"""
    monkeypatch.setattr(RM, "_alpha_agent_log_dir", lambda: tmp_path / "none")
    row = {"native_freq": "1min", "task_id": "no-state"}
    with pytest.raises(RuntimeError, match="拒绝日频兜底"):
        RM._resolve_face(str(tmp_path / "missing.h5"), row)
