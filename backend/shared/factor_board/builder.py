"""因子概率板：编排（候选池 → 自算 IC → 入选 → 合成 → 共振 → 产物 dict）。

模块全部纯函数/装载器；本文件只做流程编排与 meta/质检组包。
"""

from __future__ import annotations

import logging
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

from backend.shared.factor_board import board_io as IO
from backend.shared.factor_board import composite as CP
from backend.shared.factor_board import config as C
from backend.shared.factor_board import sectors as S
from backend.shared.factor_board import selection as SEL
from backend.shared.factor_board import signals as SIG
from backend.shared.utc_datetime import to_utc_iso, utc_now

log = logging.getLogger("factor_board.builder")


class BuildError(RuntimeError):
    """硬门不过 → 不落盘（CLI 退码 1）。"""


def build_board(
    quantdb_root: Path,
    *,
    model_date: str | None = None,
    signals_csv: str | Path | None = None,
    today: _date | None = None,
) -> dict:
    today = today or _date.today()
    warnings: list[str] = []

    # ── 1. 候选池与数据窗 ──────────────────────────────────────────
    cand, cand_fp = SEL.load_candidates(quantdb_root)
    dates = CP.list_factor_dates(quantdb_root)[-C.WINDOW_DATES :]
    if len(dates) < C.EVAL_DAYS + 6:
        raise BuildError(f"因子分区不足：仅 {len(dates)} 日")
    trade_date = dates[-1]
    close = CP.load_close_matrix(quantdb_root, dates)
    if close.empty:
        raise BuildError("qfq 收盘矩阵为空（daily_forward 缺失）")

    nonpos = int((close <= 0).to_numpy().sum())
    total_cells = int(close.notna().to_numpy().sum())
    if total_cells and nonpos / max(total_cells, 1) > C.CLOSE_NONPOS_MAX_FRAC:
        warnings.append(f"close 非正值 {nonpos}/{total_cells} 超阈值")
    stale_days = (today - pd.Timestamp(trade_date).date()).days
    if stale_days > C.FACTOR_STALE_DAYS:
        warnings.append(
            f"因子最新分区 {trade_date} 距今 {stale_days} 天（>{C.FACTOR_STALE_DAYS}）"
        )

    cand, dropped = SEL.drop_missing_columns(cand, quantdb_root, trade_date)
    if dropped:
        warnings.append(
            f"候选剔除 {sum(len(v) for v in dropped.values())} 个：{sorted(dropped)[:4]}"
        )

    # ── 2. 自算近效 IC → 闸门 → 入选 ──────────────────────────────
    eval_idx = range(len(dates) - C.EVAL_DAYS - 1, len(dates) - 5)
    fwd5 = CP.fwd_ranks(close, dates, eval_idx, 5)
    ic_table = SEL.compute_ic_table(quantdb_root, cand, list(fwd5.keys()), fwd5)
    stats = SEL.summarize_ic(ic_table, cand)
    if stats.empty:
        raise BuildError("无候选因子可评估（IC 全空）")
    sel, gate_info = SEL.select_factors(stats)
    if len(sel) < C.MIN_SELECTED:
        raise BuildError(f"入选因子 {len(sel)} < 硬门 {C.MIN_SELECTED}")
    if gate_info["degraded"]:
        warnings.append(f"胜率门槛降级至 {C.DEGRADE_WIN_RATE}（过闸不足）")

    # ── 3. 合成截面 + 持有期画像 ──────────────────────────────────
    comps = CP.build_composite_history(quantdb_root, sel, dates, len(dates) - 70)
    comp_today = comps.get(trade_date, pd.Series(dtype=float)).dropna()
    if len(comp_today) < C.MIN_COMPOSITE_TODAY:
        raise BuildError(
            f"当日合成覆盖 {len(comp_today)} < 硬门 {C.MIN_COMPOSITE_TODAY}"
        )
    hz_idx = range(len(dates) - C.HORIZON_WINDOW, len(dates) - 20)
    fwd_by_h = {h: CP.fwd_ranks(close, dates, hz_idx, h) for h in C.HORIZON_DAYS}
    horizon = CP.horizon_table(
        quantdb_root, sel, dates, len(dates) - C.HORIZON_WINDOW, fwd_by_h
    )

    # ── 4. 模型信号 + 个股/板块共振 ───────────────────────────────
    mdl, mdl_meta = SIG.load_model_signals(csv_path=signals_csv, model_date=model_date)
    if mdl_meta["stale"]:
        warnings.append(f"模型信号非当日（{mdl_meta['trade_date']}，stale=true）")
    if mdl_meta["n"] < C.MODEL_MIN_ROWS:
        warnings.append(
            f"模型信号仅 {mdl_meta['n']} 行（<{C.MODEL_MIN_ROWS}，疑似残缺 run）"
        )

    name_of, st_of = SIG.load_instruments(quantdb_root)
    members = SIG.load_sector_members(quantdb_root)
    ind2 = (
        members[members["SectorType"] == "行业板块(二级)"]
        .drop_duplicates("Symbol")
        .set_index("Symbol")["SectorName"]
    )

    stock = pd.DataFrame({"composite": comp_today})
    stock["name"] = name_of.reindex(stock.index)
    stock["st"] = st_of.reindex(stock.index).fillna(False)
    stock["mdl_rank"] = mdl["rank_pct"].reindex(stock.index)
    stock["sector_l2"] = ind2.reindex(stock.index)
    cov_n = int(stock["mdl_rank"].notna().sum())
    if cov_n < C.MIN_MODEL_INTERSECTION:
        warnings.append(f"因子×模型交集仅 {cov_n} 只（<{C.MIN_MODEL_INTERSECTION}）")

    q = S.quadrant_frame(stock)
    rho = S.spearman(q["composite"], q["mdl_rank"])

    sectors_payload: dict = {}
    for stype in C.SECTOR_TYPES:
        payload = IO.sector_payload(stock, members, stype)
        key = C.SECTOR_KEY[stype]
        if payload is None:
            warnings.append(f"板块表缺失：{stype}")
            payload = {"q67": None, "q33": None, "items": []}
        sectors_payload[key] = payload

    # ── 5. 验证（近窗口径自洽；样本内注记） ───────────────────────
    val = _validate(comps, fwd5, members, dates)

    # ── 6. 组包 ───────────────────────────────────────────────────
    decision_date = str(pd.Timestamp(mdl_meta["trade_date"]).date())
    trade_date_iso = str(pd.Timestamp(trade_date).date())  # 20261009 → 2026-10-09
    board = {
        "schema_version": C.SCHEMA_VERSION,
        "meta": {
            "decision_date": decision_date,
            "trade_date": trade_date_iso,
            "generated_at": to_utc_iso(utc_now()),
            "data_window": [dates[0], dates[-1]],
            "eval_days": int(len(fwd5)),
            "model": mdl_meta,
            "inputs": {**cand_fp, "dropped": {k: len(v) for k, v in dropped.items()}},
            "thresholds": C.thresholds_snapshot(),
            "coverage": {
                "pool": int(len(cand)),
                "evaluated": gate_info["evaluated"],
                "gated": gate_info["gated"],
                "gated_long": gate_info["gated_long"],
                "gated_short": gate_info["gated_short"],
                "selected": int(len(sel)),
                "families": gate_info["families"],
                "composite_n": int(len(comp_today)),
                "model_cov_n": cov_n,
                "close_nonpos": nonpos,
            },
            "warnings": warnings,
        },
        "factors": IO.factor_payload(sel, horizon),
        "horizon": IO.horizon_payload(horizon, sel),
        "validation": val,
        "resonance": {
            "spearman": IO._clean(rho),
            "n": int(len(q)),
            "quadrants": S.quadrant_counts(q),
        },
        "sectors": sectors_payload,
        "stocks": S.stock_lists(q),
    }
    return board


