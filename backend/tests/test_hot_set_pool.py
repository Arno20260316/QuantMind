"""当日候选池进热集（T-P6-06 补，2026-10-13）：池源采集 + 优先级 + 分片压力。

**为什么单开一份**：池票是**决策轮当天真的要下单**的那份名单（
``data/reports/stock_picks/{日}_picks.json``），而热集原先只由 PG ``engine_signal_scores``
的分数 TopN 供票——两份名单**不相交**。池票不在热集 ⇒ 没有行情快照 ⇒ ``l3.stale_quote``
（5s，fail-closed）把当天那一批买单**全数拒掉**（2026-10-12 实测：池 30 只，15 只不在
热集 562；决策轮 22/22 全拒）。

盯四件事：

1. ``compose_hot_set(pool=…)`` 的优先级：池只让位于持仓，**不得**被候选挤出截断；
2. ``HotSetBuilder._collect_pool``：真池文件 → 后缀式清单；文件不在 / 开关关 → 空且**无错**；
   读失败 → 空 + 如实告警（三类结果必须可区分，否则「今天还没出池」与「池读挂了」在
   报告里长一样）；
3. ``_shard_pressure`` 与订阅侧 ``collector.shard_symbols`` **同一分片算法**（余量必须等于
   订阅侧真正会撞的数，否则运维照着一个假余量调分片）；
4. I：``build_once`` 全链——当天池票必须**全部**落到输出键里。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]


def _pool_doc(rows: list[str], source: str = "/tmp/picks.json"):
    """``PoolDoc`` 替身（只带 ``_collect_pool`` 真正读的字段：``rows[].code`` / ``source``）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        rows=tuple(SimpleNamespace(code=code) for code in rows),
        source=source,
    )


# ── 1. 合成优先级 ───────────────────────────────────────────────────


@pytest.mark.unit
def test_pool_outranks_candidates_under_truncation():
    """池票在截断中**先于候选**活下来：池是今天要下单的，候选只是分数高的。"""
    from backend.shared.hot_set import compose_hot_set

    positions = ["600036.SH"]
    pool = ["300750.SZ", "601318.SH"]
    candidates = [f"00000{i}.SZ" for i in range(10)]
    out = compose_hot_set(
        positions=positions,
        pool=pool,
        anomalies=[],
        candidates=candidates,
        cap=3,  # 只装得下 持仓 + 2 只池票
    )
    assert out["symbols"] == ["600036.SH", "300750.SZ", "601318.SH"]
    assert all(c not in out["symbols"] for c in candidates), "候选不得挤掉池票"
    assert out["stats"]["truncated"] == 10


@pytest.mark.unit
def test_pool_ranks_after_positions_and_dedupes():
    """持仓 > 池：同一票既在持仓又在池里时只留一份，且归一到后缀式。"""
    from backend.shared.hot_set import compose_hot_set

    out = compose_hot_set(
        positions=["SH600036"],
        pool=["600036", "300750.SZ"],  # 裸码与持仓重复
        anomalies=[],
        candidates=[],
        cap=10,
    )
    assert out["symbols"] == ["600036.SH", "300750.SZ"]
    assert out["stats"]["unique"] == 2


@pytest.mark.unit
def test_pool_defaults_to_empty_so_existing_callers_see_same_stats():
    """``pool`` 默认空元组：既有调用点的统计口径不得因新增源而漂移。"""
    from backend.shared.hot_set import compose_hot_set

    without = compose_hot_set(
        positions=["600036.SH"], anomalies=["601318"], candidates=["600519"], cap=10
    )
    explicit_empty = compose_hot_set(
        positions=["600036.SH"],
        pool=[],
        anomalies=["601318"],
        candidates=["600519"],
        cap=10,
    )
    assert without == explicit_empty


