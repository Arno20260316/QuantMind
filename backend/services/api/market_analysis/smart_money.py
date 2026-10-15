"""聪明钱读侧（CN 市场分析「聪明钱」Tab · 细案 §5.2 六端点支撑）。

数据面：产物文件（latest/trends/evidence/scores 分区，datasets.product_dir()）
+ 原始 parquet（事件时间线 / 营业部榜 / 北向季度序列，datasets 读侧缓存）。

契约：
- 无产物（latest.json 缺失）→ :class:`SmartMoneyNotBuilt`（router 映射 404
  ``smart_money_not_built``，页面显示空态不白屏）。evidence 例外：它由验证脚本
  产出（先于构建），独立判定缺失；
- 缺失字段一律 null（前端「—」），**绝不补 0**（产物层已保证，本层原样透传）；
- symbol 输出前缀式，入参前后缀/裸码兼容（StockCodeUtil）。
"""

from __future__ import annotations

import logging

import pandas as pd

from backend.shared.smart_money import datasets, stats
from backend.shared.stock_utils import StockCodeUtil

log = logging.getLogger(__name__)

#: 营业部榜允许的聚合窗口（交易日）
BRANCHES_WINDOWS = (5, 20)
#: 营业部榜返回条数（净买降序）
BRANCHES_TOP_N = 20
#: 营业部榜日历回看（日历日；覆盖 window=20 交易日 + 长假）
_BRANCH_LOOKBACK_DAYS = 45
#: 个股档案事件时间线长度（条）
_TIMELINE_LIMIT = 30


class SmartMoneyNotBuilt(Exception):
    """聪明钱产物未构建（404 smart_money_not_built）。"""


def _latest() -> dict:
    latest = datasets.load_product_json("latest")
    if not isinstance(latest, dict):
        raise SmartMoneyNotBuilt()
    return latest


def get_overview() -> dict:
    """概览：统计卡组 + 趋势序列 + 新鲜度 + 裁定柱元数据。"""
    latest = _latest()
    trends = datasets.load_product_json("trends")
    trends_body = {
        k: v for k, v in (trends or {}).items() if k not in ("as_of", "generated_at")
    }
    return {
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "admitted": latest.get("admitted"),
        "pillars": latest.get("pillars"),
        "summary": latest.get("summary"),
        "overview": latest.get("overview"),
        "trends": trends_body,
        "freshness": latest.get("freshness"),
    }


def get_stocks(limit: int = 100) -> dict:
    """选股榜：summary + items（score 降序原样截断；total 为全量条数）。"""
    latest = _latest()
    items = latest.get("items")
    items = items if isinstance(items, list) else []
    return {
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "admitted": latest.get("admitted"),
        "pillars": latest.get("pillars"),
        "summary": latest.get("summary"),
        "total": len(items),
        "items": items[:limit],
    }


def _north_series(symbol: str) -> list[dict]:
    """该股北向季度持仓序列（分数 → 百分点；逐季环比差）。"""
    q = datasets.north_quarterly()
    if q.empty:
        return []
    sub = q[q["symbol"] == symbol].sort_values("quarter")
    out: list[dict] = []
    prev: float | None = None
    for _, r in sub.iterrows():
        pct = r["holding_percentage"]
        pp = None if pd.isna(pct) else round(float(pct) * 100.0, 4)
        chg = None if (pp is None or prev is None) else round(pp - prev, 4)
        out.append(
            {
                "quarter": str(r["quarter"]),
                "report_date": str(r["report_date"]),
                "pct_pp": pp,
                "chg_pp": chg,
            }
        )
        prev = pp
    return out


def get_stock_detail(symbol: str) -> dict:
    """个股档案：榜上条目（可空）+ 事件时间线 + 北向季度序列。

    未上榜也返回 200（on_board=false，时间线可为空）——档案面按「有什么给什么」
    展示；只有产物整体缺失才 404。
    """
    latest = _latest()
    suffix = StockCodeUtil.to_suffix(symbol) or symbol
    prefix = StockCodeUtil.to_prefix(suffix) or symbol

    items = latest.get("items")
    items = items if isinstance(items, list) else []
    item = next((it for it in items if it.get("symbol") == prefix), None)

    name = item.get("name") if item else None
    if name is None:
        names = datasets.name_map()
        if suffix in names.index:
            name = str(names.get(suffix))

    return {
        "symbol": prefix,
        "name": name,
        "industry": item.get("industry") if item else None,
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "on_board": item is not None,
        "item": item,
        "pillars": latest.get("pillars"),
        "timeline": datasets.event_timeline(suffix, limit=_TIMELINE_LIMIT),
        "north": _north_series(suffix),
    }


def get_branches(window: int = 5) -> dict:
    """活跃营业部榜：窗内净买降序（原始层现算，两窗口同一条代码路径）。"""
    if window not in BRANCHES_WINDOWS:
        raise ValueError(f"window 只支持 {BRANCHES_WINDOWS}，收到 {window!r}")
    latest = _latest()
    as_of = latest.get("as_of")
    branches: list[dict] = []
    if as_of:
        end = pd.Timestamp(as_of)
        cal = datasets.trading_calendar(end - pd.Timedelta(days=_BRANCH_LOOKBACK_DAYS), end)
        branches = stats.branch_rank(
            datasets.load_raw("lhb_branch"), cal, end, window=window, top_n=BRANCHES_TOP_N
        )
    return {"as_of": as_of, "window": window, "branches": branches}


def get_evidence() -> dict:
    """验证证据台：evidence.json 原样（由 smart_money_validate.py 产出）。"""
    ev = datasets.load_product_json("evidence")
    if not isinstance(ev, dict):
        raise SmartMoneyNotBuilt()
    return ev


def available_dates() -> dict:
    """可用评分快照日期（YYYY-MM-DD 升序）+ 当前产物 as_of。"""
    latest = _latest()
    dates = datasets.score_dates()
    return {
        "dates": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates],
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
    }
