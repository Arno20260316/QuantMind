"""港股聪明钱 · 统计层单元测试（合成数据，纯内存）。

见证：双过闸边界 / 完美与反向信号的 IC-命中-不对称 / 样本闸（天数·标的数）/
方向判定 / 相关塌缩贪心（严格大于阈值） / 席位收缩 w=n/(n+20) 与三档窗口 /
episode 首日与嫌疑出统计 / 复合分方向翻转与缺失绝不填 50 / 信号定义域与
域内零流=0 / 日×类型净流合计。

席位 v2（2026-10-16）：方向技能（买 +exc / 卖 −exc）与 90% 下界
（lb = 均值 − 1.28×SE）手工钉死、|Δvalue| 加权、月度/标的聚合自洽、
持续性审计结构（散点门槛 / 秩相关含反向捕获 / 四类出统计）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.shared.hk_smart_money import CCASS_CATEGORIES
from backend.shared.hk_smart_money.stats import (
    _domain,
    _flows_field,
    _holdings_presence,
    _variants_from_flow,
    category_skill,
    collapse_admitted,
    compute_scores,
    corr_matrix,
    daily_category_totals,
    direction_from,
    evaluate,
    seat_metrics,
    seat_monthly,
    seat_persistence,
    seat_symbols,
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


def _seat_ev(
    dt_i,
    pid,
    direction,
    exc,
    *,
    lead=1,
    suspect=False,
    cat="hk",
    symbol="0001.HK",
    value=1e7,
    exc5=None,
    exc20=None,
    name=None,
):
    """单条席位事件行（与真实事件 schema 同构的 v2 必需列齐备）。"""
    return {
        "dt": SEAT_CAL[dt_i].strftime("%Y%m%d"),
        "symbol": symbol,
        "stock_name": f"標的{symbol}",
        "participant_id": pid,
        "participant_name": name or f"SEAT {pid}",
        "category": cat,
        "kind": "broker",
        "direction": direction,
        "delta_value": direction * float(value),
        "fwd_exc_5": exc if exc5 is None else exc5,
        "fwd_exc_10": exc,
        "fwd_exc_20": exc if exc20 is None else exc20,
        "episode_lead": lead,
        "transfer_suspect": suspect,
    }


def _seat_events() -> pd.DataFrame:
    rows: list[dict] = [
        # 旧事件（大单 2e7、另一标的）：出 20 日窗、留全窗——供 vw/n_symbols 区分
        _seat_ev(0, "P1", 1, 0.10, symbol="0003.HK", value=2e7)
    ]
    for i, (d, e) in enumerate(
        [(1, 0.02), (1, 0.03), (1, 0.04), (-1, -0.01), (-1, -0.02), (-1, -0.03)]
    ):
        rows.append(_seat_ev(20 + i, "P1", d, e))
    rows.append(_seat_ev(26, "P1", 1, 0.99, lead=0))  # episode 续存 → 出统计
    rows.append(_seat_ev(27, "P1", 1, -0.50, suspect=True))  # 嫌疑 → 出统计
    rows.append(_seat_ev(28, "P1", 1, 0.50, cat="southbound"))  # 南向 → 出统计
    rows.append(_seat_ev(28, "P2", 1, 0.01))
    rows.append(_seat_ev(29, "P2", 1, 0.01))
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


def test_seat_metrics_v2_skill_lb_vw():
    """v2 技能列手工钉死：skill=买+exc/卖−exc；lb=mean−1.28×SE；vw=|Δvalue| 加权。

    P1 全窗技能 [1000,200,300,400,100,200,300]bp → mean=2500/7、sd=299.205、
    SE=113.089 → lb=212.4；旧事件 2e7 单值使 vw(437.5)≠均值(357.1)。
    """
    out = seat_metrics(_seat_events(), SEAT_CAL)
    row = out[(out["participant_id"] == "P1") & (out["window"] == "all")].iloc[0]
    assert row["skill_bp"] == 357.1
    assert row["skill_t"] == 3.16
    assert row["lb_skill_bp"] == 212.4
    assert row["vw_skill_bp"] == 437.5
    # 各侧原始超额与其余 horizon 对照列
    assert row["buy_exc_bp"] == 475.0 and row["sell_exc_bp"] == -200.0
    assert row["exc5_bp"] == 185.7 and row["exc20_bp"] == 185.7
    assert row["n_symbols"] == 2 and row["avg_value_m"] == 11.43
    assert row["first_event_date"] == SEAT_CAL[0].strftime("%Y%m%d")

    row20 = out[(out["participant_id"] == "P1") & (out["window"] == "20")].iloc[0]
    # 20 窗 6 事件技能 [200,300,400,100,200,300] → mean 250、sd 104.880、
    # SE 42.817 → lb 195.2；等权 → vw==均值
    assert row20["skill_bp"] == 250.0
    assert row20["skill_t"] == 5.84 and row20["lb_skill_bp"] == 195.2
    assert row20["vw_skill_bp"] == 250.0 and row20["avg_value_m"] == 10.0
    assert row20["n_symbols"] == 1 and row20["exc5_bp"] == 50.0

    # P2 仅 2 笔（<SEAT_MIN_EVENTS）：不给 90% 下界（正态近似失效，「—」绝不伪造）；
    # t 不可估 → NaN（读侧 null）
    p2 = out[(out["participant_id"] == "P2") & (out["window"] == "all")].iloc[0]
    assert p2["skill_bp"] == 100.0 and pd.isna(p2["lb_skill_bp"])
    assert pd.isna(p2["skill_t"]) and p2["buy_exc_bp"] == 100.0
    assert pd.isna(p2["sell_exc_bp"])


def test_seat_skill_lb_penalizes_small_sample_noise():
    """90% 下界纪律：小样本噪声席技能均值虽正，lb 仍沉底；平稳席 lb=技能。"""
    rows = [
        _seat_ev(20 + i, "NOISY", 1, e)
        for i, e in enumerate([0.01, -0.005, 0.02, 0.015, -0.01, 0.005, 0.03, -0.015])
    ]
    rows += [_seat_ev(20 + i, "STEADY", 1, 0.01) for i in range(8)]
    rows += [_seat_ev(20 + i, "TINY", 1, 0.02) for i in range(3)]
    out = seat_metrics(pd.DataFrame(rows), SEAT_CAL).set_index(
        ["participant_id", "window"]
    )
    noisy = out.loc[("NOISY", "all")]
    steady = out.loc[("STEADY", "all")]
    tiny = out.loc[("TINY", "all")]
    # NOISY：mean 62.5bp、sd 155.265、SE 54.894 → lb −7.8（弱样本下界转负）
    assert noisy["skill_bp"] == 62.5 and noisy["lb_skill_bp"] == -7.8
    assert noisy["skill_t"] == 1.14
    # STEADY：零方差 → lb=100（不缩），t 不可估（NaN 经读侧落 null）
    assert steady["lb_skill_bp"] == 100.0 and pd.isna(steady["skill_t"])
    assert steady["lb_skill_bp"] > noisy["lb_skill_bp"]
    # TINY：n=3（<SEAT_MIN_EVENTS）零方差——若无门槛会伪造 lb=200 浮顶 → 空
    assert tiny["skill_bp"] == 200.0 and pd.isna(tiny["lb_skill_bp"])


def test_seat_monthly_series():
    out = seat_monthly(_seat_events()).set_index(["participant_id", "month"])
    # 出统计行（lead=0/嫌疑/南向）不落月序：仅 P1×2 月 + P2×1 月
    assert set(out.index) == {("P1", "2026-01"), ("P1", "2026-02"), ("P2", "2026-02")}
    jan, feb = out.loc[("P1", "2026-01")], out.loc[("P1", "2026-02")]
    assert jan["n"] == 1 and jan["skill_bp"] == 1000.0 and jan["vw_skill_bp"] == 1000.0
    assert feb["n"] == 6 and feb["skill_bp"] == 250.0 and feb["vw_skill_bp"] == 250.0
    assert feb["hit"] == 1.0
    p2 = out.loc[("P2", "2026-02")]
    assert p2["n"] == 2 and p2["skill_bp"] == 100.0


def test_seat_symbols_aggregate():
    out = seat_symbols(_seat_events()).set_index(["participant_id", "symbol"])
    p1_main = out.loc[("P1", "0001.HK")]
    assert p1_main["n"] == 6 and p1_main["skill_bp"] == 250.0
    assert p1_main["vw_skill_bp"] == 250.0
    assert p1_main["stock_name"] == "標的0001.HK"
    assert p1_main["last_dt"] == SEAT_CAL[25].strftime("%Y%m%d")
    old = out.loc[("P1", "0003.HK")]
    assert old["n"] == 1 and old["skill_bp"] == 1000.0
    p2 = out.loc[("P2", "0001.HK")]
    assert p2["n"] == 2 and p2["skill_bp"] == 100.0


def test_category_skill_two_categories_and_southbound_excluded():
    df = pd.DataFrame(
        [
            _seat_ev(20, "A", 1, 0.02),  # hk：技能 +200bp
            _seat_ev(21, "B", -1, 0.01, value=3e7),  # hk：卖出落空 → 技能 −100bp
            _seat_ev(22, "C", 1, -0.01, cat="apac"),  # apac：技能 −100bp
            _seat_ev(23, "D", 1, 0.99, cat="southbound"),  # 南向 → 出统计
        ]
    )
    out = category_skill(df)
    assert set(out) == {"hk", "apac"}
    # hk：均值 (200−100)/2=50；vw=(200×1e7 − 100×3e7)/4e7 = −25（单笔巨量主导）
    assert out["hk"] == {"skill_bp": 50.0, "vw_skill_bp": -25.0, "n_lead_events": 2}
    assert out["apac"]["skill_bp"] == -100.0
    assert out["apac"]["n_lead_events"] == 1


def test_seat_persistence_structure_and_empty():
    out = seat_persistence(_seat_events())
    # 切分日 = 事件日中位（20260205）：日历索引 4
    assert out["split_date"] == "2026-02-05"
    assert out["scatter"] == []  # 门槛 n1≥20 ∧ n2≥10：合成小样本全不达
    assert out["spearman"] == {"h10": None, "h20": None, "hit": None}
    assert out["quartiles"] == []  # n1≥50 四分位门槛
    halves = {c["category"]: c for c in out["category_halves"]}
    assert set(halves) == set(CCASS_CATEGORIES)
    # hk 前/后半 vw 技能（权重：旧事件 2e7、其余 1e7）
    assert halves["hk"]["first_half_vw_skill_bp"] == 580.0
    assert halves["hk"]["second_half_vw_skill_bp"] == 160.0
    assert halves["hk"]["n"] == 9
    assert halves["apac"]["first_half_vw_skill_bp"] is None
    assert halves["apac"]["n"] == 0
    assert "稳健" in out["method"]["note"] or "描述性统计" in out["method"]["note"]
    # 全部出统计（全嫌疑）→ 结构仍在、split_date 空、不炸
    empty = seat_persistence(_seat_events().assign(transfer_suspect=True))
    assert empty["split_date"] is None and empty["scatter"] == []


def test_seat_persistence_spearman_persistent_and_reversed():
    """8 席 × 前 30/后 15 事件（跨两门槛）：h10 完全持续 → ρ=1.0；
    单席 h20 仅后半反向 → 秩相关被拉低（伪持续不放过）。"""
    rows: list[dict] = []
    for k in range(8):
        base = (k + 1) * 0.001
        for j in range(30):  # 前半：15 个日期 × 2 事件（n1=30 ≥ 20）
            exc = base + (1e-4 if j % 2 == 0 else -1e-4)
            rows.append(_seat_ev(j // 2, f"S{k}", 1, exc, symbol=f"00{k:02d}.HK"))
        for j in range(15):  # 后半（n2=15 ≥ 10；S7 的 h20 反向=伪持续）
            exc = base + (2e-4 if j % 2 == 0 else -2e-4)
            rows.append(
                _seat_ev(
                    15 + j,
                    f"S{k}",
                    1,
                    exc,
                    symbol=f"00{k:02d}.HK",
                    exc20=-exc if k == 7 else exc,
                )
            )
    out = seat_persistence(pd.DataFrame(rows))
    assert out["split_date"] == SEAT_CAL[15].strftime("%Y-%m-%d")
    assert len(out["scatter"]) == 8
    assert out["scatter"][0]["n1"] == 30 and out["scatter"][0]["n2"] == 15
    assert out["spearman"]["h10"] == 1.0
    # S7 的 h20 后半由最高翻到最低：Σd²=7×1+49=56 → ρ = 1−6×56/(8×63) = 0.333
    assert out["spearman"]["h20"] == 0.333
    # 命中率全 1.0 无变异 → 秩相关不可估 → None（绝不伪造成 1）
    assert out["spearman"]["hit"] is None
    assert out["quartiles"] == []  # n1=30 < 50 仍不进四分位


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
