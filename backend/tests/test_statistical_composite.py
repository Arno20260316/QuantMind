"""统计派 · IC 加权合成因子（Tier 3 / T-S3-04）——权重定点 + 表达式/pandas 同式。

合成因子的落库形态是**单行 qlib 表达式**（回测链路消费），IC 在 pandas 里算
（引擎消费）——两者必须同式，否则报告的 IC 与回测的因子不是一回事。这里钉：

1. **权重定点**：归一后 6 位小数，表达式文本与 ``plan.weights`` 是同一份数值
   （一侧全精度一侧截断 = 等价性对拍永远修不好的假绿）；舍入到 0 的分量整条
   剔除，剔不干净（< 2 个分量）→ None，宁可不合成；
2. **表达式白名单安全**：单行、无空格、无方括号/引号、含 ``$``、首项符号
   显式（不出现裸 ``+`` 开头）；
3. **min_history** = 分量最大回看 + (z 窗 − 1)——z 窗满员的第一个下标；
4. **pandas 实现**：``plan.candidate().compute`` 与手算 ``Σ w·(v−mean)/std``
   （逐 symbol 滚动，min_periods=1）逐点一致；
5. qlib 目录可用时，对真实候选合成式做**逐点等价对拍**（rtol=1e-4）——目录
   缺失或 qlib 未装则整组 skip。
"""

from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import candidates as cand  # noqa: E402
from backend.services.engine.alpha_agent.statistical import composite as comp  # noqa: E402

QLIB_DIR = os.environ.get("QM_TEST_QLIB_DIR", "/data/qlib/cn_data")
INSTRUMENTS = ("sh600000", "sz000001")
FETCH_FIELDS = ("$open", "$high", "$low", "$close", "$volume", "$amount")
FETCH_START = "2021-01-01"
FETCH_END = "2026-06-30"

#: 表达式里权重项的抓取（形如 ``0.666667*(`` / ``-0.333333*(``）
_WEIGHT_RE = re.compile(r"([+-]?\d+\.\d{6})\*\(")


def _fake(key: str, compute, expression: str, *, min_history: int = 3) -> cand.Candidate:
    return cand.Candidate(
        key=key, name=key, family="test", formulation=f"{key} (测试用)",
        source="test", expression=expression, min_history=min_history, compute=compute,
    )


PRICE = _fake("px", lambda frame: frame["close"], "$close", min_history=3)
VOLUME = _fake("vl", lambda frame: frame["volume"], "$volume", min_history=5)


