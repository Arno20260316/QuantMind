"""分钟（1min）Qlib provider 构建器契约测试。

契约（移植 P1.5，2026-10-10）：

1. **qlib bin 格式**：首 4 字节 float32 = 日历起始下标，其余为 ``<f4`` 值数组；
   数组长度 = ``last_idx - start_idx + 1``——缺 bar 必须按 NaN 对齐，不能压缩。
2. **复权口径**：价格字段（OHLC/vwap）× 当日 day 层 step factor；成交量/额不动；
   「交易日内最后一根 1min close × factor == day close_bin」是恒等式。
3. **混合叠加**：day 层（日历 + 池内标的 + 基准日线 bins）必须原样复制进分钟
   provider——执行器/撮合/基准全在 day 频。
4. **instruments 收窄**：只留已构建标的，区间 ∩ 分钟覆盖（不冒充日线长历史）。
5. **金额单位自动测定**：量价反推对 10 的幂吸附；样本不足回缺省并进 warning。
6. **原子性与新鲜度**：构建失败不碰旧 provider；ensure 在源指纹未变时零重建，
   源 mtime/size 变化即重建；状态接口如实报 stale。
7. **不造 bar、不补缺**：日历 = 源 time 列的并集；09:25 集合竞价 bar 原样保留。

TDD：先红后绿。全部走 tmp_path 合成数据，不读 /data、不写生产目录。
"""

from __future__ import annotations

import json
import os
import struct
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine import minute_qlib_provider as MP  # noqa: E402

# ── 合成数据 ─────────────────────────────────────────────────────────

DAY1 = date(2026, 1, 5)
DAY2 = date(2026, 1, 6)
#: day 日历比分钟数据多出一天 —— 模拟「日线在长，提供回测 t+1 缓冲」
DAY3 = date(2026, 1, 7)
BARS = ("09:30:00", "09:31:00", "09:32:00", "09:33:00", "09:34:00")
CODES = ("sh600000", "sz000001")
BENCH = "sh000300"


def _raw_close(code: str, day: date, bar: str) -> float:
    """确定性合成收盘价（不同标的/日期/bar 各不相同但有界）。"""
    base = {"sh600000": 10.0, "sz000001": 20.0}[code]
    return base + (0 if day == DAY1 else 1.0) + BARS.index(bar) * 0.1


def _day_factor(code: str, day: date) -> float:
    """逐日 step factor：sz000001 全程 2.0；sh600000 第一天 1.0、之后 1.5。"""
    if code == "sz000001":
        return 2.0
    return 1.0 if day == DAY1 else 1.5


def _write_source(
    tmp_path: Path,
    *,
    codes: tuple[str, ...] = CODES,
    amount_scale: float = 1e4,
    drop: tuple[str, date, str] | None = None,
    days: tuple[date, ...] = (DAY1, DAY2),
) -> Path:
    """合成 min1_kline parquet 源（后缀式文件名）；``days`` 可造中间断层。"""
    src = tmp_path / "quantdb" / "1_kline_data" / "min1_kline"
    src.mkdir(parents=True, exist_ok=True)
    for code in codes:
        rows = []
        for day in days:
            for bar in BARS:
                if drop and (code, day, bar) == drop:
                    continue
                close = _raw_close(code, day, bar)
                volume = 1000.0 + BARS.index(bar)
                rows.append(
                    {
                        "time": datetime.fromisoformat(f"{day.isoformat()} {bar}"),
                        "open": close - 0.05,
                        "high": close + 0.1,
                        "low": close - 0.1,
                        "close": close,
                        "volume": volume,
                        # amount = close × volume / scale → 反推系数恰为 scale
                        "amount": close * volume / amount_scale,
                        "volinstock": 1.0,
                        "forwardfactor": 1.0,
                    }
                )
        pd.DataFrame(rows).to_parquet(src / f"{code[2:]}.{code[:2].upper()}.parquet")
    return src


