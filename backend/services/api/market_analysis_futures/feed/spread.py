"""期货价差分析 —— 内外盘跨市场价差 + 贵金属期现基差。

口径（每条 pair 自带 formula/tax 说明，页面原样展示，避免误读）：
- 跨市场：国际价为**美元报价**，先按当日中行汇率折算人民币（逐日对齐、
  缺失日跳过该点，不外推），再按单位换算到与国内同一量纲
- 税制：黄金为投资净价口径直接可比（投资金免税）；白银与有色（沪铜/沪铝等
  vs COMEX/LME）按进口到岸口径乘 (1+13%) 增值税——不调税会把增值税当价差，
  失去意义；原油 SC 含消费税与增值税，未调税，仅作名义价差
- 期现：上金所现货收盘 vs 沪期主力连续收盘（两市场收盘时刻略不同，
  点为收盘对收盘口径）；基差 = 现货 − 期货，正值现货升水
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed.base import (
    _fx_rate,
    _fx_series,
    _num,
    _series,
    to_iso,
)
from backend.shared.futures_universe import SECTORS, symbol_meta

_OZ_G = 31.1035  # 金衡盎司 → 克
_LB_T = 2204.62  # 磅 → 吨
_CENT = 100.0  # 美分 → 美元（COMEX 铜源侧按美分/磅报价，见 cu_hg）
_VAT = 1.13  # 进口增值税（有色到岸可比口径）

_FLAT_BAND = 0.05  # 基差率绝对值 < 0.05% 视为平水


def _raw(p: float, fx: float) -> float:
    return p * fx


def _usd_oz_to_cny_g(p: float, fx: float) -> float:
    return p * fx / _OZ_G


def _usd_oz_to_cny_kg(p: float, fx: float) -> float:
    return p * fx * 1000.0 / _OZ_G


def _usd_oz_to_cny_kg_vat(p: float, fx: float) -> float:
    """美元/盎司 → 元/千克（含 13% 增值税）。沪银盘面含税（投资金免税政策
    仅覆盖黄金），不调税会把 13% 增值税当价差——黄金平价 ±0.7% 即反证。"""
    return _usd_oz_to_cny_kg(p, fx) * _VAT


def _usd_lb_to_cny_t(p: float, fx: float) -> float:
    return p * fx * _LB_T


def _cent_lb_to_cny_t(p: float, fx: float) -> float:
    """美分/磅 → 元/吨（COMEX 铜）。源侧（新浪/akshare 美精铜）按美分/磅
    报价落盘（HG.FUT close≈662），与其余国际品种的纯美元单位不同，
    不 ÷100 会把价差放大 100 倍（-99% 假价差）。"""
    return p / _CENT * fx * _LB_T


def _usd_t_to_cny_t_vat(p: float, fx: float) -> float:
    return p * fx * _VAT


_CROSS_PAIRS: list[dict[str, Any]] = [
    {
        "key": "au_gc",
        "name": "黄金",
        "dom": "AU0.CN",
        "intl": "GC.FUT",
        "dom_unit": "元/克",
        "intl_unit": "美元/盎司",
        "to_cny": _usd_oz_to_cny_g,
        "formula": "美元/盎司 × 汇率 ÷ 31.1035 = 元/克",
        "tax": "均为投资净价口径，未调税",
    },
    {
        "key": "ag_si",
        "name": "白银",
        "dom": "AG0.CN",
        "intl": "SI.FUT",
        "dom_unit": "元/千克",
        "intl_unit": "美元/盎司",
        "to_cny": _usd_oz_to_cny_kg_vat,
        "formula": "美元/盎司 × 汇率 × 1000 ÷ 31.1035 × 1.13 = 元/千克",
        "tax": "沪银含 13% 增值税（投资金免税不覆盖白银），按到岸口径加 13%",
    },
    {
        "key": "cu_hg",
        "name": "铜",
        "dom": "CU0.CN",
        "intl": "HG.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美分/磅",
        "to_cny": lambda p, fx: _cent_lb_to_cny_t(p, fx) * _VAT,
        "formula": "美分/磅 ÷ 100 × 汇率 × 2204.62 × 1.13 = 元/吨",
        "tax": "进口到岸口径（含 13% 增值税）",
    },
    {
        "key": "sc_cl",
        "name": "原油",
        "dom": "SC0.CN",
        "intl": "CL.FUT",
        "dom_unit": "元/桶",
        "intl_unit": "美元/桶",
        "to_cny": _raw,
        "formula": "美元/桶 × 汇率 = 元/桶",
        "tax": "SC 含消费税与增值税，未调税（名义价差）",
    },
    {
        "key": "al_ahd",
        "name": "铝",
        "dom": "AL0.CN",
        "intl": "AHD.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美元/吨",
        "to_cny": _usd_t_to_cny_t_vat,
        "formula": "美元/吨 × 汇率 × 1.13 = 元/吨",
        "tax": "LME 为不含税报价，按到岸口径加 13% 增值税",
    },
    {
        "key": "zn_zsd",
        "name": "锌",
        "dom": "ZN0.CN",
        "intl": "ZSD.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美元/吨",
        "to_cny": _usd_t_to_cny_t_vat,
        "formula": "美元/吨 × 汇率 × 1.13 = 元/吨",
        "tax": "LME 为不含税报价，按到岸口径加 13% 增值税",
    },
    {
        "key": "ni_nid",
        "name": "镍",
        "dom": "NI0.CN",
        "intl": "NID.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美元/吨",
        "to_cny": _usd_t_to_cny_t_vat,
        "formula": "美元/吨 × 汇率 × 1.13 = 元/吨",
        "tax": "LME 为不含税报价，按到岸口径加 13% 增值税",
    },
    {
        "key": "sn_snd",
        "name": "锡",
        "dom": "SN0.CN",
        "intl": "SND.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美元/吨",
        "to_cny": _usd_t_to_cny_t_vat,
        "formula": "美元/吨 × 汇率 × 1.13 = 元/吨",
        "tax": "LME 为不含税报价，按到岸口径加 13% 增值税",
    },
    {
        "key": "pb_pbd",
        "name": "铅",
        "dom": "PB0.CN",
        "intl": "PBD.FUT",
        "dom_unit": "元/吨",
        "intl_unit": "美元/吨",
        "to_cny": _usd_t_to_cny_t_vat,
        "formula": "美元/吨 × 汇率 × 1.13 = 元/吨",
        "tax": "LME 为不含税报价，按到岸口径加 13% 增值税",
    },
]

_BASIS_PAIRS: list[dict[str, str]] = [
    {
        "key": "au_spot",
        "name": "黄金期现",
        "spot": "Au99.99",
        "fut": "AU0.CN",
    },
    {
        "key": "ag_spot",
        "name": "白银期现",
        "spot": "Ag(T+D)",
        "fut": "AG0.CN",
    },
]


def _rate_by_day(days: list[str]) -> pd.Series:
    """把汇率对齐到给定 K 线日：取不晚于该日的最近牌价（ffill，不外推未来）。"""
    fx = _fx_series("USD")
    if fx.empty:
        return pd.Series(dtype="float64")
    s = pd.Series(fx["rate"].to_numpy(), index=fx["dt"].astype(str).to_numpy())
    idx = sorted(set(s.index) | set(days))
    s = s.reindex(idx).ffill()
    return s.reindex(days)


def get_cross_market(days: int = 60) -> dict:
    """内外盘价差（9 组）：逐日折算 + 最新价差 + 价差率历史。"""

    def _load() -> dict:
        dates, k = _series(days)
        fx_latest = _fx_rate("USD")
        empty = {
            "trade_date": None,
            "fx": fx_latest,
            "pairs": [],
        }
        if not dates or k.empty:
            return empty
        wide = k.pivot_table(
            index="dt", columns="symbol", values="close", aggfunc="last"
        )
        rate = _rate_by_day(dates)
        out = []
        for p in _CROSS_PAIRS:
            dom, intl = p["dom"], p["intl"]
            dom_meta, intl_meta = symbol_meta(dom), symbol_meta(intl)
            entry: dict[str, Any] = {
                "key": p["key"],
                "name": p["name"],
                "sector": dom_meta["sector"],
                "sector_name": SECTORS.get(dom_meta["sector"], dom_meta["sector"]),
                "domestic_symbol": dom,
                "domestic_name": dom_meta["name"],
                "domestic_unit": p["dom_unit"],
                "intl_symbol": intl,
                "intl_name": intl_meta["name"],
                "intl_unit": p["intl_unit"],
                "formula": p["formula"],
                "tax": p["tax"],
                "trade_date": None,
                "domestic_close": None,
                "intl_close_native": None,
                "intl_close_cny": None,
                "spread": None,
                "spread_pct": None,
                "points": [],
            }
            if dom not in wide.columns or intl not in wide.columns:
                out.append(entry)
                continue
            points = []
            last_native = None  # 跟随 points[-1] 同日取值（新旧端缺数据日不定）
            to_cny: Callable[[float, float], float] = p["to_cny"]
            for d in reversed(dates):  # 升序出点，便于前端画图
                dp, ip = wide.at[d, dom], wide.at[d, intl]
                fx = rate.get(d)
                if pd.isna(dp) or pd.isna(ip) or fx is None or pd.isna(fx):
                    continue
                conv = to_cny(float(ip), float(fx))
                if conv <= 0:
                    continue
                spread = float(dp) - conv
                last_native = float(ip)
                points.append(
                    {
                        "date": to_iso(d),
                        "domestic": _num(dp),
                        "intl_cny": _num(conv),
                        "spread": _num(spread),
                        "spread_pct": _num(spread / conv * 100),
                    }
                )
            if points:
                last = points[-1]
                entry.update(
                    {
                        "trade_date": last["date"],
                        "domestic_close": last["domestic"],
                        "intl_close_native": _num(last_native),
                        "intl_close_cny": last["intl_cny"],
                        "spread": last["spread"],
                        "spread_pct": last["spread_pct"],
                        "points": points,
                    }
                )
            out.append(entry)
        trade_date = to_iso(dates[0]) if dates else None
        return {"trade_date": trade_date, "fx": fx_latest, "pairs": out}

    return cached(f"futures_cross_market:{days}", _load)


def get_precious_basis(days: int = 60) -> dict:
    """贵金属期现基差：上金所现货 vs 沪期主力（收盘对收盘）。"""

    def _load() -> dict:
        dates, k = _series(days)
        empty = {"trade_date": None, "pairs": []}
        if not dates or k.empty:
            return empty
        wide = k.pivot_table(
            index="dt", columns="symbol", values="close", aggfunc="last"
        )
        out = []
        for p in _BASIS_PAIRS:
            spot, fut = p["spot"], p["fut"]
            meta = symbol_meta(fut)
            entry: dict[str, Any] = {
                "key": p["key"],
                "name": p["name"],
                "spot_symbol": spot,
                "spot_name": symbol_meta(spot)["name"],
                "fut_symbol": fut,
                "fut_name": meta["name"],
                "unit": meta["unit"],
                "trade_date": None,
                "spot_close": None,
                "fut_close": None,
                "basis": None,
                "basis_pct": None,
                "state": None,
                "points": [],
            }
            if spot not in wide.columns or fut not in wide.columns:
                out.append(entry)
                continue
            points = []
            for d in reversed(dates):
                sp, fp = wide.at[d, spot], wide.at[d, fut]
                if pd.isna(sp) or pd.isna(fp) or fp <= 0:
                    continue
                basis = float(sp) - float(fp)
                points.append(
                    {
                        "date": to_iso(d),
                        "spot": _num(sp),
                        "fut": _num(fp),
                        "basis": _num(basis),
                        "basis_pct": _num(basis / float(fp) * 100),
                    }
                )
            if points:
                last = points[-1]
                bp = last["basis_pct"]
                if bp is None:
                    state = None
                elif bp > _FLAT_BAND:
                    state = "premium"  # 现货升水（back 结构特征）
                elif bp < -_FLAT_BAND:
                    state = "discount"  # 现货贴水（contango 结构特征）
                else:
                    state = "flat"
                entry.update(
                    {
                        "trade_date": last["date"],
                        "spot_close": last["spot"],
                        "fut_close": last["fut"],
                        "basis": last["basis"],
                        "basis_pct": bp,
                        "state": state,
                        "points": points,
                    }
                )
            out.append(entry)
        trade_date = to_iso(dates[0]) if dates else None
        return {"trade_date": trade_date, "pairs": out}

    return cached(f"futures_precious_basis:{days}", _load)
