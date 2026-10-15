"""前瞻视角 —— 多因子截面分位合成（carry/基差/库存/动量/季节性）。

口径（页面「口径卡」逐条显形，结构化定义见 `factor_meta`）：
- 五因子**等权**合成：各因子先转 0-100 分位（各自参照系见 `factor_meta.ref`），
  缺失剔除、权重重归一；可用因子 < 2 的品种 composite = null（「—」显形，
  绝不排名半张桌子）
- 三档判定 q33/q67（与全平台概率板同口径）：leading/mid/lagging
  （展示面文案「靠前/居中/靠后」，不给买卖词汇、不写「概率」）
- 席位净多（member_net）免费源样本仅数个交易日，**不入合成**，纯事实展示
- 本板为历史统计口径的**分位视角**：carry 与基差溢价是商品期货文献广泛
  验证的截面收益相关因子，但本板不构成收益承诺与投资建议

因子参照系（ref 取值）：
- `cs`       = 当日全市场截面分位（分母 = 该因子当日有值的品种数）
- `ts250`    = 品种自身近 250 个交易日时序分位
- `ts_inv`   = 品种自身全历史时序分位、**方向取反**（去库 = 偏强）
- `fact`     = 纯事实，无分位
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from backend.services.api.market_analysis_shared.caching import cached
from backend.services.api.market_analysis_futures.feed import (
    basis as basis_feed,
    inventory as inventory_feed,
    members as members_feed,
)
from backend.services.api.market_analysis_futures.feed.base import (
    KLINE_REL,
    _contracts,
    _dedupe_bars,
    _inventory_em,
    _latest_full_trade_date,
    _member_rank,
    _num,
    _rank_pct,
    _read_partitioned,
    _series,
    _spot_basis,
    _trading_days,
    to_iso,
)
from backend.services.api.market_analysis_futures.feed.term import (
    STEM_NAME,
    get_term_structure,
)
from backend.shared.futures_universe import SECTORS, symbol_meta

# 合成因子（等权）；member_net 纯事实不入合成
COMPOSITE_FACTORS = ("carry", "basis", "inv_chg", "mom_20d", "seasonality")
MIN_COVERAGE = 2  # 可用因子数下限，低于此 composite=null

_CARRY_HISTORY_DAYS = 260  # carry 面板回看交易日（约一年，供画像历史）
_SEASONS_WINDOW = 2200  # 季节性回看分区（约 8.8 年）

FACTOR_META: dict[str, dict] = {
    "carry": {
        "name": "年化展期收益（carry）",
        "ref": "cs",
        "unit": "%",
        "direction": "positive",
        "definition": "ln(近月/远月) × 365 / (月差×30.44)，近/远月取当日活跃合约最早/最晚月",
        "source": "contracts_daily（分合约日K）",
        "note": "正 = backwardation（近高远低，多头展期收益）；统一月差口径保证历史可比",
    },
    "basis": {
        "name": "现货基差率",
        "ref": "ts250",
        "unit": "%",
        "direction": "positive",
        "definition": "生意社现货对主力合约基差率（(现货−主力)/现货），分位为品种自身近 250 交易日时序分位",
        "source": "spot_basis（生意社）",
        "note": "现货升水（正基差）通常对应近端偏紧；个别品种源侧现期脱钩，时序分位天然稳健化",
    },
    "inv_chg": {
        "name": "库存 20 日变化",
        "ref": "ts_inv",
        "unit": "%",
        "direction": "negative",
        "definition": "库存 20 交易日变化率 = 今/20 交易日前−1；分位为自身全历史分位且方向取反（去库 = 高分位）",
        "source": "inventory_em（东财）",
        "note": "theory of storage：显性库存去化通常对应近端偏强；库存单位随品种，只做相对比较",
    },
    "mom_20d": {
        "name": "20 日动量",
        "ref": "cs",
        "unit": "%",
        "direction": "positive",
        "definition": "主力连续 20 交易日收益率，截面分位",
        "source": "daily_forward（主力连续）",
        "note": "主力连续含换月跳空，收益率口径以此为限",
    },
    "seasonality": {
        "name": "季节性（同月效应）",
        "ref": "cs",
        "unit": "%",
        "direction": "positive",
        "definition": "品种历史各年**当前日历月**月收益均值（不含当年），截面分位",
        "source": "daily_forward（近 8 年）",
        "note": "样本为历史月均收益，年份数随上市时间而异，样本 < 3 年不出值",
    },
    "member_net": {
        "name": "席位净多（不入合成）",
        "ref": "fact",
        "unit": "手",
        "direction": "none",
        "definition": "前 20 席位多单持仓合计 − 空单持仓合计（主力合约）及日变化",
        "source": "member_rank_sina（新浪）",
        "note": "覆盖仅 SHFE/CZCE；样本起步仅数个交易日，样本不足不做分位",
    },
}


# ---- 因子：carry（含历史面板） ----


def _carry_panel(n_days: int = _CARRY_HISTORY_DAYS) -> pd.DataFrame:
    """由 contracts_daily 重建逐日 carry 面板（每 (stem, 交易日) 一条）。

    近/远月 = 当日**活跃合约**（持仓量>0 或成交>0）中年月最早/最晚者；
    年化 = ln(近/远) × 365 / (月差 × 30.44)——与当期曲线同一口径，历史
    与当期可比。活跃月 < 2 或近远同月的日期不产出（无曲线）。
    """

    def _load() -> pd.DataFrame:
        df = _contracts()
        if df.empty:
            return pd.DataFrame()
        ymd = df["time"].dt.strftime("%Y%m%d")
        keep = set(sorted(ymd.unique())[-n_days:])
        df = df[ymd.isin(keep)].copy()
        if df.empty:
            return df
        oi = pd.to_numeric(df.get("open_interest"), errors="coerce")
        vol = pd.to_numeric(df.get("volume"), errors="coerce")
        act = df[(oi.fillna(0) > 0) | (vol.fillna(0) > 0)].copy()
        act["ym_num"] = pd.to_numeric(act["ym"], errors="coerce")
        act["close"] = pd.to_numeric(act["close"], errors="coerce")
        act = act.dropna(subset=["ym_num", "close"])
        if act.empty:
            return pd.DataFrame()
        g = act.groupby(["stem", "time"])
        near = act.loc[g["ym_num"].idxmin()]
        far = act.loc[g["ym_num"].idxmax()]
        m = near.merge(far, on=["stem", "time"], suffixes=("_n", "_f"))
        m = m[m["ym_num_n"] < m["ym_num_f"]]
        if m.empty:
            return pd.DataFrame()
        months = (
            (m["ym_num_f"] // 100 * 12 + m["ym_num_f"] % 100)
            - (m["ym_num_n"] // 100 * 12 + m["ym_num_n"] % 100)
        ).astype("int64")
        ok = months >= 1
        m, months = m[ok], months[ok]
        if m.empty:
            return pd.DataFrame()
        ratio = (m["close_n"] / m["close_f"]).astype("float64")
        ratio = ratio.where(ratio > 0)  # 非正价格（异常源数据）→ NaN
        carry = np.log(ratio) * 365.0 / (months * 30.44)
        out = pd.DataFrame(
            {
                "stem": m["stem"].astype(str),
                "dt": m["time"].dt.strftime("%Y%m%d"),
                "near_contract": m["symbol_n"].astype(str),
                "far_contract": m["symbol_f"].astype(str),
                "near_close": m["close_n"],
                "far_close": m["close_f"],
                "months": months,
                "carry_annual": carry,
            }
        )
        return out.sort_values(["stem", "dt"])

    return cached(f"futures:carry_panel:{n_days}", _load, ttl=1800.0)


def _carry_now() -> pd.DataFrame:
    """carry 最新值 + 截面分位。列 [stem, value(%), pct, as_of, months, near, far]。"""

    def _load() -> pd.DataFrame:
        panel = _carry_panel()
        if panel.empty:
            return pd.DataFrame()
        latest = panel.sort_values("dt").drop_duplicates("stem", keep="last").copy()
        latest["as_of"] = latest["dt"]
        latest["value"] = latest["carry_annual"] * 100.0
        latest["pct"] = _rank_pct(latest["value"])
        return latest

    return cached("futures:carry_now", _load, ttl=1800.0)


# ---- 因子：动量 / 季节性（daily_forward .CN） ----


def _daily_hist(n_days: int) -> pd.DataFrame:
    """daily_forward 近 n 个分区（仅国内 .CN 主力连续），[symbol, dt, close]。"""

    def _load() -> pd.DataFrame:
        days = _trading_days(None, n_days)
        if not days:
            return pd.DataFrame()
        df = _read_partitioned(KLINE_REL, days, columns="symbol, dt, close")
        if df.empty:
            return df
        df = _dedupe_bars(df, ["symbol", "dt"])
        df["dt"] = df["dt"].astype(str)
        df["symbol"] = df["symbol"].astype(str)
        df = df[df["symbol"].str.endswith(".CN")]
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.dropna(subset=["close"]).sort_values(["symbol", "dt"])

    return cached(f"futures:daily_hist:{n_days}", _load, ttl=1800.0)


def _mom_now() -> pd.DataFrame:
    """20 交易日动量（%）+ 截面分位。列 [stem, value, pct, as_of]。"""

    def _load() -> pd.DataFrame:
        df = _daily_hist(45)
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["ret20"] = df.groupby("symbol")["close"].pct_change(20)
        latest = df.sort_values("dt").drop_duplicates("symbol", keep="last")
        latest = latest.dropna(subset=["ret20"])
        out = pd.DataFrame(
            {
                "stem": latest["symbol"].str.replace(r"0\.CN$", "", regex=True),
                "value": latest["ret20"] * 100.0,
                "as_of": latest["dt"],
            }
        )
        out["pct"] = _rank_pct(out["value"])
        return out

    return cached("futures:mom_now", _load, ttl=1800.0)


def _season_now() -> pd.DataFrame:
    """季节性：历史同月月收益均值（%）+ 截面分位。列 [stem, value, pct, years]。"""

    def _load() -> pd.DataFrame:
        df = _daily_hist(_SEASONS_WINDOW)
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["ym"] = df["dt"].str[:6]
        me = (
            df.sort_values("dt")
            .groupby(["symbol", "ym"], as_index=False)["close"]
            .last()
        )
        me["ret"] = me.groupby("symbol")["close"].pct_change()
        cur = me["ym"].max()
        if not isinstance(cur, str) or len(cur) != 6:
            return pd.DataFrame()
        cur_mm, cur_yy = cur[4:], cur[:4]
        same = me[(me["ym"].str[4:] == cur_mm) & (me["ym"].str[:4] != cur_yy)]
        same = same.dropna(subset=["ret"])
        agg = same.groupby("symbol")["ret"].agg(["mean", "count"]).reset_index()
        agg = agg[agg["count"] >= 3]  # 样本 < 3 年不出值（如新上市品种）
        if agg.empty:
            return pd.DataFrame()
        out = pd.DataFrame(
            {
                "stem": agg["symbol"].str.replace(r"0\.CN$", "", regex=True),
                "value": agg["mean"] * 100.0,
                "years": agg["count"].astype(int),
            }
        )
        out["pct"] = _rank_pct(out["value"])
        return out

    return cached(f"futures:season_now:{_SEASONS_WINDOW}", _load, ttl=1800.0)


# ---- 因子：基差 / 库存（各自参照） ----


def _basis_now() -> pd.DataFrame:
    """现货基差最新值（%）+ 自身 250 交易日时序分位。列 [stem, value, pct, as_of, n]。"""

    def _load() -> pd.DataFrame:
        df = _spot_basis(600)
        if df.empty or "dom_basis_rate" not in df.columns:
            return pd.DataFrame()
        rows = []
        for sym, sub in df.dropna(subset=["dom_basis_rate"]).groupby("symbol"):
            tail = sub.tail(250)
            latest = tail.iloc[-1]
            pct = _rank_pct(tail["dom_basis_rate"]).iloc[-1]
            rows.append(
                {
                    "stem": str(sym),
                    "value": float(latest["dom_basis_rate"]) * 100.0,
                    "pct": float(pct) if pd.notna(pct) else float("nan"),
                    "as_of": str(latest["dt"]),
                    "n": int(len(tail)),
                }
            )
        return pd.DataFrame(rows)

    return cached("futures:basis_now", _load, ttl=1800.0)


def _inv_now() -> pd.DataFrame:
    """库存 20 日变化率（%）+ 全历史分位（方向取反：去库=高分位）。

    列 [stem, value, pct, as_of, n]。样本不足 21 行的品种不产出变化率。
    """

    def _load() -> pd.DataFrame:
        df = _inventory_em()
        if df.empty:
            return pd.DataFrame()
        rows = []
        for sym, sub in df.groupby("symbol"):
            sub = sub.dropna(subset=["inventory"]).sort_values("dt")
            if len(sub) < 21:
                continue
            chg = sub["inventory"] / sub["inventory"].shift(20) - 1.0
            chg = chg.replace([float("inf"), float("-inf")], float("nan"))
            valid = chg.dropna()
            if valid.empty or pd.isna(valid.iloc[-1]):
                continue
            # 方向取反：对 -chg 求分位（去库 chg<0 → -chg>0 → 高分位）
            pct = _rank_pct(-valid).iloc[-1]
            rows.append(
                {
                    "stem": str(sym),
                    "value": float(valid.iloc[-1]) * 100.0,
                    "pct": float(pct) if pd.notna(pct) else float("nan"),
                    "as_of": str(sub["dt"].iloc[-1]),
                    "n": int(len(valid)),
                }
            )
        return pd.DataFrame(rows)

    return cached("futures:inv_now", _load, ttl=1800.0)


# ---- 因子：席位净多（纯事实） ----


def _members_now() -> pd.DataFrame:
    """席位净多：前 20 席位多−空（手）及日变化。列 [stem, net, net_delta, as_of, sample_days]。"""

    def _load() -> pd.DataFrame:
        mb = _member_rank()
        if mb.empty:
            return pd.DataFrame()
        latest = str(mb["dt"].max())
        cur = mb[mb["dt"] == latest]
        sample_days = int(mb["dt"].nunique())
        agg = cur.groupby(["variety", "side"])["size"].sum(min_count=1).unstack("side")
        dlt = cur.groupby(["variety", "side"])["delta"].sum(min_count=1).unstack("side")
        rows = []
        for variety in agg.index:
            lng = agg.loc[variety].get("long")
            sht = agg.loc[variety].get("short")
            if pd.isna(lng) or pd.isna(sht):
                continue
            net = float(lng) - float(sht)
            d_l = (
                dlt.loc[variety].get("long") if "long" in dlt.columns else float("nan")
            )
            d_s = (
                dlt.loc[variety].get("short")
                if "short" in dlt.columns
                else float("nan")
            )
            net_delta = (
                float(d_l) - float(d_s)
                if pd.notna(d_l) and pd.notna(d_s)
                else float("nan")
            )
            rows.append(
                {
                    "stem": str(variety),
                    "net": net,
                    "net_delta": net_delta,
                    "as_of": latest,
                    "sample_days": sample_days,
                }
            )
        return pd.DataFrame(rows)

    return cached("futures:members_now", _load, ttl=1800.0)


# ---- 合成 ----


def _f(v: float | None, nd: int = 2) -> float | None:
    """因子出口：NaN → None。"""
    if v is None:
        return None
    return _num(v, nd)


def _outlook_rows() -> dict:
    """全市场前瞻行 + 分位基准（cached 1800s；get_outlook / get_variety 共用）。"""

    def _load() -> dict:
        carry = _carry_now()
        basis = _basis_now()
        inv = _inv_now()
        mom = _mom_now()
        seas = _season_now()
        mem = _members_now()

        def _index(df: pd.DataFrame) -> dict[str, dict]:
            if df is None or df.empty:
                return {}
            return {str(r["stem"]): r for _, r in df.iterrows()}

        ci, bi, ii = _index(carry), _index(basis), _index(inv)
        mi, si, me = _index(mom), _index(seas), _index(mem)

        stems = set(ci) | set(bi) | set(ii) | set(mi) | set(si)
        rows: list[dict] = []
        for stem in sorted(stems):
            meta = symbol_meta(f"{stem}0.CN")
            f_carry = ci.get(stem)
            f_basis = bi.get(stem)
            f_inv = ii.get(stem)
            f_mom = mi.get(stem)
            f_seas = si.get(stem)
            f_mem = me.get(stem)
            factors: dict[str, dict] = {
                "carry": {
                    "value": _f(f_carry["value"]) if f_carry is not None else None,
                    "pct": _f(f_carry["pct"], 1) if f_carry is not None else None,
                    "as_of": to_iso(str(f_carry["as_of"]))
                    if f_carry is not None
                    else None,
                },
                "basis": {
                    "value": _f(f_basis["value"]) if f_basis is not None else None,
                    "pct": _f(f_basis["pct"], 1) if f_basis is not None else None,
                    "as_of": to_iso(str(f_basis["as_of"]))
                    if f_basis is not None
                    else None,
                    "sample_days": int(f_basis["n"]) if f_basis is not None else None,
                },
                "inv_chg": {
                    "value": _f(f_inv["value"]) if f_inv is not None else None,
                    "pct": _f(f_inv["pct"], 1) if f_inv is not None else None,
                    "as_of": to_iso(str(f_inv["as_of"])) if f_inv is not None else None,
                    "sample_days": int(f_inv["n"]) if f_inv is not None else None,
                },
                "mom_20d": {
                    "value": _f(f_mom["value"]) if f_mom is not None else None,
                    "pct": _f(f_mom["pct"], 1) if f_mom is not None else None,
                    "as_of": to_iso(str(f_mom["as_of"])) if f_mom is not None else None,
                },
                "seasonality": {
                    "value": _f(f_seas["value"]) if f_seas is not None else None,
                    "pct": _f(f_seas["pct"], 1) if f_seas is not None else None,
                    "years": int(f_seas["years"]) if f_seas is not None else None,
                },
                "member_net": {
                    "value": _f(f_mem["net"], 0) if f_mem is not None else None,
                    "pct": None,  # 纯事实展示，不入合成、无分位（schema 与其他因子对齐）
                    "value_wan": _num(f_mem["net"] / 1e4)
                    if f_mem is not None
                    else None,
                    "delta_wan": (
                        _num(f_mem["net_delta"] / 1e4)
                        if f_mem is not None and pd.notna(f_mem["net_delta"])
                        else None
                    ),
                    "as_of": to_iso(str(f_mem["as_of"])) if f_mem is not None else None,
                    "sample_days": int(f_mem["sample_days"])
                    if f_mem is not None
                    else None,
                },
            }
            pcts = [
                factors[k]["pct"]
                for k in COMPOSITE_FACTORS
                if factors[k]["pct"] is not None
            ]
            coverage = len(pcts)
            composite = (
                round(sum(pcts) / coverage, 1) if coverage >= MIN_COVERAGE else None
            )
            rows.append(
                {
                    "symbol": stem,
                    "symbol_cn": f"{stem}0.CN",
                    "name": meta["name"],
                    "sector": meta["sector"],
                    "sector_name": SECTORS.get(meta["sector"], meta["sector"]),
                    "composite": composite,
                    "coverage": coverage,
                    "tier": None,  # 分位基准算完回填
                    "factors": factors,
                }
            )

        comp = pd.Series([r["composite"] for r in rows], dtype="float64").dropna()
        if comp.empty:
            quantiles = {"q33": None, "q67": None}
        else:
            q33 = float(comp.quantile(0.33))
            q67 = float(comp.quantile(0.67))
            quantiles = {"q33": _num(q33, 1), "q67": _num(q67, 1)}
            for r in rows:
                if r["composite"] is None:
                    continue
                if r["composite"] >= q67:
                    r["tier"] = "leading"
                elif r["composite"] <= q33:
                    r["tier"] = "lagging"
                else:
                    r["tier"] = "mid"
        return {"rows": rows, "quantiles": quantiles}

    return cached("futures:outlook_rows", _load, ttl=1800.0)


def get_outlook(sector: str | None = None, limit: int | None = None) -> dict:
    """前瞻板：五因子合成分位视角（可选板块过滤/限行数）。"""
    base = _outlook_rows()
    rows = base["rows"]
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    rows = sorted(
        rows,
        key=lambda r: (
            r["composite"] is None,
            -(r["composite"] if r["composite"] is not None else 0),
        ),
    )
    if limit:
        rows = rows[:limit]
    full = base["rows"]
    coverage_ok = sum(1 for r in full if r["composite"] is not None)
    as_of = _latest_full_trade_date()
    return {
        "as_of": to_iso(as_of) if as_of else None,
        "weights": {
            k: round(100.0 / len(COMPOSITE_FACTORS), 1) for k in COMPOSITE_FACTORS
        },
        "composite_quantiles": base["quantiles"],
        "factor_meta": FACTOR_META,
        "universe_size": len(full),
        "covered_size": coverage_ok,
        "excluded_note": (
            f"{len(full) - coverage_ok} 个品种可用因子 < {MIN_COVERAGE}，"
            "composite 为「—」（不排名）"
        )
        if coverage_ok < len(full)
        else None,
        "rows": rows,
    }


_STEM_RE = re.compile(r"^[A-Za-z]+")


def _norm_stem(s: str) -> str:
    code = str(s).strip().upper().replace(".CN", "").replace(".FUT", "")
    m = _STEM_RE.match(code)
    return m.group(0) if m else code


def get_variety(symbol: str) -> dict:
    """品种画像：前瞻因子行 + 期限曲线 carry 历史 + 基差/库存/价量/席位下钻。"""
    stem = _norm_stem(symbol)

    def _load() -> dict:
        meta = symbol_meta(f"{stem}0.CN")
        base = _outlook_rows()
        row = next((r for r in base["rows"] if r["symbol"] == stem), None)

        # carry 历史（近一年）
        panel = _carry_panel()
        carry_hist: list[dict] = []
        if not panel.empty:
            sub = panel[panel["stem"] == stem].sort_values("dt")
            carry_hist = [
                {
                    "date": to_iso(str(r["dt"])),
                    "carry_annual_pct": _num(r["carry_annual"] * 100.0),
                    "months": int(r["months"]),
                    "near_contract": str(r["near_contract"]),
                    "far_contract": str(r["far_contract"]),
                }
                for _, r in sub.iterrows()
            ]

        # 价格/持仓 120 日
        _, sdf = _series(120)
        price_oi: list[dict] = []
        if not sdf.empty:
            sub = sdf[sdf["symbol"].astype(str) == f"{stem}0.CN"].sort_values("dt")
            price_oi = [
                {
                    "date": to_iso(str(r["dt"])),
                    "close": _num(r["close"]),
                    "position": _num(r["position"], 0),
                    "volume": _num(r["volume"], 0),
                }
                for _, r in sub.iterrows()
            ]

        term = get_term_structure(stem)
        return {
            "symbol": stem,
            "symbol_cn": f"{stem}0.CN",
            "name": STEM_NAME.get(stem, meta["name"]),
            "sector": meta["sector"],
            "sector_name": SECTORS.get(meta["sector"], meta["sector"]),
            "exchange": meta["exchange"],
            "unit": meta["unit"],
            "outlook": row,
            "term": term,
            "carry_history": carry_hist,
            "basis": basis_feed.get_basis(stem),
            "inventory": inventory_feed.get_inventory_em(stem),
            "price_oi": price_oi,
            "members": members_feed.get_members(stem),
        }

    return cached(f"futures_variety:{stem}", _load, ttl=1800.0)
