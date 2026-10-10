"""分钟挖掘的请求归一契约（移植 P1.5）：MinuteScope + 分钟分支。

钉死四件事：

1. **可用范围**（``minute_scope``）：来自 provider 的**末端连续块**（跨过数据
   空洞），不是 ``meta.first``；断层表如实带出。
2. **默认窗**：分钟任务不指定窗口时物化成可用连续块（子进程要求显式窗口——
   日线尺度的 4+1 年默认窗在 ~4 个月分钟数据上毫无意义）。
3. **拒绝面**：跨断层 / 落在断层前 / 超覆盖 / 非 A 股 / provider 未构建 /
   带数据集 —— 一律 ValueError（→ 路由 400），绝不静默按日线跑。
4. **日线路径零变化**：未指定频率时逐位同迁移前（空频率、全选数据集）。

合成 provider 复用 ``test_minute_qlib_provider`` 的构造器（真实
``build_minute_provider`` 产物，不手搭目录冒充）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from backend.services.engine import minute_qlib_provider as MP
from backend.services.engine.alpha_agent.launcher import (
    MiningConfig,
    minute_scope,
    normalize_mining_config,
)
from backend.services.engine.rd_agent.mining_datasets import SUPPORTED_DATASETS
from backend.tests.test_minute_qlib_provider import (
    DAY1,
    DAY2,
    HOLE_AFTER,
    _write_day_provider,
    _write_source,
)

#: 断层之后的连续块（3 个交易日）：2026-03-04 ~ 2026-03-06
BLOCK = (HOLE_AFTER, HOLE_AFTER + timedelta(days=1), HOLE_AFTER + timedelta(days=2))
BLOCK_END = BLOCK[-1].isoformat()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QM_QLIB_MINUTE_DIR", raising=False)


@pytest.fixture()
def minute_provider(tmp_path, monkeypatch):
    """带 58 天空洞的合成 provider（源里 DAY2 之后直跳 HOLE_AFTER）。"""
    days = (DAY1, DAY2, *BLOCK)
    source = _write_source(tmp_path, days=days)
    day = _write_day_provider(tmp_path, days=(*days, BLOCK[-1] + timedelta(days=1)))
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(provider))
    return provider


def _norm(
    mining_start=None,
    mining_end=None,
    valid_start=None,
    valid_end=None,
    datasets=None,
    *,
    freq="1min",
    market="a_share",
    **kwargs,
) -> MiningConfig:
    return normalize_mining_config(
        mining_start,
        mining_end,
        valid_start,
        valid_end,
        datasets,
        freq=freq,
        market=market,
        **kwargs,
    )


# ── 1. 可用范围 ──────────────────────────────────────────────────────


def test_minute_scope_uses_terminal_contiguous_block(minute_provider):
    scope = minute_scope()
    assert scope is not None
    assert (scope.usable.start.isoformat(), scope.usable.end.isoformat()) == (
        BLOCK[0].isoformat(),
        BLOCK_END,
    )
    assert scope.days == len(BLOCK)
    assert scope.data_end == BLOCK[-1]  # day 层留了 t+1，无需再退让
    # provider 覆盖仍含断层之前的碎片（如实上屏，不冒充可用区间）
    assert scope.coverage.start == DAY1 and scope.coverage.end == BLOCK[-1]
    assert scope.gaps == ((DAY2, HOLE_AFTER),)
    assert scope.gap_text() == f"{DAY2} ~ {HOLE_AFTER}"


def test_minute_scope_none_when_provider_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(tmp_path / "nope"))
    assert minute_scope() is None


# ── 2. 默认窗与合法窗 ────────────────────────────────────────────────


def test_minute_default_window_is_usable_block(minute_provider):
    cfg = _norm()
    assert (cfg.mining_start, cfg.mining_end) == (BLOCK[0].isoformat(), BLOCK_END)
    assert cfg.valid_start == "" and cfg.valid_end == ""
    assert cfg.freq == "1min"
    assert cfg.datasets == ()  # 分钟任务不落数据集（也不伪装成"全选"）


def test_minute_explicit_window_inside_block_kept(minute_provider):
    cfg = _norm(BLOCK[1].isoformat(), BLOCK_END)
    assert (cfg.mining_start, cfg.mining_end) == (BLOCK[1].isoformat(), BLOCK_END)


def test_minute_validation_window_after_mining_kept(minute_provider):
    """样本外检验在分钟模式同样可用——只是窗口小到要用户自己切。"""
    cfg = _norm(
        BLOCK[0].isoformat(),
        BLOCK[0].isoformat(),
        BLOCK[1].isoformat(),
        BLOCK_END,
    )
    assert cfg.mining_start == BLOCK[0].isoformat()
    assert (cfg.valid_start, cfg.valid_end) == (BLOCK[1].isoformat(), BLOCK_END)


# ── 3. 拒绝面 ────────────────────────────────────────────────────────


def test_minute_window_spanning_hole_rejected(minute_provider):
    with pytest.raises(ValueError) as exc:
        _norm(DAY1.isoformat(), BLOCK_END)
    assert "断层" in str(exc.value)
    assert f"{DAY2} ~ {HOLE_AFTER}" in str(exc.value)
    assert str(len(BLOCK)) in str(exc.value)  # 可用交易日数一并给出


def test_minute_window_before_hole_rejected(minute_provider):
    """断层之前的碎片在 provider 覆盖内，但不是可用块——照样拒。"""
    with pytest.raises(ValueError) as exc:
        _norm(DAY1.isoformat(), DAY2.isoformat())
    assert "断层" in str(exc.value)


def test_minute_window_beyond_provider_rejected(minute_provider):
    with pytest.raises(ValueError) as exc:
        _norm(BLOCK[0].isoformat(), "2026-04-01")
    assert "覆盖" in str(exc.value)


def test_minute_requires_built_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(tmp_path / "nope"))
    with pytest.raises(ValueError) as exc:
        _norm()
    assert "分钟数据面未构建" in str(exc.value)


def test_minute_rejected_for_non_a_share(minute_provider):
    with pytest.raises(ValueError) as exc:
        _norm(market="hong_kong")
    assert "A 股" in str(exc.value)


def test_minute_rejects_dataset_selection(minute_provider):
    with pytest.raises(ValueError) as exc:
        _norm(freq="1min", datasets=["features_daily"])
    assert "数据集" in str(exc.value)


def test_unknown_freq_rejected(minute_provider):
    with pytest.raises(ValueError) as exc:
        _norm(freq="5min")
    assert "不支持的挖掘频率" in str(exc.value)


# ── 4. 日线路径零变化 ────────────────────────────────────────────────


def test_day_defaults_unchanged(minute_provider):
    """provider 在场也不影响日线：没有频率就是没有频率，窗口不留痕。"""
    cfg = _norm(freq=None)
    assert cfg == MiningConfig(datasets=SUPPORTED_DATASETS, freq="")
    assert _norm(freq="day") == cfg
    assert _norm(freq="") == cfg


def test_day_explicit_window_untouched_by_minute_logic(minute_provider):
    cfg = _norm("2021-01-04", "2024-12-31", freq="day")
    assert (cfg.mining_start, cfg.mining_end) == ("2021-01-04", "2024-12-31")
    assert cfg.freq == ""
