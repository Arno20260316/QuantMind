#!/usr/bin/env python3
"""资金流信号预测证据预研（机构视角重规划用）。

在真实 QuantDB 上测量候选信号的**前向预测力**（qfq 收益口径，与因子板同源）：
- 股票层：主力净额比 / 净额比 z20 / 主力-散户背离 → 未来 3 日收益 RankIC（分年）
- 条件事件：主力净流入连续 streak≥k → 未来 3 日收益 vs 基准
- 板块层（行业二级，4144 只覆盖）：板块净额比/广度/streak → 板块等权未来 3 日收益 RankIC
输出 JSON 供细案引用；所有数字实测，不预设方向。
"""
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
from backend.shared.quantdb_flow_units import normalize_l2_flow_money_to_yuan  # noqa: E402

ROOT = Path("/data/quantdb")
L2 = ROOT / "6_ml_datasets/l2_factors"
FW = ROOT / "1_kline_data/daily_forward"
MEMBERS = ROOT / "2_base_sector/sector_concept/sector_members.parquet"
N_WIN = 520  # ≈2 年评估窗
FWD_H = 3

t0 = time.time()
con = duckdb.connect()

dts = sorted(
    d.name[3:]
    for d in L2.iterdir()
    if d.is_dir() and d.name.startswith("dt=") and d.name[3:].isdigit()
)
win = dts[-(N_WIN + FWD_H + 2):]
print(f"[load] l2 分区共 {len(dts)}，窗 {win[0]}..{win[-1]}（{len(win)} 日）")

l2_files = [str(L2 / f"dt={d}" / "data.parquet") for d in win]
fw_files = [str(FW / f"dt={d}" / "data.parquet") for d in win]
fw_files = [p for p in fw_files if Path(p).exists()]

flow = con.execute(
    f"""SELECT symbol, dt,
        flow_super_net, flow_large_net, flow_medium_net, flow_small_net,
        flow_net_amount, flow_net_ratio, amount
    FROM read_parquet({l2_files!r})"""
).df()
print(f"[load] l2 rows={len(flow)} ({time.time()-t0:.0f}s)")

close = con.execute(
    f"SELECT symbol, dt, close FROM read_parquet({fw_files!r})"
).df()
print(f"[load] close rows={len(close)} ({time.time()-t0:.0f}s)")

# ── 单位归一（按 dt 分组自动判别万元/元）→ 派生信号 ──────────────────────
flow = normalize_l2_flow_money_to_yuan(flow)
flow["main"] = flow["flow_super_net"] + flow["flow_large_net"]
flow["retail"] = flow["flow_medium_net"] + flow["flow_small_net"]
flow["amount_y"] = flow["amount"] * 1e4  # amount 恒万元
flow = flow[flow["amount_y"] > 0]
flow["net_ratio"] = flow["main"] / flow["amount_y"]
flow["divergence"] = (flow["main"] - flow["retail"]) / flow["amount_y"]

flow_dts = sorted(flow["dt"].unique())
print(f"[norm] dt 数={len(flow_dts)}，示例净额比 median={flow['net_ratio'].median():.4f}")

# 信号矩阵 symbol×dt（列=日期序）
sig_ratio = flow.pivot_table(index="symbol", columns="dt", values="net_ratio")
sig_main = flow.pivot_table(index="symbol", columns="dt", values="main")
sig_div = flow.pivot_table(index="symbol", columns="dt", values="divergence")
cols = list(sig_ratio.columns)  # int yyyymmdd, sorted

# z20（含当日 trailing 20 日）——信号 t 日收盘后可得
mu = sig_ratio.rolling(20, min_periods=15, axis=1).mean()
sd = sig_ratio.rolling(20, min_periods=15, axis=1).std()
z20 = (sig_ratio - mu) / sd

# streak：连续主力净流入天数（numpy 逐列滚动）
flag = (sig_main > 0).fillna(False).to_numpy()
streak = np.zeros_like(flag, dtype=np.int32)
for j in range(1, flag.shape[1]):
    streak[:, j] = np.where(flag[:, j], streak[:, j - 1] + 1, 0)
streak_df = pd.DataFrame(streak, index=sig_ratio.index, columns=cols)

# 前向收益矩阵（列=日期序）
px = close.pivot_table(index="symbol", columns="dt", values="close")
px = px.reindex(columns=cols)
fwd = px.shift(-FWD_H, axis=1) / px - 1.0
fwd = fwd.replace([np.inf, -np.inf], np.nan)


def rank_ic_series(sig_df, fwd_df, eval_cols):
    ics = {}
    for c in eval_cols:
        s = sig_df[c] if c in sig_df.columns else None
        f = fwd_df[c] if c in fwd_df.columns else None
        if s is None or f is None:
            continue
        m = s.notna() & f.notna()
        if int(m.sum()) < 100:
            continue
        ics[c] = float(s[m].rank().corr(f[m].rank()))
    return pd.Series(ics)


def ic_report(ics: pd.Series, label: str):
    if ics.empty:
        return {"label": label, "n_days": 0}
    mean, std = float(ics.mean()), float(ics.std())
    icir = mean / std if std > 0 else float("nan")
    yearly = {
        str(y): round(float(g.mean()), 4)
        for y, g in ics.groupby(ics.index // 10000)
    }
    rep = {
        "label": label,
        "n_days": int(len(ics)),
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 3),
        "pct_pos": round(float((ics > 0).mean()), 3),
        "t_stat": round(mean / std * np.sqrt(len(ics)), 2) if std > 0 else None,
        "yearly": yearly,
    }
    print(f"[IC] {label}: {rep}")
    return rep


