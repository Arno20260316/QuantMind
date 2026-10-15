#!/usr/bin/env python3
"""daily_forward 分区回填 position（持仓量）/ settlement（动态结算价）两列。

背景（2026-10-15）：`akshare_futures_sync._normalise_daily` 在落盘时以
KLINE_COLS 裁剪列，历史分区（7771 个）缺持仓量与动态结算价——市场分析
「持仓分析 / 沉淀资金」无数据可用。修复分两步：
  1. 同步脚本已改（新分区起带两列，源不提供时落 NULL）；
  2. 本脚本回填历史分区：拉取国内 82 主力连续全历史（futures_main_sina，
     含持仓量/动态结算价），与现有分区按 (symbol, time) join，**只补两列，
     绝不改动任何既有价格/成交量列**（保护 l1_factors 与 qlib 缓存一致性）。

纪律：
  - 幂等可重跑：已有非空 position 的行不覆盖（fillna 语义）；
  - 国际（.FUT）/上金所（SGE）品种源不含量仓 → 两列落 NULL，不落 0；
  - 写回前逐列断言 8 个既有列与读入完全一致，不一致即中止该分区。

用法：
  python backend/scripts/quantfutures_backfill_position.py --dry-run
  python backend/scripts/quantfutures_backfill_position.py --days 10      # 只补最近 10 个分区
  python backend/scripts/quantfutures_backfill_position.py                # 全量
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))

from akshare_futures_sync import CN_MAIN, _data_dir, _normalise_daily  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("quantfutures_backfill_position")

# 既有列（回填不变量）：任何一列被改动即中止
_GUARD_COLS = ["symbol", "time", "open", "high", "low", "close", "volume", "amount"]
_FILL_COLS = ["position", "settlement"]


def fetch_cn_position_frames(codes: list[str] | None = None) -> pd.DataFrame:
    """拉取国内主力连续全历史，返回 symbol/time/position/settlement 长表。"""
    import akshare as ak

    syms = codes or list(CN_MAIN.keys())
    frames = []
    for code in syms:
        try:
            df = ak.futures_main_sina(symbol=code)
            if df is None or df.empty:
                log.warning("主力 %s 返回空", code)
                continue
            norm = _normalise_daily(df, f"{code}.CN")
            frames.append(norm[["symbol", "time", "position", "settlement"]])
        except Exception as exc:  # noqa: BLE001
            log.warning("主力 %s 拉取失败: %s", code, exc)
    if not frames:
        raise RuntimeError("全部品种拉取失败，中止")
    meta = pd.concat(frames, ignore_index=True)
    # (symbol, time) 必须唯一，否则 merge 会广播行
    dup = meta.duplicated(subset=["symbol", "time"]).sum()
    if dup:
        log.warning("源数据存在 %d 行重复 (symbol,time)，按最后一条去重", dup)
        meta = meta.drop_duplicates(subset=["symbol", "time"], keep="last")
    meta["time"] = pd.to_datetime(meta["time"])
    return meta


def _backfill_one(part: Path, meta: pd.DataFrame, dry_run: bool) -> dict:
    """回填单个分区。返回统计 dict。"""
    df = pd.read_parquet(part)
    df["time"] = pd.to_datetime(df["time"])
    for c in _FILL_COLS:
        if c not in df.columns:
            df[c] = pd.Series([None] * len(df), dtype="float64")

    merged = df[_GUARD_COLS + _FILL_COLS].merge(
        meta, on=["symbol", "time"], how="left", suffixes=("", "_new")
    )
    # 只填空：既有非空值（新分区已带）不被历史数据覆盖
    filled = 0
    for c in _FILL_COLS:
        before_null = merged[c].isna()
        merged[c] = merged[c].fillna(merged[f"{c}_new"])
        filled += int((before_null & merged[c].notna()).sum())
    out = merged[_GUARD_COLS + _FILL_COLS]

    # 不变量断言：8 个既有列逐列一致（NaN 等价）
    for c in _GUARD_COLS:
        if not out[c].equals(df[c]):
            raise AssertionError(f"{part.name} 列 {c} 被意外改动，中止")

    stats = {
        "rows": int(len(out)),
        "pos_filled": int(out["position"].notna().sum()),
        "sett_filled": int(out["settlement"].notna().sum()),
        "changed": filled,
    }
    if not dry_run and filled:
        tmp = part.with_suffix(".parquet.tmp")
        out.to_parquet(tmp, index=False)
        os.replace(tmp, part)
    return stats


def run(dry_run: bool = False, days: int | None = None) -> dict:
    root = _data_dir() / "1_kline_data" / "daily_forward"
    parts = sorted(root.glob("dt=*/data.parquet"))
    if not parts:
        raise RuntimeError(f"未找到分区: {root}")
    if days:
        parts = parts[-days:]
    log.info("分区 %d 个（dry_run=%s），拉取国内主力全历史…", len(parts), dry_run)

    meta = fetch_cn_position_frames()
    log.info("品种元数据 %d 行 / %d 品种", len(meta), meta["symbol"].nunique())

    totals = {
        "partitions": 0,
        "rows": 0,
        "pos_filled": 0,
        "sett_filled": 0,
        "changed": 0,
    }
    for i, part in enumerate(parts, 1):
        stats = _backfill_one(part, meta, dry_run)
        totals["partitions"] += 1
        for k in ("rows", "pos_filled", "sett_filled", "changed"):
            totals[k] += stats[k]
        if i % 1000 == 0:
            log.info("进度 %d/%d，已补 %d 行", i, len(parts), totals["changed"])

    log.info(
        "完成：分区 %d，行 %d，position 非空 %d（%.1f%%），settlement 非空 %d（%.1f%%），本次补 %d 行",
        totals["partitions"],
        totals["rows"],
        totals["pos_filled"],
        100.0 * totals["pos_filled"] / max(totals["rows"], 1),
        totals["sett_filled"],
        100.0 * totals["sett_filled"] / max(totals["rows"], 1),
        totals["changed"],
    )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(
        description="回填 daily_forward 的 position/settlement 列"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    parser.add_argument("--days", type=int, default=None, help="只处理最近 N 个分区")
    args = parser.parse_args()
    try:
        totals = run(dry_run=args.dry_run, days=args.days)
        print(totals)
        return 0
    except Exception as exc:  # noqa: BLE001
        log.error("回填失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
