"""FM-DSL → qlib 表达式翻译（``factorminer/qlib_export.py``）契约测试。

钉三件事：

1. **逐算子映射表**——每个映射都是与 ``evaluator.py`` 的口径对照（qlib
   侧语义已由容器探针/对拍实测，见 ``test_factorminer_qlib_parity``），
   改映射必须同步重跑对拍后按意图重签本表；
2. **合成字段展开**（``$amt``→``$amount``、``$vwap``/``$returns`` 展开式）
   与 **CsRank gap**（截面算子，qlib 表达式引擎表达不了）；
3. **全库覆盖快照**：110 条 = 31 可翻译 + 79 gap（72 条 CsRank 截面 + 7 条
   EMA 递归口径）（T-FM-01 算子谱 census 的翻译侧镜像；数字变化 = 论文库或
   映射变了，必须重跑对拍）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import (  # noqa: E402
    qlib_export as qe,
)


class TestMapping:
    @pytest.mark.parametrize(
        ("fm", "expected"),
        [
            # 字段 / 字面量
            ("$close", "$close"),
            ("$amt", "$amount"),
            ("Add($close, 1e-6)", "Add($close, 0.000001)"),
            ("Add($close, 0.0001)", "Add($close, 0.0001)"),
            ("Mean($close, 12)", "Mean($close, 12)"),
            # 一元
            ("Neg($close)", "Mul($close, -1)"),
            # 时间窗改名类
            ("Delay($close, 5)", "Ref($close, 5)"),
            ("TsRank($close, 10)", "Rank($close, 10)"),
            ("TsMax($close, 10)", "Max($close, 10)"),
            ("TsMin($close, 10)", "Min($close, 10)"),
            ("SMA($close, 5)", "Mean($close, 5)"),
            ("TsArgMax($close, 5)", "Sub(5, IdxMax($close, 5))"),
            # 比较类：该 qlib 的 Greater/Less 是 np.maximum/minimum（环境事实），
            # 真比较在 Gt/Lt——见模块 docstring
            ("Greater($close, $open)", "Gt($close, $open)"),
            ("Less($close, $open)", "Lt($close, $open)"),
            ("Eq($close, $open)", "Eq($close, $open)"),
            # 逐元素组合类
            (
                "IfElse(Greater($close, $open), $close, $open)",
                "If(Gt(Gt($close, $open), 0), $close, $open)",
            ),
            # Max2/Min2 直映射到该 qlib 的 Greater/Less（= np.maximum/minimum）
            ("Max2($close, $open)", "Greater($close, $open)"),
            ("Min2($close, $open)", "Less($close, $open)"),
            (
                "SignedPower($volume, 0.5)",
                "Mul(Sign($volume), Power(Abs($volume), 0.5))",
            ),
            # WMA：该 qlib 多除一次窗长 → 满窗 ×n 复原
            ("WMA($close, 5)", "Mul(WMA($close, 5), 5)"),
            # And/Or 语义分叉：FM 布尔（(a>0) 判定），qlib 对原值 bitwise
            (
                "And(Greater($close, 10), Greater($volume, 100))",
                "Mul(Gt(Gt($close, 10), 0), Gt(Gt($volume, 100), 0))",
            ),
            (
                "Or(Greater($close, 10), Less($close, 1))",
                "Gt(Add(Gt(Gt($close, 10), 0), Gt(Lt($close, 1), 0)), 0)",
            ),
            # 同名直通（抽样；全量清单见模块常量，全库快照测试兜底）
            ("Corr($close, $volume, 12)", "Corr($close, $volume, 12)"),
            ("Delta($close, 5)", "Delta($close, 5)"),
            (
                "Mean(Div(Sub($high, $low), $close), 20)",
                "Mean(Div(Sub($high, $low), $close), 20)",
            ),
            # 嵌套 + 合成字段
            (
                "Neg(Div(Sub($close, $vwap), $vwap))",
                "Mul(Div(Sub($close, Mul(Mul(Div($amount, $volume), 10000), "
                "$factor)), Mul(Mul(Div($amount, $volume), 10000), $factor)), -1)",
            ),
            (
                "$returns",
                "Sub(Div($close, Ref($close, 1)), 1)",
            ),
        ],
    )
    def test_translation_table(self, fm: str, expected: str):
        result = qe.export(fm)
        assert result.ok, f"{fm} 应可翻译：{result.gap}"
        assert result.expression == expected

    def test_csrank_raises_gap(self):
        result = qe.export("Neg(CsRank($returns))")
        assert not result.ok
        assert "CsRank" in (result.gap or "")

    def test_ema_raises_gap(self):
        """该 qlib 的 EMA 是 adjust=True，求值器是 adjust=False 递归——初值
        暂态不可精确互译，必须给 gap 而不是近似的假翻译。"""
        result = qe.export("EMA($close, 10)")
        assert not result.ok
        assert "EMA" in (result.gap or "")

    def test_parse_failure_gives_gap_not_raise(self):
        result = qe.export("Foo($close)")
        assert not result.ok
        assert "FM 解析失败" in (result.gap or "")


class TestLibraryCoverageSnapshot:
    """全库翻译审计快照（与 qlib_export.library_coverage 同一实现）。"""

    def test_counts_31_79(self):
        cov = qe.library_coverage()
        assert cov.n_translatable == 31
        assert cov.n_gap == 79

    def test_all_gaps_are_csrank_or_ema(self):
        """79 个 gap 全部且仅因 CsRank（72）或 EMA（7）——映射表覆盖了其余
        所有算子（漏一个映射就会多出一个「未建立 qlib 映射」原因的 gap，
        这里立刻红）。"""
        cov = qe.library_coverage()
        for fid, reason in cov.gaps:
            assert "CsRank" in reason or "EMA" in reason, (
                f"fm{fid} 的 gap 既非 CsRank 也非 EMA：{reason}"
            )

    def test_translatable_fid_snapshot(self):
        cov = qe.library_coverage()
        assert [fid for fid, _ in cov.translatable] == [
            "006",
            "017",
            "023",
            "024",
            "025",
            "026",
            "027",
            "028",
            "029",
            "030",
            "031",
            "034",
            "057",
            "061",
            "072",
            "076",
            "077",
            "081",
            "082",
            "083",
            "084",
            "086",
            "087",
            "089",
            "090",
            "091",
            "093",
            "094",
            "095",
            "096",
            "100",
        ]

    def test_translated_strings_pass_qlib_whitelist(self):
        """翻译产物只含 qlib 表达式字符集；合成字段/截面算子/语义分叉算子
        不得漏进产物。"""
        token_re = re.compile(r"^[\w\s$.,()+\-*/]+$")
        cov = qe.library_coverage()
        for fid, expr in cov.translatable:
            assert token_re.fullmatch(expr), f"fm{fid} 含白名单外字符：{expr}"
            assert "CsRank" not in expr, f"fm{fid} 残留 CsRank"
            assert "$amt" not in expr, f"fm{fid} 残留 $amt（应展开为 $amount）"
            assert "$vwap" not in expr, f"fm{fid} 残留 $vwap（应展开）"
            assert "$returns" not in expr, f"fm{fid} 残留 $returns（应展开）"
            # 语义分叉算子必须已换形态：And/Or（布尔 vs bitwise）、EMA（gap 后
            # 不应出现）、裸 WMA（必须包在 Mul(..., n) 里）。
            assert "And(" not in expr, f"fm{fid} 残留 And（应翻成 Gt 指示积）"
            assert "Or(" not in expr, f"fm{fid} 残留 Or（应翻成 Gt 指示和）"
            assert "EMA(" not in expr, f"fm{fid} 残留 EMA（应为 gap）"
            assert "WMA(" not in expr or "Mul(WMA(" in expr, (
                f"fm{fid} 的 WMA 未包 ×n 复原"
            )
