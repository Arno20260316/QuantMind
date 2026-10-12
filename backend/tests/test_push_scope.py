"""QQ 推送面范围过滤（push_scope）单测。

背景（2026-10-12 用户指令）：QQ 只推「实盘有的股票」，且跟随券商选定
（切 TDX 推 TDX 的仓、切 QMT 推 QMT 的仓）；模拟盘/手工自选/未选定券商
的标的站内照旧、QQ 不推。实况：选定 TDX（4 只）时 QQ 仍在推 QMT（54 只）
与模拟盘的票。实现见 backend/shared/push_scope.py。
"""

from __future__ import annotations

import pytest

import backend.shared.push_scope as push_scope

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_cache():
    push_scope.clear_cache()
    yield
    push_scope.clear_cache()


def _positions(*rows: tuple[str, list[str]]) -> dict[str, dict]:
    """(symbol, sources) → merge 后的 positions 形状（row 带 source/sources）。"""
    return {
        sym: {"symbol": sym, "source": srcs[0], "sources": list(srcs)}
        for sym, srcs in rows
    }


def _meta(active: str | None, *sources: str, stale: tuple[str, ...] = ()) -> dict:
    return {
        "sources": {s: {"stale": s in stale} for s in sources},
        "snapshot_at": "2026-10-12T14:30:00",
        "active_broker": active,
    }


def _install(monkeypatch, positions, meta, calls: list | None = None):
    def _loader(tenant: str, user_id: str):
        if calls is not None:
            calls.append((tenant, user_id))
        return positions, meta

    monkeypatch.setattr(push_scope, "_load_positions", _loader)


# ── 范围集语义 ──────────────────────────────────────────────────────


def test_tdx_selected_scopes_to_tdx_positions(monkeypatch):
    positions = _positions(
        ("SZ002074", ["tdx_bridge"]),
        ("SH601000", ["tdx_bridge"]),
        ("SZ000002", ["qmt_exec"]),
        ("SZ002141", ["qmt_exec"]),
    )
    _install(monkeypatch, positions, _meta("tdx_bridge", "tdx_bridge", "qmt_exec"))

    allowed = push_scope.live_symbols_for_push("default", "10000001")

    assert allowed == {"SZ002074", "SH601000"}


