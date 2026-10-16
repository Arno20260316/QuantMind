"""聪明钱读侧（CN 市场分析「聪明钱」Tab · 细案 §5.2 端点族）。

数据面：产物文件（latest/trends/evidence/track_record/event_study/seats/
scores 分区，datasets.product_dir()）+ 原始 parquet（事件时间线 / 营业部榜 /
席位明细 / 大宗明细 / 北向季度序列，datasets 读侧缓存）。

契约：
- 无产物（latest.json 缺失）→ :class:`SmartMoneyNotBuilt`（router 映射 404
  ``smart_money_not_built``，页面显示空态不白屏）。evidence 例外：它由验证脚本
  产出（先于构建），独立判定缺失；
- ``?date=`` 历史快照：命中最近 ≤date 的计分分区，条目富化（5 日窗金额 /
  最新事件）按该日窗**现算**（与 build 共用 ``items.build_items`` 一份实现），
  响应 ``as_of`` 标注真实快照日防误导；无 ≤date 分区 → SnapshotMissing → 404；
- 缺失字段一律 null（前端「—」），**绝不补 0**（产物层已保证，本层原样透传）；
- symbol 输出前缀式，入参前后缀/裸码兼容（StockCodeUtil）。
"""

from __future__ import annotations

import logging

import pandas as pd

from backend.shared.smart_money import analytics, datasets, stats
from backend.shared.smart_money.items import build_items, n_or_none
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

#: 席位榜参数域（窗口标签与 analytics.SEAT_WINDOWS 输出同源）
SEATS_WINDOWS = ("20", "60", "all")
SEATS_SORTS = ("net", "win_rate")
SEATS_LIMIT_DEFAULT = 50
SEATS_LIMIT_MAX = 300  # = analytics.SEAT_LIST_CAP（列表本身已截断）

#: 选股榜参数域
STOCKS_EVENTS = ("lhb", "inst", "block_trade", "survey")
STOCKS_LIMIT_DEFAULT = 50
STOCKS_LIMIT_MAX = 500

#: 历史快照现算的日历回看（日历日；覆盖「最新事件」一年的声明窗。
#: 注：align_events 把早于日历起点的日期归到 cal[0]，回看太短会把旧事件
#: 的日期错标成窗首日——宁长勿短）
_SNAPSHOT_LOOKBACK_DAYS = 400
#: 历史快照的两融读取回看（日历日；> 5 日窗 + 长假）
_SNAPSHOT_MARGIN_DAYS = 45

#: 个股档案：大宗明细展示笔数
_DZJY_LIMIT = 20

#: 事件类型 → 事件族名（study_ref 引用；与 event_study.json families 键同源）
_STUDY_FAMILIES: dict[str, str] = {
    "lhb": "龙虎榜",
    "inst": "机构席位",
    "block_trade": "大宗交易",
    "survey": "机构调研",
}

#: 北向口径边界文案（2024-08-19 官方取消盘中实时与日频买卖明细；唯一读取点）
NORTH_BOUNDARY_NOTE = (
    "北向披露边界：2024-08-19 起交易所取消盘中实时与日频买卖明细，"
    "本站北向口径为季度持仓（披露延迟约一个季度），非实时资金流"
)


class SmartMoneyNotBuilt(Exception):
    """聪明钱产物未构建（404 smart_money_not_built）。"""


class SmartMoneySnapshotMissing(Exception):
    """请求日期早于最早评分分区（404 smart_money_no_snapshot）。"""


def _latest() -> dict:
    latest = datasets.load_product_json("latest")
    if not isinstance(latest, dict):
        raise SmartMoneyNotBuilt()
    return latest


def get_overview() -> dict:
    """概览：统计卡组 + 趋势序列 + 新鲜度 + 裁定柱元数据 + 北向边界文案。"""
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
        "north_note": NORTH_BOUNDARY_NOTE,
    }


