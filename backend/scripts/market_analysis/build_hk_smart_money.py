#!/usr/bin/env python3
"""港股聪明钱产物构建：增量事件 → 逐日复合分 → latest/consensus/trends/meta。

细案 docs/港股市场分析_聪明钱深度分析_细案_20261016.md §3.5/§3.6/§4：

- 入柱 = ``evidence.json.admitted``（方向 = h10 IC 符号；负向柱分位取 100−pct）
  —— **零入柱 → evidence-only 模式（不拒绝）**：共识榜空态 +
  ``evidence_only=true``，类型效力表照常交付；
- **席位统计 v2（2026-10-16 起）由本脚本每日生成**：seats.parquet（29 列）/
  seat_monthly.parquet / seat_symbols.parquet / seat_persistence.json——
  读侧每日消费的盘点数据不挂在校准节奏上（原在 validate，暗坑已修）；
- ``evidence.json`` 缺失 / admitted 含未知键 / 缺 direction → 拒绝出分（退码 2，
  先跑 ``hk_smart_money_validate.py``）；
- ``--rebuild-days N`` 同时作用于事件仓增量窗与评分重算窗（0 = 全量，首建/回填；
  日常默认 40）；``--skip-events`` 供纯出分重跑（要求事件仓非空）；
- 本特性**无独立采集脚本**——原始面（ccass_top50 / hsgt_south / daily_forward）
  由港股市场同步链维护，构建只读；产物原子写（同日重跑覆盖）；
- 展示口径：symbol 为港股 4 位 + .HK（0700.HK，与 quant 全链一致）；缺失一律
  null（前端「—」），绝不补 0；事后收益封存列不读不写。

用法（项目根）：
  .venv/bin/python backend/scripts/market_analysis/build_hk_smart_money.py
  .venv/bin/python backend/scripts/market_analysis/build_hk_smart_money.py --rebuild-days 0
退码：0 成功；1 构建失败/数据不可用（不落盘）；2 拒绝出分（证据缺失/非法）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.services.api.market_analysis_hk import (  # noqa: E402
    institutional_classifier as ic,
)
from backend.shared.hk_smart_money import (  # noqa: E402
    CATEGORIES,
    CCASS_CATEGORIES,
    CCASS_SIGNALS,
    CONSENSUS_TOP_N,
    SIGNAL_KEY_SEP,
    SOUTH_SIGNALS,
    STALE_PARTIAL_TRADE_DAYS,
    TREND_DAYS,
)
from backend.shared.hk_smart_money import datasets as ds  # noqa: E402
from backend.shared.hk_smart_money import events as ev_mod  # noqa: E402
from backend.shared.hk_smart_money import stats  # noqa: E402
from backend.shared.utc_datetime import utc_now  # noqa: E402

log = logging.getLogger("build_hk_smart_money")

#: 日常重算窗口（交易日）
DEFAULT_REBUILD_DAYS = 40
#: overview 排除计数窗口（交易日）
COUNTS_WINDOW_DAYS = 30


class BuildRefused(Exception):
    """拒绝出分（证据缺失/非法）——不落盘任何产物。"""


# ---------------------------------------------------------------- 入柱裁定


def _allowed_keys() -> set[str]:
    keys = {f"{c}{SIGNAL_KEY_SEP}{v}" for c in CCASS_CATEGORIES for v in CCASS_SIGNALS}
    keys |= {f"southbound{SIGNAL_KEY_SEP}{v}" for v in SOUTH_SIGNALS}
    return keys


def resolve_admitted() -> tuple[tuple[str, int], ...]:
    """入柱（信号键, 方向±1）：evidence.json 唯一来源；空 admitted = evidence-only。

    方向决定分位是否取反——evidence 缺 direction 一律拒绝出分，绝不静默 +1
    （方向搞反 = 榜单调头）。
    """
    ev = ds.load_product_json("evidence")
    if ev is None:
        raise BuildRefused(
            "证据未裁定（无 evidence.json）——先跑 "
            "hk_smart_money_validate.py --no-composite"
        )
    names = tuple(ev.get("admitted") or ())
    if not names:
        return ()
    allowed = _allowed_keys()
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise BuildRefused(f"admitted 含未知信号 {unknown}（候选 {sorted(allowed)}）")
    dirs = {s.get("name"): s.get("direction") for s in (ev.get("signals") or [])}
    out: list[tuple[str, int]] = []
    for n in names:
        d = dirs.get(n)
        if d not in (1, -1):
            raise BuildRefused(
                f"evidence 缺 {n} 的 direction（重跑 hk_smart_money_validate.py）"
            )
        out.append((n, int(d)))
    return tuple(out)


# ---------------------------------------------------------------- 小工具


def _iso(dt: str | None) -> str | None:
    if not dt:
        return None
    try:
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _r(v: Any, nd: int = 2) -> float | None:
    """numpy/NaN → JSON 安全 float|None（缺失一律 None，绝不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _i(v: Any) -> int | None:
    f = _r(v, 0)
    return None if f is None else int(f)


