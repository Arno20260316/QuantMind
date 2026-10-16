"""港股聪明钱读侧（HK 市场分析「聪明钱」Tab · 细案 §5 端点族）。

数据面：产物文件（latest/consensus/trends/evidence JSON、seats.parquet、
events/dt=* 事件仓，``datasets.product_dir()``），全部经 ``datasets`` 读侧。

契约：
- 无产物（latest.json 缺失）→ :class:`HkSmartMoneyNotBuilt`（router 映射 404
  ``hk_smart_money_not_built``，页面空态不白屏）；evidence 独立判定缺失；
- 零入柱（evidence_only）是**真裁定不是故障**：共识榜空态、类型面板照常
  （标 is_admitted=false 与最强变体对照）——本层绝不把 evidence_only 当错误；
- 缺失一律 None（前端「—」），**绝不补 0**（产物层已保证，本层原样透传）；
- 统计口径与 validate 一字不差：席位/个股统计只用 episode 首日
  （episode_lead==1）∧ ¬transfer_suspect；胜率 = fwd_exc_10 > 0 占比
  （事后统计，非预测）；南向不在 CCASS 事件仓（直读 hsgt_south）。
"""

from __future__ import annotations

import logging

import pandas as pd

from backend.services.api.market_analysis_hk import institutional_classifier as ic
from backend.shared.hk_smart_money import (
    CCASS_CATEGORIES,
    HORIZONS,
    SEAT_MIN_EVENTS,
)
from backend.shared.hk_smart_money import datasets as ds

log = logging.getLogger(__name__)

#: 席位榜参数域（产物 window 列取值；入参 "0" 视为 all，与常量 SEAT_WINDOWS 同义）
SEATS_WINDOWS = ("20", "60", "all")
#: 排序键 → seats 列对（主, 次）；NaN 沉底。lb_skill = v2 默认（90% 下界，
#: 小样本自动沉底——细案 §2 原则）
SEATS_SORT_COLUMNS: dict[str, list[str]] = {
    "lb_skill": ["lb_skill_bp", "skill_bp"],
    "skill": ["skill_bp", "n_exc10"],
    "vw_skill": ["vw_skill_bp", "n_exc10"],
    "avg_exc": ["avg_exc_bp", "n_events"],
    "shrunk_hit": ["shrunk_hit", "n_events"],
    "n": ["n_events", "shrunk_hit"],
}
SEATS_SORTS = tuple(SEATS_SORT_COLUMNS)
SEATS_LIMIT_DEFAULT = 30
SEATS_LIMIT_MAX = 100

#: 席位档案：活跃标的条数与最近事件回溯（交易日分区数）/ 条数
SEAT_PROFILE_SYMBOLS = 8
SEAT_PROFILE_WINDOW_DAYS = 60
SEAT_PROFILE_EVENTS = 10

#: 共识榜参数域
CONSENSUS_LIMIT_DEFAULT = 50
CONSENSUS_LIMIT_MAX = 200

#: 个股档案窗口（交易日）与时间线条数上限
STOCK_DAYS_DEFAULT = 60
STOCK_DAYS_MIN = 20
STOCK_DAYS_MAX = 120
STOCK_TIMELINE_LIMIT = 300

#: types 端点接受 horizon（字符串键，与证据 results 同源）
HORIZON_KEYS = tuple(str(h) for h in HORIZONS)

#: 口径说明（随响应披露；与细案 §3.4/§3.5 文案一致）
HIT_NOTE = "历史胜率 = 事件后 10 日超额（对当日全市场中位）为正的占比——事后统计，非预测"
SHRINK_NOTE = "收缩估计 w=n/(n+20) 向中性收缩（超额 0bp、胜率 50%）；n 随行披露"
SKILL_NOTE = (
    "方向技能 = 买入事件 +事后超额 / 卖出事件 −事后超额（方向对不对）；"
    "同样是事后统计——持续性审计（/persistence）显示席位层弱持续，"
    "榜单是描述性盘点，不是可跟随信号"
)
LB_NOTE = "90% 下界 = 技能均值 − 1.28×标准误：小样本自动沉底，榜默认按此排序"
SUSPECT_NOTE = "过户嫌疑（单席单日 |Δvalue| > 1.2×当日成交额）只标记，不计入统计"
EPISODE_NOTE = "统计只用 episode 首日（同席同股同向 5 交易日内归并）"
SOUTH_NOTE = "南向（内资·港股通）为 hsgt_south 直读披露，不在 CCASS 事件仓"
TIMELINE_NOTE = "事后超额为封存展示列（T+1 收盘入场口径），不参与任何信号构造"


