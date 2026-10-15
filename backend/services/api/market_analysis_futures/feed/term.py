"""期货期限结构 —— 单品种近远月曲线 / 全市场 contango-backwardation 全景。

口径：
- 数据源 `contracts_daily`（分合约日K，每个合约存其生命周期内全部交易日）
- 曲线取**每个合约的最后一根K线**；活跃合约 = 持仓量>0 或成交量>0
- `near_far_spread_pct` = (近月价 − 远月价) / 远月价 × 100：
  负值 = 近低远高（contango 升水，展期成本）；正值 = 近高远低
  （backwardation 贴水，持多头展期收益）。阈值 ±0.25% 内记 flat
- `carry_annual_pct` = ln(近月/远月) × 365 / (月差 × 30.44) × 100 年化
  展期收益；正 = backwardation（多头展期收益）。**统一月差口径**（不逐日
  用到期日差）：历史序列与当期可比，分位才成立
- 距到期日：`contract_meta`（last_trade_date→expire_date）→ 缺失按合约月
  15 日**估计**（expiry_estimated=true；DCE/GFEX 无 meta 全部走估计）
"""

from __future__ import annotations

import math
import re

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _contract_expiry,
    _contracts,
    _num,
    _rank_pct,
)
from backend.shared.futures_universe import CN_MAIN, SECTORS, symbol_meta

_FLAT_BAND = 0.25  # 价差率绝对值 < 0.25% 视为平水
_FRESH_DAYS = 7  # 合约最后一根K线距今 > 7 自然日 = 已到期/长期停牌，不进当期曲线

# stem → 品种中文名（SR → 白糖）
STEM_NAME: dict[str, str] = {k[:-1]: v for k, v in CN_MAIN.items()}


def _norm_stem(s: str) -> str:
    """用户输入归一：'AU0' / 'AU0.CN' / 'au' → 'AU'。"""
    code = s.strip().upper().replace(".CN", "").replace(".FUT", "")
    m = re.match(r"^[A-Z]+", code)
    return m.group(0) if m else code


def _col_of(df: pd.DataFrame, name: str) -> pd.Series:
    """缺列返回全 NaN Series（旧版 contracts 文件无 open_interest 列）。"""
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index)


def _curve_frame(stem: str) -> pd.DataFrame:
    """该品种**在册合约**的最后一根K线（按到期月升序）。

    已到期合约的文件仍留有存续期数据，其「最后一根」是到期日——用
    相对最新K线 7 自然日的新鲜度闸剔除，避免旧合约混入当期曲线。
    """
    df = _contracts()
    if df.empty:
        return pd.DataFrame()
    sub = df[df["stem"] == stem]
    if sub.empty:
        return pd.DataFrame()
    latest = sub.sort_values("time").drop_duplicates("symbol", keep="last").copy()
    if latest.empty:
        return latest
    cutoff = latest["time"].max() - pd.Timedelta(days=_FRESH_DAYS)
    latest = latest[latest["time"] >= cutoff]
    latest["ym_num"] = pd.to_numeric(latest["ym"], errors="coerce")
    return latest.dropna(subset=["ym_num", "close"]).sort_values("ym_num")


def _spread_of(latest: pd.DataFrame) -> dict | None:
    """由曲线帧算 near/far 价差与结构类型（活跃合约 < 2 返回 None）。"""
    oi = _col_of(latest, "open_interest")
    vol = _col_of(latest, "volume")
    active = latest[(oi.fillna(0) > 0) | (vol.fillna(0) > 0)]
    if len(active) < 2:
        return None
    near, far = active.iloc[0], active.iloc[-1]
    near_close, far_close = float(near["close"]), float(far["close"])
    if far_close <= 0:
        return None
    spread = (near_close - far_close) / far_close * 100
    if spread > _FLAT_BAND:
        structure = "backwardation"
    elif spread < -_FLAT_BAND:
        structure = "contango"
    else:
        structure = "flat"
    return {
        "near_contract": str(near["symbol"]),
        "near_price": _num(near_close),
        "far_contract": str(far["symbol"]),
        "far_price": _num(far_close),
        "near_far_spread_pct": _num(spread),
        "structure": structure,
    }


