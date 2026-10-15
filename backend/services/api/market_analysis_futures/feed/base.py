"""期货市场分析 —— 数据层基座（唯一与 QuantFutures parquet 目录耦合的地方）。

所有域模块（overview/sectors/term/positions/inventory/spread）共用本文件的
读取、缓存、交易日与品种元数据工具。

数据口径（实测，2026-10-15）：
- `daily_forward` 为主力连续日K，symbol 三层命名：`RB0.CN`（国内主力连续）/
  `CL.FUT`（国际）/ `Au99.99`（上金所现货）
- `volume` 单位为**手**；`amount = close × volume` 是**无乘数的估算值（量纲无意义）**，
  不要当成交额用——名义成交额由本模块 `close × volume × multiplier` 现算
- `position`（持仓量）/`settlement`（动态结算价）自 2026-10-15 起落盘，
  历史由 `backend/scripts/quantfutures_backfill_position.py` 回填；
  国际品种与上金所源不提供 → NULL（见 futures_universe.symbol_meta.has_oi）
- **当日同步时序**：最新分区（=今天凌晨 03:00 同步）可能只有国际夜盘品种，
  「完整交易日」判定 = 国内（.CN）品种数 ≥ MIN_CN_SYMBOLS 的分区
- 文件内部自带 `dt` 列会**遮蔽** hive 分区列，按日期取数一律走显式分区
  文件列表（`_read_partitioned`），禁止对目录全 glob
"""

from __future__ import annotations

import logging
import re
from typing import Any
from collections.abc import Sequence

import duckdb
import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached, clear_cache
from backend.services.api.market_analysis_shared.display import safe_float
from backend.services.api.market_analysis_shared.market_days import (
    list_partition_dates,
    to_iso,
)
from backend.services.engine.data_platform.quantfutures_hub import (
    _resolve_quantfutures_data_dir,
)
from backend.shared.futures_universe import FUTURES_META, SECTORS, symbol_meta

logger = logging.getLogger(__name__)

# ---- 数据目录与分区相对路径 ----

DATA_DIR = _resolve_quantfutures_data_dir()

KLINE_REL = "1_kline_data/daily_forward"  # 主力连续日K（三层 symbol）
CONTRACTS_REL = "2_base_sector/contracts_daily"  # 分合约日K（期限结构唯一来源）
RECEIPTS_REL = "2_base_sector/warehouse_receipts"  # 交易所仓单（日分区）
CFTC_REL = "2_base_sector/cftc"  # CFTC 持仓宽表（周度）
FX_REL = "2_base_sector/fx_daily"  # 中行外汇牌价（100 外币兑人民币）
REALTIME_REL = "2_base_sector/futures_realtime"  # 实时快照
SPOT_BASIS_REL = "2_base_sector/spot_basis"  # 生意社现货价+基差（52 品种，日分区）
INVENTORY_EM_REL = "2_base_sector/inventory_em"  # 东财库存（59 品种平铺文件）
MEMBERS_REL = "2_base_sector/member_rank_sina"  # 席位排名（SHFE/CZCE，日分区）
CCIDX_REL = "2_base_sector/ccidx_index"  # 中证商品期货指数（单文件双序列）
EXD_REL = "2_base_sector/exchange_daily"  # 交易所官方全合约日行情（真实成交额）
CMETA_REL = "2_base_sector/contract_meta"  # 合约生命周期快照（日分区）

# ---- 口径常量 ----

# 「完整交易日」判定：国内主力品种数阈值（84 个国内品种的绝大多数）
MIN_CN_SYMBOLS = 60

# 合约代码解析：RB2601 / AU2610 / IF2512（大写字母 + 4 位年月）
CONTRACT_RE = r"^([A-Z]+)(\d{4})$"


# ---- 基础设施 ----


def clear_cache_futures() -> None:
    """清空期货市场分析缓存（外部刷新入口）。"""
    clear_cache()


def _num(v: Any, nd: int = 2) -> float | None:
    """数值出口：NaN/None → None（前端显示 —），否则四舍五入。绝不落 0 冒名。"""
    f = safe_float(v, float("nan"))
    if f != f:
        return None
    return round(f, nd)


def _yi(v: Any) -> float | None:
    """「亿」口径出口。与共享层 `fmt_yi` 的关键区别：缺失 → None。

    `fmt_yi(NaN)` 返回 0.0（三市场共享层的既有行为，为免回归不动它），
    期货模块一律用本函数，否则「源不提供」会被渲染成「成交额为零」。
    """
    f = safe_float(v, float("nan"))
    if f != f:
        return None
    return round(f / 1e8, 2)