class HkSmartMoneyNotBuilt(Exception):
    """港股聪明钱产物未构建（404 hk_smart_money_not_built）。"""


class HkSeatNotFound(Exception):
    """席位档案查无此席（404 seat_not_found）。"""


# ---------------------------------------------------------------- 小工具


def _latest() -> dict:
    latest = ds.load_product_json("latest")
    if not isinstance(latest, dict):
        raise HkSmartMoneyNotBuilt()
    return latest


def _evidence() -> dict:
    ev = ds.load_product_json("evidence")
    if not isinstance(ev, dict):
        raise HkSmartMoneyNotBuilt()
    return ev


def _n(v) -> float | int | None:
    """numpy/NaN → JSON 安全标量（缺失 None，绝不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return int(f) if f.is_integer() and abs(f) < 2**53 else float(f)


def _r(v, nd: int) -> float | None:
    f = _n(v)
    return None if f is None else round(float(f), nd)


def _bool(v) -> bool | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return bool(v)


def _s(v) -> str | None:
    """pandas 标量 → JSON 安全 str|None（pd.NA/NaN/空白 一律 None，绝不落「nan」）。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    t = str(v).strip()
    return t if t else None


def _iso(dt) -> str | None:
    """YYYYMMDD / Timestamp → YYYY-MM-DD（None 安全）。"""
    if dt is None or (isinstance(dt, float) and pd.isna(dt)):
        return None
    s = str(dt)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return pd.Timestamp(s).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _normalize_symbol(raw: str) -> str:
    """港股代码归一：Zfill(4) + .HK（全链 4 位后缀式）；非法 → ValueError。"""
    s = (raw or "").strip().upper().removesuffix(".HK").strip()
    if not s.isdigit() or not 1 <= len(s) <= 5:
        raise ValueError("symbol_invalid")
    return f"{int(s):04d}.HK"


# ---------------------------------------------------------------- 端点数据面


def get_overview() -> dict:
    """概览：新鲜度 + 类型面板（持仓/净额 + 入柱 + 最强变体）+ 30 日计数 + 趋势。"""
    latest = _latest()
    ev = ds.load_product_json("evidence")
    admitted: list[str] = list(latest.get("admitted") or [])
    admitted_cats = {a.split("::")[0] for a in admitted}

    # 每类最强 h10 变体（|rank_ic| 最大；None 安全）——与裁定网格同源
    best: dict[str, dict] = {}
    for s in (ev or {}).get("signals") or []:
        r10 = (s.get("results") or {}).get("10") or {}
        icv = r10.get("rank_ic")
        if icv is None:
            continue
        cur = best.get(str(s.get("category")))
        if cur is None or abs(float(icv)) > abs(float(cur["h10_ic"])):
            best[str(s.get("category"))] = {
                "h10_ic": icv,
                "h10_tail_ok": r10.get("tail_ok"),
                "variant": s.get("variant"),
                "variant_label": s.get("variant_label"),
                "direction": _n(s.get("direction")),
                "admitted": bool(s.get("admitted")),
            }

    categories: list[dict] = []
    for row in latest.get("categories") or []:
        cat = str(row.get("category"))
        categories.append(
            {
                **row,
                "category_label": row.get("label") or ic.category_label(cat),
                "admitted": cat in admitted_cats,
                "best": best.get(cat),
            }
        )

    trends = ds.load_product_json("trends")
    trends_body = {
        k: v for k, v in (trends or {}).items() if k not in ("as_of", "generated_at")
    }
    return {
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "version": latest.get("version"),
        "evidence_only": bool(latest.get("evidence_only")),
        "admitted": admitted,
        "directions": latest.get("directions") or {},
        "freshness": latest.get("freshness"),
        "categories": categories,
        "counts_30d": latest.get("counts_30d"),
        "summary": latest.get("summary"),
        "trends": trends_body,
        "notes": {
            "hit": HIT_NOTE,
            "shrink": SHRINK_NOTE,
            "suspect": SUSPECT_NOTE,
        },
    }


