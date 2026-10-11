"""T-FM-04 LLM 侧经验记忆/禁区：归一、饱和聚合、渲染与注入文件。

全部为纯函数/文件面测试（不碰 DB、不碰真实 /data）：

契约：
- 归一映射钉附录 B 词表（门禁结构化 key 优先，原生串子串兜底，未知 → other
  绝不静默丢弃）；
- 饱和聚合含 ``(未归因)`` 桶（对不上方向如实展示，不硬编造映射）；
- dup_hits_90d 按注入时刻的 90 日窗；materialized_count 随方向；
- 渲染段以「【历史经验·禁区】」开头、含纪律句、超 top-8 标注「已截断」、
  总行数 ≤ 60；
- 空源 → ``available=False`` 最小段；
- ``prepare_injection``：开关关闭/组装失败/全空 → None 且不落文件；成功 →
  原子写 ``experience_context.md``（无 .tmp 残留）；
- 纯派生：build_memory 不改任何输入。
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import experience_memory as em  # noqa: E402

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


# ── fixtures ───────────────────────────────────────────────────────────


def _manifest() -> dict:
    return {
        "fid_dup_a": {
            "status": "rejected_duplicate",
            "column": "rd_reversal_10d",
            "name": "Reversal_10d",
            "corr": 0.9351,
            "corr_against": "a158_SUMN10",
            "at": "2026-09-28T14:18:51Z",
        },
        "fid_dup_b": {
            "status": "rejected_duplicate",
            "column": "rd_momentum_5d",
            "name": "Momentum_5d",
            "corr": 0.913,
            "corr_against": "a158_SUMN10",
            "at": "2026-09-01T00:00:00Z",
        },
        "fid_dup_old": {
            "status": "rejected_duplicate",
            "column": "rd_vol_change_5d",
            "corr": 0.999,
            "corr_against": "gtja_080",
            "at": "2026-06-01T00:00:00Z",  # >90 日 → 只计总数不计 90 日
        },
        "fid_gate": {
            "status": "rejected_gate",
            "column": "rd_pv_corr",
            "at": "2026-09-30T00:00:00Z",
            "gates": {
                "rejected": True,
                "gates": [
                    {
                        "key": "pfs_floor",
                        "label": "PFS 下限",
                        "mode": "hard",
                        "status": "fail",
                        "message": "PFS=0.5100 ≤ 0.9000（扰动保真度不足）",
                    },
                    {
                        "key": "rre_floor",
                        "label": "RRE 下限",
                        "mode": "soft",
                        "status": "fail",
                        "message": "RRE=0.4000 ≤ 0.5000（排序结构日间漂移过大）",
                    },
                    {
                        "key": "turnover_cap",
                        "label": "年化换手上限",
                        "mode": "soft",
                        "status": "pass",
                        "message": "年化换手=12.0000 ≤ 60.0000",
                    },
                ],
            },
        },
        "fid_orphan": {
            "status": "rejected_duplicate",
            "column": "rd_orphan_col",
            "corr": 0.7,
            "corr_against": "a158_KLEN",
            "at": "2026-09-29T00:00:00Z",
        },
        "fid_ok": {
            "status": "materialized",
            "column": "rd_avgamplitude_5d",
            "at": "2026-09-20T00:00:00Z",
        },
    }


FACTOR_TASK_IDS = {
    "fid_dup_a": "task1",
    "fid_dup_b": "task1",
    "fid_dup_old": "task1",
    "fid_gate": "task2",
    "fid_ok": "task1",
    # fid_orphan 故意缺席 → (未归因)
}

TASK_DIRECTIONS = {"task1": "尾盘资金流强度", "task2": "波动率结构挖掘"}

TASKS = [
    {
        "task_id": "task1",
        "direction": "尾盘资金流强度",
        "status": "completed",
        "factor_count": 12,
    },
    {
        "task_id": "task2",
        "direction": "波动率结构挖掘",
        "status": "cancelled",
        "factor_count": 0,
    },
    {"task_id": "task3", "direction": "", "status": "completed", "factor_count": 4},
]


def _report() -> dict:
    return {
        "task": "T-FM-01",
        "generated_at": "2026-10-11T01:27:30Z",
        "paper": {
            "dedup": {
                "dropped": [
                    ["fm029", "fm028", -0.66],
                    ["reversal_60d", "parkinson_20d", -1.0],
                ]
            },
            "factors": [
                {
                    "key": "fm029",
                    "reject_reason": "与 fm028 高度相关（ρ=-0.66，阈值 0.5）",
                },
                {
                    "key": "fm030",
                    "reject_reason": "多重检验未过（BH-FDR）；折间符号不稳（2/4 折与全窗同号）",
                },
                {"key": "fm031", "reject_reason": "未入选 Top-K（按选择分排序）"},
                {"key": "fm032", "reject_reason": ""},
            ],
        },
        "house": {"dedup": {"dropped": []}, "factors": []},
    }


def _memory(**over):
    kwargs = {
        "market": "a_share",
        "tasks": TASKS,
        "manifest": _manifest(),
        "factor_task_ids": FACTOR_TASK_IDS,
        "task_directions": TASK_DIRECTIONS,
        "now": NOW,
    }
    kwargs.update(over)
    return em.build_memory(**kwargs)


def _bucket(memory, name):
    hits = [b for b in memory.reason_buckets if b.bucket == name]
    assert hits, f"bucket {name!r} 缺席：{[b.bucket for b in memory.reason_buckets]}"
    return hits[0]


def _saturated(memory, direction):
    hits = [s for s in memory.saturated_directions if s.direction == direction]
    return hits[0] if hits else None


# ── 归一映射钉串（附录 B）─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "gate_key", "expected"),
    [
        ("PFS=0.51 ≤ 0.9（扰动保真度不足）", None, "gate:fidelity"),
        ("RRE=0.4 ≤ 0.5（排序结构日间漂移过大）", None, "gate:rank_drift"),
        ("IC 池内分位=0.10 ≤ 0.30（池内相对位置过靠后）", None, "gate:pool_pct"),
        ("年化换手=80 > 60（换手过高、成本侵蚀严重）", None, "gate:turnover"),
        ("扣费年化=-0.01 ≤ 0（扣成本后无收益）", None, "gate:net_return"),
        ("增量残差 IC_IR=0.05（对库冗余、独立信息不足）", None, "gate:incremental"),
        ("", "pfs_floor", "gate:fidelity"),
        ("", "incremental_ic_ir_floor", "gate:incremental"),
        ("与 fm006 高度相关（ρ=0.53，阈值 0.5）", None, "stat:dedup"),
        ("多重检验未过（BH-FDR）", None, "stat:fdr"),
        ("折间符号不稳（2/4 折与全窗同号）", None, "stat:fold"),
        ("FactorEmptyError: 分位数全空", None, "empty_factor"),
        ("CoderError: 1/3 场景失败", None, "coding_error"),
        # 未映射的筛选结论不静默丢弃 → other（非“拒因”，如实归类）
        ("未入选 Top-K（按选择分排序）", None, "other"),
        ("", None, "other"),
    ],
)
def test_normalize_bucket_pinned(raw, gate_key, expected):
    assert em.normalize_bucket(raw, gate_key=gate_key) == expected


def test_every_bucket_has_label_and_vice_versa():
    assert set(em.BUCKET_LABELS) == {
        "dup_library",
        "gate:fidelity",
        "gate:rank_drift",
        "gate:pool_pct",
        "gate:turnover",
        "gate:net_return",
        "gate:incremental",
        "stat:dedup",
        "stat:fdr",
        "stat:fold",
        "empty_factor",
        "coding_error",
        "other",
    }
    for name, label in em.BUCKET_LABELS.items():
        assert label.strip(), name


def test_structured_gate_key_wins_over_message():
    # message 串提到「换手」但门禁是 pfs_floor：结构化 key 优先，不误归换手桶
    assert (
        em.normalize_bucket("PFS 不足（对比换手）", gate_key="pfs_floor")
        == "gate:fidelity"
    )


# ── 拒因聚类 ───────────────────────────────────────────────────────────


def test_buckets_from_manifest_and_report():
    memory = _memory(report=_report(), report_path="/refs/T-FM-01_x.json")

    dup = _bucket(memory, "dup_library")
    # 4 条 manifest 查重全计数（a/b/old 归尾盘 + orphan 未归因——未归因只丢
    # 方向样例、不丢现象计数；与 test_sample_caps 全未归因仍计 6 条同口径）
    assert dup.count == 4
    assert dup.last_at == "2026-09-29T00:00:00Z"
    # 样例列 cap=3、按 fid 序：orphan 的 rd_orphan_col 被 cap 挡在样例之外
    assert set(dup.sample_columns) == {
        "rd_reversal_10d",
        "rd_momentum_5d",
        "rd_vol_change_5d",
    }
    assert "尾盘资金流强度" in dup.sample_dirs
    assert dup.sources == ("manifest",)

    # 只有 hard+fail 的门禁算拒因；soft fail 的 rre 不计
    fid = _bucket(memory, "gate:fidelity")
    assert fid.count == 1
    assert fid.last_at == "2026-09-30T00:00:00Z"
    assert fid.sample_columns == ("rd_pv_corr",)
    assert fid.sample_dirs == ("波动率结构挖掘",)
    assert not [b for b in memory.reason_buckets if b.bucket == "gate:rank_drift"]

    # 报告侧：fm030 一段串拆出两个桶
    assert _bucket(memory, "stat:fdr").count == 1
    assert _bucket(memory, "stat:fold").count == 1
    assert _bucket(memory, "stat:dedup").count == 1
    assert _bucket(memory, "stat:dedup").sources == ("report",)
    assert _bucket(memory, "stat:dedup").last_at == "2026-10-11T01:27:30Z"
    assert _bucket(memory, "other").count == 1

    # sources.report 只回文件名（评审 security-F5：绝对路径不出网）；
    # 展示面只做真值判断，不需要宿主目录结构
    assert memory.sources["report"] == "T-FM-01_x.json"


def test_buckets_sorted_by_count_desc():
    memory = _memory(report=_report())
    counts = [b.count for b in memory.reason_buckets]
    assert counts == sorted(counts, reverse=True)


def test_sample_caps():
    manifest = {}
    for i in range(6):
        manifest[f"f{i}"] = {
            "status": "rejected_duplicate",
            "column": f"rd_c{i}",
            "at": "2026-09-01T00:00:00Z",
        }
    memory = _memory(manifest=manifest, factor_task_ids={}, task_directions={})
    dup = _bucket(memory, "dup_library")
    assert dup.count == 6
    assert len(dup.sample_columns) == em.SAMPLE_CAP
    assert len(dup.sample_dirs) <= em.SAMPLE_CAP


# ── 饱和方向（含未归因）────────────────────────────────────────────────


def test_saturation_aggregation_with_unattributed():
    memory = _memory(report=_report(), report_path="/refs/T-FM-01_x.json")

    tail = _saturated(memory, "尾盘资金流强度")
    assert tail is not None
    assert tail.dup_hits == 3
    assert tail.dup_hits_90d == 2  # fid_dup_old 在 90 日窗外
    assert tail.top_opponent == "a158_SUMN10"
    assert tail.materialized_count == 1

    # 无查重命中的方向不进饱和列表（materialized 单独不构成饱和）
    assert _saturated(memory, "波动率结构挖掘") is None

    # 对不上方向的命中（孤儿因子 + 报告静态因子）如实进 (未归因)
    orphan = _saturated(memory, em.UNATTRIBUTED)
    assert orphan is not None
    assert orphan.dup_hits == 3  # fid_orphan + fm029 + reversal_60d
    assert orphan.dup_hits_90d == 1
    assert orphan.materialized_count == 0


def test_saturation_sorted_and_top_opponent_tiebreak():
    memory = _memory()
    hits = [s.dup_hits for s in memory.saturated_directions]
    assert hits == sorted(hits, reverse=True)


def test_90d_window_boundary():
    manifest = {
        "fid_edge": {
            "status": "rejected_duplicate",
            "column": "rd_x",
            "corr_against": "a",
            "at": "2026-07-13T12:00:00Z",  # 恰好 90 天前 → 计入
        },
        "fid_out": {
            "status": "rejected_duplicate",
            "column": "rd_y",
            "corr_against": "a",
            "at": "2026-07-13T11:59:00Z",  # 早一分钟 → 不计
        },
    }
    memory = _memory(
        manifest=manifest,
        factor_task_ids={"fid_edge": "task1", "fid_out": "task1"},
    )
    row = _saturated(memory, "尾盘资金流强度")
    assert row.dup_hits == 2
    assert row.dup_hits_90d == 1


def test_bad_at_not_counted_in_window():
    manifest = {
        "fid_bad": {
            "status": "rejected_duplicate",
            "column": "rd_x",
            "at": "not-a-timestamp",
        }
    }
    memory = _memory(manifest=manifest, factor_task_ids={}, task_directions={})
    row = _saturated(memory, em.UNATTRIBUTED)
    assert row.dup_hits == 1
    assert row.dup_hits_90d == 0


# ── 方向任务史 ─────────────────────────────────────────────────────────


def test_direction_stats():
    memory = _memory()
    stats = {d.direction: d for d in memory.direction_stats}
    assert stats["尾盘资金流强度"].tasks == 1
    assert stats["尾盘资金流强度"].completed == 1
    assert stats["尾盘资金流强度"].factors == 12
    assert stats["波动率结构挖掘"].completed == 0
    # 空方向不再靠空串上屏
    assert em.NO_DIRECTION in stats
    assert stats[em.NO_DIRECTION].factors == 4


def test_direction_label_truncated():
    long_dir = "第一行假设\n第二行依据" + "很长" * 60
    memory = _memory(
        tasks=[
            {
                "task_id": "t",
                "direction": long_dir,
                "status": "completed",
                "factor_count": 1,
            }
        ],
        manifest={},
        factor_task_ids={},
        task_directions={},
    )
    stat = memory.direction_stats[0]
    assert "\n" not in stat.label
    assert stat.label.startswith("第一行假设")
    assert len(stat.label) <= em.LABEL_MAX_CHARS + 1
    assert stat.direction == long_dir  # 原始值不被改写


def test_bucket_sample_dirs_are_short_labels():
    """真数据方向是整段多行假设——样例方向必须过短标签（无换行、超长截断），

    否则渲染段被内嵌换行撕碎、行数上限失真（真数据 smoke 抓到过）。
    原始方向值只在 direction_stats.direction / 饱和行的 direction 字段可查。
    """
    long_dir = "第一行假设\n第二行依据" + "很长" * 60
    memory = _memory(
        task_directions={"task1": long_dir, "task2": "波动率结构挖掘"},
    )
    dup = _bucket(memory, "dup_library")
    assert dup.sample_dirs, "样例方向不该丢"
    assert all("\n" not in d for d in dup.sample_dirs)
    assert all(len(d) <= em.LABEL_MAX_CHARS + 1 for d in dup.sample_dirs)
    assert dup.sample_dirs[0].startswith("第一行假设")

    # 渲染段：任何一行都不携带内嵌换行（splitlines 行数 = 段内逻辑行数）
    text = em.render_block(memory)
    assert all(len(line) < 400 for line in text.splitlines())
    assert len(text.splitlines()) <= em.RENDER_MAX_LINES


# ── 报告目录解析（host / 容器双路径）─────────────────────────────────


def test_ref_dir_prefers_first_usable_candidate(tmp_path, monkeypatch):
    """候选序即优先级：首个「存在且非空」的候选胜出（空目录不作数）。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    good = tmp_path / "good"
    good.mkdir()
    (good / "T-FM-01_factor_test_x.json").write_text("{}")
    monkeypatch.setattr(em, "_candidate_ref_dirs", lambda: (empty, good))
    assert em._refs_dir() == good


