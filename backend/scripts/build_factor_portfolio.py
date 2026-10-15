#!/usr/bin/env python3
"""生成推荐因子组合（factor_portfolio.json）——训练勾选的数据来源。

规则与实现见 backend/services/engine/factor_report/portfolio.py：
|ICIR| 门槛 → 覆盖率门槛 → T+20 调仓的扣费净收益 > 0 → 相关性去重 → 最大 ICIR 配权 → 取 top-N。

用法：
  python backend/scripts/build_factor_portfolio.py                     # 本市场数据集全跑（缺省 CN）
  python backend/scripts/build_factor_portfolio.py --market HK         # 港股（l1_factors/ccass/south）
  python backend/scripts/build_factor_portfolio.py --dataset l1_l2_factors
  python backend/scripts/build_factor_portfolio.py --n-top 40 --no-net-gate

产出：<市场根>/<数据集>/report/factor_portfolio.json
下一步：python backend/scripts/apply_factor_selection_to_catalog.py --dataset <数据集>   # 写进训练目录（训练页勾选）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.engine.factor_report.datasets import (  # noqa: E402
    DATASETS_BY_MARKET,
    datasets_for_market,
    market_key,
    supports_market,
)
from backend.services.engine.factor_report.portfolio import build_portfolio, portfolio_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成推荐因子组合 JSON")
    ap.add_argument("--dataset", default="all")
    ap.add_argument("--market", default="CN",
                    help=f"市场（缺省 CN；可用: {', '.join(DATASETS_BY_MARKET)}）")
    ap.add_argument("--n-top", type=int, default=30, help="最终入选因子数上限")
    ap.add_argument("--icir-min", type=float, default=0.15, help="|ICIR| 门槛")
    ap.add_argument("--coverage-min", type=float, default=0.60, help="覆盖率门槛")
    ap.add_argument("--corr-threshold", type=float, default=0.9, help="同源去重阈值")
    ap.add_argument("--no-net-gate", action="store_true", help="关闭「扣费净收益 > 0」门槛")
    ap.add_argument("--holding-days", type=int, default=20, help="调仓周期（成本口径）")
    args = ap.parse_args()

    if not supports_market(args.market):
        ap.error(f"因子报告未接入市场: {args.market!r}（可用: {', '.join(DATASETS_BY_MARKET)}）")
    mk = market_key(args.market)
    reg = datasets_for_market(mk)

    datasets = list(reg) if args.dataset == "all" else [args.dataset]
    datasets = [d for d in datasets if d in reg]
    if not datasets:
        print(f"[{mk}] 无可用数据集（请求 {args.dataset!r}；本市场: {', '.join(reg)}）")
        return 1

    for ds in datasets:
        res = build_portfolio(
            ds,
            n_top=args.n_top,
            icir_min=args.icir_min,
            coverage_min=args.coverage_min,
            corr_threshold=args.corr_threshold,
            require_positive_net=not args.no_net_gate,
            holding_days=args.holding_days,
            market=mk,
        )
        if not res.get("available"):
            print(f"[{mk}/{ds}] 跳过：{res.get('reason')}")
            continue
        s = res["summary"]
        print(f"[{mk}/{ds}] 入选 {s['n_selected']}/{s['n_universe']}（通过门槛 {s['n_passed']}）"
              f"｜组合 IC {s['composite_ic']:+.4f} / ICIR {s['composite_icir']:+.3f}"
              f"（单因子平均 |ICIR| {s['single_icir_avg']:.3f}）")
        top = res["factors"][:5]
        print("   前 5 权重：" + "、".join(f"{f['name']}{f['weight']:+.2f}" for f in top))
        bs = res.get("best_subset") or {}
        if bs.get("available"):

            def _f(v):
                return f"{v:+.2f}" if isinstance(v, (int, float)) else "—"

            ch = bs["chosen"]
            benches = bs["benchmarks"]
            cur = benches.get("current_rule")
            print(
                f"   精选子集 K*={bs['k']}（区间 {bs['k_range'][0]}–{bs['k_range'][1]}，贪心+交换）"
                f"｜train/holdout Sharpe {_f(ch['train_sharpe'])}/{_f(ch['holdout_sharpe'])}"
                f"｜等权 1/N holdout {_f(benches['equal_weight_all'].get('holdout_sharpe'))}"
                f"｜现行规则 holdout {_f(cur.get('holdout_sharpe') if cur else None)}"
            )
            if bs.get("overfit_warning"):
                print(
                    f"   ⚠ train/holdout 比 {bs['sharpe_ratio_train_holdout']} > 3：过拟合旗标"
                )
        else:
            print(f"   精选子集：不可用（{bs.get('reason', '缺 best_subset 块')}）")
        print(f"   → {portfolio_path(ds, mk)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