# ── 2. 池源采集 ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_collect_pool_reads_the_days_file(monkeypatch):
    """正常路径：``load_pool_doc`` 的出口已是后缀式，原样带回并记下出处。"""
    import backend.shared.decision_context_source as dcs
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    monkeypatch.setattr(
        dcs,
        "load_pool_doc",
        lambda day, **kw: _pool_doc(
            ["603357.SH", "300750.SZ"], source="/x/20261013_picks.json"
        ),
    )
    builder = HotSetBuilder(hot_set_key="qm:hot_set:test-unit")

    symbols, err, src = builder._collect_pool("20261013")

    assert symbols == ["603357.SH", "300750.SZ"]
    assert err is None
    assert src == "/x/20261013_picks.json"


@pytest.mark.unit
def test_collect_pool_missing_file_is_not_an_error(monkeypatch):
    """凌晨未产出（北京 ~03:00/04:00 才有池）**不是错误**：空池、无错、无出处。"""
    import backend.shared.decision_context_source as dcs
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    monkeypatch.setattr(dcs, "load_pool_doc", lambda day, **kw: None)
    builder = HotSetBuilder(hot_set_key="qm:hot_set:test-unit")

    assert builder._collect_pool("20261013") == ([], None, "")


@pytest.mark.unit
def test_collect_pool_failure_is_reported_with_reason(monkeypatch):
    """读失败必须与「没出池」区分开：报告里要能看出是**读挂了**。"""
    import backend.shared.decision_context_source as dcs
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    def boom(day, **kw):
        raise OSError("permission denied")

    monkeypatch.setattr(dcs, "load_pool_doc", boom)
    builder = HotSetBuilder(hot_set_key="qm:hot_set:test-unit")

    symbols, err, src = builder._collect_pool("20261013")
    assert symbols == []
    assert err is not None and "候选池采集失败" in err
    assert src == ""


@pytest.mark.unit
def test_collect_pool_can_be_switched_off(monkeypatch):
    """``QM_HOT_SET_POOL`` 关掉即完全退回旧行为（词表只认小写 true 的反面）。"""
    import backend.shared.decision_context_source as dcs
    from backend.services.live_trading.services.hot_set_builder import (
        POOL_FEATURE_ENV,
        HotSetBuilder,
    )

    called: list[int] = []

    def spy(day, **kw):
        called.append(1)
        return _pool_doc(["603357.SH"])

    monkeypatch.setattr(dcs, "load_pool_doc", spy)
    monkeypatch.setenv(POOL_FEATURE_ENV, "false")
    builder = HotSetBuilder(hot_set_key="qm:hot_set:test-unit")

    assert builder._collect_pool("20261013") == ([], None, "")
    assert not called, "关掉开关就不该去读盘"


@pytest.mark.unit
def test_collect_pool_defaults_to_today_in_beijing(monkeypatch):
    """不传日期 → 读**北京时间今天**的池（与决策轮同一日界，不是 UTC 日）。"""
    import backend.shared.decision_context_source as dcs
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    seen: list[str] = []

    def spy(day, **kw):
        seen.append(str(day))
        return None

    monkeypatch.setattr(dcs, "load_pool_doc", spy)
    builder = HotSetBuilder(hot_set_key="qm:hot_set:test-unit")
    builder._collect_pool()

    assert seen == [dcs.now_cn().strftime("%Y%m%d")]


# ── 3. 分片压力 ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_shard_pressure_matches_the_subscription_side_algorithm():
    """余量必须等于订阅侧真正会撞的数：两处用**同一个** crc32 分片函数。"""
    from backend.shared.tdx_aidata.collector import shard_symbols
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    symbols = [f"{600000 + i}.SH" for i in range(200)]
    shard_count = 3

    pressure = HotSetBuilder._shard_pressure(symbols, shard_count)

    per_shard = [
        len(shard_symbols(symbols, sid, shard_count)) for sid in range(shard_count)
    ]
    assert pressure["shards"] == per_shard
    assert pressure["shard_max"] == max(per_shard)
    assert pressure["shard_count"] == shard_count
    assert pressure["headroom"] == pressure["sdk_cap"] - max(per_shard)
    assert pressure["sdk_cap"] == 100