def _validate(
    comps: dict[str, pd.Series],
    fwd5: dict[str, pd.Series],
    members: pd.DataFrame,
    dates: list[str],
) -> dict:
    """个股层 + 板块（行业二级）层 RankIC（近 VALIDATION_WINDOW 评估日，fwd5）。"""
    sm2 = members[members["SectorType"] == "行业板块(二级)"].rename(
        columns={"Symbol": "symbol"}
    )[["symbol", "SectorName"]]
    stock_ics, sector_ics = [], []
    for dt in dates[len(dates) - C.EVAL_DAYS : len(dates) - 5]:
        comp = comps.get(dt)
        fr = fwd5.get(dt)
        if comp is None or fr is None:
            continue
        m = comp.notna() & fr.notna()
        if int(m.sum()) < C.MIN_COMPOSITE_IC_DAY:
            continue
        stock_ics.append(float(comp[m].rank().corr(fr[m])))
        d = pd.DataFrame({"symbol": comp.index, "comp": comp.values})
        d["fwd"] = fr.reindex(d["symbol"]).values
        d = d.dropna().merge(sm2, on="symbol")
        g = d.groupby("SectorName").agg(
            comp=("comp", "mean"), fwd=("fwd", "mean"), n=("fwd", "size")
        )
        g = g[g["n"] >= C.SECTOR_MIN_N]
        if len(g) >= C.SECTOR_IC_MIN_SECTORS:
            sector_ics.append(float(g["comp"].rank().corr(g["fwd"].rank())))
    return {
        "stock": _agg_ic(stock_ics),
        "sector": _agg_ic(sector_ics),
        "in_sample": True,
        "note": "入选窗=评估窗（近60交易日），口径自洽验证；真 OOS 由 P2 滚动重选交付",
    }


def _agg_ic(ics: list[float]) -> dict:
    s = pd.Series(ics, dtype=float).dropna().tail(C.VALIDATION_WINDOW)
    if s.empty or float(s.std()) == 0:
        return {
            "rank_ic": None,
            "icir": None,
            "t": None,
            "win": None,
            "days": int(len(s)),
        }
    mu, sd = float(s.mean()), float(s.std())
    return {
        "rank_ic": IO._clean(mu),
        "icir": IO._clean(mu / sd),
        "t": IO._clean(mu / sd * float(np.sqrt(len(s)))),
        "win": IO._clean(float((s > 0).mean())),
        "days": int(len(s)),
    }
