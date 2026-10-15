#!/usr/bin/env python3
"""聪明钱产物构建：sync（可选）→ 逐日 scores 分区 → latest/trends/meta.json。

单命令幂等（细案 docs/市场分析_聪明钱追踪与选股_细案_20261015.md §5.3）；
调度任务 ``smart_money_daily`` 串行调用（sync 失败但已有原始数据时仍构建，
并置 ``meta.partial=true`` 由页面横幅披露）。

口径要点：
- 交易日历 = daily_forward 分区日；评分日 = 池非空日（事件驱动，空池日跳过）
- 入柱 = evidence.json 的 admitted + direction（方向=h10 IC 符号；缺失回退
  signals.ADMITTED_PILLARS）——负向柱分位取 100−pct（HK 先例方向对齐）；
  两者皆空 / evidence 缺方向 → **拒绝出分**（先跑 smart_money_validate.py 出闸）
- ``--rebuild-days N``：重算最近 N 个交易日分区（日常 40）；0 = 全量（首建/回填）
- 展示口径（细案 §5.2）：symbol 输出**前缀式**；缺失一律 null（前端「—」），
  绝不补 0；前向收益封存列 fwd_ret_* 不进任何产物（本脚本根本不读）

用法（容器内）：
  docker exec -w /app quantmind python backend/scripts/market_analysis/build_smart_money.py
  ... --skip-sync --rebuild-days 0        # 首建/回填：全量出分
退码：0 成功；1 构建失败/数据不可用（不落盘）；2 入柱未裁定（拒绝出分）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.shared.smart_money import datasets, signals, stats  # noqa: E402
from backend.shared.stock_utils import StockCodeUtil  # noqa: E402
from backend.shared.utc_datetime import utc_now  # noqa: E402

log = logging.getLogger("build_smart_money")

#: 日常重算窗口（交易日）
DEFAULT_REBUILD_DAYS = 40
#: 增量采集下限（水位语义保证实际从 last−3 起）
SYNC_FLOOR = date(2024, 1, 1)
#: 柱暖机付给评分窗前的交易日数（> z20=20 与 survey 90 日历日≈63 交易日）
WARMUP_TRADING_DAYS = 90
#: 两融读取多付的日历天数（覆盖 calc 窗起点前的 z 暖机）
MARGIN_PAD_DAYS = 30
#: 趋势序列长度
TREND_DAYS_SHORT = 20
TREND_DAYS_MARGIN = 60
#: 原始表落后今天超过此天数 → partial（覆盖长假）
STALE_PARTIAL_DAYS = 15


class BuildRefused(Exception):
    """拒绝出分（入柱未裁定 / 数据不可用）——不落盘任何产物。"""


# ---------------------------------------------------------------- 入柱裁定


def resolve_admitted() -> tuple[tuple[str, int], ...]:
    """入柱（名, 方向±1）：evidence.json 优先（方向=h10 IC 符号），回退常量。

    方向决定分位是否取反（负向柱 100−pct，HK 先例）——evidence 缺 direction
    一律拒绝出分，绝不静默默认 +1（方向搞反 = 榜单调头）。
    """
    ev = datasets.load_product_json("evidence") or {}
    names = tuple(ev.get("admitted") or ())
    if names:
        unknown = [n for n in names if n not in signals.PILLAR_CANDIDATES]
        if unknown:
            raise BuildRefused(f"admitted 含未知柱 {unknown}（候选：{signals.PILLAR_CANDIDATES}）")
        dirs = {s.get("name"): s.get("direction") for s in (ev.get("signals") or [])}
        out: list[tuple[str, int]] = []
        for n in names:
            d = dirs.get(n)
            if d not in (1, -1):
                raise BuildRefused(
                    f"evidence 缺 {n} 的 direction（重跑 smart_money_validate.py 重新出闸）"
                )
            out.append((n, int(d)))
        return tuple(out)
    admitted = tuple(signals.ADMITTED_PILLARS)
    unknown = [n for n, _ in admitted if n not in signals.PILLAR_CANDIDATES]
    if unknown:
        raise BuildRefused(f"admitted 含未知柱 {unknown}（候选：{signals.PILLAR_CANDIDATES}）")
    bad = [n for n, d in admitted if d not in (1, -1)]
    if bad:
        raise BuildRefused(f"入柱方向非法 {bad}（须 +1/−1）")
    return admitted


# ---------------------------------------------------------------- 采集（可选）


def _default_sync() -> bool:
    """增量采集（水位自愈）；返回 False 表示有表异常（不抛出、不阻塞构建）。"""
    from backend.scripts import smart_money_sync as sms

    meta = sms.load_meta()
    ok = True
    for name in sms.DATASETS:
        try:
            sms.sync_dataset(name, SYNC_FLOOR, date.today(), meta)
        except Exception as exc:  # noqa: BLE001 —— 单表失败不拖垮其余表/构建
            log.exception("[sync:%s] 异常：%s", name, exc)
            ok = False
    sms.save_meta(meta)
    return ok


# ---------------------------------------------------------------- io


def _write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _write_json_atomic(name: str, payload: Any) -> Path:
    path = datasets.product_dir() / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    # allow_nan=False：NaN 漏入即崩溃（绝不产出非法 JSON）——清洗在 _n() 完成
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def _n(v: Any) -> float | None:
    """numpy/NaN → JSON 安全 float|None（缺失一律 None，绝不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _i(v: Any) -> int | None:
    f = _n(v)
    return None if f is None else int(f)