def _write_day_provider(
    tmp_path: Path,
    *,
    codes: tuple[str, ...] = CODES,
    extra_instrument_pool: bool = True,
    days: tuple[date, ...] = (DAY1, DAY2, DAY3),
) -> Path:
    """合成日线 provider：day 日历（默认含 DAY3）+ factor/close/volume bins + 池文件。"""
    day = tmp_path / "qlib" / "cn_data"
    (day / "calendars").mkdir(parents=True)
    (day / "features").mkdir()
    (day / "instruments").mkdir()
    cal = list(days)
    (day / "calendars" / "day.txt").write_text(
        "\n".join(d.isoformat() for d in cal) + "\n", encoding="utf-8"
    )
    for code in (*codes, BENCH):
        feat = day / "features" / code
        feat.mkdir(parents=True)
        factors = np.array(
            [1.0 if code == BENCH else _day_factor(code, d) for d in cal], dtype="<f4"
        )
        MP._write_bin(feat / "factor.day.bin", 0, factors)
        if code == BENCH:
            closes = np.arange(100.0, 100.0 + len(cal), dtype="<f4")
        else:
            closes = np.array(
                [_raw_close(code, d, BARS[-1]) * _day_factor(code, d) for d in cal],
                dtype="<f4",
            )
        MP._write_bin(feat / "close.day.bin", 0, closes)
        MP._write_bin(feat / "volume.day.bin", 0, np.full(len(cal), 5e5, dtype="<f4"))
    rows = [f"{code}\t{DAY1.isoformat()}\t2026-10-09" for code in codes]
    (day / "instruments" / "csi300.txt").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    (day / "instruments" / "all.txt").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )
    if extra_instrument_pool:
        # 日线有、分钟没有的池：应被"空池不落地"规则跳过（不写出空文件）
        (day / "instruments" / "csi500.txt").write_text(
            "sh601988\t2016-01-04\t2026-10-09\n", encoding="utf-8"
        )
    return day


def _read_field(
    feat_dir: Path, field: str, freq: str = "1min"
) -> tuple[int, np.ndarray]:
    return MP._read_bin(feat_dir / f"{field}.{freq}.bin")


def _read_calendar(provider: Path, freq: str = "1min") -> list[str]:
    return [
        ln.strip()
        for ln in (provider / "calendars" / f"{freq}.txt").read_text().splitlines()
        if ln.strip()
    ]


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, Path]:
    return {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }


def _build(env: dict[str, Path], **kwargs) -> MP.BuildReport:
    return MP.build_minute_provider(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
        **kwargs,
    )


# ── 1. bin 格式与日历 ────────────────────────────────────────────────


def test_build_writes_minute_calendar_in_qlib_text_format(env):
    _build(env)
    cal = _read_calendar(env["provider"], "1min")
    assert len(cal) == 10  # 2 日 × 5 bar
    assert cal[0] == "2026-01-05 09:30:00"
    assert cal[-1] == "2026-01-06 09:34:00"
    assert all(len(ts) == 19 for ts in cal)


def test_bin_header_is_start_index_and_payload_float32(env):
    _build(env)
    bin_path = env["provider"] / "features" / "sh600000" / "close.1min.bin"
    raw = bin_path.read_bytes()
    start = struct.unpack("<f", raw[:4])[0]
    assert start == 0.0
    assert (len(raw) - 4) % 4 == 0
    values = np.frombuffer(raw[4:], dtype="<f4")
    assert len(values) == 10


def test_bin_start_index_tracks_symbol_window(env):
    """只覆盖第二天数据的标的：start_idx = 该标的首 bar 在全市场日历中的下标。"""
    src = env["source"]
    df = pd.read_parquet(src / "600000.SH.parquet")
    df[df["time"] >= pd.Timestamp("2026-01-06")].to_parquet(src / "600000.SH.parquet")
    _build(env)
    start, values = _read_field(env["provider"] / "features" / "sh600000", "close")
    assert start == 5  # 前 5 根 bar 属于 DAY1，被裁掉
    assert len(values) == 5


# ── 2. 缺口对齐 ──────────────────────────────────────────────────────


