"""QuantFutures 期货/贵金属数据平台测试。

策略：
- 不联网；不依赖 akshare API 的真实响应
- 校验 catalog 数据集 → akshare field/extra task 分发映射（quantfutures_daily_sync）
- 校验日K标准化（akshare → QuantDB schema，volume/amount 兜底）
- 校验分区写入（dt=YYYYMMDD/data.parquet，增量去重）
- 校验管理控制台 FUTURES 数据集目录（_default_datasets("FUTURES")）
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.scripts.akshare_futures_extra import _CN_MAIN_STEMS
from backend.scripts.akshare_futures_sync import (
    CN_MAIN,
    FOREIGN_SYMBOLS,
    KLINE_COLS,
    SGE_SYMBOLS,
    _normalise_daily,
    _write_kline_partition,
)
from backend.scripts.quantfutures_daily_sync import (
    DATASET_FIELDS,
    DEFAULT_EXTRA_DATASETS,
    EXTRA_DATASET_TASKS,
    run,
)


def test_symbol_pools_are_populated():
    # 国际期货 / 国内主力 / 上金所现货各覆盖主流品种
    assert len(FOREIGN_SYMBOLS) >= 30
    # 能源（原油/天然气）
    assert "CL" in FOREIGN_SYMBOLS  # NYMEX 原油
    assert "OIL" in FOREIGN_SYMBOLS  # 布伦特原油
    assert "NG" in FOREIGN_SYMBOLS  # NYMEX 天然气
    # 贵金属（COMEX + 伦敦现货）
    assert "GC" in FOREIGN_SYMBOLS  # COMEX 黄金
    assert "SI" in FOREIGN_SYMBOLS  # COMEX 白银
    assert "XAU" in FOREIGN_SYMBOLS  # 伦敦金现货
    assert "XAG" in FOREIGN_SYMBOLS  # 伦敦银现货
    # 基本金属（COMEX + LME 迷你）
    assert "HG" in FOREIGN_SYMBOLS  # COMEX 铜
    assert "CAD" in FOREIGN_SYMBOLS  # LME 铜迷你
    assert "AHD" in FOREIGN_SYMBOLS  # LME 铝迷你
    # 农产品/软商品
    assert "S" in FOREIGN_SYMBOLS  # 美大豆
    assert "W" in FOREIGN_SYMBOLS
    assert "CT" in FOREIGN_SYMBOLS  # ICE 棉花
    assert "FCPO" in FOREIGN_SYMBOLS  # 马来棕榈油
    # 金融/其他
    assert "FEF" in FOREIGN_SYMBOLS  # 欧元外汇期货
    assert "EUA" in FOREIGN_SYMBOLS  # 欧盟碳配额
    assert "BTC" in FOREIGN_SYMBOLS  # 比特币期货
    # 国内主力（跨交易所全覆盖）
    assert len(CN_MAIN) >= 70
    assert "RB0" in CN_MAIN  # 螺纹钢主力连续
    assert "AU0" in CN_MAIN  # 沪金主力连续
    assert "SC0" in CN_MAIN  # 上海原油
    assert "PG0" in CN_MAIN  # 液化石油气
    assert "LC0" in CN_MAIN  # 碳酸锂
    assert "SI0" in CN_MAIN  # 工业硅
    assert "T0" in CN_MAIN  # 10年期国债
    assert "IF0" in CN_MAIN  # 沪深300指数
    # 上金所现货（含新增迷你/白银/铂金）
    for s in ("Au99.99", "Au(T+D)", "mAu(T+D)", "Ag99.99", "Pt99.95"):
        assert s in SGE_SYMBOLS


def test_contract_stems_cover_cn_main():
    # 分合约日K的品种 stem 必须覆盖国内主力清单（去掉尾部 0 即合约前缀）
    assert set(_CN_MAIN_STEMS) == {k[:-1] for k in CN_MAIN}
    assert "RB" in _CN_MAIN_STEMS
    assert "LC" in _CN_MAIN_STEMS
    assert "IF" in _CN_MAIN_STEMS


def test_catalog_dataset_mapping_covers_sync_fields():
    # 后台 catalog 的 daily_forward / futures_realtime 两个数据集，
    # 必须映射到 akshare_futures_sync 的全部 5 个 field，否则面板「同步」会静默空转
    fields = [f for fs in DATASET_FIELDS.values() for f in fs]
    assert set(fields) == {
        "foreign_realtime",
        "foreign_daily",
        "cn_realtime",
        "cn_daily",
        "sge_daily",
    }
    assert DATASET_FIELDS["daily_forward"] == ["foreign_daily", "cn_daily", "sge_daily"]
    assert DATASET_FIELDS["futures_realtime"] == ["foreign_realtime", "cn_realtime"]


def test_extra_dataset_tasks_exist():
    # 扩展数据集的分发目标必须是注册模块中真实存在的 task 函数（注册表为
    # (模块简称, task 名) 二元组，模块经 _EXTRA_MODULES 解析）
    import importlib

    from backend.scripts.quantfutures_daily_sync import _EXTRA_MODULES

    for ds, (mod_key, task) in EXTRA_DATASET_TASKS.items():
        mod = importlib.import_module(_EXTRA_MODULES[mod_key])
        assert callable(getattr(mod, f"task_{task}", None)), (
            f"{ds} -> {mod_key}.task_{task} 缺失"
        )
    # 日频全量同步默认附带日频扩展（contracts_daily 请求量大，不默认跑）
    assert "contracts_daily" not in DEFAULT_EXTRA_DATASETS
    # 回填量中等的三项仅在面板勾选时跑
    for heavy in ("contract_meta", "exchange_daily", "member_rank_sina"):
        assert heavy not in DEFAULT_EXTRA_DATASETS
    assert set(DEFAULT_EXTRA_DATASETS) <= set(EXTRA_DATASET_TASKS)


def test_run_unknown_dataset_returns_empty_result():
    # 未知数据集不应抛错，返回空 result
    result = run(datasets=["not_a_dataset"])
    assert result["market"] == "futures"
    assert result["result"] == {}


def test_normalise_daily_renames_and_coerces():
    raw = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-08-01"),
                "open": 100.0,
                "high": 110.0,
                "low": 95.0,
                "close": 105.5,
                "volume": 1000.0,
            },
        ]
    )
    df = _normalise_daily(raw, symbol="CL.FUT")
    assert list(df.columns) == KLINE_COLS
    row = df.iloc[0]
    assert row["symbol"] == "CL.FUT"
    assert row["close"] == 105.5
    # amount = close × volume 估算
    assert row["amount"] == pytest.approx(105.5 * 1000.0)
    assert row["release_id"] == "akshare"
    # 期货 _normalise_daily 保留完整时间（00:00:00），与 QuantDB 分区键对齐
    assert row["time"] == pd.Timestamp("2026-08-01")


def test_normalise_daily_volume_falls_back_to_position():
    # akshare 部分接口无 volume 列，用 position（持仓量）兜底
    raw = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-08-01"),
                "open": 100.0,
                "high": 110.0,
                "low": 95.0,
                "close": 105.5,
                "position": 500,
            },
        ]
    )
    df = _normalise_daily(raw, symbol="Au99.99")
    row = df.iloc[0]
    assert row["volume"] == 500
    assert row["amount"] == pytest.approx(105.5 * 500.0)


def test_normalise_daily_drops_missing_close():
    raw = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-08-01"),
                "open": 100.0,
                "high": 110.0,
                "low": 95.0,
                "close": None,
                "volume": 1000.0,
            },
        ]
    )
    df = _normalise_daily(raw, symbol="CL.FUT")
    assert df.empty


def test_normalise_daily_drops_future_dated_placeholder_row():
    # 新浪国际期货历史接口对个别品种（实测 CT，2026-10-15 实锤）夹带一条
    # 未来日期的占位行（OHLC 全相同、volume=0），不拦会在 _write_kline_partition
    # 凭空造出 dt=<未来日> 分区，且每次同步重生（幽灵分区根因）。
    today = pd.Timestamp.now().normalize()
    raw = pd.DataFrame(
        [
            {
                "date": today - pd.Timedelta(days=2),
                "open": 80.0,
                "high": 81.0,
                "low": 79.0,
                "close": 80.5,
                "volume": 100,
            },
            {
                "date": today,
                "open": 80.5,
                "high": 81.0,
                "low": 79.5,
                "close": 80.0,
                "volume": 50,
            },
            {
                "date": today + pd.Timedelta(days=15),
                "open": 78.75,
                "high": 78.75,
                "low": 78.75,
                "close": 78.75,
                "volume": 0,
            },
        ]
    )
    df = _normalise_daily(raw, symbol="CT.FUT")
    assert len(df) == 2  # 未来行被丢，历史与今日行保留
    assert df["time"].max() <= today


def test_write_kline_partition_incremental_dedup(tmp_path):
    root = tmp_path / "1_kline_data" / "daily_forward"
    chunk1 = pd.DataFrame(
        {
            "symbol": ["CL.FUT", "AU0.CN"],
            "time": pd.to_datetime(["2026-08-01", "2026-08-01"]),
            "close": [70.0, 550.0],
            "release_id": ["akshare", "akshare"],
        }
    )
    written = _write_kline_partition(root, chunk1)
    assert written == 1
    target = root / "dt=20260801" / "data.parquet"
    assert target.is_file()

    # 增量写入同一分区：同一 (symbol, time) 应被覆盖而非新增
    chunk2 = pd.DataFrame(
        {
            "symbol": ["CL.FUT"],
            "time": pd.to_datetime(["2026-08-01"]),
            "close": [72.0],
            "release_id": ["akshare"],
        }
    )
    _write_kline_partition(root, chunk2)
    merged = pd.read_parquet(target)
    assert len(merged) == 2  # CL.FUT 被覆盖，不新增
    cl = merged[merged["symbol"] == "CL.FUT"].iloc[0]
    assert cl["close"] == 72.0


def test_market_console_futures_datasets():
    from backend.services.api.routers.admin.global_market_console import (
        _default_datasets,
    )

    specs = _default_datasets("FUTURES")
    by_name = {s.dataset: s for s in specs}
    names = set(by_name)
    # 期货数据段：日K + 实时快照 + 扩展数据集（仓单/持仓/分合约/CFTC/汇率）
    assert names == set(DATASET_FIELDS) | set(EXTRA_DATASET_TASKS)
    assert by_name["daily_forward"].layout == "partition"
    assert by_name["futures_realtime"].layout == "symbol"
    assert by_name["fx_daily"].layout == "symbol"  # 14 币种文件，前端按币种预览
    # catalog 每个数据集都必须有同步分发路径，防止面板「同步」静默空转
    for n in names:
        assert n in DATASET_FIELDS or n in EXTRA_DATASET_TASKS
