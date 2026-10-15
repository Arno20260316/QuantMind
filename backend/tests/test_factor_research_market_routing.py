"""因子研究四市场分层 —— 路由层装配与契约锁（2026-10-16）。

前两份市场测试锁定 store/scorecard 层（`test_factor_research_markets.py`、
`test_factor_research_panel_markets.py`）；本文件补的是**从 HTTP 面进入的那一层**：

1. **端点层**：非法 market 在请求级 422（而非冒泡成 500）；`classic × 非 CN`
   由 `_run` 译 422。这类错误的共同失败模式是「状态码对、语义错」——
   500 会让前端把它当服务故障、引导用户去点「一键计算」，而实际是参数问题。
2. **装配层**：`start_build` 给私有构建器装 `--market`。漏传的后果不是报错，
   是**港股页点了计算、默默建了 A 股面板**（全链无异常）——必须钉死。
3. **契约层**：leaderboard 的 `excess_by_bench`（键集 = 本市场基准全表）/ CN 旧键
   同源保留 / 非 CN 旧键缺席 / FUTURES 诚实空表；catalog 的基准清单按市场给出。

不碰真实数据目录：端点用例走的是「校验/翻译路径」（`artifact_dir` 在目录解析
**之前**就抛 MarketUnsupported），服务用例把 store 全桩成内存迷你面板。
"""

from __future__ import annotations

import time

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.services.engine import main as engine_main
from backend.services.engine.factor_research import router as fr_router
from backend.services.engine.factor_research import scorecard, service
from backend.services.engine.factor_research.store import MarketUnsupported

SECRET = "unit-test-internal-secret-9f2a"
# 前端实际形状：经网关转发（内部密钥在）+ 已登录用户身份。
# 只带内部密钥是「匿名访客」，会被 auth 中间件 401 拦住、够不到路由
# （那是 test_factor_research_auth_gate.py 钉的事，这里必须放行到路由层）。
GATE_HEADERS = {"X-Internal-Call": SECRET, "X-User-Id": "10000001"}


@pytest.fixture(autouse=True)
def _fixed_internal_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_main, "get_internal_call_secret", lambda: SECRET)


@pytest.fixture
def client() -> TestClient:
    """不进入 `with`：不触发 lifespan，用例因此不需要 DB / Redis。"""
    return TestClient(engine_main.app)


# ---------------------------------------------------------------------------
# 1. 端点层：错误码分流
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/factor-research/catalog",
        "/api/v1/factor-research/screening",
    ],
)
def test_非法市场请求级422而非500(client: TestClient, path: str) -> None:
    """`_market()` 收口：绝不把校验留给服务深处（裸 ValueError 会冒泡成 500）。"""
    r = client.get(f"{path}?market=bogus", headers=GATE_HEADERS)
    assert r.status_code == 422, r.text
    assert "未知市场" in r.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # 读面：catalog 走 store.metrics → artifact_dir 抛
        ("GET", "/api/v1/factor-research/catalog?dataset=classic&market=HK"),
        # 写面：一键计算同样先解析产物目录，514 之前就拒绝
        ("POST", "/api/v1/factor-research/build?dataset=classic&market=HK"),
    ],
)
def test_classic非cn端点译422(client: TestClient, method: str, path: str) -> None:
    """`MarketUnsupported` → 422（请求合法、组合不供给），而不是 503/500。"""
    r = client.request(method, path, headers=GATE_HEADERS)
    assert r.status_code == 422, r.text
    assert "A 股专属" in r.json()["detail"]


def test_未接入基准的市场端点译422(client: TestClient) -> None:
    """CRYPTO 是合法市场（validate_market 放行）但工作台未接基准表 → 422。

    这条防的是「静默回退 CN 指数」：CRYPTO 页面若拿沪深300 当基准，
    超额的每一个数都是假的、且全程无异常。
    """
    r = client.get(
        "/api/v1/factor-research/catalog?dataset=private&market=CRYPTO",
        headers=GATE_HEADERS,
    )
    assert r.status_code == 422, r.text
    assert "未接入市场" in r.json()["detail"]


def test_market收口_规范化与别名() -> None:
    """大小写 / 别名在收口层消化；合法键原样返回规范大写。"""
    assert fr_router._market("hk") == "HK"
    assert fr_router._market("a_share") == "CN"
    assert fr_router._market(" CN ") == "CN"
    with pytest.raises(HTTPException) as ei:
        fr_router._market("NASDAQ")
    assert ei.value.status_code == 422