def test_missing_bar_is_nan_padded_not_compacted(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path, drop=("sz000001", DAY2, "09:31:00")),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    _build(env)
    start, values = _read_field(env["provider"] / "features" / "sz000001", "close")
    assert start == 0
    assert len(values) == 10  # 仍占满全市场日历跨度
    assert np.isnan(values[6])  # DAY2 09:31 的洞
    assert np.isfinite(values[5]) and np.isfinite(values[7])


# ── 3. 复权口径与 day 层 ─────────────────────────────────────────────


def test_price_fields_multiplied_by_daily_step_factor(env):
    _build(env)
    # sz000001 全程 factor=2.0；sh600000 DAY2 起 1.5
    _, sz_close = _read_field(env["provider"] / "features" / "sz000001", "close")
    expected = np.array(
        [_raw_close("sz000001", d, b) * 2.0 for d in (DAY1, DAY2) for b in BARS],
        dtype="<f4",
    )
    np.testing.assert_allclose(sz_close, expected, rtol=1e-6)

    _, sh_close = _read_field(env["provider"] / "features" / "sh600000", "close")
    expected_sh = np.array(
        [
            _raw_close("sh600000", d, b) * _day_factor("sh600000", d)
            for d in (DAY1, DAY2)
            for b in BARS
        ],
        dtype="<f4",
    )
    np.testing.assert_allclose(sh_close, expected_sh, rtol=1e-6)


def test_last_minute_close_matches_day_bin_close(env):
    """恒等式：日内最后一根 1min close × factor == day close_bin。"""
    _build(env)
    bp = env["provider"]
    cal = _read_calendar(bp, "1min")
    day_cal = _read_calendar(bp, "day")
    for code in CODES:
        m_start, m_close = _read_field(bp / "features" / code, "close", "1min")
        d_start, d_close = _read_field(bp / "features" / code, "close", "day")
        for day in (DAY1, DAY2):
            last_pos = max(
                i for i, ts in enumerate(cal) if ts.startswith(day.isoformat())
            )
            day_pos = day_cal.index(day.isoformat())
            assert m_close[last_pos - m_start] == pytest.approx(
                d_close[day_pos - d_start], rel=1e-6
            )


def test_volume_and_amount_kept_raw(env):
    _build(env)
    _, vol = _read_field(env["provider"] / "features" / "sz000001", "volume")
    assert vol[0] == pytest.approx(1000.0)
    _, amount = _read_field(env["provider"] / "features" / "sz000001", "amount")
    assert amount[0] == pytest.approx(
        _raw_close("sz000001", DAY1, BARS[0]) * 1000.0 / 1e4
    )


def test_day_layer_copied_with_calendar_and_benchmark(env):
    _build(env)
    bp = env["provider"]
    assert _read_calendar(bp, "day") == [d.isoformat() for d in (DAY1, DAY2, DAY3)]
    for code in (*CODES, BENCH):
        assert (bp / "features" / code / "close.day.bin").is_file()
        assert (bp / "features" / code / "factor.day.bin").is_file()
    _, bench_close = _read_field(bp / "features" / BENCH, "close", "day")
    assert bench_close[0] == pytest.approx(100.0)
    # 基准不参与分钟层（源里没有它的分钟数据）
    assert not (bp / "features" / BENCH / "close.1min.bin").exists()


# ── 4. instruments 收窄 ──────────────────────────────────────────────


def test_instruments_restricted_to_built_symbols_and_clamped(env):
    _build(env)
    rows = (env["provider"] / "instruments" / "csi300.txt").read_text().splitlines()
    assert len(rows) == len(CODES)
    for line in rows:
        code, start, end = line.split("\t")
        assert code in CODES
        assert start == DAY1.isoformat()  # 起点收窄到分钟覆盖
        assert end == DAY3.isoformat()  # 末端 = 覆盖末端 +1 个 day 日历日（t+1 缓冲）
    # 日线里存在、分钟里没有标的的池：不落地空文件（宁可响亮报缺，不静默空集）
    assert not (env["provider"] / "instruments" / "csi500.txt").exists()
    assert (env["provider"] / "instruments" / "all.txt").is_file()


