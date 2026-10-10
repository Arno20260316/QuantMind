"""挖掘配置的 API 面契约（移植 P1）：请求归一 / 覆盖校验 / 预览端点。

钉死三件事：

1. **请求归一**（``_normalize_request_mining_config``）：没动过挖掘配置的任务
   行不留参数痕迹（全 None）；选了就按登记序归一（缓存身份稳定）；非 A 股
   市场带挖掘配置即拒（不许静默忽略）；覆盖越界即拒。
2. **数据覆盖**（``_quantdb_coverage``）：与挖掘数据面同源（daily_forward
   分区），带 TTL 缓存、读不到回 None（不拦请求）。
3. **预览端点**（GET /mining/config-options）：默认窗口/分段与提交路径同一套
   纯函数；非法组合 400；``validation=None`` = 不启用样本外检验。
4. **回测末端保护**（``_backtest_guard``）：窗口终点一律退让到「数据末端前一个
   交易日」（qlib 回测按 t+1 成交需要次日行情，压在最后一根 bar 上必崩）。
   任务行与预览存/显示的都是退让后的端点；覆盖读不到也照样保护。

依赖 fastapi/DB（RDAgentFactorPersistence 在导入期构造），本地轻量环境整体
跳过；在 OSS 容器内跑：
``python -m pytest backend/tests/test_mining_config_api.py -q``
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

try:  # pragma: no cover - 环境相关
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.services.engine.routers import alpha_agent as aa
except Exception:  # noqa: BLE001
    aa = None

pytestmark = pytest.mark.skipif(
    aa is None, reason="alpha_agent 依赖不可用（需容器环境）"
)

_PREFIX = "/api/v1/alpha-agent"


def _w(s: str, e: str):
    from backend.services.engine.alpha_agent.mining_config import Window

    return Window(date.fromisoformat(s), date.fromisoformat(e))


@pytest.fixture(autouse=True)
def _reset_coverage_cache():
    """覆盖缓存逐测试复位（模块级状态，不串味）。"""
    aa._coverage_cache = (0.0, None)
    yield
    aa._coverage_cache = (0.0, None)


@pytest.fixture()
def _coverage(monkeypatch):
    """把覆盖固定成 2016-01-04 ~ 2026-10-09（真实 QuantDB 口径）。

    同时把回测末端保护关掉（``_backtest_guard`` → None）：本文件测的是**覆盖
    口径**，末端保护有专门用例（夹一天会让这里每条断言都跟着移一天）。保护
    本身读 provider 日历目录，不该出现在覆盖语义的断言里。
    """
    monkeypatch.setattr(aa, "_quantdb_coverage", lambda: _w("2016-01-04", "2026-10-09"))
    monkeypatch.setattr(aa, "_backtest_guard", lambda coverage=None: None)


@pytest.fixture()
def _guard(monkeypatch):
    """回测可用末端固定为 2026-10-08（覆盖末端 2026-10-09 的前一个交易日）。"""
    monkeypatch.setattr(aa, "_quantdb_coverage", lambda: _w("2016-01-04", "2026-10-09"))
    monkeypatch.setattr(
        aa, "_backtest_guard", lambda coverage=None: date(2026, 10, 8)
    )


# ── 1. 请求归一 ──────────────────────────────────────────────────────


def test_no_config_stays_none(_coverage):
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="",
        mining_end="",
        valid_start="",
        valid_end="",
        datasets=None,
    )
    assert got == (None, None, None, None, None, None)


def test_custom_window_normalized(_coverage):
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="2021-01-04",
        mining_end="2024-12-31",
        valid_start="2025-01-01",
        valid_end="2025-12-31",
        datasets=None,
    )
    assert got == (
        "2021-01-04",
        "2024-12-31",
        "2025-01-01",
        "2025-12-31",
        None,  # 没选数据集 → None（任务行不记"全选"这种参数回声）
        None,  # 未指定频率 → None（日线是默认语义，不留"显式 day"的痕迹）
    )


def test_datasets_selection_dedupes_in_registry_order(_coverage):
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="",
        mining_end="",
        valid_start="",
        valid_end="",
        datasets=["l2_factors", "features_daily", "l2_factors"],
    )
    assert got[4] == ("features_daily", "l2_factors")


def test_unknown_dataset_rejected(_coverage):
    with pytest.raises(ValueError) as exc:
        aa._normalize_request_mining_config(
            market="a_share",
            mining_start="",
            mining_end="",
            valid_start="",
            valid_end="",
            datasets=["no_such_dataset"],
        )
    assert "no_such_dataset" in str(exc.value)


def test_overlapping_validation_rejected(_coverage):
    with pytest.raises(ValueError):
        aa._normalize_request_mining_config(
            market="a_share",
            mining_start="2021-01-04",
            mining_end="2024-12-31",
            valid_start="2024-01-01",  # 早于挖掘终点 → 样本内泄漏
            valid_end="2025-12-31",
            datasets=None,
        )


def test_validation_without_mining_rejected(_coverage):
    with pytest.raises(ValueError):
        aa._normalize_request_mining_config(
            market="a_share",
            mining_start="",
            mining_end="",
            valid_start="2025-01-01",
            valid_end="2025-12-31",
            datasets=None,
        )


def test_window_outside_coverage_rejected(_coverage):
    with pytest.raises(ValueError) as exc:
        aa._normalize_request_mining_config(
            market="a_share",
            mining_start="2015-01-01",
            mining_end="2020-12-31",
            valid_start="",
            valid_end="",
            datasets=None,
        )
    assert "覆盖" in str(exc.value)


def test_non_a_share_market_with_config_rejected(_coverage):
    with pytest.raises(ValueError) as exc:
        aa._normalize_request_mining_config(
            market="us_stock",
            mining_start="2021-01-04",
            mining_end="2024-12-31",
            valid_start="",
            valid_end="",
            datasets=None,
        )
    assert "a_share" in str(exc.value)
    with pytest.raises(ValueError):
        aa._normalize_request_mining_config(
            market="hong_kong",
            mining_start="",
            mining_end="",
            valid_start="",
            valid_end="",
            datasets=["features_daily"],
        )


def test_non_a_share_market_without_config_passes(_coverage):
    """没动挖掘配置时其它市场照旧可提交（本功能不改变它们的任何行为）。"""
    got = aa._normalize_request_mining_config(
        market="us_stock",
        mining_start="",
        mining_end="",
        valid_start="",
        valid_end="",
        datasets=None,
    )
    assert got == (None, None, None, None, None, None)


def test_coverage_none_skips_check(monkeypatch):
    """数据源不可用（覆盖读不到）不做假校验——放行，由引擎侧失败。"""
    monkeypatch.setattr(aa, "_quantdb_coverage", lambda: None)
    monkeypatch.setattr(aa, "_backtest_guard", lambda coverage=None: None)
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="2000-01-01",
        mining_end="2030-01-01",
        valid_start="",
        valid_end="",
        datasets=None,
    )
    assert got[:2] == ("2000-01-01", "2030-01-01")


def test_backtest_guard_applies_even_without_coverage(monkeypatch):
    """覆盖读不到 ≠ 放弃末端保护：日历读得到就照退让（回测崩的判据是日历）。

    "放行覆盖校验"与"回测末端保护"是两件事：前者是数据源可用性（不做假校验），
    后者是 qlib 回测的硬约束（t+1 成交需要次日行情）。
    """
    monkeypatch.setattr(aa, "_quantdb_coverage", lambda: None)
    monkeypatch.setattr(
        aa, "_backtest_guard", lambda coverage=None: date(2026, 10, 8)
    )
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="2000-01-01",
        mining_end="2030-01-01",
        valid_start="",
        valid_end="",
        datasets=None,
    )
    assert got[:2] == ("2000-01-01", "2026-10-08")


def test_task_row_stores_clamped_window(_guard):
    """任务行存退让后的端点：任务详情/预览/实跑看到同一组日期，不留"好看但会崩"的。"""
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="2021-01-01",
        mining_end="2026-10-09",
        valid_start="2026-10-08",
        valid_end="2026-10-09",
        datasets=None,
    )
    assert got[:4] == ("2021-01-01", "2026-10-08", "2026-10-08", "2026-10-08")

    # 无检验窗：挖掘端点同样退让（test 段 = 挖掘段末 15%，不退让照样崩）
    got = aa._normalize_request_mining_config(
        market="a_share",
        mining_start="2021-01-01",
        mining_end="2026-10-09",
        valid_start="",
        valid_end="",
        datasets=None,
    )
    assert got[:2] == ("2021-01-01", "2026-10-08")


# ── 2. 数据覆盖 ──────────────────────────────────────────────────────


def _make_partitions(root: Path, days: list[str]) -> None:
    base = root / "1_kline_data" / "daily_forward"
    for d in days:
        (base / f"dt={d}").mkdir(parents=True, exist_ok=True)


def test_coverage_reads_daily_forward_partitions(tmp_path, monkeypatch):
    from backend.services.engine.rd_agent.rd_loop_wrapper import RDLoopWrapper

    _make_partitions(tmp_path, ["20160104", "20160105", "20261009"])
    monkeypatch.setattr(
        RDLoopWrapper, "_resolve_quantdb_dir", staticmethod(lambda: str(tmp_path))
    )
    assert aa._quantdb_coverage() == _w("2016-01-04", "2026-10-09")


def test_coverage_caches_within_ttl(tmp_path, monkeypatch):
    from backend.services.engine.rd_agent.rd_loop_wrapper import RDLoopWrapper

    _make_partitions(tmp_path, ["20160104", "20261009"])
    monkeypatch.setattr(
        RDLoopWrapper, "_resolve_quantdb_dir", staticmethod(lambda: str(tmp_path))
    )
    assert aa._quantdb_coverage() == _w("2016-01-04", "2026-10-09")
    # 数据侧又长了一天：TTL 内不重读（覆盖只随日更变化，不值得按请求现读）
    _make_partitions(tmp_path, ["20261012"])
    assert aa._quantdb_coverage() == _w("2016-01-04", "2026-10-09")
    # 缓存复位后重读
    aa._coverage_cache = (0.0, None)
    assert aa._quantdb_coverage() == _w("2016-01-04", "2026-10-12")


def test_coverage_none_when_dir_missing(monkeypatch):
    from backend.services.engine.rd_agent.rd_loop_wrapper import RDLoopWrapper

    monkeypatch.setattr(
        RDLoopWrapper, "_resolve_quantdb_dir", staticmethod(lambda: None)
    )
    assert aa._quantdb_coverage() is None
    # 读不到也缓存（防每次请求重复探测刷日志）
    assert aa._coverage_cache[0] != 0.0


# ── 3. 派发环原样下发挖掘配置 ────────────────────────────────────────


class _StubLauncher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_or_queue(self, user_id, **kwargs):  # noqa: ANN001
        self.calls.append(kwargs)

        class _R:
            status = "running"
            task_id = "t1"
            queue_position = None
            deduplicated = False

        return _R()

    # 单条 evolve 路径的合约面（限流判定 + 启动）：形状与真 launcher 一致，
    # 容量给宽松常数——本文件不测 429 背压。
    def count_running(self):
        return {"global": 0, "by_user": {}}

    def running_capacity(self):
        return (5, 50)

    async def start_evolution(self, user_id, **kwargs):  # noqa: ANN001
        self.calls.append(kwargs)
        return "t1"


def test_dispatch_forwards_mining_config():
    launcher = _StubLauncher()
    items, started, queued, failed = asyncio.run(
        aa._dispatch_direction_items(
            launcher,
            "u1",
            "t1",
            market="a_share",
            universe="csi300",
            loop_n=3,
            picks=[("方向A", None)],
            direction_mode=None,
            mining_start="2021-01-04",
            mining_end="2024-12-31",
            valid_start="2025-01-01",
            valid_end="2025-12-31",
            datasets=("features_daily",),
        )
    )
    assert (started, queued, failed) == (1, 0, 0)
    assert items[0]["task_id"] == "t1"
    call = launcher.calls[0]
    assert call["mining_start"] == "2021-01-04"
    assert call["mining_end"] == "2024-12-31"
    assert call["valid_start"] == "2025-01-01"
    assert call["valid_end"] == "2025-12-31"
    assert call["datasets"] == ("features_daily",)


# ── 4. 预览端点 ──────────────────────────────────────────────────────


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(aa, "get_authenticated_identity", lambda request: ("u1", "t1"))
    app = FastAPI()
    app.include_router(aa.router)
    return TestClient(app)


def test_config_options_defaults(client, _coverage):
    r = client.get(f"{_PREFIX}/mining/config-options")
    assert r.status_code == 200
    d = r.json()["data"]

    assert [x["key"] for x in d["datasets"]] == [
        "features_daily",
        "l1_factors",
        "l2_factors",
    ]
    assert d["coverage"] == {"start": "2016-01-04", "end": "2026-10-09"}
    # 默认窗口：覆盖末端前推 5 年 = 4 年挖掘 + 1 年检验
    assert d["defaults"]["mining"] == {"start": "2021-10-09", "end": "2025-10-09"}
    assert d["defaults"]["validation"] == {"start": "2025-10-09", "end": "2026-10-09"}
    assert d["windows"]["is_default"] is True
    assert d["windows"]["mining"] == d["defaults"]["mining"]
    # 分段与提交路径同源：test = 检验区间
    assert d["segments"]["test"] == {"start": "2025-10-09", "end": "2026-10-09"}
    # 无回测末端保护（本 fixture 关掉了）→ 字段在位但为 None（前端据此判断
    # 日期选择器上限；None = 不设上限）
    assert d["backtest_end_guard"] is None


def test_config_options_carries_algorithm_catalog(client, _coverage):
    """算法目录（P2）随配置面板一次下发，与 GET /algorithms 同源同形状。

    前端靠每条的 uses_llm/uses_datasets/requires_gpu/stages 决定控件显隐与
    步骤渲染——同源才能保证「目录说什么、提交路径认什么」永不漂移。
    """
    r = client.get(f"{_PREFIX}/mining/config-options")
    d = r.json()["data"]

    assert d["default_algorithm"] == "rdagent"
    by_key = {a["key"]: a for a in d["algorithms"]}
    assert {"rdagent", "statistical"} <= set(by_key)
    assert by_key["rdagent"]["uses_llm"] is True
    stat = by_key["statistical"]
    assert stat["uses_llm"] is False
    assert stat["uses_datasets"] is False
    assert stat["requires_gpu"] is False
    assert [s["key"] for s in stat["stages"]] == [
        "data",
        "filter",
        "evaluate",
        "select",
        "stability",
        "dedup",
        "composite",
    ]


def test_config_options_defaults_anchor_backtest_guard(client, _guard):
    """默认窗锚保护末端：预填的默认终点本身就是可回测的最后一天。"""
    r = client.get(f"{_PREFIX}/mining/config-options")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["backtest_end_guard"] == "2026-10-08"
    assert d["coverage"] == {"start": "2016-01-04", "end": "2026-10-09"}
    assert d["defaults"]["validation"] == {"start": "2025-10-08", "end": "2026-10-08"}
    assert d["defaults"]["mining"] == {"start": "2021-10-08", "end": "2025-10-08"}
    assert d["segments"]["test"]["end"] == "2026-10-08"


def test_config_options_preview_clamps_to_backtest_guard(client, _guard):
    """预览与实跑同源：终点压线的请求在预览里就显示成退让后的端点。"""
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={
            "mining_start": "2021-01-04",
            "mining_end": "2026-09-30",
            "valid_start": "2026-10-01",
            "valid_end": "2026-10-09",
        },
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["windows"]["mining"] == {"start": "2021-01-04", "end": "2026-09-30"}
    assert d["windows"]["validation"] == {"start": "2026-10-01", "end": "2026-10-08"}
    assert d["segments"]["test"] == {"start": "2026-10-01", "end": "2026-10-08"}

    # 只给挖掘窗且终点压线 → 同样是退让后的端点（test 段=挖掘段末 15%）
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={"mining_start": "2021-01-04", "mining_end": "2026-10-09"},
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["windows"]["mining"] == {"start": "2021-01-04", "end": "2026-10-08"}
    assert d["segments"]["test"]["end"] == "2026-10-08"


def test_config_options_preview_custom_windows(client, _coverage):
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={
            "mining_start": "2021-01-04",
            "mining_end": "2024-12-31",
            "valid_start": "2025-01-01",
            "valid_end": "2025-12-31",
        },
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["windows"]["is_default"] is False
    assert d["windows"]["mining"] == {"start": "2021-01-04", "end": "2024-12-31"}
    assert d["windows"]["validation"] == {"start": "2025-01-01", "end": "2025-12-31"}
    assert d["segments"]["test"] == {"start": "2025-01-01", "end": "2025-12-31"}
    # 默认值仍在（前端「恢复默认」按钮的数据源）
    assert d["defaults"]["mining"]["start"] == "2021-10-09"


def test_config_options_preview_mining_only_has_no_validation(client, _coverage):
    """只给挖掘窗 = 不启用样本外检验（test 退化为挖掘窗内 85% 之后）。"""
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={"mining_start": "2021-01-04", "mining_end": "2024-12-31"},
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["windows"]["validation"] is None
    assert d["segments"]["test"]["end"] == "2024-12-31"


def test_config_options_invalid_window_400(client, _coverage):
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={"mining_start": "2024-12-31", "mining_end": "2021-01-04"},
    )
    assert r.status_code == 400
    assert "挖掘数据区间" in r.json()["detail"]


def test_config_options_out_of_coverage_400(client, _coverage):
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={"mining_start": "2015-01-01", "mining_end": "2020-12-31"},
    )
    assert r.status_code == 400
    assert "覆盖" in r.json()["detail"]


# ── 5. 提交入口的 400（烧 token 前拦截） ─────────────────────────────


@pytest.fixture()
def _evolve_stubs(monkeypatch):
    monkeypatch.setattr(aa, "get_authenticated_identity", lambda request: ("u1", "t1"))
    monkeypatch.setattr(aa, "assert_identity_not_spoofed", lambda **kwargs: None)


def test_evolve_rejects_invalid_window_before_llm(client, _coverage, _evolve_stubs):
    r = client.post(
        f"{_PREFIX}/evolve",
        json={
            "market": "a_share",
            "mining_start": "2024-12-31",
            "mining_end": "2021-01-04",
        },
    )
    assert r.status_code == 400
    assert "挖掘数据区间" in r.json()["detail"]


def test_evolve_rejects_datasets_for_other_market(client, _coverage, _evolve_stubs):
    r = client.post(
        f"{_PREFIX}/evolve",
        json={"market": "us_stock", "datasets": ["features_daily"]},
    )
    assert r.status_code == 400
    assert "a_share" in r.json()["detail"]


# ── 6. 分钟频率（移植 P1.5） ──────────────────────────────

MINUTE_BLOCK = ("2026-03-04", "2026-03-06")


@pytest.fixture()
def _minute_provider(tmp_path, monkeypatch):
    """带 58 天空洞的合成分钟 provider，并把它设成任务级 provider 目录。"""
    from datetime import timedelta

    from backend.services.engine import minute_qlib_provider as MP
    from backend.tests.test_minute_qlib_provider import (
        DAY1,
        DAY2,
        HOLE_AFTER,
        _write_day_provider,
        _write_source,
    )

    block = (HOLE_AFTER, HOLE_AFTER + timedelta(days=1), HOLE_AFTER + timedelta(days=2))
    days = (DAY1, DAY2, *block)
    source = _write_source(tmp_path, days=days)
    day = _write_day_provider(tmp_path, days=(*days, block[-1] + timedelta(days=1)))
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(provider))
    return provider


@pytest.fixture()
def _minute_provider_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(tmp_path / "nope"))


def test_request_normalize_minute_materializes_default_window(_minute_provider):
    """分钟默认窗 = 可用连续块，且**显式**返回（子进程不接受"没窗口"）。"""
    out = aa._normalize_request_mining_config(
        market="a_share",
        mining_start=None,
        mining_end=None,
        valid_start=None,
        valid_end=None,
        datasets=None,
        freq="1min",
    )
    assert out == (*MINUTE_BLOCK, None, None, None, "1min")


def test_request_normalize_minute_skips_quantdb_coverage(_minute_provider, monkeypatch):
    """分钟窗口的覆盖校验走 provider 可用块，不读 QuantDB（那覆盖是日线的）。"""

    def _boom():
        raise AssertionError("分钟模式不该读 QuantDB 覆盖")

    monkeypatch.setattr(aa, "_quantdb_coverage", _boom)
    out = aa._normalize_request_mining_config(
        market="a_share",
        mining_start=MINUTE_BLOCK[0],
        mining_end=MINUTE_BLOCK[1],
        valid_start=None,
        valid_end=None,
        datasets=None,
        freq="1min",
    )
    assert out[:2] == MINUTE_BLOCK


def test_request_normalize_minute_window_spanning_hole_rejected(_minute_provider):
    with pytest.raises(ValueError) as exc:
        aa._normalize_request_mining_config(
            market="a_share",
            mining_start="2026-01-05",
            mining_end=MINUTE_BLOCK[1],
            valid_start=None,
            valid_end=None,
            datasets=None,
            freq="1min",
        )
    assert "断层" in str(exc.value)


def test_request_normalize_minute_rejected_for_other_market(_minute_provider):
    with pytest.raises(ValueError):
        aa._normalize_request_mining_config(
            market="us_stock",
            mining_start=None,
            mining_end=None,
            valid_start=None,
            valid_end=None,
            datasets=None,
            freq="1min",
        )


def test_dispatch_forwards_freq():
    launcher = _StubLauncher()
    asyncio.run(
        aa._dispatch_direction_items(
            launcher,
            "u1",
            "t1",
            market="a_share",
            universe="csi300",
            loop_n=3,
            picks=[("方向A", None)],
            direction_mode=None,
            mining_start=MINUTE_BLOCK[0],
            mining_end=MINUTE_BLOCK[1],
            freq="1min",
        )
    )
    assert launcher.calls[0]["freq"] == "1min"


def test_config_options_minute_block(client, _minute_provider, _coverage):
    """分钟预览：覆盖=provider 全区间，usable/gaps 如实带上，默认窗=可用块。"""
    r = client.get(f"{_PREFIX}/mining/config-options", params={"freq": "1min"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["freq"] == "1min"
    assert [x["value"] for x in d["freqs"]] == ["day", "1min"]
    assert d["minute"]["ready"] is True
    assert d["minute"]["usable"] == {"start": MINUTE_BLOCK[0], "end": MINUTE_BLOCK[1]}
    assert d["minute"]["usable_days"] == 3
    assert d["minute"]["gaps"] == [{"start": "2026-01-06", "end": "2026-03-04"}]
    assert d["minute"]["coverage"]["start"] == "2026-01-05"
    # 默认窗与生效窗都是可用块；无检验窗（4 个月数据没有 4+1 年可切）
    assert d["windows"]["is_default"] is True
    assert d["windows"]["mining"] == d["minute"]["usable"]
    assert d["windows"]["validation"] is None
    assert d["backtest_end_guard"] == MINUTE_BLOCK[1]
    assert d["segments"]["test"]["end"] == MINUTE_BLOCK[1]


def test_config_options_minute_not_ready_is_degraded_not_500(
    client, _minute_provider_missing, _coverage
):
    """provider 未构建：如实回 ready=false + 空窗口，前端据此置灰——不 500 也不假默认。"""
    r = client.get(f"{_PREFIX}/mining/config-options", params={"freq": "1min"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["minute"]["ready"] is False
    assert d["minute"]["usable"] is None and d["minute"]["gaps"] == []
    assert d["coverage"] is None and d["defaults"]["mining"] is None
    assert d["windows"]["mining"] is None and d["segments"] == {}
    assert d["backtest_end_guard"] is None


def test_config_options_minute_spanning_hole_400(client, _minute_provider, _coverage):
    r = client.get(
        f"{_PREFIX}/mining/config-options",
        params={
            "freq": "1min",
            "mining_start": "2026-01-05",
            "mining_end": MINUTE_BLOCK[1],
        },
    )
    assert r.status_code == 400
    assert "断层" in r.json()["detail"]


def test_config_options_unknown_freq_400(client, _coverage):
    r = client.get(f"{_PREFIX}/mining/config-options", params={"freq": "5min"})
    assert r.status_code == 400
    assert "不支持的挖掘频率" in r.json()["detail"]


def test_evolve_rejects_minute_for_other_market(client, _coverage, _evolve_stubs):
    r = client.post(
        f"{_PREFIX}/evolve", json={"market": "us_stock", "freq": "1min"}
    )
    assert r.status_code == 400
    assert "A 股" in r.json()["detail"]


def test_evolve_rejects_minute_without_provider(
    client, _minute_provider_missing, _coverage, _evolve_stubs
):
    r = client.post(
        f"{_PREFIX}/evolve", json={"market": "a_share", "freq": "1min"}
    )
    assert r.status_code == 400
    assert "分钟数据面未构建" in r.json()["detail"]


def test_evolve_minute_happy_path_materializes_window_for_launcher(
    client, _minute_provider, _coverage, _evolve_stubs, monkeypatch
):
    """分钟提交穿透端点（query 与 body 两条路径）：

    默认窗物化成**可用连续块**后用显式日期下发（任务行/子进程不接受"没窗口"），
    freq 原样带到 launcher；检验窗/数据集保持 None（分钟面不吃这些档位）。
    """
    from backend.services.engine.alpha_agent.llm_client import LLMConfig

    cfg = LLMConfig(
        api_key="sk-x", base_url="https://llm.test/v1", model="m1", protocol="openai"
    )

    async def fake_llm(user_id, tenant_id):
        return cfg, "user_profile", None

    launcher = _StubLauncher()
    monkeypatch.setattr(aa, "get_launcher", lambda: launcher)
    monkeypatch.setattr(aa, "_resolve_effective_llm_config", fake_llm)

    # query 路径（老前端形态：不带 body）
    r = client.post(f"{_PREFIX}/evolve", params={"market": "a_share", "freq": "1min"})
    assert r.status_code == 200, r.text
    # body 路径（新前端：payload.freq 覆盖 query 缺省）
    r2 = client.post(f"{_PREFIX}/evolve", json={"market": "a_share", "freq": "1min"})
    assert r2.status_code == 200, r2.text

    assert len(launcher.calls) == 2
    for call in launcher.calls:
        assert call["freq"] == "1min"
        assert call["mining_start"] == MINUTE_BLOCK[0]
        assert call["mining_end"] == MINUTE_BLOCK[1]
        assert call["valid_start"] is None and call["valid_end"] is None
        assert call["datasets"] is None
