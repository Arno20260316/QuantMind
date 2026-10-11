"""T-FE-02：K 因子最佳子集（组合 Sharpe 边际贡献选择）。

锁死的边（细案 `docs/因子挖掘_T-FE-02_K因子最佳子集_细案_20261011.md`）：

1. **边际贡献≠单因子 IC**：贪心每步的目标是「加入后组合 Sharpe 的变化」——
   单因子强但与已选重复的因子会被弱而正交的因子挤掉（构造实例钉死）；
2. **swap 补贪心的洞**：贪心路径存在局部最优，1-swap 必须把它纠正到全局更优；
3. **无泄露**：选择只看训练窗——仅在留出窗有效的因子不得入选；
4. **收益空间口径**：带符号（方向对齐）净日收益 = dir×ls_h/h − 扣费；
   扣费公式与 `_series_stats` 同口径（抽到 optimize 收口，冻结旧实现对拍）；
5. **K 规格**：默认 K∈[10,15]，K* = train Sharpe argmax、平票取小；
   候选不足时退化并如实报 `available:false` 或缩短区间；
6. **基准对照**：等权 1/N 全体候选、现行规则（带符号权重）——同窗同费率手算对拍；
7. **集成不拖垮主链**：best_subset 计算异常时主 payload 照常可用。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.engine.factor_report import best_subset as BS
from backend.services.engine.factor_report import portfolio as PF

ANN = 252.0


# ═══════════════ 手算参考实现（测试内独立复刻，不与被测共用代码）═══════════════


def _ref_sharpe(series: np.ndarray, ann: float = ANN) -> float:
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    return float(s.mean() / s.std() * np.sqrt(ann))


def _mk_df(cols: dict[str, np.ndarray]) -> pd.DataFrame:
    """列名 → 日收益序列（等长）。索引用整数，只看位置与顺序。"""
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.RangeIndex(n))


def _noise(rng, t: int, mean: float, vol: float) -> np.ndarray:
    return mean + vol * rng.normal(size=t)


# ═══════════════ 1. 扣费公式收口 + _series_stats 重构逐位对拍 ═══════════════


def _frozen_cost_daily(
    turnover, holding_days: int, round_trip_cost: float
) -> np.ndarray:
    """冻结旧实现（portfolio.py _series_stats 内联三行的逐字转录）。"""
    d = pd.Series(turnover).fillna(0.0).clip(0.0, 1.0)
    turn_n = (1.0 - (1.0 - d) ** holding_days).clip(0.0, 1.0)
    return (turn_n * round_trip_cost / holding_days).to_numpy()


def test_turnover_cost_daily_与冻结旧实现逐位一致():
    rng = np.random.default_rng(7)
    t = rng.uniform(-0.1, 1.5, 200)  # 含负数/超 1，覆盖 clip 两侧
    t[::13] = np.nan
    t[5] = np.inf
    t[9] = -np.inf
    got = BS.turnover_cost_daily(t, holding_days=20, round_trip_cost=0.002)
    want = _frozen_cost_daily(t, 20, 0.002)
    np.testing.assert_allclose(got, want, rtol=0, atol=0)


def _frozen_series_stats(df: pd.DataFrame, round_trip_cost: float, holding_days: int):
    """冻结旧实现（重构前 _series_stats 的逐行转录，直接吃长表）。"""
    names = set(df.columns)
    ls_col = f"ls_{holding_days}"
    for c in ("turnover", "coverage", ls_col):
        if c in df.columns:
            df[c] = df[c].where(np.isfinite(df[c].astype(float)))
    d = df["turnover"].fillna(0.0).clip(0.0, 1.0)
    turn_n = (1.0 - (1.0 - d) ** holding_days).clip(0.0, 1.0)
    cost_daily = turn_n * round_trip_cost / holding_days
    if ls_col in names:
        gross_daily = df[ls_col].abs().fillna(0.0) / holding_days
    else:
        gross_daily = np.nan
    df = df.assign(net_daily=gross_daily - cost_daily)
    g = df.groupby("factor").agg(
        net_daily=("net_daily", "mean"),
        turnover_daily=("turnover", "mean"),
        coverage=("coverage", "mean"),
    )
    return {
        str(k): {
            kk: float(vv) if np.isfinite(vv) else float("nan") for kk, vv in v.items()
        }
        for k, v in g.to_dict("index").items()
    }


def test_series_stats_重构与冻结旧实现逐位一致(tmp_path, monkeypatch):
    rng = np.random.default_rng(11)
    n = 120
    rows = []
    for fac, ls_mean in (("f1", 0.4), ("f2", -0.2)):
        for i in range(n):
            rows.append(
                {
                    "factor": fac,
                    "date": f"2025{i:04d}",
                    "turnover": float(rng.uniform(0, 1)) if i % 7 else np.nan,
                    "coverage": 0.9,
                    "ls_20": ls_mean + float(rng.normal(0, 0.1)),
                }
            )
    df = pd.DataFrame(rows)
    df.loc[df["factor"] == "f1", "ls_20"] = np.nan  # 缺 ls 的存量也覆盖
    df.iloc[3, df.columns.get_loc("ls_20")] = np.inf

    monkeypatch.setattr(PF, "dataset_dir", lambda ds: tmp_path)
    rep = tmp_path / "report"
    rep.mkdir()
    df.to_parquet(rep / "factor_series.parquet")

    got = PF._series_stats("x", 0.002, 20)
    want = _frozen_series_stats(df.copy(), 0.002, 20)
    assert set(got) == set(want)
    for fac in want:
        for key in want[fac]:
            g, w = got[fac][key], want[fac][key]
            if np.isnan(w):
                assert np.isnan(g), (fac, key)
            else:
                assert g == pytest.approx(w, rel=0, abs=0), (fac, key)


# ═══════════════ 2. 序列加载：方向对齐 + 扣费 ═══════════════


def test_load_net_returns_方向对齐与扣费(tmp_path, monkeypatch):
    monkeypatch.setattr(BS, "dataset_dir", lambda ds: tmp_path)
    rep = tmp_path / "report"
    rep.mkdir()
    n = 60
    rows = []
    for i in range(n):
        rows.append(
            {
                "factor": "f_pos",
                "date": f"d{i:04d}",
                "turnover": 0.2,
                "coverage": 0.9,
                "ls_20": 0.4,
            }
        )
        rows.append(
            {
                "factor": "f_neg",
                "date": f"d{i:04d}",
                "turnover": 0.2,
                "coverage": 0.9,
                "ls_20": -0.4,
            }
        )
    pd.DataFrame(rows).to_parquet(rep / "factor_series.parquet")

    ret = BS.load_net_returns(
        "x",
        ["f_pos", "f_neg"],
        directions={"f_pos": 1, "f_neg": -1},
        holding_days=20,
        round_trip_cost=0.002,
    )
    assert ret is not None
    cost = float(((1 - 0.8**20) * 0.002) / 20)  # ≈ 9.885e-5
    # 负 IC 因子方向翻转后两条收益序列完全一致且为正
    assert ret["f_pos"].iloc[0] == pytest.approx(0.4 / 20 - cost, abs=1e-12)
    assert (ret["f_neg"] == ret["f_pos"]).all()


def test_load_net_returns_尾部全缺行两表对齐(tmp_path, monkeypatch):
    """ls 前向标签未完成的尾日（全因子 NaN）：ls pivot 丢行、turnover pivot 不丢。

    不对齐就是 (45,) vs (50,) 广播错——真实数据 rd_mined 实测 1617 vs 1636 复现。
    """
    monkeypatch.setattr(BS, "dataset_dir", lambda ds: tmp_path)
    rep = tmp_path / "report"
    rep.mkdir()
    rows = []
    for i in range(50):
        for fac, ls in (("a", 0.4), ("b", 0.2)):
            rows.append(
                {
                    "factor": fac,
                    "date": f"d{i:04d}",
                    "turnover": 0.2,
                    "coverage": 0.9,
                    "ls_20": np.nan if i >= 45 else ls,
                }
            )
    pd.DataFrame(rows).to_parquet(rep / "factor_series.parquet")
    ret = BS.load_net_returns(
        "x",
        ["a", "b"],
        directions={"a": 1, "b": 1},
        holding_days=20,
        round_trip_cost=0.002,
    )
    assert ret is not None
    assert len(ret) == 50, "两 pivot 必须对齐到全量日期"
    assert ret["a"].iloc[:45].notna().all()
    assert ret["a"].iloc[45:].isna().all()


def test_load_net_returns_缺列或缺序列返回None(tmp_path, monkeypatch):
    monkeypatch.setattr(BS, "dataset_dir", lambda ds: tmp_path)
    rep = tmp_path / "report"
    rep.mkdir()
    pd.DataFrame(
        {
            "factor": ["f"],
            "date": ["d0"],
            "turnover": [0.1],
            "coverage": [0.9],
            "ls_5": [0.1],
        }
    ).to_parquet(rep / "factor_series.parquet")
    assert (
        BS.load_net_returns(
            "x", ["f"], directions={"f": 1}, holding_days=20, round_trip_cost=0.002
        )
        is None
    )
    # 列存在但候选不在表里 → None
    pd.DataFrame(
        {
            "factor": ["other"],
            "date": ["d0"],
            "turnover": [0.1],
            "coverage": [0.9],
            "ls_20": [0.1],
        }
    ).to_parquet(rep / "factor_series.parquet")
    assert (
        BS.load_net_returns(
            "x", ["f"], directions={"f": 1}, holding_days=20, round_trip_cost=0.002
        )
        is None
    )


# ═══════════════ 3. 选择核心：边际贡献、swap、无泄露 ═══════════════


def test_贪心按边际贡献而非单因子IC_重复强者被正交弱者挤掉():
    """A 单因子最强；B 单因子≈A（近乎重复）；C 单因子稍弱但正交。

    选 2 个时：贪心第 2 步必须选 C（边际提升）而不是 B（单因子更强但被 A 覆盖）。
    """
    t = 400
    rng = np.random.default_rng(3)
    u = rng.normal(size=t)
    v = rng.normal(size=t)
    cols = {
        "A": 0.0026 + 0.01 * u,  # 最强单因子
        "B": 0.0018 + 0.0099 * u + 0.001 * v,  # ≈A：与 A 近乎重复（无分散），单因子不弱
        "C": 0.0018 + 0.01 * v,  # 稍弱于 A 但与 A 正交（A+C 波动减半 → 组合 Sharpe 高）
        "D": _noise(rng, t, 0.0005, 0.01),  # 噪声
        "E": _noise(rng, t, 0.0005, 0.01),  # 噪声
    }
    res = BS.compute_best_subset(
        _mk_df(cols), k_min=2, k_max=2, holding_days=20, round_trip_cost=0.002
    )
    assert res["available"] is True
    names = {f["name"] for f in res["factors"]}
    assert names == {"A", "C"}, f"应选正交的 C 而非重复的 B，实际 {names}"


def test_swap纠正贪心局部最优():
    """经典换手实例：三因子两两相关 0.5 / B-C 负相关。

    贪心（单因子全平票取先者）→ {A,B}；1-swap 应纠正到 {B,C}（全局最优）。
    """
    t = 260
    rng = np.random.default_rng(5)
    u = rng.normal(size=t)
    v = rng.normal(size=t)
    s3 = np.sqrt(3.0) / 2.0
    cols = {
        "A": 0.0026 + 0.01 * u,
        "B": 0.002 + 0.01 * (0.5 * u + s3 * v),  # corr(A,B)=0.5，corr(B,C)=−0.5
        "C": 0.002 + 0.01 * (0.5 * u - s3 * v),
        "D": _noise(rng, t, 0.0, 0.01),
        "E": _noise(rng, t, 0.0, 0.01),
    }
    df = _mk_df(cols)
    R = df.to_numpy(dtype=float)
    greedy = BS._greedy_path(R, 2)
    greedy_names = [df.columns[i] for i in greedy]
    # 前置：贪心第一步选 A（单因子最强）；第二步在 B/C 平票对里取先者——
    # B、C 构造对称，抽到哪个都行，swap 的活是把它纠正到 {B,C}
    assert greedy_names[0] == "A", f"前置：贪心第一步应为 A，实际 {greedy_names}"

    res = BS.compute_best_subset(
        df, k_min=2, k_max=2, holding_days=20, round_trip_cost=0.002
    )
    names = {f["name"] for f in res["factors"]}
    assert names == {"B", "C"}, f"swap 应纠正到 {{B,C}}，实际 {names}"
    g_pair = _ref_sharpe(R[:, greedy].mean(axis=1))
    assert res["chosen"]["train_sharpe"] > g_pair, "交换后的 train Sharpe 必须严格提升"


def test_选择只看训练窗_留出窗强者不得泄露入选():
    t = 300
    rng = np.random.default_rng(9)
    # X：训练窗无效、留出窗很强；Y：训练窗很强、留出窗无效；Z：全程噪声（凑候选数下限）
    x_train = _noise(rng, 210, 0.0, 0.01)
    x_hold = _noise(rng, 90, 0.004, 0.01)
    y_train = _noise(rng, 210, 0.004, 0.01)
    y_hold = _noise(rng, 90, 0.0, 0.01)
    df = _mk_df(
        {
            "X": np.concatenate([x_train, x_hold]),
            "Y": np.concatenate([y_train, y_hold]),
            "Z": _noise(rng, t, 0.0, 0.01),
        }
    )
    res = BS.compute_best_subset(
        df, k_min=1, k_max=1, train_frac=0.7, holding_days=20, round_trip_cost=0.002
    )
    assert [f["name"] for f in res["factors"]] == ["Y"]
    # 披露面：X 的 holdout 更强是事实，但选择面只认训练窗
    assert res["chosen"]["holdout_sharpe"] is not None


def test_K规格与曲线_chosen取train最大值平票取小():
    t = 320
    rng = np.random.default_rng(21)
    cols = {f"f{i}": _noise(rng, t, 0.0022 - 0.0001 * i, 0.01) for i in range(16)}
    res = BS.compute_best_subset(_mk_df(cols), holding_days=20, round_trip_cost=0.002)
    assert res["available"] is True
    assert res["k_range"] == [10, 15]
    assert [row["k"] for row in res["curve"]] == [10, 11, 12, 13, 14, 15]
    best_train = max(row["train_sharpe"] for row in res["curve"])
    assert res["chosen"]["train_sharpe"] == pytest.approx(best_train, abs=1e-9)
    assert res["k"] == res["chosen"]["k"]
    assert len(res["factors"]) == res["k"]


def test_choose_k_平票取小并跳过None():
    curve = [
        {"k": 10, "train_sharpe": 1.5},
        {"k": 11, "train_sharpe": 1.5},  # 平票 → 取 10
        {"k": 12, "train_sharpe": 1.2},
    ]
    assert BS._choose_k(curve) == 10
    assert (
        BS._choose_k([{"k": 10, "train_sharpe": None}, {"k": 11, "train_sharpe": 0.5}])
        == 11
    )
    assert BS._choose_k([{"k": 10, "train_sharpe": None}]) is None


def test_候选不足与样本不足返回不可用():
    df3 = _mk_df(
        {n: _noise(np.random.default_rng(1), 200, 0.002, 0.01) for n in ("a", "b")}
    )
    res = BS.compute_best_subset(df3, holding_days=20, round_trip_cost=0.002)
    assert res["available"] is False and "候选" in res["reason"]

    df_small = _mk_df(
        {n: _noise(np.random.default_rng(2), 60, 0.002, 0.01) for n in ("a", "b", "c")}
    )
    res2 = BS.compute_best_subset(df_small, holding_days=20, round_trip_cost=0.002)
    assert res2["available"] is False and "天数" in res2["reason"]


def test_全因子缺失的尾日行被剔除不当0收益():
    """前向标签未完成的尾日（全因子 NaN）：剔除而非当 0 收益日稀释 Sharpe。"""
    t_valid, t_nan = 150, 20
    rng = np.random.default_rng(71)
    cols = {}
    for i in range(4):
        s = _noise(rng, t_valid, 0.002, 0.01)
        cols[f"f{i}"] = np.concatenate([s, np.full(t_nan, np.nan)])
    res = BS.compute_best_subset(
        _mk_df(cols), k_min=1, k_max=2, holding_days=20, round_trip_cost=0.002
    )
    assert res["available"] is True
    assert res["n_days"]["total"] == t_valid, "全缺尾行必须剔除（不能算进总天数）"
    assert res["n_days"]["train"] + res["n_days"]["holdout"] == t_valid


def test_短历史因子剔除并计数披露():
    t = 300
    rng = np.random.default_rng(31)
    short = np.concatenate([np.full(t - 30, np.nan), _noise(rng, 30, 0.002, 0.01)])
    cols = {f"f{i}": _noise(rng, t, 0.002, 0.01) for i in range(12)}
    cols["shorty"] = short
    res = BS.compute_best_subset(_mk_df(cols), holding_days=20, round_trip_cost=0.002)
    assert res["available"] is True
    assert res["universe"]["n_excluded_short_history"] == 1
    assert res["universe"]["n_candidates"] == 12
    assert all(f["name"] != "shorty" for f in res["factors"])


# ═══════════════ 4. 基准对照（等权 1/N 与现行规则）═══════════════


def test_基准对照与手算对拍():
    t = 300
    rng = np.random.default_rng(41)
    names = [f"g{i}" for i in range(12)]
    cols = {n: _noise(rng, t, 0.002, 0.01) for n in names}
    df = _mk_df(cols)
    R = df.to_numpy(dtype=float)

    w = np.array([0.5, -0.3, 0.2])  # 带符号，gross=1
    res = BS.compute_best_subset(
        df,
        current_rule=(names[:3], w),
        holding_days=20,
        round_trip_cost=0.002,
    )
    split = int(t * res["train_frac"])
    eqw = res["benchmarks"]["equal_weight_all"]
    assert eqw["n_factors"] == 12
    assert eqw["train_sharpe"] == pytest.approx(
        _ref_sharpe(R[:split].mean(axis=1)), abs=2e-4
    )

    cur_series = R[:, :3] @ np.abs(w)  # r 已含方向 → 权重取 |w|（细案 D7 的符号代数）
    assert res["benchmarks"]["current_rule"]["train_sharpe"] == pytest.approx(
        _ref_sharpe(cur_series[:split]), abs=2e-4
    )
    assert "beats_equal_weight" in res["vs_benchmarks"]


def test_current_rule缺失因子权重归一化():
    t = 250
    rng = np.random.default_rng(43)
    cols = {f"g{i}": _noise(rng, t, 0.002, 0.01) for i in range(10)}
    df = _mk_df(cols)
    R = df.to_numpy(dtype=float)
    # 权重里有 2 个因子不在矩阵中 → 按在场因子 |w| 重新归一
    names = ["g0", "g1", "ghost_a", "ghost_b"]
    w = np.array([0.4, 0.4, 0.1, 0.1])
    res = BS.compute_best_subset(
        df, current_rule=(names, w), holding_days=20, round_trip_cost=0.002
    )
    a = np.array([0.5, 0.5])
    split = int(t * res["train_frac"])
    assert res["benchmarks"]["current_rule"]["train_sharpe"] == pytest.approx(
        _ref_sharpe((R[:, :2] @ a)[:split]), abs=2e-4
    )


def test_过拟合旗标与样本提示():
    t = 320
    rng = np.random.default_rng(51)
    cols = {f"f{i}": _noise(rng, t, 0.002, 0.01) for i in range(12)}
    res = BS.compute_best_subset(_mk_df(cols), holding_days=20, round_trip_cost=0.002)
    ratio = res["sharpe_ratio_train_holdout"]
    if ratio is not None:
        assert res["overfit_warning"] == bool(ratio > 3.0)
    assert any("1000" in n for n in res["notes"]), "样本 <1000 天须有论文口径提示"


# ═══════════════ 5. portfolio.py 集成 ═══════════════


def _fake_snapshot(n: int = 14):
    rng = np.random.default_rng(61)
    names = [f"p{i}" for i in range(n)]
    # 对称小相关：非对角 ~N(0, 0.2²)，远离 corr_threshold=0.9 —— 不让去重随机削员
    B = rng.normal(0.0, 0.2, size=(n, n))
    S = (B + B.T) / 2
    np.fill_diagonal(S, 1.0)
    factors = [
        {
            "name": nm,
            "display_name": nm,
            "library": "L1",
            "ic_mean": 0.03 + 0.001 * i,
            "icir": 0.5 + 0.01 * i,
        }
        for i, nm in enumerate(names)
    ]
    return {"correlation": {"factors": names, "matrix": S.tolist()}, "factors": factors}


def _write_series_parquet(tmp_path, names, t=300, seed=63):
    rng = np.random.default_rng(seed)
    rows = []
    for nm in names:
        for i in range(t):
            rows.append(
                {
                    "factor": nm,
                    "date": f"d{i:05d}",
                    "ic": 0.03,
                    "turnover": 0.2,
                    "coverage": 0.9,
                    "ls_20": float(0.4 + rng.normal(0, 0.2)),
                }
            )
    rep = tmp_path / "report"
    rep.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_parquet(rep / "factor_series.parquet")


def test_build_portfolio_集成带best_subset块(tmp_path, monkeypatch):
    snap = _fake_snapshot()
    monkeypatch.setattr(PF, "dataset_dir", lambda ds: tmp_path)
    monkeypatch.setattr(PF, "load_snapshot", lambda ds: snap)
    _write_series_parquet(tmp_path, snap["correlation"]["factors"])

    res = PF.build_portfolio("x", persist=False)
    assert res["available"] is True
    bs = res["best_subset"]
    assert bs["available"] is True
    assert 10 <= bs["k"] <= 15
    assert bs["rule"]["holding_days"] == 20
    assert bs["benchmarks"]["current_rule"]["n_factors"] == 14  # n_top=30 > 候选数


def test_build_portfolio_精选子集异常不拖垮主链(tmp_path, monkeypatch):
    snap = _fake_snapshot()
    monkeypatch.setattr(PF, "dataset_dir", lambda ds: tmp_path)
    monkeypatch.setattr(PF, "load_snapshot", lambda ds: snap)
    _write_series_parquet(tmp_path, snap["correlation"]["factors"])

    def _boom(*a, **k):
        raise RuntimeError("举火")

    monkeypatch.setattr(PF, "compute_best_subset", _boom)
    res = PF.build_portfolio("x", persist=False)
    assert res["available"] is True
    assert res["best_subset"]["available"] is False
    assert "失败" in res["best_subset"]["reason"]
