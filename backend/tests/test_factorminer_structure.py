"""FM-DSL 结构原创闸（``factorminer/structure.py``）契约测试。

T-FM-06：算子路谱（前序 op 序列 + n-gram 多重集 + 树深/节点数）→ 与库内
既有表达式分级（clone / literal_variant / near / novel）。硬拒只到 clone
（同式值必然重复，计算前拒零风险）；literal_variant（窗口/常数变体）是
T-FE-09 变体残差台账的**计算对象**，预拒会掐死变体探索——由值级 0.5 库级
闸兜底；near 只标注（未来生成循环的「饱和方向」输入）。

本文件钉三件事：

1. profile 的确定性计数（手写小树钉数、窗口字面量抽象、特征名保留）；
2. 分级定义边界（AST 相等 / 字面量变体 / trigram 余弦含等号 / novel）；
3. 110 全库 smoke + 已知结构对（fm024/fm025 仅窗口不同的变体）钉死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import structure  # noqa: E402
from backend.services.engine.alpha_agent.factorminer.dsl import Call, parse  # noqa: E402
from backend.services.engine.alpha_agent.factorminer.library import LIBRARY, by_id  # noqa: E402


# fm024/fm025：同一形状（open/close 的 TsRank(Delta(·, w), 2w) 差），仅窗口 6/9、12/18 不同。
_FM024 = "Sub(TsRank(Delta($open, 6), 12), TsRank(Delta($close, 6), 12))"
_FM025 = "Sub(TsRank(Delta($open, 9), 18), TsRank(Delta($close, 9), 18))"
# fm028/fm029：仅差顶层 Neg 与低价/高价字段——trigram 余弦介于「同形」与「无关」之间。
_FM028 = "Neg(Mul(Sub($close, $low), Div($volume, Mean($volume, 12))))"
_FM029 = "Mul(Sub($high, $close), Div($volume, Mean($volume, 12)))"


class TestProfile:
    def test_single_op_counts(self):
        prof = structure.profile(parse("Neg($close)"))
        assert prof.op_sequence == ("Neg",)
        assert prof.shape_key == "Neg,$close"
        assert prof.depth == 2
        assert prof.node_count == 2
        assert prof.op_count == 1
        assert prof.distinct_ops == 1
        assert prof.ngrams[1] == {("Neg",): 1}
        # 序列不足 n 时该档为空（不是补零）
        assert prof.ngrams[2] == {}
        assert prof.ngrams[3] == {}

    def test_zero_arg_call_does_not_raise(self):
        # 解析器产不出零参 Call，但手工构造 AST 可以——路谱必须给出深度而非
        # 让 max() 抛 ValueError（LOW-1：空 args 兜底）
        prof = structure.profile(Call(op="Const", args=()))
        assert prof.depth == 1
        assert prof.op_sequence == ("Const",)
        assert prof.node_count == 1

    def test_ngrams_top_level_read_only(self):
        # LOW-2：顶层映射只读——误写立刻 TypeError，而不是悄悄改掉被共享的路谱
        prof = structure.profile(parse(_FM024))
        with pytest.raises(TypeError):
            prof.ngrams[2] = {}  # type: ignore[index]

    def test_window_literal_abstracted_feature_kept(self):
        prof = structure.profile(parse(_FM024))
        assert prof.op_sequence == ("Sub", "TsRank", "Delta", "TsRank", "Delta")
        # 字面量抽象为 #（含各窗口算子自身的窗口）、特征名保留
        assert prof.shape_key == "Sub,TsRank,Delta,$open,#,#,TsRank,Delta,$close,#,#"
        assert prof.depth == 4
        assert prof.node_count == 11
        assert prof.op_count == 5
        assert prof.distinct_ops == 3
        assert prof.ngrams[2] == {
            ("Sub", "TsRank"): 1,
            ("TsRank", "Delta"): 2,
            ("Delta", "TsRank"): 1,
        }
        assert prof.ngrams[3] == {
            ("Sub", "TsRank", "Delta"): 1,
            ("TsRank", "Delta", "TsRank"): 1,
            ("Delta", "TsRank", "Delta"): 1,
        }

    def test_feature_identity_kept_in_shape(self):
        a = structure.profile(parse("Neg($close)"))
        b = structure.profile(parse("Neg($volume)"))
        assert a.shape_key != b.shape_key
        assert a.op_sequence == b.op_sequence

    def test_deterministic(self):
        assert structure.profile(parse(_FM024)) == structure.profile(parse(_FM024))


class TestAstEqual:
    def test_same_text(self):
        assert structure.ast_equal(parse(_FM024), parse(_FM024))

    def test_window_difference_not_equal(self):
        assert not structure.ast_equal(parse(_FM024), parse(_FM025))

    def test_numeric_value_semantics(self):
        # 数值按浮点值比较：1e-8 与 0.00000001 同值（elementwise 位置允许小数）
        assert structure.ast_equal(
            parse("Add($close, 1e-8)"), parse("Add($close, 0.00000001)")
        )


class TestSimilarity:
    def test_clone_self(self):
        node = parse(_FM024)
        sim = structure.similarity(node, node)
        assert sim.clone is True
        assert sim.shape_equal is True
        assert sim.trigram_cosine == pytest.approx(1.0)

    def test_literal_variant_windows(self):
        sim = structure.similarity(parse(_FM024), parse(_FM025))
        assert sim.clone is False
        assert sim.shape_equal is True
        assert sim.trigram_cosine == pytest.approx(1.0)
        assert sim.depth_delta == 0
        assert sim.node_count_delta == 0

    def test_feature_difference_not_shape_equal(self):
        sim = structure.similarity(parse("Neg($close)"), parse("Neg($volume)"))
        assert sim.clone is False
        assert sim.shape_equal is False

    def test_trigram_cosine_partial_overlap(self):
        # fm028 = Neg(Mul(Sub,Div,Mean))、fm029 = Mul(Sub,Div,Mean)：
        # 3 trigram vs 2 trigram、重叠 2 → 2/√(3·2)
        sim = structure.similarity(parse(_FM028), parse(_FM029))
        assert sim.clone is False
        assert sim.shape_equal is False
        assert sim.trigram_cosine == pytest.approx(2.0 / (3.0**0.5 * 2.0**0.5))
        assert sim.depth_delta == 1

    def test_disjoint_is_zero(self):
        sim = structure.similarity(parse("Neg($close)"), parse("Mul($close, $volume)"))
        assert sim.trigram_cosine == 0.0


class TestClassify:
    def test_tiers(self):
        clone = structure.similarity(parse(_FM024), parse(_FM024))
        variant = structure.similarity(parse(_FM024), parse(_FM025))
        near = structure.similarity(parse(_FM028), parse(_FM029))
        far = structure.similarity(parse("Neg($close)"), parse("Mul($close, $volume)"))
        assert structure.classify(clone) == structure.VERDICT_CLONE
        assert structure.classify(variant) == structure.VERDICT_LITERAL_VARIANT
        # 含等号：θ 恰好等于余弦值 → near
        assert (
            structure.classify(near, near_threshold=near.trigram_cosine)
            == structure.VERDICT_NEAR
        )
        assert (
            structure.classify(near, near_threshold=near.trigram_cosine + 1e-9)
            == structure.VERDICT_NOVEL
        )
        assert structure.classify(far) == structure.VERDICT_NOVEL

    def test_invalid_threshold(self):
        sim = structure.similarity(parse(_FM024), parse(_FM024))
        with pytest.raises(ValueError):
            structure.classify(sim, near_threshold=1.5)
        with pytest.raises(ValueError):
            structure.classify(sim, near_threshold=-0.1)


class TestScreen:
    def test_clone_hits_sorted_by_key(self):
        node = parse("Neg($close)")
        corpus = {"b": node, "a": node}
        res = structure.screen(node, corpus, near_threshold=0.8)
        assert res.verdict == structure.VERDICT_CLONE
        assert [hit.key for hit in res.matches] == ["a", "b"]
        assert res.best is not None and res.best.key == "a"

    def test_literal_variant_flagged(self):
        corpus = {"024": parse(_FM024)}
        res = structure.screen(parse(_FM025), corpus, near_threshold=0.8)
        assert res.verdict == structure.VERDICT_LITERAL_VARIANT
        assert res.best is not None and res.best.key == "024"

    def test_near_ranked_by_cosine(self):
        corpus = {"028": parse(_FM028), "neg": parse("Neg($close)")}
        res = structure.screen(parse(_FM029), corpus, near_threshold=0.8)
        assert res.verdict == structure.VERDICT_NEAR
        assert [hit.key for hit in res.matches] == ["028"]
        assert res.best is not None and res.best.key == "028"

    def test_strength_order_clone_before_near(self):
        corpus = {"near": parse(_FM028), "exact": parse(_FM029)}
        res = structure.screen(parse(_FM029), corpus, near_threshold=0.8)
        assert res.verdict == structure.VERDICT_CLONE
        assert [hit.key for hit in res.matches] == ["exact", "near"]

    def test_novel_when_below_threshold(self):
        corpus = {"neg": parse("Neg($close)")}
        res = structure.screen(
            parse("Mul($close, $volume)"), corpus, near_threshold=0.8
        )
        assert res.verdict == structure.VERDICT_NOVEL
        assert res.matches == ()
        assert res.best is None

    def test_empty_corpus(self):
        res = structure.screen(parse("Neg($close)"), {}, near_threshold=0.8)
        assert res.verdict == structure.VERDICT_NOVEL
        assert res.best is None

    def test_deterministic(self):
        corpus = {"024": parse(_FM024), "028": parse(_FM028)}
        cand = parse(_FM025)
        first = structure.screen(cand, corpus, near_threshold=0.8)
        second = structure.screen(cand, corpus, near_threshold=0.8)
        assert first == second


class TestLibraryPopulation:
    def test_all_110_profileable(self):
        for factor in LIBRARY:
            prof = structure.profile(factor.ast)
            assert prof.op_count >= 1
            assert prof.node_count == len(tuple(_walk_nodes(factor.ast)))
        assert len(LIBRARY) == 110

    def test_fm024_fm025_literal_variant(self):
        sim = structure.similarity(by_id("024").ast, by_id("025").ast)
        assert sim.clone is False
        assert sim.shape_equal is True

    def test_library_has_no_exact_ast_clones(self):
        clones = []
        for i, a in enumerate(LIBRARY):
            for b in LIBRARY[i + 1 :]:
                if structure.ast_equal(a.ast, b.ast):
                    clones.append((a.fid, b.fid))
        assert clones == []


def _walk_nodes(node):
    from backend.services.engine.alpha_agent.factorminer.dsl import walk

    return walk(node)