def _rank_pct(s: pd.Series) -> pd.Series:
    """0-100 分位（rank(pct)×100，组内秩平均处理并列）。

    缺失保留 NaN——调用方出口再落 None（「—」），绝不把缺失当 0/末位。
    """
    v = pd.to_numeric(s, errors="coerce")
    r = v.rank(pct=True) * 100.0
    return r.where(v.notna())


def _q(sql: str) -> pd.DataFrame:
    """执行 DuckDB 只读查询（短连接，线程安全）。"""
    con = duckdb.connect()
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def _avail() -> bool:
    """数据目录与关键分区可用性。"""
    return (DATA_DIR / KLINE_REL).is_dir()


def _partition_file(rel_dir: str, ymd: str) -> str | None:
    """分区文件的绝对路径（不存在返回 None）。"""
    path = DATA_DIR / rel_dir / f"dt={ymd}" / "data.parquet"
    return str(path) if path.exists() else None


def _read_partitioned(
    rel_dir: str, ymd_days: Sequence[str], columns: str = "*"
) -> pd.DataFrame:
    """按显式分区文件列表读取（**唯一正确的按日期取数方式**）。

    `union_by_name=true` 不可省：position/settlement 回填过渡期新旧分区
    列集不一致，缺列需补 NULL 而不是报错。
    """
    files = [f for f in (_partition_file(rel_dir, d) for d in ymd_days) if f]
    if not files:
        return pd.DataFrame()
    literal = ",".join(repr(f) for f in files)
    return _q(f"SELECT {columns} FROM read_parquet([{literal}], union_by_name=true)")


def _trading_days(end: str | None, n: int) -> list[str]:
    """截至 end（YYYYMMDD 或 None=最新）的最近 n 个**分区日期**，降序，[0]=最新。"""
    dates = list_partition_dates(KLINE_REL, DATA_DIR)
    if end:
        dates = [d for d in dates if d <= end.replace("-", "")]
    return dates[-n:][::-1]


def _latest_trade_date() -> str | None:
    """最新分区日期（可能是只有国际品种的不完整分区）。"""
    d = _trading_days(None, 1)
    return d[0] if d else None


