"""恒生行业热力图 L2 下钻 —— 点行业看成分股（A 股 /heatmap/drill 模式平移）。

口径（与 L1 父子自洽，见 quanthk_feed.get_sector_heatmap）：
- L1 行业面积 = 成分当日成交额求和（亿）；L2 个股面积 = 个股当日成交额（亿）。
  两层在同一请求内共享同一份 _market_pct_snapshot()（同函数取数、同交易日），
  summary.total_amount_yi 与 L1 同行 value 在无截断时逐位相等（父子和账）。
- 南向 5 日持股变化（south_factors.sb_pct_d5，比率差 ×100 转百分点）作可选
  标注：非港股通标的无南向持仓，输出 None（前端显示 —），不冒充 0。
- 行业名不存在 → 返回 None，router 转 404（detail 带 name），前端据此回退 L1。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.api.market_analysis_hk.quanthk_feed import (
    DATA_DIR,
    _avail,
    _industry_groups,
    _market_pct_snapshot,
    _name_map,
    _q,
)
from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_shared.display import fmt_yi
from backend.services.api.market_analysis_shared.market_days import iso_date

logger = logging.getLogger(__name__)

# L2 单行业成分股上限（防超大盘行业拖垮渲染；按成交额降序截断并如实披露）
MAX_DRILL_ITEMS = 300

# south_factors 数据集（南向持股因子，日分区）
SOUTH_FACTORS_REL = "6_ml_datasets/south_factors"


def _south_d5_map() -> dict[str, float]:
    """最新 south_factors 分区的 symbol -> sb_pct_d5（百分点，2 位小数）。

    只读最新单个分区（直读 data.parquet，不做跨分区 glob）；读取失败返回
    空 dict（下钻不因南向数据缺失而降级失败）。
    """
    try:
        dates = sorted(
            p.name[3:] for p in (DATA_DIR / SOUTH_FACTORS_REL).glob("dt=*")
            if (p / "data.parquet").is_file()
        )
        if not dates:
            return {}
        path = DATA_DIR / SOUTH_FACTORS_REL / f"dt={dates[-1]}" / "data.parquet"
        df = _q(
            "SELECT symbol, sb_pct_d5 FROM read_parquet("
            f"'{path}') WHERE sb_pct_d5 IS NOT NULL"
        )
        if df.empty:
            return {}
        # sb_pct_d5 是持股占比的 5 日差分（比率）；×100 转百分点展示口径
        return {
            str(s): round(float(v) * 100.0, 2)
            for s, v in zip(df["symbol"], df["sb_pct_d5"], strict=True)
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hk_drill] south_factors 读取失败: %s", exc)
        return {}


def get_heatmap_drill(sector_name: str) -> dict[str, Any] | None:
    """行业成分股下钻（L2）。行业名不存在返回 None。"""

    def _load() -> dict[str, Any] | None:
        if not _avail():
            return None
        groups = _industry_groups()
        syms = groups.get(sector_name)
        if not syms:
            return None
        trade_date, prices = _market_pct_snapshot()
        trade_date = iso_date(trade_date)
        if prices.empty:
            return {
                "name": sector_name,
                "trade_date": trade_date,
                "items": [],
                "summary": {"stock_count": 0, "total_amount_yi": 0.0, "truncated": False},
            }
        sub = prices[prices["symbol"].isin(syms)].copy()
        # 成交额缺失（停牌等）按 0 参与求和，排序时沉底；面积下限由前端处理
        sub["amount"] = sub["amount"].fillna(0.0)
        sub = sub.sort_values("amount", ascending=False)
        total_count = int(len(sub))
        truncated = total_count > MAX_DRILL_ITEMS
        sub = sub.head(MAX_DRILL_ITEMS)
        names = _name_map()
        south = _south_d5_map()
        items: list[dict[str, Any]] = []
        for row in sub.itertuples(index=False):
            symbol = str(row.symbol)
            pct_val = float(row.pct_change)
            if pct_val != pct_val:  # NaN 防护（快照已 fillna，双保险）
                pct_val = 0.0
            items.append(
                {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol),
                    "pct_change": round(pct_val, 2),
                    "amount_yi": fmt_yi(float(row.amount)),
                    # 无南向持仓（非港股通标的）→ None，前端显示 —（不冒充 0）
                    "sb_chg_d5_pp": south.get(symbol),
                }
            )
        return {
            "name": sector_name,
            "trade_date": trade_date,
            "items": items,
            "summary": {
                "stock_count": total_count,
                "total_amount_yi": fmt_yi(float(sub["amount"].sum())),
                "truncated": truncated,
            },
        }

    return cached(f"hk_heatmap_drill:{sector_name}", _load)
