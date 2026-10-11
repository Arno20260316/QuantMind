#!/usr/bin/env python3
"""v3 正交检验：板块 streak 超额是独立信息还是价格动量代理？
A. 对照：板块近 3 日涨幅 top 条件 → fwd3 超额（纯动量）
B. 交互：streak≥3 ∧ 低调（涨幅 rank<0.5） vs streak≥3 ∧ 追涨（rank≥0.5）
C. 环境：streak≥3 条件按「入场前 20 日市场等权累计收益」± 分档
"""
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, "/app")

ROOT = Path("/data/quantdb")
L2 = ROOT / "6_ml_datasets/l2_factors"
FW = ROOT / "1_kline_data/daily_forward"
MEMBERS = ROOT / "2_base_sector/sector_concept/sector_members.parquet"
N_WIN = 520

t0 = time.time()
con = duckdb.connect()
dts = sorted(
    d.name[3:] for d in L2.iterdir()
    if d.is_dir() and d.name.startswith("dt=") and d.name[3:].isdigit()
)
win = dts[-(N_WIN + 8):]
l2_files = [str(L2 / f"dt={d}" / "data.parquet") for d in win]
fw_files = [str(FW / f"dt={d}" / "data.parquet") for d in win if (FW / f"dt={d}" / "data.parquet").exists()]

flow = con.execute(
    f"""SELECT symbol, dt, flow_super_net, flow_large_net, flow_net_amount,
        flow_net_ratio, amount FROM read_parquet({l2_files!r})"""
).df()
close = con.execute(f"SELECT symbol, dt, close FROM read_parquet({fw_files!r})").df()
flow["main"] = flow["flow_super_net"] + flow["flow_large_net"]
flow["amount_y"] = flow["amount"] * 1e4
flow = flow[flow["amount_y"] > 0]
sig_main = flow.pivot_table(index="symbol", columns="dt", values="main")
cols = list(sig_main.columns)

px = close.pivot_table(index="symbol", columns="dt", values="close").reindex(columns=cols)
fwd3 = (px.shift(-3, axis=1) / px - 1.0).replace([np.inf, -np.inf], np.nan)

mem = con.execute(
    f"""SELECT Symbol, SectorName FROM read_parquet('{MEMBERS}') WHERE SectorType='行业板块(二级)'"""
).df()
sym2sec = mem.set_index("Symbol")["SectorName"]

# 板块 streak（连续主力净流入日数）
day_main = flow.assign(sector=flow["symbol"].map(sym2sec)).dropna(subset=["sector"])
sec_main = day_main.pivot_table(index="sector", columns="dt", values="main", aggfunc="sum")
sflag = (sec_main > 0).fillna(False).to_numpy()
s_streak = np.zeros_like(sflag, dtype=np.int32)
for j in range(1, sflag.shape[1]):
    s_streak[:, j] = np.where(sflag[:, j], s_streak[:, j - 1] + 1, 0)
s_streak_df = pd.DataFrame(s_streak, index=sec_main.index, columns=list(sec_main.columns))

# 板块近 3 日收益（成分等权，t-2..t）与市场 20 日环境（截至 t）
r1 = (px / px.shift(1, axis=1) - 1.0).replace([np.inf, -np.inf], np.nan)
sec = r1.index.to_series().map(sym2sec)
sec_r1 = r1.groupby(sec.values).mean()
sec_mom3 = sec_r1.rolling(3, min_periods=2, axis=1).sum()
mkt_r1 = r1.mean(axis=0)
mkt_env20 = mkt_r1.rolling(20, min_periods=15).sum()  # 截至 t 的过去 20 日

# 板块等权 fwd3
fwd_cols = {}
for c in cols:
    f = fwd3[c].dropna()
    fwd_cols[c] = f.groupby(f.index.to_series().map(sym2sec).values).mean()
sec_fwd3 = pd.DataFrame(fwd_cols)