# ---------------------------------------------------------------------------
# 2. 装配层：构建子进程的 --market
# ---------------------------------------------------------------------------


class _FakeProc:
    pid = 424242


@pytest.fixture
def build_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """私有构建装配：市场根指向 tmp，Popen 换成记录器（绝不真起构建子进程）。"""
    # 面包屑必须放：resolve_quantdb_dir 对「不存在**或为空**」的候选目录直接跳过，
    # 空 tmp 目录会让 CN 解析落到项目 data/quantdb（符号链接 → 真数据，只读）。
    for sub in ("quanthk", "quantdb"):
        d = tmp_path / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / ".keep").write_text("", encoding="utf-8")
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path / "quanthk"))
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "quantdb"))
    cmds: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        cmds.append(list(cmd))
        return _FakeProc()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    return cmds


def test_build_private恒装配显式市场键(build_env) -> None:
    """private 构建器**恒**收显式 ``--market``（CN 也传，不做特例）。

    漏传的失败模式不是报错，是「港股页点了计算、默默建了 A 股面板」；
    而「CN 不传」会让脚本默认值变成第二条事实源（脚本改默认 → CN 静默漂移）。
    市场由 service 单一决定，脚本只执行。
    """
    out_hk = service.start_build("private", "HK")
    out_cn = service.start_build("private", "CN")
    assert out_hk["started"] is True and out_hk["market"] == "HK"
    assert out_cn["started"] is True and out_cn["market"] == "CN"
    assert build_env[0][-2:] == ["--market", "HK"]
    assert build_env[1][-2:] == ["--market", "CN"]


def test_snapshot_status回执带市场(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path / "quanthk"))
    out = service.snapshot_status("private", "HK")
    assert out["market"] == "HK" and out["dataset"] == "private"
    assert out["exists"] is False and out["running"] is False


# ---------------------------------------------------------------------------
# 3. 契约层：leaderboard 超额键 / catalog 基准清单
# ---------------------------------------------------------------------------

_MONTHS = pd.date_range("2024-01-31", periods=8, freq="ME")  # 8 个月 > MIN_MONTHS=6
_HK_SYMS = ["00700.HK", "09988.HK"]


def _tiny_panel() -> scorecard.Panel:
    rows = []
    for code in ("A", "B"):
        for mi, mon in enumerate(_MONTHS):
            for rk, sym in enumerate(_HK_SYMS, 1):
                rows.append(
                    {
                        "factor_code": code,
                        "trade_date": mon,
                        "rank": rk,
                        "symbol": sym,
                        "score": float(10 - rk - mi),
                        "raw": float(rk),
                        "fwd_ret": 0.01 * rk + 0.001 * mi,
                    }
                )
    return scorecard.Panel(pd.DataFrame(rows))


def _bench_frame(codes_nav: dict[str, list[float]]) -> pd.DataFrame:
    """基准长表（报告链同款契约：trade_date / nav / index_code）。"""
    rows = []
    for code, navs in codes_nav.items():
        for d, nav in zip(_MONTHS, navs, strict=True):
            rows.append({"trade_date": d, "nav": float(nav), "index_code": code})
    return pd.DataFrame(rows)


def _ic_frame() -> pd.DataFrame:
    """IC 长表（快照真实形状：factor_code / trade_date / ic）。

    生产快照恒有 ic.parquet；ic 缺失的 leaderboard 降级是既有边角（与本任务无关），
    此处按真实形状打桩、不为它造第二条路径。
    """
    # IC 带噪声：恒定序列 std=0 → ic_ir=None（真实数据不会出现），
    # 桩要长得像生产数据，不能把边界当常态。
    return pd.DataFrame(
        [
            {"factor_code": c, "trade_date": d, "ic": 0.03 + 0.001 * mi}
            for c in ("A", "B")
            for mi, d in enumerate(_MONTHS)
        ]
    )


