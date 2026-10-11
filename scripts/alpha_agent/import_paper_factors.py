"""T-FM-01 论文因子落库：报告 JSON → 因子池 ``rd_agent_factors``。

把 FactorMiner 论文 110 因子（T-FM-01 实测报告）按 tier 落进因子池：

- **factor_code = ``fm:`` + FM 原式**——求值走 ``factorminer.runtime`` 的
  白名单求值器（``routers/alpha_agent._detect_factor_kind`` 识别，子进程
  隔离）；与统计派的裸 qlib 表达式同 kind 不同 dialect；
- **身份 = ``statistical.dedup.stable_factor_id``**（candidate_key
  ``paper_fmNNN`` + 固定 task_fingerprint）——同报告复跑幂等：同 fid 恒同
  factor_id，重复导入只会更新名称/代码/指标，不产生新行；
- **metadata 与统计派同形**（source/algorithm/market/universe/horizons/
  fdr_*/fold_*/reselect_* 等），另加 ``qlib_expression``（31 条 cs-free 的
  可携带翻译，审计/移植用；CsRank 类为 null 并附 ``qlib_export_gap``）；
  **刻意不写 ``formulation`` 键**——admin 管线 ``_extract_qlib_expression``
  会把它当 qlib 表达式直喂求值器，FM 原式不是 qlib 语法；
- 默认 **dry-run**（打印逐条身份/翻译状态），``--apply`` 才真写。

用法（宿主或容器；容器里 qlib 数据路径固定 /data）：

    python scripts/alpha_agent/import_paper_factors.py            # dry-run, tier=fdr
    python scripts/alpha_agent/import_paper_factors.py --tier selected
    python scripts/alpha_agent/import_paper_factors.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import (  # noqa: E402
    qlib_export,
)
from backend.services.engine.alpha_agent.factorminer.dsl import (  # noqa: E402
    FM_CODE_PREFIX,
)
from backend.services.engine.alpha_agent.statistical import dedup as dedup_mod  # noqa: E402
from backend.services.engine.alpha_agent.statistical.serialize import (  # noqa: E402
    json_safe,
)

TASK_ID = "T-FM-01"
#: 固定指纹 ⇒ 同 fid 跨复跑恒同身份（报告重跑后重导只更新、不新增）
TASK_FINGERPRINT = "tfm01-paper-110"
DATA_SOURCE = "factorminer"
LITERATURE = "FactorMiner (arXiv:2602.14670) 附录 D"
DEFAULT_USER_ID = "10000001"
_REPORT_NAME = "T-FM-01_factor_test_csi300_2021-10-09_2025-10-09.json"
#: 报告路径候选：宿主 ROOT/data 是符号链接直达数据盘；容器里 /app/data 是
#: 空目录、真数据卷挂在 /data——按存在性择一（显式覆盖用 --report）。
_HOST_REPORT = ROOT / "data" / "refs" / "factorminer" / _REPORT_NAME
_CONTAINER_REPORT = Path("/data/refs/factorminer") / _REPORT_NAME
TIERS = ("fdr", "selected", "all")


def default_report() -> Path:
    """宿主 → 容器存在性择一；都不在时返回宿主候选（报错即见预期路径）。"""
    for candidate in (_HOST_REPORT, _CONTAINER_REPORT):
        if candidate.is_file():
            return candidate
    return _HOST_REPORT


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def select_factors(report: dict, tier: str) -> list[dict]:
    """tier 过滤：``fdr``=过 BH-FDR 的 102 条、``selected``=去重入选 20 条、
    ``all``=全 110。"""
    factors = report["paper"]["factors"]
    if tier == "all":
        return list(factors)
    if tier == "selected":
        kept = set(report["paper"]["dedup"]["kept"])
        return [f for f in factors if f["key"] in kept]
    if tier == "fdr":
        return [f for f in factors if f.get("fdr_horizons")]
    raise ValueError(f"未知 tier：{tier}（可选 {TIERS}）")


def _build_metadata(
    factor: dict,
    report: dict,
    *,
    tier: str,
    scheme: str,
    report_path: Path,
    market: str,
    universe: str,
) -> dict:
    horizons = {
        str(h["horizon"]): {k: v for k, v in h.items() if k != "horizon"}
        for h in factor["horizons"]
    }
    exported = qlib_export.export(factor["expression"])
    meta = {
        "source": DATA_SOURCE,
        "algorithm": "factorminer",
        "literature": LITERATURE,
        "market": market,
        "universe": universe,
        "task_id": TASK_ID,
        "task_fingerprint": TASK_FINGERPRINT,
        "identity_scheme": scheme,
        "candidate_key": factor["key"],
        "fid": factor["fid"],
        "tier": tier,
        "selected": bool(factor.get("selected")),
        "fm_expression": factor["expression"],
        "qlib_expression": exported.expression,
        "qlib_export_gap": exported.gap,
        "min_history": factor.get("min_history"),
        "complexity": factor.get("complexity"),
        "best_horizon": factor.get("best_horizon"),
        "horizons": horizons,
        "rank_ic_mean": factor.get("rank_ic_mean"),
        "rank_ic_win_rate": factor.get("rank_ic_win_rate"),
        "t_stat": factor.get("t_stat"),
        "p_value": factor.get("p_value"),
        "icir": factor.get("icir"),
        "rank_icir": factor.get("icir"),
        "fdr_pass": bool(factor.get("fdr_horizons")),
        "fdr_horizons": factor.get("fdr_horizons", []),
        "fdr_q": report.get("fdr_q"),
        "fold_ics": factor.get("fold_ics"),
        "fold_sign_consistency": factor.get("fold_sign_consistency"),
        "stability_eligible": factor.get("stability_eligible"),
        "stability_note": factor.get("stability_note"),
        "selection_score": factor.get("selection_score"),
        "reselect_rate": factor.get("reselect_rate"),
        "reselect_hits": factor.get("reselect_hits"),
        "reselect_checkpoints": factor.get("reselect_checkpoints"),
        "max_abs_corr": factor.get("max_abs_corr"),
        "corr_warn": factor.get("corr_warn"),
        "max_abs_corr_vs_house": factor.get("max_abs_corr_vs_house"),
        "closest_house": factor.get("closest_house"),
        "reject_reason": factor.get("reject_reason"),
        "window": report.get("window"),
        "returns_convention": report.get("returns_convention"),
        "neutralize_mode": report.get("neutralize"),
        "tradability_filter": (report.get("tradability") or {}).get("filter"),
        "vwap_convention": report.get("vwap_convention"),
        "report": str(report_path),
    }
    return json_safe(meta)


def build_rows(
    report: dict,
    *,
    tier: str,
    report_path: Path,
    user_id: str = DEFAULT_USER_ID,
    market: str = "a_share",
    universe: str = "csi300",
) -> list[dict]:
    """报告 + tier → 待落库行（纯函数：不触库，dry-run 与 --apply 共用）。"""
    rows: list[dict] = []
    for factor in select_factors(report, tier):
        key = factor["key"]
        fm_expr = factor["expression"]
        factor_id, scheme = dedup_mod.stable_factor_id(
            user_id=user_id,
            market=market,
            universe=universe,
            candidate_key=f"paper_{key}",
            task_fingerprint=TASK_FINGERPRINT,
            task_id=TASK_ID,
        )
        meta = _build_metadata(
            factor,
            report,
            tier=tier,
            scheme=scheme,
            report_path=report_path,
            market=market,
            universe=universe,
        )
        horizons = meta["horizons"]
        h1 = horizons.get("1") or next(iter(horizons.values()), {})
        window = report.get("window") or {}
        rows.append(
            {
                "factor_id": factor_id,
                "scheme": scheme,
                "key": key,
                "factor_name": f"FactorMiner 论文因子（{key}）",
                "factor_code": FM_CODE_PREFIX + fm_expr,
                "factor_formulation": fm_expr,
                "metadata": meta,
                "qlib_expression": meta["qlib_expression"],
                "qlib_gap": meta["qlib_export_gap"],
                "ic_value": h1.get("ic_mean"),
                "rank_ic": factor.get("rank_ic_mean"),
                "icir": factor.get("icir"),
                "date_range": f"{window.get('start', '?')}~{window.get('end', '?')}",
                "user_id": user_id,
                "market": market,
                "universe": universe,
            }
        )
    return rows


async def apply_rows(rows: list[dict], *, persistence) -> dict:
    """真写：每行 save_factor（新行 status=pending）+ update_factor_metrics
    （status=completed 带指标）。失败向上抛，由调用方记账。"""
    await persistence.ensure_tables()
    for row in rows:
        await persistence.save_factor(
            factor_id=row["factor_id"],
            factor_name=row["factor_name"],
            factor_code=row["factor_code"],
            user_id=row["user_id"],
            metadata=row["metadata"],
            market=row["market"],
            universe=row["universe"],
            factor_formulation=row["factor_formulation"],
            data_source=DATA_SOURCE,
        )
        await persistence.update_factor_metrics(
            factor_id=row["factor_id"],
            status="completed",
            ic_value=row["ic_value"],
            rank_ic=row["rank_ic"],
            icir=row["icir"],
            rank_icir=row["icir"],
            universe=row["universe"],
            date_range=row["date_range"],
            metadata=row["metadata"],
        )
    return {"saved": len(rows)}


def _fmt_ic(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:+.4f}"
    return "  —  "


def _default_persistence():
    """真写库的 persistence（延迟导入：qlib_app.services 深链会拉 qlib，
    dry-run 与 venv 测试都不该为此买单）。"""
    from backend.services.engine.qlib_app.services.rd_agent_persistence import (  # noqa: PLC0415
        RDAgentFactorPersistence,
    )

    return RDAgentFactorPersistence()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T-FM-01 论文因子落库（默认 dry-run，--apply 才写库）"
    )
    parser.add_argument("--report", default=str(default_report()))
    parser.add_argument("--tier", choices=TIERS, default="fdr")
    parser.add_argument(
        "--apply", action="store_true", help="真写 rd_agent_factors（默认只打印）"
    )
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--market", default="a_share")
    parser.add_argument("--universe", default="csi300")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    report = load_report(report_path)
    rows = build_rows(
        report,
        tier=args.tier,
        report_path=report_path,
        user_id=args.user_id,
        market=args.market,
        universe=args.universe,
    )
    n_qlib = sum(1 for r in rows if r["qlib_expression"])
    for row in rows:
        state = "qlib✓" if row["qlib_expression"] else "FM-only"
        print(
            f"{row['key']:>6}  id={row['factor_id'][:12]}  {state:>7}  "
            f"rank_ic={_fmt_ic(row['rank_ic'])}  {row['factor_name']}"
        )
    print(
        f"共 {len(rows)} 条（tier={args.tier}；可翻译 qlib {n_qlib} / FM-only "
        f"{len(rows) - n_qlib}）；"
        + ("--apply 已给，开始写库" if args.apply else "dry-run（--apply 才写库）")
    )
    if not args.apply:
        return 0

    summary = asyncio.run(apply_rows(rows, persistence=_default_persistence()))
    print(f"落库完成：{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