sec_cond = []
sec_cond.append({"label": "对照·板块近3日涨幅rank≥0.7（纯动量）", **{}})
rows, bases, per_day = [], [], []
for c in cols:
    if c not in sec_fwd3.columns or c not in sec_mom3.columns:
        continue
    f = sec_fwd3[c].dropna()
    mom = sec_mom3[c].reindex(f.index)
    if mom.notna().sum() < 40:
        continue
    m = mom.rank(pct=True) >= 0.7
    cm = f[m.reindex(f.index).fillna(False)]
    if len(cm) == 0:
        continue
    rows.append(cm); bases.append(f)
    per_day.append((c, float(cm.mean() - f.mean()), int(len(cm))))
r = pd.concat(rows); b = pd.concat(bases)
days = pd.DataFrame(per_day, columns=["c", "excess", "n"])
days["year"] = days["c"].astype(str).str[:4]
rep = {
    "label": "对照·板块近3日涨幅rank≥0.7（纯动量）→fwd3",
    "n": int(len(r)), "excess_bp": round((float(r.mean()) - float(b.mean())) * 1e4, 1),
    "excess_daily_bp": round(float(days["excess"].mean()) * 1e4, 2),
    "yearly": {y: {"n": int(g["n"].sum()), "excess_bp": round(float(g["excess"].mean()) * 1e4, 1)} for y, g in days.groupby("year")},
}
print("[MOM]", json.dumps(rep, ensure_ascii=False))

# B. streak≥3 × 动量 rank 分层
def combo(cond_fn, label):
    rows, bases, per_day = [], [], []
    for c in cols:
        if c not in sec_fwd3.columns or c not in sec_mom3.columns:
            continue
        f = sec_fwd3[c].dropna()
        mom = sec_mom3[c].reindex(f.index)
        st = s_streak_df[c].reindex(f.index).fillna(0) if c in s_streak_df.columns else None
        if st is None or mom.notna().sum() < 40:
            continue
        m = cond_fn(st, mom.rank(pct=True))
        cm = f[m.fillna(False)]
        if len(cm) == 0:
            continue
        rows.append(cm); bases.append(f)
        per_day.append((c, float(cm.mean() - f.mean()), int(len(cm))))
    if not rows:
        return {"label": label, "n": 0}
    r = pd.concat(rows); b = pd.concat(bases)
    days = pd.DataFrame(per_day, columns=["c", "excess", "n"])
    days["year"] = days["c"].astype(str).str[:4]
    rep = {
        "label": label, "n": int(len(r)),
        "excess_bp": round((float(r.mean()) - float(b.mean())) * 1e4, 1),
        "excess_daily_bp": round(float(days["excess"].mean()) * 1e4, 2),
        "yearly": {y: {"n": int(g["n"].sum()), "excess_bp": round(float(g["excess"].mean()) * 1e4, 1)} for y, g in days.groupby("year")},
    }
    print("[COMBO]", json.dumps(rep, ensure_ascii=False))
    return rep


combo(lambda st, mr: (st >= 3) & (mr < 0.5), "streak≥3∧涨幅rank<0.5（低调吸筹）→fwd3")
combo(lambda st, mr: (st >= 3) & (mr >= 0.5), "streak≥3∧涨幅rank≥0.5（追涨确认）→fwd3")

# C. 市场环境分层（截至 t 过去 20 日市场累计）
rows2 = {"up": [], "down": []}
bases2 = {"up": [], "down": []}
for c in cols:
    if c not in sec_fwd3.columns or c not in mkt_env20.index:
        continue
    f = sec_fwd3[c].dropna()
    st = s_streak_df[c].reindex(f.index).fillna(0) if c in s_streak_df.columns else None
    env = mkt_env20.get(c, np.nan)
    if st is None or pd.isna(env):
        continue
    cm = f[st >= 3]
    if len(cm) == 0:
        continue
    k = "up" if env > 0 else "down"
    rows2[k].append(cm); bases2[k].append(f)
for k in ("up", "down"):
    if rows2[k]:
        r = pd.concat(rows2[k]); b = pd.concat(bases2[k])
        rep = {
            "label": f"streak≥3·市场20日环境{'上涨' if k=='up' else '下跌'}→fwd3",
            "n": int(len(r)),
            "excess_bp": round((float(r.mean()) - float(b.mean())) * 1e4, 1),
        }
        print("[ENV]", json.dumps(rep, ensure_ascii=False))

print(f"[done] {time.time()-t0:.0f}s")