out = {"window": [win[0], win[-1]], "fwd_h": FWD_H}

eval_cols = [c for c in cols if c in fwd.columns and fwd[c].notna().any()]
print(f"[eval] 评估日 {len(eval_cols)}")

stock_reports = []
stock_reports.append(ic_report(rank_ic_series(sig_ratio, fwd, eval_cols), "股票·主力净额比→fwd3"))
stock_reports.append(ic_report(rank_ic_series(z20, fwd, eval_cols), "股票·净额比z20→fwd3"))
stock_reports.append(ic_report(rank_ic_series(sig_div, fwd, eval_cols), "股票·主力散户背离→fwd3"))
out["stock_ic"] = stock_reports

# ── 条件事件：stock streak≥k → fwd3 vs 基准 ──────────────────────────────
def cond_returns(sig_df, fwd_df, level, label, eval_cols):
    cnt, rets, base_rets = 0, [], []
    for c in eval_cols:
        if c not in fwd_df.columns:
            continue
        f = fwd_df[c].dropna()
        base_rets.append(f)
        hit = sig_df[c].reindex(f.index).fillna(0)
        m = hit >= level
        if int(m.sum()):
            cnt += int(m.sum())
            rets.append(f[m])
    if not rets:
        return {"label": label, "n": 0}
    r = pd.concat(rets)
    b = pd.concat(base_rets)
    rep = {
        "label": label,
        "n": int(cnt),
        "ret_mean_pct": round(float(r.mean()) * 100, 3),
        "ret_med_pct": round(float(r.median()) * 100, 3),
        "hit_gt0": round(float((r > 0).mean()), 3),
        "base_mean_pct": round(float(b.mean()) * 100, 3),
        "base_med_pct": round(float(b.median()) * 100, 3),
        "base_hit_gt0": round(float((b > 0).mean()), 3),
    }
    print(f"[COND] {label}: {rep}")
    return rep


out["stock_streak"] = [
    cond_returns(streak_df, fwd, k, f"股票·连续净流入≥{k}日→fwd3", eval_cols)
    for k in (2, 3, 5)
]

# ── 板块层（行业二级）：成分聚合 → 板块信号 → 板块等权 fwd3 ──────────────
mem = con.execute(
    f"""SELECT Symbol, SectorName FROM read_parquet('{MEMBERS}')
    WHERE SectorType='行业板块(二级)'"""
).df()
sym2sec = mem.set_index("Symbol")["SectorName"]
flow["sector"] = flow["symbol"].map(sym2sec)
sf = flow[flow["sector"].notna()]
print(f"[sector] 覆盖股票 {sf['symbol'].nunique()}，板块 {sf['sector'].nunique()}")

day = sf.groupby(["sector", "dt"]).agg(
    main=("main", "sum"), amount_y=("amount_y", "sum"),
    n=("symbol", "count"), n_pos=("main", lambda x: (x > 0).sum()),
).reset_index()
day["sec_ratio"] = day["main"] / day["amount_y"]
day["breadth"] = day["n_pos"] / day["n"]

sec_ratio = day.pivot_table(index="sector", columns="dt", values="sec_ratio")
sec_breadth = day.pivot_table(index="sector", columns="dt", values="breadth")
sec_main = day.pivot_table(index="sector", columns="dt", values="main")

# 板块等权 fwd3 = 成分 fwd3 均值（仅成员内、有价）
fwd_cols = {}
for c in cols:
    if c not in fwd.columns:
        continue
    f = fwd[c].dropna()
    sec = f.index.to_series().map(sym2sec)
    g = f.groupby(sec.values).mean()
    fwd_cols[c] = g
sec_fwd = pd.DataFrame(fwd_cols)

sec_eval = [c for c in eval_cols if c in sec_fwd.columns]
s_reports = []
s_reports.append(ic_report(rank_ic_series(sec_ratio, sec_fwd, sec_eval), "板块·净额比→板块等权fwd3"))
s_reports.append(ic_report(rank_ic_series(sec_breadth, sec_fwd, sec_eval), "板块·净流入广度→板块等权fwd3"))

# 板块 streak（连续净额比>0）
sf_flag = (sec_main > 0).fillna(False).to_numpy()
s_streak = np.zeros_like(sf_flag, dtype=np.int32)
for j in range(1, sf_flag.shape[1]):
    s_streak[:, j] = np.where(sf_flag[:, j], s_streak[:, j - 1] + 1, 0)
s_streak_df = pd.DataFrame(s_streak, index=sec_main.index, columns=list(sec_main.columns))

s_reports.append(cond_returns(s_ratio_streak := s_streak_df, sec_fwd, 3, "板块·连续净流入≥3日→板块等权fwd3", sec_eval))
s_reports.append(cond_returns(s_streak_df, sec_fwd, 5, "板块·连续净流入≥5日→板块等权fwd3", sec_eval))
out["sector_ic"] = s_reports

out["elapsed_s"] = round(time.time() - t0, 1)
Path("/tmp/flow_probe_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(f"[done] {out['elapsed_s']}s → /tmp/flow_probe_out.json")
