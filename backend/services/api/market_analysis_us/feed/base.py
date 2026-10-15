"""美股市场分析 —— 数据层基座（唯一与 parquet 目录耦合的地方）。

所有域模块（indices/breadth/sectors/earnings/analysts/holdings/valuation）
共用本文件的读取、缓存、交易日与名称/行业映射工具。换数据源只需改这里。

数据口径（实测，2026-09-12 复核）：
- `daily_forward` 是 **yfinance auto_adjust=False 的原始价（未复权）**，
  `volume` 为股数，**`amount` 为美元原始成交额**（无换算系数，与 A 股「股/万元」不同）
- 文件内部自带 `dt`(BIGINT) 列，会**遮蔽** hive 分区列 `dt`，因此按日期取数
  一律走显式分区文件列表（`_read_kline`），禁止对 `data/**/*.parquet` 全 glob
  （实测 250 日窗口：全 glob 1.73s vs 显式列表 0.05s，且全 glob 随历史增长退化）
- 标的池 517 只（标普500 + 纳指补充），非全市场；行业分类 516 只
"""

from __future__ import annotations

import logging
from typing import Any
from functools import lru_cache
from collections.abc import Iterable, Sequence

import duckdb
import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached, clear_cache
from backend.services.api.market_analysis_shared.market_days import (
    list_partition_dates,
    partition_dates_to_sql,
    to_iso,
    trading_days_until,
)
from backend.services.api.market_analysis_shared.names import build_name_map
from backend.services.engine.data_platform.quantus_hub import _resolve_quantus_data_dir

logger = logging.getLogger(__name__)

# ---- 数据目录与分区相对路径 ----

DATA_DIR = _resolve_quantus_data_dir()

KLINE_REL = "1_kline_data/daily_forward"  # 全市场日线（原始价、美元成交额）
INDEX_REL = "1_kline_data/index_daily"  # 指数日线
VALUATION_REL = "5_technical_derived/valuation"  # 估值快照（日分区）
L1_REL = "6_ml_datasets/l1_factors"  # L1 量价因子（日分区）

SECTOR_GLOB = "2_base_sector/sector/*.parquet"  # GICS 行业（每股一文件，快照）
F10_GLOB = "2_base_sector/f10/*.parquet"  # 基本面快照（市值/PE/PB/股息/52周高低）
SECURITY_MASTER_REL = "2_base_sector/security_master/data.parquet"  # 标的池 + 中文名

ANALYST_REL = "4_analyst"  # 分析师 12 张子表（每股一文件）
DIVIDEND_GLOB = "3_financial_data/dividend/*.parquet"  # 分红事件
SPLITS_GLOB = "3_financial_data/splits/*.parquet"  # 拆股事件

# ---- 口径常量 ----

# 美股无涨跌停（有 LULD 熔断）：±5% 只作「异动」统计口径，含义与 A 股涨停不同
BIG_MOVE_THRESHOLD = 5.0

# 单日涨跌幅裁剪上限：拆股/大额分红造成纯价格跳变，裁剪防其污染行业均值与龙头榜
PCT_CLIP = 100.0

# 52 周 / 均线窗口（交易日数）
WINDOW_52W = 250
WINDOW_MA50 = 50
WINDOW_MA200 = 200

# 核心指数（index_daily 实际存在的 5 个；**无 VIX、无 ETF**）
INDEX_OVERVIEW: list[dict[str, str]] = [
    {"symbol": "SPX.US", "name": "标普500"},
    {"symbol": "NDX.US", "name": "纳斯达克100"},
    {"symbol": "IXIC.US", "name": "纳斯达克"},
    {"symbol": "DJI.US", "name": "道琼斯"},
    {"symbol": "SOX.US", "name": "费城半导体"},
]