def _dedupe_bars(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """同 (symbol, dt) 可能有多条 release，保留最新发布行。"""
    if df.empty:
        return df
    if "published_at" in df.columns:
        return df.sort_values("published_at", ascending=False).drop_duplicates(
            keys, keep="first"
        )
    return df.drop_duplicates(keys, keep="first")


def _complete_partition(ymd: str) -> bool:
    """该分区是否收录了足够的国内品种（判定「完整交易日」）。"""
    df = _read_partitioned(KLINE_REL, [ymd], columns="symbol")
    if df.empty:
        return False
    syms = df["symbol"].astype(str)
    cn = syms[syms.str.endswith(".CN")]
    return cn.nunique() >= MIN_CN_SYMBOLS


def _full_trade_days(n: int, end: str | None = None) -> list[str]:
    """最近 n 个**完整交易日**（降序，[0]=最新）。

    当日凌晨 03:00 同步先落国际夜盘品种，当天分区午后才是全量——
    截面/榜单一律以「完整分区」为准，否则会把 2 个国际品种当全市场。
    """
    probe = n + 8

    def _load() -> list[str]:
        full: list[str] = []
        for d in _trading_days(end, probe):
            try:
                if _complete_partition(d):
                    full.append(d)
                    if len(full) >= n:
                        break
            except Exception:  # noqa: BLE001
                logger.warning("完整交易日探测失败: %s", d)
                continue
        return full

    return cached(f"futures:full_days:{n}:{end or 'latest'}", _load, ttl=300.0)


def _latest_full_trade_date() -> str | None:
    days = _full_trade_days(1)
    return days[0] if days else None


# ---- 截面与序列（跨域唯一实现） ----


_SNAP_COLS = "symbol, dt, close, volume, amount, position, settlement"


def _cross_section(end: str | None = None) -> tuple[str | None, pd.DataFrame]:
    """最新完整交易日的全品种截面（**跨域唯一实现**）。

    返回 (trade_date, DataFrame[symbol, close, volume, amount, position,
    settlement, prev_close, prev_position, pct_change, oi_change])。
    `pct_change` 用前一完整交易日收盘计算；`oi_change` = 持仓量日变化（手）。
    新上市/暂停品种无前收盘 → pct_change 为 NaN（前端显示 —，不落 0）。
    """

    def _load() -> tuple[str | None, pd.DataFrame]:
        days = _full_trade_days(2, end)
        if not days:
            return None, pd.DataFrame()
        cur = days[0]
        k = _read_partitioned(KLINE_REL, days, columns=_SNAP_COLS)
        if k.empty:
            return cur, pd.DataFrame()
        k = _dedupe_bars(k, ["symbol", "dt"])
        k["dt"] = k["dt"].astype(str)
        snap = k[k["dt"] == cur].drop(columns=["dt"]).copy()
        if len(days) > 1:
            prev = k[k["dt"] == days[1]][["symbol", "close", "position"]].rename(
                columns={"close": "prev_close", "position": "prev_position"}
            )
            snap = snap.merge(prev, on="symbol", how="left")
        else:
            snap["prev_close"] = float("nan")
            snap["prev_position"] = float("nan")
        valid = snap["prev_close"].notna() & (snap["prev_close"] > 0)
        snap["pct_change"] = ((snap["close"] / snap["prev_close"] - 1) * 100).where(
            valid
        )
        snap["oi_change"] = snap["position"] - snap["prev_position"]
        return cur, snap

    return cached(f"futures:cross_section:{end or 'latest'}", _load, ttl=300.0)


def _enrich(snap: pd.DataFrame) -> pd.DataFrame:
    """给截面附加元数据 + 名义成交额/持仓额（元）。

    - `turnover` = close × volume × multiplier（**名义口径**；国际/上金所
      无量仓数据 → NaN）
    - `oi_amount` = close × position × multiplier（名义持仓额，沉淀资金近似）
    缺失一律 NaN，绝不落 0。
    """
    if snap.empty:
        return snap
    out = snap.copy()

    def _col(name: str) -> pd.Series:
        if name in out.columns:
            return pd.to_numeric(out[name], errors="coerce")
        return pd.Series([float("nan")] * len(out), index=out.index)

    metas = out["symbol"].map(lambda s: symbol_meta(str(s)))
    out["name"] = metas.map(lambda m: m["name"])
    out["sector"] = metas.map(lambda m: m["sector"])
    out["exchange"] = metas.map(lambda m: m["exchange"])
    out["unit"] = metas.map(lambda m: m["unit"])
    mult = metas.map(lambda m: m["multiplier"]).astype("float64")
    close, volume, position = _col("close"), _col("volume"), _col("position")
    # 诚实空缺闸：源不提供量仓的品种（meta.has_volume/has_oi 为 False）里，
    # 落盘的 0 是「源空缺」而非「真零」——置回 NaN 显示 —。国内 0 是真零不动。
    has_vol = metas.map(lambda m: bool(m["has_volume"]))
    has_oi = metas.map(lambda m: bool(m["has_oi"]))
    volume = volume.mask((volume == 0) & (~has_vol))
    position = position.mask((position == 0) & (~has_oi))
    out["volume"] = volume
    out["position"] = position
    out["turnover"] = close * volume * mult
    out["oi_amount"] = close * position * mult
    return out


def _series(n_days: int, end: str | None = None) -> tuple[list[str], pd.DataFrame]:
    """最近 n 个完整交易日的品种序列（长表，供趋势/变化类计算）。"""

    def _load() -> tuple[list[str], pd.DataFrame]:
        days = _full_trade_days(n_days, end)
        if not days:
            return [], pd.DataFrame()
        k = _read_partitioned(
            KLINE_REL, days, columns="symbol, dt, close, position, volume"
        )
        if k.empty:
            return days, pd.DataFrame()
        k = _dedupe_bars(k, ["symbol", "dt"])
        k["dt"] = k["dt"].astype(str)
        return days, k

    return cached(f"futures:series:{n_days}:{end or 'latest'}", _load, ttl=300.0)


# ---- 分合约（期限结构） ----


def _contracts() -> pd.DataFrame:
    """contracts_daily 全量（约 300-1500 个小文件），解析出 stem/ym。

    列：symbol/时间/OHLC/volume/amount/settlement/open_interest + stem/ym。
    新旧文件列集不一（2026-08 旧版缺 settlement/open_interest）→ union_by_name。
    """

    def _load() -> pd.DataFrame:
        path = DATA_DIR / CONTRACTS_REL
        if not path.is_dir():
            return pd.DataFrame()
        files = sorted(path.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        literal = ",".join(repr(str(f)) for f in files)
        df = _q(f"SELECT * FROM read_parquet([{literal}], union_by_name=true)")
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"])
        code = df["symbol"].astype(str).str.replace(r"\.CN$", "", regex=True)
        ext = code.str.extract(CONTRACT_RE)
        df["stem"] = ext[0]
        df["ym"] = ext[1]
        return df.dropna(subset=["stem", "ym"])

    return cached("futures:contracts", _load, ttl=1800.0)


# ---- 仓单 ----


def _receipts() -> pd.DataFrame:
    """warehouse_receipts 全分区（小表），附 `dt`（由文件名提取，不依赖文件内列）。

    覆盖现状（2026-10-15 实测）：数据止于 20260910 且最新日期仅 CZCE
    （上期所接口失效）——页面必须如实标注覆盖所，**不得把缺数据渲染成 0**。
    """

    def _load() -> pd.DataFrame:
        parts = sorted((DATA_DIR / RECEIPTS_REL).glob("dt=*/data.parquet"))
        if not parts:
            return pd.DataFrame()
        literal = ",".join(repr(str(f)) for f in parts)
        df = _q(
            "SELECT * FROM read_parquet("
            f"[{literal}], union_by_name=true, filename=true)"
        )
        if df.empty:
            return df
        df["dt"] = df["filename"].str.extract(r"dt=(\d{8})")
        df["receipt_qty"] = pd.to_numeric(df["receipt_qty"], errors="coerce")
        df["variety"] = df["variety"].astype(str)
        return df.dropna(subset=["dt"])

    return cached("futures:receipts", _load, ttl=1800.0)


# ---- CFTC 持仓 ----


def _cftc_long(kind: str) -> pd.DataFrame:
    """CFTC 宽表 → 长表 [date, item, long, short, net]（周度，约 12 品种）。

    kind: `c`（非商业/投机仓，macro_usa_cftc_c_holding）|
    `merchant_goods`（商业/套保仓）。
    """

    def _load() -> pd.DataFrame:
        p = DATA_DIR / CFTC_REL / f"cftc_{kind}.parquet"
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_parquet(p)
        if "日期" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"]).sort_values("date")
        items = sorted(
            {str(c).split("-")[0] for c in df.columns if str(c).endswith("-净仓位")}
        )
        parts = []
        for item in items:
            lc, sc, nc = f"{item}-多头仓位", f"{item}-空头仓位", f"{item}-净仓位"
            parts.append(
                pd.DataFrame(
                    {
                        "date": df["date"],
                        "item": item,
                        "long": pd.to_numeric(df.get(lc), errors="coerce"),
                        "short": pd.to_numeric(df.get(sc), errors="coerce"),
                        "net": pd.to_numeric(df[nc], errors="coerce"),
                    }
                )
            )
        out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        return out.dropna(subset=["net"])

    return cached(f"futures:cftc:{kind}", _load, ttl=1800.0)


# ---- 外汇 ----


def _fx_rate(code: str = "USD") -> dict[str, Any] | None:
    """最新中行牌价：{"date", "rate"}，rate = 每 1 外币的人民币折算价。"""

    def _load() -> dict[str, Any] | None:
        p = DATA_DIR / FX_REL / f"{code}.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        df = df.dropna(subset=["time"]).sort_values("time")
        if df.empty:
            return None
        last = df.iloc[-1]
        mid = last.get("mid") if "mid" in df.columns else None
        if mid is None or pd.isna(mid):
            mid = last.get("pbc_mid") if "pbc_mid" in df.columns else None
        if mid is None or pd.isna(mid) or float(mid) <= 0:
            return None
        return {
            "date": str(pd.to_datetime(last["time"]).date()),
            "rate": round(float(mid) / 100.0, 6),  # quote_unit=100外币
        }

    return cached(f"futures:fx:{code}", _load, ttl=1800.0)


def _fx_series(code: str = "USD") -> pd.DataFrame:
    """中行牌价全量 → DataFrame[dt(YYYYMMDD), rate(每 1 外币人民币)]。

    与 K 线分区日期（YYYYMMDD 字符串）对齐，供逐日换算内外盘价差。
    """

    def _load() -> pd.DataFrame:
        p = DATA_DIR / FX_REL / f"{code}.parquet"
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_parquet(p)
        df = df.dropna(subset=["time"])
        mid = df["mid"] if "mid" in df.columns else None
        if mid is None or mid.notna().sum() == 0:
            mid = df["pbc_mid"] if "pbc_mid" in df.columns else None
        if mid is None:
            return pd.DataFrame()
        out = pd.DataFrame(
            {
                "dt": pd.to_datetime(df["time"], errors="coerce").dt.strftime("%Y%m%d"),
                "rate": pd.to_numeric(mid, errors="coerce") / 100.0,
            }
        ).dropna()
        out = out[out["rate"] > 0]
        return out.sort_values("dt").drop_duplicates("dt", keep="last")

    return cached(f"futures:fx_series:{code}", _load, ttl=1800.0)


# ---- 现货基差（生意社） ----


def _spot_basis(n_days: int = 500) -> pd.DataFrame:
    """spot_basis 近 n 个分区（历史基差，供分位与单品种历史）。

    列：dt(YYYYMMDD) + symbol(品种 stem 大写) + spot_price / near_contract /
    near_contract_price / dominant_contract / dominant_contract_price /
    near_basis(_rate) / dom_basis(_rate)。rate 为**小数**（0.02 = 2%）。
    """

    def _load() -> pd.DataFrame:
        parts = sorted((DATA_DIR / SPOT_BASIS_REL).glob("dt=*/data.parquet"))[-n_days:]
        if not parts:
            return pd.DataFrame()
        literal = ",".join(repr(str(f)) for f in parts)
        df = _q(
            f"SELECT * FROM read_parquet([{literal}], union_by_name=true, filename=true)"
        )
        if df.empty:
            return df
        df["dt"] = df["filename"].str.extract(r"dt=(\d{8})")
        df["symbol"] = df["symbol"].astype(str).str.upper()
        for c in (
            "spot_price",
            "near_contract_price",
            "dominant_contract_price",
            "near_basis",
            "dom_basis",
            "near_basis_rate",
            "dom_basis_rate",
        ):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["dt"]).sort_values(["symbol", "dt"])

    return cached(f"futures:spot_basis:{n_days}", _load, ttl=1800.0)


# ---- 东财库存 ----


def _inventory_em() -> pd.DataFrame:
    """东财库存（每品种一个平铺文件，各自滚动窗口）。

    列 [symbol, dt(YYYYMMDD), inventory, delta]。**每品种 as-of 不同**：
    个别品种源侧停更（实测 AP 止于 2026-05-29、FU 止于 2025-12-31），
    页面必须按品种标注最新日期，不得用全局最新日冒充。
    库存为东财口径（单位随品种），只做相对分位与变化，不做跨品种绝对值比较。
    """

    def _load() -> pd.DataFrame:
        files = sorted((DATA_DIR / INVENTORY_EM_REL).glob("*.parquet"))
        frames = []
        for f in files:
            try:
                d = pd.read_parquet(f, columns=["time", "symbol", "inventory", "delta"])
            except Exception:  # noqa: BLE001
                logger.warning("inventory_em 读取失败: %s", f.name)
                continue
            frames.append(d)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df["dt"] = pd.to_datetime(df["time"], errors="coerce").dt.strftime("%Y%m%d")
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["inventory"] = pd.to_numeric(df["inventory"], errors="coerce")
        df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
        return df.dropna(subset=["dt"]).sort_values(["symbol", "dt"])

    return cached("futures:inventory_em", _load, ttl=1800.0)


# ---- 席位排名（新浪，SHFE/CZCE） ----


def _member_rank() -> pd.DataFrame:
    """新浪席位排名全分区（主力合约 long/short/volume 三榜，前 10-20 席位）。

    列：dt + time/venue/contract/variety/side/rank/member/size/delta。
    size 单位=**手**（long/short 为持仓量、volume 为成交量）；CZCE 合约码
    在摄取侧已归一 4 位年（AP2610）。覆盖仅 SHFE/CZCE（DCE/CFFEX/INE 无源）。
    """

    def _load() -> pd.DataFrame:
        parts = sorted((DATA_DIR / MEMBERS_REL).glob("dt=*/data.parquet"))
        if not parts:
            return pd.DataFrame()
        literal = ",".join(repr(str(f)) for f in parts)
        df = _q(
            f"SELECT * FROM read_parquet([{literal}], union_by_name=true, filename=true)"
        )
        if df.empty:
            return df
        df["dt"] = df["filename"].str.extract(r"dt=(\d{8})")
        df["variety"] = df["variety"].astype(str).str.upper()
        df["side"] = df["side"].astype(str).str.lower()
        for c in ("size", "delta", "rank"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["dt"])

    return cached("futures:member_rank", _load, ttl=1800.0)


# ---- 中证商品指数 ----


def _ccidx(slug: str = "commodity") -> pd.DataFrame:
    """中证商品期货指数序列（单文件；slug=commodity|price）。

    自带官方口径区间收益：change_1m/3m/6m/ytd_change（%）与
    annual_return_1y/3y/5y/10y（%）。序列自 2022-10-17 起。
    """

    def _load() -> pd.DataFrame:
        p = DATA_DIR / CCIDX_REL / "data.parquet"
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_parquet(p)
        if "index_slug" not in df.columns:
            return pd.DataFrame()
        df = df[df["index_slug"] == slug].copy()
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        for c in (
            "close",
            "settle",
            "change",
            "pct_change",
            "change_1m",
            "change_3m",
            "change_6m",
            "ytd_change",
            "annual_return_1y",
            "annual_return_3y",
            "annual_return_5y",
            "annual_return_10y",
        ):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["time"]).sort_values("time")

    return cached(f"futures:ccidx:{slug}", _load, ttl=1800.0)


