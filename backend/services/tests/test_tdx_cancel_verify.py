"""TDX 撤单「假失败」复核单测（tdx_cancel_verify + 双客户端消费）。

背景（2026-10-12 实测）: 桥撤单路由只认 Value=="1"，而直连账号的成功码为
"2"（同仓下单路径 plan_executor 已把 "2" 当成功）——实际已生效的撤单被桥
报成 {"success": false}；委托 2s 后终态 side=cancel / status=rejected。
消费方纪律: 撤单握手指失败/异常时，不能仅凭报文下结论，须按当日委托终态复核
（side=cancel 即已撤；filled=撤销前已成交；rejected 非撤单=废单已终结）。
"""

from __future__ import annotations

import pytest

from backend.services.live_trading.services import tdx_cancel_verify as cv
from backend.services.live_trading.services.tdx_cancel_verify import (
    classify_order_terminal,
    verify_cancel_terminal,
)
from backend.services.live_trading.services.tdx_push_service import (
    TdxPushError,
    TdxPushService,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fast_verify(monkeypatch):
    monkeypatch.setattr(cv, "CANCEL_VERIFY_ATTEMPTS", 3)
    monkeypatch.setattr(cv, "CANCEL_VERIFY_DELAY_S", 0)


# ---------- classify_order_terminal ----------


@pytest.mark.parametrize(
    "order,expected",
    [
        (None, "unknown"),
        ({}, "unknown"),
        # 已撤: BSFlag=-1 优先于 Status（TDX Status=0 已撤与废单同码）
        ({"side": "cancel", "status": "rejected"}, "cancelled"),
        ({"side": "sell", "status": "cancelled"}, "cancelled"),
        (
            {
                "side": "buy",
                "status": "partial_cancelled",
                "filled_volume": 100,
                "total_volume": 200,
            },
            "cancelled",
        ),
        # 成交
        (
            {
                "side": "buy",
                "status": "filled",
                "filled_volume": 100,
                "total_volume": 100,
            },
            "filled",
        ),
        # 状态缺失但量已满: 以量兜底
        (
            {
                "side": "buy",
                "status": "submitted",
                "filled_volume": 100,
                "total_volume": 100,
            },
            "filled",
        ),
        # 终态但非本次撤单所致（废单/过期）
        (
            {
                "side": "buy",
                "status": "rejected",
                "filled_volume": 0,
                "total_volume": 100,
            },
            "dead",
        ),
        ({"side": "buy", "status": "expired"}, "dead"),
        # 在途
        ({"side": "buy", "status": "submitted"}, "active"),
        (
            {
                "side": "buy",
                "status": "partial_fill",
                "filled_volume": 50,
                "total_volume": 100,
            },
            "active",
        ),
        ({"side": "buy", "status": "weird-new-status"}, "unknown"),
    ],
)
def test_classify_order_terminal(order, expected):
    assert classify_order_terminal(order) == expected


# ---------- verify_cancel_terminal ----------


def _seq_query(seq):
    """按序返回的假查询；元素为 dict 或 Exception。记录调用次数。"""
    calls = {"n": 0}

    async def _q():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        item = seq[i]
        if isinstance(item, Exception):
            raise item
        return dict(item)

    return _q, calls


async def test_verify_returns_cancelled_early():
    q, calls = _seq_query([{"side": "cancel", "status": "rejected"}])
    state, order = await verify_cancel_terminal(q)
    assert state == "cancelled" and order["side"] == "cancel"
    assert calls["n"] == 1


async def test_verify_waits_through_active_then_cancelled():
    q, calls = _seq_query(
        [
            {"side": "buy", "status": "submitted"},
            {"side": "cancel", "status": "rejected"},
        ]
    )
    state, _ = await verify_cancel_terminal(q)
    assert state == "cancelled" and calls["n"] == 2


async def test_verify_all_active_reports_active():
    q, calls = _seq_query([{"side": "buy", "status": "submitted"}])
    state, _ = await verify_cancel_terminal(q)
    assert state == "active" and calls["n"] == 3  # attempts 用满


async def test_verify_query_error_then_cancelled():
    q, _ = _seq_query(
        [TdxPushError("桥不可达"), {"side": "cancel", "status": "rejected"}]
    )
    state, _ = await verify_cancel_terminal(q)
    assert state == "cancelled"


async def test_verify_all_query_errors_reports_unknown():
    q, _ = _seq_query([TdxPushError("x")])
    state, order = await verify_cancel_terminal(q)
    assert state == "unknown" and order == {}


# ---------- TdxPushService.cancel_order ----------


def _svc(cancel_resp, status_seq):
    svc = TdxPushService(bridge_url="http://bridge.test", bridge_token="t")
    calls = {"cancel": 0, "status": 0}
    q, _ = _seq_query(status_seq)

    async def _post(path, payload):
        calls["cancel"] += 1
        assert path == "/api/v1/orders/cancel"
        if isinstance(cancel_resp, Exception):
            raise cancel_resp
        return dict(cancel_resp)

    async def _status(wtbh):
        calls["status"] += 1
        return await q()

    svc._post = _post
    svc.pull_order_status = _status
    return svc, calls


async def test_pusher_cancel_success_passthrough_no_query():
    resp = {"success": True, "message": "撤单成功"}
    svc, calls = _svc(resp, [])
    out = await svc.cancel_order("601000.SH", "229323")
    assert out == resp and calls["status"] == 0


async def test_pusher_cancel_false_but_verified_cancelled():
    svc, _ = _svc(
        {"success": False, "message": "提交撤单失败：撤单提交失败"},
        [{"side": "cancel", "status": "rejected", "order_id": "229323"}],
    )
    out = await svc.cancel_order("601000.SH", "229323")
    assert out["success"] is True and out.get("verified") is True
    assert "复核" in out["message"]
    assert out["bridge_message"] == "提交撤单失败：撤单提交失败"


async def test_pusher_cancel_false_still_active_unchanged():
    resp = {"success": False, "message": "提交撤单失败"}
    svc, _ = _svc(resp, [{"side": "buy", "status": "submitted"}])
    out = await svc.cancel_order("601000.SH", "229323")
    assert out == resp


async def test_pusher_cancel_false_but_filled():
    svc, _ = _svc(
        {"success": False, "message": "fail"},
        [
            {
                "side": "buy",
                "status": "filled",
                "filled_volume": 100,
                "total_volume": 100,
            }
        ],
    )
    out = await svc.cancel_order("601000.SH", "229323")
    assert out["success"] is False and out.get("verified") is True
    assert "已成交" in out["message"]


async def test_pusher_cancel_false_dead_order():
    svc, _ = _svc(
        {"success": False, "message": "fail"},
        [
            {
                "side": "buy",
                "status": "rejected",
                "filled_volume": 0,
                "total_volume": 100,
            }
        ],
    )
    out = await svc.cancel_order("601000.SH", "229323")
    assert out["success"] is False and out.get("verified") is True
    assert "终结" in out["message"]


async def test_pusher_cancel_transport_error_but_cancelled_is_reported():
    svc, _ = _svc(
        TdxPushError("桥返回 HTTP 502"),
        [{"side": "cancel", "status": "rejected"}],
    )
    out = await svc.cancel_order("601000.SH", "229323")
    assert out["success"] is True and out.get("verified") is True


async def test_pusher_cancel_transport_error_not_cancelled_reraises():
    svc, _ = _svc(
        TdxPushError("桥返回 HTTP 502"), [{"side": "buy", "status": "submitted"}]
    )
    with pytest.raises(TdxPushError):
        await svc.cancel_order("601000.SH", "229323")


# ---------- TdxBroker.cancel_order_verbose / cancel_order ----------


def _tdx_broker(http_status, http_payload, query_state):
    from backend.services.live_trading.services.broker_client import TdxBroker

    b = TdxBroker(bridge_url="http://bridge.test", bridge_token="t")

    class _Resp:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class _Cli:
        async def post(self, url, json=None, headers=None):
            assert url.endswith("/api/v1/orders/cancel")
            return _Resp(http_status, http_payload)

    async def _get_client():
        return _Cli()

    async def _query(symbol, order_id):
        if isinstance(query_state, Exception):
            raise query_state
        return dict(query_state)

    b._get_client = _get_client
    b._query_order_state = _query
    return b


async def test_tdx_broker_verbose_success():
    b = _tdx_broker(200, {"success": True, "message": "ok"}, {})
    ok, reason = await b.cancel_order_verbose("229323", symbol="601000.SH")
    assert ok is True and reason == "submitted"


async def test_tdx_broker_verbose_verified_cancelled():
    b = _tdx_broker(
        200,
        {"success": False, "message": "fail"},
        {"side": "cancel", "status": "rejected"},
    )
    ok, reason = await b.cancel_order_verbose("229323", symbol="601000.SH")
    assert ok is True and reason == "submitted"


async def test_tdx_broker_verbose_filled_is_counter_rejected():
    b = _tdx_broker(
        200,
        {"success": False, "message": "fail"},
        {"side": "buy", "status": "filled", "filled_volume": 1, "total_volume": 1},
    )
    ok, reason = await b.cancel_order_verbose("229323", symbol="601000.SH")
    assert ok is False and reason == "counter_rejected"


async def test_tdx_broker_verbose_still_active_is_rejected():
    b = _tdx_broker(
        200,
        {"success": False, "message": "fail"},
        {"side": "buy", "status": "submitted"},
    )
    ok, reason = await b.cancel_order_verbose("229323", symbol="601000.SH")
    assert ok is False and reason == "rejected"


async def test_tdx_broker_verbose_http_error_query_down_is_timeout():
    b = _tdx_broker(500, {"error": "TDX_UNAVAILABLE"}, RuntimeError("bridge down"))
    ok, reason = await b.cancel_order_verbose("229323", symbol="601000.SH")
    assert ok is False and reason == "timeout"


async def test_tdx_broker_cancel_order_delegates_to_verbose():
    b = _tdx_broker(
        200,
        {"success": False, "message": "fail"},
        {"side": "cancel", "status": "rejected"},
    )
    assert await b.cancel_order("229323", symbol="601000.SH") is True
