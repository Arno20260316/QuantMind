"""增量正交闸（T-FE-01）：``statistical/incremental.py`` 纯函数回归。

契约（台账 §1.2）：新因子对库（|ρ|≥0.3 的对照列）施密特逐次正交化，
残差对前向收益的 IC_IR 口径 = ``|mean(rank_ic)| / std(rank_ic, ddof=1)``
（与 ``ic_stats.summarize_ic`` 同式）；残差 IC_IR > 0.3 才算「有独立信息」。

本文件钉死的几条**静默错误防线**：

- **前向收益必须按交易日取 t+1**（对照帧含全部交易日）。若实现先按采样日
  截断再 shift，得到的是「下一个采样日」收益（≈2 个交易日跳步），IC 口径
  整体位移而不报错——``test_forward_return_uses_next_trading_day`` 钉死；
- **对照列缺 close 只能判「不可得」**，绝不用别的列顶替（静默降级）；
- **基列顺序必须确定性**（|ρ| 降序、并列按列名）——顺序影响逐次残差，
  shuffle 输入列不许改变结果；
- **完美共线残差全零** → IC 不可算 → 判 ``insufficient_days``（不可验证），
  绝不把「残差恒 0」误报成 IC_IR=0 的 fail 或 pass。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.engine.alpha_agent.statistical import incremental as inc

N_DAYS = 60
N_SYMS = 40


def _panel(n_days: int = N_DAYS, n_syms: int = N_SYMS, seed: int = 5):
    """合成面板：交易日字符串索引 + 次日收益 ``ret``（d 行 = d+1 日收益）。

    ``close`` 由 ``ret`` 逐日复利生成，故 ``fwd_ret_1d`` 在日 d 恰等于
    ``ret[d+1]``——把「预测次日收益」构造成完全可解的确定性关系。
    """
    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 4) for d in range(1, 21)]
    days = days[:n_days]
    syms = [f"SH60{i:04d}" for i in range(n_syms)]
    idx = pd.MultiIndex.from_product([days, syms], names=["trade_date", "symbol"])
    rng = np.random.default_rng(seed)
    ret = pd.Series(
        rng.normal(0, 0.02, (len(days), n_syms)).ravel(), index=idx, name="ret"
    )
    close = ((1.0 + ret).groupby(level=1).cumprod() * 10.0).sort_index()
    # close 在日 d 的水平使 close[d+1]/close[d]-1 恰等于 ret 在 d+1 行的值
    noise1 = pd.Series(rng.normal(size=len(idx)), index=idx)
    noise2 = pd.Series(rng.normal(size=len(idx)), index=idx)
    # alpha[d] = 次日收益的带噪截面百分位（近完美但不完美的预知：日间 IC
    # 有波动，IC_IR 有限；完全预知会 std(IC)=0 → IC_IR 退化，不是本闸语义）
    eta = pd.Series(rng.normal(0, 0.02, len(idx)), index=idx)
    signal = ret.groupby(level=1).shift(-1) + eta
    alpha = signal.groupby(level=0).rank(pct=True) - 0.5
    return days, idx, close, noise1, noise2, alpha


def _controls(idx, close, **cols) -> pd.DataFrame:
    frame = pd.DataFrame({"close": close, **cols}, index=idx)
    return frame


# ── 前向收益：必须走交易日（采样日截断陷阱）────────────────────────────


@pytest.mark.unit
def test_forward_return_uses_next_trading_day():
    """采样日 {d1,d3}（中间隔 d2）：d1 的 fwd 必须用 d2 收盘（+100%），
    而不是「下一个采样日 d3」（+300%）。这里 d 的 close = 2^(d序)。"""
    days = [f"2026-01-{i:02d}" for i in range(1, 6)]  # d1..d5
    syms = ["SH600000"]
    idx = pd.MultiIndex.from_product([days, syms], names=["trade_date", "symbol"])
    close = pd.Series([1.0, 2.0, 4.0, 8.0, 16.0], index=idx)
    panel = pd.DataFrame({"close": close}, index=idx)

    fwd = inc.forward_return_series(panel)

    assert fwd is not None
    assert fwd.loc[(days[0], "SH600000")] == pytest.approx(1.0)  # 2/1-1，t+1=d2
    assert fwd.loc[(days[2], "SH600000")] == pytest.approx(1.0)  # 8/4-1，t+1=d4
    assert np.isnan(fwd.loc[(days[4], "SH600000")])  # 末日无 t+1
    # 空帧 → 不可得
    assert inc.forward_return_series(pd.DataFrame()) is None
    # 有列但缺 close → 不可得（None），绝不拿别的列顶替（不能走空帧守卫）
    assert inc.forward_return_series(pd.DataFrame({"vwap": close})) is None


# ── 对照列日均秩相关（mean_rho_by_column）──────────────────────────────


@pytest.mark.unit
def test_mean_rho_by_column_signs_and_exclude():
    days, idx, _close, noise1, noise2, _alpha = _panel()
    values = noise1 * 3.0 + 1.0  # 与 noise1 同秩
    controls = _controls(idx, _close, noise1=noise1, neg1=-noise1, noise2=noise2)

    rho = inc.mean_rho_by_column(values, controls)

    assert rho["noise1"] == pytest.approx(1.0, abs=1e-9)
    assert rho["neg1"] == pytest.approx(-1.0, abs=1e-9)  # 有符号，降序排序才有意义
    assert abs(rho["noise2"]) < 0.3
    # exclude 的列不进结果
    rho_ex = inc.mean_rho_by_column(values, controls, exclude=("noise1",))
    assert "noise1" not in rho_ex.index
    # 无重叠 → 空系列（调用方回落 0.0/None，不误报）
    assert inc.mean_rho_by_column(values, controls, sample_days={"1999-01-01"}).empty


@pytest.mark.unit
def test_sparse_control_rho_not_deflated_by_coverage():
    """对照列覆盖域窄于 values 时，ρ 必须按「逐列对交集」域算——不得缩水。

    实测史（T-FM-03，2026-10-11）：``rd_cci_20 vs TDXGS_CCI_20`` 真 ρ=0.9985，
    旧实现报 0.7632（缩水比 = sqrt(k_pair/k_wide) = sqrt(3255/5511)≈0.773）。
    根因 = 协方差取逐列 pair 域、new 方差却取「任一对照列非空」的宽域——
    l1 对照列只覆盖部分标的（帧是全票并集）时全体 l1 列被系统性缩水，
    真重复列在 0.9 闸下静默漏过。克隆列（真 ρ=1）是该缺陷的钢性判据。
    """
    days = [f"2026-01-{i:02d}" for i in range(1, 7)]
    syms = [f"SH60{i:04d}" for i in range(60)]
    idx = pd.MultiIndex.from_product([days, syms], names=["trade_date", "symbol"])
    rng = np.random.default_rng(7)
    values = pd.Series(rng.normal(size=len(idx)), index=idx)
    # 稀疏列 = values 在「每日下半区 30 只」上的克隆：values 在这 30 行上的
    # 全局秩恰为 1..30，与稀疏列自身的局部秩逐位相同 → 真 ρ=1。若改取任意
    # 30 只，全局秩带缺口，Pearson-on-rank 本身 <1，克隆判据就不钢性了。
    keep = values.groupby(level=0).rank() <= 30
    sparse = values.where(keep)
    controls = pd.DataFrame({"dense": values * 3.0 + 1.0, "sparse": sparse}, index=idx)

    rho = inc.mean_rho_by_column(values, controls)

    assert rho["dense"] == pytest.approx(1.0, abs=1e-9)
    assert rho["sparse"] == pytest.approx(1.0, abs=1e-9)  # 旧实现≈sqrt(30/60)=0.707


@pytest.mark.unit
def test_matches_pandas_pairwise_corr_on_ragged_panel():
    """金样：逐日 rank → pandas 逐日成对 corr（pairwise-complete）→ 对日均值。

    覆盖「全票列 / 半票列 / 带洞相关列」参差面板——该口径与物化器值级查重
    及审计脚本（T-FM-03 audit_library_corr）必须同源。
    """
    days = [f"2026-01-{i:02d}" for i in range(1, 9)]
    syms = [f"SH60{i:04d}" for i in range(50)]
    idx = pd.MultiIndex.from_product([days, syms], names=["trade_date", "symbol"])
    rng = np.random.default_rng(11)
    values = pd.Series(rng.normal(size=len(idx)), index=idx)
    full = pd.Series(rng.normal(size=len(idx)), index=idx)
    half = rng.normal(size=len(idx))
    half_vals = pd.Series(half, index=idx)
    half_vals[~idx.get_level_values(1).isin(syms[::2])] = np.nan  # 隔一留一
    ragged = values + pd.Series(rng.normal(0, 0.5, len(idx)), index=idx)
    ragged[rng.random(len(idx)) < 0.3] = np.nan  # 30% 随机空洞
    controls = pd.DataFrame(
        {"full": full, "half": half_vals, "ragged": ragged}, index=idx
    )

    rho = inc.mean_rho_by_column(values, controls)

    frame = pd.concat([values.rename("__new__"), controls], axis=1)
    for col in controls.columns:
        per_day = []
        for _day, day_df in frame.groupby(level=0):
            day_ranks = day_df.rank()
            per_day.append(day_ranks["__new__"].corr(day_ranks[col], min_periods=20))
        valid = pd.Series(per_day).dropna()
        expected = valid.mean() if len(valid) >= 5 else float("nan")
        if np.isnan(expected):
            assert np.isnan(rho[col])
        else:
            assert rho[col] == pytest.approx(expected, abs=1e-9)


# ── 残差 IC_IR 主口径 ──────────────────────────────────────────────────


@pytest.mark.unit
def test_no_basis_when_orthogonal_to_library():
    days, idx, close, noise1, noise2, alpha = _panel()
    controls = _controls(idx, close, noise1=noise1, noise2=noise2)

    res = inc.incremental_ic_ir(alpha, controls, sample_days=set(days))

    assert res.status == "no_basis"
    assert res.ic_ir is None and res.n_basis == 0


@pytest.mark.unit
def test_redundant_factor_residual_carries_no_ic():
    """对库完全冗余（与对照同秩）→ 残差≈0 → 判不可验证，绝不误报数字。"""
    days, idx, close, noise1, _noise2, _alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)
    redundant = noise1 * 2.0 + 7.0

    res = inc.incremental_ic_ir(redundant, controls, sample_days=set(days))

    assert res.n_basis == 1 and res.basis == ("noise1",)
    assert res.ic_ir is None
    assert res.status == "insufficient_days"  # 残差恒 0 → 无有效日可算


@pytest.mark.unit
def test_residual_ic_ir_survives_partial_redundancy():
    """部分冗余：残差保留独立信号 → 状态 ok；冗余越重，残差 IC_IR 越低。"""
    days, idx, close, noise1, noise2, alpha = _panel()
    controls = _controls(idx, close, noise1=noise1, noise2=noise2)
    # alpha2 与 noise2 相关（进基），但与 noise1 无关 → 残差≈alpha
    alpha2 = alpha + 0.5 * noise2
    # mixed 重冗余：noise1 压倒性主导（alpha 权重 0.05 → 残差排序被噪音
    # 打散），独立信息应显著低于 alpha2 的残差
    mixed = 1.0 * noise1 + 0.05 * alpha

    res_light = inc.incremental_ic_ir(alpha2, controls, sample_days=set(days))
    res_heavy = inc.incremental_ic_ir(mixed, controls, sample_days=set(days))

    assert res_light.status == "ok" and res_light.ic_ir is not None
    assert res_heavy.status == "ok" and res_heavy.ic_ir is not None
    assert res_light.ic_ir > 0.3, "残差≈完全预知次日收益的 alpha，IC_IR 应显著"
    assert res_light.ic_ir > res_heavy.ic_ir, "冗余越重，残差独立信息越少"
    assert res_light.basis == ("noise2",)


@pytest.mark.unit
def test_basis_exclude_removes_replaced_incumbent():
    """替换受理时排除被顶替列：库沿替换后不含它，残差就不该被它清零。"""
    days, idx, close, _noise1, noise2, alpha = _panel()
    controls = _controls(idx, close, noise2=noise2)
    values = alpha + 0.5 * noise2  # 与 noise2 相关 ≥0.3

    plain = inc.incremental_ic_ir(values, controls, sample_days=set(days))
    excluded = inc.incremental_ic_ir(
        values, controls, sample_days=set(days), basis_exclude=("noise2",)
    )

    assert plain.basis == ("noise2",)
    assert excluded.status == "no_basis" and excluded.n_basis == 0


@pytest.mark.unit
def test_basis_order_is_deterministic_under_column_shuffle():
    """逐次施密特吃顺序：基列必须按 |ρ| 降序、并列按列名——shuffle 不许改结果。"""
    days, idx, close, noise1, noise2, alpha = _panel()
    values = alpha + 0.6 * noise1 + 0.5 * noise2
    frame_a = _controls(idx, close, noise1=noise1, noise2=noise2)
    frame_b = frame_a[["close", "noise2", "noise1"]]  # 换列序，内容一致

    res_a = inc.incremental_ic_ir(values, frame_a, sample_days=set(days))
    res_b = inc.incremental_ic_ir(values, frame_b, sample_days=set(days))

    assert res_a.n_basis == 2
    assert res_a.basis == res_b.basis, "基序由 |ρ| 排序决定，与输入列序无关"
    assert res_a.ic_ir == pytest.approx(res_b.ic_ir, rel=1e-12)


@pytest.mark.unit
def test_insufficient_days_is_unverifiable_not_zero():
    days, idx, close, noise1, _noise2, alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)
    values = noise1 + alpha
    short = set(days[:10])  # < 20 天

    res = inc.incremental_ic_ir(values, controls, sample_days=short)

    assert res.n_basis == 1
    assert res.ic_ir is None
    assert res.status == "insufficient_days"


@pytest.mark.unit
def test_precomputed_rho_reuse_is_exact_and_guarded():
    """物化器复用值级查重的筛选列：同帧同 exclude 时结果必须逐位一致；
    rho 与 basis_exclude 混用（口径打架）必须响亮拒绝。"""
    days, idx, close, _noise1, noise2, alpha = _panel()
    controls = _controls(idx, close, noise2=noise2)
    values = alpha + 0.5 * noise2
    pre = inc.mean_rho_by_column(values, controls, sample_days=set(days))

    reused = inc.incremental_ic_ir(values, controls, sample_days=set(days), rho=pre)
    internal = inc.incremental_ic_ir(values, controls, sample_days=set(days))

    assert reused.status == internal.status == "ok"
    assert reused.basis == internal.basis
    assert reused.ic_ir == pytest.approx(internal.ic_ir, rel=1e-12)
    with pytest.raises(ValueError, match="互斥"):
        inc.incremental_ic_ir(
            values, controls, sample_days=set(days), rho=pre, basis_exclude=("noise2",)
        )


@pytest.mark.unit
def test_unavailable_when_panel_or_close_missing():
    days, idx, close, noise1, _noise2, _alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)

    assert (
        inc.incremental_ic_ir(pd.Series(dtype=float), controls).status == "unavailable"
    )
    assert inc.incremental_ic_ir(noise1, pd.DataFrame()).status == "unavailable"
    no_close = controls.drop(columns="close")
    assert inc.incremental_ic_ir(noise1, no_close).status == "unavailable"


@pytest.mark.unit
def test_all_nan_rho_is_unavailable_never_a_silent_no_basis():
    """有对照列却算不出 ρ（对照列全 NaN）→ unavailable，绝不冒充 no_basis。

    评审 HIGH-1：``abs_rho.empty`` 曾让「ρ 全 NaN」和「无对照列」同走
    no_basis——闸据此 pass「无冗余基」拿绿票。soft 下是审计假绿灯，
    升 hard 即假放过。有候选列而算不出，只能判不可验证。
    """
    days, idx, close, noise1, _noise2, _alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)

    res = inc.incremental_ic_ir(
        noise1,
        controls,
        sample_days=set(days),
        rho=pd.Series({"noise1": float("nan")}),
    )

    assert res.status == "unavailable"
    assert res.ic_ir is None and res.n_basis == 0


@pytest.mark.unit
def test_all_nan_rho_from_tiny_universe_is_unavailable():
    """小截面（每日 12 只 < min_pairs=20）→ ρ 每日被置 NaN → 全 NaN →
    unavailable。复现评审 HIGH-1 的实测路径：12 只/日 × 40 日。"""
    days, idx, close, noise1, _noise2, _alpha = _panel(n_days=40, n_syms=12)
    controls = _controls(idx, close, noise1=noise1)

    rho = inc.mean_rho_by_column(noise1, controls, sample_days=set(days))
    res = inc.incremental_ic_ir(noise1, controls, sample_days=set(days))

    assert len(rho) > 0 and rho.isna().all(), "前提：ρ 有列但全 NaN"
    assert res.status == "unavailable"


@pytest.mark.unit
def test_empty_rho_stays_no_basis_and_below_threshold_stays_pass():
    """对照两条诚实路径：候选列本就为空（len 0）→ no_basis；
    算出来全 < 0.3 → no_basis。它们不是「算不出」，不许被 unavailable 吞。"""
    days, idx, close, noise1, _noise2, _alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)

    empty = inc.incremental_ic_ir(
        noise1, controls, sample_days=set(days), rho=pd.Series(dtype=float)
    )
    below = inc.incremental_ic_ir(
        noise1,
        controls,
        sample_days=set(days),
        rho=pd.Series({"noise1": 0.1, "close": -0.2}),
    )

    assert empty.status == "no_basis"
    assert below.status == "no_basis"
    assert below.ic_ir is None


# ── 替换判据用的同窗 |rankIC| ──────────────────────────────────────────


@pytest.mark.unit
def test_mean_abs_rank_ic_same_window_pairing():
    days, idx, close, noise1, _noise2, alpha = _panel()
    controls = _controls(idx, close, noise1=noise1)
    fwd = inc.forward_return_series(controls)

    ic_alpha, n_alpha = inc.mean_abs_rank_ic(alpha, fwd, sample_days=set(days))
    ic_noise, n_noise = inc.mean_abs_rank_ic(noise1, fwd, sample_days=set(days))

    assert ic_alpha is not None and ic_alpha > 0.5
    assert n_alpha == n_noise, "同一窗口、同一 fwd 口径 → 有效日必须一致"
    # 纯噪音在 40 只小截面上的 mean|日IC| ≈ 0.1 量级（生产 5000 只才 ~0.01），
    # 故用小面板只断言「量级差距」，绝对阈值门槛由生产截面保证
    assert ic_noise is not None and ic_alpha > 3.0 * ic_noise
    # 不足 20 日 → None（宁缺勿凑，替换判据 fail-closed）
    ic_short, n_short = inc.mean_abs_rank_ic(alpha, fwd, sample_days=set(days[:5]))
    assert ic_short is None and n_short < 20