# ---- 交易所官方成交额 ----


def _exchange_turnover() -> dict[str, Any] | None:
    """交易所官方最新**完整日**成交额（真实口径）。

    完整日 = SHFE/CZCE/CFFEX/INE 四所齐备的最近分区。turnover 源侧单位为
    **万元**（实测四所 ratio≈1e-4，与 close×volume×乘数 一致），此处换算为
    亿元。DCE/GFEX 源侧无接口 → 恒缺，如实列出 `venues_missing`。
    """

    def _load() -> dict[str, Any] | None:
        d = DATA_DIR / EXD_REL
        days = sorted(p.name[3:] for p in d.glob("dt=*") if p.is_dir())
        for ymd in reversed(days[-10:]):
            f = d / f"dt={ymd}" / "data.parquet"
            if not f.exists():
                continue
            try:
                df = pd.read_parquet(f, columns=["venue", "turnover"])
            except Exception:  # noqa: BLE001
                logger.warning("exchange_daily 读取失败: %s", ymd)
                continue
            venues = set(df["venue"].dropna().astype(str))
            if not {"SHFE", "CZCE", "CFFEX", "INE"} <= venues:
                continue
            t = pd.to_numeric(df["turnover"], errors="coerce")
            by = (
                df.assign(_t=t).groupby("venue")["_t"].sum(min_count=1)
                * 1e4  # 万元 → 元
            )
            total_yuan = float(t.sum(min_count=1)) * 1e4
            return {
                "date": to_iso(ymd),
                "total_yi": round(total_yuan / 1e8, 1),
                "by_venue_yi": {
                    str(v): round(float(x) / 1e8, 1) for v, x in by.items()
                },
                "row_count": int(len(df)),
                "venues_missing": ["DCE", "GFEX"],
            }
        return None

    return cached("futures:exchange_turnover", _load, ttl=1800.0)


