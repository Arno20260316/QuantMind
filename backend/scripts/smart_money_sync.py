#!/usr/bin/env python3
"""聪明钱数据采集（A股）→ QuantDB 扁平 parquet。

数据面（akshare 1.18.88 实测，2026-10-15）：
  - 龙虎榜明细     stock_lhb_detail_em(start,end)   全市场×日×上榜原因
  - 机构席位统计   stock_lhb_jgmmtj_em(start,end)   全市场×日（机构专用席位）
  - 活跃营业部     stock_lhb_hyyyb_em(start,end)    营业部×日
  - 大宗交易统计   stock_dzjy_mrtj(start,end)       全市场×日
  - 机构调研统计   stock_jgdy_tj_em(date)           全市场×公告日（**近 365 天
    滚动全量**，见下「调研留存窗」）

落盘（扁平单文件，数据量小免分区读放大）：
  {QM_QUANTDB_DATA_DIR}/2_base_sector/smart_money/
    lhb_detail.parquet / lhb_inst.parquet / lhb_branch.parquet
    block_trade.parquet / survey.parquet / _sync_meta.json(水位)

口径契约（改动即破坏下游，见 docs/市场分析_聪明钱追踪与选股_细案_20261015.md §2）：
  - symbol 后缀式（600036.SH，QuantDB 分层口径）
  - trade_date 'YYYY-MM-DD' 字符串；金额一律**元**、成交量**股**（写入时换算）
  - 折溢率/占比类为**分数**（0.0123 = 1.23%），换手率/涨跌幅为百分数
  - **前向收益列封存**：上榜后1/2/5/10日 → fwd_ret_1d/2d/5d/10d，
    仅可展示，绝不可入信号（signal 层显式排除 fwd_* 前缀，有单测钉死）
  - 单位自检：用恒等式（净买额/成交额≈占比、价×量≈额）在真实数据上定标并落日志

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
"""

from __future__ import annotations

import argparse
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
        log.warning("[%s] 单位自检异常: %s median=%.4g 不在 [%.1g, %.1g]（单位口径可能变了）",
                    table, col, med, lo, hi)


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
    net_ratio: pd.Series, net: pd.Series, denominators: list[pd.Series], table: str, col: str
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
            log.info("[%s] %s 定标：百分数 → 归一到分数（scale=%.1f）", table, col, scale)
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
        out["net_ratio"], out["lhb_net_buy"], [out["lhb_amount"], out["market_amount"]],
        "lhb_detail", "net_ratio",
    )
    out["amount_ratio"] = _canonical_ratio(
        out["amount_ratio"], out["lhb_amount"], [out["market_amount"]],
        "lhb_detail", "amount_ratio",
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
        out["inst_net_ratio"], out["inst_net_buy"], [out["market_amount"]],
        "lhb_inst", "inst_net_ratio",
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
            "amount_ratio": _num(df["成交总额/流通市值"]) / 100.0,  # 发布口径为百分数 → 分数
        }
    )
    # 恒等式定标：价 × 量(股) ≈ 额(元)
    ok = out["deal_price"].notna() & out["volume"].notna() & (out["amount"] > 0)
    if ok.sum() >= 10:
        resid = ((out["deal_price"] * out["volume"] - out["amount"]).abs() / out["amount"])[ok]
        med = resid.median()
        if med > 0.05:
            log.warning("[block_trade] 价×量 恒等式偏差 median=%.3f（万股/万元换算可能变了）", med)
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
    raise ValueError(f"unknown range dataset: {dataset}")


def _fetch_survey_since(day: str) -> pd.DataFrame:
    """机构调研统计：上游为「公告日 > day」的**近 365 天滚动全量转储**。

    实测（2026-10-16）：date=20240101 仅回到 2025-10-16（365 天留存窗），
    date 更早不加深；窗口完全越界（如 2024 区间）返回空。故逐日循环调用
    是纯冗余（每次都是同一份近一年快照），一律单次调用。
    """
    import akshare as ak

    return ak.stock_jgdy_tj_em(date=day)


# ---------------------------------------------------------------- registry

DATASETS: dict[str, dict] = {
    "lhb_detail": {
        "file": "lhb_detail.parquet",
        "mode": "range",
        "normalize": normalize_lhb_detail,
        "keys": ["trade_date", "symbol", "reason"],
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


def merge_write(name: str, new_df: pd.DataFrame, keys: list[str]) -> int:
    """读旧 + 新 → 去重（新优先）→ 排序 → 原子替换。返回合并后行数。"""
    path = SMART_MONEY_DIR / DATASETS[name]["file"]
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
                log.error("[%s] 重试 %d 次仍失败：%s: %s", label, MAX_RETRY, type(exc).__name__, exc)
                return None
            time.sleep(RETRY_WAITS[attempt])
    return None


def sync_dataset(
    name: str, floor: date, end: date, meta: dict, extend_history: bool = False
) -> None:
    spec = DATASETS[name]
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
                log.warning("[%s] 切片 %s~%s 失败，水位停在 %s（下次自愈）",
                            name, c_start, c_end, watermark or last)
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
        log.warning("[%s] 归一化后无有效行（原始 %d 行），不落盘不推进水位", name, len(raw_all))
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


def main() -> int:
    parser = argparse.ArgumentParser(description="聪明钱数据采集（A股）")
    parser.add_argument("--start", default=None, help="回补下限 YYYY-MM-DD（默认 2024-01-01）")
    parser.add_argument("--end", default=None, help="采集截止 YYYY-MM-DD（默认今天）")
    parser.add_argument("--dataset", default=None, help="仅同步单表（默认全部）")
    parser.add_argument(
        "--extend-history",
        action="store_true",
        help="向前扩历史：补 [--start, 水位−1] 缺口（暖机期回填），尾部水位不动",
    )
    args = parser.parse_args()

    floor = (
        pd.Timestamp(args.start).date() if args.start else date(2024, 1, 1)
    )
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