def _name_map() -> dict[str, str]:
    """最新 ccass/south 分区的 code → 中文名（缺失不猜）。"""
    out: dict[str, str] = {}
    cc = ds.ccass_dates()
    if cc:
        df = ds.load_ccass(cc[-1])
        for sym, name in zip(df["stock_code"], df["stock_name"], strict=True):
            if isinstance(name, str) and name.strip():
                out[sym] = name.strip()
    so = ds.south_dates()
    if so:
        df = ds.load_south(so[-1])
        for sym, name in zip(df["symbol"], df["stock_name"], strict=True):
            if isinstance(name, str) and name.strip():
                out.setdefault(sym, name.strip())
    return out


def _freshness(calendar: pd.DatetimeIndex, as_of: str) -> dict[str, Any]:
    """新鲜度：滞后 = as_of（最新事件日）之后的行情交易日数。

    休市日（CCASS 有分区、行情无该日 → ``days_skipped_unpriced``）是港股日历的
    结构性缺口（元旦/春节/清明/端午样例 8 日），不触发 partial——只有行情交易日
    已到、事件却没跟上（CCASS 同步或行情管线滞后）才算「披露滞后」。
    """
    c_last = ds.latest_ccass_date()
    k_last = ds.latest_kline_date()
    gap: int | None = int((calendar > pd.Timestamp(as_of)).sum()) if k_last else None
    partial = bool(gap is not None and gap > STALE_PARTIAL_TRADE_DAYS)
    return {
        "ccass_date": _iso(c_last),
        "south_date": _iso(ds.latest_south_date()),
        "kline_date": _iso(k_last),
        "ccass_lag_days": gap,
        "partial": partial,
    }


def _counts_window(edates: list[str]) -> tuple[dict[str, Any], dict[str, dict]]:
    """逐日 counts.json 读取（趋势窗共享）；返回 (30 日汇总, {dt: counts})。"""
    win = edates[-max(COUNTS_WINDOW_DAYS, TREND_DAYS) :]
    cmap = {d: ev_mod.read_counts(d) for d in win}
    use30 = win[-COUNTS_WINDOW_DAYS:]
    agg: dict[str, Any] = {
        "days": len(use30),
        "events": 0,
        "transfer_suspect": 0,
        "first_seen": 0,
        "exit_boundary": 0,
    }
    for d in use30:
        c = cmap.get(d) or {}
        agg["events"] += int(c.get("events_n") or 0)
        agg["transfer_suspect"] += int(c.get("suspect_n") or 0)
        agg["first_seen"] += int(c.get("first_seen_n") or 0)
        agg["exit_boundary"] += int(c.get("exit_n") or 0)
    return agg, cmap


def _totals_lookup(win: list[str]) -> dict[tuple[str, str], tuple[float, int]]:
    flows = ds.load_flows(dates=win)
    totals = stats.daily_category_totals(flows)
    return {
        (str(r["dt"]), str(r["category"])): (float(r["net_value"]), int(r["n_symbols"]))
        for _, r in totals.iterrows()
    }


# ---------------------------------------------------------------- 展示面