def _months_between(ym_a: str, ym_b: str) -> int | None:
    """两个 4 位年月码（'2610'）间的月数差（b − a）。"""
    try:
        a, b = int(ym_a), int(ym_b)
    except (TypeError, ValueError):
        return None
    return (b // 100 * 12 + b % 100) - (a // 100 * 12 + a % 100)


def _annualize_carry(
    near_close: float, far_close: float, months: int | None
) -> float | None:
    """年化展期收益（小数）：ln(近/远) × 365 / (月差 × 30.44)。

    正 = 近高远低（backwardation，多头展期收益）；负 = contango。
    月数 > 0 且价格为正才产出，否则 None（绝不落 0 冒名平水）。
    """
    if months is None or months < 1:
        return None
    if near_close is None or far_close is None:
        return None
    try:
        if float(near_close) <= 0 or float(far_close) <= 0:
            return None
        return math.log(float(near_close) / float(far_close)) * 365.0 / (months * 30.44)
    except (TypeError, ValueError):
        return None


def _expiry_map() -> dict[str, str]:
    """合约（大写 4 位年）→ 到期参考日（YYYY-MM-DD），contract_meta 最新快照。"""
    df = _contract_expiry()
    if df.empty or "expiry" not in df.columns:
        return {}
    return {
        str(s): str(e)
        for s, e in zip(df["symbol"], df["expiry"], strict=False)
        if isinstance(e, str) and e
    }


def _est_expiry(ym_num: float) -> str:
    """合约月估计到期日 = 当月 15 日（无 meta 品种的显式估计口径）。"""
    y = int(ym_num) // 100 + 2000
    m = int(ym_num) % 100
    return f"{y:04d}-{m:02d}-15"


def _curve_carry(
    latest: pd.DataFrame, asof: pd.Timestamp, emap: dict[str, str]
) -> dict:
    """曲线附加：每合约 expiry/days_to_expiry + 近远月年化 carry（统一月差口径）。

    `latest` = `_curve_frame` 输出（含 ym_num）；`asof` = 曲线快照日。
    """
    per: dict[str, dict] = {}
    for _, r in latest.iterrows():
        sym = str(r["symbol"])
        # contract_meta 键为无后缀合约码（AU2610），曲线里是全码（AU2610.CN）——
        # 曾直接以全码查表全部 miss，真到期日被估计值顶替
        key = sym.replace(".CN", "").replace(".FUT", "")
        est = key not in emap
        exp = emap.get(key) or _est_expiry(r["ym_num"])
        try:
            d2e = (pd.Timestamp(exp) - asof).days
        except (TypeError, ValueError):
            d2e = None
        per[sym] = {
            "expiry_date": exp,
            "days_to_expiry": int(d2e) if d2e is not None else None,
            "expiry_estimated": bool(est),
        }
    out: dict = {
        "per_contract": per,
        "carry_annual_pct": None,
        "near": None,
        "far": None,
    }
    oi = _col_of(latest, "open_interest")
    vol = _col_of(latest, "volume")
    active = latest[(oi.fillna(0) > 0) | (vol.fillna(0) > 0)]
    if len(active) < 2:
        return out
    near, far = active.iloc[0], active.iloc[-1]
    months = _months_between(str(near["ym"]), str(far["ym"]))
    c = _annualize_carry(float(near["close"]), float(far["close"]), months)
    out["carry_annual_pct"] = _num(c * 100.0) if c is not None else None
    out["carry_months"] = months
    out["near"] = str(near["symbol"])
    out["far"] = str(far["symbol"])
    return out


def get_term_structure(symbol: str) -> dict:
    """单品种近远月曲线（全部有数据的合约）+ 结构判定 + 主力合约。"""
    stem = _norm_stem(symbol)

    def _load() -> dict:
        empty = {
            "stem": stem,
            "name": STEM_NAME.get(stem, stem),
            "sector": symbol_meta(f"{stem}0.CN")["sector"],
            "trade_date": None,
            "curve": [],
            "structure": None,
            "near_far_spread_pct": None,
            "carry_annual_pct": None,
            "carry_months": None,
            "main_contract": None,
            "main_by": None,
            "total_oi_wan": None,
        }
        latest = _curve_frame(stem)
        if latest.empty:
            return empty
        asof = latest["time"].max()
        cf = _curve_carry(latest, asof, _expiry_map())
        per = cf["per_contract"]
        mult = symbol_meta(f"{stem}0.CN")["multiplier"]
        oi = _col_of(latest, "open_interest")
        curve = []
        for idx, r in latest.iterrows():
            oi_val = oi.loc[idx]
            oi_amount_yi = (
                oi_val * float(r["close"]) * mult / 1e8
                if mult and pd.notna(oi_val)
                else float("nan")
            )
            extra = per.get(str(r["symbol"]), {})
            curve.append(
                {
                    "contract": str(r["symbol"]),
                    "ym": str(r["ym"]),
                    "close": _num(r["close"]),
                    "settlement": _num(r.get("settlement")),
                    "volume_wan": _num(
                        pd.to_numeric(r.get("volume"), errors="coerce") / 1e4
                    ),
                    "oi_wan": _num(oi_val / 1e4),
                    "oi_amount_yi": _num(oi_amount_yi),
                    "expiry_date": extra.get("expiry_date"),
                    "days_to_expiry": extra.get("days_to_expiry"),
                    "expiry_estimated": extra.get("expiry_estimated", True),
                }
            )
        spread = _spread_of(latest)
        if oi.notna().any():
            main_contract = str(latest.loc[oi.idxmax(), "symbol"])
            main_by = "oi"
        else:  # 持仓量缺失（旧文件）→ 退而用成交量指认主力，如实标注
            vol = _col_of(latest, "volume")
            main_contract = (
                str(latest.loc[vol.idxmax(), "symbol"]) if vol.notna().any() else None
            )
            main_by = "volume" if main_contract else None
        trade_date = latest["time"].max()
        near_p = per.get(str(cf.get("near") or ""), {})
        far_p = per.get(str(cf.get("far") or ""), {})
        return {
            **empty,
            "sector_name": SECTORS.get(empty["sector"], empty["sector"]),
            "trade_date": str(trade_date.date()) if pd.notna(trade_date) else None,
            "curve": curve,
            "main_contract": main_contract,
            "main_by": main_by,
            "total_oi_wan": _num(oi.sum(min_count=1) / 1e4),
            "carry_annual_pct": cf.get("carry_annual_pct"),
            "carry_months": cf.get("carry_months"),
            "near_expiry_date": near_p.get("expiry_date"),
            "near_days_to_expiry": near_p.get("days_to_expiry"),
            "far_expiry_date": far_p.get("expiry_date"),
            "far_days_to_expiry": far_p.get("days_to_expiry"),
            **(spread or {}),
        }

    return cached(f"futures_term:{stem}", _load)


def get_term_ranking(limit: int = 40) -> dict:
    """全市场期限结构榜：按 near_far_spread_pct 降序（backwardation 深者在前）。

    行附年化 carry 与 carry_pct（**截面分位**，分母=本期有 carry 的品种数）；
    排序仍按价差率。carry 与价差对同一曲线只差年化与合约间距，正常情况下
    符号一致——不一致多见于近月临到期（月差 < 1 时 carry 输出 null）。
    """

    def _load() -> dict:
        df = _contracts()
        if df.empty:
            return {"trade_date": None, "rows": []}
        emap = _expiry_map()
        rows = []
        for stem, _sub in df.groupby("stem"):
            latest = _curve_frame(str(stem))
            if latest.empty:
                continue
            spread = _spread_of(latest)
            if not spread:
                continue
            cf = _curve_carry(latest, latest["time"].max(), emap)
            near_p = cf["per_contract"].get(str(cf.get("near") or ""), {})
            oi = _col_of(latest, "open_interest")
            sector = symbol_meta(f"{stem}0.CN")["sector"]
            rows.append(
                {
                    "stem": str(stem),
                    "name": STEM_NAME.get(str(stem), str(stem)),
                    "sector": sector,
                    "sector_name": SECTORS.get(sector, sector),
                    "contracts": int(len(latest)),
                    "total_oi_wan": _num(oi.sum(min_count=1) / 1e4),
                    "carry_annual_pct": cf.get("carry_annual_pct"),
                    "carry_months": cf.get("carry_months"),
                    "near_days_to_expiry": near_p.get("days_to_expiry"),
                    **spread,
                }
            )
        if rows:
            carry_series = pd.Series(
                [r["carry_annual_pct"] for r in rows], dtype="float64"
            )
            pcts = _rank_pct(carry_series)
            for r, p in zip(rows, pcts, strict=False):
                r["carry_pct"] = _num(p, 1)
        rows.sort(key=lambda x: -(x["near_far_spread_pct"] or 0))
        trade_date = df["time"].max()
        return {
            "trade_date": str(trade_date.date()) if pd.notna(trade_date) else None,
            "rows": rows[:limit],
        }

    return cached(f"futures_term_ranking:{limit}", _load)
