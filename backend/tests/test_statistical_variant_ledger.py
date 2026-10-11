"""变体残差台账（T-FE-09）：父子映射一致性 + 台账 IO + 冷却判定回归。

三类钉死点：

- **映射即事实**：``BASELINE_BY_KEY`` 必须与 ``candidates`` 目录双向一致、
  无环、无自指；根/链中节点（任何人的父本）**永不冷却**（防掐断控制链）；
  可冷却集精确 = 全集 − 父本保护集 − 无母本键。
- **台账 IO 免疫**：坏 JSON 自愈为空骨架（不抛）；同幂等键 upsert 覆盖不
  堆积；观测裁剪到最近 24 条；save 原子（读者永不见半截 JSON）。
- **非对称否决纪律**：只有「高置信失败」（ok ∧ n_days≥40 ∧ 残差 ICIR<0.3）
  才冷却；不可验证（insufficient_days/unavailable）**永不判失败**；新鲜
  高置信通过观测解除冷却（lift）；冷却按 (变体, 池) 键控 + 90 天 TTL。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from backend.services.engine.alpha_agent.statistical import candidates as cand
from backend.services.engine.alpha_agent.statistical import variant_ledger as vl
from backend.shared.utc_datetime import utc_now


def _obs_dict(
    variant: str,
    *,
    universe: str = "all_a",
    window_end: str = "2026-09-30",
    run_id: str = "run1",
    status: str = "ok",
    ic_ir: float | None = 0.2,
    n_days: int = 60,
    horizon: int = 5,
    extra_horizons: tuple[dict, ...] = (),
) -> dict:
    """构造一条已序列化的观测（horizons 项 = IncrementalResult.to_dict + horizon）。"""
    stat = {
        "horizon": horizon,
        "status": status,
        "ic_ir": ic_ir,
        "ic_mean": 0.02 if ic_ir is not None else None,
        "n_days": n_days,
        "basis": [],
        "n_basis": 0,
        "max_basis_abs_corr": 0.1,
    }
    return {
        "variant_key": variant,
        "universe": universe,
        "window_start": "2024-01-01",
        "window_end": window_end,
        "run_id": run_id,
        "horizons": [stat, *extra_horizons],
        "best_horizon": horizon,
        "at": "2026-10-11T00:00:00Z",
    }


# ── 映射一致性 ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_baseline_map_matches_candidate_registry():
    keys = set(cand.CANDIDATE_BY_KEY)
    assert len(keys) == 32
    assert set(vl.BASELINE_BY_KEY) <= keys
    assert set(vl.BASELINE_BY_KEY.values()) <= keys
    assert set(vl.EDIT_TYPE_BY_KEY) == set(vl.BASELINE_BY_KEY)
    assert len(vl.BASELINE_BY_KEY) == 22
    for child, parent in vl.BASELINE_BY_KEY.items():
        assert child != parent, f"{child} 不许自指"
    # 链无环
    for child in vl.BASELINE_BY_KEY:
        seen = {child}
        cur = vl.BASELINE_BY_KEY[child]
        while cur in vl.BASELINE_BY_KEY:
            assert cur not in seen, f"父链成环于 {cur}"
            seen.add(cur)
            cur = vl.BASELINE_BY_KEY[cur]


@pytest.mark.unit
def test_known_parent_relations_four_edit_types():
    """四类编辑各抽钉（调窗链 / 换算子 / 加门 / 矩·估计量变换）。"""
    b = vl.BASELINE_BY_KEY
    e = vl.EDIT_TYPE_BY_KEY
    assert b["reversal_10d"] == "reversal_5d" and e["reversal_10d"] == "调窗"
    assert b["reversal_60d"] == "reversal_20d" and e["reversal_60d"] == "调窗"
    assert b["amount_burst_20d"] == "volume_spike_20d"
    assert e["amount_burst_20d"] == "换算子"
    assert (
        b["volreg_reversal_5d"] == "reversal_5d" and e["volreg_reversal_5d"] == "加门"
    )
    assert b["skew_20d"] == "max_ret_20d" and e["skew_20d"] == "矩变换"
    assert b["downside_vol_20d"] == "vol_20d"
    # 池内无 10 日无门控母本 → 不判（宁可留空，不臆造父本）
    assert "vol_kurt_gate_10d" not in b


@pytest.mark.unit
def test_coolable_set_is_16_and_disjoint_from_parents():
    coolable = vl.coolable_keys()
    protected = vl.protected_parent_keys()
    assert protected == set(vl.BASELINE_BY_KEY.values())
    assert not (coolable & protected)
    assert len(coolable) == 16
    assert "vol_kurt_gate_10d" not in coolable  # 无母本 → 不判
    assert "reversal_60d" in coolable  # 叶节点可冷却
    assert "reversal_5d" not in coolable  # 根 + 是 volreg 的父本 → 保护


# ── 台账 IO ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_ledger_self_heals(tmp_path):
    p = tmp_path / "variant_ledger.json"
    assert vl.load_ledger(p)["variants"] == {}  # 缺失 → 空骨架
    p.write_text("{not json", encoding="utf-8")
    assert vl.load_ledger(p)["variants"] == {}  # 坏 JSON → 空骨架
    p.write_text('{"variants": 5}', encoding="utf-8")
    assert vl.load_ledger(p)["variants"] == {}  # 结构不符 → 字段重置


@pytest.mark.unit
def test_save_ledger_atomic(tmp_path):
    p = tmp_path / "variant_ledger.json"
    ledger = vl.upsert_observations(
        vl.empty_ledger(), [_obs_dict("reversal_60d")], now=utc_now()
    )
    vl.save_ledger(p, ledger)

    loaded = vl.load_ledger(p)
    assert loaded["variants"]["reversal_60d"]["observations"]
    assert loaded["schema_version"] == vl.LEDGER_SCHEMA_VERSION
    assert not (tmp_path / "variant_ledger.json.tmp").exists()
    # 文件必须是合法 JSON（NaN 不许漏出）
    import json

    json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_upsert_is_idempotent_and_trims():
    ledger = vl.empty_ledger()
    first = vl.upsert_observations(
        ledger, [_obs_dict("reversal_60d", run_id="r1")], now=utc_now()
    )
    again = vl.upsert_observations(
        first,
        [_obs_dict("reversal_60d", run_id="r1")],  # 同幂等键 → 覆盖
        now=utc_now(),
    )
    assert len(again["variants"]["reversal_60d"]["observations"]) == 1
    # 入参不可变
    assert ledger["variants"] == {}
    assert len(first["variants"]["reversal_60d"]["observations"]) == 1
    # 超上限裁剪到最近 MAX 条（保留最大 window_end）
    for i in range(vl.MAX_OBSERVATIONS + 5):
        again = vl.upsert_observations(
            again,
            [
                _obs_dict(
                    "reversal_60d",
                    run_id=f"r{i}",
                    window_end=f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                )
            ],
            now=utc_now(),
        )
    obs = again["variants"]["reversal_60d"]["observations"]
    assert len(obs) == vl.MAX_OBSERVATIONS
    ends = [o["window_end"] for o in obs]
    assert ends == sorted(ends)


# ── 冷却判定 ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_high_conf_fail_cools_leaf_variant():
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60, status="ok")],
        now=utc_now(),
    )
    out, updates = vl.decide_cooling(ledger)
    cooled = vl.active_cooled_keys(out, universe="all_a", now=utc_now())
    assert cooled == {"reversal_60d"}
    assert updates and updates[0]["action"] == "cooled"
    entry = out["cooling"][0] if isinstance(out["cooling"], list) else None
    assert entry is not None
    assert entry["baseline_key"] == "reversal_20d"
    assert entry["evidence"]["residual_ic_ir"] == pytest.approx(0.1)


@pytest.mark.unit
def test_low_conf_and_unverifiable_never_cool():
    cases = [
        {"status": "ok", "ic_ir": 0.1, "n_days": 30},  # 有效日不足 40 → 不判
        {"status": "insufficient_days", "ic_ir": None, "n_days": 0},
        {"status": "unavailable", "ic_ir": None, "n_days": 0},
    ]
    ledger = vl.empty_ledger()
    for i, case in enumerate(cases):
        ledger = vl.upsert_observations(
            ledger,
            [_obs_dict("reversal_60d", run_id=f"r{i}", **case)],
            now=utc_now(),
        )
    out, updates = vl.decide_cooling(ledger)
    assert vl.active_cooled_keys(out, universe="all_a", now=utc_now()) == frozenset()
    assert updates == []


@pytest.mark.unit
def test_strong_pass_is_not_cooled_and_lifts_existing():
    """ic_ir ≥ 0.3 = 通过，绝不判失败；且新鲜强通过解除既有冷却。"""
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60, window_end="2026-01-31")],
        now=utc_now(),
    )
    ledger, _ = vl.decide_cooling(ledger)
    assert vl.active_cooled_keys(ledger, universe="all_a", now=utc_now()) == {
        "reversal_60d"
    }

    lifted = vl.upsert_observations(
        ledger,
        [_obs_dict("reversal_60d", ic_ir=0.55, n_days=60, window_end="2026-09-30")],
        now=utc_now(),
    )
    out, updates = vl.decide_cooling(lifted)
    assert vl.active_cooled_keys(out, universe="all_a", now=utc_now()) == frozenset()
    assert any(u["action"] == "lifted" for u in updates)


@pytest.mark.unit
def test_parent_keys_are_never_cooled_even_on_fail():
    """链中节点（reversal_10d 是 reversal_20d 的父本）失败也不冷却。"""
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_10d", ic_ir=0.05, n_days=80)],
        now=utc_now(),
    )
    out, updates = vl.decide_cooling(ledger)
    assert vl.active_cooled_keys(out, universe="all_a", now=utc_now()) == frozenset()
    assert updates == []


@pytest.mark.unit
def test_decide_cooling_judges_latest_per_universe():
    """判定按 (变体, 池) 键控：别池**更新**的观测不得遮蔽本池失败。"""
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [
            _obs_dict(
                "reversal_60d",
                universe="all_a",
                ic_ir=0.1,
                n_days=60,
                window_end="2024-12-31",
            ),
            _obs_dict(
                "reversal_60d",
                universe="csi300",
                ic_ir=0.5,
                n_days=60,
                window_end="2025-06-30",  # 池 B 的观测更新且通过
            ),
        ],
        now=now,
    )
    out, updates = vl.decide_cooling(ledger, now=now)

    assert vl.active_cooled_keys(out, universe="all_a", now=now) == {"reversal_60d"}, (
        "all_a 的高置信失败必须被判定——不被 csi300 更新的观测遮蔽"
    )
    assert vl.active_cooled_keys(out, universe="csi300", now=now) == frozenset()
    assert [u["universe"] for u in updates if u["action"] == "cooled"] == ["all_a"]


@pytest.mark.unit
def test_cooling_is_universe_scoped_and_ttl_expires():
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60, universe="csi300")],
        now=now,
    )
    out, _ = vl.decide_cooling(ledger, now=now)
    # 池不匹配 → 不生效（换池重评）
    assert vl.active_cooled_keys(out, universe="all_a", now=now) == frozenset()
    assert vl.active_cooled_keys(out, universe="csi300", now=now) == {"reversal_60d"}
    # TTL 到期自动失效
    later = now + timedelta(days=vl.COOLDOWN_DAYS + 1)
    assert vl.active_cooled_keys(out, universe="csi300", now=later) == frozenset()


@pytest.mark.unit
def test_judgement_uses_best_horizon_only():
    """判定只看 best_horizon 的那格（变体表现最好的周期仍无增量才算失败）。"""
    other = {
        "horizon": 1,
        "status": "ok",
        "ic_ir": 0.05,
        "ic_mean": 0.01,
        "n_days": 80,
        "basis": [],
        "n_basis": 0,
        "max_basis_abs_corr": 0.2,
    }
    # best_horizon=5 那格通过（0.5），h=1 那格失败（0.05）→ 不冷却
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [
            _obs_dict(
                "reversal_60d",
                ic_ir=0.5,
                n_days=60,
                horizon=5,
                extra_horizons=(other,),
            )
        ],
        now=utc_now(),
    )
    out, updates = vl.decide_cooling(ledger)
    assert updates == []
    assert vl.active_cooled_keys(out, universe="all_a", now=utc_now()) == frozenset()


@pytest.mark.unit
def test_decide_cooling_prunes_expired_entries():
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60)],
        now=now,
    )
    out, _ = vl.decide_cooling(ledger, now=now)
    assert out["cooling"], "前提：先有了冷却条目"
    later = now + timedelta(days=vl.COOLDOWN_DAYS + 2)
    pruned, _ = vl.decide_cooling(out, now=later)
    assert pruned["cooling"] == []


@pytest.mark.unit
def test_decide_cooling_is_idempotent_for_same_observation():
    """同一观测重复判定必须幂等：不重复动作、TTL 不随调用次数漂移。"""
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60)],
        now=now,
    )
    out1, updates1 = vl.decide_cooling(ledger, now=now)
    assert len(updates1) == 1
    out2, updates2 = vl.decide_cooling(out1, now=now + timedelta(seconds=1))
    assert updates2 == []
    assert out2["cooling"] == out1["cooling"]


@pytest.mark.unit
def test_decide_cooling_refreshes_ttl_on_repeat_fail():
    """同一 (变体, 池) 再次高置信失败：刷新 TTL 与证据，since 保留首次。"""
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", ic_ir=0.1, n_days=60)],
        now=now,
    )
    out, _ = vl.decide_cooling(ledger, now=now)
    first = out["cooling"][0]
    later = now + timedelta(days=30)
    again = vl.upsert_observations(
        out,
        [_obs_dict("reversal_60d", ic_ir=0.05, n_days=70, window_end="2026-12-31")],
        now=later,
    )
    refreshed, _ = vl.decide_cooling(again, now=later)
    entry = refreshed["cooling"][0]
    assert entry["since"] == first["since"], "since 保留首次入冷时间"
    assert entry["until"] > first["until"], "重复失败刷新 TTL"
    assert entry["evidence"]["residual_ic_ir"] == pytest.approx(0.05)


@pytest.mark.unit
def test_variant_skeleton_covers_registry():
    rows = vl.variant_skeleton()
    assert len(rows) == 32
    by_key = {r["key"]: r for r in rows}
    assert by_key["reversal_60d"]["baseline_key"] == "reversal_20d"
    assert by_key["reversal_60d"]["edit_type"] == "调窗"
    assert by_key["reversal_60d"]["coolable"] is True
    assert by_key["reversal_5d"]["baseline_key"] is None
    assert by_key["reversal_5d"]["coolable"] is False
    assert by_key["vol_kurt_gate_10d"]["baseline_key"] is None
    # 保序 = 注册表顺序（UI 表格稳定）
    assert [r["key"] for r in rows] == [c.key for c in cand.CANDIDATES]


# ── 展示投影 ledger_view ──────────────────────────────────────────────


@pytest.mark.unit
def test_ledger_view_pool_scoped_and_verdict_only_with_pool():
    """观测与冷却都按池过滤；池不明时 cooling_active=None（不臆造「生效」）。"""
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [
            _obs_dict("reversal_60d", universe="all_a", ic_ir=0.1, n_days=60),
            _obs_dict("reversal_60d", universe="csi300", ic_ir=0.5, n_days=60),
        ],
        now=now,
    )
    ledger, _ = vl.decide_cooling(ledger, now=now)

    view = vl.ledger_view(ledger, universe="all_a", now=now)
    assert [r["key"] for r in view["variants"]] == [c.key for c in cand.CANDIDATES]
    row = next(r for r in view["variants"] if r["key"] == "reversal_60d")
    assert row["observations_count"] == 1, "只看本池观测（跨池不可比）"
    assert row["latest"]["universe"] == "all_a"
    assert row["cooling_active"] is True
    assert view["cooling_active"] == ["reversal_60d"]
    assert view["constants"]["cool_icir_floor"] == vl.COOL_ICIR_FLOOR

    other = vl.ledger_view(ledger, universe="csi300", now=now)
    other_row = next(r for r in other["variants"] if r["key"] == "reversal_60d")
    assert other_row["cooling_active"] is False, "csi300 那格是强通过 → 不冷却"
    assert other["cooling_active"] == []

    full = vl.ledger_view(ledger, now=now)
    full_row = next(r for r in full["variants"] if r["key"] == "reversal_60d")
    assert full_row["observations_count"] == 2, "不给池 = 全量原样"
    assert full_row["cooling_active"] is None
    assert full["cooling_active"] is None


@pytest.mark.unit
def test_ledger_view_empty_rows_are_none_not_zero():
    """无观测行 latest/latest_stat=None（前端落「—」），绝不显 0。"""
    view = vl.ledger_view(vl.empty_ledger(), universe="all_a", now=utc_now())
    assert len(view["variants"]) == 32
    for row in view["variants"]:
        assert row["latest"] is None
        assert row["latest_stat"] is None
        assert row["observations_count"] == 0
        assert row["cooling"] == []
        assert row["cooling_active"] is False


@pytest.mark.unit
def test_ledger_view_redacts_run_id_everywhere():
    """展示投影零句柄（评审 security-F2）：观测与冷却判据（含 evidence 子
    字典）两层都不回传 ``run_id``；磁盘台账（内存源对象）保留全量供运维直查。"""
    now = utc_now()
    ledger = vl.upsert_observations(
        vl.empty_ledger(),
        [_obs_dict("reversal_60d", run_id="run-secret-hx", ic_ir=0.1, n_days=60)],
        now=now,
    )
    ledger, _ = vl.decide_cooling(ledger, now=now)

    view = vl.ledger_view(ledger, universe="all_a", now=now)
    row = next(r for r in view["variants"] if r["key"] == "reversal_60d")
    assert "run_id" not in row["latest"], "观测层脱敏"
    assert row["latest"]["window_end"] == "2026-09-30", "其余字段原样"
    assert row["observations_count"] == 1, "过滤/排序仍按全量字段"
    assert view["cooling"], "本用例应真有冷却条目"
    for entry in view["cooling"]:
        assert "run_id" not in entry
        assert "run_id" not in entry["evidence"], "判据子字典同脱敏"
    assert row["cooling"], "行内冷却投影同源"
    assert "run_id" not in row["cooling"][0]["evidence"]
    assert "run-secret-hx" not in json.dumps(view), "整棵投影零句柄"
    # 磁盘侧不脱敏：源台账对象仍保留 run_id（运维直查不受影响）
    entry = ledger["variants"]["reversal_60d"]["observations"][0]
    assert entry["run_id"] == "run-secret-hx"