# ---------------------------------------------------------------- 构建核心


def _freshness() -> dict:
    def _iso(dt: str | None) -> str | None:
        if not dt:
            return None
        try:
            return pd.Timestamp(dt).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return None

    return {
        "sources": datasets.raw_freshness(),
        "margin_last": _iso(datasets.latest_margin_date()),
        "kline_last": _iso(datasets.latest_kline_date()),
    }


def _is_partial(sync_ok: bool) -> bool:
    if not sync_ok:
        return True
    fr = datasets.raw_freshness()
    today = pd.Timestamp.today().normalize()
    for name in datasets.RAW_DATASETS:
        last = fr.get(name, {}).get("last_date")
        if not last:
            return True
        if (today - pd.Timestamp(str(last))).days > STALE_PARTIAL_DAYS:
            return True
    return False


def _window_sum(raw: dict[str, pd.DataFrame], table: str, col: str, win: pd.DatetimeIndex) -> dict:
    df = raw.get(table)
    if df is None or df.empty or col not in df.columns:
        return {}
    sub = df[df["trade_date"].isin(win)]
    if sub.empty:
        return {}
    return sub.groupby("symbol")[col].sum().to_dict()


def _build_items(
    scores: pd.DataFrame,
    raw: dict[str, pd.DataFrame],
    cal: pd.DatetimeIndex,
    as_of: pd.Timestamp,
    margin: pd.DataFrame,
    admitted: tuple[tuple[str, int], ...],
    north_delta_pp: dict[str, float | None],
) -> list[dict]:
    """当日选股榜条目（score 降序；金额单位随柱原样，缺失 null）。"""
    pillar_names = [n for n, _ in admitted]
    names = datasets.name_map()
    industries = datasets.industry_map()
    pos = int(cal.searchsorted(as_of))
    win = cal[max(0, pos - signals.WINDOW_5D + 1) : pos + 1]
    inst5 = _window_sum(raw, "lhb_inst", "inst_net_buy", win)
    lhb5 = _window_sum(raw, "lhb_detail", "lhb_net_buy", win)
    mg5: dict = {}
    if margin is not None and not margin.empty:
        mw = margin[margin["trade_date"].isin(win)]
        if not mw.empty:
            mg5 = mw.groupby("symbol")["finance_net"].sum().to_dict()
    ev_map = stats.latest_event_map(raw, cal, as_of)

    def _label(series: pd.Series, sym: str) -> str | None:
        if series.empty or sym not in series.index:
            return None
        v = series.get(sym)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v)
        return None if s in ("nan", "NaT", "<NA>", "") else s

    items: list[dict] = []
    for _, r in scores.sort_values("score", ascending=False).iterrows():
        sym = str(r["symbol"])
        items.append(
            {
                "symbol": StockCodeUtil.to_prefix(sym) or sym,
                "name": _label(names, sym),
                "industry": _label(industries, sym),
                "score": _n(r["score"]),
                "covered": _i(r["covered"]),
                "n_pillars": _i(r["n_pillars"]),
                "pct": {n: _n(r.get(f"pct_{n}")) for n in pillar_names},
                "val": {n: _n(r.get(f"val_{n}")) for n in pillar_names},
                "inst_net_5d": _n(inst5.get(sym)),
                "lhb_net_5d": _n(lhb5.get(sym)),
                "margin_net_5d": _n(mg5.get(sym)),
                "north_q_delta_pp": _n(north_delta_pp.get(sym)),
                "latest_event": ev_map.get(sym),
            }
        )
    return items