def test_ref_dir_falls_back_to_last_candidate(tmp_path, monkeypatch):
    """全候选落空 → 返回末候选（仓库根口径），报错文案可定位。"""
    last = tmp_path / "host-root" / "data" / "refs" / "factorminer"
    monkeypatch.setattr(em, "_candidate_ref_dirs", lambda: (tmp_path / "nope", last))
    assert em._refs_dir() == last


# ── 渲染 ───────────────────────────────────────────────────────────────


def test_render_block_shape_and_discipline():
    memory = _memory(report=_report())
    text = em.render_block(memory)
    lines = text.splitlines()
    assert lines[0] == em.RENDER_TITLE
    assert len(lines) <= em.RENDER_MAX_LINES
    assert em.DISCIPLINE_LINE in lines
    assert "尾盘资金流强度" in text
    assert "扰动保真度不足" in text
    assert "已截断" not in text  # top-8 装得下，不虚标截断


def test_render_truncates_with_annotation():
    manifest = {}
    for i in range(12):
        manifest[f"f{i}"] = {
            "status": "rejected_duplicate",
            "column": f"rd_c{i}",
            "at": "2026-09-01T00:00:00Z",
            "corr_against": f"opp{i}",
        }
    dirs = {f"d{i:02d}": f"方向{i}" for i in range(11)}
    factor_task_ids = {f"f{i}": f"d{i:02d}" for i in range(11)}  # 11 个方向 → 超 top-8
    memory = _memory(
        manifest=manifest, factor_task_ids=factor_task_ids, task_directions=dirs
    )
    text = em.render_block(memory)
    assert len(text.splitlines()) <= em.RENDER_MAX_LINES
    assert "已截断" in text


