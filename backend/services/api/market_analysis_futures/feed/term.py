"""期货期限结构 —— 单品种近远月曲线 / 全市场 contango-backwardation 全景。

口径：
- 数据源 `contracts_daily`（分合约日K，每个合约存其生命周期内全部交易日）
- 曲线取**每个合约的最后一根K线**；活跃合约 = 持仓量>0 或成交量>0
- `near_far_spread_pct` = (近月价 − 远月价) / 远月价 × 100：
  负值 = 近低远高（contango 升水，展期成本）；正值 = 近高远低
  （backwardation 贴水，持多头展期收益）。阈值 ±0.25% 内记 flat
"""

from __future__ import annotations

import re

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _contracts,
    _num,
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
            "main_contract": None,
            "main_by": None,
            "total_oi_wan": None,
        }
        latest = _curve_frame(stem)
        if latest.empty:
            return empty
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
        return {
            **empty,
            "sector_name": SECTORS.get(empty["sector"], empty["sector"]),
            "trade_date": str(trade_date.date()) if pd.notna(trade_date) else None,
            "curve": curve,
            "main_contract": main_contract,
            "main_by": main_by,
            "total_oi_wan": _num(oi.sum(min_count=1) / 1e4),
            **(spread or {}),
        }

    return cached(f"futures_term:{stem}", _load)


def get_term_ranking(limit: int = 40) -> dict:
    """全市场期限结构榜：按 near_far_spread_pct 降序（backwardation 深者在前）。"""

    def _load() -> dict:
        df = _contracts()
        if df.empty:
            return {"trade_date": None, "rows": []}
        rows = []
        for stem, _sub in df.groupby("stem"):
            latest = _curve_frame(str(stem))
            if latest.empty:
                continue
            spread = _spread_of(latest)
            if not spread:
                continue
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
                    **spread,
                }
            )
        rows.sort(key=lambda x: -(x["near_far_spread_pct"] or 0))
        trade_date = df["time"].max()
        return {
            "trade_date": str(trade_date.date()) if pd.notna(trade_date) else None,
            "rows": rows[:limit],
        }

    return cached(f"futures_term_ranking:{limit}", _load)