def test_instruments_end_is_coverage_end_when_day_calendar_has_no_successor(tmp_path):
    """分钟覆盖已经顶到 day 日历末位：没有次日可留，末端就是覆盖末端。"""
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path, days=(DAY1, DAY2)),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    _build(env)
    for line in (
        (env["provider"] / "instruments" / "csi300.txt").read_text().splitlines()
    ):
        _, _, end = line.split("\t")
        assert end == DAY2.isoformat()


def test_build_raises_when_universe_has_no_minute_data(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    (env["day"] / "instruments" / "csi300.txt").write_text(
        "sh601988\t2016-01-04\t2026-10-09\n", encoding="utf-8"
    )
    with pytest.raises(MP.MinuteProviderError):
        _build(env)
    assert not env["provider"].exists()


# ── 5. 金额单位测定 ─────────────────────────────────────────────────


@pytest.mark.parametrize("scale", [1.0, 1e4])
def test_detect_amount_scale_snaps_to_power_of_ten(scale):
    close = np.array([10.0, 11.0, 12.0] * 10)
    volume = np.array([100.0, 200.0, 300.0] * 10)
    amount = close * volume / scale
    detected, residual = MP.detect_amount_scale(amount, volume, close)
    assert detected == pytest.approx(scale)
    assert residual == pytest.approx(1.0, rel=1e-6)


def test_detect_amount_scale_insufficient_samples_falls_back():
    detected, residual = MP.detect_amount_scale(
        np.array([1.0]), np.array([1.0]), np.array([1.0])
    )
    assert detected == MP.DEFAULT_AMOUNT_SCALE
    assert np.isnan(residual)


def test_vwap_equals_price_when_amount_consistent(env):
    _build(env)
    _, close = _read_field(env["provider"] / "features" / "sz000001", "close")
    _, vwap = _read_field(env["provider"] / "features" / "sz000001", "vwap")
    np.testing.assert_allclose(vwap, close, rtol=1e-6)


def test_yuan_denominated_source_still_builds(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path, amount_scale=1.0),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    report = _build(env)
    assert report.symbols == len(CODES)
    meta = MP.read_minute_meta(env["provider"])
    assert not any("残留" in w or "残差" in w for w in meta["warnings"])
    _, vwap = _read_field(env["provider"] / "features" / "sh600000", "vwap")
    _, close = _read_field(env["provider"] / "features" / "sh600000", "close")
    np.testing.assert_allclose(vwap, close, rtol=1e-6)


# ── 6. 原子性 / 新鲜度 / 状态 ────────────────────────────────────────


def test_ensure_hits_fresh_provider_and_rebuilds_on_source_change(env):
    pdir, first = MP.ensure_minute_provider(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
    )
    assert first is not None
    assert pdir == env["provider"]
    _, second = MP.ensure_minute_provider(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
    )
    assert second is None  # 新鲜：零重建

    parquet = env["source"] / "600000.SH.parquet"
    df = pd.read_parquet(parquet)
    df.loc[df.index[-1], "close"] += 0.5
    df.to_parquet(parquet)
    st = parquet.stat()
    os.utime(parquet, ns=(st.st_mtime_ns + 10**9, st.st_mtime_ns + 10**9))

    _, third = MP.ensure_minute_provider(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
    )
    assert third is not None  # 源变化 → 重建
    _, close = _read_field(env["provider"] / "features" / "sh600000", "close")
    assert close[-1] == pytest.approx(
        (_raw_close("sh600000", DAY2, BARS[-1]) + 0.5) * 1.5, rel=1e-5
    )


def test_failed_build_keeps_previous_provider_intact(env):
    _build(env)
    before = MP.read_minute_meta(env["provider"])
    before_close = _read_field(env["provider"] / "features" / "sh600000", "close")[
        1
    ].copy()
    with pytest.raises(MP.MinuteProviderError):
        _build(env, symbols=["sh601988"])  # 源里没有该标的
    assert MP.is_minute_provider_ready(env["provider"])
    after = MP.read_minute_meta(env["provider"])
    assert after == before
    np.testing.assert_array_equal(
        _read_field(env["provider"] / "features" / "sh600000", "close")[1], before_close
    )


def test_status_reports_ready_coverage_and_stale(env):
    MP.ensure_minute_provider(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
    )
    status = MP.minute_provider_status(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
        universe="csi300",
    )
    assert status["ready"] is True
    assert status["stale"] is False
    assert status["coverage"]["start"] == DAY1.isoformat()
    assert status["coverage"]["end"] == DAY2.isoformat()
    assert status["coverage"]["days"] == 2
    assert status["coverage"]["bars"] == 10
    assert status["coverage"]["symbols"] == len(CODES)
    assert status["source_coverage"]["days"] == 2

    parquet = env["source"] / "000001.SZ.parquet"
    df = pd.read_parquet(parquet)
    df.to_parquet(parquet)
    st = parquet.stat()
    os.utime(parquet, ns=(st.st_mtime_ns + 10**9, st.st_mtime_ns + 10**9))
    status2 = MP.minute_provider_status(
        provider_dir=env["provider"],
        source_dir=env["source"],
        day_provider_dir=env["day"],
        universe="csi300",
    )
    assert status2["stale"] is True


def test_status_reports_error_when_source_missing(env):
    status = MP.minute_provider_status(
        provider_dir=env["provider"],
        source_dir=env["tmp"] / "nope",
        day_provider_dir=env["day"],
        universe="csi300",
    )
    assert status["ready"] is False
    assert status["source_error"]


# ── 7. 源扫描与路径解析 ──────────────────────────────────────────────


def test_scan_source_coverage_reports_missing_symbols(env):
    scan = MP.scan_source_coverage(
        env["source"], [*CODES, "sh601988"], universe="csi300"
    )
    assert scan.symbols_found == CODES
    assert scan.symbols_missing == ("sh601988",)
    assert scan.coverage.days == 2
    assert scan.coverage.bars == 10
    assert scan.per_symbol_first["sh600000"] == DAY1
    assert scan.per_symbol_last["sh600000"] == DAY2


def test_scan_source_coverage_keeps_auction_bar(tmp_path):
    """09:25 集合竞价 bar 原样保留（不造、不删）。"""
    src = tmp_path / "quantdb" / "1_kline_data" / "min1_kline"
    src.mkdir(parents=True)
    rows = [
        {
            "time": datetime(2026, 1, 5, 9, 25),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        },
        {
            "time": datetime(2026, 1, 5, 9, 30),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        },
    ]
    pd.DataFrame(rows).to_parquet(src / "600000.SH.parquet")
    scan = MP.scan_source_coverage(src, ["sh600000"])
    assert scan.coverage.bars == 2
    assert scan.per_symbol_first["sh600000"] == DAY1


def test_read_minute_frame_columns_and_window(env):
    """read_minute_frame：列名/类型与 provider 一致，窗口按交易日闭区间收窄。"""
    _build(env)
    frame = MP.read_minute_frame(env["provider"], CODES)
    assert list(frame.columns) == [f"${f}" for f in MP.MINUTE_FIELDS]
    assert frame.index.names == ["datetime", "instrument"]
    assert len(frame) == 10 * len(CODES)  # 无缺 bar：2 日 × 5 bar × 2 标的
    assert set(frame.index.get_level_values("instrument")) == set(CODES)
    assert frame["$close"].dtype == "float64"

    day2 = MP.read_minute_frame(env["provider"], CODES, start=DAY2, end=DAY2)
    assert len(day2) == 5 * len(CODES)
    assert {str(t.date()) for t in day2.index.get_level_values("datetime")} == {
        "2026-01-06"
    }

    assert MP.read_minute_frame(env["provider"], CODES, start=DAY3).empty


def test_read_minute_frame_drops_missing_bars(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path, drop=("sz000001", DAY2, "09:31:00")),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    _build(env)
    frame = MP.read_minute_frame(env["provider"], CODES)
    assert len(frame) == 10 * len(CODES) - 1  # 缺的那根 bar 不落行
    assert np.isfinite(frame["$close"]).all()


def test_read_minute_frame_values_match_bins(env):
    """数据面与 provider bin 同源：同一根 bar 取值逐位一致。"""
    _build(env)
    frame = MP.read_minute_frame(env["provider"], ["sh600000"])
    start_idx, values = MP._read_bin(
        env["provider"] / "features" / "sh600000" / "close.1min.bin"
    )
    assert frame.iloc[0]["$close"] == pytest.approx(float(values[0]))
    assert frame.iloc[-1]["$close"] == pytest.approx(float(values[-1]))


def test_read_minute_frame_rejects_unready_provider(tmp_path):
    with pytest.raises(MP.MinuteProviderError):
        MP.read_minute_frame(tmp_path / "nope", CODES)


def test_resolve_dirs_honor_env(monkeypatch, tmp_path):
    monkeypatch.setenv(MP._ENV_PROVIDER_DIR, str(tmp_path / "p"))
    monkeypatch.setenv(MP._ENV_SOURCE_DIR, str(tmp_path / "s"))
    monkeypatch.setenv(MP._ENV_DAY_PROVIDER, str(tmp_path / "d"))
    monkeypatch.delenv("QM_QUANTDB_DATA_DIR", raising=False)
    assert MP.resolve_minute_provider_dir() == tmp_path / "p"
    assert MP.resolve_minute_source_dir() == tmp_path / "s"
    assert MP.resolve_day_provider_dir() == tmp_path / "d"


def test_resolve_source_dir_falls_back_to_quantdb_root(monkeypatch, tmp_path):
    monkeypatch.delenv(MP._ENV_SOURCE_DIR, raising=False)
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path))
    resolved = MP.resolve_minute_source_dir()
    assert resolved == tmp_path / "1_kline_data" / "min1_kline"