# GICS 11 大类中文名（sector 列的实际取值来自 yahoo，含空值）
SECTOR_CN: dict[str, str] = {
    "Technology": "信息技术",
    "Industrials": "工业",
    "Consumer Cyclical": "可选消费",
    "Financial Services": "金融",
    "Healthcare": "医疗保健",
    "Consumer Defensive": "日常消费",
    "Utilities": "公用事业",
    "Real Estate": "房地产",
    "Communication Services": "通信服务",
    "Basic Materials": "基础材料",
    "Energy": "能源",
    "Unknown": "未分类",
}

# 细分行业（yahoo industry 口径）常用词条中译 —— 下钻 tooltip 与个股画像展示用。
# 查不到的中译回落英文原文（不硬译），新词条随行业表更新迭代。
INDUSTRY_CN: dict[str, str] = {
    # 信息技术
    "Consumer Electronics": "消费电子",
    "Software - Infrastructure": "基础软件",
    "Software - Application": "应用软件",
    "Semiconductors": "半导体",
    "Semiconductor Equipment & Materials": "半导体设备与材料",
    "Computer Hardware": "计算机硬件",
    "Information Technology Services": "信息技术服务",
    "Communication Equipment": "通信设备",
    "Electronic Components": "电子元件",
    "Scientific & Technical Instruments": "科学仪器",
    "Solar": "光伏",
    "Electronics & Computer Distribution": "电子与计算机分销",
    "Computer Distribution": "计算机分销",
    "Data Storage": "数据存储",
    # 金融
    "Banks - Diversified": "多元化银行",
    "Banks - Regional": "区域银行",
    "Credit Services": "信贷服务",
    "Asset Management": "资产管理",
    "Capital Markets": "资本市场",
    "Financial Data & Stock Exchanges": "金融数据与交易所",
    "Insurance - Diversified": "多元化保险",
    "Insurance - Life": "寿险",
    "Insurance - Property & Casualty": "财产与意外险",
    "Insurance - Reinsurance": "再保险",
    "Insurance - Specialty": "专业保险",
    "Insurance Brokers": "保险经纪",
    "Mortgage Finance": "抵押金融",
    "Financial Conglomerates": "金融集团",
    "Shell Companies": "壳公司",
    # 医疗保健
    "Drug Manufacturers - General": "通用制药",
    "Drug Manufacturers - Specialty & Generic": "特色与仿制药",
    "Biotechnology": "生物技术",
    "Medical Devices": "医疗器械",
    "Medical Instruments & Supplies": "医疗仪器与耗材",
    "Diagnostics & Research": "诊断与研究服务",
    "Healthcare Plans": "健康保险计划",
    "Medical Care Facilities": "医疗服务设施",
    "Health Information Services": "健康信息服务",
    "Pharmaceutical Retailers": "医药零售",
    "Medical Distribution": "医疗分销",
    # 可选消费
    "Auto Manufacturers": "汽车制造",
    "Auto Parts": "汽车零部件",
    "Auto & Truck Dealerships": "汽车经销商",
    "Recreational Vehicles": "房车",
    "Home Improvement Retail": "家居建材零售",
    "Specialty Retail": "专业零售",
    "Internet Retail": "互联网零售",
    "Apparel Retail": "服装零售",
    "Department Stores": "百货商店",
    "Discount Stores": "折扣商店",
    "Restaurants": "餐饮",
    "Lodging": "酒店住宿",
    "Resorts & Casinos": "度假村与赌场",
    "Travel Services": "旅游服务",
    "Leisure": "休闲",
    "Gambling": "博彩",
    "Textile Manufacturing": "纺织制造",
    "Apparel Manufacturing": "服装制造",
    "Footwear & Accessories": "鞋类与配饰",
    "Luxury Goods": "奢侈品",
    "Furnishings, Fixtures & Appliances": "家具与家电",
    "Residential Construction": "住宅建设",
    "Building Products & Equipment": "建筑产品与设备",
    "Packaging & Containers": "包装与容器",
    "Personal Services": "个人服务",
    "Specialty Business Services": "专业商务服务",
    "Staffing & Employment Services": "人力资源服务",
    "Education & Training Services": "教育培训",
    "Advertising Agencies": "广告代理",
    # 日常消费
    "Food Distribution": "食品分销",
    "Grocery Stores": "食品杂货店",
    "Packaged Foods": "包装食品",
    "Confectioners": "糖果",
    "Beverages - Non-Alcoholic": "非酒精饮料",
    "Beverages - Brewers": "啤酒",
    "Beverages - Wineries & Distilleries": "葡萄酒与烈酒",
    "Tobacco": "烟草",
    "Household & Personal Products": "家庭与个人用品",
    "Farm Products": "农产品",
    "Agricultural Inputs": "农业投入品",
    # 工业
    "Aerospace & Defense": "航空航天与国防",
    "Airlines": "航空",
    "Airports & Air Services": "机场与航空服务",
    "Railroads": "铁路",
    "Trucking": "公路货运",
    "Marine Shipping": "海运",
    "Integrated Freight & Logistics": "综合货运物流",
    "Engineering & Construction": "工程与建筑",
    "Industrial Distribution": "工业分销",
    "Machinery": "机械",
    "Farm & Heavy Construction Machinery": "农业与重型工程机械",
    "Specialty Industrial Machinery": "专业工业机械",
    "Tools & Accessories": "工具与配件",
    "Metal Fabrication": "金属加工",
    "Electrical Equipment & Parts": "电气设备与部件",
    "Pollution & Treatment Controls": "污染治理",
    "Waste Management": "废物管理",
    "Security & Protection Services": "安防服务",
    "Consulting Services": "咨询服务",
    "Rental & Leasing Services": "租赁服务",
    "Business Equipment & Supplies": "商用设备与耗材",
    "Conglomerates": "综合企业",
    # 能源
    "Oil & Gas Integrated": "综合油气",
    "Oil & Gas E&P": "油气勘探与生产",
    "Oil & Gas Midstream": "油气中游",
    "Oil & Gas Refining & Marketing": "炼油与销售",
    "Oil & Gas Drilling": "油气钻井",
    "Oil & Gas Equipment & Services": "油气设备与服务",
    "Uranium": "铀",
    "Thermal Coal": "动力煤",
    # 公用事业
    "Utilities - Regulated Electric": "受监管电力",
    "Utilities - Regulated Gas": "受监管燃气",
    "Utilities - Regulated Water": "受监管水务",
    "Utilities - Diversified": "多元化公用事业",
    "Utilities - Independent Power Producers": "独立发电",
    "Utilities - Renewable": "可再生能源",
    # 房地产
    "REIT - Diversified": "多元化 REIT",
    "REIT - Residential": "住宅 REIT",
    "REIT - Retail": "零售 REIT",
    "REIT - Office": "办公 REIT",
    "REIT - Industrial": "工业 REIT",
    "REIT - Healthcare Facilities": "医疗 REIT",
    "REIT - Hotel & Motel": "酒店 REIT",
    "REIT - Specialty": "特色 REIT",
    "REIT - Mortgage": "抵押 REIT",
    "Real Estate Services": "房地产服务",
    "Real Estate - Development": "房地产开发",
    "Real Estate - Diversified": "多元化房地产",
    # 基础材料
    "Chemicals": "化工",
    "Specialty Chemicals": "特种化工",
    "Steel": "钢铁",
    "Copper": "铜",
    "Aluminum": "铝",
    "Gold": "黄金",
    "Silver": "白银",
    "Other Precious Metals & Mining": "其他贵金属与采矿",
    "Other Industrial Metals & Mining": "其他工业金属与采矿",
    "Coking Coal": "焦煤",
    "Paper & Paper Products": "造纸",
    "Lumber & Wood Production": "木材",
    "Building Materials": "建筑材料",
    # 通信服务
    "Internet Content & Information": "互联网内容与信息",
    "Telecom Services": "电信服务",
    "Entertainment": "娱乐",
    "Electronic Gaming & Multimedia": "电子游戏与多媒体",
    "Broadcasting": "广播电视",
    "Publishing": "出版",
}


