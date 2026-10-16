#!/usr/bin/env python3
"""聪明钱数据采集（A股）→ QuantDB 扁平 parquet。

数据面（akshare 1.18.88 实测，2026-10-15 / 2026-10-16）：
  - 龙虎榜明细     stock_lhb_detail_em(start,end)   全市场×日×上榜原因
  - 机构席位统计   stock_lhb_jgmmtj_em(start,end)   全市场×日（机构专用席位）
  - 活跃营业部     stock_lhb_hyyyb_em(start,end)    营业部×日
  - 大宗交易统计   stock_dzjy_mrtj(start,end)       全市场×日
  - 机构调研统计   stock_jgdy_tj_em(date)           全市场×公告日（**近 365 天
    滚动全量**，见下「调研留存窗」）
  - 龙虎榜席位明细 stock_lhb_stock_detail_em(symbol,date,flag)  单股×日×方向（买/卖
    各一次调用，各返回 5 档席位）——抓取单元是 (trade_date, symbol) 股日对，
    见下「席位回补」
  - 大宗交易明细   stock_dzjy_mrmx(symbol='A股',start,end)      逐笔（含买卖双方
    营业部），支持区间

落盘（扁平单文件，数据量小免分区读放大）：
  {QM_QUANTDB_DATA_DIR}/2_base_sector/smart_money/
    lhb_detail.parquet / lhb_inst.parquet / lhb_branch.parquet
    lhb_seat.parquet / lhb_seat_pairs.parquet(完成态) / dzjy_detail.parquet
    block_trade.parquet / survey.parquet / _sync_meta.json(水位)

口径契约（改动即破坏下游，见 docs/市场分析_聪明钱追踪与选股_细案_20261015.md §2）：
  - symbol 后缀式（600036.SH，QuantDB 分层口径）
  - trade_date 'YYYY-MM-DD' 字符串；金额一律**元**、成交量**股**（写入时换算）
  - 折溢率/占比类为**分数**（0.0123 = 1.23%），换手率/涨跌幅为百分数
  - **同名不同尺度的坑（实测钉死）**：龙虎榜席位 `买入金额-占总成交比例`
    实测已是**分数**（0.0651=6.51%）；大宗明细 `成交额/流通市值` 实测为
    **百分数**（0.005926=0.005926%）。两者都在 normalize 里定标，改动前重验
  - **前向收益列封存**：上榜后1/2/5/10日 → fwd_ret_1d/2d/5d/10d，
    仅可展示，绝不可入信号（signal 层显式排除 fwd_* 前缀，有单测钉死）
  - 单位自检：用恒等式（净买额/成交额≈占比、价×量≈额）在真实数据上定标并落日志

席位回补（2026-10-16 新增，mode="seat"）：
  席位明细无区间接口，只能逐 (trade_date, symbol) 股日对调用（2 次/对）。
  全史回补 ≈ 4.8 万对 ≈ 9.7 万次调用 @约 6.7 req/s ≈ 4 小时，**从新到旧**
  处理保证水位（last_date=最新完整日）始终新鲜；完成态以 lhb_seat_pairs.parquet
  为准（skip-existing 天然断点续跑），单实例 flock 防夜班任务与后台回补并发
  写坏文件。日常增量只回拉水位后 1 天重叠窗（≈150 对，不足 1 分钟）。
  全量回补须显式 `--dataset lhb_seat --extend-history --start 2024-01-01`；
  首跑无水位时的日常兜底窗为 30 天（防新装环境夜班任务意外跑 4 小时）。
  转债（11/12 开头）上龙虎榜但席位接口不支持（全史 925 对）→ 完成态记
  status="skipped"（计入水位完整日，不重试），display 侧按 pairs 文件披露覆盖率。

水位语义：meta.last_date 之前的片段视为完整；每次运行从
max(--start 回补下限, last_date−重叠3天) 拉起，逐月切片幂等重写；
某切片重试仍失败 → 停止推进（下次自愈），已采部分照常落盘。

调研留存窗（2026-10-16 实测钉死）：stock_jgdy_tj_em 的 date 参数只是
「公告日 > date」的下界，且上游**只保留最近 365 天**——任何 date 都拿不到
更早记录（窗口完全越界返回空）。故 survey 走 mode="cumulative"：单次调用
取留存全量、merge 去重；本地 parquet 自首次采集起自行累积，历史越攒越长。
2025-10-16 之前的调研上游不供，验证/展示口径须按实际可得窗口披露。

用法：
  python backend/scripts/smart_money_sync.py --start 2024-01-01     # 回补
  python backend/scripts/smart_money_sync.py                        # 日常增量
  python backend/scripts/smart_money_sync.py --dataset survey       # 单表
  # 席位全史回补（长跑 ≈4h，可反复调用续跑；建议 nohup 后台）
  python backend/scripts/smart_money_sync.py --dataset lhb_seat --extend-history \
      --start 2024-01-01
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.shared.stock_utils import StockCodeUtil  # noqa: E402
from backend.shared.utc_datetime import utc_now  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("smart_money_sync")

SMART_MONEY_DIR = (
    Path(os.getenv("QM_QUANTDB_DATA_DIR", str(PROJECT_ROOT / "data" / "quantdb")))
    / "2_base_sector"
    / "smart_money"
)
META_PATH = SMART_MONEY_DIR / "_sync_meta.json"

OVERLAP_DAYS = 3  # 回拉重叠窗口（覆盖数据修订/当晚未出齐）
MAX_RETRY = 3
RETRY_WAITS = (5, 15, 30)

# 席位回补（mode="seat"，见模块 docstring「席位回补」）
SEAT_PACE_SECONDS = float(os.getenv("SMART_MONEY_SEAT_PACE", "0.15"))  # ≈6.7 req/s
SEAT_OVERLAP_DAYS = 1  # 席位榜发布即终稿，重叠只为兜住半途失败日
SEAT_BOOTSTRAP_DAYS = 30  # 无水位首跑的兜底窗（全史回补须显式 --extend-history）
SEAT_CHECKPOINT_EVERY = 200  # 每 N 对股日落盘一次（长回补可断点续跑）
SEAT_PAIRS_FILE = "lhb_seat_pairs.parquet"  # 完成态索引（含 empty 异常股日）
SEAT_BAN_GUARD_MIN = 20  # 前 N 次全失败 → 疑被限流，中止本次运行

# 单位自检的合理量级（median 应落于区间内）
_UNIT_GUARDS: dict[str, tuple[float, float]] = {
    "float_mktcap": (1e8, 1e13),  # 流通市值 元
    "lhb_net_buy": (-1e10, 1e10),  # 龙虎榜净买额 元
    "amount": (1e3, 1e11),  # 大宗成交总额 元
}


# ---------------------------------------------------------------- helpers


def _to_suffix(code: object) -> str | None:
    s = str(code).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    try:
        return StockCodeUtil.to_suffix(s)
    except Exception:  # noqa: BLE001 —— 非 A 股代码（如北交所新段/异常值）静默丢弃
        return None


def _norm_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed").dt.strftime("%Y-%m-%d")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _strip_str(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().replace({"": None})


def _unit_guard(table: str, col: str, series: pd.Series) -> None:
    lo, hi = _UNIT_GUARDS.get(col, (float("-inf"), float("inf")))
    med = series.dropna().abs().median()
    if pd.isna(med):
        return
    if not (lo <= med <= hi):
        log.warning(
            "[%s] 单位自检异常: %s median=%.4g 不在 [%.1g, %.1g]（单位口径可能变了）",
            table,
            col,
            med,
            lo,
            hi,
        )


def _canonical_mktcap(series: pd.Series, table: str) -> pd.Series:
    """流通市值定标到元：接口/年代口径不同（jgmmtj 实测为亿元，detail 为元）。

    判据用中位量级：<1e6 → 亿元（×1e8）；1e8~1e13 → 已是元。两域无重叠
    （1e6 亿 = 1e14 元 > 任何 A 股流通市值），区分确定。
    """
    med = series.dropna().abs().median()
    if pd.isna(med):
        return series
    if med < 1e6:
        log.info("[%s] float_mktcap 定标：亿元 → 元（median=%.4g）", table, med)
        return series * 1e8
    if 1e8 <= med <= 1e13:
        return series
    log.warning("[%s] float_mktcap 量级异常 median=%.4g，原样保留", table, med)
    return series


def _canonical_ratio(
    net_ratio: pd.Series,
    net: pd.Series,
    denominators: list[pd.Series],
    table: str,
    col: str,
) -> pd.Series:
    """占比列定标：用恒等式探测「分数 or 百分数」，百分数则 /100 归一到分数。

    探测式 median(ratio / (net/denom))：≈1 → 分数；≈100 → 百分数。
    两种分母都探测不到 → 原样保留 + 告警（宁可显式可疑，不可静默改数）。
    """
    for denom in denominators:
        ok = net_ratio.notna() & net.notna() & (denom > 0)
        if ok.sum() < 10:
            continue
        scale = (net_ratio[ok] / (net[ok] / denom[ok])).median()
        if pd.isna(scale):
            continue
        if 0.5 <= scale <= 2:
            log.info("[%s] %s 定标：分数（scale=%.3f）", table, col, scale)
            return net_ratio
        if 50 <= scale <= 200:
            log.info(
                "[%s] %s 定标：百分数 → 归一到分数（scale=%.1f）", table, col, scale
            )
            return net_ratio / 100.0
    log.warning("[%s] %s 无法定标（恒等式不成立），原样保留待人工核对", table, col)
    return net_ratio


# ---------------------------------------------------------------- normalizers
# 纯函数：原始中文列 DataFrame（akshare 原样）→ 契约列 DataFrame。
# 单测直接喂构造样本，不触网。


def normalize_lhb_detail(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "symbol": df["代码"].map(_to_suffix),
            "name": _strip_str(df["名称"]),
            "trade_date": _norm_date(df["上榜日"]),
            "reason": _strip_str(df["上榜原因"]),
            "interpretation": _strip_str(df["解读"]),
            "close": _num(df["收盘价"]),
            "pct_chg": _num(df["涨跌幅"]),
            "lhb_net_buy": _num(df["龙虎榜净买额"]),
            "lhb_buy": _num(df["龙虎榜买入额"]),
            "lhb_sell": _num(df["龙虎榜卖出额"]),
            "lhb_amount": _num(df["龙虎榜成交额"]),
            "market_amount": _num(df["市场总成交额"]),
            "net_ratio": _num(df["净买额占总成交比"]),
            "amount_ratio": _num(df["成交额占总成交比"]),
            "turnover": _num(df["换手率"]),
            "float_mktcap": _num(df["流通市值"]),  # 实测为元尺度
            # 前向收益：封存列（fwd_ 前缀 = 禁入信号，仅展示）
            "fwd_ret_1d": _num(df["上榜后1日"]),
            "fwd_ret_2d": _num(df["上榜后2日"]),
            "fwd_ret_5d": _num(df["上榜后5日"]),
            "fwd_ret_10d": _num(df["上榜后10日"]),
        }
    )
    out["net_ratio"] = _canonical_ratio(
        out["net_ratio"],
        out["lhb_net_buy"],
        [out["lhb_amount"], out["market_amount"]],
        "lhb_detail",
        "net_ratio",
    )
    out["amount_ratio"] = _canonical_ratio(
        out["amount_ratio"],
        out["lhb_amount"],
        [out["market_amount"]],
        "lhb_detail",
        "amount_ratio",
    )
    out["float_mktcap"] = _canonical_mktcap(out["float_mktcap"], "lhb_detail")
    _unit_guard("lhb_detail", "float_mktcap", out["float_mktcap"])
    _unit_guard("lhb_detail", "lhb_net_buy", out["lhb_net_buy"])
    return out.dropna(subset=["symbol", "trade_date"])


def normalize_lhb_inst(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "symbol": df["代码"].map(_to_suffix),
            "name": _strip_str(df["名称"]),
            "trade_date": _norm_date(df["上榜日期"]),
            "reason": _strip_str(df["上榜原因"]),
            "close": _num(df["收盘价"]),
            "pct_chg": _num(df["涨跌幅"]),
            "inst_buy_seats": _num(df["买方机构数"]),
            "inst_sell_seats": _num(df["卖方机构数"]),
            "inst_buy": _num(df["机构买入总额"]),
            "inst_sell": _num(df["机构卖出总额"]),
            "inst_net_buy": _num(df["机构买入净额"]),
            "market_amount": _num(df["市场总成交额"]),
            "inst_net_ratio": _num(df["机构净买额占总成交额比"]),
            "turnover": _num(df["换手率"]),
            "float_mktcap": _num(df["流通市值"]),
        }
    )
    out["inst_net_ratio"] = _canonical_ratio(
        out["inst_net_ratio"],
        out["inst_net_buy"],
        [out["market_amount"]],
        "lhb_inst",
        "inst_net_ratio",
    )
    # jgmmtj 的流通市值实测为亿元（与 detail 表同名不同单位，2026-10-15 实测）
    out["float_mktcap"] = _canonical_mktcap(out["float_mktcap"], "lhb_inst")
    _unit_guard("lhb_inst", "float_mktcap", out["float_mktcap"])
    return out.dropna(subset=["symbol", "trade_date"])


def normalize_lhb_branch(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "trade_date": _norm_date(df["上榜日"]),
            "branch_name": _strip_str(df["营业部名称"]),
            "branch_code": _strip_str(df["营业部代码"]),
            "n_stocks_bought": _num(df["买入个股数"]),
            "n_stocks_sold": _num(df["卖出个股数"]),
            "buy_amount": _num(df["买入总金额"]),
            "sell_amount": _num(df["卖出总金额"]),
            "net_amount": _num(df["总买卖净额"]),
            "stocks_bought": _strip_str(df["买入股票"]),
        }
    )
    return out.dropna(subset=["trade_date", "branch_name"])


def normalize_lhb_seat(
    raw: pd.DataFrame, trade_date: str, symbol: str, side: str
) -> pd.DataFrame:
    """席位明细归一。side: buy|sell（买卖两个方向各一次调用、各 5 档）。

    占比列实测**已是分数**（0.0651=6.51%，2026-10-16 探针钉死），不除 100；
    金额为元。对侧金额常为 NaN（买入榜行无卖出额）——原样保留 NaN。
    """
    out = pd.DataFrame(
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "side": side,
            "seat_name": _strip_str(raw["交易营业部名称"]),
            "buy_amount": _num(raw["买入金额"]),
            "sell_amount": _num(raw["卖出金额"]),
            "net_amount": _num(raw["净额"]),
            "buy_ratio": _num(raw["买入金额-占总成交比例"]),
            "sell_ratio": _num(raw["卖出金额-占总成交比例"]),
        }
    )
    return out.dropna(subset=["seat_name"])


def normalize_dzjy_detail(df: pd.DataFrame) -> pd.DataFrame:
    """大宗交易逐笔明细归一（含买卖双方营业部）。"""
    out = pd.DataFrame(
        {
            "trade_date": _norm_date(df["交易日期"]),
            "symbol": df["证券代码"].map(_to_suffix),
            "name": _strip_str(df["证券简称"]),
            "pct_chg": _num(df["涨跌幅"]),
            "close": _num(df["收盘价"]),
            "deal_price": _num(df["成交价"]),
            "premium": _num(df["折溢率"]),  # 分数（与 mrtj 同口径）
            "volume": _num(df["成交量"]),  # 股（价×量恒等式定标）
            "amount": _num(df["成交额"]),  # 元
            # 实测为**百分数**（平安银行 1.35e7 / 0.005926% ≈ 2276 亿 ✓，
            # 2026-10-16 探针）→ /100 归一到分数
            "amount_ratio": _num(df["成交额/流通市值"]) / 100.0,
            "seat_buyer": _strip_str(df["买方营业部"]),
            "seat_seller": _strip_str(df["卖方营业部"]),
        }
    )
    # 恒等式定标：amount / ratio ≈ 流通市值（仅说明用；量级越界即告警）
    okr = (
        out["amount"].notna()
        & (out["amount"] > 0)
        & out["amount_ratio"].notna()
        & (out["amount_ratio"] > 0)
    )
    if okr.sum() >= 10:
        implied = (out["amount"][okr] / out["amount_ratio"][okr]).median()
        if 1e8 <= implied <= 1e13:
            log.info(
                "[dzjy_detail] 占比定标通过（隐含流通市值 median=%.3g 元）", implied
            )
        else:
            log.warning(
                "[dzjy_detail] 占比定标异常：隐含流通市值 median=%.3g 元（口径可能变了）",
                implied,
            )
    # 价×量 恒等式（与 block_trade 同款）
    okv = out["deal_price"].notna() & out["volume"].notna() & (out["amount"] > 0)
    if okv.sum() >= 10:
        resid = (
            (out["deal_price"] * out["volume"] - out["amount"]).abs() / out["amount"]
        )[okv]
        med = resid.median()
        if med > 0.05:
            log.warning(
                "[dzjy_detail] 价×量 恒等式偏差 median=%.3f（股/元换算可能变了）", med
            )
        else:
            log.info("[dzjy_detail] 价×量 恒等式定标通过（median 偏差=%.3f）", med)
    _unit_guard("dzjy_detail", "amount", out["amount"])
    return out.dropna(subset=["symbol", "trade_date"])


def normalize_block_trade(df: pd.DataFrame) -> pd.DataFrame:
    volume_wan = _num(df["成交总量"])  # 万股
    amount_wan = _num(df["成交总额"])  # 万元
    out = pd.DataFrame(
        {
            "trade_date": _norm_date(df["交易日期"]),
            "symbol": df["证券代码"].map(_to_suffix),
            "name": _strip_str(df["证券简称"]),
            "pct_chg": _num(df["涨跌幅"]),
            "close": _num(df["收盘价"]),
            "deal_price": _num(df["成交价"]),
            "premium": _num(df["折溢率"]),  # 分数（-0.2165 = -21.65%）
            "n_trades": _num(df["成交笔数"]),
            "volume": volume_wan * 1e4,  # 股
            "amount": amount_wan * 1e4,  # 元
            "amount_ratio": _num(df["成交总额/流通市值"])
            / 100.0,  # 发布口径为百分数 → 分数
        }
    )
    # 恒等式定标：价 × 量(股) ≈ 额(元)
    ok = out["deal_price"].notna() & out["volume"].notna() & (out["amount"] > 0)
    if ok.sum() >= 10:
        resid = (
            (out["deal_price"] * out["volume"] - out["amount"]).abs() / out["amount"]
        )[ok]
        med = resid.median()
        if med > 0.05:
            log.warning(
                "[block_trade] 价×量 恒等式偏差 median=%.3f（万股/万元换算可能变了）",
                med,
            )
        else:
            log.info("[block_trade] 价×量 恒等式定标通过（median 偏差=%.3f）", med)
    _unit_guard("block_trade", "amount", out["amount"])
    return out.dropna(subset=["symbol", "trade_date"])


def normalize_survey(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "symbol": df["代码"].map(_to_suffix),
            "name": _strip_str(df["名称"]),
            "announce_date": _norm_date(df["公告日期"]),
            "reception_date": _norm_date(df["接待日期"]),
            "close": _num(df["最新价"]),
            "pct_chg": _num(df["涨跌幅"]),
            "inst_count": _num(df["接待机构数量"]),
            "reception_type": _strip_str(df["接待方式"]),
            "reception_staff": _strip_str(df["接待人员"]),
            "reception_place": _strip_str(df["接待地点"]),
        }
    )
    # 事件日 = 信息可得日（公告日）；缺公告日回退接待日
    out["trade_date"] = out["announce_date"].fillna(out["reception_date"])
    return out.dropna(subset=["symbol", "trade_date"])


# ---------------------------------------------------------------- fetch


def _fetch_range(dataset: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    if dataset == "lhb_detail":
        return ak.stock_lhb_detail_em(start_date=start, end_date=end)
    if dataset == "lhb_inst":
        return ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end)
    if dataset == "lhb_branch":
        return ak.stock_lhb_hyyyb_em(start_date=start, end_date=end)
    if dataset == "block_trade":
        return ak.stock_dzjy_mrtj(start_date=start, end_date=end)
    if dataset == "dzjy_detail":
        return ak.stock_dzjy_mrmx(symbol="A股", start_date=start, end_date=end)
    raise ValueError(f"unknown range dataset: {dataset}")


def _fetch_survey_since(day: str) -> pd.DataFrame:
    """机构调研统计：上游为「公告日 > day」的**近 365 天滚动全量转储**。

    实测（2026-10-16）：date=20240101 仅回到 2025-10-16（365 天留存窗），
    date 更早不加深；窗口完全越界（如 2024 区间）返回空。故逐日循环调用
    是纯冗余（每次都是同一份近一年快照），一律单次调用。
    """
    import akshare as ak

    return ak.stock_jgdy_tj_em(date=day)


class _Pacer:
    """全局最小间隔节流（monotonic 计时，长跑不累积漂移）。"""

    def __init__(self, interval: float):
        self._interval = interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self._interval:
            time.sleep(self._interval - gap)
        self._last = time.monotonic()


def _is_seat_unsupported(symbol: str) -> bool:
    """席位接口不支持的标的：转债（11/12 开头的 6 位码，沪深两市）。

    实测（2026-10-16）：转债会上龙虎榜（全史 925 对），但
    stock_lhb_stock_detail_em 对转债返回空结构 → TypeError 重试尽失败。
    A 股股票代码无 11/12 开头，无碰撞。
    """
    digits = str(symbol).split(".")[0]
    return len(digits) == 6 and digits.isdigit() and digits.startswith(("11", "12"))


def _fetch_seat_pair(
    symbol: str, day: str, pacer: _Pacer
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """单股单日两方向席位（各 5 档）。任一方向重试仍失败 → None（该对整对下次重拉）。"""
    import akshare as ak

    # 裸码 = 后缀式小数点前段（600036.SH → 600036）。**不要**用
    # to_prefix() 再切片：未识别的码（转债 11/12 开头）to_prefix 原样透传，
    # [2:] 会截掉前两位 → 拉错标的（2026-10-16 踩过：111024 → 请求 "1024"）
    bare = str(symbol).split(".")[0]

    def _call(flag: str) -> pd.DataFrame | None:
        def _do() -> pd.DataFrame:
            pacer.wait()
            return ak.stock_lhb_stock_detail_em(symbol=bare, date=day, flag=flag)

        return _fetch_with_retry(_do, f"seat {bare} {day} {flag}")

    return _call("买入"), _call("卖出")


try:
    import fcntl as _fcntl  # POSIX（容器）；Windows 本地跑降级为无锁+告警
except ImportError:  # pragma: no cover —— 平台分支
    _fcntl = None


@contextlib.contextmanager
def _seat_lock():
    """席位同步单实例锁（flock 非阻塞）。产出是否持有锁。

    夜班任务与后台全史回补可能并发：两者都要读改写同一 parquet，
    无锁会互相覆盖。拿不到锁的一方直接跳过（数据由拿下锁的一方采，
    重叠窗+done 集保证不漏）。
    """
    if _fcntl is None:
        log.warning("[lhb_seat] 平台无 fcntl，跳过单实例锁（勿并发运行）")
        yield True
        return
    SMART_MONEY_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(SMART_MONEY_DIR / "_lhb_seat.lock", "w")  # noqa: SIM115 —— 生命周期跨 with
    try:
        try:
            _fcntl.flock(fh, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        try:
            _fcntl.flock(fh, _fcntl.LOCK_UN)
        finally:
            fh.close()


# ---------------------------------------------------------------- registry

DATASETS: dict[str, dict] = {
    "lhb_detail": {
        "file": "lhb_detail.parquet",
        "mode": "range",
        "normalize": normalize_lhb_detail,
        "keys": ["trade_date", "symbol", "reason"],
    },
    # 席位明细（mode="seat"）：抓取单元=股日对，由 lhb_detail 驱动 —— 插入序
    # 必须在 lhb_detail 之后，同一 run 里先刷驱动表再刷席位
    "lhb_seat": {
        "file": "lhb_seat.parquet",
        "mode": "seat",
        "normalize": None,  # 逐对归一（normalize_lhb_seat 带上下文），不走通用管线
        "keys": ["trade_date", "symbol", "side", "seat_name"],
    },
    "lhb_inst": {
        "file": "lhb_inst.parquet",
        "mode": "range",
        "normalize": normalize_lhb_inst,
        "keys": ["trade_date", "symbol", "reason"],
    },
    "lhb_branch": {
        "file": "lhb_branch.parquet",
        "mode": "range",
        "normalize": normalize_lhb_branch,
        "keys": ["trade_date", "branch_name"],
    },
    "block_trade": {
        "file": "block_trade.parquet",
        "mode": "range",
        "normalize": normalize_block_trade,
        "keys": ["trade_date", "symbol"],
    },
    "dzjy_detail": {
        "file": "dzjy_detail.parquet",
        "mode": "range",
        "normalize": normalize_dzjy_detail,
        "keys": [
            "trade_date",
            "symbol",
            "seat_buyer",
            "seat_seller",
            "deal_price",
            "volume",
        ],
    },
    "survey": {
        "file": "survey.parquet",
        "mode": "cumulative",
        "normalize": normalize_survey,
        "keys": ["trade_date", "symbol", "reception_type"],
    },
}


# ---------------------------------------------------------------- io / state


def load_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("meta 读取失败（按空处理）：%s", exc)
    return {}


def save_meta(meta: dict) -> None:
    SMART_MONEY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, META_PATH)


def merge_write(
    name: str, new_df: pd.DataFrame, keys: list[str], file: str | None = None
) -> int:
    """读旧 + 新 → 去重（新优先）→ 排序 → 原子替换。返回合并后行数。

    file 覆盖 DATASETS[name]["file"]（席位完成态索引等附属文件用）。
    """
    path = SMART_MONEY_DIR / (file or DATASETS[name]["file"])
    frames = []
    if path.exists():
        try:
            frames.append(pd.read_parquet(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] 旧文件读取失败，将整表重写：%s", name, exc)
    frames.append(new_df)
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=keys, keep="last")
    merged = merged.sort_values(keys, kind="stable").reset_index(drop=True)
    SMART_MONEY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return len(merged)


# ---------------------------------------------------------------- sync


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        # 下月首日 - 1 天 = 本月末
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(nxt - timedelta(days=1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def _fetch_with_retry(fn, label: str) -> pd.DataFrame | None:
    for attempt in range(MAX_RETRY):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 —— 东财偶发 SSL EOF/超时
            if attempt == MAX_RETRY - 1:
                log.error(
                    "[%s] 重试 %d 次仍失败：%s: %s",
                    label,
                    MAX_RETRY,
                    type(exc).__name__,
                    exc,
                )
                return None
            time.sleep(RETRY_WAITS[attempt])
    return None


def sync_dataset(
    name: str, floor: date, end: date, meta: dict, extend_history: bool = False
) -> None:
    spec = DATASETS[name]
    if spec["mode"] == "seat":
        # 席位是股日对驱动（见模块 docstring「席位回补」），起止/水位语义
        # 与 range/cumulative 不同，独立实现
        _sync_lhb_seat(floor, end, meta, extend_history)
        return
    last = meta.get(name, {}).get("last_date")
    last_d = pd.Timestamp(last).date() if last else None
    # 向前扩历史（--extend-history）：只补 [floor, last−1] 缺口（暖机期回填），
    # 尾部水位保持不变——扩档不应让「增量续跑」从缺口末日起跳。
    extending = bool(extend_history and last_d)
    if extending:
        start = floor
        end = min(end, last_d - timedelta(days=1))
        if start > end:
            log.info("[%s] 无需向前扩档（floor=%s ≥ last=%s）", name, floor, last)
            return
    elif last_d:
        start = max(floor, last_d - timedelta(days=OVERLAP_DAYS))
    else:
        start = floor
    if start > end:
        log.info("[%s] 已最新（last=%s）", name, last)
        return

    log.info("[%s] 同步 %s → %s", name, start, end)
    raw_frames: list[pd.DataFrame] = []
    watermark: date | None = None

    if spec["mode"] == "range":
        for c_start, c_end in _month_chunks(start, end):
            raw = _fetch_with_retry(
                lambda cs=c_start, ce=c_end: _fetch_range(  # noqa: B023
                    name, cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d")
                ),
                f"{name} {c_start}~{c_end}",
            )
            if raw is None:
                log.warning(
                    "[%s] 切片 %s~%s 失败，水位停在 %s（下次自愈）",
                    name,
                    c_start,
                    c_end,
                    watermark or last,
                )
                break
            if raw is not None and not raw.empty:
                raw_frames.append(raw)
            watermark = c_end
    elif spec["mode"] == "cumulative":
        # 上游 = 「公告日 > date」的 365 天滚动全量：单次调用取留存全量，
        # merge 去重幂等。下界取 start−1 日（严格大于语义含 start 当日）；
        # 返回集可能覆盖到 end 之后（留存窗尾部）——照常合并，无前视风险
        # （未来几天的公告只是提前入库）。水位 = min(end, 数据最大公告日)。
        call_day = (start - timedelta(days=1)).strftime("%Y%m%d")
        raw = _fetch_with_retry(
            lambda d=call_day: _fetch_survey_since(d),  # noqa: B023
            f"{name} >{call_day}",
        )
        if raw is None:
            log.warning("[%s] 调用失败，水位停在 %s（下次自愈）", name, last)
            return
        if not raw.empty:
            raw_frames.append(raw)
        watermark = end
    else:
        raise ValueError(f"未知 mode：{spec['mode']!r}（预期 range/cumulative）")

    if not raw_frames:
        # 区间全空：不落盘也不推进水位（下次重叠重拉，代价可忽略）
        log.info("[%s] 无新数据（%s~%s 全空）", name, start, end)
        return

    raw_all = pd.concat(raw_frames, ignore_index=True)
    norm = spec["normalize"](raw_all)
    if norm.empty:
        log.warning(
            "[%s] 归一化后无有效行（原始 %d 行），不落盘不推进水位", name, len(raw_all)
        )
        return
    rows = merge_write(name, norm, spec["keys"])
    new_watermark = norm["trade_date"].max()
    if extending:
        # 本次只补历史缺口：尾部水位不动（数据覆盖区间不降反扩）
        final = last_d
    else:
        # 水位只推进到「抓取区间成功覆盖」的日期：若中途失败，watermark < end
        final = min(watermark or start, pd.Timestamp(new_watermark).date())
    meta[name] = {
        "last_date": final.isoformat(),
        "rows": rows,
        "new_rows": len(norm),
        "fetched_at": utc_now().isoformat(),
    }
    save_meta(meta)
    log.info("[%s] 完成：新增 %d 行，累计 %d 行，水位 %s", name, len(norm), rows, final)


# ---------------------------------------------------------------- seat sync


def _seat_pending(
    detail: pd.DataFrame, start: date, end: date, done: set[tuple[str, str]]
) -> list[tuple[str, str]]:
    """[start, end] 内未采集的股日对，**从新到旧**排序（水位优先新鲜）。"""
    sub = detail[
        (detail["trade_date"] >= start.isoformat())
        & (detail["trade_date"] <= end.isoformat())
    ]
    return sorted(
        (
            (d, s)
            for d, s in sub[["trade_date", "symbol"]].itertuples(index=False, name=None)
            if (d, s) not in done
        ),
        key=lambda p: p[0],
        reverse=True,
    )


def _sync_lhb_seat(floor: date, end: date, meta: dict, extend_history: bool) -> None:
    """席位明细同步入口（语义全文见模块 docstring「席位回补」）。

    锁内重读水位与完成态（另一进程可能刚推进过），再定窗口与待采集。
    """
    name = "lhb_seat"
    with _seat_lock() as acquired:
        if not acquired:
            log.warning("[lhb_seat] 另一进程正在同步（flock 被占），本次跳过")
            return
        meta_disk = load_meta()  # 锁内重读：以磁盘最新进度为准
        last = meta_disk.get(name, {}).get("last_date")
        last_d = pd.Timestamp(last).date() if last else None
        if extend_history:
            start = floor  # 显式全史回补
        elif last_d:
            start = max(floor, last_d - timedelta(days=SEAT_OVERLAP_DAYS))
        else:
            start = max(floor, end - timedelta(days=SEAT_BOOTSTRAP_DAYS))
        if start > end:
            log.info("[lhb_seat] 已最新（last=%s）", last)
            return

        detail_path = SMART_MONEY_DIR / DATASETS["lhb_detail"]["file"]
        if not detail_path.exists():
            log.warning(
                "[lhb_seat] 缺驱动表 lhb_detail，跳过（同 run 里它先同步即可自愈）"
            )
            return
        detail = pd.read_parquet(
            detail_path, columns=["trade_date", "symbol"]
        ).drop_duplicates()

        pairs_path = SMART_MONEY_DIR / SEAT_PAIRS_FILE
        done: set[tuple[str, str]] = set()
        day_done: dict[str, int] = {}
        if pairs_path.exists():
            pdone = pd.read_parquet(pairs_path)
            done = set(
                pdone[["trade_date", "symbol"]].itertuples(index=False, name=None)
            )
            day_done = pdone.groupby("trade_date")["symbol"].nunique().to_dict()
        day_total = detail.groupby("trade_date")["symbol"].nunique().to_dict()

        pending = _seat_pending(detail, start, end, done)
        if not pending:
            log.info(
                "[lhb_seat] 窗口 %s~%s 无待采股日（累计 %d 对）", start, end, len(done)
            )
            return
        _run_seat_fetch(name, pending, day_total, day_done, done, meta_disk)
        if name in meta_disk:
            meta[name] = meta_disk[name]


def _run_seat_fetch(
    name: str,
    pending: list[tuple[str, str]],
    day_total: dict[str, int],
    day_done: dict[str, int],
    done: set[tuple[str, str]],
    meta: dict,
) -> None:
    spec = DATASETS[name]
    pacer = _Pacer(SEAT_PACE_SECONDS)
    rows_buf: list[pd.DataFrame] = []
    pair_buf: list[dict] = []
    stats = {
        "ok": 0,
        "empty": 0,
        "skipped": 0,
        "fail": 0,
        "rows_total": 0,
        "pairs_total": len(done),
    }
    total_pairs = sum(day_total.values())
    n = len(pending)
    log.info(
        "[lhb_seat] 待采 %d 对股日（%s → %s，预计 %.1f 小时 @%.2fs/次）",
        n,
        pending[0][0],
        pending[-1][0],
        n * 2 * SEAT_PACE_SECONDS / 3600,
        SEAT_PACE_SECONDS,
    )
    t0 = time.monotonic()

    def _flush() -> None:
        if rows_buf:
            merged = pd.concat(rows_buf, ignore_index=True)
            stats["rows_total"] = merge_write(name, merged, spec["keys"])
            rows_buf.clear()
        if pair_buf:
            pdf = pd.DataFrame(pair_buf)
            stats["pairs_total"] = merge_write(
                name, pdf, ["trade_date", "symbol"], file=SEAT_PAIRS_FILE
            )
            pair_buf.clear()
        complete = [d for d, tot in day_total.items() if day_done.get(d, 0) >= tot]
        covered = [d for d, cnt in day_done.items() if cnt > 0]
        m = meta.setdefault(name, {})
        if complete:
            m["last_date"] = max(complete)
        if covered:
            m["coverage_from"] = min(covered)
        m.update(
            {
                "rows": stats["rows_total"],
                "pairs_done": stats["pairs_total"],
                "pairs_pending": max(total_pairs - stats["pairs_total"], 0),
                "fetched_at": utc_now().isoformat(),
            }
        )
        save_meta(meta)

    for i, (day, symbol) in enumerate(pending, 1):
        if _is_seat_unsupported(symbol):
            # 转债（11/12 开头）也会上龙虎榜，但席位接口只支持股票：
            # 记录为 skipped 入完成态（否则当日永不完整、水位卡死，且每轮白重试）
            stats["skipped"] += 1
            pair_buf.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "rows": 0,
                    "status": "skipped",
                    "fetched_at": utc_now().isoformat(),
                }
            )
            done.add((day, symbol))
            day_done[day] = day_done.get(day, 0) + 1
            continue
        buy, sell = _fetch_seat_pair(symbol, day.replace("-", ""), pacer)
        if buy is None or sell is None:
            stats["fail"] += 1  # 整对不落盘不入 done → 下次重拉
        else:
            frames = []
            if not buy.empty:
                frames.append(normalize_lhb_seat(buy, day, symbol, "buy"))
            if not sell.empty:
                frames.append(normalize_lhb_seat(sell, day, symbol, "sell"))
            n_rows = 0
            if frames:
                fr = pd.concat(frames, ignore_index=True)
                n_rows = len(fr)
                if n_rows:
                    rows_buf.append(fr)
            stats["ok" if n_rows else "empty"] += 1
            pair_buf.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "rows": n_rows,
                    "status": "ok" if n_rows else "empty",
                    "fetched_at": utc_now().isoformat(),
                }
            )
            done.add((day, symbol))
            day_done[day] = day_done.get(day, 0) + 1
        if i % SEAT_CHECKPOINT_EVERY == 0:
            _flush()
            log.info(
                "[lhb_seat] 进度 %d/%d（%s 往回；ok=%d empty=%d skipped=%d fail=%d）",
                i,
                n,
                day,
                stats["ok"],
                stats["empty"],
                stats["skipped"],
                stats["fail"],
            )
        if i == SEAT_BAN_GUARD_MIN and stats["ok"] + stats["empty"] == 0:
            log.error(
                "[lhb_seat] 前 %d 对全部失败（疑被限流），中止本次运行（已完成部分照常落盘）",
                i,
            )
            break
    _flush()
    elapsed = time.monotonic() - t0
    log.info(
        "[lhb_seat] 本轮结束：ok=%d empty=%d skipped=%d fail=%d，累计 %d 对（待采 %d），"
        "水位 %s，耗时 %.1f 分钟（失败对下次自动重拉）",
        stats["ok"],
        stats["empty"],
        stats["skipped"],
        stats["fail"],
        stats["pairs_total"],
        max(total_pairs - stats["pairs_total"], 0),
        meta.get(name, {}).get("last_date"),
        elapsed / 60,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="聪明钱数据采集（A股）")
    parser.add_argument(
        "--start", default=None, help="回补下限 YYYY-MM-DD（默认 2024-01-01）"
    )
    parser.add_argument("--end", default=None, help="采集截止 YYYY-MM-DD（默认今天）")
    parser.add_argument("--dataset", default=None, help="仅同步单表（默认全部）")
    parser.add_argument(
        "--extend-history",
        action="store_true",
        help="向前扩历史：补 [--start, 水位−1] 缺口（暖机期回填），尾部水位不动",
    )
    args = parser.parse_args()

    floor = pd.Timestamp(args.start).date() if args.start else date(2024, 1, 1)
    end = pd.Timestamp(args.end).date() if args.end else date.today()

    names = [args.dataset] if args.dataset else list(DATASETS)
    for n in names:
        if n not in DATASETS:
            log.error("未知数据集：%s（可选 %s）", n, ", ".join(DATASETS))
            return 2

    meta = load_meta()
    for n in names:
        try:
            sync_dataset(n, floor, end, meta, extend_history=args.extend_history)
        except Exception as exc:  # noqa: BLE001 —— 单表失败不拖垮其余表
            log.exception("[%s] 同步异常：%s", n, exc)
    log.info("全部完成。meta=%s", META_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