# ---- 合约生命周期 ----


def _norm_meta_symbol(sym: str) -> str:
    """contract_meta 合约码归一：CZCE 3 位年（AP610 → AP2610），其余大写。"""
    s = str(sym).strip().upper()
    m = re.fullmatch(r"([A-Z]+)(\d{3})", s)
    return f"{m.group(1)}2{m.group(2)}" if m else s


def _contract_expiry() -> pd.DataFrame:
    """最新 contract_meta 快照：合约 → 到期参考日。

    列 [symbol(大写 4 位年), venue, expiry(YYYY-MM-DD), source]。
    expiry = last_trade_date（CFFEX/CZCE 有）→ expire_date（SHFE/INE 仅有
    后者）；DCE/GFEX 无 meta → 不在表内（调用方走月差估算并标注 estimated）。
    """

    def _load() -> pd.DataFrame:
        parts = sorted((DATA_DIR / CMETA_REL).glob("dt=*/data.parquet"))
        if not parts:
            return pd.DataFrame()
        try:
            df = pd.read_parquet(parts[-1])
        except Exception:  # noqa: BLE001
            logger.warning("contract_meta 读取失败: %s", parts[-1].name)
            return pd.DataFrame()
        if "symbol" not in df.columns:
            return pd.DataFrame()
        out = pd.DataFrame(index=df.index)
        out["symbol"] = df["symbol"].astype(str).map(_norm_meta_symbol)
        out["venue"] = (
            df["venue"].astype(str)
            if "venue" in df.columns
            else pd.Series("", df.index)
        )
        ltd = pd.to_datetime(df.get("last_trade_date"), errors="coerce")
        exp = pd.to_datetime(df.get("expire_date"), errors="coerce")
        pick = ltd.where(ltd.notna(), exp)
        src = pd.Series("last_trade_date", index=df.index).where(
            ltd.notna(), pd.Series("expire_date", index=df.index).where(exp.notna())
        )
        out["expiry"] = pick.dt.strftime("%Y-%m-%d")
        out["source"] = src
        return out.dropna(subset=["symbol"]).drop_duplicates("symbol", keep="first")

    return cached("futures:contract_expiry", _load, ttl=1800.0)