# ---- 基础设施 ----


def clear_cache_us() -> None:
    """清空美股市场分析缓存（外部刷新入口）。"""
    clear_cache()
    _name_map.cache_clear()


def _q(sql: str) -> pd.DataFrame:
    """执行 DuckDB 只读查询（短连接，线程安全）。"""
    con = duckdb.connect()
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def _avail() -> bool:
    """数据目录与关键分区可用性。"""
    return (DATA_DIR / "1_kline_data").is_dir()


def _glob_sql(rel_glob: str) -> str:
    return f"read_parquet('{DATA_DIR / rel_glob}', hive_partitioning=1)"


def _partition_file(rel_dir: str, ymd: str) -> str | None:
    """分区文件的绝对路径（不存在返回 None）。"""
    path = DATA_DIR / rel_dir / f"dt={ymd}" / "data.parquet"
    return str(path) if path.exists() else None


def _read_partitioned(
    rel_dir: str, ymd_days: Sequence[str], columns: str = "*"
) -> pd.DataFrame:
    """按显式分区文件列表读取（**唯一正确的按日期取数方式**）。

    走文件列表而不是 `**/*.parquet` + WHERE：文件内 dt 列会遮蔽 hive 分区列，
    DuckDB 无法裁剪，且耗时会随库内总分区数增长。

    `columns` 必须按需裁剪：日线文件有 11 列，`SELECT *` 读 310 个分区耗时
    1.62s，只取 symbol/dt/close 三列只要 0.05s（32 倍差距）。
    """
    files = [f for f in (_partition_file(rel_dir, d) for d in ymd_days) if f]
    if not files:
        return pd.DataFrame()
    literal = ",".join(repr(f) for f in files)
    return _q(f"SELECT {columns} FROM read_parquet([{literal}])")


