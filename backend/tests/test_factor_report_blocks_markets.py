"""读时派生层（blocks）**市场分层**契约（2026-10-16）。

锁死四件事：
1. 基准表按市场分派（CN 别名不变；HK/US 各三条；FUTURES 诚实空表）；
2. ``excess_block`` 的取数市场透传 + FUTURES 整块降级且 reason 说「该市场暂无
   指数基准数据」（不是「同步缺失」——对运维的含义不同）；
3. ``bench_daily_returns`` 的进程内缓存**按市场分键**（符号做键会跨市场串数据）；
4. ``_regime_block`` 的 regime 指数走 ``market_regime.REGIME_INDEX_BY_MARKET``
   唯一口径，表外市场（FUTURES）诚实缺席。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.engine.factor_report import blocks as B
from backend.services.engine.factor_report import blocks_common as BC


# ─────────────────────────── 基准注册表 ───────────────────────────

def test_基准表按市场分派_cn别名逐字不变():
    assert BC.BENCHMARKS == BC.BENCHMARKS_BY_MARKET["CN"]
    assert BC.BENCHMARKS[0] == ("000300.SH", "沪深300")
    assert [s for s, _ in BC.benchmarks_for_market("HK")] == ["HSI.HK", "HSCEI.HK", "HSTECH.HK"]
    assert [s for s, _ in BC.benchmarks_for_market("us")] == ["SPX.US", "NDX.US", "DJI.US"]
    assert BC.benchmarks_for_market("FUTURES") == ()
    assert BC.benchmarks_for_market(None) == BC.BENCHMARKS  # 空值按 CN 缺省


def test_未接入市场抛错而不是落_cn():
    with pytest.raises(ValueError, match="未接入市场"):
        BC.benchmarks_for_market("XX")


# ─────────────────────────── excess_block 降级与透传 ───────────────────────────

def test_futures无基准整块降级_理由说该市场暂无():
    dates = ["2026-01-05", "2026-01-06"]
    out = B.excess_block(dates, np.array([0.01, 0.02]), np.array([0.005, 0.01]),
                         None, market="FUTURES")
    assert out["available"] is False
    assert "该市场暂无指数基准数据" in out["reason"]
    assert "同步" not in out["reason"]  # 不是「同步缺失」—— FUTURES 本来就没有


def test_hk超额按本市场基准表_且取数市场透传(monkeypatch):
    seen: list[tuple[str, str]] = []

    def fake_bench(sym, dates, market="CN"):
        seen.append((sym, market))
        return np.full(len(dates), 0.001)

    monkeypatch.setattr(B, "bench_daily_returns", fake_bench)
    dates = ["2026-01-05", "2026-01-06"]
    out = B.excess_block(dates, np.array([0.01, 0.02]), np.array([0.005, 0.01]),
                         None, market="HK")

    assert out["available"] is True
    assert out["bench_symbol"] == "HSI.HK", "HK 主基准必须是恒生，不是沪深300"
    assert out["bench_name"] == "恒生指数"
    assert [b["symbol"] for b in out["benchmarks"]] == ["HSI.HK", "HSCEI.HK", "HSTECH.HK"]
    assert all(mk == "HK" for _, mk in seen), f"取数市场未透传：{seen}"


def test_点名基准仍排最前且未在市场中沿用代码做名(monkeypatch):
    monkeypatch.setattr(B, "bench_daily_returns",
                        lambda sym, dates, market="CN": np.full(len(dates), 0.001))
    dates = ["2026-01-05", "2026-01-06"]
    out = B.excess_block(dates, np.array([0.01, 0.02]), np.array([0.005, 0.01]),
                         "HSTECH.HK", market="HK")
    assert out["bench_symbol"] == "HSTECH.HK"
    assert out["bench_name"] == "恒生科技指数"
    # 点名项不重复出现，其余 HK 基准仍列在后面
    syms = [b["symbol"] for b in out["benchmarks"]]
    assert syms == ["HSTECH.HK", "HSI.HK", "HSCEI.HK"]


# ─────────────────────────── 缓存按市场分键 ───────────────────────────

def test_基准缓存按市场分键(monkeypatch):
    """同一符号在两个市场取到不同序列时，第二次调用不得命中第一个市场的缓存。"""
    from backend.services.engine.factor_report import blocks_bench as BB

    ratio = {"CN": 1.10, "HK": 1.20}

    def fake_load(columns=(), symbols=None, start=None, end=None, market="CN"):
        sym = list(symbols)[0]
        return pd.DataFrame({
            "time": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "symbol": [sym, sym],
            "close": [100.0, 100.0 * ratio[market]],
        })

    monkeypatch.setattr(BB, "_BENCH_CACHE", {})  # 用例内隔离，退出自动还原
    monkeypatch.setattr("backend.shared.benchmark.load_index_frame", fake_load)
    dates = ["2026-01-05", "2026-01-06"]

    cn = BB.bench_daily_returns("SAME.HK", dates, "CN")
    hk = BB.bench_daily_returns("SAME.HK", dates, "HK")

    assert cn is not None and hk is not None
    assert hk[1] == pytest.approx(0.20) and cn[1] == pytest.approx(0.10), \
        "缓存串市场：第二个市场读到了第一个市场的序列"


# ─────────────────────────── regime 市场口径 ───────────────────────────

def test_regime块走市场唯一口径_表外市场缺席(monkeypatch):
    from backend.shared import market_regime as MR

    calls: list[str] = []

    def fake_load(columns=(), symbols=None, start=None, end=None, market="CN"):
        calls.append(list(symbols)[0])
        n = 300
        days = pd.bdate_range("2024-01-01", periods=n)
        closes = 100.0 * np.cumprod(1 + np.full(n, 0.001))
        return pd.DataFrame({"time": days, "symbol": [list(symbols)[0]] * n, "close": closes})

    monkeypatch.setattr("backend.shared.benchmark.load_index_frame", fake_load)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=300)]
    ls = np.full(300, 0.001)

    hk = B._regime_block(dates, ls, "HK")
    assert hk is not None and calls == ["HSI.HK"], "HK regime 指数应为恒生（唯一口径）"

    calls.clear()
    assert B._regime_block(dates, ls, "FUTURES") is None
    assert calls == [], "FUTURES 没有 regime 指数口径，不得发起取数"
    assert MR.REGIME_INDEX_BY_MARKET["CN"] == "000300.SH"