def _overview(stats_df: pd.DataFrame, raw: dict[str, pd.DataFrame], cal: pd.DatetimeIndex, as_of: pd.Timestamp) -> dict:
    def _latest(col: str) -> tuple[str | None, float | None]:
        if col not in stats_df.columns:
            return None, None
        s = stats_df[col].dropna()
        if s.empty:
            return None, None
        return s.index[-1].strftime("%Y-%m-%d"), _n(s.iloc[-1])

    lhb_date, lhb_n = _latest("lhb_n_stocks")
    _, lhb_net = _latest("lhb_net_buy_yi")
    tail20 = stats_df.tail(TREND_DAYS_SHORT)
    inst_date, inst_n = _latest("inst_n_stocks")
    _, inst_net = _latest("inst_net_buy_yi")
    dz_date, dz_amt = _latest("dzjy_amount_yi")
    _, dz_prem = _latest("dzjy_premium_share")
    sv_date, sv_n = _latest("survey_n_stocks")
    _, sv_inst = _latest("survey_n_inst")
    mg_date, mg_net5 = _latest("margin_net_5d_yi")
    _, mg_bal = _latest("margin_bal_yi")

    lhb20_n = tail20["lhb_n_stocks"].dropna() if "lhb_n_stocks" in tail20 else pd.Series(dtype=float)
    lhb20_net = tail20["lhb_net_buy_yi"].dropna() if "lhb_net_buy_yi" in tail20 else pd.Series(dtype=float)

    return {
        "lhb": {
            "date": lhb_date,
            "n_stocks": _i(lhb_n),
            "net_buy_yi": lhb_net,
            "n20d_avg": _n(lhb20_n.mean()) if not lhb20_n.empty else None,
            "net20d_sum_yi": _n(lhb20_net.sum()) if not lhb20_net.empty else None,
        },
        "inst": {"date": inst_date, "n_stocks": _i(inst_n), "net_buy_yi": inst_net},
        "dzjy": {"date": dz_date, "amount_yi": dz_amt, "premium_share": dz_prem},
        "survey": {"date": sv_date, "n_stocks": _i(sv_n), "n_inst": _n(sv_inst)},
        "margin": {"date": mg_date, "net_5d_yi": mg_net5, "balance_yi": mg_bal},
        "north": stats.north_summary(datasets.north_delta()),
        "branches_top": stats.branch_rank(
            raw.get("lhb_branch"), cal, as_of, window=signals.POOL_WINDOW, top_n=10
        ),
    }


def _trends(stats_df: pd.DataFrame) -> dict:
    def _series(cols: list[str], n: int) -> list[dict]:
        sub = stats_df.tail(n)
        out: list[dict] = []
        for idx, row in sub.iterrows():
            rec: dict = {"date": idx.strftime("%Y-%m-%d")}
            rec.update({c: _n(row.get(c)) for c in cols})
            out.append(rec)
        return out

    return {
        "lhb": _series(["lhb_n_stocks", "lhb_net_buy_yi"], TREND_DAYS_SHORT),
        "inst": _series(["inst_n_stocks", "inst_net_buy_yi"], TREND_DAYS_SHORT),
        "dzjy": _series(["dzjy_amount_yi", "dzjy_premium_share"], TREND_DAYS_SHORT),
        "survey": _series(["survey_n_stocks", "survey_n_inst"], TREND_DAYS_SHORT),
        "margin": _series(["margin_net_5d_yi", "margin_bal_yi"], TREND_DAYS_MARGIN),
    }