def test_fingerprint_changes_when_day_factor_updates(env):
    _build(env)
    meta = MP.read_minute_meta(env["provider"])
    path = env["day"] / "features" / "sh600000" / "factor.day.bin"
    MP._write_bin(path, 0, np.array([1.0, 1.5, 1.5], dtype="<f4"))
    st = path.stat()
    os.utime(path, ns=(st.st_mtime_ns + 10**9, st.st_mtime_ns + 10**9))
    fp = MP.source_fingerprint(
        env["source"],
        meta["built_symbols"],
        universe="csi300",
        day_provider_dir=env["day"],
    )
    assert fp != meta["fingerprint"]  # 除权因子更新 → 需重建


def test_build_report_and_meta_facts(env):
    report = _build(env)
    assert report.symbols == len(CODES)
    assert report.bars == 10
    assert report.days == 2
    assert report.first == DAY1 and report.last == DAY2
    meta = json.loads((env["provider"] / "meta.json").read_text())
    assert meta["version"] == MP._META_VERSION
    assert meta["freq"] == "1min"
    assert meta["fields"] == list(MP.MINUTE_FIELDS)
    assert meta["built_symbols"] == list(CODES)
    assert BENCH in meta["day_layer_symbols"]
    assert meta["fingerprint"] == report.fingerprint
    assert MP.is_minute_provider_ready(env["provider"])


