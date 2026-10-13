"""启动资格清单（`shared/start_eligibility.py`）单测。

口径：清单是**唯一展示实现**，判据必须全部复用执行点——
`can_start`（生命周期）与 `promotion_gate`（回测体检晋级），
本模块**不得**新增阈值或另写一套判断，否则展示面与执行面会漂移。
"""

from __future__ import annotations

import pytest

from backend.shared.backtest_health import (
    LIVE_MAX_TRACKING_ERROR,
    LIVE_SIM_MIN_TRADING_DAYS,
)
from backend.shared.start_eligibility import build_start_eligibility
from backend.shared.strategy_lifecycle import (
    STATUS_DRAFT,
    STATUS_LIVE,
    STATUS_SIM,
    STATUS_VERIFIED,
)

_A_HEALTH = {"verdict": "A", "verdict_label": "可信", "confidence": 88}
_L_HEALTH = {"verdict": "L", "verdict_label": "运气嫌疑", "confidence": 12}


def _items(result: dict) -> dict[str, dict]:
    return {item["key"]: item for item in result["items"]}


# ── SIMULATION ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_verified_with_health_a_can_start_sim() -> None:
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=_A_HEALTH, mode="SIMULATION"
    )
    assert result["allowed"] is True
    assert result["mode"] == "SIMULATION"
    keys = [i["key"] for i in result["items"]]
    assert keys == ["lifecycle", "health"]  # REAL 专属项不进模拟清单
    assert all(i["passed"] for i in result["items"])
    assert result["next_step"] == ""


@pytest.mark.unit
def test_draft_blocked_by_lifecycle_not_by_health() -> None:
    """DRAFT 先撞生命周期——体检项如实展示但不喧宾夺主。"""
    result = build_start_eligibility(
        status=STATUS_DRAFT, health=_A_HEALTH, mode="SIMULATION"
    )
    assert result["allowed"] is False
    items = _items(result)
    assert items["lifecycle"]["passed"] is False
    assert "DRAFT" in items["lifecycle"]["reason"]
    assert items["lifecycle"]["next_step"]
    assert result["next_step"] == items["lifecycle"]["next_step"]


@pytest.mark.unit
def test_health_missing_blocks_sim_with_actionable_next_step() -> None:
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=None, mode="SIMULATION"
    )
    assert result["allowed"] is False
    items = _items(result)
    assert items["lifecycle"]["passed"] is True
    assert items["health"]["passed"] is False
    assert "回测" in items["health"]["next_step"]


@pytest.mark.unit
def test_health_verdict_l_blocks_sim() -> None:
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=_L_HEALTH, mode="SIMULATION"
    )
    assert result["allowed"] is False
    assert _items(result)["health"]["passed"] is False


@pytest.mark.unit
def test_health_gate_disabled_skips_health_item() -> None:
    """门禁总开关关掉时，执行点不吃体检——展示面必须同步放行。"""
    result = build_start_eligibility(
        status=STATUS_VERIFIED,
        health=None,
        mode="SIMULATION",
        health_gate_enabled=False,
    )
    assert result["allowed"] is True
    items = _items(result)
    assert items["health"]["passed"] is True
    assert "关" in items["health"]["value"] or "关" in items["health"]["reason"]


# ── REAL ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_verified_blocked_for_real_and_points_at_sim() -> None:
    """本仓最常踩的一条：VERIFIED 不能直接进实盘。"""
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=_A_HEALTH, mode="REAL"
    )
    assert result["allowed"] is False
    items = _items(result)
    assert items["lifecycle"]["passed"] is False
    assert "模拟" in items["lifecycle"]["next_step"]
    assert result["next_step"] == items["lifecycle"]["next_step"]


@pytest.mark.unit
def test_sim_with_insufficient_days_blocked_for_real() -> None:
    result = build_start_eligibility(
        status=STATUS_SIM,
        health=_A_HEALTH,
        mode="REAL",
        sim_trading_days=LIVE_SIM_MIN_TRADING_DAYS - 1,
        tracking_error=0.02,
    )
    assert result["allowed"] is False
    items = _items(result)
    assert items["sim_days"]["passed"] is False
    assert str(LIVE_SIM_MIN_TRADING_DAYS) in items["sim_days"]["reason"]


@pytest.mark.unit
def test_sim_with_excessive_tracking_error_blocked_for_real() -> None:
    result = build_start_eligibility(
        status=STATUS_SIM,
        health=_A_HEALTH,
        mode="REAL",
        sim_trading_days=LIVE_SIM_MIN_TRADING_DAYS,
        tracking_error=LIVE_MAX_TRACKING_ERROR + 0.01,
    )
    assert result["allowed"] is False
    items = _items(result)
    assert items["sim_days"]["passed"] is True
    assert items["tracking_error"]["passed"] is False


@pytest.mark.unit
def test_sim_ready_for_real_all_pass() -> None:
    result = build_start_eligibility(
        status=STATUS_LIVE,
        health=_A_HEALTH,
        mode="REAL",
        sim_trading_days=LIVE_SIM_MIN_TRADING_DAYS + 3,
        tracking_error=0.03,
    )
    assert result["allowed"] is True
    assert [i["key"] for i in result["items"]] == [
        "lifecycle",
        "health",
        "sim_days",
        "tracking_error",
    ]
    assert result["next_step"] == ""


@pytest.mark.unit
def test_real_without_sim_state_history_only_hints() -> None:
    """无状态史（None）= 只提示不拦，与 `promotion_gate` 同口径。"""
    result = build_start_eligibility(
        status=STATUS_SIM, health=_A_HEALTH, mode="REAL"
    )
    assert result["allowed"] is True
    items = _items(result)
    assert items["sim_days"]["passed"] is True
    assert items["sim_days"]["value"]  # 有可读的「无记录」占位
    assert items["tracking_error"]["passed"] is True


# ── 模板 / 非用户策略 ────────────────────────────────────────────────


@pytest.mark.unit
def test_template_source_bypasses_all_gates() -> None:
    result = build_start_eligibility(
        status=STATUS_DRAFT, health=None, mode="REAL", source="template"
    )
    assert result["allowed"] is True
    assert result["items"] == []


@pytest.mark.unit
def test_unknown_mode_rejected() -> None:
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=_A_HEALTH, mode="WHATEVER"
    )
    assert result["allowed"] is False
    assert result["next_step"]


@pytest.mark.unit
def test_lifecycle_reason_matches_execution_point_verbatim() -> None:
    """生命周期项的理由必须与 `/start` 的 400 文案逐字一致（同一句人话）。"""
    result = build_start_eligibility(
        status=STATUS_VERIFIED, health=_A_HEALTH, mode="REAL"
    )
    assert _items(result)["lifecycle"]["reason"] == (
        "策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证"
    )