def get_types(horizon: str = "10") -> dict:
    """类型效力全网格：24 信号 × h5/h10/h20 裁定统计 + 标定说明（evidence 驱动）。"""
    hk = str(horizon)
    if hk not in HORIZON_KEYS:
        raise ValueError("horizon_invalid")
    ev = _evidence()
    signals: list[dict] = []
    for s in ev.get("signals") or []:
        results = s.get("results") or {}
        signals.append(
            {
                "name": s.get("name"),
                "category": s.get("category"),
                "category_label": s.get("category_label"),
                "variant": s.get("variant"),
                "variant_label": s.get("variant_label"),
                "coverage": s.get("coverage"),
                "direction": _n(s.get("direction")),
                "admitted": bool(s.get("admitted")),
                "decision": s.get("decision"),
                "results": {h: results.get(h) for h in HORIZON_KEYS},
            }
        )
    return {
        "validated_at": ev.get("validated_at"),
        "as_of": (ev.get("window") or {}).get("eval_end"),
        "admit_horizon": ev.get("admit_horizon"),
        "horizon": hk,
        "window": ev.get("window"),
        "admitted": ev.get("admitted") or [],
        "calibration": ev.get("calibration"),
        "signals": signals,
        "notes": {
            "hit": HIT_NOTE,
            "admit": f"入柱判据取 h{ev.get('admit_horizon')} 双过闸",
        },
    }


def _seat_row(r) -> dict:
    """seats 行 → JSON（/seats items 与 /seat windows 共用一份序列化）。

    v2 新列取 r.get：旧产物（19 列）在部署窗口期读回 None，不炸。
    """
    cat = str(r["category"])
    return {
        "participant_id": str(r["participant_id"]),
        "participant_name": _s(r["participant_name"]),
        "category": cat,
        "category_label": ic.category_label(cat),
        "kind": str(r["kind"]),
        "window": str(r["window"]),
        "n_events": _n(r["n_events"]),
        "n_buy": _n(r["n_buy"]),
        "n_sell": _n(r["n_sell"]),
        "n_exc10": _n(r["n_exc10"]),
        "avg_exc_bp": _r(r["avg_exc_bp"], 1),
        "hit": _r(r["hit"], 3),
        "buy_hit": _r(r["buy_hit"], 3),
        "sell_hit": _r(r["sell_hit"], 3),
        "t": _r(r["t"], 2),
        "shrunk_exc_bp": _r(r["shrunk_exc_bp"], 1),
        "shrunk_hit": _r(r["shrunk_hit"], 3),
        "stability": _bool(r["stability"]),
        "eligible": bool(r["eligible"]),
        "last_event_date": _iso(r["last_event_date"]),
        "first_event_date": _iso(r.get("first_event_date")),
        "skill_bp": _r(r.get("skill_bp"), 1),
        "skill_t": _r(r.get("skill_t"), 2),
        "lb_skill_bp": _r(r.get("lb_skill_bp"), 1),
        "vw_skill_bp": _r(r.get("vw_skill_bp"), 1),
        "exc5_bp": _r(r.get("exc5_bp"), 1),
        "exc20_bp": _r(r.get("exc20_bp"), 1),
        "buy_exc_bp": _r(r.get("buy_exc_bp"), 1),
        "sell_exc_bp": _r(r.get("sell_exc_bp"), 1),
        "n_symbols": _n(r.get("n_symbols")),
        "avg_value_m": _r(r.get("avg_value_m"), 2),
    }


def get_seats(
    window: str = "60",
    category: str | None = None,
    sort: str = "lb_skill",
    min_events: int = SEAT_MIN_EVENTS,
    limit: int = SEATS_LIMIT_DEFAULT,
) -> dict:
    """席位榜：窗口/类型过滤 + 排序白名单（seats.parquet 现裁，NaN 沉底）。

    v2 默认 sort=lb_skill（90% 下界）：小样本噪声自动沉底；前端默认
    min_events=50（读侧契约默认仍 SEAT_MIN_EVENTS）。
    """
    w = "all" if window == "0" else window
    if w not in SEATS_WINDOWS:
        raise ValueError("window_invalid")
    if category is not None and category not in CCASS_CATEGORIES:
        raise ValueError("category_invalid")
    if sort not in SEATS_SORTS:
        raise ValueError("sort_invalid")
    seats = ds.load_seats()
    if seats is None:
        raise HkSmartMoneyNotBuilt()

    df = seats[seats["window"] == w]
    if category is not None:
        df = df[df["category"] == category]
    df = df[df["n_events"] >= int(min_events)]
    cols = [c for c in SEATS_SORT_COLUMNS[sort] if c in df.columns] or ["n_events"]
    df = df.sort_values(cols, ascending=False, na_position="last")

    items = [_seat_row(r) for _, r in df.iterrows()]
    latest = ds.load_product_json("latest") or {}
    return {
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "window": w,
        "category": category,
        "sort": sort,
        "min_events": int(min_events),
        "horizon": 10,  # fwd_exc_10（n_exc10 同源）
        "total": int(len(df)),
        "items": items[:limit],
        "notes": {
            "hit": HIT_NOTE,
            "skill": SKILL_NOTE,
            "lower_bound": LB_NOTE,
            "shrink": SHRINK_NOTE,
            "suspect": SUSPECT_NOTE,
            "episode": EPISODE_NOTE,
            "eligible": f"eligible = 事件数 ≥ {SEAT_MIN_EVENTS}（其余仍披露原值）",
        },
    }