# ---- 数据质检（/status 与总览质检条共用） ----

# 新鲜度阈值（自然日）：fresh ≤ _FRESH_DAYS；≤ _STALE_DAYS 为 stale；否则 unavailable
_FRESH_DAYS = 4  # 覆盖周末与单日源侧延迟
_STALE_DAYS = 14


def _freshness(latest_iso: str | None, fresh_days: int = _FRESH_DAYS) -> str:
    """最新日期 → fresh/stale/unavailable（无数据 = unavailable）。"""
    if not latest_iso:
        return "unavailable"
    try:
        d = pd.Timestamp(latest_iso)
    except Exception:  # noqa: BLE001
        return "unavailable"
    age = (pd.Timestamp.now().normalize() - d.normalize()).days
    if age <= fresh_days:
        return "fresh"
    return "stale" if age <= _STALE_DAYS else "unavailable"


def _datasets_quality() -> list[dict[str, Any]]:
    """十项数据集的「最新日期 / 覆盖 / 新鲜度」质检行（页面质检条唯一数据源）。"""
    rows: list[dict[str, Any]] = []

    def _add(
        key: str,
        name: str,
        latest: str | None,
        coverage: str,
        fresh_days: int = _FRESH_DAYS,
        note: str | None = None,
    ) -> None:
        rows.append(
            {
                "key": key,
                "name": name,
                "latest": latest,
                "coverage": coverage,
                "freshness": _freshness(latest, fresh_days),
                "note": note,
            }
        )

    if not _avail():
        return rows

    # 行情主源
    kd = _latest_trade_date()
    _add("kline", "主力连续日K", to_iso(kd) if kd else None, "国内 84 品种主力连续")
    # 分合约
    c = _contracts()
    _add(
        "contracts",
        "分合约日K",
        c["time"].max().strftime("%Y-%m-%d") if not c.empty else None,
        f"{int(c['stem'].nunique())} 品种" if not c.empty else "0 品种",
    )
    # 官方成交额
    try:
        ex = _exchange_turnover()
    except Exception:  # noqa: BLE001
        ex = None
    _add(
        "exchange_daily",
        "交易所官方日行情",
        ex["date"] if ex else None,
        "SHFE/CZCE/CFFEX/INE（DCE/GFEX 无源）",
        note="真实成交额来源；常规约 640 合约/日",
    )
    # 现货基差
    try:
        sb = _spot_basis(10)
    except Exception:  # noqa: BLE001
        sb = pd.DataFrame()
    _add(
        "spot_basis",
        "现货基差（生意社）",
        to_iso(str(sb["dt"].max())) if not sb.empty else None,
        f"{int(sb['symbol'].nunique())} 品种/日" if not sb.empty else "0 品种",
    )
    # 东财库存
    try:
        ie = _inventory_em()
    except Exception:  # noqa: BLE001
        ie = pd.DataFrame()
    if not ie.empty:
        latest_ie = str(ie["dt"].max())
        fresh_n = int(
            ie.groupby("symbol")["dt"]
            .max()
            .ge((pd.Timestamp(latest_ie) - pd.Timedelta(days=7)).strftime("%Y%m%d"))
            .sum()
        )
        _add(
            "inventory_em",
            "东财库存",
            to_iso(latest_ie),
            f"{int(ie['symbol'].nunique())} 品种（{fresh_n} 个跟随最新）",
        )
    else:
        _add("inventory_em", "东财库存", None, "0 品种")
    # 仓单
    r = _receipts()
    if not r.empty:
        rl = str(r["dt"].max())
        rv = sorted(r.loc[r["dt"] == rl, "venue"].dropna().astype(str).unique())
        _add("receipts", "交易所仓单", to_iso(rl), "、".join(rv) or "无")
    else:
        _add("receipts", "交易所仓单", None, "无")
    # 席位
    try:
        mb = _member_rank()
    except Exception:  # noqa: BLE001
        mb = pd.DataFrame()
    if not mb.empty:
        ml = str(mb["dt"].max())
        _add(
            "members",
            "席位排名（新浪）",
            to_iso(ml),
            f"{int(mb.loc[mb['dt'] == ml, 'variety'].nunique())} 品种（SHFE/CZCE）",
        )
    else:
        _add("members", "席位排名（新浪）", None, "无")
    # CFTC
    cf = _cftc_long("c")
    _add(
        "cftc",
        "CFTC 持仓",
        cf["date"].max() if not cf.empty else None,
        f"{int(cf['item'].nunique())} 品种" if not cf.empty else "0 品种",
        fresh_days=10,  # 周度数据
    )
    # 中证商品指数
    ci = _ccidx("commodity")
    _add(
        "ccidx",
        "中证商品期货指数",
        ci["time"].max().strftime("%Y-%m-%d") if not ci.empty else None,
        "commodity + price 双序列",
    )
    # 合约生命周期
    cm = _contract_expiry()
    meta_dirs = sorted((DATA_DIR / CMETA_REL).glob("dt=*"))
    _add(
        "contract_meta",
        "合约生命周期",
        to_iso(meta_dirs[-1].name[3:]) if meta_dirs else None,
        f"{len(cm)} 合约（SHFE/CZCE/CFFEX/INE）" if not cm.empty else "无",
    )
    return rows