def _trading_days(end: str | None, n: int) -> list[str]:
    """截至 end（YYYYMMDD 或 None=最新）的最近 n 个交易日，降序，[0]=最新。"""
    dates = list_partition_dates(KLINE_REL, DATA_DIR)
    if end:
        dates = [d for d in dates if d <= end]
    return dates[-n:][::-1]


def _latest_trade_date() -> str | None:
    d = _trading_days(None, 1)
    return d[0] if d else None


def _latest_index_date() -> str | None:
    """指数分区最新日期（**通常滞后个股若干天**，页面需分别标注）。"""
    dates = list_partition_dates(INDEX_REL, DATA_DIR)
    return dates[-1] if dates else None


def _index_trading_days(end: str | None, n: int) -> list[str]:
    """**指数分区自己的**交易日序列（降序）。

    不可用个股日历替代：index_daily 与 daily_forward 的分区集合不同，
    混用会取到不存在的分区、静默返回空。
    """
    dates = list_partition_dates(INDEX_REL, DATA_DIR)
    if end:
        dates = [d for d in dates if d <= end]
    return dates[-n:][::-1]


def _dedupe_bars(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """同 (symbol, dt) 可能有多条 release（同步去重抓取），保留最新发布行。"""
    if df.empty:
        return df
    if "published_at" in df.columns:
        return df.sort_values("published_at", ascending=False).drop_duplicates(
            keys, keep="first"
        )
    return df.drop_duplicates(keys, keep="first")


# ---- 全市场截面快照 ----

_MARKET_SNAP_COLS = "symbol, dt, close, amount, volume"


def _market_pct_snapshot() -> tuple[str | None, pd.DataFrame]:
    """最新交易日的全市场涨跌幅快照（**跨域唯一实现**）。

    返回 (trade_date, DataFrame[symbol, close, amount, volume, pct_change])。
    口径：close_t / close_{t-1} - 1（**原始未复权价**）。剔除停牌（close<=0/NaN）
    与当日新上市（无前收）的个股；±100% 之外视为拆股/公司行动造成的价格跳变，
    裁剪以免污染行业均值与龙头榜。
    """
    days = _trading_days(None, 2)
    if not days:
        return None, pd.DataFrame()
    k = _read_partitioned(KLINE_REL, days, columns=_MARKET_SNAP_COLS)
    if k.empty:
        return days[0], pd.DataFrame()
    k = _dedupe_bars(k, ["symbol", "dt"])
    k["dt"] = k["dt"].astype(str)
    cur = days[0]
    cols = [c for c in ("symbol", "close", "amount", "volume") if c in k.columns]
    if len(days) == 1:
        snap = k[k["dt"] == cur][cols].copy()
        snap["pct_change"] = 0.0
        return cur, snap[snap["close"].fillna(0) > 0]

    prev = days[1]
    p = k.pivot_table(index="symbol", columns="dt", values="close")
    if cur not in p.columns or prev not in p.columns:
        snap = k[k["dt"] == cur][cols].copy()
        snap["pct_change"] = 0.0
        return cur, snap[snap["close"].fillna(0) > 0]
    prev_close = p[prev].reindex(p.index)
    valid = prev_close.notna() & (prev_close > 0)
    calc = ((p[cur] / prev_close - 1) * 100).where(valid)
    snap = k[k["dt"] == cur][cols].copy()
    snap = snap.merge(calc.rename("pct_change").reset_index(), on="symbol", how="left")
    snap = snap[snap["close"].fillna(0) > 0]
    snap["pct_change"] = snap["pct_change"].fillna(0.0).clip(-PCT_CLIP, PCT_CLIP)
    return cur, snap


# ---- 量能基准（量比计算的底座） ----

_VOLUME_BASELINE_WINDOW = 20


def _volume_baseline(window: int = _VOLUME_BASELINE_WINDOW) -> pd.DataFrame:
    """各标的在**前 window 个交易日**的平均成交量/成交额。

    返回 DataFrame[symbol, avg_volume, avg_amount, base_days]。

    **基准窗口不含最新交易日** —— 量比 = 当日量 / 前 20 日均量，若把当日
    算进基准，巨量当日会抬高分母把自己稀释掉（专业口径都是「前 N 日」）。
    """
    days = _trading_days(None, window + 1)
    if len(days) < 2:
        return pd.DataFrame(columns=["symbol", "avg_volume", "avg_amount", "base_days"])
    base_days = days[1 : window + 1]  # days[0] 是最新交易日，排除
    k = _read_partitioned(KLINE_REL, base_days, columns="symbol, volume, amount")
    if k.empty:
        return pd.DataFrame(columns=["symbol", "avg_volume", "avg_amount", "base_days"])
    agg = k.groupby("symbol").agg(
        avg_volume=("volume", "mean"),
        avg_amount=("amount", "mean"),
        base_days=("volume", "size"),
    )
    return agg.reset_index()


def _hot_snapshot() -> tuple[str | None, pd.DataFrame]:
    """最新交易日截面 + 量比/成交额（热门榜的公共底座）。

    返回 DataFrame[symbol, close, amount, volume, pct_change, avg_volume,
    avg_amount, rvol]。`rvol`（量比）为当日量 / 前 20 日均量，基准不足
    20 天的标的 rvol 置空而不是用短窗口凑数（新股量比会严重失真）。
    """
    latest, snap = _market_pct_snapshot()
    if not latest or snap.empty:
        return latest, pd.DataFrame()
    base = _volume_baseline()
    df = snap.merge(base, on="symbol", how="left")
    enough = df["base_days"].fillna(0) >= _VOLUME_BASELINE_WINDOW
    avg_vol = df["avg_volume"].where(df["avg_volume"].fillna(0) > 0)
    df["rvol"] = (df["volume"] / avg_vol).where(enough)
    return latest, df


# ---- 名称 / 行业 / 基本面快照 ----


@lru_cache(maxsize=1)
def _name_map() -> dict[str, str]:
    """symbol(AAPL) -> 中文名（security_master 全市场主表）。"""
    return build_name_map(str(DATA_DIR / SECURITY_MASTER_REL))


def _names(symbols: Iterable[str]) -> dict[str, str]:
    return {s: _name_map().get(s, s) for s in symbols}


def _universes() -> pd.DataFrame:
    """标的池主表（symbol / cn_name / en_name）。"""
    path = DATA_DIR / SECURITY_MASTER_REL
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "cn_name", "en_name"])

    def _load() -> pd.DataFrame:
        df = pd.read_parquet(path)
        return df[["symbol", "cn_name", "en_name"]].drop_duplicates("symbol")

    return cached("us:universe", _load, ttl=1800.0)