def _filter_page(
    items: list[dict],
    *,
    event: str | None,
    industry: str | None,
    min_score: float | None,
    offset: int,
    limit: int,
) -> tuple[int, list[dict]]:
    """条目筛选 + 分页：返回 (筛选后总数, 当前页)。

    筛选语义：min_score = 复合分下限（score 为 None 的条目剔除，不冒充达标）；
    industry = 行业名精确匹配（rs_hyname）；event = **最近事件类型**等值
    （latest_event.type；「最近有该类事件」的宽语义不在此实现）。
    """

    def _keep(it: dict) -> bool:
        if min_score is not None:
            sc = it.get("score")
            if sc is None or float(sc) < min_score:
                return False
        if industry is not None and it.get("industry") != industry:
            return False
        if event is not None:
            ev = it.get("latest_event") or {}
            if ev.get("type") != event:
                return False
        return True

    hits = [it for it in items if _keep(it)]
    return len(hits), hits[offset : offset + limit]


def _stocks_at(
    latest: dict,
    date: str,
    *,
    limit: int,
    offset: int,
    event: str | None,
    industry: str | None,
    min_score: float | None,
) -> dict:
    """历史快照选股榜：命中最近 ≤date 的计分分区，富化按该日窗现算。"""
    if not (
        isinstance(date, str)
        and len(date) == 10
        and date[4] == "-"
        and date[7] == "-"
        and date.replace("-", "").isdigit()
    ):
        raise ValueError("date_invalid")
    try:
        target = pd.Timestamp(date)
    except (TypeError, ValueError):
        raise ValueError("date_invalid") from None
    if pd.isna(target):
        raise ValueError("date_invalid")

    key = target.strftime("%Y%m%d")
    dates = datasets.score_dates()
    hit: str | None = None
    for d in dates:
        if d <= key:
            hit = d
        else:
            break
    if hit is None:
        raise SmartMoneySnapshotMissing(
            f"无 ≤{date} 的计分分区（最早 {dates[0] if dates else '无'}）"
        )
    scores = datasets.load_scores(hit)
    if scores is None or scores.empty:
        raise SmartMoneyNotBuilt()

    as_of = pd.Timestamp(hit)
    admitted = tuple(
        (str(p["name"]), int(p.get("direction") or 1))
        for p in (latest.get("pillars") or [])
    )
    cal = datasets.trading_calendar(
        as_of - pd.Timedelta(days=_SNAPSHOT_LOOKBACK_DAYS), as_of
    )
    raw = {name: datasets.load_raw(name) for name in datasets.RAW_DATASETS}
    margin = datasets.margin_daily(
        as_of - pd.Timedelta(days=_SNAPSHOT_MARGIN_DAYS), as_of
    )
    north_pp: dict[str, float | None] = {}
    nd = datasets.north_delta()
    if not nd.empty:
        north_pp = {
            str(r["symbol"]): n_or_none(r["delta"] * 100.0) for _, r in nd.iterrows()
        }
    items = build_items(scores, raw, cal, as_of, margin, admitted, north_pp)
    total, page = _filter_page(
        items,
        event=event,
        industry=industry,
        min_score=min_score,
        offset=offset,
        limit=limit,
    )
    return {
        "as_of": as_of.strftime("%Y-%m-%d"),  # 真实快照日（≤ 请求日，防误导）
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "admitted": latest.get("admitted"),
        "pillars": latest.get("pillars"),
        "summary": {
            "pool_size": int(len(scores)),
            "covered_mean": n_or_none(scores["covered"].mean()),
        },
        "requested_date": date,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": page,
    }