def build(
    rebuild_days: int = DEFAULT_REBUILD_DAYS,
    run_sync: Callable[[], bool] | None = None,
) -> dict:
    """构建全链；返回 meta dict。run_sync=None → 不采集（纯构建/测试）。"""
    sync_ok = True
    if run_sync is not None:
        try:
            sync_ok = bool(run_sync())
        except Exception:  # noqa: BLE001 —— 采集异常不阻塞既有数据构建
            log.exception("sync 异常（用既有数据构建，置 partial）")
            sync_ok = False

    admitted = resolve_admitted()
    if not admitted:
        raise BuildRefused(
            "入柱未裁定（evidence.json.admitted 与 signals.ADMITTED_PILLARS 均为空）"
            "——先跑 smart_money_validate.py 出闸"
        )
    name_list = [n for n, _ in admitted]

    raw = {n: datasets.load_raw(n) for n in datasets.RAW_DATASETS}
    floors = [
        df["trade_date"].min()
        for n, df in raw.items()
        if n != "lhb_branch" and not df.empty and "trade_date" in df
    ]
    if not floors:
        raise BuildRefused("原始表全空——先跑 smart_money_sync.py 采集")
    floor = min(floors)

    cal = datasets.trading_calendar(floor, date.today())
    if len(cal) == 0:
        raise BuildRefused("行情日历为空（daily_forward 无分区）——先同步行情数据")
    if rebuild_days and rebuild_days > 0:
        score_cal = cal[-rebuild_days:]
    else:
        score_cal = cal
    calc_cal = cal if rebuild_days in (0, None) else cal[-(len(score_cal) + WARMUP_TRADING_DAYS):]
    if len(calc_cal) < len(score_cal):  # 日历本身不足（测试/新库）
        calc_cal = cal

    margin = datasets.margin_daily(calc_cal[0] - pd.Timedelta(days=MARGIN_PAD_DAYS))

    pool = signals.compute_pool(calc_cal, raw, margin)
    pillars = signals.compute_pillars(calc_cal, raw, margin, names=name_list)

    n_written = 0
    last_scores: pd.DataFrame | None = None
    as_of: pd.Timestamp | None = None
    for d in score_cal:
        sc = signals.compute_scores(d, pool, pillars, admitted)
        if sc.empty:
            continue
        _write_parquet_atomic(
            sc, datasets.product_dir() / "scores" / f"dt={d.strftime('%Y%m%d')}" / "data.parquet"
        )
        n_written += 1
        last_scores, as_of = sc, d
    if last_scores is None or as_of is None:
        raise BuildRefused(f"评分窗 {score_cal[0]:%Y-%m-%d}~{score_cal[-1]:%Y-%m-%d} 内池全空，无评分输出")

    # ---- 展示层（stats 窗 = 趋势窗 + 5 日短窗暖机）
    stats_cal = cal[-(max(TREND_DAYS_MARGIN, TREND_DAYS_SHORT) + signals.WINDOW_5D):]
    stats_df = stats.daily_market_stats(stats_cal, raw, margin)
    north_pp: dict[str, float | None] = {}
    nd = datasets.north_delta()
    if not nd.empty:
        north_pp = {
            str(r["symbol"]): _n(r["delta"] * 100.0) for _, r in nd.iterrows()
        }

    items = _build_items(last_scores, raw, calc_cal, as_of, margin, admitted, north_pp)
    covered_mean = _n(last_scores["covered"].mean())
    partial = _is_partial(sync_ok)
    freshness = _freshness()

    latest = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "generated_at": utc_now().isoformat(),
        "admitted": name_list,
        "pillars": [
            {
                "name": n,
                "label": signals.PILLAR_META[n]["label"],
                "unit": signals.PILLAR_META[n]["unit"],
                "direction": d,
            }
            for n, d in admitted
        ],
        "summary": {"pool_size": int(len(last_scores)), "covered_mean": covered_mean},
        "overview": _overview(stats_df, raw, calc_cal, as_of),
        "items": items,
        "freshness": freshness,
        "partial": partial,
    }
    trends = {"as_of": as_of.strftime("%Y-%m-%d"), "generated_at": latest["generated_at"], **_trends(stats_df)}

    meta = {
        "generated_at": latest["generated_at"],
        "as_of": latest["as_of"],
        "admitted": name_list,
        "rebuild_days": rebuild_days,
        "score_dates_written": n_written,
        "score_dates_total": len(datasets.score_dates()),
        "window": {"calendar_start": cal[0].strftime("%Y-%m-%d"), "calendar_end": cal[-1].strftime("%Y-%m-%d")},
        "sync_ok": sync_ok,
        "partial": partial,
        "freshness": freshness,
    }

    _write_json_atomic("latest", latest)
    _write_json_atomic("trends", trends)
    _write_json_atomic("meta", meta)
    log.info(
        "构建完成：as_of=%s 评分日=%d（分区总 %d）池=%d covered均值=%s partial=%s",
        meta["as_of"], n_written, meta["score_dates_total"],
        latest["summary"]["pool_size"], covered_mean, partial,
    )
    return meta


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="聪明钱产物构建")
    parser.add_argument(
        "--rebuild-days", type=int, default=DEFAULT_REBUILD_DAYS,
        help="重算最近 N 个交易日分区（默认 40；0=全量，首建/回填用）",
    )
    parser.add_argument("--skip-sync", action="store_true", help="跳过采集（纯构建）")
    args = parser.parse_args()
    if args.rebuild_days < 0:
        log.error("--rebuild-days 不可为负")
        return 2
    try:
        build(rebuild_days=args.rebuild_days, run_sync=None if args.skip_sync else _default_sync)
    except BuildRefused as exc:
        log.error("构建拒绝：%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        log.exception("构建失败：%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