def _category_panel(
    close: pd.DataFrame,
    edates: list[str],
    cmap: dict[str, dict],
    cat_skill: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """6 类型面板：持仓市值（亿元）+ 5d/20d 净额（亿元）+ 净变动票数。

    南向净额只累计「有披露」的日子（缺披露日绝不伪造 0，days 并列披露）。
    v2 加类别方向技能（skill_bp/vw_skill_bp/n_lead_events；南向无事件仓 → null）。
    """
    last = edates[-1]
    ts = pd.Timestamp(last)
    close_row = close.loc[ts] if ts in close.index else None
    win20, win5 = edates[-20:], edates[-5:]
    lookup = _totals_lookup(win20)
    hold_ccass = ds.load_holdings(dates=[last])
    s_date = ds.latest_south_date()
    hold_south = ds.load_south(s_date) if s_date else pd.DataFrame()
    s_ts = pd.Timestamp(s_date) if s_date else None
    south_close = (
        close.loc[s_ts] if s_ts is not None and s_ts in close.index else close_row
    )

    def _days(cat: str, win: list[str]) -> list[str]:
        if cat != "southbound":
            return win
        return [d for d in win if (cmap.get(d) or {}).get("south_available")]

    rows: list[dict[str, Any]] = []
    for cat in CATEGORIES:
        if cat == "southbound":
            qty = hold_south.loc[
                hold_south["holding_quantity"] > 0, ["symbol", "holding_quantity"]
            ].set_index("symbol")["holding_quantity"]
            row_close, holding_date = south_close, _iso(s_date)
        else:
            sub = hold_ccass[
                (hold_ccass["category"] == cat) & (hold_ccass["qty_sum"] > 0)
            ]
            qty = sub.set_index("symbol")["qty_sum"]
            row_close, holding_date = close_row, _iso(last)
        holding_yi = None
        if row_close is not None and len(qty):
            px = row_close.reindex(qty.index)
            val = (qty.astype("float64") * px).dropna()
            if len(val):
                holding_yi = _r(val.sum() / 1e8, 1)
        days5, days20 = _days(cat, win5), _days(cat, win20)
        net5 = (
            _r(sum(lookup.get((d, cat), (0.0, 0))[0] for d in days5) / 1e8, 2)
            if days5
            else None
        )
        net20 = (
            _r(sum(lookup.get((d, cat), (0.0, 0))[0] for d in days20) / 1e8, 2)
            if days20
            else None
        )
        sk = (cat_skill or {}).get(cat) or {}
        rows.append(
            {
                "category": cat,
                "label": ic.category_label(cat),
                "holding_yi": holding_yi,
                "holding_date": holding_date,
                "n_symbols": int(len(qty)),
                "net5_yi": net5,
                "net5_days": len(days5),
                "net20_yi": net20,
                "net20_days": len(days20),
                "skill_bp": sk.get("skill_bp"),
                "vw_skill_bp": sk.get("vw_skill_bp"),
                "n_lead_events": sk.get("n_lead_events"),
            }
        )
    return rows


def _trends(
    edates: list[str], cmap: dict[str, dict], generated_at: str
) -> dict[str, Any]:
    """60 交易日 × 6 类型净流序列（亿元）+ 逐日覆盖面（净变动票数）。"""
    win = edates[-TREND_DAYS:]
    lookup = _totals_lookup(win)
    categories: dict[str, list[dict[str, Any]]] = {}
    for cat in CATEGORIES:
        series: list[dict[str, Any]] = []
        for d in win:
            if cat == "southbound" and not (cmap.get(d) or {}).get("south_available"):
                series.append({"date": _iso(d), "net_yi": None, "n_symbols": None})
                continue
            nv, ns = lookup.get((d, cat), (0.0, 0))
            series.append({"date": _iso(d), "net_yi": _r(nv / 1e8), "n_symbols": ns})
        categories[cat] = series
    return {
        "as_of": _iso(win[-1]) if win else None,
        "generated_at": generated_at,
        "window": len(win),
        "categories": categories,
    }


def _consensus(
    spec: tuple[tuple[str, int], ...], as_of: str | None, generated_at: str
) -> dict[str, Any]:
    """最新共识榜（top {CONSENSUS_TOP_N} + 分柱明细）；零入柱 → 诚实空态。"""
    base: dict[str, Any] = {
        "generated_at": generated_at,
        "adopted": [n for n, _ in spec],
        "directions": dict(spec),
        "evidence_only": not spec,
        "items": [],
    }
    if not spec:
        return {**base, "as_of": as_of}
    sdates = ds.score_dates()
    if not sdates:
        return {**base, "as_of": as_of}
    last = sdates[-1]
    sc = ds.load_scores(last)
    if sc is None or sc.empty:
        return {**base, "as_of": as_of}
    names = _name_map()
    keys = [n for n, _ in spec]
    sc = (
        sc[sc["score"].notna()]
        .sort_values("score", ascending=False)
        .head(CONSENSUS_TOP_N)
    )
    items: list[dict[str, Any]] = []
    for _, r in sc.iterrows():
        sym = str(r["symbol"])
        items.append(
            {
                "symbol": sym,
                "name": names.get(sym),
                "score": _r(r["score"]),
                "covered": _i(r["covered"]),
                "n_pillars": _i(r["n_pillars"]),
                "pct": {k: _r(r.get(f"pct_{k}"), 1) for k in keys},
                "val": {k: _r(r.get(f"val_{k}"), 6) for k in keys},
            }
        )
    return {**base, "as_of": _iso(last), "items": items}


# ---------------------------------------------------------------- 构建核心


def build(rebuild_days: int = DEFAULT_REBUILD_DAYS, skip_events: bool = False) -> dict:
    """构建全链；返回 meta dict。evidence-only（零入柱）不拒绝、照常出产物。"""
    t0 = time.monotonic()
    spec = resolve_admitted()
    evidence_only = not spec
    if evidence_only:
        log.warning("零入柱（evidence-only 模式）：共识榜空态，仅出事件/趋势/新鲜度面")

    events_stats: dict | None = None
    if skip_events:
        if not ds.event_dates():
            raise RuntimeError(
                "事件仓为空，--skip-events 不可用（先跑一次不带该 flag）"
            )
        log.info("事件仓：--skip-events，使用既有 %d 个分区", len(ds.event_dates()))
    else:
        events_stats = ev_mod.build_events(rebuild_days=rebuild_days)
        log.info("事件仓：%s", events_stats)

    edates = list(ds.event_dates())
    if not edates:
        raise RuntimeError(
            "事件仓为空（全部交易日无行情？）——先核对 ccass/daily_forward"
        )
    cc = ds.ccass_dates()
    kd = [d for d in ds.kline_dates() if d >= cc[0]]
    close, amount = ds.load_prices(kd)
    if close.empty:
        raise RuntimeError("daily_forward 无可用分区（ccass 覆盖段内）")
    calendar = close.index

    # 席位统计 v2（v2 起由 build 每日生成；validate 只留信号裁定——席位榜是
    # 每日消费的盘点数据，不能挂在校准节奏上）。列裁剪控内存（细案 §3.2）。
    seat_cols = (
        "dt",
        "symbol",
        "stock_name",  # seat_symbols 标的聚合展示名（缺列即 KeyError）
        "participant_id",
        "participant_name",
        "category",
        "kind",
        "direction",
        "delta_value",
        "episode_lead",
        "transfer_suspect",
        "fwd_exc_5",
        "fwd_exc_10",
        "fwd_exc_20",
    )
    seat_events = ds.load_events(columns=seat_cols)
    seats = stats.seat_metrics(seat_events, calendar)
    if not seats.empty:
        ds.write_parquet_atomic(seats, ds.product_dir() / "seats.parquet")
    seat_mon = stats.seat_monthly(seat_events)
    if not seat_mon.empty:
        ds.write_parquet_atomic(seat_mon, ds.product_dir() / "seat_monthly.parquet")
    seat_sym = stats.seat_symbols(seat_events)
    if not seat_sym.empty:
        ds.write_parquet_atomic(seat_sym, ds.product_dir() / "seat_symbols.parquet")
    ds.write_json_atomic("seat_persistence", stats.seat_persistence(seat_events))
    cat_skill = stats.category_skill(seat_events)
    log.info(
        "席位统计 v2：%d 席 × 3 窗 / 月度 %d 行 / 标的聚合 %d 行 / 类别技能 %d 类",
        seats["participant_id"].nunique() if not seats.empty else 0,
        len(seat_mon),
        len(seat_sym),
        len(cat_skill),
    )

    n_written = 0
    if spec:
        panels = stats.compute_all_signals(close, amount)
        score_cal = calendar if rebuild_days <= 0 else calendar[-rebuild_days:]
        for d in score_cal:
            sc = stats.compute_scores(d, panels, spec)
            if sc.empty:
                continue
            ds.write_parquet_atomic(
                sc,
                ds.product_dir()
                / "scores"
                / f"dt={d.strftime('%Y%m%d')}"
                / "data.parquet",
            )
            n_written += 1
        if n_written == 0:
            raise BuildRefused("评分窗内无有效输出（admitted 信号面板全空？）")
        log.info("复合分：写入 %d 个评分日", n_written)

    generated_at = utc_now().isoformat()
    as_of = _iso(edates[-1])
    fresh = _freshness(calendar, as_of)
    counts30, cmap = _counts_window(edates)

    latest: dict[str, Any] = {
        "version": 1,
        "as_of": as_of,
        "generated_at": generated_at,
        "admitted": [n for n, _ in spec],
        "directions": dict(spec),
        "evidence_only": evidence_only,
        "freshness": fresh,
        "partial": fresh["partial"],
        "counts_30d": counts30,
        "categories": _category_panel(close, edates, cmap, cat_skill),
        "summary": {"scores_date": None, "pool_size": None, "covered_mean": None},
    }
    sdates = ds.score_dates()
    if sdates:
        sc_last = ds.load_scores(sdates[-1])
        if sc_last is not None and not sc_last.empty:
            latest["summary"] = {
                "scores_date": _iso(sdates[-1]),
                "pool_size": int(len(sc_last)),
                "covered_mean": _r(sc_last["covered"].mean(), 2),
            }

    trends = _trends(edates, cmap, generated_at)
    consensus = _consensus(spec, as_of, generated_at)
    meta = {
        "version": 1,
        "generated_at": generated_at,
        "as_of": as_of,
        "admitted": [n for n, _ in spec],
        "evidence_only": evidence_only,
        "rebuild_days": rebuild_days,
        "events": events_stats,
        "scores": {"written": n_written, "total_dates": len(sdates)},
        "window": {
            "calendar_start": calendar[0].strftime("%Y-%m-%d"),
            "calendar_end": calendar[-1].strftime("%Y-%m-%d"),
        },
        "freshness": fresh,
        "partial": fresh["partial"],
        "duration_s": round(time.monotonic() - t0, 1),
    }

    ds.write_json_atomic("latest", latest)
    ds.write_json_atomic("trends", trends)
    ds.write_json_atomic("consensus", consensus)
    ds.write_json_atomic("meta", meta)
    log.info(
        "构建完成：as_of=%s 入柱=%d 评分日=%d/%d 共识条=%d partial=%s（%.1fs）",
        as_of,
        len(spec),
        n_written,
        len(sdates),
        len(consensus["items"]),
        fresh["partial"],
        meta["duration_s"],
    )
    return meta


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="港股聪明钱产物构建")
    parser.add_argument(
        "--rebuild-days",
        type=int,
        default=DEFAULT_REBUILD_DAYS,
        help="事件增量窗与评分重算窗（交易日；默认 40；0=全量，首建/回填用）",
    )
    parser.add_argument(
        "--skip-events", action="store_true", help="跳过事件仓刷新（纯出分重跑）"
    )
    args = parser.parse_args()
    if args.rebuild_days < 0:
        log.error("--rebuild-days 不可为负")
        return 2
    try:
        build(rebuild_days=args.rebuild_days, skip_events=args.skip_events)
    except BuildRefused as exc:
        log.error("构建拒绝：%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        log.exception("构建失败：%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
