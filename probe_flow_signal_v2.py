#!/usr/bin/env python3
"""资金流信号预测证据预研 v2 — 板块层修正 + 中性化/共振追加。

v1 发现：股票层裸信号 IC≈0（净额比 0.006 / z20 0.000），板块 streak 条件超额显著。
v2 修正板块 IC 截面门槛（80 板块）、追加：
  A. 板块净额比 / 广度 → 板块等权 fwd3 RankIC（分年）
  B. 板块 streak≥3,5 条件的 fwd1/fwd3/fwd5 分年超额
  C. 板块内中性化个股资金流（within-sector rank）→ fwd3 IC
  D. 共振条件：板块 streak≥3 ∧ 个股板块内 rank≥0.7 → fwd3 vs 仅个股 vs 仅板块
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
N_WIN = 520

t0 = time.time()
con = duckdb.connect()

dts = sorted(
    d.name[3:]
    for d in L2.iterdir()
    if d.is_dir() and d.name.startswith("dt=") and d.name[3:].isdigit()
)
win = dts[-(N_WIN + 8):]
l2_files = [str(L2 / f"dt={d}" / "data.parquet") for d in win]
fw_files = [str(FW / f"dt={d}" / "data.parquet") for d in win if (FW / f"dt={d}" / "data.parquet").exists()]

flow = con.execute(
    f"""SELECT symbol, dt,
        flow_super_net, flow_large_net, flow_medium_net, flow_small_net,
        flow_net_amount, flow_net_ratio, amount
    FROM read_parquet({l2_files!r})"""
).df()
close = con.execute(f"SELECT symbol, dt, close FROM read_parquet({fw_files!r})").df()
print(f"[load] l2={len(flow)} close={len(close)} ({time.time()-t0:.0f}s)")

flow = normalize_l2_flow_money_to_yuan(flow)
flow["main"] = flow["flow_super_net"] + flow["flow_large_net"]
flow["retail"] = flow["flow_medium_net"] + flow["flow_small_net"]
flow["amount_y"] = flow["amount"] * 1e4
flow = flow[flow["amount_y"] > 0]
flow["net_ratio"] = flow["main"] / flow["amount_y"]

sig_ratio = flow.pivot_table(index="symbol", columns="dt", values="net_ratio")
sig_main = flow.pivot_table(index="symbol", columns="dt", values="main")
cols = list(sig_ratio.columns)

px = close.pivot_table(index="symbol", columns="dt", values="close").reindex(columns=cols)
fwds = {}
for h in (1, 3, 5):
    f = px.shift(-h, axis=1) / px - 1.0
    fwds[h] = f.replace([np.inf, -np.inf], np.nan)

mem = con.execute(
    f"""SELECT Symbol, SectorName FROM read_parquet('{MEMBERS}')
    WHERE SectorType='行业板块(二级)'"""
).df()
sym2sec = mem.set_index("Symbol")["SectorName"]

# 板块聚合
flow["sector"] = flow["symbol"].map(sym2sec)
sf = flow[flow["sector"].notna()]
day = sf.groupby(["sector", "dt"]).agg(
    main=("main", "sum"), amount_y=("amount_y", "sum"),
    n=("symbol", "count"), n_pos=("main", lambda x: (x > 0).sum()),
).reset_index()
day["sec_ratio"] = day["main"] / day["amount_y"]
day["breadth"] = day["n_pos"] / day["n"]
sec_ratio = day.pivot_table(index="sector", columns="dt", values="sec_ratio")
sec_breadth = day.pivot_table(index="sector", columns="dt", values="breadth")
sec_main = day.pivot_table(index="sector", columns="dt", values="main")

sec_fwds = {}
for h, f in fwds.items():
    d = {}
    for c in cols:
        fc = f[c].dropna()
        sec = fc.index.to_series().map(sym2sec)
        d[c] = fc.groupby(sec.values).mean()
    sec_fwds[h] = pd.DataFrame(d)

sig_flag = (sig_main > 0).fillna(False).to_numpy()
streak = np.zeros_like(sig_flag, dtype=np.int32)
for j in range(1, sig_flag.shape[1]):
    streak[:, j] = np.where(sig_flag[:, j], streak[:, j - 1] + 1, 0)
streak_df = pd.DataFrame(streak, index=sig_ratio.index, columns=cols)

sflag = (sec_main > 0).fillna(False).to_numpy()
s_streak = np.zeros_like(sflag, dtype=np.int32)
for j in range(1, sflag.shape[1]):
    s_streak[:, j] = np.where(sflag[:, j], s_streak[:, j - 1] + 1, 0)
s_streak_df = pd.DataFrame(s_streak, index=sec_main.index, columns=cols)

out = {"window": [win[0], win[-1]]}


def rank_ic_series(sig_df, fwd_df, eval_cols, min_pairs):
    ics = {}
    for c in eval_cols:
        if c not in sig_df.columns or c not in fwd_df.columns:
            continue
        s, f = sig_df[c], fwd_df[c]
        m = s.notna() & f.notna()
        if int(m.sum()) < min_pairs:
            continue
        ics[c] = float(s[m].rank().corr(f[m].rank()))
    return pd.Series(ics)


def ic_report(ics, label):
    if ics.empty:
        rep = {"label": label, "n_days": 0}
        print(f"[IC] {rep}")
        return rep
    mean, std = float(ics.mean()), float(ics.std())
    rep = {
        "label": label, "n_days": int(len(ics)),
        "ic_mean": round(mean, 4), "icir": round(mean / std, 3) if std > 0 else None,
        "pct_pos": round(float((ics > 0).mean()), 3),
        "yearly": {str(y): round(float(g.mean()), 4) for y, g in ics.groupby(ics.index // 10000)},
    }
    print(f"[IC] {rep}")
    return rep


eval_h3 = [c for c in cols if c in fwds[3].columns and fwds[3][c].notna().any()]
out["sector_ic"] = [
    ic_report(rank_ic_series(sec_ratio, sec_fwds[3], eval_h3, 30), "板块·净额比→板块等权fwd3"),
    ic_report(rank_ic_series(sec_breadth, sec_fwds[3], eval_h3, 30), "板块·净流入广度→板块等权fwd3"),
]

# ── B. 板块 streak 条件（h=1/3/5，分年） ────────────────────────────────
def cond_report(sig_streak, fwd, level, label, eval_cols, min_pairs=0):
    conds, bases, per_day = [], [], []
    for c in eval_cols:
        if c not in fwd.columns or c not in sig_streak.columns:
            continue
        f = fwd[c].dropna()
        hit = sig_streak[c].reindex(f.index).fillna(0)
        m = hit >= level
        cm = f[m]
        if len(cm) == 0:
            continue
        conds.append(cm)
        bases.append(f)
        per_day.append((c, float(cm.mean() - f.mean()), int((cm > 0).sum()), int(len(cm)), float((f > 0).mean())))
    if not conds:
        return {"label": label, "n": 0}
    r = pd.concat(conds, ignore_index=True)
    b = pd.concat(bases, ignore_index=True)
    days = pd.DataFrame(per_day, columns=["c", "excess", "win_n", "n", "base_hit"])
    days["year"] = days["c"].astype(str).str[:4]
    yearly = {
        y: {
            "n": int(g["n"].sum()),
            "excess_bp": round(float(g["excess"].mean()) * 1e4, 1),
            "hit": round(float(g["win_n"].sum() / g["n"].sum()), 3),
            "base_hit": round(float(g["base_hit"].mean()), 3),
        }
        for y, g in days.groupby("year")
    }
    rep = {
        "label": label, "n": int(len(r)), "n_days": len(days),
        "cond_mean_pct": round(float(r.mean()) * 100, 3),
        "base_mean_pct": round(float(b.mean()) * 100, 3),
        "excess_bp": round((float(r.mean()) - float(b.mean())) * 1e4, 1),
        "excess_daily_bp": round(float(days["excess"].mean()) * 1e4, 2),
        "hit": round(float((r > 0).mean()), 3),
        "base_hit": round(float((b > 0).mean()), 3),
        "yearly": yearly,
    }
    print(f"[COND] {json.dumps(rep, ensure_ascii=False)}")
    return rep


sec_cond = []
for h in (1, 3, 5):
    ev = [c for c in cols if c in sec_fwds[h].columns and sec_fwds[h][c].notna().any()]
    for k in (2, 3, 5):
        sec_cond.append(cond_report(s_streak_df, sec_fwds[h], k, f"板块·连续净流入≥{k}日→板块等权fwd{h}", ev))
out["sector_streak_cond"] = sec_cond

# ── C. 板块内中性化个股资金流 ────────────────────────────────────────────
within = pd.DataFrame(np.nan, index=sig_ratio.index, columns=sig_ratio.columns)
for _sec, syms in mem.groupby("SectorName")["Symbol"]:
    sub = sig_ratio.reindex(syms)
    sub = sub[sub.notna().any(axis=1)]
    within.loc[sub.index] = sub.rank(pct=True).to_numpy()
out["stock_within_ic"] = [
    ic_report(rank_ic_series(within, fwds[3], eval_h3, 100), "股票·板块内净额比rank→fwd3"),
]

# ── D. 共振：板块 streak≥3 ∧ 板块内 rank≥0.7 ───────────────────────────
s_streak_stock = s_streak_df.reindex(sym2sec.reindex(within.index).values)
s_streak_stock.index = within.index


def group_cond(mask_fn, label, h=3):
    ev = [c for c in cols if c in fwds[h].columns and fwds[h][c].notna().any()]
    conds, bases, per_day = [], [], []
    for c in ev:
        f = fwds[h][c].dropna()
        mask = mask_fn(c).reindex(f.index).fillna(False)
        cm = f[mask]
        if len(cm) == 0:
            continue
        conds.append(cm)
        bases.append(f)
        per_day.append((c, float(cm.mean() - f.mean()), int((cm > 0).sum()), int(len(cm))))
    if not conds:
        return {"label": label, "n": 0}
    r = pd.concat(conds, ignore_index=True)
    b = pd.concat(bases, ignore_index=True)
    days = pd.DataFrame(per_day, columns=["c", "excess", "win_n", "n"])
    days["year"] = days["c"].astype(str).str[:4]
    yearly = {
        y: {
            "n": int(g["n"].sum()),
            "excess_bp": round(float(g["excess"].mean()) * 1e4, 1),
            "hit": round(float(g["win_n"].sum() / g["n"].sum()), 3),
        }
        for y, g in days.groupby("year")
    }
    rep = {
        "label": label, "n": int(len(r)),
        "excess_bp": round((float(r.mean()) - float(b.mean())) * 1e4, 1),
        "excess_daily_bp": round(float(days["excess"].mean()) * 1e4, 2),
        "hit": round(float((r > 0).mean()), 3),
        "base_hit": round(float((b > 0).mean()), 3),
        "yearly": yearly,
    }
    print(f"[RESO] {json.dumps(rep, ensure_ascii=False)}")
    return rep


res = []
res.append(group_cond(lambda c: within[c] >= 0.7, "个股·板块内rank≥0.7→fwd3"))
res.append(group_cond(lambda c: s_streak_stock[c] >= 3, "个股·所属板块streak≥3→fwd3"))
res.append(group_cond(lambda c: (s_streak_stock[c] >= 3) & (within[c] >= 0.7), "共振·板块streak≥3∧个股板块内rank≥0.7→fwd3"))
out["resonance"] = res

out["elapsed_s"] = round(time.time() - t0, 1)
Path("/tmp/flow_probe2_out.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(f"[done] {out['elapsed_s']}s")