def get_consensus(limit: int = CONSENSUS_LIMIT_DEFAULT) -> dict:
    """共识榜：最新 score 分区 top-N（零入柱 → 空榜 + evidence_only=true）。"""
    cons = ds.load_product_json("consensus")
    if not isinstance(cons, dict):
        raise HkSmartMoneyNotBuilt()
    items = cons.get("items") or []
    return {
        "as_of": cons.get("as_of"),
        "generated_at": cons.get("generated_at"),
        "adopted": cons.get("adopted") or [],
        "directions": cons.get("directions") or {},
        "evidence_only": bool(cons.get("evidence_only")),
        "total": int(len(items)),
        "items": items[:limit],
        "notes": {
            "method": "入柱信号截面分位等权（负向柱取 100−pct）；covered<1 不发布分数",
            "missing": "分柱缺失以「—」展示，绝不填 50",
        },
    }


def get_seat_profile(participant_id: str) -> dict:
    """席位档案：三窗统计 + 月度战绩 + 活跃标的 top + 最近事件。

    查无此席 → :class:`HkSeatNotFound`（404 seat_not_found）；产物缺失 → 404。
    """
    pid = (participant_id or "").strip().upper()
    if not pid or len(pid) > 12:
        raise ValueError("participant_invalid")
    seats = ds.load_seats()
    if seats is None:
        raise HkSmartMoneyNotBuilt()
    rows = seats[seats["participant_id"].astype(str) == pid]
    if rows.empty:
        raise HkSeatNotFound()
    meta = rows.iloc[0]
    cat = str(meta["category"])

    windows = {str(r["window"]): _seat_row(r) for _, r in rows.iterrows()}

    monthly: list[dict] = []
    mon = ds.load_seat_monthly()
    if mon is not None and not mon.empty:
        sub = mon[mon["participant_id"].astype(str) == pid].sort_values("month")
        for _, r in sub.iterrows():
            monthly.append(
                {
                    "month": str(r["month"]),
                    "n": _n(r["n"]),
                    "skill_bp": _r(r["skill_bp"], 1),
                    "vw_skill_bp": _r(r["vw_skill_bp"], 1),
                    "hit": _r(r["hit"], 3),
                }
            )

    top_symbols: list[dict] = []
    syms = ds.load_seat_symbols()
    if syms is not None and not syms.empty:
        sub = (
            syms[syms["participant_id"].astype(str) == pid]
            .sort_values("n", ascending=False)
            .head(SEAT_PROFILE_SYMBOLS)
        )
        for _, r in sub.iterrows():
            top_symbols.append(
                {
                    "symbol": str(r["symbol"]),
                    "name": _s(r["stock_name"]),
                    "n": _n(r["n"]),
                    "skill_bp": _r(r["skill_bp"], 1),
                    "vw_skill_bp": _r(r["vw_skill_bp"], 1),
                    "last_event_date": _iso(r["last_dt"]),
                }
            )

    recent: list[dict] = []
    edates = list(ds.event_dates())
    if edates:
        df = ds.load_events(
            dates=edates[-SEAT_PROFILE_WINDOW_DAYS:],
            columns=[
                "dt",
                "symbol",
                "stock_name",
                "participant_id",
                "kind",
                "direction",
                "delta_value",
                "delta_pct",
                "price",
                "transfer_suspect",
                "episode_lead",
                "fwd_exc_5",
                "fwd_exc_10",
                "fwd_exc_20",
            ],
        )
        sub = (
            df[df["participant_id"].astype(str) == pid]
            .sort_values("dt", ascending=False)
            .head(SEAT_PROFILE_EVENTS)
        )
        for _, r in sub.iterrows():
            recent.append(
                {
                    "date": _iso(r["dt"]),
                    "symbol": str(r["symbol"]),
                    "name": _s(r["stock_name"]),
                    "kind": _s(r["kind"]),
                    "direction": _n(r["direction"]),
                    "delta_value": _n(r["delta_value"]),
                    "value_yi": _r(r["delta_value"] / 1e8, 4),
                    "delta_pct": _r(r["delta_pct"], 4),
                    "price": _r(r["price"], 3),
                    "transfer_suspect": bool(r["transfer_suspect"]),
                    "first_of_episode": bool(r["episode_lead"] == 1),
                    "fwd_exc_5_bp": _r(r["fwd_exc_5"] * 1e4, 1),
                    "fwd_exc_10_bp": _r(r["fwd_exc_10"] * 1e4, 1),
                    "fwd_exc_20_bp": _r(r["fwd_exc_20"] * 1e4, 1),
                }
            )

    latest = ds.load_product_json("latest") or {}
    return {
        "participant": {
            "participant_id": pid,
            "participant_name": _s(meta["participant_name"]),
            "category": cat,
            "category_label": ic.category_label(cat),
            "kind": str(meta["kind"]),
            "first_event_date": _iso(meta.get("first_event_date")),
            "last_event_date": _iso(meta.get("last_event_date")),
        },
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "windows": windows,
        "monthly": monthly,
        "top_symbols": top_symbols,
        "recent_events": recent,
        "notes": {
            "hit": HIT_NOTE,
            "skill": SKILL_NOTE,
            "lower_bound": LB_NOTE,
            "shrink": SHRINK_NOTE,
            "suspect": SUSPECT_NOTE,
            "episode": EPISODE_NOTE,
            "fwd": TIMELINE_NOTE,
        },
    }


