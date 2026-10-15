#!/usr/bin/env python3
"""akshare 期货数据 → QuantFutures 同步脚本。

用 akshare 抓取国内/国际期货、贵金属数据，按 QuantDB 的 Hive 分区格式
落盘到 data/quantfutures。

数据段:
  foreign_realtime  国际期货实时（原油/黄金/白银/铜等）→ 2_base_sector/futures_realtime
  foreign_daily     国际期货日K（主力连续）          → 1_kline_data/daily_forward
  cn_realtime       国内期货实时（828 只合约）       → 2_base_sector/futures_realtime
  cn_daily          国内主力连续日K                  → 1_kline_data/daily_forward
  sge_daily         上金所贵金属现货日K              → 1_kline_data/daily_forward

落盘格式:
  {quantfutures}/1_kline_data/daily_forward/dt=YYYYMMDD/data.parquet
  {quantfutures}/2_base_sector/futures_realtime/{symbol}.parquet

symbol 命名:
  国际实时: CL  → CL.FUT（NYMEX原油）
  国内实时: 合约代码原样（ag2608）
  日K: 品种代码（CL / GC / AU9999）

用法:
  # 全部数据段
  python backend/scripts/akshare_futures_sync.py --field all

  # 国际期货实时
  python backend/scripts/akshare_futures_sync.py --field foreign_realtime
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("akshare_futures_sync")

# 数据目录：环境变量 QM_QUANTFUTURES_DATA_DIR，默认 data/quantfutures
_FUTURES_DATA_DIR_ENV = "QM_QUANTFUTURES_DATA_DIR"
_FUTURES_DEFAULT_DIRS = [
    "/data/quantfutures",  # Docker 容器内
    str(PROJECT_ROOT / "data" / "quantfutures"),  # 项目根
]

KLINE_COLS = [
    "symbol", "time", "open", "high", "low", "close",
    "volume", "amount", "position", "settlement",
    "release_id", "published_at",
]

# 品种宇宙（中文名/板块/交易所/合约乘数）统一出处：
# backend/shared/futures_universe.py（2026-10-15 迁出）。
# 此处 re-export 三张清单，保持既有引用（如
# `from akshare_futures_sync import CN_MAIN`）兼容。
from backend.shared.futures_universe import (  # noqa: E402
    CN_MAIN,
    FOREIGN_SYMBOLS,
    SGE_SYMBOLS,
)


def _data_dir() -> Path:
    env_val = os.getenv(_FUTURES_DATA_DIR_ENV, "").strip()
    if env_val:
        p = Path(env_val)
        p.mkdir(parents=True, exist_ok=True)
        return p
    for d in _FUTURES_DEFAULT_DIRS:
        p = Path(d)
        if p.is_dir():
            return p
    p = Path(_FUTURES_DEFAULT_DIRS[-1])
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_kline_partition(root: Path, all_df: pd.DataFrame) -> int:
    """按交易日分区写入日K，增量合并去重。"""
    root.mkdir(parents=True, exist_ok=True)
    grouped = {ts.strftime("%Y%m%d"): g for ts, g in all_df.groupby(all_df["time"].dt.date)}
    written = 0
    for date_str, chunk in sorted(grouped.items()):
        dt_dir = root / f"dt={date_str}"
        dt_dir.mkdir(parents=True, exist_ok=True)
        out = dt_dir / "data.parquet"
        if out.exists():
            old = pd.read_parquet(out)
            combined = pd.concat([old, chunk], ignore_index=True)
            combined = combined.drop_duplicates(subset=["symbol", "time"], keep="last")
            combined.to_parquet(out, index=False)
        else:
            chunk.to_parquet(out, index=False)
        written += 1
    return written


# akshare 不同接口返回中英文两套列名，统一映射到标准列
_COL_MAP = {
    "日期": "time", "date": "time",
    "开盘价": "open", "open": "open",
    "最高价": "high", "high": "high",
    "最低价": "low", "low": "low",
    "收盘价": "close", "close": "close",
    "成交量": "volume", "volume": "volume",
    "持仓量": "position", "position": "position",
    "动态结算价": "settlement", "settlement": "settlement",
}


def _normalise_daily(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """日K标准化 → QuantDB kline schema（兼容 akshare 中英文列名）。"""
    df = df.rename(columns=_COL_MAP)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])
    # 丢弃未来日期行：新浪国际期货历史接口对个别品种会夹带一条未来日期的
    # 占位行（实测 CT@2026-10-30：OHLC 全相同、volume=0），不拦会在
    # _write_kline_partition 凭空造出 dt=<未来日> 幽灵分区且每次同步重生。
    today = pd.Timestamp.now().normalize()
    future = df["time"] > today
    if future.any():
        log.warning(
            "丢弃未来日期行 %s: %s",
            symbol,
            sorted(str(d) for d in df.loc[future, "time"].dt.date.unique()),
        )
        df = df.loc[~future]
    df["symbol"] = symbol
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            df[c] = None
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # volume 别名处理
    if "volume" not in df.columns:
        if "position" in df.columns:
            df["volume"] = pd.to_numeric(df["position"], errors="coerce")
        else:
            df["volume"] = 0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["amount"] = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    # 持仓量 / 动态结算价（机构分析口径：持仓增减、沉淀资金）：
    # 源不提供（全空或恒 0——实测新浪外盘 futures_foreign_hist 与上金所 spot_hist_sge
    # 即如此）→ 落 NULL 保持诚实空缺，绝不落 0（0 会被下游误读为「持仓归零」）。
    for c in ("position", "settlement"):
        if c not in df.columns:
            df[c] = None
        col = pd.to_numeric(df[c], errors="coerce")
        if col.notna().sum() == 0 or (col.fillna(0.0) == 0).all():
            col = pd.Series([None] * len(df), index=df.index, dtype="float64")
        df[c] = col
    df["release_id"] = "akshare"
    df["published_at"] = datetime.now().isoformat(timespec="seconds")
    return df[KLINE_COLS].dropna(subset=["close"])


def sync_foreign_realtime(symbols: list[str] | None = None) -> dict:
    """国际期货实时行情。"""
    import akshare as ak

    syms = symbols or list(FOREIGN_SYMBOLS.keys())
    root = _data_dir() / "2_base_sector" / "futures_realtime"
    root.mkdir(parents=True, exist_ok=True)

    ok = 0
    err = 0
    for code in syms:
        try:
            df = ak.futures_foreign_commodity_realtime(symbol=code)
            if df is None or df.empty:
                err += 1
                continue
            df = df.copy()
            df["symbol"] = f"{code}.FUT"
            df["release_id"] = "akshare"
            df["published_at"] = datetime.now().isoformat(timespec="seconds")
            df.to_parquet(root / f"{code}.FUT.parquet", index=False)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("国际实时 %s 失败: %s", code, exc)
            err += 1

    return {"field": "foreign_realtime", "symbols": len(syms), "ok": ok, "err": err, "dir": str(root)}


def sync_foreign_daily(symbols: list[str] | None = None) -> dict:
    """国际期货日K（主力连续）。"""
    import akshare as ak

    syms = symbols or list(FOREIGN_SYMBOLS.keys())
    root = _data_dir() / "1_kline_data" / "daily_forward"

    frames = []
    for code in syms:
        try:
            df = ak.futures_foreign_hist(symbol=code)
            if df is None or df.empty:
                continue
            frames.append(_normalise_daily(df, f"{code}.FUT"))
            log.info("国际日K %s -> %s.FUT: %d 行", code, code, len(df))
        except Exception as exc:  # noqa: BLE001
            log.warning("国际日K %s 失败: %s", code, exc)

    if not frames:
        return {"field": "foreign_daily", "rows": 0}
    all_df = pd.concat(frames, ignore_index=True)
    written = _write_kline_partition(root, all_df)
    return {"field": "foreign_daily", "rows": int(len(all_df)), "partitions": written}


def sync_cn_realtime(symbols: list[str] | None = None) -> dict:
    """国内期货实时行情（futures_comm_info）。"""
    import akshare as ak

    root = _data_dir() / "2_base_sector" / "futures_realtime"
    root.mkdir(parents=True, exist_ok=True)

    try:
        df = ak.futures_comm_info()
        if df is None or df.empty:
            return {"field": "cn_realtime", "rows": 0}
        df = df.copy()
        # 合约代码在「合约代码」列，作为 symbol
        df = df.rename(columns={"合约代码": "symbol"})
        df["release_id"] = "akshare"
        df["published_at"] = datetime.now().isoformat(timespec="seconds")
        df.to_parquet(root / "cn_all.parquet", index=False)
        return {"field": "cn_realtime", "rows": int(len(df)), "dir": str(root)}
    except Exception as exc:  # noqa: BLE001
        log.warning("国内实时行情失败: %s", exc)
        return {"field": "cn_realtime", "error": str(exc)}


def sync_cn_daily(symbols: list[str] | None = None) -> dict:
    """国内主力连续日K。"""
    import akshare as ak

    syms = symbols or list(CN_MAIN.keys())
    root = _data_dir() / "1_kline_data" / "daily_forward"

    frames = []
    for code in syms:
        try:
            df = ak.futures_main_sina(symbol=code)
            if df is None or df.empty:
                continue
            frames.append(_normalise_daily(df, f"{code}.CN"))
            log.info("国内主力 %s -> %s.CN: %d 行", code, code, len(df))
        except Exception as exc:  # noqa: BLE001
            log.warning("国内主力 %s 失败: %s", code, exc)

    if not frames:
        return {"field": "cn_daily", "rows": 0}
    all_df = pd.concat(frames, ignore_index=True)
    written = _write_kline_partition(root, all_df)
    return {"field": "cn_daily", "rows": int(len(all_df)), "partitions": written}


def sync_sge_daily(symbols: list[str] | None = None) -> dict:
    """上金所贵金属现货日K。"""
    import akshare as ak

    syms = symbols or SGE_SYMBOLS
    root = _data_dir() / "1_kline_data" / "daily_forward"

    frames = []
    for sym in syms:
        try:
            df = ak.spot_hist_sge(symbol=sym)
            if df is None or df.empty:
                continue
            frames.append(_normalise_daily(df, sym))
            log.info("上金所 %s: %d 行", sym, len(df))
        except Exception as exc:  # noqa: BLE001
            log.warning("上金所 %s 失败: %s", sym, exc)

    if not frames:
        return {"field": "sge_daily", "rows": 0}
    all_df = pd.concat(frames, ignore_index=True)
    written = _write_kline_partition(root, all_df)
    return {"field": "sge_daily", "rows": int(len(all_df)), "partitions": written}


FIELDS = {
    "foreign_realtime": sync_foreign_realtime,
    "foreign_daily": sync_foreign_daily,
    "cn_realtime": sync_cn_realtime,
    "cn_daily": sync_cn_daily,
    "sge_daily": sync_sge_daily,
}


def run(*, field: str = "all", symbols: str | None = None, **kwargs) -> dict:
    """供后台管理 API 调用的编程接口。"""
    return sync(field, symbols=symbols)


def sync(field: str = "all", *, symbols: str | None = None) -> dict:
    """同步指定期货数据段。"""
    if field not in FIELDS and field != "all":
        raise ValueError(f"field 必须是 {'/'.join(FIELDS)}/all，收到 {field}")

    syms = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    if field == "all":
        result = {}
        for name in FIELDS:
            result[name] = FIELDS[name](syms)
        return result
    return {field: FIELDS[field](syms)}


def main() -> int:
    parser = argparse.ArgumentParser(description="akshare 期货数据 → QuantFutures")
    parser.add_argument("--field", default="all", choices=[*FIELDS.keys(), "all"], help="数据段")
    parser.add_argument("--symbols", default=None, help="指定品种代码，逗号分隔")
    args = parser.parse_args()

    try:
        result = sync(args.field, symbols=args.symbols)
        print(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        log.error("同步失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