def _frame(n: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 10 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame(
        {
            "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
            "close": close, "volume": rng.uniform(1e5, 3e5, n), "amount": 1e7,
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


class TestNormalizedWeights:
    def test_sign_preserved_and_abs_sum_one(self):
        weights = comp._normalized_weights([0.06, -0.02, 0.02])

        assert weights is not None
        assert sum(abs(w) for w in weights) == pytest.approx(1.0, abs=1e-9)
        assert weights[0] > 0 and weights[1] < 0 and weights[2] > 0

    def test_rounded_to_fixed_decimals(self):
        weights = comp._normalized_weights([0.1, 0.2])

        assert weights == (pytest.approx(1 / 3, abs=1e-6), pytest.approx(2 / 3, abs=1e-6))
        assert all(abs(w * 10**6 - round(w * 10**6)) < 1e-3 for w in weights)

    def test_tiny_component_rounded_to_zero(self):
        """量级差 6 个数量级的成分被舍到 0（整条剔除，不留在表达式里）。"""
        weights = comp._normalized_weights([1.0, 1e-8])

        assert weights is not None
        assert weights[1] == 0.0

    def test_degenerate_inputs_return_none(self):
        """NaN / 空 / 全零：没有可归一的东西 → None（不是发一份 0 权重）。"""
        assert comp._normalized_weights([np.nan, 0.1]) is None
        assert comp._normalized_weights([]) is None
        assert comp._normalized_weights([0.0, 0.0]) is None


class TestBuildCompositePlan:
    def test_plan_shape_and_expression(self):
        plan = comp.build_composite_plan(
            [(PRICE, 0.06), (VOLUME, 0.03)], horizon=5, z_window=4
        )

        assert plan is not None
        assert plan.keys == ("px", "vl")
        assert plan.horizon == 5 and plan.z_window == 4
        assert plan.min_history == max(PRICE.min_history, VOLUME.min_history) + 3
        expression = plan.expression
        assert "\n" not in expression and " " not in expression
        assert "$close" in expression and "$volume" in expression
        assert "Mean($close,4)" in expression and "Std($close,4)" in expression
        assert not expression.startswith("+"), "首项符号显式：正号不带 +，负号带 -"

    def test_expression_weights_equal_plan_weights(self):
        """表达式文本里的权重 == plan.weights（同一份定点值）。

        这是「表达式与 pandas 同式」的地基：一侧全精度、一侧 6 位截断，
        两侧算出的 IC 会永远差一点，对拍修不完。
        """
        plan = comp.build_composite_plan(
            [(PRICE, 0.06), (VOLUME, -0.02)], horizon=1, z_window=6
        )

        assert plan is not None
        parsed = [float(text) for text in _WEIGHT_RE.findall(plan.expression)]
        assert parsed == list(plan.weights)
        assert len(parsed) == len(plan.components)
        assert parsed[1] < 0, "负 IC 分量保留负号（方向是信息）"

    def test_fewer_than_two_usable_components_returns_none(self):
        assert comp.build_composite_plan([(PRICE, 0.05)], horizon=1) is None
        assert comp.build_composite_plan([(PRICE, 0.0), (VOLUME, 0.03)], horizon=1) is None
        assert (
            comp.build_composite_plan([(PRICE, np.nan), (VOLUME, 0.03)], horizon=1)
            is None
        )

    def test_rounding_away_a_component_can_leave_too_few(self):
        plan = comp.build_composite_plan([(PRICE, 1.0), (VOLUME, 1e-8)], horizon=1)
        assert plan is None, "舍到 0 后剩 1 个分量：宁可不合成，不发假货"

    def test_z_window_must_be_at_least_two(self):
        with pytest.raises(ValueError, match="z_window"):
            comp.build_composite_plan([(PRICE, 0.06), (VOLUME, 0.03)], horizon=1, z_window=1)

    def test_pandas_compute_matches_manual_formula(self):
        plan = comp.build_composite_plan(
            [(PRICE, 0.06), (VOLUME, 0.03)], horizon=5, z_window=4
        )
        assert plan is not None
        frame = _frame()

        actual = plan.candidate().compute(frame)

        w_px, w_vl = plan.weights
        manual = None
        for candidate, weight in zip(plan.components, (w_px, w_vl), strict=True):
            values = candidate.compute(frame).astype(float)
            z = (values - values.rolling(4, min_periods=1).mean()) / values.rolling(
                4, min_periods=1
            ).std()
            manual = weight * z if manual is None else manual + weight * z
        np.testing.assert_allclose(
            actual.to_numpy(), manual.to_numpy(), rtol=1e-12, atol=1e-12, equal_nan=True
        )
        assert actual.name == comp.COMPOSITE_KEY


# ── qlib 表达式等价（集成；目录缺失即 skip） ─────────────────────────


@lru_cache(maxsize=1)
def _qlib_panel() -> pd.DataFrame:
    import qlib  # noqa: PLC0415
    import qlib.data as qd  # noqa: PLC0415
    from qlib.constant import REG_CN  # noqa: PLC0415

    plan = comp.build_composite_plan(
        [(cand.CANDIDATE_BY_KEY["reversal_5d"], 0.06),
         (cand.CANDIDATE_BY_KEY["momentum_20d"], 0.03)],
        horizon=5,
        z_window=60,
    )
    assert plan is not None
    qlib.init(provider_uri=QLIB_DIR, region=REG_CN)
    fields = [*FETCH_FIELDS, plan.expression]
    raw = qd.D.features(list(INSTRUMENTS), fields, start_time=FETCH_START, end_time=FETCH_END)
    parts = []
    for inst in INSTRUMENTS:
        part = raw.xs(inst, level=0).copy()
        part.index.name = "trade_date"
        part["symbol"] = inst
        parts.append(part)
    panel = pd.concat(parts).set_index("symbol", append=True).sort_index()
    panel.index.names = ["trade_date", "symbol"]
    panel.attrs["expression"] = plan.expression
    return panel


@pytest.fixture(scope="module")
def qlib_panel() -> pd.DataFrame:
    if not Path(QLIB_DIR).is_dir():
        pytest.skip(f"qlib 数据目录不存在: {QLIB_DIR}（QM_TEST_QLIB_DIR 可覆盖）")
    try:
        panel = _qlib_panel()
    except ImportError as exc:
        pytest.skip(f"qlib 不可用: {exc}")
    assert not panel.empty, "qlib 取数返回空面板"
    return panel


class TestQlibCompositeEquivalence:
    def test_composite_expression_matches_pandas(self, qlib_panel):
        """真实候选合成式：qlib 求值 == pandas 实现（暖机满员区逐点）。"""
        plan = comp.build_composite_plan(
            [(cand.CANDIDATE_BY_KEY["reversal_5d"], 0.06),
             (cand.CANDIDATE_BY_KEY["momentum_20d"], 0.03)],
            horizon=5,
            z_window=60,
        )
        assert plan is not None, "金样合成方案必须可构造"
        assert qlib_panel.attrs["expression"] == plan.expression

        renamed = qlib_panel[list(FETCH_FIELDS)].rename(
            columns={f"${name}": name for name in cand.REQUIRED_COLUMNS}
        )
        expected = qlib_panel[plan.expression].astype(float)
        actual = cand.compute_panel(renamed, plan.candidate()).astype(float)

        assert actual.index.equals(expected.index)
        position = qlib_panel.groupby(level=1).cumcount().to_numpy()
        region = position >= plan.min_history
        assert int(region.sum()) > 500, f"有效对比点太少（{int(region.sum())}）"
        assert (
            actual.isna().to_numpy()[region].tolist()
            == expected.isna().to_numpy()[region].tolist()
        ), "暖机后 NaN 位置仍分叉——窗口/算子语义不一致"
        np.testing.assert_allclose(
            actual.to_numpy()[region],
            expected.to_numpy()[region],
            rtol=1e-4,
            atol=1e-7,
            err_msg="合成因子 pandas 实现与 qlib 表达式不等价",
        )


class TestSerialization:
    def test_plan_expression_is_strict_json_safe(self):
        """表达式文本进 metadata 前必须过严格 JSON（无裸 NaN 等方言产物）。"""
        plan = comp.build_composite_plan(
            [(PRICE, 0.06), (VOLUME, 0.03)], horizon=5, z_window=4
        )
        assert plan is not None
        text = json.dumps({"factor_code": plan.expression}, allow_nan=False)
        assert json.loads(text)["factor_code"] == plan.expression

    def test_expression_compatible_with_qlib_route_whitelist(self):
        """表达式必须过路由的 ``_looks_like_qlib_expression`` 白名单。

        落库的 factor_code 会被 alpha_agent 路由判定为 qlib 表达式才走回测链路
        ——白名单外的字符（引号/方括号/换行）会让合成因子静默路由去别处。
        """
        from backend.services.engine.routers.alpha_agent import (  # noqa: PLC0415
            _looks_like_qlib_expression,
        )

        plan = comp.build_composite_plan(
            [(PRICE, 0.06), (VOLUME, 0.03)], horizon=5, z_window=4
        )
        assert plan is not None
        assert _looks_like_qlib_expression(plan.expression)