def get_persistence() -> dict:
    """持续性审计台：seat_persistence.json 原样（构建期实算，同 evidence 模式）。

    席位层弱持续是实测结论（非故障）——界面「稳健性」面如实披露。
    """
    p = ds.load_product_json("seat_persistence")
    if not isinstance(p, dict):
        raise HkSmartMoneyNotBuilt()
    return p


def _stock_stats(sub: pd.DataFrame, days: int) -> dict:
    """该股窗口统计（口径与席位榜一字不差：episode 首日 ∧ ¬嫌疑）。"""
    elig = sub[(sub["episode_lead"] == 1) & (~sub["transfer_suspect"].astype(bool))]
    rows: list[dict] = []
    for cat in CCASS_CATEGORIES:
        c = elig[elig["category"] == cat]
        if c.empty:
            continue
        buy, sell = c[c["direction"] == 1], c[c["direction"] == -1]
        rows.append(
            {
                "category": cat,
                "category_label": ic.category_label(cat),
                "n_buy": int(len(buy)),
                "n_sell": int(len(sell)),
                "net_value_yi": _r(c["delta_value"].sum() / 1e8, 2),
                "avg_exc_10_bp_buy": _r(buy["fwd_exc_10"].mean() * 1e4, 1),
                "avg_exc_10_bp_sell": _r(sell["fwd_exc_10"].mean() * 1e4, 1),
            }
        )
    exc = elig["fwd_exc_10"].dropna()
    up = elig[(elig["direction"] == 1) & elig["fwd_exc_10"].notna()]
    dn = elig[(elig["direction"] == -1) & elig["fwd_exc_10"].notna()]
    return {
        "days": int(days),
        "events": int(len(elig)),
        "n_buy": int((elig["direction"] == 1).sum()),
        "n_sell": int((elig["direction"] == -1).sum()),
        "net_value_yi": _r(elig["delta_value"].sum() / 1e8, 2) if len(elig) else None,
        "avg_exc_10_bp": _r(exc.mean() * 1e4, 1) if len(exc) else None,
        "buy_hit": _r((up["fwd_exc_10"] > 0).mean(), 3) if len(up) else None,
        "sell_hit": _r((dn["fwd_exc_10"] < 0).mean(), 3) if len(dn) else None,
        "by_category": rows,
        "notes": {"hit": HIT_NOTE, "episode": EPISODE_NOTE, "south": SOUTH_NOTE},
    }