def _snapshot_files(rel_glob: str) -> list[str]:
    """标的级快照目录下的全部 parquet 文件（显式清单，供 union_by_name 读取）。"""
    return sorted(str(p) for p in (DATA_DIR / rel_glob).parent.glob("*.parquet"))


def _read_snapshot_files(sql_cols: str, rel_glob: str) -> pd.DataFrame:
    """读「每股一文件」快照目录，**列型不一致安全的唯一读法**。

    上游降级写入会把个别文件写出全 NULL 列（NULL 型），DuckDB glob 默认取
    首文件 schema → `ConversionException: failed to cast ... to NULL` 整批读炸
    （2026-10-15 实测 f10/sector 各 516 文件）。因此：
    1. 走文件清单 + `union_by_name=true`（对齐 `_load_analyst_table` 先例）
    2. 外层 try/except 降级为空表 —— 下游面板显示空，而不是整页 500
    """
    files = _snapshot_files(rel_glob)
    if not files:
        return pd.DataFrame()
    literal = ",".join(repr(f) for f in files)
    return _q(f"SELECT {sql_cols} FROM read_parquet([{literal}], union_by_name=true)")


def _sector_map() -> pd.DataFrame:
    """GICS 行业映射：symbol -> sector(11 大类) / industry(细分)。

    来源 `2_base_sector/sector/{SYMBOL}.parquet`（每股一文件，快照）。
    security_master 里**没有**行业字段，行业一律走这张表。
    """

    def _load() -> pd.DataFrame:
        empty = pd.DataFrame(columns=["symbol", "sector", "industry"])
        try:
            df = _read_snapshot_files(
                "symbol, CAST(sector AS VARCHAR) AS sector, "
                "CAST(industry AS VARCHAR) AS industry",
                SECTOR_GLOB,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 sector 快照失败，降级为空表: %s", exc)
            return empty
        if df.empty:
            return empty
        df = df.drop_duplicates("symbol")
        # 29 只 sector 为空 → 归为「未分类」，避免热力图/轮动出现 NaN 分组
        df["sector"] = df["sector"].fillna("").replace("", "Unknown")
        df["industry"] = df["industry"].fillna("")
        return df

    return cached("us:sector_map", _load, ttl=1800.0)


def _f10_snapshot() -> pd.DataFrame:
    """基本面快照：市值 / PE / PB / 股息率 / 52 周高低 / 中文名。

    这是**估值类分析的主数据源**（valuation 分区曾长期静默写空，
    见 docs/美股市场分析模块_设计方案.md §四）。读取走
    `_read_snapshot_files`（union_by_name + 降级），列型不一致不再炸。
    """
    _EMPTY_COLS = [
        "symbol",
        "name",
        "market_cap",
        "pe_ratio",
        "pb_ratio",
        "dividend_yield",
        "52w_high",
        "52w_low",
        "avg_volume",
    ]

    def _load() -> pd.DataFrame:
        try:
            df = _read_snapshot_files(
                "symbol, CAST(name AS VARCHAR) AS name, "
                "CAST(market_cap AS DOUBLE) AS market_cap, "
                "CAST(pe_ratio AS DOUBLE) AS pe_ratio, "
                "CAST(pb_ratio AS DOUBLE) AS pb_ratio, "
                "CAST(dividend_yield AS DOUBLE) AS dividend_yield, "
                'CAST("52w_high" AS DOUBLE) AS "52w_high", '
                'CAST("52w_low" AS DOUBLE) AS "52w_low", '
                "CAST(avg_volume AS DOUBLE) AS avg_volume",
                F10_GLOB,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 f10 快照失败，降级为空表: %s", exc)
            return pd.DataFrame(columns=_EMPTY_COLS)
        if df.empty:
            return pd.DataFrame(columns=_EMPTY_COLS)
        return df.drop_duplicates("symbol")

    return cached("us:f10", _load, ttl=1800.0)


def _load_analyst_table(name: str) -> pd.DataFrame:
    """读取 `4_analyst/{name}/` 下全部标的文件（约 480-500 个小文件）。

    结果带长 TTL 缓存：这些是快照/事件式小表，全市场合计行数不大
    （最大的 upgrades_downgrades 约 18 万行），但按请求重读文件会很浪费。
    """

    def _load() -> pd.DataFrame:
        path = DATA_DIR / ANALYST_REL / name
        if not path.is_dir():
            return pd.DataFrame()
        files = sorted(path.glob("*.parquet"))
        if not files:
            return pd.DataFrame()
        literal = ",".join(repr(str(f)) for f in files)
        return _q(f"SELECT * FROM read_parquet([{literal}], union_by_name=true)")

    return cached(f"us:analyst:{name}", _load, ttl=1800.0)


def _load_events(rel_glob: str) -> pd.DataFrame:
    """读取事件式数据集（分红 / 拆股）。"""

    def _load() -> pd.DataFrame:
        if not (DATA_DIR / rel_glob).parent.is_dir():
            return pd.DataFrame()
        return _q(f"SELECT * FROM {_glob_sql(rel_glob)}")

    return cached(f"us:events:{rel_glob}", _load, ttl=1800.0)


def _sector_cn(sector: str | None) -> str:
    """GICS 英文类名 -> 中文展示名。"""
    if not sector:
        return SECTOR_CN["Unknown"]
    return SECTOR_CN.get(sector, sector)


def _industry_cn(industry: str | None) -> str:
    """细分行业英文名 -> 中文展示名（查不到回落英文原文）。"""
    if not industry:
        return ""
    return INDUSTRY_CN.get(industry, industry)


# ---- 诊断 ----


def feed_status() -> dict[str, Any]:
    """数据可用性与各数据集最新日期（前端诊断 & 面板日期标注）。"""
    avail = _avail()
    return {
        "available": avail,
        "data_dir": str(DATA_DIR),
        "kline_latest": _latest_trade_date() if avail else None,
        "index_latest": _latest_index_date() if avail else None,
        "valuation_latest": (
            list_partition_dates(VALUATION_REL, DATA_DIR)[-1:] or [None]
        )[0]
        if avail
        else None,
        "universe_size": int(len(_universes())) if avail else 0,
        "sector_covered": int(len(_sector_map())) if avail else 0,
        "indices": INDEX_OVERVIEW,
        # 页面需向用户明示的口径与覆盖限制
        "notes": {
            "universe": "标的池：标普500 + 纳指补充，非全市场",
            "price_adjust": "日线为原始价（未复权），成交额为美元原始值",
            "no_vix": "本地指数数据无 VIX，也无任何 ETF",
            "index_lag": "指数分区通常滞后个股若干交易日，面板分别标注日期",
        },
    }


# 供 router 的 SSE 刷新复用
__all__ = [
    "DATA_DIR",
    "KLINE_REL",
    "INDEX_REL",
    "BIG_MOVE_THRESHOLD",
    "INDEX_OVERVIEW",
    "clear_cache_us",
    "feed_status",
    "_q",
    "_avail",
    "_trading_days",
    "_latest_trade_date",
    "_latest_index_date",
    "_index_trading_days",
    "_market_pct_snapshot",
    "_read_partitioned",
    "_partition_file",
    "_glob_sql",
    "_dedupe_bars",
    "_names",
    "_universes",
    "_sector_map",
    "_f10_snapshot",
    "_load_analyst_table",
    "_load_events",
    "_sector_cn",
    "_industry_cn",
    "to_iso",
    "trading_days_until",
    "partition_dates_to_sql",
]