def test_render_line_cap_is_hard():
    text = em.render_block(_memory(report=_report()), max_lines=8)
    lines = text.splitlines()
    assert len(lines) <= 8
    assert lines[-1].startswith("（已截断")


def test_empty_memory_minimal_block():
    memory = em.build_memory(market="a_share", now=NOW)
    assert memory.available is False
    text = em.render_block(memory)
    assert text.splitlines()[0] == em.RENDER_TITLE
    assert "暂无" in text
    assert len(text.splitlines()) <= em.RENDER_MAX_LINES


def test_available_when_any_source_present():
    assert _memory().available is True
    assert em.build_memory(market="a_share", manifest=_manifest(), now=NOW).available
    assert em.build_memory(market="a_share", tasks=TASKS, now=NOW).available
    assert em.build_memory(market="a_share", report=_report(), now=NOW).available


# ── 纯派生：不改输入 ───────────────────────────────────────────────────


def test_build_memory_does_not_mutate_inputs():
    manifest = _manifest()
    report = _report()
    m_snapshot = copy.deepcopy(manifest)
    r_snapshot = copy.deepcopy(report)
    _memory(manifest=manifest, report=report)
    assert manifest == m_snapshot
    assert report == r_snapshot


def test_to_dict_round_trip_json():
    memory = _memory(report=_report())
    data = memory.to_dict()
    assert data["available"] is True
    assert data["market"] == "a_share"
    assert data["generated_at"] == "2026-10-11T12:00:00Z"
    assert {
        "reason_buckets",
        "saturated_directions",
        "direction_stats",
        "sources",
    } <= set(data)
    json.dumps(data)  # 必须可 JSON 序列化（API 直出）


