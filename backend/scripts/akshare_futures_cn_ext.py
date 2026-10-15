#!/usr/bin/env python3
"""QuantFutures 国内扩展数据集（二批）— 现货基差/结算参数/交易规则/合约信息/
官方日行情/库存/商品指数/费率/上金所结算/生猪产业链/席位排名。

落盘（data/quantfutures/2_base_sector/）:
  spot_basis/dt=YYYYMMDD/data.parquet        生意社现货价+基差（近月/主力双口径）
  settle_params/dt=YYYYMMDD/data.parquet     日结算参数（SHFE/INE/CZCE 统一宽表）
  trade_rule/dt=YYYYMMDD/data.parquet        品种级交易规则快照（全所）
  contract_meta/dt=YYYYMMDD/data.parquet     合约生命周期（SHFE/CZCE/CFFEX/INE）
  exchange_daily/dt=YYYYMMDD/data.parquet    交易所官方日行情（含真实成交额）
  inventory_em/{CODE}.parquet                东财库存（68 品种，滚动 ~3 个月）
  ccidx_index/data.parquet                   中证商品期货指数×2（2022-10 起）
  fees_info/data.parquet                     全合约费率快照（乘数/开平今费/保证金）
  hog_chain/{family}.parquet                 生猪产业链（core/cost/supply 三族长表）
  member_rank_sina/dt=YYYYMMDD/data.parquet  SHFE/CZCE 主力合约席位排名（新浪）

口径纪律（写解析器前必读）:
  - **比率一律小数（0-1）**：源侧百分数（CZCE margin_ratio '20'、futures_rule
    保证金 16.0、涨跌停 7.0）落盘前 ÷100；SHFE settle 源本身就是小数。
  - CZCE 结算参数为带千分位字符串（'8,000.00'）与 '±8' 涨跌停——统一清洗；
    '±' 为对称幅度，落盘无符号。
  - 新浪席位 CZCE 合约须 **4 位年**（AP2610）；交易所官方口径是 3 位年（AP610）
    —— `_czce_contract_sina()` 是唯一转换点。
  - 生意社 date 为字符串 YYYYMMDD；非交易日 warning 属正常跳过。
  - 死接口（2026-10-15 实测，勿再尝试）：`futures_contract_info_dce/gfex`、
    `futures_delivery_*`/`futures_to_spot_*` 全系、`futures_inventory_99`、
    `futures_settle_cffex/gfex`（恒空）。侦查收据见
    `docs/期货数据面扩容_国内数据集二批_细案_20261015.md`。

用法:
  python backend/scripts/akshare_futures_cn_ext.py --task spot_basis --days 500
  python backend/scripts/akshare_futures_cn_ext.py --task all --days 5
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.scripts.akshare_futures_extra import (  # noqa: E402
    BASE_DIR,
    _recent_trading_days,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("futures_cn_ext")

RELEASE = "akshare"


# ---------------- 通用助手 ----------------


def _retry(fn: Callable[[], Any], *, tries: int = 3, delay: float = 0.8) -> Any:
    """带退避的重试（sina/100ppi 系接口偶发断连）。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay * (i + 1))
    raise last  # type: ignore[misc]


class _VenueBreaker:
    """交易所接口的连败熔断器。

    空帧是「无数据」（休市日/尚未发布），**不算故障**——旧逻辑把春节等
    连续休市日记成连续失败，3 天就把全部市场振出局（实测 2026 春节后
    exchange_daily 整段 2026-02-16 起被跳过）。只有异常才计连败；连败
    ≥ strikes 次进入 cooldown 天冷却，冷却期满自动重探（限流多为瞬时）。
    """

    def __init__(
        self, venues: Iterable[str], *, strikes: int = 5, cooldown: int = 20
    ) -> None:
        self.strikes = strikes
        self.cooldown = cooldown
        self._fails: dict[str, int] = dict.fromkeys(venues, 0)
        self._resume: dict[str, int] = dict.fromkeys(venues, 0)  # 解除冷却的日期下标

    def skip(self, venue: str, idx: int) -> bool:
        return self._resume[venue] > idx

    def on_success(self, venue: str) -> None:
        self._fails[venue] = 0

    def on_empty(self, venue: str) -> None:
        # 休市日/暂无数据：清连败计数（与成功同待遇），避免假日振出
        self._fails[venue] = 0

    def on_error(self, venue: str, idx: int) -> bool:
        """记一次异常；触发熔断返回 True（调用方据此打告警日志）。"""
        self._fails[venue] += 1
        if self._fails[venue] >= self.strikes:
            self._resume[venue] = idx + self.cooldown
            self._fails[venue] = 0
            return True
        return False

    def cooling(self, idx: int) -> list[str]:
        """任务结束时仍处冷却期的市场（本轮被跳过的）。"""
        return sorted(v for v, r in self._resume.items() if r > idx)


