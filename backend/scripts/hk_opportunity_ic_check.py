#!/usr/bin/env python3
"""港股机会雷达 · 候选因子 IC 与尾部形状验证（离线，仅本机使用）。

目的：机会雷达的六柱评分只允许采入「IC 方向 + 尾部形状」双过闸的因子。
背景（2026-10-15）：ca_broker_pct / ca_cust_share_disc 两个因子出现「全截面
秩 IC」与「五分位尾部差」方向相反的现象——收益关系呈 U 形（非单调），
线性 IC 方向在尾部榜单（Top30）上会反向。机会雷达是纯尾部榜单，
因此每个候选因子必须同时报告：
  - rank IC 流（日度 Spearman，防前视：ret(t→t+h)=close[t+h]/close[t+1]-1）
  - 五分位超额（q1..q5，对当日全市场中位数；收益截尾 ±(-50%,+100%) 防仙股）
  - tail_ok 判据：sign(IC) 与 sign(q5-q1) 一致才可入分柱

用法:
  python backend/scripts/hk_opportunity_ic_check.py                # 全部候选
  python backend/scripts/hk_opportunity_ic_check.py --start 2026-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("hk_opportunity_ic_check")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

OUT_CSV = Path(PROJECT_ROOT) / "logs" / "hk_opportunity_ic_check.csv"
MIN_NAMES_PER_DAY = 100
MIN_ABS_IC = 0.01  # 入评分门槛的 IC 最低强度（防 IC≈0 时 sign 判据被噪声通过）
HORIZONS = (5, 10)

# 候选因子（机会雷达分柱映射；数据集 → [列名]）
FACTOR_SPECS: dict[str, list[str]] = {
    "l1_factors": [
        # 动量柱候选
        "mom_ret_5d",
        "mom_ret_20d",
        "mom_ret_60d",
        "mom_breakout_20d",
        "mom_rsi_14",
        # 趋势质量柱候选
        "trend_r2_20",
        "trend_slope_20",
        "price_position_20",
        "price_position_60",
        "ma_gap_20",
        # 风险柱候选
        "vol_atr_14",
        "vol_downside_20",
        "vol_std_20",
        "vol_realized_rv",
        # 流动性柱候选
        "liq_amount_ratio_5",
        "liq_amihud_20",
        "liq_amount",
        "liq_turnover_os",
        # 量价辅助
        "pv_corr_20",
        "up_volume_ratio_20",
    ],
    "south_factors": [
        "sb_quantity",
        "sb_pct",
        "sb_pct_z20",
        "sb_consec_up",
        "sb_pct_d5",
    ],
    "ccass_factors": [
        "ca_hhi_disc",
        "ca_rank_overlap_20d",
        "ca_n_pis",
        "ca_disclosed_sum",
        "ca_broker_pct",
        "ca_cust_share_disc",
    ],
}


def _hub():
    from backend.services.engine.data_platform.quanthk_hub import QuantHKDataHub

    return QuantHKDataHub()


def _close_matrix(start: pd.Timestamp) -> pd.DataFrame:
    from backend.scripts.update_market_features import load_hk_parquet

    df = load_hk_parquet(start_year=int(start.year) - 1)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df[df["trade_date"] >= start - pd.Timedelta(days=21)]
    return df.pivot_table(
        index="trade_date", columns="instrument", values="close", aggfunc="last"
    ).sort_index()


def _load_factor_wide(
    hub, dataset: str, factor: str, start: pd.Timestamp
) -> pd.DataFrame | None:
    """单因子宽表（date × symbol）；只读 start 之后的分区（按 dt 分区名过滤）。

    列自适应（2026-10-15 修）：l1_factors 分区内 **没有 date 列**（日期由
    hive 分区 dt 承载），旧实现固定取 columns=["symbol","date",factor]
    抛异常被吞 → l1 全部 20 个因子误报「缺失」。现按首个分区的 schema
    探测 date 列是否存在，缺失时用分区名 dt 构造。
    """
    root = hub.data_dir / "6_ml_datasets" / dataset
    if not root.is_dir():
        return None
    floor = start.strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []
    has_date: bool | None = None
    last_err: Exception | None = None
    for f in sorted(root.glob("dt=*/data.parquet")):
        dt_name = f.parent.name[3:]
        if not dt_name.isdigit() or dt_name < floor:
            continue
        try:
            if has_date is None:
                import pyarrow.parquet as pq

                has_date = "date" in pq.ParquetFile(str(f)).schema_arrow.names
            cols = ["symbol", "date", factor] if has_date else ["symbol", factor]
            s = pd.read_parquet(f, columns=cols)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
        if has_date is False:
            s["date"] = pd.Timestamp(dt_name)
        frames.append(s)
    if not frames:
        if last_err is not None:
            log.warning("因子 %s/%s 读取全失败: %s", dataset, factor, last_err)
        return None
    p = pd.concat(frames, ignore_index=True)
    p["date"] = pd.to_datetime(p["date"], errors="coerce")
    return p.pivot_table(
        index="date", columns="symbol", values=factor, aggfunc="mean"
    ).sort_index()


def _evaluate(mat: pd.DataFrame, closes: pd.DataFrame) -> list[dict]:
    """单因子：各 horizon 的 IC 流 + 五分位形状。"""
    rows: list[dict] = []
    for h in HORIZONS:
        fwd = (closes.shift(-h) / closes.shift(-1) - 1).clip(-0.5, 1.0)
        fwd = fwd.replace([np.inf, -np.inf], np.nan)
        ics: list[float] = []
        q_acc: dict[int, list[float]] = {i: [] for i in range(5)}
        for d in mat.index:
            if d not in fwd.index:
                continue
            r = mat.loc[d]
            f = fwd.loc[d]
            ok = r.notna() & f.notna()
            if ok.sum() < MIN_NAMES_PER_DAY:
                continue
            rr = r[ok]
            ff = f[ok]
            # 个别日因子/收益全同值 → corrcoef 出 NaN；过滤后再入流统计
            # （2026-10-15 修：旧实现 NaN 污染均值，ca_n_pis/sb_consec_up 误报 NaN）
            ic_val = float(np.corrcoef(rr.rank(), ff.rank())[0, 1])
            if np.isfinite(ic_val):
                ics.append(ic_val)
            q = pd.qcut(rr.rank(method="first"), 5, labels=False)
            exc = ff - ff.median()
            for i in range(5):
                q_acc[i].append(float(exc[q == i].mean()))
        if len(ics) < 30:
            continue
        arr = np.asarray(ics)
        std = arr.std(ddof=1)
        q_means = [float(np.mean(q_acc[i])) * 1e4 for i in range(5)]
        q51 = q_means[4] - q_means[0]
        ic_mean = float(arr.mean())
        rows.append(
            {
                "horizon": h,
                "n_days": len(arr),
                "rank_ic": round(ic_mean, 4),
                "icir": round(ic_mean / std, 3) if std > 0 else None,
                "ic_pos_rate": round(float((arr > 0).mean()), 3),
                "q1_bp": round(q_means[0], 0),
                "q2_bp": round(q_means[1], 0),
                "q3_bp": round(q_means[2], 0),
                "q4_bp": round(q_means[3], 0),
                "q5_bp": round(q_means[4], 0),
                "q5_minus_q1_bp": round(q51, 0),
                # 双过闸判据：IC 与尾部差同号 + IC 最低强度 |IC|≥MIN_ABS_IC
                # （防 IC≈0 时 sign 噪声通过）+ 样本足够
                "tail_ok": bool(
                    np.sign(ic_mean) == np.sign(q51)
                    and abs(ic_mean) >= MIN_ABS_IC
                    and abs(q51) > 1
                    and len(arr) >= 60
                ),
            }
        )
    return rows


def run(start: pd.Timestamp) -> pd.DataFrame:
    hub = _hub()
    closes = _close_matrix(start)
    log.info("价格矩阵 %d 天 × %d 标的", *closes.shape)
    out_rows: list[dict] = []
    for dataset, factors in FACTOR_SPECS.items():
        for factor in factors:
            mat = _load_factor_wide(hub, dataset, factor, start)
            if mat is None or mat.empty:
                log.warning("跳过缺失因子 %s/%s", dataset, factor)
                continue
            mat = mat.reindex(index=closes.index, columns=closes.columns)
            for row in _evaluate(mat, closes):
                out_rows.append({"dataset": dataset, "factor": factor, **row})
    return pd.DataFrame(out_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="港股机会雷达候选因子 IC/尾部验证")
    parser.add_argument("--start", default="2026-01-01")
    args, _ = parser.parse_known_args()
    start = pd.Timestamp(args.start)
    df = run(start)
    if df.empty:
        print("无有效结果")
        return 1
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"\n===== 港股机会雷达候选因子验证（start={start.date()}） ===== ({OUT_CSV})")
    show = df[df.horizon == 10].sort_values(
        ["dataset", "rank_ic"],
        key=lambda s: s.abs() if s.name == "rank_ic" else s,
        ascending=False,
    )
    with pd.option_context("display.width", 220, "display.max_rows", 200):
        print(
            show[
                [
                    "dataset",
                    "factor",
                    "n_days",
                    "rank_ic",
                    "icir",
                    "ic_pos_rate",
                    "q1_bp",
                    "q3_bp",
                    "q5_bp",
                    "q5_minus_q1_bp",
                    "tail_ok",
                ]
            ].to_string(index=False)
        )
    ok = df[(df.horizon == 10) & df.tail_ok]
    print(
        f"\n双过闸（tail_ok）因子 {ok['factor'].nunique()} / {df[df.horizon == 10]['factor'].nunique()}"
    )
    for _, r in ok.sort_values(
        "rank_ic", key=lambda s: s.abs(), ascending=False
    ).iterrows():
        print(
            f"  ✓ {r['dataset']:<15s} {r['factor']:<22s} IC={r['rank_ic']:+.4f} ICIR={r['icir']:+.3f} q5-q1={r['q5_minus_q1_bp']:+.0f}bp"
        )
    print("\n未过闸（IC 与尾部反向或过弱）：")
    bad = df[(df.horizon == 10) & ~df.tail_ok]
    for _, r in bad.iterrows():
        print(
            f"  ✗ {r['dataset']:<15s} {r['factor']:<22s} IC={r['rank_ic']:+.4f} q5-q1={r['q5_minus_q1_bp']:+.0f}bp"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
