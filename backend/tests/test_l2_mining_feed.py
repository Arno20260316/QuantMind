"""T-MV-07：L2 微观结构因子全量接入挖掘数据面 + 正名。

验收（框架 §4）：「挖掘可按 L2 方向跑；数据面列清单与目录一致」。
三条纪律钉死：
1. **列清单 = 磁盘事实**：``l2_factor_columns`` 现场读 parquet schema，剔除
   键列与 OHLCV 锚列（锚列与 base h5 的 $open 等重名，混入即 $ 引用错位），
   输出字母序（parquet 物理列序随日漂移，历史教训）。测试一律用 tmp 夹具，
   列数不写死。
2. **分块按键定位直写预分配大块**（2026-10-14 OOM 修复后契约）：200+ 列按
   ``_L2_FETCH_CHUNK`` 分块，每块 ``get_indexer`` 按键定位写入 (nrows×ncols)
   float32 大块；行序与 df 一一对应、未覆盖行 NaN；任一块失败只跳过该块；
   l2 重复键（HK daily_forward 双来源同型坑）→ 整块跳过（等价旧版按行数
   膨胀的判据），绝不把错行写进 GB 级共享缓存。
3. **正名**：LLM 提示面（``_l2_referenceable_columns``）与数据面同一列清单
   单源；种子模板以 ``MicroSeed_`` 前缀与 L2 实列区分。
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.services.engine.rd_agent import rd_loop_wrapper as rw
from backend.services.engine.rd_agent.market_adapters.a_share import AShareAdapter

_ANCHORS = ("open", "high", "low", "close", "volume", "amount")


def _write_l2_partition(
    quantdb_dir: Path,
    dt: str,
    columns: list[str],
    *,
    n: int = 3,
    with_data: bool = True,
) -> Path:
    """伪造一个 l2_factors 分区（列序故意乱序，验证选择器不依赖文件序）。"""
    part = quantdb_dir / "6_ml_datasets" / "l2_factors" / f"dt={dt}"
    part.mkdir(parents=True, exist_ok=True)
    if not with_data:
        return part
    data: dict[str, object] = {
        "date": pd.date_range("2026-01-01", periods=n),
        "symbol": ["600036.SH"] * n,
    }
    for c in columns:
        data[c] = np.arange(n, dtype="float64") + 1.0
    pq.write_table(pa.table(data), part / "data.parquet")
    return part


class _FakeHub:
    """记录分块请求并返回可定制 frame 的 duck-type hub。"""

    def __init__(
        self, frame: pd.DataFrame, *, fail_from_call: int | None = None
    ) -> None:
        self.frame = frame
        self.fail_from_call = fail_from_call
        self.calls: list[list[str]] = []

    def fetch_ml_columns(self, dataset, columns, start, end, symbols=None):  # noqa: ANN001
        if self.fail_from_call is not None and len(self.calls) >= self.fail_from_call:
            raise RuntimeError("boom")
        self.calls.append(list(columns))
        cols = [c for c in columns if c in self.frame.columns]
        return self.frame[["symbol", "trade_date", *cols]].copy()


def _bare_wrapper() -> rw.RDLoopWrapper:
    w = rw.RDLoopWrapper.__new__(
        rw.RDLoopWrapper
    )  # 不跑 __init__（避免拉起适配器注册）
    w.market = "a_share"
    return w


# ── 1. 列清单 = 磁盘事实 ──────────────────────────────────────────────


def test_l2_factor_columns_sorted_minus_keys_and_anchors(tmp_path: Path) -> None:
    _write_l2_partition(
        tmp_path,
        "20260102",
        [*_ANCHORS, "micro_vpin_100", "flow_cancel_ratio", "vol_realized_5min"],
    )
    assert rw.l2_factor_columns(str(tmp_path)) == [
        "flow_cancel_ratio",
        "micro_vpin_100",
        "vol_realized_5min",
    ]


def test_l2_factor_columns_empty_when_missing_or_no_partitions(tmp_path: Path) -> None:
    assert rw.l2_factor_columns(str(tmp_path)) == []  # 目录不存在
    assert rw.l2_factor_columns("") == []
    (tmp_path / "6_ml_datasets" / "l2_factors").mkdir(parents=True)  # 有库目录无分区
    assert rw.l2_factor_columns(str(tmp_path)) == []


def test_l2_factor_columns_latest_partition_wins_and_empty_falls_back(
    tmp_path: Path,
) -> None:
    _write_l2_partition(tmp_path, "20260101", ["micro_old"])
    # 最新分区无 parquet（坏分区）→ 回退到早先可读分区
    _write_l2_partition(tmp_path, "20260102", ["micro_new"], with_data=False)
    assert rw.l2_factor_columns(str(tmp_path)) == ["micro_old"]
    # 最新分区可读 → 以最新为准
    _write_l2_partition(tmp_path, "20260102", ["micro_new", "flow_x"])
    assert rw.l2_factor_columns(str(tmp_path)) == ["flow_x", "micro_new"]


# ── 2. 分块按键定位直写大块 ──────────────────────────────────────────


def _l2_frame(dates: list[str], symbols: list[str], cols: list[str]) -> pd.DataFrame:
    rows = []
    for s in symbols:
        for d in dates:
            rows.append({"symbol": s, "trade_date": pd.Timestamp(d)})
    frame = pd.DataFrame(rows)
    for i, c in enumerate(cols):
        frame[c] = np.arange(len(frame), dtype="float64") + i
    return frame


def test_merge_l2_factors_chunked_block_alignment_float32(
    tmp_path: Path, monkeypatch
) -> None:
    cols = [f"micro_f{i:03d}" for i in range(60)]  # > _L2_FETCH_CHUNK 强制分块
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: list(cols))

    df = pd.DataFrame(
        {
            "symbol": ["600036.SH", "600036.SH", "000001.SZ", "000001.SZ"],
            "trade_date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"]
            ),
            "close": [1.0, 2.0, 3.0, 4.0],
        }
    )
    # l2 只覆盖部分 (symbol, date)：未覆盖行必须留 NaN（左对齐语义）
    hub = _FakeHub(_l2_frame(["2026-01-01", "2026-01-02"], ["600036.SH"], cols))

    block, merged = _bare_wrapper()._merge_l2_factors(
        df, hub, str(tmp_path), None, None, None
    )

    assert block is not None and merged == cols
    assert block.shape == (len(df), len(cols)), "行序与 df 一一对应，列序同 merged"
    assert block.dtype == np.float32
    assert [len(c) for c in hub.calls] == [
        rw._L2_FETCH_CHUNK,
        len(cols) - rw._L2_FETCH_CHUNK,
    ]
    assert [c for call in hub.calls for c in call] == cols, (
        "分块并集 = 全量清单，序稳定"
    )
    # 值 = sub 行序 + 列序（_l2_frame 口径）：600036.SH 01-02 是 sub 行 1
    assert block[0, 0] == 0.0 and block[1, 0] == 1.0
    assert block[1, 50] == 51.0, "第二块列也须定位正确"
    assert np.isnan(block[2, 0]) and np.isnan(block[3, 50]), "未覆盖行留 NaN"


def test_merge_l2_factors_chunk_failure_skips_chunk(
    tmp_path: Path, monkeypatch
) -> None:
    cols = [f"micro_f{i:03d}" for i in range(60)]
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: list(cols))
    df = pd.DataFrame(
        {
            "symbol": ["600036.SH"],
            "trade_date": pd.to_datetime(["2026-01-01"]),
        }
    )
    hub = _FakeHub(_l2_frame(["2026-01-01"], ["600036.SH"], cols), fail_from_call=1)

    block, merged = _bare_wrapper()._merge_l2_factors(
        df, hub, str(tmp_path), None, None, None
    )

    assert len(hub.calls) == 1, "第二块抛异常被吞，仅第一块成功"
    assert block is not None
    assert merged == cols[: rw._L2_FETCH_CHUNK], "失败块不并入，成功块保留"
    assert block.shape == (1, rw._L2_FETCH_CHUNK), "整宽块按实际并入列收窄"
    assert block[0, 0] == 0.0 and block[0, rw._L2_FETCH_CHUNK - 1] == (
        rw._L2_FETCH_CHUNK - 1
    ), "并入列的值定位不受跳块影响"


def test_merge_l2_factors_duplicate_keys_skip_chunk(
    tmp_path: Path, monkeypatch
) -> None:
    cols = ["micro_dup"]
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: list(cols))
    df = pd.DataFrame(
        {
            "symbol": ["600036.SH", "600036.SH"],
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        }
    )
    dup = _l2_frame(["2026-01-01", "2026-01-01"], ["600036.SH"], cols)  # 同日两行
    hub = _FakeHub(dup)

    block, merged = _bare_wrapper()._merge_l2_factors(
        df, hub, str(tmp_path), None, None, None
    )

    assert merged == [] and block is None, (
        "重复键块整块跳过（get_indexer 非唯一报错，等价旧版行数膨胀判据）"
    )


def test_merge_l2_factors_empty_column_list_returns_none(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: [])
    df = pd.DataFrame({"symbol": ["a"], "trade_date": pd.to_datetime(["2026-01-01"])})
    block, merged = _bare_wrapper()._merge_l2_factors(
        df, _FakeHub(df), str(tmp_path), None, None, None
    )
    assert merged == [] and block is None


def test_merge_l2_factors_maps_by_keys_not_row_position(
    tmp_path: Path, monkeypatch
) -> None:
    """df 行序与 sub 行序相反时按键定位：行序跟随 df，绝不按位置取值。"""
    cols = ["micro_x"]
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: list(cols))
    # df 行序：000001.SZ 在前——与 sub（600036.SH 在前）不同
    df = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "600036.SH", "000001.SZ", "600036.SH"],
            "trade_date": pd.to_datetime(
                ["2026-01-02", "2026-01-02", "2026-01-01", "2026-01-01"]
            ),
        }
    )
    # sub 行序：600036(01-01→0, 01-02→1), 000001(01-01→2, 01-02→3)
    hub = _FakeHub(
        _l2_frame(["2026-01-01", "2026-01-02"], ["600036.SH", "000001.SZ"], cols)
    )

    block, merged = _bare_wrapper()._merge_l2_factors(
        df, hub, str(tmp_path), None, None, None
    )

    assert merged == cols and block is not None
    assert block[:, 0].tolist() == [3.0, 1.0, 2.0, 0.0], (
        "000001 01-02=3, 600036 01-02=1, 000001 01-01=2, 600036 01-01=0"
    )


def test_malloc_trim_never_raises() -> None:
    """驻留优化助手：非 glibc/无符号环境静默通过（不影响正确性）。"""
    rw._malloc_trim()


# ── 3. 缓存失效随 L2 更新 ────────────────────────────────────────────


def test_h5_cache_fresh_tracks_l2_mtime(tmp_path: Path) -> None:
    t0 = 1_700_000_000.0

    def touch(rel: str, ts: float) -> Path:
        d = tmp_path / rel / "dt=20260101"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "data.parquet"
        f.write_bytes(b"x")
        os.utime(f, (ts, ts))
        return f

    for rel in (
        "1_kline_data/daily_forward",
        "6_ml_datasets/features_daily",
        "6_ml_datasets/l1_factors",
        "6_ml_datasets/l2_factors",
    ):
        touch(rel, t0)
    cache = tmp_path / "cache.h5"
    cache.write_bytes(b"x")
    os.utime(cache, (t0 + 100, t0 + 100))

    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is True
    os.utime(touch("6_ml_datasets/l2_factors", t0 + 200), (t0 + 200, t0 + 200))
    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is False


def test_h5_cache_fresh_requires_l2_column_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    """存量缓存 mtime 新鲜但缺整批 L2 列 → 判定过期；补列后转新鲜。

    没有这条覆盖检查，T-MV-07 之前生成的共享缓存（mtime 比所有输入都新）
    会永远命中，「数据面列清单与目录一致」在存量部署上永不成立。
    """
    t0 = 1_700_000_000.0
    for rel in (
        "1_kline_data/daily_forward",
        "6_ml_datasets/features_daily",
        "6_ml_datasets/l1_factors",
    ):
        d = tmp_path / rel / "dt=20260101"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "data.parquet"
        f.write_bytes(b"x")
        os.utime(f, (t0, t0))

    cache = tmp_path / "cache.h5"
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-01-01"), "sh600036")], names=["datetime", "instrument"]
    )

    def write_cache(columns: dict[str, list[float]]) -> None:
        pd.DataFrame(columns, index=idx).to_hdf(cache, key="data", mode="w")
        os.utime(cache, (t0 + 100, t0 + 100))

    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: ["alpha_x", "beta_y"])
    write_cache({"$close": [1.0]})
    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is False, (
        "缺 L2 列的存量缓存必须判过期"
    )

    write_cache({"$close": [1.0], "$alpha_x": [1.0], "$beta_y": [2.0]})
    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is True

    # 损坏缓存读不出列 → 覆盖检查失败 → 判过期（宁重算不放行）
    cache.write_bytes(b"not-an-h5")
    os.utime(cache, (t0 + 100, t0 + 100))
    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is False

    # 本机无 L2 库（列清单为空）→ 无覆盖义务，mtime 新鲜即新鲜
    write_cache({"$close": [1.0]})
    monkeypatch.setattr(rw, "l2_factor_columns", lambda _d: [])
    assert rw.RDLoopWrapper._h5_cache_fresh(str(tmp_path), str(cache)) is True


# ── 4. 正名（提示面与数据面同源） ────────────────────────────────────


def test_l2_referenceable_columns_uses_disk_list_and_dictionary(
    tmp_path: Path, monkeypatch
) -> None:
    _write_l2_partition(
        tmp_path, "20260102", [*_ANCHORS, "micro_vpin_100", "flow_cancel_ratio"]
    )
    monkeypatch.setattr(
        AShareAdapter, "_get_quantdb_dir", staticmethod(lambda: str(tmp_path))
    )

    out = AShareAdapter._l2_referenceable_columns()

    assert set(out) == {"micro_vpin_100", "flow_cancel_ratio"}, "锚列/键列不得进提示面"
    assert "L2 微观结构列" in out["micro_vpin_100"]
    assert "VPIN" in out["micro_vpin_100"], "释义取自 quantdb_factor_dictionary"
    assert "撤单" in out["flow_cancel_ratio"]


def test_usable_quantdb_columns_includes_l2_real_columns(
    tmp_path: Path, monkeypatch
) -> None:
    _write_l2_partition(tmp_path, "20260102", ["micro_vpin_100"])
    monkeypatch.setattr(
        AShareAdapter, "_get_quantdb_dir", staticmethod(lambda: str(tmp_path))
    )

    cols = AShareAdapter._usable_quantdb_columns()

    assert "rsi_14" in cols, "原 32 列富化清单保留"
    assert "micro_vpin_100" in cols, "L2 真列并入 $ 可引用清单"


def test_l2_referenceable_columns_degrades_to_empty(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        AShareAdapter, "_get_quantdb_dir", staticmethod(lambda: str(tmp_path))
    )
    assert AShareAdapter._l2_referenceable_columns() == {}


def test_seed_templates_are_not_l2_columns() -> None:
    seeds = AShareAdapter._l2_factor_expressions()
    assert seeds, "种子模板非空"
    assert all(k.startswith("MicroSeed_") for k in seeds), (
        "T-MV-07 正名：种子模板不得再以 L2_ 前缀冒充 L2 实列"
    )


# ── 5. 端到端组装行序（2026-10-14 OOM 修复）──────────────────────────
#
# 修复把组装末尾的整表 sort_index 换成「上游预排 + 组装即有序」。行序口径
# 必须与 sort_index 逐位一致：二级键是 qlib 形 instrument 的字典序
# （bj… < sh… < sz…），而非原始后缀码（000001.SZ < 430047.BJ < 600036.SH，
# 跨交易所会错序）。下列测试用乱序输入 + 跨交易所样本钉死这条口径，并覆盖
# 预排失效时的兜底排序路径。


class _OrderHub:
    """跨交易所标的 + 乱序输入的端到端 hub 替身（K线/富化/L2 全走真构建路径）。"""

    _L2_VAL = {"600036.SH": 1.0, "000001.SZ": 2.0, "430047.BJ": 3.0}

    def __init__(self, dates: list[str], *, nan_symbol_row: bool = False) -> None:
        self.available = True
        self.dates = dates
        self.nan_symbol_row = nan_symbol_row

    def fetch_stock_list(self):  # noqa: ANN201
        return pd.DataFrame({"Symbol": ["600036.SH", "000001.SZ", "430047.BJ"]})

    def _base_cols(self) -> list[str]:
        return ["open", "high", "low", "close", "volume", "amount"]

    def fetch_daily_kline_batch(self, symbols, start, end, adjust="qfq"):  # noqa: ANN001
        if adjust != "qfq":
            return pd.DataFrame()  # 不复权缺失 → factor = 1.0（本测不关心）
        rows = [
            {
                "symbol": s,
                "trade_date": pd.Timestamp(d),
                **dict.fromkeys(self._base_cols(), 1.0),
            }
            for s in symbols
            for d in self.dates
        ]
        if self.nan_symbol_row:
            rows.append(
                {
                    "symbol": np.nan,
                    "trade_date": pd.Timestamp(self.dates[0]),
                    **dict.fromkeys(self._base_cols(), 1.0),
                }
            )
        return pd.DataFrame(rows[::-1])  # 故意逆序：行序必须由构建器排序键决定

    def fetch_ml_columns(self, dataset, columns, start, end, symbols=None):  # noqa: ANN001
        if dataset == "l2_factors":
            rows = [
                {
                    "symbol": s,
                    "trade_date": pd.Timestamp(d),
                    **dict.fromkeys(columns, self._L2_VAL[s]),
                }
                for s in (symbols or list(self._L2_VAL))
                for d in self.dates
            ]
            return pd.DataFrame(rows)
        if dataset == "features_daily":
            rows = [
                {
                    "symbol": s,
                    "trade_date": pd.Timestamp(d),
                    **dict.fromkeys(columns, 1.0),
                }
                for s in (symbols or list(self._L2_VAL))
                for d in self.dates
            ]
            return pd.DataFrame(rows[::-1])  # 富化 merge 也不得打乱左表行序
        return pd.DataFrame()  # l1_factors：走空表跳过分支


def _clear_mining_env(monkeypatch) -> None:  # noqa: ANN001
    for name in (
        "QM_MINING_WINDOW_START",
        "QM_MINING_WINDOW_END",
        "QM_VALIDATION_WINDOW_START",
        "QM_VALIDATION_WINDOW_END",
        "QM_MINING_DATASETS",
    ):
        monkeypatch.delenv(name, raising=False)


def _patch_gen_hub(monkeypatch, hub: _OrderHub) -> None:  # noqa: ANN001
    from backend.services.engine.data_platform import quantdb_hub as hub_mod

    monkeypatch.setattr(hub_mod, "QuantDBDataHub", lambda _dir: hub)


def test_generate_h5_instrument_ordering_matches_sort_index(
    monkeypatch, tmp_path: Path
) -> None:
    _clear_mining_env(monkeypatch)
    _write_l2_partition(tmp_path, "20260102", ["micro_x"])
    _patch_gen_hub(monkeypatch, _OrderHub(["2026-01-02", "2026-01-01"]))
    out = tmp_path / "daily_pv.h5"

    ok = _bare_wrapper()._generate_h5_from_parquet(
        str(tmp_path), str(out), use_cache=False
    )

    assert ok is True
    frame = pd.read_hdf(out, key="data")
    assert frame.index.is_monotonic_increasing
    idx = list(frame.index)
    # 与 sort_index 逐位一致：时刻升序，同时刻按 qlib instrument 字典序
    assert idx == sorted(idx, key=lambda t: (t[0], t[1])), (
        "预排/免排序路径的行序必须与 sort_index 逐位一致"
    )
    assert {(t[0].date().isoformat(), t[1]) for t in idx} == {
        (d, inst)
        for d in ("2026-01-01", "2026-01-02")
        for inst in ("bj430047", "sh600036", "sz000001")
    }, "跨交易所样本必须按 qlib 名排序（bj<sh<sz），不得按原始后缀码"
    # 直写块行序与 index 一一对应：每行 L2 值 = 该行标的的常量
    expect = {
        rw.RDLoopWrapper._to_qlib_symbol(k): v for k, v in _OrderHub._L2_VAL.items()
    }
    vals = frame["$micro_x"].to_numpy()
    assert all(v == expect[t[1]] for v, t in zip(vals, idx, strict=True)), (
        "按键直写块与行序错位"
    )
    assert frame["$close"].dtype == np.float64
    # 富化列经 merge 后仍在、f32 口径不变（行序断言已覆盖 merge 保序）
    from backend.services.engine.rd_agent.mining_datasets import ENRICH_BY_DATASET

    fd_out = sorted(set(ENRICH_BY_DATASET["features_daily"].values()))
    assert f"${fd_out[0]}" in frame.columns
    assert frame[f"${fd_out[0]}"].dtype == np.float32


def test_generate_h5_fallback_sort_when_symbols_not_presortable(
    monkeypatch, tmp_path: Path
) -> None:
    """NaN 符号行令预排失效 → 兜底 sort_index 仍须保证行序正确。"""
    _clear_mining_env(monkeypatch)
    _write_l2_partition(tmp_path, "20260102", ["micro_x"])
    _patch_gen_hub(
        monkeypatch, _OrderHub(["2026-01-02", "2026-01-01"], nan_symbol_row=True)
    )
    out = tmp_path / "daily_pv.h5"

    ok = _bare_wrapper()._generate_h5_from_parquet(
        str(tmp_path), str(out), use_cache=False
    )

    assert ok is True
    frame = pd.read_hdf(out, key="data")
    idx = list(frame.index)
    # 注：fixed 格式 h5 会把 instrument 层的 'nan' 字符串往返成 float NaN
    # （pytables 对 object 列的固有损耗，真实数据无此符号形态），故读回帧的
    # is_monotonic_increasing 不可用；按「字符串行保序 + NaN 行计数」断言。
    real = [t for t in idx if isinstance(t[1], str)]
    assert real == sorted(real, key=lambda t: (t[0], t[1])), (
        "兜底 sort_index 仍须保证真实行序与 sort_index 一致"
    )
    nan_rows = [t for t in idx if not isinstance(t[1], str)]
    assert len(nan_rows) == 1 and nan_rows[0][0].date().isoformat() == "2026-01-02", (
        "NaN 符号行在预排失效时按兜底路径保留"
    )
    assert len(idx) == 7
