"""因子概率板：产物组包/校验/原子落盘/读取（schema ``factor_board/1.0``）。

落盘纪律：临时文件 + ``os.replace``（读到一半的 JSON 比没有更坏）；同日重跑覆盖；
``latest.json`` 与 ``{decision_date}.json`` 内容一致。缺失一律 JSON ``null``。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from backend.shared.factor_board import config as C
from backend.shared.factor_board import sectors as S

log = logging.getLogger("factor_board.board_io")

_REQUIRED_TOP = (
    "schema_version",
    "meta",
    "factors",
    "horizon",
    "validation",
    "resonance",
    "sectors",
    "stocks",
)


def _clean(x, nd: int = 4):
    """NaN/inf → None；float 取 nd 位。JSON 里绝不允许出现 NaN（非标准）。"""
    if x is None:
        return None
    if isinstance(x, (int, np.integer)):
        return int(x)
    try:
        if not np.isfinite(x):
            return None
    except TypeError:
        return x
    return round(float(x), nd)


def sector_payload(q: pd.DataFrame, members: pd.DataFrame, stype: str) -> dict | None:
    """单类板块（行业二级/一级/概念）payload：quantiles + items（按均分降序）。"""
    sm = members[members["SectorType"] == stype]
    if sm.empty:
        return None
    base = q.merge(
        sm.rename(columns={"Symbol": "symbol"}).set_index("symbol")[["SectorName"]],
        left_index=True,
        right_index=True,
        how="inner",
    )
    g = S.sector_table(base)
    if g.empty:
        return None
    g, q67, q33 = S.apply_verdicts(g)
    g = g.sort_values("fac", ascending=False, na_position="last")
    items = []
    for name, r in g.iterrows():
        items.append(
            {
                "name": str(name),
                "n": int(r["n"]),
                "fac": _clean(r["fac"]),
                "med": _clean(r["med"]),
                "fbreadth": _clean(r["fbreadth"]),
                "mbreadth": _clean(r["mbreadth"]),
                "model_cov": _clean(r["model_cov"]),
                "verdict": str(r["verdict"]),
            }
        )
    return {"q67": _clean(q67), "q33": _clean(q33), "items": items}


def factor_payload(sel: pd.DataFrame, horizon: pd.DataFrame) -> list[dict]:
    """入选因子清单（按 |IC60| 降序），携带持有期画像。"""
    out = []
    hz = horizon if horizon is not None else pd.DataFrame()
    for _, r in sel.sort_values(
        "ic60", key=lambda s: s.abs(), ascending=False
    ).iterrows():
        item = {
            "name": r["name"],
            "lib": r["lib"],
            "group": r["group"],
            "display": r["display"],
            "sign": int(r["sgn"]),
            "ic60": _clean(r["ic60"]),
            "win60": _clean(r["win60"]),
            "icir60": _clean(r["icir60"]),
            "kept_ic": _clean(r["kept_ic"]),
            "weight": _clean(r["w"]),
            "horizon": {},
        }
        if (r["lib"], r["name"]) in hz.index:
            row = hz.loc[(r["lib"], r["name"])]
            item["horizon"] = {str(h): _clean(row.get(h)) for h in C.HORIZON_DAYS}
        out.append(item)
    return out


def horizon_payload(horizon: pd.DataFrame, sel: pd.DataFrame) -> dict:
    """全局 + 分族持有期 |IC|。"""
    if horizon is None or horizon.empty:
        return {"global": {}, "by_group": {}}
    glob = {str(h): _clean(horizon[h].mean(skipna=True)) for h in C.HORIZON_DAYS}
    hz = horizon.join(sel.set_index(["lib", "name"])["group"])
    by_group: dict[str, dict] = {}
    for grp, g in hz.groupby("group"):
        by_group[str(grp)] = {
            str(h): _clean(g[h].mean(skipna=True)) for h in C.HORIZON_DAYS
        }
    return {"global": glob, "by_group": by_group}


def validate_board(board: dict) -> list[str]:
    """schema 自校验：返回错误列表（空=通过）。"""
    errors: list[str] = []
    for key in _REQUIRED_TOP:
        if key not in board:
            errors.append(f"缺顶层字段: {key}")
    if board.get("schema_version") != C.SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {C.SCHEMA_VERSION}")
    meta = board.get("meta") or {}
    if not meta.get("decision_date"):
        errors.append("meta.decision_date 缺失")
    if not meta.get("trade_date"):
        errors.append("meta.trade_date 缺失")
    if (
        not isinstance(board.get("factors"), list)
        or len(board["factors"]) < C.MIN_SELECTED
    ):
        errors.append(f"factors 少于硬门 {C.MIN_SELECTED}")
    sectors = board.get("sectors") or {}
    for key in ("industry_l1", "industry_l2", "concept"):
        if key not in sectors:
            errors.append(f"sectors.{key} 缺失")
    return errors


def write_board(board: dict, out_dir: Path) -> tuple[Path, Path]:
    """原子写 ``{decision_date}.json`` + ``latest.json``。校验不过直接抛。"""
    errors = validate_board(board)
    if errors:
        raise ValueError(f"board schema 自校验失败: {errors}")
    out_dir.mkdir(parents=True, exist_ok=True)
    decision = board["meta"]["decision_date"]
    text = json.dumps(board, ensure_ascii=False, indent=2, allow_nan=False)
    dated = out_dir / f"{decision}.json"
    latest = out_dir / "latest.json"
    _atomic_write(dated, text)
    _atomic_write(latest, text)
    return dated, latest


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_board(out_dir: Path, date: str | None = None) -> dict | None:
    """读取产物：指定日期→该日文件；否则 latest.json。文件缺失/坏 JSON → None。"""
    path = out_dir / f"{date}.json" if date else out_dir / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("读取产物失败 %s: %s", path, exc)
        return None


def available_dates(out_dir: Path) -> list[str]:
    """已产出的决策日列表（降序）。"""
    if not out_dir.is_dir():
        return []
    out = []
    for p in out_dir.glob("*.json"):
        stem = p.stem
        if len(stem) == 10 and stem[4] == "-" and stem.replace("-", "").isdigit():
            out.append(stem)
    return sorted(out, reverse=True)
