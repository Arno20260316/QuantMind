"""因子排行榜「去重」契约（2026-10-15）。

用户语义：排行榜加去重按钮 = 与筛选页同口径（|ρ| ≥ 0.9 同源只留一个）→
去掉重复的行 → 重新排名。四条断言线：

1. ``greedy_dedup`` 逐字镜像筛选页 ``greedy_direct_corr``：按给定序先到先得、
   阈值含等号（``|ρ| ≥ t`` 即剔）、``duplicate_of`` 取已保留中最相关者、
   NaN 对与矩阵未覆盖的 code 必保留（覆盖回退）；
2. 相关矩阵内核 ``pairwise_corr`` 与 pandas ``rank().corr(min_periods=50)`` 逐位
   一致 —— 含 min_periods 边界（49 缺 / 50 有效）、常量列（0/0 → NaN，与 pandas
   同判）、完全同源与反号列（±1）；
3. ``store.dedup_corr``：classic 走 corr.parquet 长表镜像、private 走 npz
   （长因子名不得截断 —— 截断即静默改名，榜单 code 匹配不上永不去重）、缺失 None；
4. ``service.leaderboard(dedup=)``：榜单序贪心（保留更靠前者）、rank 重编号 1..N、
   composite 原值不动、meta.dedup 计数、dedup=0 恒等旧行为、矩阵缺失抛
   ``DedupMatrixMissing`` 且路由译 409。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.scripts.build_factor_panel_corr import pairwise_corr, rank_cols
from backend.services.engine.factor_research import dedup, scorecard, service, store
from backend.services.engine.factor_research import router as fr_router
from backend.services.engine.factor_research.store import DedupMatrixMissing


def _pair_matrix(names: list[str], pairs: dict[tuple[str, str], float]) -> np.ndarray:
    """names + {(a,b): rho} → 对称矩阵（未给出的对为 NaN，对角 1）。"""
    pos = {c: i for i, c in enumerate(names)}
    m = np.full((len(names), len(names)), np.nan)
    for (a, b), r in pairs.items():
        m[pos[a], pos[b]] = r
        m[pos[b], pos[a]] = r
    np.fill_diagonal(m, 1.0)
    return m


# ---------------------------------------------------------------------------
# 1. 贪心去重语义（与筛选页 greedy_direct_corr 同口径）
# ---------------------------------------------------------------------------


def test_greedy_keeps_first_and_drops_later_duplicate():
    names = ["a", "b", "c"]
    m = _pair_matrix(names, {("a", "b"): 0.95, ("a", "c"): 0.5, ("b", "c"): 0.2})

    kept, dropped, dup_of = dedup.greedy_dedup(["a", "b", "c"], names, m, 0.9)

    assert kept == ["a", "c"]
    assert dropped == ["b"]
    assert dup_of == {"b": "a"}


def test_greedy_threshold_is_inclusive():
    """|ρ| 恰好 = 阈值 → 剔除（与筛选页 `abs(rho) >= threshold` 同判）。"""
    names = ["a", "b", "c"]
    m = _pair_matrix(names, {("a", "b"): 0.9, ("a", "c"): 0.899999})

    kept, dropped, _ = dedup.greedy_dedup(["a", "b", "c"], names, m, 0.9)

    assert kept == ["a", "c"]
    assert dropped == ["b"]


def test_greedy_duplicate_of_is_the_strongest_kept():
    """命中多个保留者时，duplicate_of = |ρ| 最大的那个（同筛选页 max(key=)）。"""
    names = ["a", "b", "c"]
    m = _pair_matrix(names, {("a", "b"): 0.1, ("a", "c"): 0.97, ("b", "c"): 0.93})

    kept, dropped, dup_of = dedup.greedy_dedup(["a", "b", "c"], names, m, 0.9)

    assert kept == ["a", "b"]
    assert dropped == ["c"]
    assert dup_of == {"c": "a"}


def test_greedy_uses_absolute_rho():
    """反向同源（ρ = −0.95）同样剔除：口径用 |ρ|。"""
    names = ["a", "b"]
    m = _pair_matrix(names, {("a", "b"): -0.95})

    kept, dropped, _ = dedup.greedy_dedup(["a", "b"], names, m, 0.9)

    assert kept == ["a"]
    assert dropped == ["b"]


def test_greedy_nan_pair_never_drops():
    """矩阵里是 NaN 的对（共同观测不足 / 覆盖不到）不构成重复证据。"""
    names = ["a", "b"]
    m = _pair_matrix(names, {})

    kept, dropped, _ = dedup.greedy_dedup(["a", "b"], names, m, 0.9)

    assert kept == ["a", "b"]
    assert dropped == []


def test_greedy_uncovered_codes_always_kept():
    """矩阵未覆盖的 code 不参与去重（覆盖回退，不误剔）。"""
    names = ["a", "b"]
    m = _pair_matrix(names, {("a", "b"): 0.99})

    kept, dropped, _ = dedup.greedy_dedup(["a", "b", "zz"], names, m, 0.9)

    assert kept == ["a", "zz"]
    assert dropped == ["b"]


def test_greedy_order_defines_survivor():
    """判定序即优先级：先见者保留（榜单序 = 更靠前者活）。"""
    names = ["a", "b"]
    m = _pair_matrix(names, {("a", "b"): 0.95})

    kept, dropped, dup_of = dedup.greedy_dedup(["b", "a"], names, m, 0.9)

    assert kept == ["b"]
    assert dropped == ["a"]
    assert dup_of == {"a": "b"}


def test_greedy_threshold_must_be_positive():
    with pytest.raises(ValueError):
        dedup.greedy_dedup(["a"], ["a"], np.eye(1), 0.0)


# ---------------------------------------------------------------------------
# 2. 相关矩阵内核（构建脚本）vs pandas 金样
# ---------------------------------------------------------------------------


def test_pairwise_corr_matches_pandas_gold():
    """合成金样：并列值 / 缺失边界（49 剔除、50 有效）/ 常量列 / 同源 / 反号。"""
    rng = np.random.default_rng(11)
    n = 72
    X = rng.normal(size=(n, 8)).astype(np.float32)
    X[:, 0] = np.round(X[:, 0], 1)  # 制造并列
    X[0:10, 1] = np.nan  # 列1 缺 10
    X[0:23, 2] = np.nan  # 列2 缺 23 → (1,2) 联合缺 23 → 共同 49 < 50 → NaN
    X[0:9, 3] = np.nan  # 列3 缺 9 → (1,3) 联合 10 → 共同 62 有效
    X[0:8, 4] = np.nan  # 列4 缺 8
    X[8:22, 5] = np.nan  # 列5 缺 14（与列4 不相交）→ (4,5) 共同恰 50 → 有效
    X[:, 6] = 7.0  # 常量列（如某月全 0 的 is_st）→ 全 NaN
    X[:, 7] = -X[:, 0]  # 反号同源

    R = rank_cols(X)
    ref = pd.DataFrame(R).corr(min_periods=50).to_numpy()
    mine = pairwise_corr(R)

    off = ~np.eye(8, dtype=bool)
    ref_f, mine_f = np.isfinite(ref), np.isfinite(mine)
    assert ((ref_f ^ mine_f) & off).sum() == 0, "有效/缺失的形状与 pandas 不符"
    both = ref_f & mine_f & off
    np.testing.assert_allclose(mine[both], ref[both], atol=1e-12)

    # 关键边界逐点钉死（防「恰好都在同一边」的巧合通过）
    assert np.isnan(mine[1, 2]) and np.isnan(ref[1, 2])  # 共同 49 → NaN
    assert np.isfinite(mine[4, 5])  # 共同 50 → 有效
    assert np.isnan(mine[0, 6])  # 常量列 → NaN（0/0，与 pandas 同判）
    assert mine[0, 7] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# 3. store 装载
# ---------------------------------------------------------------------------


def test_matrix_from_pairs_mirrors_and_fills_nan():
    pairs = pd.DataFrame(
        {
            "factor_a": ["a", "a", "b"],
            "factor_b": ["a", "b", "c"],
            "corr": [1.0, 0.9, -0.8],
        }
    )

    names, m = dedup.matrix_from_pairs(pairs)

    assert names == ["a", "b", "c"]
    assert m[0, 1] == m[1, 0] == 0.9
    assert m[1, 2] == m[2, 1] == -0.8
    assert np.isnan(m[0, 2])
    assert (np.diag(m) == 1.0).all()


def test_store_dedup_corr_classic_uses_corr_table(monkeypatch):
    table = pd.DataFrame(
        {"factor_a": ["x", "y"], "factor_b": ["y", "z"], "corr": [0.91, -0.3]}
    )
    monkeypatch.setattr(store, "corr_table", lambda: table)

    names, m = store.dedup_corr("classic")

    assert names == ["x", "y", "z"]
    assert m[0, 1] == 0.91


def test_store_dedup_corr_classic_missing_returns_none(monkeypatch):
    monkeypatch.setattr(store, "corr_table", lambda: None)
    assert store.dedup_corr("classic") is None


def test_store_dedup_corr_private_npz_roundtrip_and_missing(tmp_path, monkeypatch):
    """npz 往返：**长因子名不得截断**（>64 字符；截断即静默改名→永不匹配→永不去重）。"""
    monkeypatch.setattr(store, "artifact_dir", lambda dataset="classic": tmp_path)
    long_name = "(" + "close - ts_mean(close, 20)" * 4 + ")"
    assert len(long_name) > 64
    p = tmp_path / "cross_corr.npz"
    np.savez_compressed(
        p,
        matrix=np.array([[1.0, 0.95], [0.95, 1.0]], dtype=np.float32),
        names=np.asarray([long_name, "b"]),
        used=np.int64(82),
    )

    names, m = store.dedup_corr("private")

    assert names == [long_name, "b"]
    assert m.dtype == np.float32 and m[0, 1] == pytest.approx(0.95)

    p.unlink()
    assert store.dedup_corr("private") is None


# ---------------------------------------------------------------------------
# 4. service.leaderboard 集成（迷你面板）
# ---------------------------------------------------------------------------

# ≥ 7 期：n_months 数的是收益序列（首期是入场、无收益），要比 scorecard.MIN_MONTHS=6
# 多留一期 —— 否则 gate_kpi 把 max_drawdown 等清成 None，全 None 列会在
# composite_scores 的 -df["max_drawdown"] 处炸（object dtype）
_MONTHS = [
    "2026-01-31",
    "2026-02-28",
    "2026-03-31",
    "2026-04-30",
    "2026-05-31",
    "2026-06-30",
    "2026-07-31",
    "2026-08-31",
]
_SYMS = ["000001.SZ", "600000.SH"]


def _tiny_panel() -> scorecard.Panel:
    rows = []
    for code in ("A", "B", "C"):
        for mi, mon in enumerate(_MONTHS):
            for rk, sym in enumerate(_SYMS, 1):
                rows.append(
                    {
                        "factor_code": code,
                        "trade_date": pd.Timestamp(mon),
                        "rank": rk,
                        "symbol": sym,
                        "score": float(10 - rk - mi),
                        "raw": float(rk),
                        "fwd_ret": 0.01 * rk,
                    }
                )
    return scorecard.Panel(pd.DataFrame(rows))


def _tiny_ic() -> pd.DataFrame:
    """迷你 ic.parquet 长表（factor_code/trade_date/ic）——leaderboard 的 ic_mean 列来源。

    ic 逐月取不同值：若全期恒定，``ic_std = 0 → ic_ir = None``，整列 object dtype
    会在 ``df["ic_ir"].abs()`` 处炸（真实数据极少触发，但合成数据一碰就中）。
    """
    return pd.DataFrame(
        [
            {
                "factor_code": code,
                "trade_date": pd.Timestamp(mon),
                "ic": 0.01 * (mi + 1),
            }
            for code in ("A", "B", "C")
            for mi, mon in enumerate(_MONTHS)
        ]
    )


@pytest.fixture()
def env(monkeypatch):
    service._ctx_cache.clear()
    service._derived_cache.clear()
    monkeypatch.setattr(service.store, "panel", lambda dataset="classic": _tiny_panel())
    monkeypatch.setattr(
        service.store, "panel_stamp", lambda dataset="classic": "snap-dedup-test"
    )
    monkeypatch.setattr(service.store, "benchmark_table", lambda *a, **k: None)
    monkeypatch.setattr(service.store, "ic_table", lambda *a, **k: _tiny_ic())
    monkeypatch.setattr(service.store, "stock_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        service.store,
        "metrics",
        lambda dataset="classic": {"leaderboard": [], "metrics": {}, "meta": {}},
    )
    yield
    service._ctx_cache.clear()
    service._derived_cache.clear()


def test_leaderboard_dedup_keeps_earlier_and_renumbers(env, monkeypatch):
    base = service.leaderboard(None, None, 30, "classic")
    base_codes = [r["code"] for r in base["leaderboard"]]
    base_composite = {r["code"]: r["composite"] for r in base["leaderboard"]}
    assert len(base_codes) == 3 and base["meta"]["dedup"] == {"applied": False}

    # A 与 B 强相关（|ρ|=0.95 ≥ 0.9）→ 榜单更靠前者保留、另一个被剔
    monkeypatch.setattr(
        store,
        "dedup_corr",
        lambda dataset="classic": (
            ["A", "B", "C"],
            _pair_matrix(["A", "B", "C"], {("A", "B"): 0.95}),
        ),
    )

    out = service.leaderboard(None, None, 30, "classic", 0.9)

    survivor, victim = (
        ("A", "B") if base_codes.index("A") < base_codes.index("B") else ("B", "A")
    )
    kept = [r["code"] for r in out["leaderboard"]]
    assert kept == [c for c in base_codes if c != victim]
    assert survivor in kept
    assert [r["rank"] for r in out["leaderboard"]] == [1, 2]  # 重新排名 1..N
    assert out["meta"]["dedup"] == {
        "applied": True,
        "threshold": 0.9,
        "n_before": 3,
        "dropped": 1,
        "covered": 3,
    }
    # composite 不重算（只重排名）
    for r in out["leaderboard"]:
        assert r["composite"] == base_composite[r["code"]]


def test_leaderboard_without_dedup_is_unchanged(env, monkeypatch):
    def _boom(dataset="classic"):
        raise AssertionError("dedup=0 不得触碰相关矩阵")

    monkeypatch.setattr(store, "dedup_corr", _boom)

    out = service.leaderboard(None, None, 30, "classic")

    assert [r["rank"] for r in out["leaderboard"]] == [1, 2, 3]
    assert out["meta"]["dedup"] == {"applied": False}


def test_leaderboard_dedup_coverage_fallback(env, monkeypatch):
    """矩阵只覆盖 A/B：C 未覆盖必保留，covered 计数如实。"""
    monkeypatch.setattr(
        store,
        "dedup_corr",
        lambda dataset="classic": (
            ["A", "B"],
            _pair_matrix(["A", "B"], {("A", "B"): 0.99}),
        ),
    )

    out = service.leaderboard(None, None, 30, "classic", 0.9)

    kept = [r["code"] for r in out["leaderboard"]]
    assert "C" in kept and len(kept) == 2
    assert out["meta"]["dedup"]["covered"] == 2
    assert out["meta"]["dedup"]["dropped"] == 1


def test_leaderboard_dedup_matrix_missing_raises(env, monkeypatch):
    monkeypatch.setattr(store, "dedup_corr", lambda dataset="classic": None)

    with pytest.raises(DedupMatrixMissing):
        service.leaderboard(None, None, 30, "classic", 0.9)


def test_router_translates_dedup_matrix_missing_to_409(monkeypatch):
    def _raise(*a, **k):
        raise DedupMatrixMissing(
            "去重相关矩阵缺失（dataset=private）：请先运行 build_factor_panel_corr.py"
        )

    monkeypatch.setattr(service, "leaderboard", _raise)
    app = FastAPI()
    app.include_router(fr_router.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/v1/factor-research/leaderboard?dataset=private&dedup=0.9")

    assert resp.status_code == 409
    assert "build_factor_panel_corr" in resp.json()["detail"]