def test_fingerprint_changes_when_builder_version_bumps(env, monkeypatch):
    """构建口径升级（版本 +1）必须触发重建：盘上旧产物不能装作新鲜。"""
    _build(env)
    meta = MP.read_minute_meta(env["provider"])
    monkeypatch.setattr(MP, "_META_VERSION", MP._META_VERSION + 1)
    fp = MP.source_fingerprint(
        env["source"],
        meta["built_symbols"],
        universe="csi300",
        day_provider_dir=env["day"],
    )
    assert fp != meta["fingerprint"]


def test_duplicate_timestamps_are_deduped_with_warning(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    parquet = env["source"] / "600000.SH.parquet"
    df = pd.read_parquet(parquet)
    dup = df.iloc[[-1]]
    dup = dup.assign(close=dup["close"] + 0.7)
    pd.concat([df, dup], ignore_index=True).to_parquet(parquet)
    report = _build(env)
    assert any("重复时间戳" in w for w in report.warnings)
    _, close = _read_field(env["provider"] / "features" / "sh600000", "close")
    assert close[-1] == pytest.approx(
        (_raw_close("sh600000", DAY2, BARS[-1]) + 0.7) * 1.5, rel=1e-5
    )


def test_missing_day_factor_falls_back_to_one_with_warning(tmp_path):
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "qlib" / "cn_data_1min",
    }
    (env["day"] / "features" / "sz000001" / "factor.day.bin").unlink()
    report = _build(env, symbols=["sz000001"])
    assert any("复权因子按 1.0" in w for w in report.warnings)
    _, close = _read_field(env["provider"] / "features" / "sz000001", "close")
    assert close[0] == pytest.approx(_raw_close("sz000001", DAY1, BARS[0]), rel=1e-6)


