"""港股聪明钱 · 统计层单元测试（合成数据，纯内存）。

见证：双过闸边界 / 完美与反向信号的 IC-命中-不对称 / 样本闸（天数·标的数）/
方向判定 / 相关塌缩贪心（严格大于阈值） / 席位收缩 w=n/(n+20) 与三档窗口 /
episode 首日与嫌疑出统计 / 复合分方向翻转与缺失绝不填 50 / 信号定义域与
域内零流=0 / 日×类型净流合计。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.shared.hk_smart_money.stats import (
    _domain,
    _flows_field,
    _holdings_presence,
    _variants_from_flow,
    collapse_admitted,
    compute_scores,
    corr_matrix,
    daily_category_totals,
    direction_from,
    evaluate,
    seat_metrics,
    seg_stats,
    tail_ok,
)

SEAT_CAL = pd.bdate_range("2026-01-05", periods=30)


def _perfect_panel(n_days: int = 70, n_names: int = 300, seed: int = 20261016):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-01", periods=n_days)
    syms = [f"{i:04d}.HK" for i in range(n_names)]
    fwd = pd.DataFrame(rng.normal(0, 0.05, (n_days, n_names)), index=days, columns=syms)
    exc = fwd.sub(fwd.median(axis=1), axis=0)
    return fwd, exc


# ---------------------------------------------------------------- 双过闸


def test_tail_ok_gate_boundaries():
    assert tail_ok(0.02, 2.0, 60) is True
    assert tail_ok(-0.02, -2.0, 60) is True
    assert tail_ok(0.02, -2.0, 60) is False  # 符号不一致
    assert tail_ok(0.009, 2.0, 60) is False  # |IC| < 0.01
    assert tail_ok(0.02, 1.0, 60) is False  # |q5−q1| 须严格 > 1bp
    assert tail_ok(0.02, 1.2, 60) is True
    assert tail_ok(0.02, 2.0, 59) is False  # n_days < 60


def test_direction_from():
    assert direction_from({"rank_ic": 0.02}) == 1
    assert direction_from({"rank_ic": -0.001}) == -1
    assert direction_from({"rank_ic": None}) is None
    assert direction_from(None) is None
    assert direction_from({}) is None


# ---------------------------------------------------------------- 评估核心


def test_seg_stats_perfect_and_negated_signal():
    fwd, exc = _perfect_panel()
    pos = seg_stats(fwd, fwd, exc)
    assert pos["rank_ic"] == 1.0
    assert pos["icir"] is None  # 恒 1.0 序列 std=0
    assert pos["ic_pos_rate"] == 1.0
    assert pos["buy_hit"] == 1.0 and pos["sell_hit"] == 1.0
    assert pos["q5_minus_q1_bp"] > 0
    assert pos["tail_ok"] is True
    neg = seg_stats(-fwd, fwd, exc)
    assert neg["rank_ic"] == -1.0
    assert neg["buy_hit"] == 0.0 and neg["sell_hit"] == 0.0  # 反向信号两头都错
    assert neg["q5_minus_q1_bp"] < 0
    assert neg["asym_bp"] < 0
    assert neg["tail_ok"] is True  # sign(IC)=sign(q51) 一致（均负）
    assert direction_from(neg) == -1


def test_seg_stats_sample_gates():
    fwd, exc = _perfect_panel()
    assert seg_stats(fwd.iloc[:29], fwd, exc) is None  # < MIN_SEG_DAYS
    short = seg_stats(fwd.iloc[:30], fwd, exc)
    assert short is not None and short["tail_ok"] is False  # 30 天可算但 n<60
    assert seg_stats(fwd.iloc[:, :50], fwd, exc) is None  # 日截面 < MIN_NAMES_PER_DAY


def test_evaluate_horizons_and_years():
    fwd, exc = _perfect_panel(n_days=70, n_names=150)
    fwd_map = dict.fromkeys((5, 10, 20), (fwd, exc))
    out = evaluate(fwd, fwd_map)
    assert set(out["results"]) == {"5", "10", "20"}
    assert out["results"]["10"]["rank_ic"] == 1.0
    assert set(out["by_year"]["10"]) == {"2025"}
    assert out["by_year"]["10"]["2025"]["rank_ic"] == 1.0


def test_corr_matrix_median_and_min_common_names():
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2026-01-05", periods=30)
    syms = [f"S{i:03d}" for i in range(25)]
    a = pd.DataFrame(rng.normal(size=(30, 25)), index=days, columns=syms)
    c = pd.DataFrame(rng.normal(size=(30, 25)), index=days, columns=syms)
    corr = corr_matrix({"a": a, "b": a * -1.0, "c": c})
    assert corr.loc["a", "b"] == -1.0
    assert np.isnan(corr.loc["a", "a"])
    assert abs(corr.loc["a", "c"]) < 0.3
    # 共同有值 < 20 → 该对 NaN
    corr2 = corr_matrix({"a": a.iloc[:, :15], "b": (a * -1.0).iloc[:, :15]})
    assert np.isnan(corr2.loc["a", "b"])


def test_collapse_admitted_greedy_keeps_stronger_ic():
    corr = pd.DataFrame(
        [[1.0, 0.9, 0.85], [0.9, 1.0, 0.1], [0.85, 0.1, 1.0]],
        index=["s_a", "s_b", "s_c"],
        columns=["s_a", "s_b", "s_c"],
    )
    ic10 = {"s_a": 0.05, "s_b": 0.02, "s_c": 0.03}
    admitted, pairs, collapsed = collapse_admitted(["s_a", "s_b", "s_c"], ic10, corr)
    assert admitted == ["s_a"]
    assert len(pairs) == 2
    assert {c["signal"] for c in collapsed} == {"s_b", "s_c"}
    # 严格大于阈值：ρ=0.8 恰好不塌缩
    corr2 = pd.DataFrame([[1.0, 0.8], [0.8, 1.0]], index=["x", "y"], columns=["x", "y"])
    ok, pairs2, coll2 = collapse_admitted(["x", "y"], {"x": 0.01, "y": 0.02}, corr2)
    assert ok == ["x", "y"] and pairs2 == [] and coll2 == []


# ---------------------------------------------------------------- 席位统计


def _seat_events() -> pd.DataFrame:
    rows: list[dict] = []

    def add(dt_i, pid, direction, exc, lead=1, suspect=False, cat="hk"):
        rows.append(
            {
                "dt": SEAT_CAL[dt_i].strftime("%Y%m%d"),
                "participant_id": pid,
                "participant_name": f"SEAT {pid}",
                "category": cat,
                "kind": "broker",
                "direction": direction,
                "fwd_exc_10": exc,
                "episode_lead": lead,
                "transfer_suspect": suspect,
            }
        )

    add(0, "P1", 1, 0.10)  # 旧事件：出 20 日窗、留全窗
    for i, (d, e) in enumerate(
        [(1, 0.02), (1, 0.03), (1, 0.04), (-1, -0.01), (-1, -0.02), (-1, -0.03)]
    ):
        add(20 + i, "P1", d, e)
    add(26, "P1", 1, 0.99, lead=0)  # episode 续存 → 出统计
    add(27, "P1", 1, -0.50, suspect=True)  # 嫌疑 → 出统计
    add(28, "P1", 1, 0.50, cat="southbound")  # 南向 → 出统计
    add(28, "P2", 1, 0.01)
    add(29, "P2", 1, 0.01)
    return pd.DataFrame(rows)


def test_seat_metrics_shrink_windows_and_eligibility():
    out = seat_metrics(_seat_events(), SEAT_CAL)
    assert set(out["window"]) == {"20", "60", "all"}
    p1 = out[out["participant_id"] == "P1"].set_index("window")
    row = p1.loc["all"]
    # lead=0 / 嫌疑 / 南向 三行都在统计外
    assert row["n_events"] == 7 and row["n_buy"] == 4 and row["n_sell"] == 3
    assert row["n_exc10"] == 7
    assert row["avg_exc_bp"] == 185.7
    assert row["hit"] == 1.0 and row["buy_hit"] == 1.0 and row["sell_hit"] == 1.0
    # 收缩到中性：w = 7/27
    assert row["shrunk_exc_bp"] == 48.1 and row["shrunk_hit"] == 0.63
    assert bool(row["eligible"]) and not bool(row["stability"])
    assert row["last_event_date"] == SEAT_CAL[25].strftime("%Y%m%d")
    # 20 日窗剔除旧事件
    row20 = p1.loc["20"]
    assert row20["n_events"] == 6 and row20["avg_exc_bp"] == 50.0
    assert row20["shrunk_exc_bp"] == 11.5 and row20["shrunk_hit"] == 0.615
    assert bool(row20["eligible"])
    assert abs(row20["t"] - 0.43) < 0.02
    assert p1.loc["60", "n_events"] == 7  # 日历 30 ≤ 60 → 全窗
    p2 = out[out["participant_id"] == "P2"].set_index("window")
    assert p2.loc["all", "n_events"] == 2 and not bool(p2.loc["all", "eligible"])
    assert p2.loc["all", "shrunk_exc_bp"] == 9.1
    assert p2.loc["all", "shrunk_hit"] == 0.545


# ---------------------------------------------------------------- 复合分


def test_compute_scores_direction_flip_and_missing_not_filled():
    day = pd.Timestamp("2026-01-05")
    idx = pd.DatetimeIndex([day])
    panels = {
        "hk::net5_tt": pd.DataFrame(
            {"0001.HK": [1.0], "0002.HK": [2.0], "0003.HK": [np.nan]}, index=idx
        ),
        "us_eu::net5_z": pd.DataFrame(
            {"0001.HK": [np.nan], "0002.HK": [-1.0], "0003.HK": [3.0]}, index=idx
        ),
        "apac::net5_tt": pd.DataFrame(
            {"0001.HK": [np.nan], "0002.HK": [np.nan], "0003.HK": [np.nan]}, index=idx
        ),
    }
    spec = (
        ("hk::net5_tt", 1),
        ("us_eu::net5_z", -1),
        ("apac::net5_tt", 1),
        ("missing::x", 1),
    )
    out = compute_scores(day, panels, spec).set_index("symbol")
    assert out.loc["0001.HK", "score"] == 50.0
    assert out.loc["0002.HK", "score"] == 75.0  # 正向 100 + 负向翻转 50
    assert out.loc["0003.HK", "score"] == 0.0  # 单柱成分 0 —— 缺失绝不填 50
    assert out.loc["0003.HK", "covered"] == 1
    assert out.loc["0002.HK", "covered"] == 2
    assert out.loc["0003.HK", "n_pillars"] == 4
    assert pd.isna(out.loc["0002.HK", "val_apac::net5_tt"])
    assert pd.isna(out.loc["0001.HK", "val_missing::x"])
    empty = compute_scores(day, {}, spec)
    assert empty.empty and "score" in empty.columns


# ---------------------------------------------------------------- 面板构建


def test_flow_variants_domain_and_zero_fill():
    cal = pd.bdate_range("2026-01-05", periods=25)
    syms = pd.Index(["A.HK", "B.HK"])
    last, prev_day = cal[-1], cal[-2]
    flows = pd.DataFrame(
        [
            {
                "dt": last.strftime("%Y%m%d"),
                "symbol": "A.HK",
                "category": "hk",
                "net_value": 1e5,
                "net_qty": 100,
                "n_up": 2,
                "n_dn": 0,
                "n_active": 2,
            }
        ]
    )
    holdings = pd.DataFrame(
        [
            {
                "dt": d.strftime("%Y%m%d"),
                "symbol": "A.HK",
                "category": "hk",
                "qty_sum": 100,
                "n_seats": 1,
            }
            for d in cal
        ]
    )
    pres = _holdings_presence(holdings, cal, syms, "hk")
    dom = _domain(pres)
    assert bool(pres.loc[last, "A.HK"]) and not bool(pres.loc[last, "B.HK"])
    assert bool(dom.loc[cal[0], "A.HK"])  # 当日即域

    def _fill(field: str) -> pd.DataFrame:
        raw = _flows_field(flows, field, cal, syms, "hk")
        return raw.mask(dom & raw.isna(), 0.0)

    amt = pd.DataFrame(1e8, index=cal, columns=syms)
    variants = _variants_from_flow(
        _fill("net_value"),
        _fill("n_up"),
        _fill("n_dn"),
        _fill("n_active"),
        amt,
        dom,
        breadth=True,
    )
    assert set(variants) == {"net5_z", "net5_tt", "net20_tt", "breadth5"}
    # 索引契约：面板与 calendar 同轴（DatetimeIndex）——曾按字符串索引而
    # 与 amount 错位静默全 NaN（2026-10-16 事故），此处钉死
    for mat in variants.values():
        assert mat.index.equals(cal)
    assert variants["net5_tt"].loc[last, "A.HK"] == pytest.approx(2e-4)
    assert variants["net20_tt"].loc[last, "A.HK"] == pytest.approx(5e-5)
    assert variants["net5_z"].loc[last, "A.HK"] == pytest.approx(4.4721, abs=1e-3)
    assert variants["breadth5"].loc[last, "A.HK"] == pytest.approx(1.0)
    # 域内无流 = 0（事实零变动）；std=0 → z 为 NaN
    assert variants["net5_tt"].loc[prev_day, "A.HK"] == 0.0
    assert np.isnan(variants["net5_z"].loc[prev_day, "A.HK"])
    for mat in variants.values():
        assert mat["B.HK"].isna().all()  # 从未持仓 → 缺失，绝不填 0


def test_daily_category_totals():
    flows = pd.DataFrame(
        [
            {"dt": "20260105", "category": "hk", "net_value": 100.0, "net_qty": 5},
            {"dt": "20260105", "category": "hk", "net_value": 50.0, "net_qty": -3},
            {"dt": "20260105", "category": "us_eu", "net_value": 10.0, "net_qty": 0},
            {"dt": "20260106", "category": "hk", "net_value": -200.0, "net_qty": -2},
        ]
    )
    agg = daily_category_totals(flows).set_index(["dt", "category"])
    assert agg.loc[("20260105", "hk"), "net_value"] == 150.0
    assert agg.loc[("20260105", "hk"), "n_symbols"] == 2  # net_qty≠0 的票数
    assert agg.loc[("20260105", "us_eu"), "n_symbols"] == 0
    assert agg.loc[("20260106", "hk"), "net_value"] == -200.0
    assert daily_category_totals(pd.DataFrame()).empty
