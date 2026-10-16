"""对比多个训练 run 的样本外指标（同一因子快照 / 同窗口的模型横评）。

读取各 run 目录（`data/training_jobs/<run_id>/`）的 `eval_report.json` +
`metadata.json` + `result.json`，输出 markdown 对比表；`--vs-archive` 时附带
在全部历史 run（档案）中的分位排名——单看绝对值分不清「好」还是「这行情大家都好」。

用法：
    python3 backend/scripts/compare_training_runs.py run_id_1 run_id_2 [run_id_3 ...]
    python3 backend/scripts/compare_training_runs.py --vs-archive run_id_1 run_id_2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_JOBS_DIR = _ROOT / "data/training_jobs"


def _load_run(jobs_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = jobs_dir / run_id
    if not run_dir.is_dir():
        raise SystemExit(f"[compare] run 目录不存在：{run_dir}")
    eval_path = run_dir / "eval_report.json"
    if not eval_path.is_file():
        raise SystemExit(f"[compare] {run_id} 还没有 eval_report.json（未完成？）")
    ev = json.loads(eval_path.read_text(encoding="utf-8"))
    meta: dict[str, Any] = {}
    for name in ("metadata.json", "result.json"):
        p = run_dir / name
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if name == "result.json":
            # result.json 的 metrics 才是按 split 的 {train/val/test: {rmse, auc}}；
            # metadata.json 的 metrics 是扁平 train_ic/val_rank_ic… 另一套口径，别互相污染。
            data = {**(data.get("metadata") or {}), "metrics": data.get("metrics")}
            meta.update(data)
            continue
        for k, v in data.items():
            meta.setdefault(k, v)
    return {"run_id": run_id, "eval": ev, "meta": meta}


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _seg(ev: dict[str, Any], seg: str, key: str) -> Any:
    return ((ev.get("by_split") or {}).get(seg) or {}).get(key)


def _metric(meta: dict[str, Any], seg: str, key: str) -> Any:
    metrics = meta.get("metrics") or {}
    # result.json 的 metrics 用 "val" 命名验证段（by_split 用 "valid"）
    node = metrics.get(seg) or (metrics.get("val") if seg == "valid" else None) or {}
    return node.get(key)


def _row(label: str, cells: list[str]) -> str:
    return f"| {label} | " + " | ".join(cells) + " |"


def _build_table(runs: list[dict[str, Any]]) -> str:
    n = len(runs)
    head = "| 指标 | " + " | ".join(f"{r['meta'].get('model_type') or '?'}（{r['run_id']}）" for r in runs) + " |"
    sep = "|" + "---|" * (n + 1)
    lines = [head, sep]

    def cells(fn) -> list[str]:
        return [fn(r) for r in runs]

    lines.append(_row("job_name", cells(lambda r: str(r["meta"].get("job_name") or "—"))))
    lines.append(_row("特征数", cells(lambda r: _fmt(r["meta"].get("feature_count")))))
    lines.append(_row("best_iteration", cells(lambda r: _fmt(r["meta"].get("best_iteration")))))
    lines.append(_row("耗时(分钟)", cells(lambda r: _fmt((r["meta"].get("elapsed_seconds") or 0) / 60, 1) if r["meta"].get("elapsed_seconds") else "—")))
    for seg, name in (("train", "训练段"), ("valid", "验证段"), ("test", "测试段")):
        lines.append(_row(f"{name} rank_ic", cells(lambda r, s=seg: _fmt(_seg(r["eval"], s, "mean")))))
        lines.append(_row(f"{name} ICIR", cells(lambda r, s=seg: _fmt(_seg(r["eval"], s, "icir")))))
        lines.append(_row(f"{name} win_rate", cells(lambda r, s=seg: _fmt(_seg(r["eval"], s, "win_rate")))))
        lines.append(_row(f"{name} 天数", cells(lambda r, s=seg: _fmt(_seg(r["eval"], s, "n_days"), 0))))
        lines.append(_row(f"{name} rmse / auc", cells(lambda r, s=seg: f"{_fmt(_metric(r['meta'], s, 'rmse'))} / {_fmt(_metric(r['meta'], s, 'auc'), 3)}")))
    def _rk(r: dict[str, Any]) -> dict[str, Any]:
        return r["eval"].get("rank_ic") or {}

    def _ls(r: dict[str, Any]) -> dict[str, Any]:
        return r["eval"].get("long_short") or {}

    def _dr(r: dict[str, Any]) -> dict[str, Any]:
        return r["meta"].get("drift") or {}

    def _yearly(r: dict[str, Any]) -> dict[Any, Any]:
        return {y.get("year"): y.get("mean") for y in (r["eval"].get("yearly") or [])}

    lines.append(_row("全窗 rank_ic / ICIR", cells(lambda r: f"{_fmt(_rk(r).get('mean'))} / {_fmt(_rk(r).get('icir'))}")))
    lines.append(_row("long_short 年化 / Sharpe / maxDD", cells(lambda r: f"{_fmt(_ls(r).get('ann_return'))} / {_fmt(_ls(r).get('sharpe'), 2)} / {_fmt(_ls(r).get('max_drawdown'))}")))
    lines.append(_row("drift severe/medium/stable（maxPSI vs 噪声底）", cells(lambda r: f"{(_dr(r).get('drift') or {}).get('severe', '—')}/{(_dr(r).get('drift') or {}).get('medium', '—')}/{(_dr(r).get('drift') or {}).get('stable', '—')}（{_fmt(_dr(r).get('max_psi'), 3)} vs {_fmt(_dr(r).get('noise_floor'), 3)}）")))
    years = sorted({y for r in runs for y in _yearly(r)})
    for y in years:
        lines.append(_row(f"{y} 年 IC（含训练段）", cells(lambda r, yy=y: _fmt(_yearly(r).get(yy)))))
    return "\n".join(lines)


def _archive_percentiles(jobs_dir: Path, runs: list[dict[str, Any]]) -> str:
    archive: list[dict[str, Any]] = []
    for p in sorted(jobs_dir.glob("*/eval_report.json")):
        try:
            ev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        archive.append({"run_id": p.parent.name, "eval": ev})
    if not archive:
        return "（档案为空）"
    lines = [f"档案样本：{len(archive)} 份 eval_report", "", "| run | 测试段 rank_ic 分位 | 验证段 rank_ic 分位 | 全窗 rank_ic 分位 |", "|---|---|---|---|"]
    for r in runs:
        cells = []
        for getter in (
            lambda e: _seg(e, "test", "mean"),
            lambda e: _seg(e, "valid", "mean"),
            lambda e: (e.get("rank_ic") or {}).get("mean"),
        ):
            vals = sorted((getter(a["eval"]) for a in archive if getter(a["eval"]) is not None), reverse=True)
            v = getter(r["eval"])
            if v is None or not vals:
                cells.append("—")
                continue
            rank = sum(1 for x in vals if x > v) + 1
            cells.append(f"{v:.4f}（#{rank}/{len(vals)}，top {100 * rank / len(vals):.0f}%）")
        lines.append(f"| {r['run_id']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_ids", nargs="+", help="要对比的 run_id（data/training_jobs/<run_id>）")
    ap.add_argument("--jobs-dir", type=Path, default=_DEFAULT_JOBS_DIR)
    ap.add_argument("--vs-archive", action="store_true", help="附全部历史 run 的分位排名")
    args = ap.parse_args()

    runs = [_load_run(args.jobs_dir, rid) for rid in args.run_ids]
    print(_build_table(runs))
    if args.vs_archive:
        print()
        print(_archive_percentiles(args.jobs_dir, runs))


if __name__ == "__main__":
    main()