def test_tmp_dirs_cleaned_after_failure(env):
    with pytest.raises(MP.MinuteProviderError):
        _build(env, symbols=["sh601988"])
    leftovers = [p for p in env["provider"].parent.iterdir() if ".building-" in p.name]
    assert leftovers == []


def test_build_creates_missing_provider_parent_dir(tmp_path):
    """首次运行：provider 的父目录还不存在时也要能建（/data/qlib 冷启动）。"""
    env = {
        "tmp": tmp_path,
        "source": _write_source(tmp_path),
        "day": _write_day_provider(tmp_path),
        "provider": tmp_path / "fresh" / "nested" / "cn_data_1min",
    }
    report = _build(env)
    assert report.symbols == len(CODES)
    assert MP.is_minute_provider_ready(env["provider"])


# ── 8. 数据空洞与可用窗口 ────────────────────────────────────────────

#: 空洞场景：源里 2026-01-06 之后直接跳到 2026-03-04（58 天断层，与生产同形态）
HOLE_AFTER = date(2026, 3, 4)


def test_calendar_gaps_separates_hole_from_holiday():
    """空洞判据是「相邻交易日相隔 > 14 自然日」：春节/国庆连休不算，真实断层算。"""
    holiday = [date(2026, 4, 30), date(2026, 5, 6)]  # 6 天连休（A 股最长 ~10 天）
    assert MP.calendar_gaps(holiday) == ()
    assert MP.calendar_gaps([DAY1, DAY2, HOLE_AFTER]) == ((DAY2, HOLE_AFTER),)


def test_calendar_gaps_threshold_is_exclusive_and_input_normalized():
    """阈值语义是"超过"：恰好 14 天不算；乱序/重复输入先规范化。"""
    assert MP.calendar_gaps([DAY1, DAY1 + timedelta(days=MP.MAX_GAP_DAYS)]) == ()
    assert MP.calendar_gaps([DAY1, DAY1 + timedelta(days=MP.MAX_GAP_DAYS + 1)]) == (
        (DAY1, DAY1 + timedelta(days=MP.MAX_GAP_DAYS + 1)),
    )
    unsorted_with_dupes = [HOLE_AFTER, DAY1, DAY2, DAY1]
    assert MP.calendar_gaps(unsorted_with_dupes) == ((DAY2, HOLE_AFTER),)
    assert MP.calendar_gaps([]) == ()


def test_terminal_usable_start_takes_block_after_last_hole():
    assert MP.terminal_usable_start([DAY1, DAY2]) == DAY1
    assert MP.terminal_usable_start([DAY1, DAY2, HOLE_AFTER]) == HOLE_AFTER
    # 两个空洞：取**最后一个**空洞之后（末端连续块）
    later = date(2026, 6, 1)
    assert MP.terminal_usable_start([DAY1, DAY2, HOLE_AFTER, later]) == later
    assert MP.terminal_usable_start([]) is None


def test_minute_data_facts_reports_hole_and_usable_block(tmp_path):
    days = (DAY1, DAY2, HOLE_AFTER)
    source = _write_source(tmp_path, days=(DAY1, DAY2, HOLE_AFTER))
    day = _write_day_provider(tmp_path, days=(*days, date(2026, 3, 5)))
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )

    facts = MP.minute_data_facts(provider)
    assert facts is not None
    assert (facts.coverage.start, facts.coverage.end) == (DAY1, HOLE_AFTER)
    assert facts.gaps == ((DAY2, HOLE_AFTER),)
    assert facts.usable_start == HOLE_AFTER
    assert facts.usable_days == 1  # 末端块只剩断层后那一个交易日

    assert facts.contains(HOLE_AFTER, HOLE_AFTER)
    assert not facts.contains(DAY1, DAY2)  # 全在空洞之前
    assert not facts.contains(DAY1, HOLE_AFTER)  # 跨空洞
    assert not facts.contains(date(2026, 3, 6), date(2026, 3, 6))  # 超出覆盖末端