def test_qmt_selected_scopes_to_qmt_positions(monkeypatch):
    positions = _positions(
        ("SZ002074", ["tdx_bridge"]),
        ("SZ000002", ["qmt_exec"]),
    )
    _install(monkeypatch, positions, _meta("qmt_exec", "tdx_bridge", "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SZ000002"}


def test_symbol_held_by_both_brokers_is_in_scope_for_each(monkeypatch):
    positions = _positions(("SH600036", ["qmt_exec", "tdx_bridge"]))
    _install(monkeypatch, positions, _meta("tdx_bridge", "tdx_bridge", "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SH600036"}


def test_qmt_selected_keeps_both_broker_symbol_via_sources(monkeypatch):
    """同票两账户：sources 并集里含选定券商即放行（不按 row['source'] 单值判）。"""
    positions = _positions(("SH600036", ["qmt_exec", "tdx_bridge"]))
    _install(monkeypatch, positions, _meta("qmt_exec", "tdx_bridge", "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SH600036"}


def test_selected_broker_snapshot_missing_falls_back_to_real_union(monkeypatch):
    """选定 TDX 但 TDX 快照缺席 → 回退实盘并集（不静默吞；账户停更另有健康告警）。"""
    positions = _positions(("SZ000002", ["qmt_exec"]))
    _install(monkeypatch, positions, _meta("tdx_bridge", "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SZ000002"}


def test_selected_broker_snapshot_stale_falls_back_to_real_union(monkeypatch):
    positions = _positions(("SZ000002", ["qmt_exec"]))
    _install(
        monkeypatch,
        positions,
        _meta("tdx_bridge", "tdx_bridge", "qmt_exec", stale=("tdx_bridge",)),
    )

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SZ000002"}


def test_no_selection_uses_real_union_not_sim_or_manual(monkeypatch):
    """未选定券商：仍是「实盘有的」——并集，但模拟/自选不在 positions 里本就不参与。"""
    positions = _positions(("SZ000002", ["qmt_exec"]))
    _install(monkeypatch, positions, _meta(None, "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == {"SZ000002"}


def test_selected_broker_fresh_but_empty_is_empty_scope(monkeypatch):
    """选定券商快照在线且确实空仓 → 空集（没有实盘股票，就什么都不推）。"""
    positions = _positions(("SZ000002", ["qmt_exec"]))
    _install(monkeypatch, positions, _meta("tdx_bridge", "tdx_bridge", "qmt_exec"))

    assert push_scope.live_symbols_for_push("default", "10000001") == set()


def test_no_snapshots_at_all_gives_none(monkeypatch):
    _install(
        monkeypatch, {}, {"sources": {}, "snapshot_at": None, "active_broker": None}
    )

    assert push_scope.live_symbols_for_push("default", "10000001") is None


def test_loader_failure_gives_none_never_raises(monkeypatch):
    def _boom(tenant: str, user_id: str):
        raise RuntimeError("db down")

    monkeypatch.setattr(push_scope, "_load_positions", _boom)

    assert push_scope.live_symbols_for_push("default", "10000001") is None


def test_env_flag_off_disables_scope(monkeypatch):
    monkeypatch.setenv(push_scope.ENV_FLAG, "false")
    _install(
        monkeypatch,
        _positions(("SZ002074", ["tdx_bridge"])),
        _meta("tdx_bridge", "tdx_bridge"),
    )

    assert push_scope.live_symbols_for_push("default", "10000001") is None


def test_env_flag_word_list_only_lowercase_true(monkeypatch):
    """全仓 env 词表纪律：唯一真值 token 是小写 true（env_flags 唯一读法），
    其余写法（含 "1"）一律按关处理——此闸默认开、写任意非 true 值即关。"""
    monkeypatch.setenv(push_scope.ENV_FLAG, "1")
    _install(
        monkeypatch,
        _positions(("SZ002074", ["tdx_bridge"])),
        _meta("tdx_bridge", "tdx_bridge"),
    )

    assert push_scope.live_symbols_for_push("default", "10000001") is None


def test_cache_hits_within_ttl_and_clears(monkeypatch):
    calls: list = []
    _install(
        monkeypatch,
        _positions(("SZ002074", ["tdx_bridge"])),
        _meta("tdx_bridge", "tdx_bridge"),
        calls,
    )

    a = push_scope.live_symbols_for_push("default", "10000001")
    b = push_scope.live_symbols_for_push("default", "10000001")
    assert a == b == {"SZ002074"}
    assert len(calls) == 1  # 二次命中缓存，不再读库

    push_scope.clear_cache()
    push_scope.live_symbols_for_push("default", "10000001")
    assert len(calls) == 2


def test_cache_negative_result_also_cached(monkeypatch):
    """失败(None)也进缓存：否则库故障时每行告警都怼一次读库+WARNING 风暴。"""
    calls: list = []

    def _boom(tenant: str, user_id: str):
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(push_scope, "_load_positions", _boom)
    assert push_scope.live_symbols_for_push("default", "10000001") is None
    assert push_scope.live_symbols_for_push("default", "10000001") is None
    assert len(calls) == 1


# ── 标的级判定（纯函数）─────────────────────────────────────────────


def test_symbol_allowed_none_scope_passes_everything():
    assert push_scope.symbol_allowed_for_push("SH600036", None)
    assert push_scope.symbol_allowed_for_push("JPM", None)
    assert push_scope.symbol_allowed_for_push("", None)


def test_symbol_allowed_prefix_and_suffix_forms_match():
    allowed = {"SH600036", "SZ002074"}
    assert push_scope.symbol_allowed_for_push("SH600036", allowed)
    assert push_scope.symbol_allowed_for_push("600036.SH", allowed)  # 后缀形
    assert push_scope.symbol_allowed_for_push("002074", allowed)  # 裸 6 位
    assert push_scope.symbol_allowed_for_push("SZ002141", allowed) is False


def test_symbol_allowed_market_level_rows_pass():
    """无标的（''/'*'）是市场级事件，不是「某只股票的推送」，不设闸。"""
    assert push_scope.symbol_allowed_for_push("*", set())
    assert push_scope.symbol_allowed_for_push("", set())


def test_symbol_allowed_non_a_share_is_rejected():
    """港股/美股：本账户体系只承 A 股实盘持仓，必然不在放行集里。"""
    allowed = {"SH600036"}
    assert push_scope.symbol_allowed_for_push("JPM", allowed) is False
    assert push_scope.symbol_allowed_for_push("3988.HK", allowed) is False
    assert push_scope.symbol_allowed_for_push("AAPL", allowed) is False


# ── 源码守卫：唯一实现纪律 ──────────────────────────────────────────


def test_loader_reuses_real_positions_sql_single_source():
    """不许第二份 SQL：范围集必须走 real_positions 的 SQL + merge 唯一实现。"""
    import inspect

    src = inspect.getsource(push_scope._load_positions)
    assert "_SNAPSHOT_SQL" in src  # 同一段 SQL
    assert "merge_real_sources" in src  # 同一 merge 口径
    assert "real_account_snapshots" not in src  # 防第二份 SQL 漂移