def get_stocks(
    limit: int = STOCKS_LIMIT_DEFAULT,
    offset: int = 0,
    date: str | None = None,
    event: str | None = None,
    industry: str | None = None,
    min_score: float | None = None,
) -> dict:
    """选股榜：筛选（event/industry/min_score）+ 分页（offset/limit）。

    默认读 latest.json 的当日条目；``date=`` 走历史快照分区现算
    （见 :func:`_stocks_at`，响应 ``as_of`` 为真实快照日）。
    """
    if event is not None and event not in STOCKS_EVENTS:
        raise ValueError("event_invalid")
    latest = _latest()
    if date:
        return _stocks_at(
            latest,
            date,
            limit=limit,
            offset=offset,
            event=event,
            industry=industry,
            min_score=min_score,
        )
    items = latest.get("items")
    items = items if isinstance(items, list) else []
    total, page = _filter_page(
        items,
        event=event,
        industry=industry,
        min_score=min_score,
        offset=offset,
        limit=limit,
    )
    return {
        "as_of": latest.get("as_of"),
        "generated_at": latest.get("generated_at"),
        "partial": latest.get("partial"),
        "admitted": latest.get("admitted"),
        "pillars": latest.get("pillars"),
        "summary": latest.get("summary"),
        "requested_date": None,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": page,
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


def _seat_breakdown(suffix: str) -> dict | None:
    """该股**最近一次**龙虎榜日的席位构成（买卖两榜逐席；缺表/无记录 → None）。

    只给事实（谁买谁卖多少）+ 文本分类，不做胜率以外的推断；金额单位=元。
    """
    seat = datasets.load_raw("lhb_seat")
    if seat.empty or "symbol" not in seat.columns:
        return None
    sub = seat[seat["symbol"] == suffix]
    if sub.empty:
        return None
    last = sub["trade_date"].max()
    day = sub[sub["trade_date"] == last].copy()
    for c in ("buy_amount", "sell_amount", "net_amount", "buy_ratio", "sell_ratio"):
        if c in day.columns:
            day[c] = pd.to_numeric(day[c], errors="coerce")
    day = day.sort_values(["side", "net_amount"], ascending=[True, False])
    rows: list[dict] = []
    for _, r in day.iterrows():
        seat_name = datasets.s_or_none(r.get("seat_name"))
        cls = analytics.classify_seat(seat_name)
        rows.append(
            {
                "seat_name": seat_name,
                "side": datasets.s_or_none(r.get("side")) or "",
                "class": cls,
                "class_label": analytics.SEAT_CLASS_LABELS.get(cls, cls),
                "buy_amount": n_or_none(r.get("buy_amount")),
                "sell_amount": n_or_none(r.get("sell_amount")),
                "net_amount": n_or_none(r.get("net_amount")),
                "buy_ratio": n_or_none(r.get("buy_ratio")),
                "sell_ratio": n_or_none(r.get("sell_ratio")),
            }
        )
    return {"date": pd.Timestamp(last).strftime("%Y-%m-%d"), "seats": rows}


def _dzjy_recent(suffix: str) -> list[dict]:
    """该股最近大宗交易逐笔（含买卖双方营业部；机构专用方向显式标注）。"""
    dz = datasets.load_raw("dzjy_detail")
    if dz.empty or "symbol" not in dz.columns:
        return []
    sub = (
        dz[dz["symbol"] == suffix]
        .sort_values("trade_date", ascending=False, kind="stable")
        .head(_DZJY_LIMIT)
    )
    rows: list[dict] = []
    for _, r in sub.iterrows():
        buyer = datasets.s_or_none(r.get("seat_buyer")) or ""
        seller = datasets.s_or_none(r.get("seat_seller")) or ""
        rows.append(
            {
                "date": pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d"),
                "deal_price": n_or_none(r.get("deal_price")),
                "close": n_or_none(r.get("close")),
                "premium": n_or_none(r.get("premium")),
                "volume": n_or_none(r.get("volume")),
                "amount": n_or_none(r.get("amount")),
                "amount_ratio": n_or_none(r.get("amount_ratio")),
                "seat_buyer": buyer or None,
                "seat_seller": seller or None,
                "buyer_is_inst": "机构专用" in buyer,
                "seller_is_inst": "机构专用" in seller,
            }
        )
    return rows


def get_stock_detail(symbol: str) -> dict:
    """个股档案：榜上条目（可空）+ 事件时间线 + 北向季度序列 +
    最近龙虎榜席位构成 + 近期大宗明细 + 事件研究引用。

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

    timeline = datasets.event_timeline(suffix, limit=_TIMELINE_LIMIT)
    families = sorted(
        {
            _STUDY_FAMILIES[t["type"]]
            for t in timeline
            if t.get("type") in _STUDY_FAMILIES
        }
    )

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
        "timeline": timeline,
        "north": _north_series(suffix),
        "seats": _seat_breakdown(suffix),
        "dzjy": _dzjy_recent(suffix),
        "study_ref": {
            "families": families,
            "endpoint": "/api/v1/market-analysis/smart-money/event-study",
            "note": (
                "事件族 CAR 是族级统计口径（同类事件全部标的的当日池分位对比），"
                "不是个股预测"
            ),
        },
    }


def get_seats(
    window: str = "all",
    sort: str = "win_rate",
    seat_class: str | None = None,
    limit: int = SEATS_LIMIT_DEFAULT,
) -> dict:
    """席位胜率榜：seats.json 现裁（窗/类过滤 + 排序 + 分页）。

    窗内样本 < min_trades 的席位在 ``sort=win_rate`` 下**沉底**——1 笔命中
    的 100% 胜率不冒充「聪明席位」（n 随行返回由前端披露）；
    ``sort=net`` 用全史净买额（窗口无关的席位事实）。
    """
    if window not in SEATS_WINDOWS:
        raise ValueError("window_invalid")
    if sort not in SEATS_SORTS:
        raise ValueError("sort_invalid")
    if seat_class is not None and seat_class not in analytics.SEAT_CLASS_LABELS:
        raise ValueError("class_invalid")
    st = datasets.load_product_json("seats")
    if not isinstance(st, dict):
        raise SmartMoneyNotBuilt()
    min_trades = int(st.get("min_trades") or 0)
    rows = [
        r
        for r in (st.get("seats") or [])
        if seat_class is None or r.get("class") == seat_class
    ]

    def _key(r: dict) -> tuple:
        w = (r.get("windows") or {}).get(window) or {}
        if sort == "net":
            net = r.get("total_net_yi")
            return (net if net is not None else float("-inf"),)
        n = w.get("n") or 0
        wr = w.get("win_rate")
        return (n >= min_trades, wr if wr is not None else -1.0, n)

    rows.sort(key=_key, reverse=True)
    return {
        "as_of": st.get("as_of"),
        "generated_at": st.get("generated_at"),
        "horizon": st.get("horizon"),
        "min_trades": st.get("min_trades"),
        "windows": st.get("windows"),
        "window": window,
        "sort": sort,
        "class": seat_class,
        "baseline_win_rate": (st.get("baseline_win_rate") or {}).get(window),
        "n_events": st.get("n_events"),
        "classes": st.get("classes") or [],
        "total": len(rows),
        "items": rows[:limit],
    }


def get_track_record() -> dict:
    """历史战绩：Top-N 组合逐日收益/窗口汇总/分箱/滚动 IC/最近案例（原样透传）。"""
    tr = datasets.load_product_json("track_record")
    if not isinstance(tr, dict):
        raise SmartMoneyNotBuilt()
    return tr


def get_event_study() -> dict:
    """事件族 CAR：族内当日池分位 q5/q1 的超额路径（原样透传）。"""
    es = datasets.load_product_json("event_study")
    if not isinstance(es, dict):
        raise SmartMoneyNotBuilt()
    return es


def get_branches(window: int = 5) -> dict:
    """活跃营业部榜：窗内净买降序（原始层现算，两窗口同一条代码路径）。"""
    if window not in BRANCHES_WINDOWS:
        raise ValueError(f"window 只支持 {BRANCHES_WINDOWS}，收到 {window!r}")
    latest = _latest()
    as_of = latest.get("as_of")
    branches: list[dict] = []
    if as_of:
        end = pd.Timestamp(as_of)
        cal = datasets.trading_calendar(
            end - pd.Timedelta(days=_BRANCH_LOOKBACK_DAYS), end
        )
        branches = stats.branch_rank(
            datasets.load_raw("lhb_branch"),
            cal,
            end,
            window=window,
            top_n=BRANCHES_TOP_N,
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
