"""方向配额（T-FE-10）—— 生成侧代理模型 + 三档配额的单测契约。

钉住的边：

1. **代理模型钉数**（D1）：``p = (n_success + 0.5) / (n_tasks + 1.0)``（Jeffreys
   先验，无史 = 0.5 中性）；``sat_penalty = min(0.5, dup_hits_90d / 4)``；
   ``score = p × (1 − sat_penalty)``——常量单源，测试逐值钉死。
2. **硬饱和剔除**（D2）：``dup_hits_90d ≥ 2`` 的方向从三档候选一律剔除；
   剔尽 → 回退同档全集并标 ``tier_fallback="saturated"``。
3. **三档语义**：explore=空白度加权（复用 direction_sampling 口径）、
   exploit=代理打分 top-K 线性归一、mutate=最近成功方向集均匀抽；
   mutate 集空 → 退 exploit 并标 ``tier_fallback="mutate_empty"``。
4. **名额切分**：floor + 余数给 exploit（常量 0.3/0.5/0.2）。
5. **meta 可重放**：``Random(seed).choices(candidates, weights)`` 复算必得
   同一命中（与 T-MV-03 同纪律）；N ≥ 候选数直派全集 meta=None。
6. **兜底不假装**：读史/打分失败 → random 空白度加权 + meta 标
   ``quota_fallback``——不拦任务创建，也绝不假装配额成功。

纯函数 + 注入 reader（默认 reader 打真库是 ``allocate`` 的路由面职责）；
本文件不碰真库。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import direction_quota as dq  # noqa: E402

# ── 桩 ────────────────────────────────────────────────────────────────


def _readers(outcomes=None, recents=None, dup=None):
    """三个注入 reader 的桩：``outcomes`` 键 → (attempts, n_tasks, n_success)。"""

    async def outcomes_reader(directions, *, user_id, market):
        rows = outcomes or {}
        if directions is None:
            return dict(rows)
        return {d: rows[d] for d in directions if d in rows}

    async def recents_reader(*, user_id, market):
        return list(recents or [])

    async def dup_reader(*, user_id, market):
        return dict(dup or {})

    return outcomes_reader, recents_reader, dup_reader


async def _allocate(directions, n, *, outcomes=None, recents=None, dup=None, **over):
    o, r, d = _readers(outcomes, recents, dup)
    kwargs = {
        "user_id": "u1",
        "market": "a_share",
        "outcomes_reader": o,
        "recents_reader": r,
        "dup_reader": d,
    }
    kwargs.update(over)
    return await dq.allocate(directions, n, **kwargs)


def _replay(meta: dict) -> str:
    """复算方按 meta 重放：必得同一命中（抽样凭证的自证）。"""
    return random.Random(meta["seed"]).choices(
        [c["direction"] for c in meta["candidates"]],
        weights=[c["weight"] for c in meta["candidates"]],
        k=1,
    )[0]


# ── D1 代理模型钉数 ────────────────────────────────────────────────────


def test_success_rate_jeffreys_pinned():
    """无史 = 0.5 中性；平滑公式逐值钉死（负数收敛不臆造）。"""
    assert dq.proxy_success_rate(n_success=0, n_tasks=0) == 0.5
    assert dq.proxy_success_rate(n_success=0, n_tasks=4) == pytest.approx(0.1)
    assert dq.proxy_success_rate(n_success=1, n_tasks=1) == pytest.approx(0.75)
    assert dq.proxy_success_rate(n_success=2, n_tasks=2) == pytest.approx(2.5 / 3.0)
    # 脏数据：负数按 0 收敛（不产生 >1 或 <0 的成功率）
    assert dq.proxy_success_rate(n_success=-3, n_tasks=-3) == 0.5
    assert dq.proxy_success_rate(n_success=9, n_tasks=1) == pytest.approx(1.0)


def test_saturation_penalty_pinned_and_capped():
    assert dq.saturation_penalty(0) == 0.0
    assert dq.saturation_penalty(1) == pytest.approx(0.25)
    assert dq.saturation_penalty(2) == pytest.approx(0.5)
    assert dq.saturation_penalty(4) == pytest.approx(0.5), "上限 0.5"
    assert dq.saturation_penalty(99) == pytest.approx(0.5)
    assert dq.saturation_penalty(-2) == 0.0


def test_build_score_is_product_and_saturated_flag():
    s = dq.build_score(
        direction="A", attempts=3, n_tasks=1, n_success=1, dup_hits_90d=1
    )
    assert s.p == pytest.approx(0.75)
    assert s.sat_penalty == pytest.approx(0.25)
    assert s.score == pytest.approx(0.75 * 0.75)
    assert s.saturated is False
    s2 = dq.build_score(
        direction="B",
        attempts=9,
        n_tasks=2,
        n_success=0,
        dup_hits_90d=dq.SATURATED_MIN_DUP_HITS_90D,
    )
    assert s2.saturated is True, "硬饱和阈值：dup_hits_90d ≥ 2"
    assert s2.score == pytest.approx(s2.p * (1 - s2.sat_penalty))


# ── D2 名额切分（floor + 余数给 exploit）──────────────────────────────


def test_split_slots_floor_plus_remainder_to_exploit():
    assert dq.split_slots(10) == {"explore": 3, "exploit": 5, "mutate": 2}
    assert dq.split_slots(5) == {"explore": 1, "exploit": 3, "mutate": 1}
    assert dq.split_slots(3) == {"explore": 0, "exploit": 3, "mutate": 0}
    assert dq.split_slots(4) == {"explore": 1, "exploit": 3, "mutate": 0}
    assert dq.split_slots(7) == {"explore": 2, "exploit": 4, "mutate": 1}
    assert dq.split_slots(1) == {"explore": 0, "exploit": 1, "mutate": 0}
    for n in range(1, 11):
        assert sum(dq.split_slots(n).values()) == n, "名额总数恒等于 N"


def test_quota_constants_pinned():
    assert (dq.QUOTA_EXPLORE, dq.QUOTA_EXPLOIT, dq.QUOTA_MUTATE) == (0.3, 0.5, 0.2)
    assert dq.TIER_ORDER == ("explore", "exploit", "mutate")
    assert dq.MODE_QUOTA == "quota"
    assert dq.EXPLOIT_TOPK == 6
    assert dq.RECENT_SUCCESS_TASKS == 5
    assert dq.DEFAULT_PREVIEW_N == 3


# ── 分配主路径 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allocate_slots_tiers_and_replayable_metas():
    """N=5/8 候选：名额 1/3/1，槽位顺序 explore→exploit→mutate，逐条可重放。

    逐步不放回意味着只有「第一个槽位」的候选池完全确定；后续槽位按
    「剩余集合」动态取池——断言因此写成「对每一步的剩余集合成立」的
    不变量（成员随抽中变化，公式/顺序/可重放不随变化）。
    """
    dirs = [f"方向{i}" for i in range(8)]
    outcomes = {
        "方向0": (4, 2, 1),  # 有史且半成功
        "方向1": (1, 1, 1),  # 全成功 → 最高分 0.75
    }
    picks = await _allocate(dirs, 5, outcomes=outcomes, recents=["方向1"])

    metas = [m for _, m in picks]
    assert [m["tier"] for m in metas] == [
        "explore",
        "exploit",
        "exploit",
        "exploit",
        "mutate",
    ]
    assert all(m["mode"] == dq.MODE_QUOTA for m in metas)
    picked = [d for d, _ in picks]
    assert len(set(picked)) == 5, "并行方向必须互不相同"

    # explore 档（第一个槽位，剩余=全集）：空白度加权，候选 = 传入顺序全集
    exp = metas[0]
    assert exp["weighting"] == dq.WEIGHTING_BLANKNESS
    assert [c["direction"] for c in exp["candidates"]] == dirs
    by_dir = {c["direction"]: c for c in exp["candidates"]}
    assert by_dir["方向0"]["attempts"] == 4
    assert by_dir["方向0"]["weight"] == pytest.approx(0.2)
    assert by_dir["方向2"]["weight"] == pytest.approx(1.0)

    # exploit 档：代理打分 top-K（K=min(6, |剩余|)）降序、线性归一权重
    remaining = set(dirs) - {picked[0]}
    for k in range(1, 4):
        expl = metas[k]
        assert expl["weighting"] == dq.WEIGHTING_PROXY
        cands = expl["candidates"]
        assert {c["direction"] for c in cands} <= remaining, "候选不越剩余集合"
        assert len(cands) == min(dq.EXPLOIT_TOPK, len(remaining))
        scores = [c["score"] for c in cands]
        assert scores == sorted(scores, reverse=True), "top-K 按分数降序"
        total = sum(scores)
        for c in cands:
            assert c["weight"] == pytest.approx(c["score"] / total)
        assert expl["picked"] in {c["direction"] for c in cands}
        remaining -= {expl["picked"]}

    # mutate 档：最近成功方向集均匀；若该方向已被前四步抽走则如实回退
    mut = metas[4]
    assert mut["tier"] == "mutate"
    if "方向1" in remaining:
        assert mut["weighting"] == dq.WEIGHTING_UNIFORM
        assert [c["direction"] for c in mut["candidates"]] == ["方向1"]
        assert mut["picked"] == "方向1"
        assert "tier_fallback" not in mut
    else:
        assert mut["tier_fallback"] == dq.FALLBACK_MUTATE_EMPTY

    # 逐条独立重放（同 seed 同命中）与全表快照
    for m in metas:
        assert _replay(m) == m["picked"]
        snap = m["scores_snapshot"]
        assert set(snap) == set(dirs)
        assert {
            "attempts",
            "n_tasks",
            "n_success",
            "p",
            "dup_hits_90d",
            "sat_penalty",
            "score",
            "saturated",
        } <= set(snap["方向0"])


@pytest.mark.asyncio
async def test_saturated_directions_removed_from_all_tiers():
    """硬饱和（dup_hits_90d ≥ 2）从三档一律剔除；仅当该档剔尽才按规回退。"""
    dirs = ["饱和方向", "A", "B", "C", "D", "E", "F"]
    picks = await _allocate(
        dirs,
        5,
        recents=["饱和方向", "A"],
        dup={"饱和方向": 2},
    )
    picked = [d for d, _ in picks]
    # explore+exploit 四槽：非饱和候选充足，饱和方向绝不出现在任何池里
    for _, meta in picks[:4]:
        assert "饱和方向" not in [c["direction"] for c in meta["candidates"]]
        assert "tier_fallback" not in meta
    assert "饱和方向" not in picked[:4]
    # mutate 槽（recents∩候选 = {饱和方向, A}）：A 未被前序抽走 → 均匀抽 A；
    # A 已被抽走 → 剔尽回退同档全集（{饱和方向}）并如实标注
    mut = picks[4][1]
    assert mut["tier"] == "mutate"
    if "A" in picked[:4]:
        assert [c["direction"] for c in mut["candidates"]] == ["饱和方向"]
        assert mut["tier_fallback"] == dq.FALLBACK_SATURATED
    else:
        assert [c["direction"] for c in mut["candidates"]] == ["A"]
        assert mut["picked"] == "A"
        assert "tier_fallback" not in mut


@pytest.mark.asyncio
async def test_all_saturated_falls_back_to_full_pool_with_flag():
    """剔尽 → 回退同档全集 + tier_fallback=saturated（不空转、不静默）。"""
    dirs = ["甲", "乙", "丙"]
    picks = await _allocate(dirs, 2, dup={"甲": 5, "乙": 3, "丙": 2})
    assert [d for d, _ in picks] != [], "全饱和不空转"
    first_pick = picks[0][0]
    for idx, (_d, meta) in enumerate(picks):
        assert meta["tier_fallback"] == dq.FALLBACK_SATURATED
        pool = {c["direction"] for c in meta["candidates"]}
        if idx == 0:
            assert pool == set(dirs), "回退池 = 同档全集（exploit 回退按打分序）"
        else:
            assert pool == set(dirs) - {first_pick}, "回退池 = 该步的剩余全集"


@pytest.mark.asyncio
async def test_mutate_empty_falls_to_exploit_with_flag():
    """最近成功集空 → mutate 槽位退 exploit 池（tier 仍是 mutate，如实标注）。"""
    dirs = ["A", "B", "C", "D", "E", "F", "G", "H"]
    picks = await _allocate(dirs, 5, recents=[])
    mut = picks[4][1]
    assert mut["tier"] == "mutate"
    assert mut["tier_fallback"] == dq.FALLBACK_MUTATE_EMPTY
    assert mut["weighting"] == dq.WEIGHTING_PROXY, "退到利用档 = 代理打分池"


@pytest.mark.asyncio
async def test_mutate_recents_outside_candidates_are_ignored():
    """最近成功方向不在候选词表内 → 不算 mutate 集（候选来源恒为传入词表）。"""
    dirs = ["A", "B", "C", "D", "E", "F", "G", "H"]
    picks = await _allocate(dirs, 5, recents=["未选中的历史方向"])
    mut = picks[4][1]
    assert mut["tier_fallback"] == dq.FALLBACK_MUTATE_EMPTY


@pytest.mark.asyncio
async def test_n_not_smaller_than_candidates_direct_dispatch_without_meta():
    """N ≥ 候选数：全集直派（去重保序）、meta=None——不伪造 seed。"""
    picks = await _allocate(["A", "B", "A"], 5)
    assert [(d, m) for d, m in picks] == [("A", None), ("B", None)]


@pytest.mark.asyncio
async def test_single_slot_is_exploit_tier():
    """N=1（单条派发）：名额全给 exploit——floor+余数给 exploit 的直接后果。"""
    picks = await _allocate(["A", "B", "C"], 1)
    assert len(picks) == 1
    assert picks[0][1]["tier"] == "exploit"
    assert picks[0][1]["mode"] == dq.MODE_QUOTA


@pytest.mark.asyncio
async def test_empty_candidates_raise_value_error():
    with pytest.raises(ValueError):
        await _allocate([], 3)
    with pytest.raises(ValueError):
        await _allocate(["  ", ""], 3)


@pytest.mark.asyncio
async def test_reader_failure_falls_back_to_random_with_quota_fallback_flag(
    monkeypatch,
):
    """读史炸了：退 random 空白度加权 + meta 标 quota_fallback——绝不假装配额。"""

    async def boom(directions, *, user_id, market):
        raise RuntimeError("db down")

    async def recents_ok(*, user_id, market):
        return []

    async def dup_ok(*, user_id, market):
        return {}

    out = await dq.allocate(
        ["A", "B", "C"],
        2,
        user_id="u1",
        market="a_share",
        outcomes_reader=boom,
        recents_reader=recents_ok,
        dup_reader=dup_ok,
    )
    assert len(out) == 2 and len({d for d, _ in out}) == 2
    for d, meta in out:
        assert meta["mode"] == "random", "退的是随机空白度加权"
        assert meta["quota_fallback"] is True
        assert meta["picked"] == d
        assert _replay(meta) == meta["picked"], "兜底证据同样可重放"


@pytest.mark.asyncio
async def test_dup_reader_failure_also_triggers_quota_fallback():
    """饱和数据读失败同样是「配额输入不可用」——不拿缺省 0 假装成功。"""

    async def outcomes_ok(directions, *, user_id, market):
        return {}

    async def recents_ok(*, user_id, market):
        return []

    async def dup_boom(*, user_id, market):
        raise RuntimeError("manifest unreadable")

    out = await dq.allocate(
        ["A", "B", "C"],
        2,
        user_id="u1",
        market="a_share",
        outcomes_reader=outcomes_ok,
        recents_reader=recents_ok,
        dup_reader=dup_boom,
    )
    assert all(meta["quota_fallback"] is True for _, meta in out)
    assert all(meta["mode"] == "random" for _, meta in out)


# ── 预演（plan_tiers / preview）────────────────────────────────────────


def test_plan_tiers_pools_and_fallbacks():
    scores = [
        dq.build_score(
            direction="A", attempts=0, n_tasks=1, n_success=1, dup_hits_90d=0
        ),
        dq.build_score(
            direction="B", attempts=0, n_tasks=0, n_success=0, dup_hits_90d=0
        ),
        dq.build_score(
            direction="C", attempts=0, n_tasks=0, n_success=0, dup_hits_90d=2
        ),
    ]
    plan = dq.plan_tiers(scores, recent_success_dirs=["A"])
    assert plan["explore"].pool == ("A", "B"), "饱和方向剔除"
    assert plan["exploit"].pool == ("A", "B")
    assert plan["mutate"].pool == ("A",)
    assert plan["explore"].fallback is None
    assert plan["mutate"].fallback is None
    assert plan["exploit"].weighting == dq.WEIGHTING_PROXY


@pytest.mark.asyncio
async def test_preview_table_slots_and_tier_plan():
    o, r, d = _readers(
        outcomes={
            "A": (2, 1, 1),
            "B": (5, 3, 0),
            "C": (0, 0, 0),
        },
        recents=["A"],
        dup={"C": 2},
    )
    data = await dq.preview(
        user_id="u1",
        market="a_share",
        directions=["A", "B", "C"],
        n=4,
        outcomes_reader=o,
        recents_reader=r,
        dup_reader=d,
    )
    assert data["available"] is True
    assert data["slots"] == {"explore": 1, "exploit": 3, "mutate": 0}
    assert data["quota"] == {"explore": 0.3, "exploit": 0.5, "mutate": 0.2}
    rows = {row["direction"]: row for row in data["directions"]}
    assert rows["A"]["p"] == pytest.approx(0.75)
    assert rows["B"]["p"] == pytest.approx(0.125)
    assert rows["C"]["saturated"] is True
    assert data["tiers"]["explore"]["pool"] == ["A", "B"]
    assert data["tiers"]["mutate"]["pool"] == ["A"]
    assert data["recent_success_dirs"] == ["A"]


@pytest.mark.asyncio
async def test_preview_without_directions_lists_history_and_no_tiers():
    o, r, d = _readers(outcomes={"A": (2, 1, 1)}, recents=["A"])
    data = await dq.preview(
        user_id="u1",
        market="a_share",
        directions=None,
        outcomes_reader=o,
        recents_reader=r,
        dup_reader=d,
    )
    assert data["available"] is True
    assert [row["direction"] for row in data["directions"]] == ["A"]
    assert data["tiers"] is None, "没有候选词表就没有档位预演"


@pytest.mark.asyncio
async def test_preview_failure_is_honest_not_exception():
    async def boom(directions, *, user_id, market):
        raise RuntimeError("db down")

    data = await dq.preview(
        user_id="u1",
        market="a_share",
        directions=["A"],
        outcomes_reader=boom,
    )
    assert data["available"] is False
    assert data["directions"] == [] and data["tiers"] is None
    assert "配额预览暂不可用" in data["error"]
    assert "db down" not in data["error"], "错误原文只进日志，不上屏"


# ── 指纹边界（配额决策不改变任务身份）────────────────────────────────


def test_direction_meta_not_in_fingerprint_boundary():
    """direction_meta（配额/抽样凭证）不是 canonical_params 的入参——
    同参同指纹与「这条方向怎么抽出来的」无关（结构性钉死）。"""
    import inspect

    from backend.services.engine.alpha_agent import task_fingerprint as tf

    params = inspect.signature(tf.canonical_params).parameters
    assert "direction_meta" not in params
    base = {
        "algorithm": "rdagent",
        "market": "a_share",
        "universe": "csi300",
        "data_source": None,
        "direction": "A",
        "direction_mode": "quota",
        "quality_gate_mode": None,
        "loop_n": 5,
        "freq": None,
        "mining_start": None,
        "mining_end": None,
        "valid_start": None,
        "valid_end": None,
        "datasets": None,
        "source": "text",
        "doc_id": None,
    }
    fp1 = tf.fingerprint_of(tf.canonical_params(**base))
    fp2 = tf.fingerprint_of(tf.canonical_params(**base))
    assert fp1 == fp2