@pytest.mark.unit
def test_shard_pressure_flags_exhausted_headroom():
    """单片超 100 时余量 ≤ 0 —— 订阅侧会按字典序硬截断（可能正好截掉池票）。"""
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    # 单片 150 只：字典序靠后的是 ``601xxx``，池票若落在那一侧就订不到
    symbols = [f"{600000 + i}.SH" for i in range(150)]
    pressure = HotSetBuilder._shard_pressure(symbols, shard_count=1)

    assert pressure["shard_max"] == 150
    assert pressure["headroom"] <= 0


@pytest.mark.unit
def test_shard_pressure_single_shard_fallback():
    """分片数读不到时按单片估（余量只会更保守，不会假装宽裕）。"""
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    pressure = HotSetBuilder._shard_pressure(["600036.SH"], shard_count=0)
    assert pressure["shard_count"] == 1
    assert pressure["shard_max"] == 1


# ── 4. 全链：build_once 必须装下当天池票 ───────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_build_once_includes_the_whole_pool(monkeypatch):
    """I：真盘池文件 → ``build_once`` → 输出键含**当天池票每一只**。

    这是本升级的验收面：池票不在热集 ⇒ 5s 陈旧价闸门全拒 ⇒ 当天买单 22/22 全拒。
    用**真池文件**（容器内 ``data/reports/stock_picks/20261012_picks.json``）而不是
    伪造夹具，才能证明「决策轮读的那份」与「热集订的那份」是同一份。
    """
    import backend.shared.decision_context_source as dcs
    from backend.shared.database_manager_v2 import close_database
    from backend.services.live_trading.services.hot_set_builder import HotSetBuilder

    day = "20261012"
    doc = dcs.load_pool_doc(day)
    if doc is None:
        pytest.skip(f"盘上没有 {day} 的池文件（本机无该产物），跳过全链用例")
    expected = {str(r.code) for r in doc.rows}
    assert expected, "池文件在但没有行——夹具/解析出问题"

    hot_key = f"qm:hot_set:test-{uuid.uuid4().hex[:6]}"
    from backend.shared.hot_set_store import make_hot_set_client

    hs = make_hot_set_client()
    try:

        async def loader():
            return {}, {}

        builder = HotSetBuilder(
            hot_set_key=hot_key,
            cap=5000,
            real_positions_loader=loader,
        )
        builder._collect_anomalies = lambda: ([], None)
        # 只**钉住日期**（真实实现原样走 load_pool_doc），不替身掉采集逻辑本身——
        # 替身掉就测不到「决策轮读的那份文件」这条链了
        real_collect = builder._collect_pool
        builder._collect_pool = lambda *a, **k: real_collect(day)

        report = await builder.build_once(tenant_filter=None)

        members = hs.smembers(hot_key)
        missing = expected - set(members)
        assert not missing, (
            f"池票未进热集（这正是 22/22 拒单的根因）: {sorted(missing)}"
        )
        assert report["sources"]["pool"] == len(expected)
        assert report["pool_file"], "池的出处必须如实带上（区分「没池」与「池读挂」）"
    finally:
        hs.delete(hot_key, f"{hot_key}:meta")
        hs.close()
        await close_database()


# ── 5. 源守卫 ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_pool_and_decision_round_read_the_same_loader():
    """单一事实源守卫：热集池源必须走 ``decision_context_source.load_pool_doc``。

    另起一套「读 picks.json」的实现 = 两份解析器（两代 schema 的兼容逻辑也会分叉），
    池文件换 schema 时只有一条链会静默读成空池。
    """
    src = (_BACKEND / "services/live_trading/services/hot_set_builder.py").read_text(
        encoding="utf-8"
    )
    assert "decision_context_source import load_pool_doc" in src
