"""决策提示词补块（``shared/decision_blocks``）单测：模型分数 + 市场状态。

两条纪律的守卫：

1. **绝不编数**——``rank_pct`` 缺失/越界不给「信号分」；regime 整包读不到、指数快照
   没取到、``inputs_ok=False``，全部如实降级并标注，绝不静默消失或补 0
   （``signal_score=0`` 的语义是「引擎明确不入场」，编 0 会被模型读成明确看空）。
2. **不可变 + 纯函数**——只吃快照对象，不连 Redis/库；行情补分返回新元组。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.shared.decision.context import PoolQuote
from backend.shared.decision_blocks import (
    attach_signal_scores,
    build_market_state_block,
    build_signal_meta_block,
)

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 14, 9, 35, tzinfo=CST)


def _quote(code: str = "600036.SH", signal_score: float | None = None) -> PoolQuote:
    return PoolQuote(
        code=code, name="测试票", price=40.0, age_min=0.5, signal_score=signal_score
    )


class TestAttachSignalScores:
    def test_fills_research_score_scale_from_rank_pct(self):
        # rank_pct 0.882 → 研究评分 88.2（与前端 researchScore.ts 同一公式/金样；
        # 渲染器行内格式 `· 信号分 {:.1f}` 是 0–100 刻度）
        quotes = (_quote(),)
        score_map = {"SH600036": {"value": 0.31, "rank_pct": 0.882}}
        out, filled = attach_signal_scores(quotes, score_map)

        assert filled == 1
        assert out[0].signal_score == 88.2
        assert quotes[0].signal_score is None  # 不可变：原对象不动

    def test_missing_or_out_of_range_rank_pct_gives_no_score(self):
        # 查无此票 / rank_pct=None / 越界（1.5——调用方把百分数当分位传了）
        # 一视同仁：不给分，也不给 0
        quotes = (_quote("600036.SH"), _quote("600519.SH"), _quote("000001.SZ"))
        score_map = {
            "SH600519": {"value": 0.2, "rank_pct": None},
            "SZ000001": {"value": 0.1, "rank_pct": 1.5},
        }
        out, filled = attach_signal_scores(quotes, score_map)

        assert filled == 0
        assert all(q.signal_score is None for q in out)

    def test_non_a_share_quote_is_untouched(self):
        quotes = (_quote("00700.HK"),)
        out, filled = attach_signal_scores(quotes, {"SH600036": {"rank_pct": 0.9}})

        assert filled == 0 and out[0].signal_score is None

    def test_entries_without_rank_pct_key_are_tolerated(self):
        # 老测试替身/老调用方给的条目没有 rank_pct 键也不能炸
        out, filled = attach_signal_scores((_quote(),), {"SH600036": {"value": 0.3}})

        assert filled == 0 and out[0].signal_score is None

    def test_empty_inputs(self):
        assert attach_signal_scores((), {"SH600036": {"rank_pct": 0.9}}) == ((), 0)
        out, filled = attach_signal_scores((_quote(),), None)
        assert filled == 0 and out[0].signal_score is None


class TestBuildSignalMetaBlock:
    META = {
        "ok": True,
        "model_id": "mdl_abc",
        "signal_date": "2026-10-14",
        "rows": 591,
        "realtime_rows": 591,
        "fallback": False,
    }

    def test_ok_path_carries_source_and_scale_note(self):
        text = build_signal_meta_block(self.META, filled=12, total=15)

        assert "【盘中模型信号】" in text
        assert "mdl_abc" in text and "2026-10-14" in text
        assert "覆盖 591 只" in text and "盘中实时行 591" in text
        # 口径句必须在：分数是该行所属批次内部的截面分位；快照可能混批（mode=off
        # 实测四批混合），绝不能写成「同一模型、同一次推理批次」的单一来源断言
        assert "该行所属推理批次内部的截面分位" in text
        assert "可能混合多个推理批次" in text
        assert "同一模型、同一次推理批次" not in text
        assert "本轮行情块 15 只中 12 只有分" in text

    def test_fallback_day_is_marked(self):
        text = build_signal_meta_block(
            {**self.META, "fallback": True}, filled=1, total=1
        )

        assert "回退日" in text

    def test_not_ok_renders_unavailable_with_reason(self):
        text = build_signal_meta_block(
            {"ok": False, "reason": "分数快照取数失败: db down"}, filled=0, total=15
        )

        assert "【盘中模型信号】本轮不可得" in text
        assert "分数快照取数失败: db down" in text
        assert "不要凭空引用模型分数" in text

    def test_ok_but_no_signal_date_is_also_unavailable(self):
        # ok=True 但没有信号日 = 表查得到、里面没数据（推理从未落库）——
        # 不能渲染成「覆盖 0 只」的正常快照
        text = build_signal_meta_block({"ok": True}, filled=0, total=15)

        assert "本轮不可得" in text

    def test_none_meta_is_unavailable(self):
        assert "本轮不可得" in build_signal_meta_block(None, filled=0, total=0)


class TestBuildMarketStateBlock:
    """``qm:regime:intraday`` 真形状 = 全字符串 Hash（含 ``str(None)`` 的字面
    "None"）。渲染必须逐字段容错：脏值至多丢一行，不许炸穿整块。"""

    REGIME = {
        "state": "bear",
        "breadth": (
            '{"coverage": "hot_set", "sample": 540, "up": 107, "down": 417,'
            ' "flat": 16, "limit_up": 9, "limit_down": 2}'
        ),
        "ret": "-0.0748",
        "window": "20",
        "vol": "0.0083",
        "position_hint": "0.3",
        "generated_at": NOW.isoformat(),
        "inputs_ok": "True",
        "index": "000300.SH",
        "notes": "[]",
    }
    INDEX_SNAP = {"Now": 4590.12, "PreClose": 4615.5, "timestamp": NOW.timestamp()}

    def test_full_real_shaped_hash(self):
        text = build_market_state_block(self.REGIME, self.INDEX_SNAP, NOW)

        assert text.startswith("【盘中市场状态】")
        assert "状态：偏弱（bear）" in text
        assert "建议仓位系数 0.3" in text
        assert "20 日收益 -7.48%" in text
        assert "日波动率（20 日窗口）0.83%" in text
        assert "指数 000300.SH：现价 ¥4590.12（-0.55%）" in text
        assert "涨 107 ／ 跌 417 ／ 平 16" in text
        assert "涨停 9 ／ 跌停 2" in text
        assert "热集样本 540" in text
        assert "快照生成于 2026-10-14 09:35:00（0 分钟前）" in text
        assert "超新鲜窗口" not in text

    def test_missing_regime_renders_unavailable(self):
        for regime in (None, {}):
            text = build_market_state_block(regime, None, NOW)
            assert "【盘中市场状态】本轮不可得" in text
            assert "不要凭空断言大盘强弱" in text

    def test_unrendered_index_says_so_instead_of_omitting(self):
        # 指数代码在、快照没取到：如实写「未取到（不参与定价）」——
        # 静默少一行会让模型以为「今天指数没动」
        text = build_market_state_block(self.REGIME, None, NOW)

        assert "指数 000300.SH：本轮行情未取到（不参与定价）" in text

    def test_stale_generated_at_is_marked(self):
        stale = {
            **self.REGIME,
            "generated_at": (NOW - timedelta(minutes=27)).isoformat(),
        }
        text = build_market_state_block(stale, None, NOW)

        assert "27 分钟前，超新鲜窗口：引擎可能停摆或非交易时段" in text

    def test_none_literal_window_does_not_crash(self):
        # 写侧 str(None) 会留下字面 "None"（实测 vratio 就是）——window 脏值
        # 只丢「N 日」前缀，不丢整块、也不出「（窗口窗口）」这种洋相
        dirty = {**self.REGIME, "window": "None"}
        text = build_market_state_block(dirty, None, NOW)

        assert "窗口收益 -7.48%" in text
        assert "日波动率0.83%" in text
        assert "（窗口窗口）" not in text

    def test_inputs_not_ok_marks_default_verdict(self):
        # inputs_ok=False 时写侧的 neutral 是**缺省值**不是判定值——标出来，
        # 防模型把缺省当结论
        text = build_market_state_block(
            {**self.REGIME, "state": "neutral", "inputs_ok": "False"}, None, NOW
        )

        assert "状态：中性（neutral）（输入不完整：以上为缺省判定）" in text

    def test_unknown_state_is_echoed_not_guessed(self):
        text = build_market_state_block({**self.REGIME, "state": "weird"}, None, NOW)

        assert "状态：weird" in text
        assert "（weird）" not in text  # 未知态不回显成「weird（weird）」

    def test_bad_breadth_json_drops_the_line_only(self):
        text = build_market_state_block(
            {**self.REGIME, "breadth": "{不是 JSON"}, None, NOW
        )

        assert "广度" not in text
        assert "状态：偏弱（bear）" in text  # 其余照常

    def test_notes_ride_the_stamp_line(self):
        regime = {
            **self.REGIME,
            "notes": '["volume_ratio_implausible(0.01)——量纲/单位可疑，弃用该输入"]',
        }
        text = build_market_state_block(regime, None, NOW)

        assert "注：volume_ratio_implausible(0.01)" in text

    def test_ts_epoch_fallback_for_stamp(self):
        # 没有 generated_at 时退 epoch 秒；两个都没有 = 不渲染时间行（但块还在）
        regime = {k: v for k, v in self.REGIME.items() if k != "generated_at"}
        regime["ts"] = NOW.timestamp()
        assert "快照生成于 2026-10-14 09:35:00" in build_market_state_block(
            regime, None, NOW
        )

        regime.pop("ts")
        text = build_market_state_block(regime, None, NOW)
        assert "快照生成于" not in text


class TestRendererIntegration:
    """补块经过 ``RebalanceContext`` 的落位：市场状态在大盘方向后、行情块里带
    信号分后缀；缺省（""）时整段不渲染——存量金样逐字节不变。"""

    def test_market_state_block_sits_after_direction_before_pool(self):
        from backend.shared.decision.context import (
            DirectionBlock,
            PoolRow,
            RebalanceContext,
            render_prompt,
        )

        ctx = RebalanceContext(
            agent="test-agent",
            now=NOW,
            direction=DirectionBlock(direction="弱势震荡"),
            pool=(PoolRow(code="600036.SH", name="招商银行"),),
            quotes=(_quote(signal_score=88.2),),
            market_state_block="【盘中市场状态】X",
            signal_meta_block="【盘中模型信号】Y",
        )
        text = render_prompt(ctx)

        assert "· 信号分 88.2" in text
        assert text.index("弱势震荡") < text.index("【盘中市场状态】X")
        assert text.index("【盘中市场状态】X") < text.index("【候选池】")
        assert text.index("【候选池实时行情") < text.index("【盘中模型信号】Y")

    def test_signal_meta_block_needs_the_quote_block(self):
        # 信号分后缀活在行情行里：行情块没渲染（无池/无报价）→ 出处块跟着不出现
        from backend.shared.decision.context import RebalanceContext, render_prompt

        ctx = RebalanceContext(
            agent="test-agent",
            now=NOW,
            quotes=(),
            market_state_block="【盘中市场状态】X",
            signal_meta_block="【盘中模型信号】Y",
        )
        text = render_prompt(ctx)

        assert "【盘中模型信号】Y" not in text
        assert "【盘中市场状态】X" in text  # 市场状态块不依赖行情块

    def test_defaults_keep_the_renderer_byte_neutral(self):
        from backend.shared.decision.context import (
            DirectionBlock,
            PoolRow,
            RebalanceContext,
            render_prompt,
        )

        base = {
            "agent": "test-agent",
            "now": NOW,
            "direction": DirectionBlock(direction="弱势震荡"),
            "pool": (PoolRow(code="600036.SH", name="招商银行"),),
            "quotes": (_quote(),),
        }
        without = render_prompt(RebalanceContext(**base))
        with_empty = render_prompt(
            RebalanceContext(**base, market_state_block="", signal_meta_block="")
        )

        assert without == with_empty
        assert "【盘中市场状态】" not in without
        assert "【盘中模型信号】" not in without