def test_minute_data_facts_none_when_not_built(tmp_path):
    assert MP.minute_data_facts(tmp_path / "nope") is None


def test_minute_data_facts_raises_when_calendar_missing(tmp_path):
    """meta 在、日历被删 = provider 损坏：响亮抛，不假装"无空洞"。"""
    source = _write_source(tmp_path)
    day = _write_day_provider(tmp_path)
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    (provider / "calendars" / "1min.txt").unlink()
    with pytest.raises(MP.MinuteProviderError):
        MP.minute_data_facts(provider)


def test_build_warns_about_source_hole(tmp_path):
    """构建日志里的"N 个交易日"不许把断层藏起来。"""
    source = _write_source(tmp_path, days=(DAY1, DAY2, HOLE_AFTER))
    day = _write_day_provider(tmp_path, days=(DAY1, DAY2, HOLE_AFTER, date(2026, 3, 5)))
    provider = tmp_path / "qlib" / "cn_data_1min"
    report = MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    hole_warnings = [w for w in report.warnings if "空洞" in w]
    assert len(hole_warnings) == 1
    assert f"{DAY2} → {HOLE_AFTER}" in hole_warnings[0]
    assert str(HOLE_AFTER) in hole_warnings[0]


# ── 7. 挖掘/物化共用的“数据面”助手（原生分钟挖掘 2026-10-16） ─────────


def test_resolve_face_symbols_sorted_and_capped(env):
    _build(env)
    symbols = MP.resolve_face_symbols(env["provider"])
    assert symbols == sorted(symbols)
    assert set(symbols) == {"sh600000", "sz000001"}
    assert MP.resolve_face_symbols(env["provider"], debug=True) == symbols[:50]


def test_resolve_face_symbols_empty_provider_raises(tmp_path):
    with pytest.raises(MP.MinuteProviderError, match="没有已构建标的"):
        MP.resolve_face_symbols(tmp_path / "nope")


def test_face_h5_cache_path_single_naming(env):
    """命名单源：挖掘侧与物化侧共拼同一路径（各拼各的 = 口径漂移无报错）。"""
    path = MP.face_h5_cache_path(env["provider"], DAY1, DAY2)
    assert path.name == f"minute_pv_all_{DAY1.isoformat()}_{DAY2.isoformat()}.h5"
    assert path.parent == env["provider"] / "h5_cache"
    dbg = MP.face_h5_cache_path(env["provider"], DAY1, DAY2, debug=True)
    assert dbg.name.startswith("minute_pv_debug_")


def test_build_face_h5_writes_atomically_and_reuses_cache(env):
    _build(env)
    symbols = MP.resolve_face_symbols(env["provider"])
    face = MP.build_face_h5(env["provider"], symbols, DAY1, DAY2)
    assert face.is_file()
    assert face.read_bytes()[:8] == b"\x89HDF\r\n\x1a\n"
    frame = pd.read_hdf(face, key="data")
    assert isinstance(frame.index, pd.MultiIndex)
    assert frame.index.nlevels == 2
    assert not list(face.parent.glob("*.tmp-*"))  # 原子换名不留残渣

    mtime = face.stat().st_mtime_ns
    again = MP.build_face_h5(env["provider"], symbols, DAY1, DAY2)
    assert again == face
    assert face.stat().st_mtime_ns == mtime  # 命中缓存零重写


def test_build_face_h5_empty_window_raises(env):
    """窗口落在覆盖外 → 空帧 → 响亮抛，绝不回退旧缓存。"""
    _build(env)
    symbols = MP.resolve_face_symbols(env["provider"])
    with pytest.raises(MP.MinuteProviderError, match="分钟数据面为空"):
        MP.build_face_h5(env["provider"], symbols, date(2027, 1, 1), date(2027, 1, 2))
