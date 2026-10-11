"""资金流延续证据构建器测试（资金流全景 v2 · P2）。

策略（防「验证性假通过」）：
- 合成 tiny QuantDB fixture（24 板块 × 2 标的 × 60 交易日跨年）：L2 资金流 +
  daily_forward + 板块成员三件套，真跑 ``build()`` 全流程（含落盘前自检）；
- 数值对账用**独立朴素参考实现**：测试内纯 python 循环从**原始 parquet** 重建
  「板块净额 → streak → 条件/基准逐日超额」，不 import 构建器任何计算函数。
  构建器输出的 n 与 streak 精确相等；bp/命中率按落盘取整口径给半刻度容差
  （0.1bp → 0.0501；2dp → 0.0051；3dp → 0.00051）。
- 另测：双跑幂等、缺成员表拒落盘（无部分产物）、CLI 退码语义、三产物 schema
  自洽（消费侧 load_* 能读回）。

fixture 结构（使各档 streak 均有真实样本）：
- 逐标的逐日主力净额按 9 日周期 v=(d+s)%9：v<5 为「连 5 正」、v≥5 为「连 4 负」
  （板块和 = 1.4*base −1e6 + 66000s + 1700d，符号恰由 v<5 决定，streak 恰可到 5）；
  同轮次标的加微扰使每个净额值逐日互异（占位阈值 25 不触发）。
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from backend.scripts.market_analysis import build_flow_evidence as bfe
from backend.shared import flow_evidence

N_SECTORS = 24
N_DAYS = 60  # 2024-12-02 起 60 个工作日 → 跨年（2024/2025 两档分年）
_BP_TOL = 0.0501
_DAILY_TOL = 0.0051
_HIT_TOL = 0.00051


def _weekdays(start: dt.date, n: int) -> list[str]:
    out: list[str] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    return out


def _flow_main(d_idx: int, s_idx: int, j: int) -> float:
    """主力净额（元）：每 9 天一个「连 5 正 / 连 4 负」周期，逐标的微扰防占位误杀。"""
    v = (d_idx + s_idx) % 9
    base = 2_000_000.0 if v < 5 else -1_500_000.0
    if j == 0:
        return base + s_idx * 37_000.0 + d_idx * 1_000.0
    return base * 0.4 - 1_000_000.0 + s_idx * 29_000.0 + d_idx * 700.0


def _close(sym_idx: int, d_idx: int) -> float:
    return (
        10.0
        + sym_idx * 0.05
        + d_idx * 0.01
        + math.sin((sym_idx + 1) * (d_idx + 1) * 0.7) * 0.3
    )


@pytest.fixture()
def quantdb_root(tmp_path: Path) -> tuple[Path, list[str], list[str], dict[str, str]]:
    """合成 QuantDB 三件套；返回 (root, days, sectors, symbol→sector)。"""
    root = tmp_path / "quantdb"
    days = _weekdays(dt.date(2024, 12, 2), N_DAYS)
    sectors = [f"合成行业{i:02d}" for i in range(N_SECTORS)]
    sym2sec: dict[str, str] = {}
    for s_idx in range(N_SECTORS):
        for j in range(2):
            sym2sec[f"{600000 + s_idx * 3 + j}.SH"] = sectors[s_idx]

    for d_idx, d in enumerate(days):
        l2_dir = root / bfe.L2_REL / f"dt={d}"
        fw_dir = root / bfe.FW_REL / f"dt={d}"
        l2_dir.mkdir(parents=True)
        fw_dir.mkdir(parents=True)
        l2_rows, fw_rows = [], []
        for sym_idx, sym in enumerate(sym2sec):
            s_idx = sym_idx // 2
            m = _flow_main(d_idx, s_idx, sym_idx % 2)
            # fixture 为「元」口径：flow_net_amount/(amount×1e4) == flow_net_ratio
            l2_rows.append(
                {
                    "symbol": sym,
                    "dt": d,
                    "flow_super_net": m * 0.6,
                    "flow_large_net": m * 0.4,
                    "flow_medium_net": -m * 0.55,
                    "flow_small_net": -m * 0.25,
                    "flow_net_amount": m,
                    "flow_net_ratio": m / 1e9,
                    "amount": 100_000.0,
                }
            )
            fw_rows.append({"symbol": sym, "dt": d, "close": _close(sym_idx, d_idx)})
        pd.DataFrame(l2_rows).to_parquet(l2_dir / "data.parquet", index=False)
        pd.DataFrame(fw_rows).to_parquet(fw_dir / "data.parquet", index=False)

    members_dir = root / bfe.MEMBERS_REL
    members_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": sym, "sector_name": sec, "sector_type": bfe.SECTOR_TYPE_NAME}
            for sym, sec in sym2sec.items()
        ]
    ).to_parquet(members_dir / "sector_members.parquet", index=False)
    return root, days, sectors, sym2sec


# ---------------------------------------------------------------------------
# 朴素参考实现（纯 python 循环，只依赖原始 parquet 与 fixture 已知单位口径）
# ---------------------------------------------------------------------------


def _naive(root: Path, days: list[str], sectors: list[str], sym2sec: dict[str, str]):
    members_of = {sec: [] for sec in sectors}
    for sym, sec in sym2sec.items():
        members_of[sec].append(sym)

    closes: dict[tuple[str, str], float] = {}
    sec_main: dict[tuple[str, str], float | None] = {}
    for d in days:
        fw = pd.read_parquet(root / bfe.FW_REL / f"dt={d}" / "data.parquet")
        l2 = pd.read_parquet(root / bfe.L2_REL / f"dt={d}" / "data.parquet")
        for sym, c in zip(fw["symbol"], fw["close"], strict=True):
            closes[(str(sym), d)] = float(c)
        day_main: dict[str, float] = {}
        for sym, sup, lg in zip(
            l2["symbol"], l2["flow_super_net"], l2["flow_large_net"], strict=True
        ):
            day_main[str(sym)] = float(sup) + float(lg)
        for sec in sectors:
            vals = [day_main[sym] for sym in members_of[sec] if sym in day_main]
            sec_main[(sec, d)] = sum(vals) if vals else None

    def _sym_r1(sym: str, i: int) -> float | None:
        if i == 0:
            return None
        c0, c1 = closes.get((sym, days[i - 1])), closes.get((sym, days[i]))
        if c0 is None or c1 is None or c0 == 0:
            return None
        return c1 / c0 - 1.0

    def stock_ret(sym: str, i: int, h: int, entry_shift: int) -> float | None:
        i0, i1 = i + entry_shift, i + entry_shift + h
        if not (0 <= i0 < len(days) and 0 <= i1 < len(days)):
            return None
        c0, c1 = closes.get((sym, days[i0])), closes.get((sym, days[i1]))
        if c0 is None or c1 is None or c0 == 0:
            return None
        return c1 / c0 - 1.0

    def sec_ret(sec: str, i: int, h: int, mode: str) -> float | None:
        shift = 0 if mode == "fwd" else 1
        vals = [
            r
            for sym in members_of[sec]
            if (r := stock_ret(sym, i, h, shift)) is not None
        ]
        return sum(vals) / len(vals) if vals else None

    # 板块等权日收益（ret 面板列）与市场环境
    sec_r1: dict[tuple[str, str], float | None] = {}
    for sec in sectors:
        for i, d in enumerate(days):
            vals = [r for sym in members_of[sec] if (r := _sym_r1(sym, i)) is not None]
            sec_r1[(sec, d)] = sum(vals) / len(vals) if vals else None
    r1_day: dict[str, float | None] = {}
    for i, d in enumerate(days):
        vals = [r for sym in sym2sec if (r := _sym_r1(sym, i)) is not None]
        r1_day[d] = sum(vals) / len(vals) if vals else None
    env20: dict[str, float | None] = {}
    for i, d in enumerate(days):
        vals = [
            r1_day[days[j]]
            for j in range(max(0, i - 19), i + 1)
            if r1_day[days[j]] is not None
        ]
        env20[d] = sum(vals) if len(vals) >= 15 else None

    streak: dict[tuple[str, str], int] = {}
    for sec in sectors:
        prev = 0
        for j, d in enumerate(days):
            if j == 0:
                # 构建器语义：窗口首日无可续前日，streak 恒 0（即使当日为正）
                streak[(sec, d)] = 0
                continue
            v = sec_main[(sec, d)]
            prev = prev + 1 if (v is not None and v > 0) else 0
            streak[(sec, d)] = prev

    def stats(k: int, h: int, mode: str, select=None) -> dict:
        rows = []
        for i, d in enumerate(days):
            if select is not None and not select(i):
                continue
            base = [(sec, sec_ret(sec, i, h, mode)) for sec in sectors]
            base = [(sec, r) for sec, r in base if r is not None]
            cond = [(sec, r) for sec, r in base if streak[(sec, d)] >= k]
            if not cond or not base:
                continue
            rows.append(
                (
                    d,
                    len(cond),
                    sum(r for _, r in cond),
                    sum(1 for _, r in cond if r > 0),
                    len(base),
                    sum(r for _, r in base),
                    sum(1 for _, r in base if r > 0),
                )
            )
        if not rows:
            return {"n": 0}
        nc = sum(x[1] for x in rows)
        nb = sum(x[4] for x in rows)
        sc = sum(x[2] for x in rows)
        sb = sum(x[5] for x in rows)
        wc = sum(x[3] for x in rows)
        wb = sum(x[6] for x in rows)
        yearly = {}
        for y in sorted({x[0][:4] for x in rows}):
            ys = [x for x in rows if x[0][:4] == y]
            n_y = sum(x[1] for x in ys)
            yearly[y] = {
                "n": n_y,
                "excess_bp": sum(x[2] / x[1] - x[5] / x[4] for x in ys) / len(ys) * 1e4,
                "hit": sum(x[3] for x in ys) / n_y,
                "base_hit": sum(x[6] / x[4] for x in ys) / len(ys),
            }
        return {
            "n": nc,
            "excess_bp": (sc / nc - sb / nb) * 1e4,
            "excess_daily_bp": sum(x[2] / x[1] - x[5] / x[4] for x in rows)
            / len(rows)
            * 1e4,
            "hit": wc / nc,
            "base_hit": wb / nb,
            "yearly": yearly,
        }

    return {
        "sec_main": sec_main,
        "sec_r1": sec_r1,
        "streak": streak,
        "stats": stats,
        "r1_day": r1_day,
        "env20": env20,
        "closes": closes,
    }


def _assert_stats(built: dict, ref: dict, ctx: str) -> None:
    if ref.get("n", 0) == 0:
        assert built.get("n", 0) == 0, f"{ctx}: 参考无样本但构建器有"
        return
    assert built["n"] == ref["n"], f"{ctx}: n 不等"
    assert abs(built["excess_bp"] - ref["excess_bp"]) <= _BP_TOL, f"{ctx}: 全窗超额"
    assert (
        abs(built["excess_daily_bp"] - ref["excess_daily_bp"]) <= _DAILY_TOL
    ), f"{ctx}: 日均超额"
    assert abs(built["hit"] - ref["hit"]) <= _HIT_TOL, f"{ctx}: hit"
    assert abs(built["base_hit"] - ref["base_hit"]) <= _HIT_TOL, f"{ctx}: base_hit"
    assert set(built["yearly"]) == set(ref["yearly"]), f"{ctx}: 分年年份集合"
    for y, rv in ref["yearly"].items():
        bv = built["yearly"][y]
        assert bv["n"] == rv["n"], f"{ctx}.{y}: 分年 n"
        assert abs(bv["excess_bp"] - rv["excess_bp"]) <= _BP_TOL, f"{ctx}.{y}: 分年超额"
        assert abs(bv["hit"] - rv["hit"]) <= _HIT_TOL, f"{ctx}.{y}: 分年 hit"
        assert (
            abs(bv["base_hit"] - rv["base_hit"]) <= _HIT_TOL
        ), f"{ctx}.{y}: 分年 base_hit"


@pytest.fixture()
def built(quantdb_root, tmp_path):
    """真跑一次 build()，返回 (summary, evidence, latest, panel, out, refs)。"""
    root, days, sectors, sym2sec = quantdb_root
    out = tmp_path / "evidence"
    summary = bfe.build(data_dir=root, out_dir=out)
    evidence = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    panel = pd.read_parquet(out / "panel_sector.parquet")
    return summary, evidence, latest, panel, out, (root, days, sectors, sym2sec)


def test_build_outputs_and_schema(built):
    summary, evidence, latest, panel, out, (_root, days, sectors, _sym2sec) = built
    assert summary["status"] == "success"
    assert summary["trade_date"] == days[-1]
    assert summary["window"] == [days[0], days[-1]]

    # 三产物齐备且消费侧能读回（schema 门通过）
    assert {p.name for p in out.iterdir()} == {
        "evidence.json",
        "latest.json",
        "panel_sector.parquet",
    }
    assert flow_evidence.load_evidence(out) is not None
    assert flow_evidence.load_latest(out) is not None

    # 覆盖：全板块 × 全交易日（fixture 成员全齐）
    assert evidence["coverage"] == {
        "days": len(days),
        "sectors": N_SECTORS,
        "panel_rows": N_SECTORS * len(days),
    }
    assert len(panel) == N_SECTORS * len(days)
    assert set(panel["dt"]) == set(days)
    assert set(panel["sector"]) == set(sectors)

    # conditions 结构：三档 × 双口径 × 三前向
    assert set(evidence["conditions"]) == {"streak_2", "streak_3", "streak_5"}
    for key, cond in evidence["conditions"].items():
        assert cond["level"] == int(key.split("_")[1])
        for scope in ("fwd", "exec"):
            assert set(cond[scope]) == {"1", "3", "5"}
            for h in bfe.HORIZONS:
                assert cond[scope][str(h)]["n"] > 0
        assert isinstance(cond["gate"]["pass"], bool)
        assert cond["gate"]["evaluated_horizon"] == bfe.BADGE_HORIZON
        assert cond["gate"]["criteria"] == bfe.GATE_CRITERIA
    assert "sub_splits" in evidence["conditions"]["streak_3"]
    assert "sub_splits" not in evidence["conditions"]["streak_2"]
    assert evidence["conditions"]["streak_3"]["sub_splits"]["low_mom"]["n"] > 0
    assert evidence["conditions"]["streak_3"]["sub_splits"]["high_mom"]["n"] > 0
    assert evidence["badge_levels"] == list(flow_evidence.BADGE_LEVELS)
    assert evidence["badge_horizon"] == flow_evidence.BADGE_HORIZON
    assert evidence["min_cond_n"] == flow_evidence.MIN_COND_N

    # 否决清单：八项固定键与 verdict
    rejected = {r["key"]: r for r in evidence["rejected"]}
    assert set(rejected) == {
        "stock_net_ratio_ic",
        "stock_ratio_z20_ic",
        "stock_divergence_ic",
        "stock_within_sector_rank_ic",
        "stock_streak_2",
        "stock_streak_3",
        "stock_streak_5",
        "sector_momentum_control",
    }
    assert rejected["stock_within_sector_rank_ic"]["verdict"] == "background"
    assert rejected["sector_momentum_control"]["verdict"] == "control"
    assert rejected["sector_momentum_control"]["metrics"]["n"] > 0
    for k in (2, 3, 5):
        assert rejected[f"stock_streak_{k}"]["verdict"] == "reject"


def test_build_matches_naive_reference(built):
    """条件统计逐值对账（独立朴素实现从原始 parquet 重建）。"""
    _summary, evidence, latest, panel, _out, (root, days, sectors, sym2sec) = built
    ref = _naive(root, days, sectors, sym2sec)
    conds = evidence["conditions"]

    for k in (2, 3, 5):
        for h in bfe.HORIZONS:
            _assert_stats(
                conds[f"streak_{k}"]["fwd"][str(h)],
                ref["stats"](k, h, "fwd"),
                f"streak_{k}.fwd.{h}",
            )
            _assert_stats(
                conds[f"streak_{k}"]["exec"][str(h)],
                ref["stats"](k, h, "exec"),
                f"streak_{k}.exec.{h}",
            )

    # 环境分层（h=3 fwd）：上下两档的 n 与全窗超额
    env_selectors = {
        "up": lambda i: ref["env20"][days[i]] is not None and ref["env20"][days[i]] > 0,
        "down": lambda i: ref["env20"][days[i]] is not None
        and ref["env20"][days[i]] <= 0,
    }
    for k in (2, 3, 5):
        env_built = conds[f"streak_{k}"]["env"]["3"]
        for key, sel in env_selectors.items():
            rv = ref["stats"](k, 3, "fwd", select=sel)
            bv = env_built[key]
            assert bv["n"] == rv.get("n", 0), f"streak_{k}.env.{key}.n"
            if rv.get("n", 0) > 0:
                assert abs(bv["excess_bp"] - rv["excess_bp"]) <= _BP_TOL
                assert abs(bv["hit"] - rv["hit"]) <= _HIT_TOL

    # 闸门五项：全部可从朴素统计重算，逐项布尔一致
    for k in (2, 3, 5):
        cond = conds[f"streak_{k}"]
        s3f = ref["stats"](k, 3, "fwd")
        s3e = ref["stats"](k, 3, "exec")
        env3 = {
            key: ref["stats"](k, 3, "fwd", select=sel)
            for key, sel in env_selectors.items()
        }
        expected_checks = {
            "n_min": s3f.get("n", 0) >= bfe.MIN_COND_N,
            "exec_h3_positive": float(s3e.get("excess_bp") or 0.0) > 0,
            "fwd_h3_positive": float(s3f.get("excess_bp") or 0.0) > 0,
            "yearly_any_positive": (
                any(v["excess_bp"] > 0 for v in (s3e.get("yearly") or {}).values())
                and any(v["excess_bp"] > 0 for v in (s3f.get("yearly") or {}).values())
            ),
            "env_double_positive": (
                float(env3["up"].get("excess_bp") or 0.0) > 0
                and float(env3["down"].get("excess_bp") or 0.0) > 0
            ),
        }
        assert cond["gate"]["checks"] == expected_checks, f"streak_{k}.gate.checks"
        assert cond["gate"]["pass"] == all(expected_checks.values())
        assert cond["years"] == len(s3f.get("yearly") or {})

    # 面板逐值：streak/net_main/members 精确；net_ratio/ret/mom3 按浮点同构口径
    last_day = days[-1]
    for sec in (sectors[0], sectors[5], sectors[-1]):
        for d in (days[2], days[30], last_day):
            i = days.index(d)
            row = panel[(panel["sector"] == sec) & (panel["dt"] == d)].iloc[0]
            assert int(row["streak"]) == ref["streak"][(sec, d)]
            assert int(row["members"]) == 2
            nm = ref["sec_main"][(sec, d)]
            assert abs(float(row["net_main"]) - nm) <= 0.01
            assert abs(float(row["net_ratio"]) - nm / 2e9) <= 1e-4
            # ret = 板块成员日收益等权均值（首日无前收 → NaN）
            if i > 0:
                assert abs(float(row["ret"]) - ref["sec_r1"][(sec, d)]) <= 1e-9
            else:
                assert pd.isna(row["ret"])
    # mom3：中间日三窗口全可用时 = 近 3 日 sec_r1 之和
    d_mid = days[30]
    row = panel[(panel["sector"] == sectors[0]) & (panel["dt"] == d_mid)].iloc[0]
    expect_mom = sum(ref["sec_r1"][(sectors[0], days[30 - k])] for k in (2, 1, 0))
    assert abs(float(row["mom3"]) - expect_mom) <= 1e-9

    # latest：末日逐板块 + 命中榜（streak≥2，按 −streak, −net_main 排序）
    assert latest["trade_date"] == last_day
    assert set(latest["sectors"]) == set(sectors)
    naive_hits = [
        (sec, ref["streak"][(sec, last_day)])
        for sec in sectors
        if ref["streak"][(sec, last_day)] >= 2
    ]
    naive_hits.sort(key=lambda x: (-x[1], -float(ref["sec_main"][(x[0], last_day)])))
    assert [(h["name"], h["streak"]) for h in latest["hits"]] == naive_hits
    for h in latest["hits"]:
        row = panel[(panel["sector"] == h["name"]) & (panel["dt"] == last_day)].iloc[0]
        assert h["net_main"] == pytest.approx(float(row["net_main"]), abs=0.01)
    for sec, info in latest["sectors"].items():
        assert info["streak"] == ref["streak"][(sec, last_day)]
        assert info["members"] == 2


def test_rebuild_is_idempotent(quantdb_root, tmp_path):
    """同日重跑：除 built_at 外完全等价（产物为确定性构建）。"""
    root, _days, _sectors, _sym2sec = quantdb_root
    out = tmp_path / "evidence"
    bfe.build(data_dir=root, out_dir=out)
    first = {
        name: json.loads((out / name).read_text(encoding="utf-8"))
        for name in ("evidence.json", "latest.json")
    }
    panel_first = pd.read_parquet(out / "panel_sector.parquet")
    bfe.build(data_dir=root, out_dir=out)
    second = {
        name: json.loads((out / name).read_text(encoding="utf-8"))
        for name in ("evidence.json", "latest.json")
    }
    for name in first:
        first[name].pop("built_at")
        second[name].pop("built_at")
        assert first[name] == second[name], f"{name} 两次构建不一致"
    pd.testing.assert_frame_equal(
        panel_first, pd.read_parquet(out / "panel_sector.parquet")
    )


def test_missing_members_refuses_without_partial_artifacts(quantdb_root, tmp_path):
    """缺成员表 → BuildError 且目录里没有任何产物（读到一半的产物比没有更坏）。"""
    root, _days, _sectors, _sym2sec = quantdb_root
    (root / bfe.MEMBERS_REL / "sector_members.parquet").unlink()
    out = tmp_path / "evidence"
    with pytest.raises(bfe.BuildError):
        bfe.build(data_dir=root, out_dir=out)
    assert not out.exists() or not any(out.iterdir())


def test_cli_exit_codes(quantdb_root, tmp_path):
    root, _days, _sectors, _sym2sec = quantdb_root
    empty = tmp_path / "empty_quantdb"
    empty.mkdir()
    # 无 L2 分区 → 1（构建失败，不落盘）
    assert bfe.main(["--data-dir", str(empty), "--out-dir", str(tmp_path / "o1")]) == 1
    # --max-days < 30 → 2（参数错误，未进入构建）
    assert (
        bfe.main(
            [
                "--data-dir",
                str(root),
                "--out-dir",
                str(tmp_path / "o2"),
                "--max-days",
                "10",
            ]
        )
        == 2
    )
    assert not (tmp_path / "o2").exists()
    # --max-days 42（≥30 且 panel 24×42=1008 过落盘自检门槛）→ 0 真构建
    assert (
        bfe.main(
            [
                "--data-dir",
                str(root),
                "--out-dir",
                str(tmp_path / "o3"),
                "--max-days",
                "42",
            ]
        )
        == 0
    )
    assert (tmp_path / "o3" / "latest.json").exists()