def _clean_num(series: pd.Series) -> pd.Series:
    """清洗源侧字符串数值：'8,000.00' → 8000.0；'±8' → 8.0；'20%' → 20.0；'' → NaN。"""
    s = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("±", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(s, errors="coerce")


def _date_str(series: pd.Series) -> pd.Series:
    """合约日期列统一 YYYY-MM-DD 字串。

    源侧混型（datetime.date / str / CZCE「2026-10-21 15:00 北京时间」带后缀），
    直写 parquet 会因列内类型不一致报 Conversion failed——先归一到字串。
    取前 10 字符 + 显式格式，避开 pandas 首元素定格式的推断路径。
    """
    s = series.astype(str).str.replace("/", "-", regex=False)
    dt = pd.to_datetime(s.str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
    return dt.dt.strftime("%Y-%m-%d")


def _stamp(df: pd.DataFrame) -> pd.DataFrame:
    df["release_id"] = RELEASE
    df["published_at"] = pd.Timestamp.now().isoformat()
    return df


def _write_part(out_dir: Path, d: str, df: pd.DataFrame) -> None:
    pdir = out_dir / f"dt={d}"
    pdir.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(pdir / "data.parquet", index=False)
    except Exception:
        # 写失败不留空分区目录（半写状态会把「最新分区」推到空目录，
        # 健康度测试与面板预览都会误判）
        if not (pdir / "data.parquet").exists():
            pdir.rmdir()
        raise


def _dt_str(d: str) -> pd.Timestamp:
    return pd.to_datetime(d, format="%Y%m%d")


# ---------------- 1. spot_basis 现货价+基差（生意社） ----------------

# 实测可用品种集（2026-10-15；全所主力品种，约 52 品种/日有值）
_SPOT_VARS = [
    "IF",
    "IC",
    "IM",
    "IH",
    "T",
    "TF",
    "TS",
    "TL",
    "C",
    "CS",
    "A",
    "B",
    "M",
    "Y",
    "P",
    "JD",
    "L",
    "V",
    "PP",
    "J",
    "JM",
    "I",
    "EG",
    "RR",
    "EB",
    "PG",
    "LH",
    "CF",
    "SR",
    "TA",
    "OI",
    "MA",
    "FG",
    "RM",
    "SF",
    "SM",
    "CY",
    "AP",
    "UR",
    "CJ",
    "SA",
    "PK",
    "PF",
    "PX",
    "SH",
    "PR",
    "PL",
    "CU",
    "AL",
    "ZN",
    "PB",
    "NI",
    "SN",
    "AU",
    "AG",
    "RB",
    "HC",
    "FU",
    "BU",
    "RU",
    "SC",
    "NR",
    "SP",
    "SS",
    "LU",
    "BC",
    "AO",
    "BR",
    "EC",
    "AD",
    "OP",
    "SI",
    "LC",
    "PS",
]

_SPOT_NUM_COLS = [
    "spot_price",
    "near_contract_price",
    "dominant_contract_price",
    "near_basis",
    "dom_basis",
    "near_basis_rate",
    "dom_basis_rate",
]


def _norm_spot(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["time"] = pd.to_datetime(
        df["date"].astype(str), format="%Y%m%d", errors="coerce"
    )
    for c in _SPOT_NUM_COLS:
        if c in df.columns:
            df[c] = _clean_num(df[c])
    return df.drop(columns=["date"]).dropna(subset=["time"])


def task_spot_basis(days: int = 90) -> dict:
    """生意社现货价+基差（date 幂等覆盖写）。按 ~20 交易日分块抓取。"""
    import akshare as ak

    out_dir = BASE_DIR / "spot_basis"
    dates = _recent_trading_days(days)
    if not dates:
        return {"task": "spot_basis", "error": "无交易日分区"}
    written = failed = 0
    chunk_size = 20
    for i in range(0, len(dates), chunk_size):
        chunk = dates[i : i + chunk_size]
        try:
            df = _retry(
                lambda c=chunk: ak.futures_spot_price_daily(
                    start_day=c[0], end_day=c[-1], vars_list=_SPOT_VARS
                ),
                tries=2,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("spot %s~%s 失败: %s", chunk[0], chunk[-1], str(exc)[:80])
            failed += 1
            continue
        if df is None or df.empty:
            continue
        norm = _stamp(_norm_spot(df))
        for d, g in norm.groupby(norm["time"].dt.strftime("%Y%m%d")):
            _write_part(out_dir, d, g.reset_index(drop=True))
            written += 1
        log.info("spot %s~%s 完成（%d 行）", chunk[0], chunk[-1], len(norm))
        time.sleep(0.3)
    return {
        "task": "spot_basis",
        "days": len(dates),
        "written": written,
        "failed": failed,
    }


# ---------------- 2. settle_params 日结算参数 ----------------

# 统一宽表列序（SHFE/INE 与 CZCE 字段并集；比率一律小数）
_SETTLE_COLS = [
    "time",
    "venue",
    "symbol",
    "variety",
    "settle_price",
    "margin_spec_long",
    "margin_hedge_long",
    "margin_spec_short",
    "margin_hedge_short",
    "margin_rate",
    "limit_rate",
    "trade_fee_ratio",
    "close_today_fee_ratio",
    "trade_fee_abs",
    "close_today_fee_abs",
    "delivery_fee_abs",
    "fee_type",
    "is_close_today",
    "is_single_market",
    "single_market_days",
    "position_limit",
    "trade_limit",
    "release_id",
    "published_at",
]


def _norm_settle_shfe(df: pd.DataFrame, venue: str, d: str) -> pd.DataFrame:
    """SHFE/INE：比率源本身为小数（0.2=20%），直接保列。"""
    ren = {
        "spec_long_margin_ratio": "margin_spec_long",
        "hedge_long_margin_ratio": "margin_hedge_long",
        "spec_short_margin_ratio": "margin_spec_short",
        "hedge_short_margin_ratio": "margin_hedge_short",
        "trade_fee_ratio": "trade_fee_ratio",
        "close_today_fee_ratio": "close_today_fee_ratio",
        "is_close_today": "is_close_today",
    }
    df = df.rename(columns=ren).copy()
    df["time"] = _dt_str(d)
    df["venue"] = venue
    df["settle_price"] = pd.to_numeric(df.get("settle_price"), errors="coerce")
    return df


def _norm_settle_czce(df: pd.DataFrame, venue: str, d: str) -> pd.DataFrame:
    """CZCE：'8,000.00' 千分位、'±8' 涨跌停、'20' 保证金（百分数）→ 小数。"""
    df = df.copy()
    df["time"] = _dt_str(d)
    df["venue"] = venue
    df["settle_price"] = _clean_num(df["settle_price"])
    df["margin_rate"] = _clean_num(df["margin_ratio"]) / 100.0
    df["limit_rate"] = _clean_num(df["limit_ratio"]) / 100.0
    df["trade_fee_abs"] = _clean_num(df["trade_fee"])
    df["close_today_fee_abs"] = _clean_num(df["close_today_fee"])
    df["delivery_fee_abs"] = _clean_num(df["delivery_fee"])
    df = df.drop(
        columns=[
            "margin_ratio",
            "limit_ratio",
            "trade_fee",
            "close_today_fee",
            "delivery_fee",
        ]
    )
    return df


def task_settle_params(days: int = 90) -> dict:
    """SHFE/INE/CZCE 日结算参数（保证金/手续费/涨跌停/限仓），逐日分区。"""
    import akshare as ak

    out_dir = BASE_DIR / "settle_params"
    dates = _recent_trading_days(days)
    venues = (
        ("SHFE", ak.futures_settle_shfe, _norm_settle_shfe),
        ("INE", ak.futures_settle_ine, _norm_settle_shfe),
        ("CZCE", ak.futures_settle_czce, _norm_settle_czce),
    )
    written = empty = 0
    for d in dates:
        frames = []
        for venue, fn, norm in venues:
            try:
                df = fn(d)
            except Exception:  # noqa: BLE001
                continue
            if df is None or df.empty:
                empty += 1
                continue
            frames.append(norm(df, venue, d))
        if frames:
            out = pd.concat(frames, ignore_index=True).reindex(
                columns=[
                    c
                    for c in _SETTLE_COLS
                    if c in set().union(*[f.columns for f in frames])
                ]
            )
            _write_part(out_dir, d, _stamp(out))
            written += 1
        time.sleep(0.15)
    return {
        "task": "settle_params",
        "days": len(dates),
        "written": written,
        "empty": empty,
    }


# ---------------- 3. trade_rule 品种级交易规则 ----------------

_RULE_COLS = [
    "time",
    "exchange",
    "variety_name",
    "variety",
    "margin_rate",
    "limit_rate",
    "multiplier",
    "tick_size",
    "max_limit_order_lots",
    "special_adjust",
    "remark",
    "release_id",
    "published_at",
]


def _norm_rule(df: pd.DataFrame, d: str) -> pd.DataFrame:
    ren = {
        "交易所": "exchange",
        "品种": "variety_name",
        "代码": "variety",
        "交易保证金比例": "margin_rate",
        "涨跌停板幅度": "limit_rate",
        "合约乘数": "multiplier",
        "最小变动价位": "tick_size",
        "限价单每笔最大下单手数": "max_limit_order_lots",
        "特殊合约参数调整": "special_adjust",
        "调整备注": "remark",
    }
    df = df.rename(columns=ren).copy()
    df["time"] = _dt_str(d)
    df["margin_rate"] = _clean_num(df["margin_rate"]) / 100.0
    df["limit_rate"] = _clean_num(df["limit_rate"]) / 100.0
    df["multiplier"] = _clean_num(df["multiplier"])
    df["tick_size"] = _clean_num(df["tick_size"])
    df["max_limit_order_lots"] = _clean_num(df["max_limit_order_lots"])
    return df


def task_trade_rule(days: int = 90) -> dict:
    import akshare as ak

    out_dir = BASE_DIR / "trade_rule"
    dates = _recent_trading_days(days)
    written = failed = 0
    for d in dates:
        try:
            df = ak.futures_rule(d)
        except Exception:  # noqa: BLE001
            failed += 1
            time.sleep(0.3)
            continue
        if df is None or df.empty:
            continue
        out = _stamp(_norm_rule(df, d))
        out = out.reindex(columns=[c for c in _RULE_COLS if c in out.columns])
        _write_part(out_dir, d, out)
        written += 1
        time.sleep(0.15)
    return {
        "task": "trade_rule",
        "days": len(dates),
        "written": written,
        "failed": failed,
    }


# ---------------- 4. contract_meta 合约生命周期 ----------------

_META_COLS = [
    "time",
    "venue",
    "symbol",
    "variety",
    "name",
    "contract_month",
    "listed_date",
    "expire_date",
    "last_trade_date",
    "delivery_start_date",
    "delivery_settle_date",
    "last_delivery_date",
    "base_price",
    "margin_rate",
    "limit_up",
    "limit_down",
    "limit_up_price",
    "limit_down_price",
    "position_limit",
    "tick_size",
    "trade_unit",
    "settlement_method",
    "release_id",
    "published_at",
]


def _norm_meta_shfe(df: pd.DataFrame, venue: str, d: str) -> pd.DataFrame:
    ren = {
        "合约代码": "symbol",
        "上市日": "listed_date",
        "到期日": "expire_date",
        "开始交割日": "delivery_start_date",
        "最后交割日": "last_delivery_date",
        "挂牌基准价": "base_price",
        "更新时间": "source_update_time",
    }
    df = df.rename(columns=ren).copy()
    df["time"] = _dt_str(d)
    df["venue"] = venue
    return df


def _norm_meta_cffex(df: pd.DataFrame, venue: str, d: str) -> pd.DataFrame:
    ren = {
        "合约代码": "symbol",
        "合约月份": "contract_month",
        "挂盘基准价": "base_price",
        "上市日": "listed_date",
        "最后交易日": "last_trade_date",
        "涨停板幅度": "limit_up",
        "跌停板幅度": "limit_down",
        "涨停板价位": "limit_up_price",
        "跌停板价位": "limit_down_price",
        "持仓限额": "position_limit",
        "品种": "variety",
    }
    df = df.rename(columns=ren).copy()
    df["time"] = _dt_str(d)
    df["venue"] = venue
    return df


def _norm_meta_czce(df: pd.DataFrame, venue: str, d: str) -> pd.DataFrame:
    """CZCE 列名含「待国家公布…」动态后缀，按前缀匹配改名。"""
    ren = {
        "合约代码": "symbol",
        "产品代码": "variety",
        "产品名称": "name",
        "第一交易日": "listed_date",
        "最后交割日": "last_delivery_date",
        "交割结算日": "delivery_settle_date",
        "最小变动价位": "tick_size",
        "交易单位": "trade_unit",
        "结算方式": "settlement_method",
        "交易保证金率": "margin_rate",
        "涨跌停板": "limit_down",
    }
    for c in df.columns:
        cs = str(c)
        if cs.startswith("最后交易日"):
            ren[c] = "last_trade_date"
        elif cs.startswith("到期时间"):
            ren[c] = "expire_date"
    df = df.rename(columns=ren).copy()
    df["time"] = _dt_str(d)
    df["venue"] = venue
    return df


def task_contract_meta(days: int = 90) -> dict:
    """合约信息快照（SHFE/CZCE/CFFEX/INE；DCE/GFEX 接口死，见细案）。"""
    import akshare as ak

    out_dir = BASE_DIR / "contract_meta"
    dates = _recent_trading_days(days)
    venues = (
        ("SHFE", ak.futures_contract_info_shfe, _norm_meta_shfe),
        ("CZCE", ak.futures_contract_info_czce, _norm_meta_czce),
        ("CFFEX", ak.futures_contract_info_cffex, _norm_meta_cffex),
        ("INE", ak.futures_contract_info_ine, _norm_meta_shfe),
    )
    written = empty = 0
    for d in dates:
        frames = []
        for venue, fn, norm in venues:
            try:
                df = fn(d)
            except Exception:  # noqa: BLE001
                continue
            if df is None or df.empty:
                empty += 1
                continue
            frames.append(norm(df, venue, d))
        if frames:
            cols = set().union(*[f.columns for f in frames])
            out = pd.concat(frames, ignore_index=True).reindex(
                columns=[c for c in _META_COLS if c in cols]
            )
            # 混型收口：日期列（date/str/CZCE 带后缀）与数值列（CFFEX 涨跌停为
            # 字符串 '0.005'）跨所 concat 后类型不一致，直写 parquet 会报
            # Conversion failed——统一先归一再落盘。
            for c in (
                "listed_date",
                "expire_date",
                "last_trade_date",
                "delivery_start_date",
                "delivery_settle_date",
                "last_delivery_date",
            ):
                if c in out.columns:
                    out[c] = _date_str(out[c])
            for c in (
                "base_price",
                "margin_rate",
                "limit_up",
                "limit_down",
                "limit_up_price",
                "limit_down_price",
                "position_limit",
                "tick_size",
            ):
                if c in out.columns:
                    out[c] = _clean_num(out[c])
            _write_part(out_dir, d, _stamp(out))
            written += 1
        time.sleep(0.15)
    return {
        "task": "contract_meta",
        "days": len(dates),
        "written": written,
        "empty": empty,
    }


# ---------------- 5. exchange_daily 交易所官方日行情 ----------------

_GFD_COLS = [
    "time",
    "venue",
    "symbol",
    "variety",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "turnover",
    "settle",
    "pre_settle",
    "release_id",
    "published_at",
]
_GFD_VENUES = ("SHFE", "CZCE", "CFFEX", "INE")
_GFD_NUM = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "turnover",
    "settle",
    "pre_settle",
)


def task_exchange_daily(days: int = 240) -> dict:
    """交易所官方全合约日行情（真实成交额 turnover）。DCE 接口死、GFEX 恒空。

    单市场异常连败走 `_VenueBreaker` 熔断（空帧 = 休市/暂无数据，不算故障）。
    """
    import akshare as ak

    out_dir = BASE_DIR / "exchange_daily"
    dates = _recent_trading_days(days)
    written = 0
    breaker = _VenueBreaker(_GFD_VENUES)
    for idx, d in enumerate(dates):
        frames = []
        for venue in _GFD_VENUES:
            if breaker.skip(venue, idx):
                continue
            try:
                df = ak.get_futures_daily(start_date=d, end_date=d, market=venue)
            except Exception:  # noqa: BLE001
                if breaker.on_error(venue, idx):
                    log.warning(
                        "exchange_daily %s 连续 %d 次异常，冷却 %d 天后重探",
                        venue,
                        breaker.strikes,
                        breaker.cooldown,
                    )
                continue
            if df is None or df.empty:
                breaker.on_empty(venue)
                continue
            breaker.on_success(venue)
            df = df.drop(columns=[c for c in ("index",) if c in df.columns]).copy()
            df["venue"] = venue
            df["time"] = _dt_str(d)
            for c in _GFD_NUM:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            frames.append(df)
        if frames:
            cols = set().union(*[f.columns for f in frames])
            out = pd.concat(frames, ignore_index=True).reindex(
                columns=[c for c in _GFD_COLS if c in cols]
            )
            _write_part(out_dir, d, _stamp(out))
            written += 1
        time.sleep(0.15)
    disabled = breaker.cooling(len(dates))
    if disabled:
        log.warning("exchange_daily 结束时仍在冷却期（本轮跳过）: %s", disabled)
    return {
        "task": "exchange_daily",
        "days": len(dates),
        "written": written,
        "disabled": disabled,
    }


# ---------------- 6. inventory_em 东财库存 ----------------

_INV_COLS = ["time", "symbol", "inventory", "delta", "release_id", "published_at"]


def task_inventory_em(days: int = 90) -> dict:
    """东财交易所库存（68 品种；源为滚动 ~3 个月窗口，整文件覆盖写）。"""
    import akshare as ak
    from akshare.futures.cons import futures_inventory_em_symbol_dict

    out_dir = BASE_DIR / "inventory_em"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for code, product_id in sorted(futures_inventory_em_symbol_dict.items()):
        if not product_id:  # 源侧缺映射（bb/bc/fb 等）
            continue
        try:
            df = _retry(lambda c=code: ak.futures_inventory_em(symbol=c), tries=2)
        except Exception:  # noqa: BLE001
            fail += 1
            continue
        if df is None or df.empty:
            fail += 1
            continue
        df = df.rename(columns={"日期": "time", "库存": "inventory", "增减": "delta"})
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df["inventory"] = _clean_num(df["inventory"])
        df["delta"] = _clean_num(df["delta"])
        df["symbol"] = str(product_id).upper()
        df = df.dropna(subset=["time"]).sort_values("time")
        out = _stamp(df).reindex(columns=_INV_COLS)
        out.to_parquet(out_dir / f"{str(product_id).upper()}.parquet", index=False)
        ok += 1
        time.sleep(0.15)
    return {"task": "inventory_em", "written": ok, "failed": fail}


# ---------------- 7. ccidx_index 中证商品期货指数 ----------------

_CCIDX_SERIES = {
    "commodity": "中证商品期货指数",
    "price": "中证商品期货价格指数",
}
_CCIDX_REN = {
    "日期": "time",
    "指数代码": "index_code",
    "收盘点位": "close",
    "结算点位": "settle",
    "涨跌": "change",
    "涨跌幅": "pct_change",
    "openingPrice": "open",
    "highPrice": "high",
    "lowPrice": "low",
    "preSettlePrice": "pre_settle",
    "settlePriceNorm": "settle_norm",
    "closingPriceNorm": "close_norm",
    "preClosingPrice": "pre_close",
    "oneYearAnnualizedReturnPercentage": "annual_return_1y",
    "threeYearAnnualizedReturnPercentage": "annual_return_3y",
    "fiveYearAnnualizedReturnPercentage": "annual_return_5y",
    "tenYearAnnualizedReturnPercentage": "annual_return_10y",
    "oneMonthChangePercentage": "change_1m",
    "threeMonthChangePercentage": "change_3m",
    "sixMonthChangePercentage": "change_6m",
    "yearToDateChangePercentage": "ytd_change",
    "createTime": "create_time",
}
# 陷阱：中文列「涨跌/涨跌幅」与英文列 dailyIncreaseAndDecrease* 在源侧是同一
# 数据的两列孪生——若两者都映射到 change/pct_change 会产生重名列，
# df[col] 退化为 DataFrame 后在 to_numeric 处炸「arg must be a list...」。
# 只保留中文列映射，英文孪生列不映射（reindex 时自然丢弃）。
_CCIDX_NUM = (
    "open",
    "high",
    "low",
    "close",
    "settle",
    "change",
    "pct_change",
    "pre_settle",
    "settle_norm",
    "close_norm",
    "pre_close",
    "annual_return_1y",
    "annual_return_3y",
    "annual_return_5y",
    "annual_return_10y",
    "change_1m",
    "change_3m",
    "change_6m",
    "ytd_change",
)


def task_ccidx_index(days: int = 0) -> dict:
    """中证商品期货指数 / 价格指数（源为全历史返回，整文件覆盖写）。"""
    import akshare as ak

    out_dir = BASE_DIR / "ccidx_index"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for slug, name in _CCIDX_SERIES.items():
        try:
            df = _retry(lambda n=name: ak.futures_index_ccidx(symbol=n), tries=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("ccidx %s 失败: %s", name, str(exc)[:80])
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns=_CCIDX_REN).copy()
        df = df.loc[:, ~df.columns.duplicated()]  # 源侧孪生列防御（见 _CCIDX_NUM 注释）
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        for c in _CCIDX_NUM:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df["index_name"] = name
        df["index_slug"] = slug
        frames.append(df.dropna(subset=["time"]))
    if not frames:
        return {"task": "ccidx_index", "error": "全部指数抓取失败"}
    cols = set().union(*[f.columns for f in frames])
    out = pd.concat(frames, ignore_index=True).sort_values(["index_slug", "time"])
    order = [
        c
        for c in ("time", "index_slug", "index_name", "index_code", *_CCIDX_NUM)
        if c in cols
    ]
    out = _stamp(out).reindex(columns=order)
    out.to_parquet(out_dir / "data.parquet", index=False)
    return {"task": "ccidx_index", "rows": len(out)}


# ---------------- 8. fees_info 费率快照 ----------------

_FEES_REN = {
    "交易所": "exchange",
    "合约代码": "contract",
    "合约名称": "contract_name",
    "品种代码": "variety",
    "品种名称": "variety_name",
    "合约乘数": "multiplier",
    "最小跳动": "tick_size",
    "开仓费率": "open_fee_ratio",
    "开仓费用/手": "open_fee_per_lot",
    "平仓费率": "close_fee_ratio",
    "平仓费用/手": "close_fee_per_lot",
    "平今费率": "close_today_fee_ratio",
    "平今费用/手": "close_today_fee_per_lot",
    "做多保证金率": "margin_long_rate",
    "做多保证金/手": "margin_long_per_lot",
    "做空保证金率": "margin_short_rate",
    "做空保证金/手": "margin_short_per_lot",
    "上日结算价": "pre_settle",
    "上日收盘价": "pre_close",
    "最新价": "last_price",
    "成交量": "volume",
    "持仓量": "open_interest",
    "1手开仓费用": "fee_open_1lot",
    "1手平仓费用": "fee_close_1lot",
    "1手平今费用": "fee_close_today_1lot",
    "做多1手保证金": "margin_long_1lot",
    "做空1手保证金": "margin_short_1lot",
    "1手市值": "value_1lot",
    "更新时间": "updated_at",
}


def task_fees_info(days: int = 0) -> dict:
    """全合约费率表快照（乘数/开平今费/保证金/1手成本），整文件覆盖写。"""
    import akshare as ak

    out_dir = BASE_DIR / "fees_info"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = _retry(ak.futures_fees_info, tries=2)
    df = df.rename(columns=_FEES_REN).copy()
    keep = [c for c in _FEES_REN.values() if c in df.columns]
    for c in keep:
        if c in (
            "exchange",
            "contract",
            "contract_name",
            "variety",
            "variety_name",
            "updated_at",
        ):
            continue
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    out = _stamp(df).reindex(columns=keep)
    out.to_parquet(out_dir / "data.parquet", index=False)
    return {"task": "fees_info", "rows": len(out)}


# ---------------- 9. hog_chain 生猪产业链 ----------------

_HOG_FAMILIES: dict[str, list[str]] = {
    "core": ["外三元", "内三元", "土杂猪"],
    "cost": ["玉米", "豆粕", "二元母猪价格", "仔猪价格"],
    "supply": [
        "猪肉批发价",
        "储备冻猪肉",
        "饲料原料数据",
        "白条肉",
        "生猪产能",
        "育肥猪",
        "肉类价格指数",
        "猪粮比价",
    ],
}


def _parse_hog_time(series: pd.Series) -> pd.Series:
    """生猪产业链时间列三形态：date / 'YYYY'（年度）/ 'YYYY年第NN周'（周度）。

    陷阱：不能整体 `pd.to_datetime(s, errors="coerce")`——pandas 2+ 按**首元素**
    推断单一格式，"2012" 定成 "%Y" 后同列的 "2026-05-28" 会被判不匹配而静默变
    NaT。按形态掩码逐段显式解析。
    """
    s = series.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    d = s.str.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
    if d.any():
        out.loc[d] = pd.to_datetime(
            s[d].str.replace("/", "-", regex=False), errors="coerce"
        )
    y = s.str.fullmatch(r"\d{4}")
    if y.any():
        out.loc[y] = pd.to_datetime(s[y] + "-01-01", errors="coerce")
    wk = s.str.extract(r"^(\d{4})年第?(\d{1,2})周$")
    m = wk[0].notna()
    if m.any():
        iso = wk[0][m] + "-W" + wk[1][m].str.zfill(2) + "-1"
        out.loc[m] = pd.to_datetime(iso, format="%G-W%V-%u", errors="coerce")
    return out


def _norm_hog_frame(df: pd.DataFrame, family: str, sym: str) -> pd.DataFrame:
    """单个来源序列 → 长表（time/family/series/value）。

    源侧三种形态：
      - `date`+`value`（多数日频/周频序列）
      - `date`+其他单列（育肥猪 benzhou）
      - `周期`+多指标宽表（年度/周度：饲料原料数据/生猪产能/白条肉）——
        melt 后每指标独立成序列 `{sym}·{指标名}`，年度值落在年初 01-01。
    """
    df = df.copy()
    if "周期" in df.columns:
        df = df.rename(columns={"周期": "time"})
        metrics = [c for c in df.columns if c != "time"]
        df = df.melt(
            id_vars="time", value_vars=metrics, var_name="metric", value_name="value"
        )
        df["series"] = sym + "·" + df["metric"].astype(str)
        df = df.drop(columns=["metric"])
    elif "date" in df.columns:
        df = df.rename(columns={"date": "time"})
        rest = [c for c in df.columns if c != "time"]
        if rest != ["value"] and len(rest) >= 1:
            df = df.rename(columns={rest[0]: "value"})
        df["series"] = sym
    else:
        raise ValueError(f"未知时间列: {list(df.columns)[:4]}")
    df["time"] = _parse_hog_time(df["time"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["family"] = family
    return df.dropna(subset=["time", "value"])


def task_hog_chain(days: int = 0) -> dict:
    """生猪产业链（15 个来源序列；源为全历史返回，按族覆盖写长表）。"""
    import akshare as ak

    out_dir = BASE_DIR / "hog_chain"
    out_dir.mkdir(parents=True, exist_ok=True)
    fns = {
        "core": ak.futures_hog_core,
        "cost": ak.futures_hog_cost,
        "supply": ak.futures_hog_supply,
    }
    written = failed = 0
    for family, symbols in _HOG_FAMILIES.items():
        frames = []
        fn = fns[family]
        for s in symbols:
            try:
                df = _retry(lambda f=fn, ss=s: f(symbol=ss), tries=2)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if df is None or df.empty:
                failed += 1
                continue
            try:
                frames.append(_norm_hog_frame(df, family, s))
            except Exception as exc:  # noqa: BLE001
                log.warning("hog %s/%s 归一失败: %s", family, s, str(exc)[:80])
                failed += 1
            time.sleep(0.2)
        if frames:
            out = pd.concat(frames, ignore_index=True).sort_values(["series", "time"])
            out = _stamp(out).reindex(
                columns=[
                    "time",
                    "family",
                    "series",
                    "value",
                    "release_id",
                    "published_at",
                ]
            )
            out.to_parquet(out_dir / f"{family}.parquet", index=False)
            written += 1
    return {"task": "hog_chain", "families_written": written, "series_failed": failed}


# ---------------- 10. member_rank_sina 席位排名（SHFE/CZCE） ----------------

_SINA_SIDES = {"long": "多单持仓", "short": "空单持仓", "volume": "成交量"}


def _czce_contract_sina(contract: str) -> str:
    """CZCE 3 位年合约码（AP610）→ 新浪 4 位年（AP2610）；其余原样返回。"""
    m = re.fullmatch(r"([A-Za-z]{1,2})(\d{3})", contract)
    if m:
        return f"{m.group(1).upper()}2{m.group(2)}"
    return contract


def _variety_venue_map() -> dict[str, str]:
    """品种（不含尾 0 的主力代码，如 RB）→ 交易所（SHFE/CZCE）。

    FUTURES_META 键为 `RB0.CN` 形态（主力连续 + .CN 后缀），去掉 `0.CN` 即品种。
    """
    from backend.shared.futures_universe import FUTURES_META

    out: dict[str, str] = {}
    for code, meta in FUTURES_META.items():
        if code.endswith("0.CN") and meta.get("exchange") in ("SHFE", "CZCE"):
            out[code[:-4]] = meta["exchange"]
    return out


def task_member_rank_sina(days: int = 10) -> dict:
    """SHFE/CZCE 主力合约席位排名（多单/空单/成交量，前 10-20 席位）。

    主力合约来源 = spot_basis 当日 dominant_contract（须先跑 spot_basis）；
    DCE/CFFEX/INE 新浪无覆盖，由官方 member_positions 兜底（DCE/GFEX）。
    """
    import akshare as ak

    out_dir = BASE_DIR / "member_rank_sina"
    venue_of = _variety_venue_map()
    dates = _recent_trading_days(days)
    written = skipped = calls = 0
    for d in dates:
        spot_path = BASE_DIR / "spot_basis" / f"dt={d}" / "data.parquet"
        if not spot_path.exists():
            skipped += 1
            continue
        spot = pd.read_parquet(spot_path)
        frames = []
        for _, row in spot.iterrows():
            variety = str(row.get("symbol") or "").upper()
            dom = row.get("dominant_contract")
            venue = venue_of.get(variety)
            if venue is None or not isinstance(dom, str) or not dom:
                continue
            contract = dom if venue == "SHFE" else _czce_contract_sina(dom)
            for side, sina_symbol in _SINA_SIDES.items():
                try:
                    df = ak.futures_hold_pos_sina(
                        symbol=sina_symbol, contract=contract, date=d
                    )
                except Exception:  # noqa: BLE001
                    continue
                calls += 1
                if df is None or df.empty:
                    continue
                df = df.rename(
                    columns={
                        "名次": "rank",
                        "会员简称": "member",
                        "比上交易增减": "delta",
                    }
                )
                df = df.loc[:, ~df.columns.duplicated()]
                value_col = [
                    c for c in df.columns if c not in ("rank", "member", "delta")
                ]
                if not value_col:
                    continue
                df = df.rename(columns={value_col[0]: "size"})
                df["variety"] = variety
                df["venue"] = venue
                df["contract"] = contract.upper()
                df["side"] = side
                df["time"] = _dt_str(d)
                frames.append(df)
                time.sleep(0.15)
        if frames:
            cols = set().union(*[f.columns for f in frames])
            order = [
                c
                for c in (
                    "time",
                    "venue",
                    "contract",
                    "variety",
                    "side",
                    "rank",
                    "member",
                    "size",
                    "delta",
                )
                if c in cols
            ]
            out = pd.concat(frames, ignore_index=True).reindex(columns=order)
            _write_part(out_dir, d, _stamp(out))
            written += 1
    return {
        "task": "member_rank_sina",
        "days": len(dates),
        "written": written,
        "skipped_no_spot": skipped,
        "calls": calls,
    }


# ---------------- CLI ----------------

TASKS: dict[str, Callable[[int], dict]] = {
    "spot_basis": task_spot_basis,
    "settle_params": task_settle_params,
    "trade_rule": task_trade_rule,
    "contract_meta": task_contract_meta,
    "exchange_daily": task_exchange_daily,
    "inventory_em": task_inventory_em,
    "ccidx_index": task_ccidx_index,
    "fees_info": task_fees_info,
    "hog_chain": task_hog_chain,
    "member_rank_sina": task_member_rank_sina,
}

# 日频轻量全集（夜链/all 用）；member_rank_sina 请求量大，仅显式跑
LIGHT_DAILY = (
    "spot_basis",
    "settle_params",
    "trade_rule",
    "contract_meta",
    "exchange_daily",
    "inventory_em",
    "ccidx_index",
    "fees_info",
    "hog_chain",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="QuantFutures 国内扩展数据集（二批）")
    ap.add_argument("--task", required=True, choices=[*TASKS, "all"])
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    names = LIGHT_DAILY if args.task == "all" else (args.task,)
    results: dict[str, Any] = {}
    for name in names:
        try:
            results[name] = TASKS[name](args.days)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": str(exc)}
        log.info("%s → %s", name, results[name])
    print(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
