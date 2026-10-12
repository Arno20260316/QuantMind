"""宿主对话轮（``scripts/live_model_analysis.py``）测试。

覆盖 2026-10-12 晚补丁的三块：
1. **复盘轮**（``--mode review``）：指数块 / 持仓「今日」列 / 复盘要求模板 /
   ``mode`` 日志标记；
2. **摘要与卡片**：``extract_summary``（纯函数）与 ``format_review_card``；
3. **提醒面**：``decide_transition_alert`` 翻转状态机、``_flag_on`` 词表、
   ``format_round_alert``、``notify_qq`` best-effort、告警状态文件读写；
4. **截断重试**：第 2 次尝试带「精简输出」提示 + ``QM_DIALOGUE_MAX_TOKENS`` 覆盖。

脚本不是包内模块，用 importlib 从 ``scripts/`` 直接装载（import 无副作用：
不读 .env、不连桥、不建目录，路径常量只是 Path 拼接）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "live_model_analysis.py"
_CN_TZ = timezone(timedelta(hours=8))


@pytest.fixture(scope="module")
def lma():
    spec = importlib.util.spec_from_file_location("live_model_analysis_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_account(code: str = "601000.SH") -> dict:
    return {
        "asset": {"asset": 100000, "cash": 50000, "market_value": 50000},
        "positions": [
            {
                "stock_code": code,
                "stock_name": "唐山港",
                "total_volume": 1000,
                "available_volume": 1000,
                "cost_price": 4.5,
            }
        ],
    }


# ── 1. 复盘轮提示词拼装 ────────────────────────────────────────────────────


def test_review_content_has_index_block_positions_day_pct_and_requirement(lma):
    """复盘轮：指数当日表现 + 持仓「今日」列 + 复盘要求（摘要指令）。"""
    snap_calls: list[str] = []

    def snapshot_reader(env, code):
        snap_calls.append(code)
        return {"now": 10.0, "last_close": 9.5}

    def price_reader(env, code):  # 复盘轮不该走现价读（应走快照）
        raise AssertionError("review 模式不应调用 price_reader")

    content, gaps = lma.build_user_content(
        {},
        account_reader=lambda env: _fake_account(),
        price_reader=price_reader,
        snapshot_reader=snapshot_reader,
        mode="review",
    )

    assert gaps == []
    # 指数块：三大指数 + 涨跌（10.0/9.5-1=+5.26%）
    assert "【收盘行情】" in content
    assert "上证指数" in content and "深证成指" in content and "创业板指" in content
    assert "+5.26%" in content
    # 持仓表「今日」列 + 行尾值
    assert "| 浮动盈亏 | 今日 |" in content
    assert "| +122.22% | +5.26% |" in content
    # 复盘要求：三问 + 摘要指令
    assert "收盘复盘" in content
    assert "收盘复盘摘要" in content
    # 指数与持仓快照都走了 snapshot_reader（3 指数 + 1 持仓）
    assert snap_calls == ["000001.SH", "399001.SZ", "399006.SZ", "601000.SH"]


def test_review_content_index_unavailable_degrades_honestly(lma):
    """指数快照全失败：明说不可用，不编数。"""
    content, _ = lma.build_user_content(
        {},
        account_reader=lambda env: None,
        snapshot_reader=lambda env, code: {},
        mode="review",
    )
    assert "【收盘行情】指数数据不可用" in content


def test_intraday_content_unchanged_shape(lma):
    """盘中轮不回归：无指数块、无「今日」列、现价走 price_reader。"""
    content, gaps = lma.build_user_content(
        {},
        account_reader=lambda env: _fake_account(),
        price_reader=lambda env, code: 10.0,
        snapshot_reader=lambda env, code: (_ for _ in ()).throw(
            AssertionError("intraday 不应调用 snapshot_reader")
        ),
    )
    assert gaps == []
    assert "【收盘行情】" not in content
    assert "今日 |" not in content
    assert "| +122.22% |" in content  # 10.0/4.5-1
    assert "盘中分析" in content


def test_account_unreachable_still_forbids_position_commentary_both_modes(lma):
    for mode in ("intraday", "review"):
        content, gaps = lma.build_user_content(
            {}, account_reader=lambda env: None, mode=mode
        )
        assert gaps == ["account_unreachable"]
        assert "禁止" in content and "编造" in content


def test_requirement_block_review_vs_intraday_summary_instruction(lma):
    review = lma._requirement_block(has_account=True, has_positions=True, mode="review")
    assert "收盘复盘摘要" in review and "明日" in review
    intraday = lma._requirement_block(has_account=True, has_positions=True)
    assert "盘中分析" in intraday and "收盘复盘摘要" not in intraday
    # 空仓复盘也带摘要指令（复用同一常量）
    empty = lma._requirement_block(has_account=True, has_positions=False, mode="review")
    assert "收盘复盘摘要" in empty and "空仓" in empty


# ── 2. 摘要抽取与卡片 ─────────────────────────────────────────────────────


def test_extract_summary_takes_first_paragraph_and_strips_markdown(lma):
    content = "# 收盘复盘摘要\n**今日大盘**微跌 `0.3%`。\n\n## 详细展开\n后面还有很多字…"
    assert lma.extract_summary(content) == "收盘复盘摘要 今日大盘微跌 0.3%。"


def test_extract_summary_limits_length(lma):
    out = lma.extract_summary("字" * 300, limit=160)
    assert len(out) == 160 and out.endswith("…")


def test_extract_summary_empty_content(lma):
    assert lma.extract_summary("") == ""
    assert lma.extract_summary("\n\n  \n") == ""


def test_format_review_card_shape(lma):
    now = datetime(2026, 10, 12, 15, 30, tzinfo=_CN_TZ)
    title, content = lma.format_review_card([("m1", "摘要一"), ("m2", "")], now)
    assert title == "📋 收盘复盘 · 10-12"
    assert "◆ m1" in content and "摘要一" in content
    assert "（本轮无摘要）" in content
    assert "━" * 14 in content
    assert "交易台 › 模型对话" in content


# ── 3. 提醒面 ─────────────────────────────────────────────────────────────


def test_decide_transition_alert_state_machine(lma):
    assert lma.decide_transition_alert(False, False) is None
    assert lma.decide_transition_alert(False, True) == "down"
    assert lma.decide_transition_alert(True, True) is None  # 持续失联不重复推
    assert lma.decide_transition_alert(True, False) == "up"


def test_flag_on_default_on_wordlist(lma):
    assert lma._flag_on({}, "QM_DIALOGUE_ALERTS") is True
    assert lma._flag_on({"QM_DIALOGUE_ALERTS": "true"}, "QM_DIALOGUE_ALERTS") is True
    assert lma._flag_on({"QM_DIALOGUE_ALERTS": "1"}, "QM_DIALOGUE_ALERTS") is True
    for off in ("0", "false", "FALSE", "no", "off", " Off "):
        assert lma._flag_on({"QM_DIALOGUE_ALERTS": off}, "QM_DIALOGUE_ALERTS") is False
    assert lma._flag_on({}, "X", default=False) is False


def test_format_round_alert_partial_and_total(lma):
    title, content = lma.format_round_alert(["b:boom", "c:length"], 1, 3)
    assert "不满员" in title and "1/3" in content and "b、c" in content
    title2, content2 = lma.format_round_alert(["a:e1", "b:e2"], 0, 2)
    assert "全部失败" in title2 and "a、b" in content2


def test_notify_qq_best_effort(lma, monkeypatch):
    sent: list[tuple] = []

    class _FakePush:
        @staticmethod
        def notify(title, content, channel="default"):
            sent.append((title, content, channel))

    monkeypatch.setitem(sys.modules, "push_notify", _FakePush)
    assert lma.notify_qq("T", "C") is True
    assert sent == [("T", "C", "default")]

    class _BoomPush:
        @staticmethod
        def notify(title, content, channel="default"):
            raise RuntimeError("qq down")

    monkeypatch.setitem(sys.modules, "push_notify", _BoomPush)
    assert lma.notify_qq("T", "C") is False  # 绝不抛


def test_alert_state_roundtrip_and_corrupt_tolerance(lma, tmp_path, monkeypatch):
    state_file = tmp_path / ".alert_state.json"
    monkeypatch.setattr(lma, "ALERT_STATE_FILE", state_file)
    assert lma._load_alert_state() == {}  # 不存在 → 空
    lma._save_alert_state({"bridge_down": True, "last_round_alert_ts": 123.0})
    assert lma._load_alert_state()["bridge_down"] is True
    state_file.write_text("{ not json", encoding="utf-8")
    assert lma._load_alert_state() == {}  # 坏文件 → 空，不抛


def test_append_log_review_mode_marker(lma, tmp_path, monkeypatch):
    monkeypatch.setattr(lma, "LOG_DIR", tmp_path)
    path = lma.append_log("u", "a", "m1", None, ["account_unreachable"], mode="review")
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["mode"] == "review"
    assert entry["data_gaps"] == ["account_unreachable"]
    path2 = lma.append_log("u", "a", "m1", None, None)
    entry2 = json.loads(path2.read_text(encoding="utf-8").splitlines()[-1])
    assert "mode" not in entry2


# ── 4. 截断重试与 token 覆盖 ──────────────────────────────────────────────


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _ok_payload(content: str, finish: str) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def test_call_model_retry_appends_brevity_note(lma, monkeypatch):
    """首次截断 → 第 2 次尝试必须带「精简输出」提示，且首两次 payload 其余一致。"""
    import requests

    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _Resp(_ok_payload("半截话", "length"))
        return _Resp(_ok_payload("完整回复", "stop"))

    monkeypatch.setattr(requests, "post", fake_post)
    env = {"OPENAI_API_BASE": "https://x.test/v1", "OPENAI_API_KEY": "k"}
    content, usage = lma.call_model(env, "deepseek-v4-flash", "deepseek-v4-flash", "正文")
    assert content == "完整回复" and usage["total_tokens"] == 30
    assert len(calls) == 2
    first, second = calls[0], calls[1]
    assert first["max_tokens"] == 8000 and second["max_tokens"] == 8000
    assert "大幅精简" not in first["messages"][1]["content"]
    assert "大幅精简" in second["messages"][1]["content"]
    assert second["messages"][1]["content"].startswith("正文")
    # 其余字段逐字不动
    assert first["messages"][0] == second["messages"][0]
    assert first["temperature"] == second["temperature"] == 0.3


def test_call_model_env_max_tokens_override_and_invalid_fallback(lma, monkeypatch):
    import requests

    seen: list[int] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(json["max_tokens"])
        return _Resp(_ok_payload("好", "stop"))

    monkeypatch.setattr(requests, "post", fake_post)
    env = {
        "OPENAI_API_BASE": "https://x.test/v1",
        "OPENAI_API_KEY": "k",
        "QM_DIALOGUE_MAX_TOKENS": "12000",
    }
    lma.call_model(env, "deepseek-v4-flash", "deepseek-v4-flash", "x")
    assert seen == [12000]
    # 非法值 → 回退默认，不抛
    env["QM_DIALOGUE_MAX_TOKENS"] = "abc"
    lma.call_model(env, "deepseek-v4-flash", "deepseek-v4-flash", "x")
    assert seen[-1] == lma.MAX_TOKENS == 8000


def test_call_model_truncated_twice_raises(lma, monkeypatch):
    """两次都截断 → TruncatedOutputError（整轮作废语义不回归）。"""
    import requests

    monkeypatch.setattr(
        requests, "post", lambda *a, **kw: _Resp(_ok_payload("半截", "length"))
    )
    env = {"OPENAI_API_BASE": "https://x.test/v1", "OPENAI_API_KEY": "k"}
    with pytest.raises(lma.TruncatedOutputError):
        lma.call_model(env, "deepseek-v4-flash", "deepseek-v4-flash", "x")
