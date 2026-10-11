"""因子概率板 · regime 分桶权重单测（P2-c）：只降不翻 + 全降级路径。

纪律（regime.py docstring）：
- 弱桶谓词唯一实现 = regime_buckets.is_weak_bucket（mean_ic≤0 ∧ n≥15）；
- 乘子保守双档：弱桶 ×0.5，其余（ok/空桶/样本不足）恒 1.0，**绝不翻方向**；
- DB 不可达/表空/长窗不足/日配对破坏 → 整块 None + warning，权重全不动
  （产板永不因 regime 失败）。

DB 侧（load_states_sync）与 IC 表（compute_ic_table）全部打桩，只测本模块逻辑。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.shared import regime_daily_store as RDS
from backend.shared.factor_board import regime as REG

D0 = date(2026, 1, 1)


def _dates(n: int) -> list[str]:
    return [(D0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _sel(rows: list[tuple[str, str, str, int, float]]) -> pd.DataFrame:
    """(lib, name, group, sgn, w) → 入选表（select_factors 出口形态）。"""
    return pd.DataFrame(
        [
            {"lib": lib, "name": n, "group": g, "sgn": s, "w": w}
            for lib, n, g, s, w in rows
        ]
    )


def _stub_env(
    monkeypatch,
    *,
    n_dates: int,
    ics: dict[tuple[str, str], list[float | None]],
    states: dict[str, str] | None,
    states_exc: Exception | None = None,
):
    """打桩：因子分区 / 收盘矩阵 / 前向 rank / compute_ic_table / 状态表。

    返回 (calls, expected)，calls 记录 load_states_sync 实参供断言。
    """
    dates = _dates(n_dates)
    monkeypatch.setattr(REG.CP, "list_factor_dates", lambda root, **kw: list(dates))
    monkeypatch.setattr(
        REG.CP, "load_close_matrix", lambda root, ds: pd.DataFrame({"x": [1.0]})
    )
    monkeypatch.setattr(
        REG.CP,
        "fwd_ranks",
        lambda close, ds, idx, h: {ds[i]: pd.Series([1.0]) for i in list(idx)},
    )

    def fake_ic_table(root, cand, eval_dates, fwd5, **kw):
        acc = {k: list(v) for k, v in ics.items()}
        days = {lib: list(eval_dates) for lib in cand["lib"]}
        return acc, None, days

    monkeypatch.setattr(REG.SEL, "compute_ic_table", fake_ic_table)
    calls: list[tuple] = []

    def fake_states(market, *, since=None, until=None):
        calls.append((market, since, until))
        if states_exc is not None:
            raise states_exc
        return dict(states or {})

    monkeypatch.setattr(RDS, "load_states_sync", fake_states)
    return calls, dates


@pytest.mark.unit
def test_弱桶降权_乘子0_5_权重只降不翻(monkeypatch):
    # Arrange：两因子同族，当前状态 bear 桶内对齐 IC 为负（弱桶）
    n = 40
    ics = {
        ("l1_factors", "f1"): [-0.05] * 35,
        ("l1_factors", "f2"): [-0.03] * 30 + [None] * 5,  # NaN 日剔除，n=30
    }
    dates = _dates(n)
    states = {f"{d[:4]}-{d[4:6]}-{d[6:]}": "bear" for d in dates}
    sel = _sel(
        [
            ("l1_factors", "f1", "l1_factors/x", 1, 0.1),
            ("l1_factors", "f2", "l1_factors/x", 1, 0.08),
        ]
    )
    calls, _ = _stub_env(monkeypatch, n_dates=n, ics=ics, states=states)
    # fwd_ranks 键 = 35 个评估日（rdate 尾留 5）——桩里 eval_dates 由它驱动

    # Act
    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    # Assert：状态表按长窗 ISO 区间查询
    assert calls == [("CN", "2026-01-01", "2026-02-09")]
    assert block is not None
    assert block["state"] == "bear" and block["state_lag_days"] == 0
    assert block["lookback_days"] == n and block["eval_days"] == 35
    # 两因子都被判弱桶：×0.5，权重只降不翻（恒正且 ≤ 原值）
    assert out["regime_mult"].tolist() == [0.5, 0.5]
    assert out["regime_note"].tolist() == ["weak", "weak"]
    assert out["regime_n"].tolist() == [35, 30]
    assert out["w"].tolist() == pytest.approx([0.05, 0.04])
    assert out["w_raw"].tolist() == pytest.approx([0.1, 0.08])
    assert (out["w"] <= out["w_raw"] + 1e-12).all() and (out["w"] > 0).all()
    assert block["weak_count"] == 2 and block["adjusted_count"] == 2
    assert block["weak_mult"] == 0.5
    assert block["bucket_n"] == n  # 全部生效日都在 bear
    # 分族：族内逐日对齐均值——前 30 日双因子 (−0.05−0.03)/2=−0.04、
    # 尾 5 日仅 f1 (−0.05) → (30×−0.04 + 5×−0.05)/35；为负 → 弱 → mult=0.5
    fam = block["by_family"]["l1_factors/x"]
    assert fam["state_ic"] == pytest.approx(-1.45 / 35, abs=1e-6)
    assert fam["mult"] == 0.5
    assert any("弱桶" in w for w in warnings)


@pytest.mark.unit
def test_反向因子对齐后为正_ok_不降权(monkeypatch):
    # sgn=-1 因子 IC 为负 = 方向一致；桶内对齐均值 +0.05 → ok
    ics = {("a", "rev"): [-0.05] * 35}
    dates = _dates(40)
    states = {f"{d[:4]}-{d[4:6]}-{d[6:]}": "bull" for d in dates}
    sel = _sel([("a", "rev", "a/s", -1, 0.12)])
    _stub_env(monkeypatch, n_dates=40, ics=ics, states=states)

    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    assert block is not None
    assert out["regime_mult"].tolist() == [1.0]
    assert out["regime_note"].tolist() == ["ok"]
    assert out["regime_ic"].tolist() == pytest.approx([0.05])  # 对齐后为正
    assert out["w"].tolist() == pytest.approx([0.12])  # 原样
    assert warnings == []


@pytest.mark.unit
def test_当前状态空桶_样本不足档_恒1_0(monkeypatch):
    # 当前状态 bull，但评估日全落在 bear → bull 桶空 → insufficient，不降权
    ics = {("a", "f"): [-0.05] * 35}
    dates = _dates(40)
    states = {f"{d[:4]}-{d[4:6]}-{d[6:]}": "bear" for d in dates}
    states[f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]}"] = "bull"
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(monkeypatch, n_dates=40, ics=ics, states=states)

    out, block, _ = REG.build_regime_weights("/qdb", sel)

    assert block is not None and block["state"] == "bull"
    assert out["regime_mult"].tolist() == [1.0]
    assert out["regime_note"].tolist() == ["insufficient"]
    assert out["regime_n"].tolist() == [0]  # 桶空：n=0 如实
    assert out["w"].tolist() == pytest.approx([0.1])
    assert block["weak_count"] == 0 and block["adjusted_count"] == 0


@pytest.mark.unit
def test_当日缺行_取最近行并显形滞后天数(monkeypatch):
    # states 无 until_iso → state = max(states)，state_asof 显形
    ics = {("a", "f"): [0.05] * 35}
    dates = _dates(40)
    states = {f"{d[:4]}-{d[4:6]}-{d[6:]}": "neutral" for d in dates[:-3]}  # 尾 3 日无行
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(monkeypatch, n_dates=40, ics=ics, states=states)

    out, block, _ = REG.build_regime_weights("/qdb", sel)

    assert block is not None
    lag_iso = f"{dates[-4][:4]}-{dates[-4][4:6]}-{dates[-4][6:]}"
    assert block["state_asof"] == lag_iso
    assert block["state_lag_days"] == 3
    assert block["state"] == "neutral"
    assert out["regime_mult"].tolist() == [1.0]


@pytest.mark.unit
def test_DB不可达_整块None_权重全不动(monkeypatch):
    ics = {("a", "f"): [-0.05] * 35}
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(
        monkeypatch,
        n_dates=40,
        ics=ics,
        states=None,
        states_exc=RuntimeError("connection refused"),
    )

    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    assert block is None
    assert out["w"].tolist() == pytest.approx([0.1])  # 权重全不动
    assert out["regime_mult"].tolist() == [1.0]
    assert out["regime_note"].tolist() == ["unavailable"]
    assert out["regime_ic"].tolist() == [None]
    assert out["regime_n"].tolist() == [0]
    assert any("不可达" in w for w in warnings)


@pytest.mark.unit
def test_状态表空_降级不写块(monkeypatch):
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(monkeypatch, n_dates=40, ics={("a", "f"): [0.1] * 35}, states={})

    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    assert block is None and out["w"].tolist() == pytest.approx([0.1])
    assert any("状态表空" in w for w in warnings)


@pytest.mark.unit
def test_长窗不足_不硬算直接降级(monkeypatch):
    # 分区数 < REGIME_MIN_DAYS×2 → 连两个弱桶样本都凑不出
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(monkeypatch, n_dates=20, ics={("a", "f"): [0.1] * 15}, states={})

    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    assert block is None and out["w"].tolist() == pytest.approx([0.1])
    assert any("长窗不足" in w for w in warnings)


@pytest.mark.unit
def test_IC日配对长度不一致_结构不变量破坏即降级(monkeypatch):
    ics = {("a", "f"): [-0.05] * 34}  # 比 days（35）短一个 → 破坏配对
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    dates = _dates(40)
    states = {f"{d[:4]}-{d[4:6]}-{d[6:]}": "bear" for d in dates}
    _stub_env(monkeypatch, n_dates=40, ics=ics, states=states)

    out, block, warnings = REG.build_regime_weights("/qdb", sel)

    assert block is None and out["w"].tolist() == pytest.approx([0.1])
    assert any("配对长度不一致" in w for w in warnings)


@pytest.mark.unit
def test_降级出口schema_恒有呈形列(monkeypatch):
    """降级时 sel' 也必须有 regime 呈形列——前端无需分支。"""
    sel = _sel([("a", "f", "a/s", 1, 0.1)])
    _stub_env(monkeypatch, n_dates=20, ics={}, states={})

    out, _, _ = REG.build_regime_weights("/qdb", sel)

    for col in ("w_raw", "regime_mult", "regime_ic", "regime_n", "regime_note"):
        assert col in out.columns, col


@pytest.mark.unit
def test_弱桶谓词与regime_buckets同源_阈值快照一致():
    from backend.shared import regime_buckets as RB

    assert REG.C.REGIME_MIN_DAYS == RB.MIN_BUCKET_DAYS
    assert REG.C.REGIME_WEAK_MULT == 0.5
