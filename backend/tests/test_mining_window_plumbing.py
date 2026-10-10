"""自定义挖掘窗口 / 数据集选择的管道验证（移植 P1）。

契约（"真正生效"的验收面——UI 上选的东西必须在数据面上可见）：

1. **缓存身份 = 窗口 + 数据集**：默认组合（旧默认窗口 + 全数据集）沿用旧文件名
   ``daily_pv_all.h5``（存量缓存与外部脚本零变化）；自定义组合按内容分文件——
   窗口 A 的缓存不得冒充窗口 B 的数据面（静默错数据）。
2. **``_custom_windows``**：env ``QM_MINING_WINDOW_*`` 未设 = None（默认路径
   零变化）；非法配置记日志退回 None，不拦主流程。
3. **数据集选择控数据面**：未选中的数据集不 fetch、不并入；L2 未选中时整段
   跳过（选择要真正控制喂给 LLM 的列，而非只记 provenance）。
4. **窗口控数据面**：K 线/富化读取的 (start, end) = 挖掘窗口 → 检验终点
   （因子值要在检验段上算得出来），生成出的 h5 行日期落在该跨度内。
5. **缓存新鲜度按选择收窄**：L2 列覆盖检查只在 L2 被选中时有义务。

需要 pytables（h5 读写）——在 quantmind 容器内跑：
``python -m pytest backend/tests/test_mining_window_plumbing.py -q``
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd

from backend.services.engine.rd_agent import mining_datasets as MD
from backend.services.engine.rd_agent import rd_loop_wrapper as rw


def _w(s: str, e: str):
    """构造 ``mining_config.Window``（覆盖/窗口断言用）。"""
    from backend.services.engine.alpha_agent.mining_config import Window

    return Window(date.fromisoformat(s), date.fromisoformat(e))

# ── 1. 缓存身份 ──────────────────────────────────────────────────────


def test_h5_cache_name_default_combo_keeps_legacy_name():
    assert (
        rw._h5_cache_name(False, date(2020, 1, 1), date(2026, 12, 31), MD.SUPPORTED_DATASETS)
        == "daily_pv_all.h5"
    )
    assert (
        rw._h5_cache_name(True, date(2020, 1, 1), date(2026, 12, 31), MD.SUPPORTED_DATASETS)
        == "daily_pv_debug.h5"
    )


def test_h5_cache_name_keys_by_window_and_datasets():
    assert (
        rw._h5_cache_name(
            False, date(2016, 1, 4), date(2020, 6, 30), ("features_daily", "l2_factors")
        )
        == "daily_pv_all__2016-01-04_2020-06-30__fd+l2.h5"
    )
    # 只差数据集 → 不同文件；只差窗口 → 不同文件
    assert rw._h5_cache_name(
        False, date(2016, 1, 4), date(2020, 6, 30), ("features_daily",)
    ) != rw._h5_cache_name(
        False, date(2016, 1, 4), date(2020, 6, 30), ("features_daily", "l2_factors")
    )


# ── 2. env 窗口解析 ──────────────────────────────────────────────────


def _bare_wrapper() -> rw.RDLoopWrapper:
    w = rw.RDLoopWrapper.__new__(rw.RDLoopWrapper)  # 不跑 __init__（免拉起适配器）
    w.market = "a_share"
    return w


def _clear_window_env(monkeypatch) -> None:
    for key in (
        "QM_MINING_WINDOW_START",
        "QM_MINING_WINDOW_END",
        "QM_VALIDATION_WINDOW_START",
        "QM_VALIDATION_WINDOW_END",
        "QM_MINING_DATASETS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_custom_windows_absent_returns_none(monkeypatch):
    _clear_window_env(monkeypatch)
    assert _bare_wrapper()._custom_windows() is None


def test_custom_windows_parses_env(monkeypatch):
    _clear_window_env(monkeypatch)
    monkeypatch.setenv("QM_MINING_WINDOW_START", "2021-01-04")
    monkeypatch.setenv("QM_MINING_WINDOW_END", "2025-10-09")
    monkeypatch.setenv("QM_VALIDATION_WINDOW_START", "2025-10-10")
    monkeypatch.setenv("QM_VALIDATION_WINDOW_END", "2026-10-09")
    mining, valid = _bare_wrapper()._custom_windows()
    assert (mining.start, mining.end) == (date(2021, 1, 4), date(2025, 10, 9))
    assert (valid.start, valid.end) == (date(2025, 10, 10), date(2026, 10, 9))


def test_custom_windows_invalid_falls_back_without_raising(monkeypatch):
    _clear_window_env(monkeypatch)
    monkeypatch.setenv("QM_MINING_WINDOW_START", "2021-01-04")  # 半截配置
    assert _bare_wrapper()._custom_windows() is None


# ── 2b. 回测末端保护（qlib 日历 ∩ 数据覆盖） ─────────────────────────


def _fake_provider(tmp_path: Path, days: list[str]) -> str:
    """造一个只有 calendars/day.txt 的 provider 目录，返回其路径。"""
    cal = tmp_path / "calendars"
    cal.mkdir(parents=True, exist_ok=True)
    (cal / "day.txt").write_text("\n".join(days) + "\n", encoding="utf-8")
    return str(tmp_path)


def _days() -> list[str]:
    # 真实日历尾形：…9/29, 9/30, [国庆假期], 10/8, 10/9
    return ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]


def test_backtest_data_end_is_second_last_trading_day(tmp_path, monkeypatch):
    """终点留出 t+1 次日行情：日历倒数第二根 bar（末位会 IndexError）。"""
    monkeypatch.setattr(
        "backend.shared.qlib_paths.resolve_qlib_provider_uri",
        lambda market="CN": _fake_provider(tmp_path, _days()),
    )
    assert rw.qlib_backtest_data_end() == date(2026, 10, 8)


def test_backtest_data_end_snaps_coverage_end_to_trading_day(tmp_path, monkeypatch):
    """覆盖末端 ∩ 日历：落在假期（非交易日）时向前贴到最近交易日，绝不停在末日。"""
    monkeypatch.setattr(
        "backend.shared.qlib_paths.resolve_qlib_provider_uri",
        lambda market="CN": _fake_provider(tmp_path, _days()),
    )
    # 10-04 在假期里（安全日历上无此交易日）→ 贴回 9-30
    assert rw.qlib_backtest_data_end(_w("2016-01-04", "2026-10-04")) == date(2026, 9, 30)
    # 覆盖末端 = 日历末日 → 仍退到倒数第二个交易日
    assert rw.qlib_backtest_data_end(_w("2016-01-04", "2026-10-09")) == date(2026, 10, 8)
    # 覆盖整体早于日历起点 → 无可用保护值（不设假保护）
    assert rw.qlib_backtest_data_end(_w("2016-01-04", "2016-12-31")) is None


def test_backtest_data_end_unreadable_is_none(tmp_path, monkeypatch):
    """provider 未就绪 / 未知市场 → None = 不做末端保护（不拦挖掘）。"""
    monkeypatch.setattr(
        "backend.shared.qlib_paths.resolve_qlib_provider_uri",
        lambda market="CN": str(tmp_path / "nope"),
    )
    assert rw.qlib_backtest_data_end() is None
    assert rw.qlib_backtest_data_end(market="not_a_market") is None


def test_coverage_from_dir_reads_daily_forward_partitions(tmp_path):
    """覆盖 = 挖掘数据面同源库（daily_forward 分区）的 min/max；无分区回 None。"""
    import pytest

    base = tmp_path / "1_kline_data" / "daily_forward"
    for d in ("20260104", "20160104", "20261009"):
        (base / f"dt={d}").mkdir(parents=True)
    assert rw.quantdb_coverage_from_dir(tmp_path) == _w("2016-01-04", "2026-10-09")
    assert rw.quantdb_coverage_from_dir(None) is None

    # 库在但没有任何分区 → None（语义 = 不做覆盖校验，不造假覆盖）
    empty = tmp_path / "empty"
    (empty / "1_kline_data" / "daily_forward").mkdir(parents=True)
    assert rw.quantdb_coverage_from_dir(empty) is None

    # 目录都不存在 → 抛 OSError（不静默吞；调用方决定放行还是拦）
    with pytest.raises(OSError):
        rw.quantdb_coverage_from_dir(tmp_path / "nope")


# ── 3. 数据集选择控数据面（_merge_enrich） ───────────────────────────


class _EnrichHub:
    """记录 fetch 了哪些数据集、并入请求列。"""

    def __init__(self) -> None:
        self.datasets: list[str] = []

    def fetch_ml_columns(self, dataset, columns, start, end, symbols=None):  # noqa: ANN001
        self.datasets.append(dataset)
        rows = [
            {"symbol": "600036.SH", "trade_date": pd.Timestamp("2026-01-05")}
            | dict.fromkeys(columns, 1.5)
        ]
        return pd.DataFrame(rows)


def _kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600036.SH"],
            "trade_date": pd.to_datetime(["2026-01-05"]),
            "close": [2.0],
        }
    )


def test_merge_enrich_only_selected_datasets():
    hub = _EnrichHub()
    out = _bare_wrapper()._merge_enrich(
        _kline_df(),
        hub,
        date(2021, 1, 4),
        date(2026, 10, 9),
        None,
        datasets=("l1_factors",),
    )
    assert hub.datasets == ["l1_factors"], "未选中的 features_daily 不得被读取"
    assert "turn_5" in out.columns
    assert "rsi_14" not in out.columns


def test_merge_enrich_none_datasets_means_all(monkeypatch):
    _clear_window_env(monkeypatch)
    hub = _EnrichHub()
    out = _bare_wrapper()._merge_enrich(
        _kline_df(), hub, date(2021, 1, 4), date(2026, 10, 9), None
    )
    assert hub.datasets == ["features_daily", "l1_factors"]
    assert "rsi_14" in out.columns and "turn_5" in out.columns


def test_merge_enrich_reads_env_selection(monkeypatch):
    _clear_window_env(monkeypatch)
    monkeypatch.setenv("QM_MINING_DATASETS", "features_daily")
    hub = _EnrichHub()
    out = _bare_wrapper()._merge_enrich(
        _kline_df(), hub, date(2021, 1, 4), date(2026, 10, 9), None
    )
    assert hub.datasets == ["features_daily"]
    assert "turn_5" not in out.columns


# ── 4. 窗口端到端控数据面（_generate_h5_from_parquet） ───────────────


class _GenHub:
    """最小可用的 QuantDBDataHub 替身：K 线 + 富化列，记录请求窗口。"""

    def __init__(self, dates: list[str]) -> None:
        self.available = True
        self.dates = dates
        self.kline_spans: list[tuple[date, date]] = []
        self.ml_calls: list[tuple[str, list[str]]] = []

    def fetch_stock_list(self):  # noqa: ANN201
        return pd.DataFrame({"Symbol": ["600036.SH"]})

    def _rows(self, symbols, start: date, end: date, cols: list[str]):  # noqa: ANN001
        return [
            {"symbol": s, "trade_date": pd.Timestamp(d), **dict.fromkeys(cols, 1.0)}
            for s in symbols
            for d in self.dates
            if start <= date.fromisoformat(d) <= end
        ]

    def fetch_daily_kline_batch(self, symbols, start, end, adjust="qfq"):  # noqa: ANN001
        self.kline_spans.append((start, end))
        base = ["open", "high", "low", "close", "volume", "amount"]
        return pd.DataFrame(self._rows(symbols, start, end, base))

    def fetch_ml_columns(self, dataset, columns, start, end, symbols=None):  # noqa: ANN001
        self.ml_calls.append((dataset, list(columns)))
        return pd.DataFrame(self._rows(symbols or ["600036.SH"], start, end, list(columns)))


def _patch_hub(monkeypatch, hub: _GenHub) -> None:
    from backend.services.engine.data_platform import quantdb_hub as hub_mod

    monkeypatch.setattr(hub_mod, "QuantDBDataHub", lambda _dir: hub)


def test_generate_h5_default_combo_is_unchanged(monkeypatch, tmp_path: Path):
    """无自定义 env：窗口/缓存名/数据集保持迁移前行为（零变化保证）。"""
    _clear_window_env(monkeypatch)
    hub = _GenHub(["2020-06-01", "2024-06-03"])
    _patch_hub(monkeypatch, hub)
    out = tmp_path / "daily_pv.h5"

    ok = _bare_wrapper()._generate_h5_from_parquet(str(tmp_path), str(out))

    assert ok is True
    assert set(hub.kline_spans) == {(date(2020, 1, 1), date(2026, 12, 31))}
    assert {d for d, _ in hub.ml_calls} == {"features_daily", "l1_factors"}
    cache = tmp_path / ".h5_cache" / "daily_pv_all.h5"
    assert cache.exists(), "默认组合必须沿用旧缓存文件名"
    assert out.exists()


def test_generate_h5_custom_window_and_dataset(monkeypatch, tmp_path: Path):
    """自定义窗口+数据集：读取窗口 = 挖掘窗口→检验终点；未选数据集不出现。"""
    _clear_window_env(monkeypatch)
    monkeypatch.setenv("QM_MINING_WINDOW_START", "2021-01-04")
    monkeypatch.setenv("QM_MINING_WINDOW_END", "2022-12-31")
    monkeypatch.setenv("QM_VALIDATION_WINDOW_START", "2023-01-01")
    monkeypatch.setenv("QM_VALIDATION_WINDOW_END", "2023-12-31")
    monkeypatch.setenv("QM_MINING_DATASETS", "features_daily")
    hub = _GenHub(["2019-06-03", "2021-06-01", "2023-06-01", "2025-06-02"])
    _patch_hub(monkeypatch, hub)
    out = tmp_path / "daily_pv.h5"

    ok = _bare_wrapper()._generate_h5_from_parquet(str(tmp_path), str(out))

    assert ok is True
    # 数据面跨度 = 挖掘起点 → 检验终点（检验段数据必须可算因子）
    assert set(hub.kline_spans) == {(date(2021, 1, 4), date(2023, 12, 31))}
    assert {d for d, _ in hub.ml_calls} == {"features_daily"}
    cache = tmp_path / ".h5_cache" / "daily_pv_all__2021-01-04_2023-12-31__fd.h5"
    assert cache.exists(), f"应按窗口+数据集分文件，实际: {list((tmp_path/'.h5_cache').iterdir())}"

    frame = pd.read_hdf(out, key="data")
    got = sorted({d.date().isoformat() for d in frame.index.get_level_values("datetime")})
    assert got == ["2021-06-01", "2023-06-01"], "窗口外行必须被排除"
    cols = set(frame.columns)
    assert "$rsi_14" in cols and "$turn_5" not in cols, "未选数据集的列不得进 h5"


# ── 5. 缓存新鲜度按选择收窄 ──────────────────────────────────────────


def _touch_partition(root: Path, rel: str, ts: float) -> None:
    d = root / rel / "dt=20260101"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "data.parquet"
    f.write_bytes(b"x")
    os.utime(f, (ts, ts))


def test_h5_cache_fresh_l2_obligation_only_when_selected(
    tmp_path: Path, monkeypatch
) -> None:
    t0 = 1_700_000_000.0
    _touch_partition(tmp_path, "1_kline_data/daily_forward", t0)
    _touch_partition(tmp_path, "6_ml_datasets/features_daily", t0)
    cache = tmp_path / "cache.h5"
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-01"), "sh600036")], names=["datetime", "instrument"]
    )
    pd.DataFrame({"$close": [1.0]}, index=idx).to_hdf(cache, key="data", mode="w")
    os.utime(cache, (t0 + 100, t0 + 100))

    # 本机有 L2 库，但本次任务没选 L2 → 缓存不含 L2 列是正确的
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: ["alpha_x"])
    assert (
        rw.RDLoopWrapper._h5_cache_fresh(
            str(tmp_path), str(cache), datasets=("features_daily",)
        )
        is True
    )
    # 选了 L2 → 缺列即过期
    assert (
        rw.RDLoopWrapper._h5_cache_fresh(
            str(tmp_path), str(cache), datasets=("features_daily", "l2_factors")
        )
        is False
    )
    # 未选中的数据集更新不影响这份缓存的构成
    _touch_partition(tmp_path, "6_ml_datasets/l1_factors", t0 + 200)
    assert (
        rw.RDLoopWrapper._h5_cache_fresh(
            str(tmp_path), str(cache), datasets=("features_daily",)
        )
        is True
    )