@pytest.fixture
def lb_env(monkeypatch: pytest.MonkeyPatch):
    """leaderboard 全桩：store 换成内存迷你面板（读取面按 service 实际消费打桩）。"""
    service._ctx_cache.clear()
    service._derived_cache.clear()
    panel = _tiny_panel()
    stamp = f"snap-{time.time_ns()}"  # 每用例唯一：防 ctx 缓存跨用例命中旧基准桩
    monkeypatch.setattr(service.store, "panel_stamp", lambda *a, **k: stamp)
    monkeypatch.setattr(service.store, "panel", lambda *a, **k: panel)
    monkeypatch.setattr(service.store, "ic_table", lambda *a, **k: _ic_frame())
    monkeypatch.setattr(service.store, "stock_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(service.store, "factors_meta", lambda *a, **k: {"meta": {}})
    monkeypatch.setattr(
        service.store, "metrics", lambda *a, **k: {"meta": {}, "metrics": {}}
    )
    yield monkeypatch
    service._ctx_cache.clear()
    service._derived_cache.clear()


def test_leaderboard_非cn按市场基准发excess_by_bench(lb_env) -> None:
    """键集 = 本市场基准全表；无序列的基准诚实 None；CN 旧键整排缺席。"""
    lb_env.setattr(
        service.store,
        "benchmark_table",
        lambda *a, **k: _bench_frame({"HSI.HK": [100 + i for i in range(8)]}),
    )
    out = service.leaderboard(None, None, 30, "private", 0.0, "HK")
    assert out["meta"]["market"] == "HK"
    assert out["meta"]["benchmarks"] == [
        {"code": "HSI.HK", "name": "恒生指数"},
        {"code": "HSCEI.HK", "name": "恒生中国企业指数"},
        {"code": "HSTECH.HK", "name": "恒生科技指数"},
    ]
    row = out["leaderboard"][0]
    assert set(row["excess_by_bench"]) == {"HSI.HK", "HSCEI.HK", "HSTECH.HK"}
    # HSI 有基准序列且面板 8 个月 ≥ MIN_MONTHS → 超额是真算出来的数
    assert row["excess_by_bench"]["HSI.HK"] is not None
    # 无序列的基准置 None（而不是拿别的指数顶替）
    assert row["excess_by_bench"]["HSCEI.HK"] is None
    for legacy in ("excess_300", "excess_800", "excess_500"):
        assert legacy not in row, f"{legacy} 挂在恒生基准上是一句假话"


def test_leaderboard_cn旧键保留且与新键同源(lb_env) -> None:
    """CN 一期兼容：旧键逐字保留，且值就是 excess_by_bench 里同码的同一份。"""
    lb_env.setattr(
        service.store,
        "benchmark_table",
        lambda *a, **k: _bench_frame(
            {
                "000300.SH": [100 + i for i in range(8)],
                "000906.SH": [90 + i * 0.5 for i in range(8)],
            }
        ),
    )
    out = service.leaderboard(None, None, 30, "private", 0.0, "CN")
    row = out["leaderboard"][0]
    assert row["excess_300"] == row["excess_by_bench"]["000300.SH"]
    assert row["excess_800"] == row["excess_by_bench"]["000906.SH"]
    assert row["excess_500"] == row["excess_by_bench"]["000905.SH"]  # 无序列 → 双 None
    assert set(row["excess_by_bench"]) == {"000300.SH", "000906.SH", "000905.SH"}
    assert [b["code"] for b in out["meta"]["benchmarks"]] == [
        "000300.SH",
        "000906.SH",
        "000905.SH",
    ]


def test_leaderboard_futures无基准诚实空表(lb_env) -> None:
    """FUTURES 无指数基准：空清单 + 空超额，绝不拿别的市场指数冒充。"""
    lb_env.setattr(service.store, "benchmark_table", lambda *a, **k: None)
    out = service.leaderboard(None, None, 30, "private", 0.0, "FUTURES")
    assert out["meta"]["benchmarks"] == []
    for row in out["leaderboard"]:
        assert row["excess_by_bench"] == {}
        assert "excess_300" not in row


def test_catalog私有库基准清单按市场(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.store, "factors_meta", lambda *a, **k: {"factors": [], "meta": {}}
    )
    hk = service.catalog("private", "HK")
    assert hk["market"] == "HK"
    assert [b["symbol"] for b in hk["benchmarks"]] == [
        "HSI.HK",
        "HSCEI.HK",
        "HSTECH.HK",
    ]
    fut = service.catalog("private", "FUTURES")
    assert fut["market"] == "FUTURES" and fut["benchmarks"] == []


def test_catalog未接入基准的市场诚实抛错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.store, "factors_meta", lambda *a, **k: {"factors": [], "meta": {}}
    )
    with pytest.raises(MarketUnsupported):
        service.catalog("private", "CRYPTO")
