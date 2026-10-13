"""api 代理超时解析：慢路径（precheck/preflight/account）必须放宽读超时。

回归背景（2026-10-13）：全局读超时 10s，而 `/trading-precheck` 实测 18.7s、
`/account` 27.2s，日志出现 34 / 104 次 `TRADE PROXY FINAL FAILURE ... ReadTimeout`——
前端把**慢而正确**的响应看成超时失败。慢路径名单此前只放行 `/manual-executions`。
"""

from __future__ import annotations

import pytest

from backend.services.api.routers import trade_proxy


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/real-trading/trading-precheck",
        "/api/v1/real-trading/preflight",
        "/api/v1/real-trading/account",
        "/api/v1/real-trading/manual-executions/preview",
    ],
)
def test_slow_paths_get_relaxed_read_timeout(path):
    timeout = trade_proxy._resolve_proxy_timeout(path)
    assert timeout.read == trade_proxy._SLOW_PATH_TIMEOUT_SECONDS
    assert timeout.read > 10.0, "慢路径必须显著高于全局 10s"
    assert timeout.connect == 3.0, "连接超时保持收紧，不许跟着放宽"


def test_fast_path_keeps_global_read_timeout():
    timeout = trade_proxy._resolve_proxy_timeout("/api/v1/real-trading/status")
    assert timeout.read == 10.0
    assert timeout.connect == 3.0