# ── prepare_injection（注入文件面）────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_injection_flag_off(monkeypatch, tmp_path):
    monkeypatch.setenv(em.ENV_FLAG, "false")
    assert (
        await em.prepare_injection(
            user_id="u1", market="a_share", task_id="t1", log_dir=tmp_path
        )
        is None
    )
    assert not (tmp_path / em.CONTEXT_FILENAME).exists()


@pytest.mark.asyncio
async def test_prepare_injection_writes_atomically(monkeypatch, tmp_path):
    monkeypatch.delenv(em.ENV_FLAG, raising=False)
    memory = _memory(report=_report())

    async def fake_load(**_kwargs):
        return memory

    monkeypatch.setattr(em, "load_memory", fake_load)
    path = await em.prepare_injection(
        user_id="u1", market="a_share", task_id="t1", log_dir=tmp_path
    )
    assert path == tmp_path / em.CONTEXT_FILENAME
    content = path.read_text(encoding="utf-8")
    assert content.splitlines()[0] == em.RENDER_TITLE
    assert em.DISCIPLINE_LINE in content
    # 原子写：不留 .tmp 尾巴
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_prepare_injection_empty_memory_skips(monkeypatch, tmp_path):
    monkeypatch.delenv(em.ENV_FLAG, raising=False)

    async def fake_load(**_kwargs):
        return em.build_memory(market="a_share", now=NOW)

    monkeypatch.setattr(em, "load_memory", fake_load)
    assert (
        await em.prepare_injection(
            user_id="u1", market="a_share", task_id="t1", log_dir=tmp_path
        )
        is None
    )
    assert not (tmp_path / em.CONTEXT_FILENAME).exists()


@pytest.mark.asyncio
async def test_prepare_injection_failure_only_warns(monkeypatch, tmp_path):
    monkeypatch.delenv(em.ENV_FLAG, raising=False)

    async def boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(em, "load_memory", boom)
    assert (
        await em.prepare_injection(
            user_id="u1", market="a_share", task_id="t1", log_dir=tmp_path
        )
        is None
    )
    assert not (tmp_path / em.CONTEXT_FILENAME).exists()