def get_stock(symbol: str, days: int = STOCK_DAYS_DEFAULT) -> dict:
    """个股档案：事件时间线 + 类型持仓快照（CCASS 5 类 + 南向）+ 窗口统计。

    无事件也返回 200（窗口统计零态、时间线空）——档案面「有什么给什么」；
    只有产物整体缺失才 404。
    """
    latest = _latest()
    sym = _normalize_symbol(symbol)
    n_days = max(STOCK_DAYS_MIN, min(int(days), STOCK_DAYS_MAX))

    edates = list(ds.event_dates())
    win = edates[-n_days:]
    sub = pd.DataFrame()
    if win:
        df = ds.load_events(
            dates=win,
            columns=[
                "dt",
                "symbol",
                "stock_name",
                "participant_id",
                "participant_name",
                "category",
                "kind",
                "direction",
                "delta_qty",
                "delta_value",
                "delta_pct",
                "price",
                "day_amount",
                "transfer_suspect",
                "episode_lead",
                "fwd_exc_5",
                "fwd_exc_10",
                "fwd_exc_20",
            ],
        )
        sub = df[df["symbol"] == sym]

    name = None
    if not sub.empty:
        n0 = sub["stock_name"].dropna()
        name = str(n0.iloc[0]) if len(n0) else None

    timeline: list[dict] = []
    if not sub.empty:
        ordered = sub.sort_values(
            ["dt", "delta_value"],
            ascending=[False, False],
            key=lambda s: s.abs() if s.name == "delta_value" else s,
        )
        for _, r in ordered.head(STOCK_TIMELINE_LIMIT).iterrows():
            day_amount = r["day_amount"]
            ratio = None
            if pd.notna(day_amount) and day_amount:
                ratio = _r(r["delta_value"] / day_amount, 4)
            cat = str(r["category"])
            timeline.append(
                {
                    "date": _iso(r["dt"]),
                    "participant_id": str(r["participant_id"]),
                    "participant_name": str(r["participant_name"]),
                    "category": cat,
                    "category_label": ic.category_label(cat),
                    "kind": str(r["kind"]),
                    "direction": _n(r["direction"]),
                    "delta_qty": _n(r["delta_qty"]),
                    "delta_value": _n(r["delta_value"]),
                    "value_yi": _r(r["delta_value"] / 1e8, 4),
                    "delta_pct": _r(r["delta_pct"], 4),
                    "price": _r(r["price"], 3),
                    "pct_of_amount": ratio,
                    "transfer_suspect": bool(r["transfer_suspect"]),
                    "first_of_episode": bool(r["episode_lead"] == 1),
                    "fwd_exc_5_bp": _r(r["fwd_exc_5"] * 1e4, 1),
                    "fwd_exc_10_bp": _r(r["fwd_exc_10"] * 1e4, 1),
                    "fwd_exc_20_bp": _r(r["fwd_exc_20"] * 1e4, 1),
                }
            )

    holdings: list[dict] = []
    if edates:
        hd = ds.load_holdings(dates=[edates[-1]])
        hsub = hd[(hd["symbol"] == sym) & (hd["qty_sum"] > 0)]
        for _, r in hsub.iterrows():
            cat = str(r["category"])
            holdings.append(
                {
                    "category": cat,
                    "category_label": ic.category_label(cat),
                    "qty_sum": _n(r["qty_sum"]),
                    "n_seats": _n(r["n_seats"]),
                    "date": _iso(edates[-1]),
                }
            )
        s_date = ds.latest_south_date()
        if s_date:
            so = ds.load_south(s_date)
            one = so[so["symbol"] == sym]
            if len(one) and float(one.iloc[0]["holding_quantity"]) > 0:
                holdings.append(
                    {
                        "category": "southbound",
                        "category_label": ic.category_label("southbound"),
                        "qty_sum": _n(one.iloc[0]["holding_quantity"]),
                        "n_seats": None,
                        "date": _iso(s_date),
                    }
                )

    return {
        "symbol": sym,
        "name": name,
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "window": {"days": n_days, "start": _iso(win[0]) if win else None},
        "timeline": timeline,
        "holdings": holdings,
        "stats": _stock_stats(sub, n_days)
        if not sub.empty
        else _stock_stats_empty(n_days),
        "notes": {
            "suspect": SUSPECT_NOTE,
            "episode": EPISODE_NOTE,
            "south": SOUTH_NOTE,
            "fwd": TIMELINE_NOTE,
        },
    }


def _stock_stats_empty(days: int) -> dict:
    return {
        "days": int(days),
        "events": 0,
        "n_buy": 0,
        "n_sell": 0,
        "net_value_yi": None,
        "avg_exc_10_bp": None,
        "buy_hit": None,
        "sell_hit": None,
        "by_category": [],
        "notes": {"hit": HIT_NOTE, "episode": EPISODE_NOTE, "south": SOUTH_NOTE},
    }


def get_evidence() -> dict:
    """证据台：evidence.json 原样（协议/全网格/标定/排除/对账/警告）。"""
    return _evidence()
