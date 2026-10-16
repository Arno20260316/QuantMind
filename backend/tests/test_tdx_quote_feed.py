"""TDX 实时行情 Feed 的告警排版（2026-10-16 QQ 中文名与排版设计 §4.1/§4.2）。

背景（设计稿实拍）：止损止盈提醒标题是裸码 ``[trading] 止损止盈提醒 · SZ002074``
（桥持仓 name 字段常为空）；策略提醒 5 只持仓 3 秒内 5 连发——
本次收敛为「单事件卡（止损止盈）+ 120s 批量卡（策略提醒，走 ``qq_digest``）」。

口径要点（钉在测试里）：
* 名字解析失败只退化为代码，**绝不编造**（消费端纪律：少一个名字 ≠ 少一条告警）。
* 分数/成本/现价沿用各生产者 ``.2f`` 现口径（不跨卡强改精度）。
* 策略提醒不再单发（``qq_alert=False`` 见调用点），行文本与批量卡 header/footer
  的接线由 ``_enqueue_strategy_qq`` 的 kwargs 钉住。
"""

from __future__ import annotations

import pytest

from backend.services.live_trading.services import tdx_quote_feed as tq

pytestmark = pytest.mark.unit


# ── 止损止盈（单事件卡）───────────────────────────────────────────────


def test_sltp_alert_uses_bridge_name_with_prefix_code():
    """实拍改后（设计 §4.1）：``⚠️ 止损止盈 · 国轩高科(SZ002074)``。"""
    title, content = tq.format_sltp_alert_text(
        symbol="SZ002074",
        name="国轩高科",
        reason="触发止盈",
        entry=41.20,
        price=43.55,
    )
    assert title == "⚠️ 止损止盈 · 国轩高科(SZ002074)"
    assert content == "触发止盈 ｜ 成本 ¥41.20 ｜ 现价 ¥43.55"


def test_sltp_alert_backfills_name_when_bridge_row_has_none(monkeypatch):
    """桥持仓 name 为空时的兜底：解析器回填中文名（patch 保证确定性）。"""
    import backend.shared.stock_display_name as sdn

    monkeypatch.setattr(sdn, "resolve", lambda symbol, hint="": "国轩高科")
    title, _ = tq.format_sltp_alert_text(
        symbol="SZ002074", name="", reason="触发止损", entry=40.0, price=39.1
    )
    assert title == "⚠️ 止损止盈 · 国轩高科(SZ002074)"


def test_sltp_alert_degrades_to_code_when_resolver_down(monkeypatch):
    """解析器抛/查无 → 只显示代码，标题照发（绝不因少一个名字丢告警）。"""
    import backend.shared.stock_display_name as sdn

    def _boom(symbol, hint=""):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(sdn, "resolve", _boom)
    title, content = tq.format_sltp_alert_text(
        symbol="SZ002074", name="", reason="触发止损", entry=40.0, price=39.1
    )
    assert title == "⚠️ 止损止盈 · SZ002074"
    assert content == "触发止损 ｜ 成本 ¥40.00 ｜ 现价 ¥39.10"


# ── 策略提醒（120s 批量卡）────────────────────────────────────────────


def test_strategy_digest_line_keeps_producer_precision():
    """行文法（设计 §3.4）：分数精度沿用生产者 ``.2f``（不改成 0.150）。"""
    line = tq.format_strategy_digest_line(
        symbol="SH601000", name="唐山港", score=0.12, threshold=0.2
    )
    assert line == "⚠️ 唐山港(SH601000) 推理分数 0.12 ≤ 阈值 0.20"


def test_strategy_digest_line_degrades_to_code_without_name(monkeypatch):
    import backend.shared.stock_display_name as sdn

    monkeypatch.setattr(sdn, "resolve", lambda symbol, hint="": "")
    line = tq.format_strategy_digest_line(
        symbol="SH601000", name="", score=0.12, threshold=0.2
    )
    assert line == "⚠️ SH601000 推理分数 0.12 ≤ 阈值 0.20"


def test_strategy_enqueue_joins_the_batch_card(monkeypatch):
    """接线：单股提醒进 120s 窗口卡（同 user 一个 digest_key），不再单发。"""
    import backend.shared.qq_digest as qd

    calls: list[dict] = []
    monkeypatch.setattr(qd, "enqueue", lambda **kw: calls.append(kw))

    tq._enqueue_strategy_qq(
        tenant_id="default",
        user_id="10000001",
        symbol="SZ002074",
        name="国轩高科",
        score=0.15,
        threshold=0.2,
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["digest_key"] == "strategy:default:10000001"
    assert call["header"] == "🔔 策略提醒"
    assert call["line"] == "⚠️ 国轩高科(SZ002074) 推理分数 0.15 ≤ 阈值 0.20"
    assert call["footer"] == "交易台 › 持仓监控"


def test_strategy_enqueue_never_raises_when_digest_explodes(monkeypatch):
    """批量卡坏了也不许反噬提醒主链（哨兵/Feed 消费端纪律）。"""
    import backend.shared.qq_digest as qd

    def _boom(**kw):
        raise RuntimeError("digest down")

    monkeypatch.setattr(qd, "enqueue", _boom)
    tq._enqueue_strategy_qq(
        tenant_id="default",
        user_id="10000001",
        symbol="SZ002074",
        name="国轩高科",
        score=0.15,
        threshold=0.2,
    )