# ---- 诊断 ----


def feed_status() -> dict[str, Any]:
    """数据可用性与各数据集最新日期（前端诊断 & 面板日期标注）。"""
    avail = _avail()
    contracts = _contracts() if avail else pd.DataFrame()
    receipts = _receipts() if avail else pd.DataFrame()
    cftc = _cftc_long("c") if avail else pd.DataFrame()
    fx = _fx_rate("USD") if avail else None
    receipts_latest = receipts["dt"].max() if not receipts.empty else None
    receipts_venues = (
        sorted(
            receipts.loc[receipts["dt"] == receipts_latest, "venue"]
            .dropna()
            .unique()
            .tolist()
        )
        if receipts_latest
        else []
    )
    return {
        "available": avail,
        "data_dir": str(DATA_DIR),
        "kline_latest": _latest_trade_date() if avail else None,
        "kline_latest_full": _latest_full_trade_date() if avail else None,
        "contracts_latest": (
            contracts["time"].max().strftime("%Y-%m-%d")
            if not contracts.empty
            else None
        ),
        "contracts_varieties": (
            int(contracts["stem"].nunique()) if not contracts.empty else 0
        ),
        "receipts_latest": to_iso(receipts_latest) if receipts_latest else None,
        "receipts_venues": receipts_venues,
        "cftc_latest": cftc["date"].max() if not cftc.empty else None,
        "fx_latest": fx["date"] if fx else None,
        "universe_size": len(FUTURES_META),
        "sectors": SECTORS,
        # 十项数据集质检（前端质检条唯一数据源）
        "datasets": _datasets_quality(),
        # 页面需向用户明示的口径与覆盖限制
        "notes": {
            "price": "价格为品种报价口径（元/吨、元/克等），主力连续含换月跳空",
            "volume_position": (
                "持仓量仅国内品种有；国际品种多数源不提供量仓（显示 —，"
                "个别源有成交量的品种照实展示），上金所仅价格无任何量仓"
            ),
            "turnover": "成交额/持仓额为名义口径（价格×手数×合约乘数），非真实资金流水",
            "receipts": "仓单数据可能滞后且覆盖所不全（以最新日期实际收录的交易所为准）",
            "cftc": "CFTC 持仓为周度数据（每周六发布上一周二数据）",
        },
    }


__all__ = [
    "DATA_DIR",
    "KLINE_REL",
    "CONTRACTS_REL",
    "RECEIPTS_REL",
    "CFTC_REL",
    "FX_REL",
    "REALTIME_REL",
    "SPOT_BASIS_REL",
    "INVENTORY_EM_REL",
    "MEMBERS_REL",
    "CCIDX_REL",
    "EXD_REL",
    "CMETA_REL",
    "MIN_CN_SYMBOLS",
    "clear_cache_futures",
    "feed_status",
    "_num",
    "_yi",
    "_rank_pct",
    "_q",
    "_avail",
    "_partition_file",
    "_read_partitioned",
    "_trading_days",
    "_latest_trade_date",
    "_full_trade_days",
    "_latest_full_trade_date",
    "_cross_section",
    "_enrich",
    "_series",
    "_dedupe_bars",
    "_contracts",
    "_receipts",
    "_cftc_long",
    "_fx_rate",
    "_fx_series",
    "_spot_basis",
    "_inventory_em",
    "_member_rank",
    "_ccidx",
    "_exchange_turnover",
    "_contract_expiry",
    "_norm_meta_symbol",
    "_datasets_quality",
    "to_iso",
]
