"""港股机会雷达 —— 可解释的因子复合评分（机会榜 / 行业榜 / 风险榜）。

设计纪律（详见 docs/港股市场分析_专业化升级_细案_20261015.md 第四、五节）：
- 展示面用词边界：本模块是研究评分（位置语言），不产生交易指令
- 因子选择双过闸：全截面 rank IC 与五分位尾部差方向一致才入分柱
  （验证器 backend/scripts/hk_opportunity_ic_check.py，结果见细案实施状态）。
  ca_broker_pct / ca_cust_share_disc 因尾部与 IC 反向（U 形关系）被排除。
- rank_pct 分母 = 当日该因子有值的全部标的（非指数成分）；缺失柱按中性
  50 分计入（不奖不罚），pillars_covered 如实披露，覆盖 <4 柱不入榜
- 入榜门槛：价格 ≥ 0.5 港元、成交额 ≥ 5000 万港元、上市 ≥ 120 交易日
  （mom_ret_120d 有值）；缺失一律 None（前端显示 —），不冒充 0
- 港股无 L2（不存在主力净额）、无市值日频（l1 市值列全 0 为垃圾列），
  评分只用已验证的真实因子
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from backend.services.api.market_analysis_hk.quanthk_feed import (
    DATA_DIR,
    _avail,
    _industry_map,
    _market_pct_snapshot,
    _name_map,
    _q,
)
from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_shared.display import fmt_yi
from backend.services.api.market_analysis_shared.market_days import iso_date

logger = logging.getLogger(__name__)

L1_REL = "6_ml_datasets/l1_factors"
SOUTH_REL = "6_ml_datasets/south_factors"
CCASS_REL = "6_ml_datasets/ccass_factors"

# 入榜门槛（港股专属：仙股/老千股过滤）
MIN_PRICE_HKD = 0.5
MIN_AMOUNT_HKD = 5e7  # 5000 万港元
MIN_PILLARS = 4  # 有效柱数下限（共 6 柱）

# 分柱权重（2026-10-15 按 hk_opportunity_ic_check.py 实测强度定稿：
# liq 族尾部最强 ±175~196bp 权重最高；ccass 族稳健次之；south 尾部最弱但
# 为港股特色资金面保留 15%）
PILLAR_WEIGHTS: dict[str, float] = {
    "mom": 0.20,  # 动量（60 日 + 20 日突破）—— mom_ret_20d 实测无效已剔除
    "trend": 0.10,  # 价格位置（60 日）—— trend_r2_20/position_20 实测未过闸
    "south": 0.15,  # 南向持仓规模（仅 sb_quantity 过闸）
    "ccass": 0.20,  # 筹码（重叠度/集中度正向，披露规模反向）
    "liq": 0.25,  # 流动性溢价（amihud 正向 / 成交额反向 / 换手正向）
    "risk": 0.10,  # 风险结构（低 ATR + 量价结构健康）
}
assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 1e-9

# 柱 → l1_factors 因子定义（全部为 h=10 双过闸因子，方向=实测方向）
_L1_COLS: dict[str, list[str]] = {
    "mom": ["mom_ret_60d", "mom_breakout_20d"],
    "trend": ["price_position_60"],
    "liq": ["liq_amihud_20", "liq_amount", "liq_turnover_os"],
    "risk": ["vol_atr_14", "pv_corr_20", "up_volume_ratio_20"],
}
# 门槛/风险榜用的非柱 l1 列（mom_ret_5d 风险事实、mom_ret_120d 上市时长门槛）
_L1_EXTRA_COLS = ["mom_ret_5d", "mom_ret_120d"]
# 南向柱因子（IC 双过闸实测：仅 sb_quantity 过闸；sb_pct_d5 只作事实标注
# 与风险榜理由，不入评分——见细案实施状态）；_SOUTH_FACT_COLS 是读取但非柱列
_SOUTH_COLS = ["sb_quantity"]
_SOUTH_FACT_COLS = ["sb_pct_d5"]
# CCASS 柱（IC 双过闸实测采信的三个因子；ca_n_pis 尾部反向、
# ca_broker_pct / ca_cust_share_disc 因 U 形非单调被剔除——见细案实施状态）
_CCASS_COLS = ["ca_hhi_disc", "ca_rank_overlap_20d", "ca_disclosed_sum"]

_FACTOR_DIR: dict[str, int] = {
    "mom_ret_60d": +1,
    "mom_breakout_20d": +1,
    "price_position_60": +1,
    "liq_amihud_20": +1,
    "liq_amount": -1,
    "liq_turnover_os": +1,
    "vol_atr_14": -1,
    "pv_corr_20": -1,
    "up_volume_ratio_20": -1,
    "sb_quantity": +1,
    "ca_hhi_disc": +1,
    "ca_rank_overlap_20d": +1,
    "ca_disclosed_sum": -1,
}

# 触发标签：柱分 ≥ 阈值时机械生成（位置语言事实陈述，非评价/非建议）
_PILLAR_TAG = {
    "mom": (80, "动量居前"),
    "trend": (80, "价格位置居前"),
    "south": (80, "南向持仓居前"),
    "ccass": (80, "筹码面居前"),
    "liq": (80, "流动性面居前"),
    "risk": (80, "低波动"),
}


def _latest_partition(rel: str) -> str | None:
    """数据集最新分区 dt 字符串（YYYYMMDD）。"""
    root = DATA_DIR / rel
    if not root.is_dir():
        return None
    names = sorted(
        p.name[3:]
        for p in root.glob("dt=*")
        if (p / "data.parquet").is_file() and p.name[3:].isdigit()
    )
    return names[-1] if names else None


def _read_partition(rel: str, cols: list[str]) -> pd.DataFrame:
    """直读指定数据集最新分区（DuckDB 单分区，禁跨分区 glob）。"""
    dt = _latest_partition(rel)
    if not dt:
        return pd.DataFrame()
    path = DATA_DIR / rel / f"dt={dt}" / "data.parquet"
    col_sql = ", ".join(f'"{c}"' for c in cols)
    try:
        return _q(f"SELECT symbol, {col_sql} FROM read_parquet('{path}')")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hk_opp] 读取 %s dt=%s 失败: %s", rel, dt, exc)
        return pd.DataFrame()


def _rank_pct(s: pd.Series) -> pd.Series:
    """横截面分位 0–100（分母=该列非 NaN 全体）；NaN 保持 NaN。"""
    r = s.rank(pct=True) * 100.0
    return r


def _pillar_scores(df: pd.DataFrame, pillar: str, factors: list[str]) -> pd.Series:
    """单柱得分：柱内因子分位均值（负向因子取 100−分位）；全缺 → NaN。

    柱内有因子缺失（如无南向持仓）时按其余可用因子均值；柱全缺则返回 NaN，
    由合成阶段按中性 50 计入。
    """
    parts: list[pd.Series] = []
    for f in factors:
        if f not in df.columns:
            continue
        direction = _FACTOR_DIR.get(f)
        if direction is None:  # 读取但未过闸的列（如 sb_pct_d5）：不入柱
            continue
        rp = _rank_pct(df[f])
        parts.append(rp if direction > 0 else 100.0 - rp)
    if not parts:
        return pd.Series(float("nan"), index=df.index)
    stacked = pd.concat(parts, axis=1)
    return stacked.mean(axis=1, skipna=True)


def _build_universe() -> tuple[str | None, pd.DataFrame]:
    """当日候选池：行情快照 ⊗ l1/南向/CCASS 因子 ⊗ 行业 + 六柱得分。

    返回 (trade_date, DataFrame)。列含 symbol/name/sector/close/amount/
    pct_change/pillar_*（未中性化前）/opp_score/pillars_covered。
    """
    trade_date, prices = _market_pct_snapshot()
    if prices.empty:
        return trade_date, pd.DataFrame()

    l1_cols = sorted(
        {c for cols in _L1_COLS.values() for c in cols} | set(_L1_EXTRA_COLS)
    )
    l1 = _read_partition(L1_REL, l1_cols)
    south = _read_partition(SOUTH_REL, _SOUTH_COLS + _SOUTH_FACT_COLS)
    ccass = _read_partition(CCASS_REL, _CCASS_COLS)

    df = prices[["symbol", "close", "amount", "pct_change"]].copy()
    for extra in (l1, south, ccass):
        if extra.empty:
            continue
        df = df.merge(extra, on="symbol", how="left")

    # 入榜门槛（评分宇宙）：价格/流动性/上市时长（mom_ret_120d 有值 ≈ 上市满 120 交易日）
    _m120 = df.get("mom_ret_120d")
    df["_eligible"] = (
        (df["close"].fillna(0.0) >= MIN_PRICE_HKD)
        & (df["amount"].fillna(0.0) >= MIN_AMOUNT_HKD)
        & (_m120.notna() if _m120 is not None else True)
    )

    # 六柱
    scores: dict[str, pd.Series] = {}
    for pillar, factors in _L1_COLS.items():
        scores[pillar] = _pillar_scores(df, pillar, factors)
    scores["south"] = _pillar_scores(df, "south", _SOUTH_COLS)
    scores["ccass"] = _pillar_scores(df, "ccass", _CCASS_COLS)

    covered = pd.concat(
        [scores[p].notna().astype(int) for p in PILLAR_WEIGHTS], axis=1
    ).sum(axis=1)
    # 缺失柱按中性 50 计入（不奖不罚）
    weighted = sum(scores[p].fillna(50.0) * w for p, w in PILLAR_WEIGHTS.items())
    df["pillars_covered"] = covered.astype(int)
    for p in PILLAR_WEIGHTS:
        df[f"pillar_{p}"] = scores[p].round(1)

    # 截面分位重标定（2026-10-15）：六柱均值合成分天然集中 35–65（中心极限），
    # 直接当 0–100 分读会让「≥70」类阈值失效（实测 breadth 恒 0）。对候选池内
    # 合成分再取一次 rank_pct → 0–100 均匀分布：排序不变、阈值语义稳定，
    # 与平台研究评分「0–100 截面分位」口径一致。非候选标的为 NaN（不入榜）。
    elig = df["_eligible"] & (covered >= MIN_PILLARS)
    df["opp_score"] = float("nan")
    df.loc[elig, "opp_score"] = (weighted[elig].rank(pct=True) * 100.0).round(1)

    names = _name_map()
    df["name"] = df["symbol"].map(lambda s: names.get(str(s), str(s)))
    industry = _industry_map()
    df["sector"] = df["symbol"].map(lambda s: industry.get(str(s)))
    return iso_date(trade_date), df


def _build_universe_cached() -> tuple[str | None, pd.DataFrame]:
    """候选池构建结果缓存（四榜共享同一份 universe，TTL 走共享 cached）。

    返回的 DataFrame 只读使用（各榜取子集均先 .copy()，不再原地写入）。
    """
    return cached("hk_opp_universe", _build_universe)


def _fmt_pillar(v: float | int | None) -> float | None:
    if v is None:
        return None
    fv = float(v)
    return None if math.isnan(fv) else round(fv, 1)


def _safe_float(v: Any, default: float = 0.0) -> float:
    """NaN/None/非法值 → default（防 NaN 进入 JSON——JSON.parse 会炸）。"""
    if v is None:
        return default
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(fv) else fv


def get_opportunity_stocks(
    limit: int = 30, sector: str | None = None
) -> dict[str, Any]:
    """机会个股榜：综合分降序（入榜门槛 + ≥4 柱覆盖）。

    展示口径：opp_score 是研究评分（截面分位合成），非模型预测；
    分母 = 当日有因子值的全市场港股（见 summary 端点）。
    """

    def _load() -> dict[str, Any]:
        if not _avail():
            return {"trade_date": None, "items": [], "universe": {}}
        trade_date, df = _build_universe_cached()
        if df.empty:
            return {"trade_date": trade_date, "items": [], "universe": {}}
        pool = df[df["_eligible"] & (df["pillars_covered"] >= MIN_PILLARS)].copy()
        scored_total = int(len(pool))
        if sector:
            pool = pool[pool["sector"] == sector]
        full = pool.sort_values("opp_score", ascending=False)
        pool = full.head(limit)

        items: list[dict[str, Any]] = []
        for row in pool.itertuples(index=False):
            pillars = {
                p: _fmt_pillar(getattr(row, f"pillar_{p}")) for p in PILLAR_WEIGHTS
            }
            tags: list[str] = []
            for p, (thr, tag) in _PILLAR_TAG.items():
                v = pillars.get(p)
                if v is not None and v >= thr:
                    tags.append(tag)
            sb_d5 = getattr(row, "sb_pct_d5", None)
            if sb_d5 is not None and not pd.isna(sb_d5) and float(sb_d5) > 0.001:
                tags.append("南向5日增持")
            sb_d5_pp = (
                None
                if sb_d5 is None or pd.isna(sb_d5)
                else round(float(sb_d5) * 100.0, 2)
            )
            items.append(
                {
                    "symbol": str(row.symbol),
                    "name": str(row.name),
                    "sector": row.sector if isinstance(row.sector, str) else None,
                    "opp_score": float(row.opp_score),
                    "pct_change": round(_safe_float(row.pct_change), 2),
                    "amount_yi": fmt_yi(_safe_float(row.amount)),
                    "pillars": pillars,
                    "pillars_covered": int(row.pillars_covered),
                    "tags": tags,
                    "sb_chg_d5_pp": sb_d5_pp,
                }
            )
        # 行业内排名（在完整候选池上计算，不因 top-N 截断失真）
        if not sector and items:
            rank_map: dict[str, tuple[int, int]] = {}
            for _sec, grp in full.groupby("sector", dropna=False):
                n = int(len(grp))
                for i, sym in enumerate(grp["symbol"].astype(str), start=1):
                    rank_map[sym] = (i, n)
            for it in items:
                rk = rank_map.get(it["symbol"])
                if rk and it["sector"] is not None:
                    it["rank_in_sector"], it["sector_size"] = rk
        return {
            "trade_date": trade_date,
            "items": items,
            "universe": {
                "eligible": int(df["_eligible"].sum()),
                "scored": scored_total,
            },
        }

    key = f"hk_opp_stocks:{limit}:{sector or ''}"
    return cached(key, _load)


def get_opportunity_sectors(limit: int = 12, min_stocks: int = 8) -> dict[str, Any]:
    """机会行业榜：行业机会分 = 成分（门槛过滤后）综合分中位数。"""

    def _load() -> dict[str, Any]:
        if not _avail():
            return {"trade_date": None, "items": []}
        trade_date, df = _build_universe_cached()
        if df.empty:
            return {"trade_date": trade_date, "items": []}
        pool = df[
            df["_eligible"]
            & (df["pillars_covered"] >= MIN_PILLARS)
            & df["sector"].notna()
        ].copy()
        items: list[dict[str, Any]] = []
        for sec, grp in pool.groupby("sector"):
            if len(grp) < min_stocks:
                continue
            median_score = float(grp["opp_score"].median())
            # 机会宽度 = 成分中截面分位 ≥70（即候选池前 30%）的占比
            breadth = float((grp["opp_score"] >= 70).mean())
            # 南向 5 日持股变化：中位数（求和会被单票异常值放大——实测
            # 「其他金融」求和 +13pp 失真；百分点口径与 drill 一致）
            sb_d5 = grp["sb_pct_d5"].dropna()
            south_median_pp = (
                round(float(sb_d5.median()) * 100.0, 2) if len(sb_d5) else None
            )
            top3 = grp.sort_values("opp_score", ascending=False).head(3)
            items.append(
                {
                    "name": str(sec),
                    "opp_score_median": round(median_score, 1),
                    "opportunity_breadth": round(breadth, 3),
                    "stock_count": int(len(grp)),
                    "south_chg_d5_median_pp": south_median_pp,
                    "top_stocks": [
                        {
                            "symbol": str(r.symbol),
                            "name": str(r.name),
                            "opp_score": float(r.opp_score),
                            "pct_change": round(_safe_float(r.pct_change), 2),
                        }
                        for r in top3.itertuples(index=False)
                    ],
                }
            )
        items.sort(key=lambda x: x["opp_score_median"], reverse=True)
        return {"trade_date": trade_date, "items": items[:limit]}

    return cached(f"hk_opp_sectors:{limit}:{min_stocks}", _load)


def get_risk_watch(limit: int = 20) -> dict[str, Any]:
    """风险警示榜：弱势 + 波动抬升 + 南向撤离 + 筹码分散（对称口径）。

    展示口径：「近期弱势 / 波动抬升 / 资金与筹码撤离线索」——事实与线索，
    不含操作建议（执行面语言仅保留在下单/台账界面）。
    """

    def _load() -> dict[str, Any]:
        if not _avail():
            return {"trade_date": None, "items": []}
        trade_date, df = _build_universe_cached()
        if df.empty:
            return {"trade_date": trade_date, "items": []}
        pool = df[df["_eligible"] & (df["pillars_covered"] >= MIN_PILLARS)].copy()
        # 风险分 = 100 − 机会分（同一套过闸因子的对称口径，透明可复算）；
        # reasons 独立机械生成（事实分位在完整候选池上计算，防 head 截断失真）
        pool["risk_score"] = (100.0 - pool["opp_score"]).round(1)
        weak5 = _rank_pct(pool["mom_ret_5d"])  # 近 5 日弱势（低分位=弱势）
        vol_hi = _rank_pct(pool["vol_atr_14"])  # 波动抬升
        amt_hi = _rank_pct(pool["amount"])  # 成交额居前（实测不利方向）
        pool = pool.sort_values("risk_score", ascending=False).head(limit)
        items: list[dict[str, Any]] = []
        for idx, row in pool.iterrows():
            reasons: list[str] = []
            if pd.notna(row["mom_ret_5d"]) and weak5.loc[idx] <= 20:
                reasons.append("近5日弱势")
            if pd.notna(row["vol_atr_14"]) and vol_hi.loc[idx] >= 80:
                reasons.append("波动抬升")
            sb = row.get("sb_pct_d5")
            if sb is not None and not pd.isna(sb) and float(sb) < -0.001:
                reasons.append("南向5日减持")
            pc = row["pillar_ccass"]
            if pd.notna(pc) and float(pc) <= 20:
                reasons.append("筹码面弱")
            if pd.notna(row["amount"]) and amt_hi.loc[idx] >= 80:
                reasons.append("成交额居前")
            items.append(
                {
                    "symbol": str(row["symbol"]),
                    "name": str(row["name"]),
                    "sector": row["sector"] if isinstance(row["sector"], str) else None,
                    "risk_score": float(row["risk_score"]),
                    "pct_change": round(_safe_float(row["pct_change"]), 2),
                    "amount_yi": fmt_yi(_safe_float(row["amount"])),
                    "reasons": reasons,
                }
            )
        return {"trade_date": trade_date, "items": items}

    return cached(f"hk_risk_watch:{limit}", _load)


def get_opportunity_summary() -> dict[str, Any]:
    """机会雷达元信息：口径、门槛、覆盖（分母透明度）。"""

    def _load() -> dict[str, Any]:
        if not _avail():
            return {"available": False}
        trade_date, df = _build_universe_cached()
        if df.empty:
            return {"available": False}
        coverage = {
            p: int(pd.to_numeric(df[f"pillar_{p}"], errors="coerce").notna().sum())
            for p in PILLAR_WEIGHTS
        }
        return {
            "available": True,
            "trade_date": trade_date,
            "pct_base": "柱分 rank_pct 分母 = 当日有该因子值的全市场港股；"
            "机会分 = 六柱合成分在候选池内的截面分位（0–100）",
            "thresholds": {
                "min_price_hkd": MIN_PRICE_HKD,
                "min_amount_hkd": MIN_AMOUNT_HKD,
                "min_pillars": MIN_PILLARS,
            },
            "pillar_weights": PILLAR_WEIGHTS,
            "pillar_coverage": coverage,
            "listed_total": int(len(df)),
            "eligible_total": int(df["_eligible"].sum()),
            "factors": {
                "l1": sorted({c for cols in _L1_COLS.values() for c in cols}),
                "south": _SOUTH_COLS,
                "ccass": _CCASS_COLS,
            },
            "excluded_factors": {
                "ca_broker_pct": "IC 与尾部形状反向（U 形关系），尾部榜单禁用",
                "ca_cust_share_disc": "IC 与尾部形状反向（U 形关系），尾部榜单禁用",
            },
            "data_as_of": {
                "l1_factors": iso_date(_latest_partition(L1_REL)),
                "south_factors": iso_date(_latest_partition(SOUTH_REL)),
                "ccass_factors": iso_date(_latest_partition(CCASS_REL)),
                "quote": trade_date,
            },
            "disclaimer": "研究辅助信息，非投资建议；评分为因子分位合成，非模型预测",
        }

    return cached("hk_opp_summary", _load)
