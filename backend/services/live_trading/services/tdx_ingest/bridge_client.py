"""通达信桥采集专用客户端——独立于 ``tdx_push_service`` 的 10s/无校验口径。

为什么不复用 ``tdx_push_service.tdx_call``（三条都是探针实测出来的差异）
-----------------------------------------------------------------------
1. **超时**：推送面 10s 够用；本采集有大调用（more_info 单股、gp 500 批量），
   探针实测正常情况下 3s 内，但桥被其他大调用按停时 30s+ 是常态（2026-10-15
   实测：zdt 2000 超时后紧随的 pricevol 连续 30s+ 超时约 90s）。本客户端读超时
   默认 **60s**（env ``TDX_INGEST_TIMEOUT_S``），宁可等，不可半路放弃把已在
   柜台/队列里的调用当失败重发。
2. **失败信封**：``tdx_push_service`` 把桥 ``success:false`` 折叠成
   ``{"result": None}``，调用方**无法区分**失败与「结果就是 None」。本客户端
   把 None 一律当**失败**抛出，并带 kind 分类。
3. **86400s 内存缓存**：桥对 ``get_more_info/get_stock_info/get_financial_data``
   （86400s）与 ``get_stock_list/get_sector_list``（3600s）有内存缓存，缓存键 =
   原始 params JSON，且键在参数过滤**之前**建立（``client.py:177-190`` 的
   ``call()`` 用 ``inspect.signature`` 过滤 kwargs，签名外参数被静默丢弃）。
   ⇒ 采集调用全部自动附加签名外参数 ``_nc``（纳秒时间戳）：缓存键改变 → 每次
   都真调 tqcenter；而实际方法调用不受影响（已读桥源码验证）。

错误分类（kind）——决定调用侧怎么处理
------------------------------------
* ``network``：连接/读超时等传输层错误 → 可重试（本客户端自动重试 1 次）；
* ``rate_limited``：HTTP 429 / RATE_LIMITED → 交 :class:`RateBudget` 退避降速；
* ``blocked``：``json has no table_list``（GP/BK/SC/GO/FN 家族缺客户端数据包）
  ——**环境前置未满足，重试无意义**，数据集状态置 blocked 等用户下载数据包；
* ``bridge``：其余桥侧失败（ErrorId != "0"、success:false、result=None）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: 读超时（秒）——探针实测建议 60s（桥队列被大调用按停时 30s+ 是常态）。
DEFAULT_TIMEOUT_S = 60.0

#: 连接超时（秒）：局域网桥，连不上应快速失败。
_CONNECT_TIMEOUT_S = 10.0

#: 桥限流与「数据包未下载」的文本标记（HTTP body / ErrorId message 内探测）。
_RATE_LIMIT_MARK = "RATE_LIMITED"
_BLOCKED_MARK = "table_list"

#: 传输层错误自动重试次数（除首次外）。
_NETWORK_RETRIES = 1

#: 签名外参数名：附加纳秒时间戳以改变桥内存缓存键（见模块 docstring）。
_CACHE_BUSTER = "_nc"


class BridgeError(Exception):
    """桥调用失败。``kind`` ∈ network|rate_limited|blocked|bridge。"""

    def __init__(self, kind: str, message: str, *, method: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.method = method


def _resolve_bridge() -> tuple[str, str]:
    """调用时解析桥地址/token（settings 运行时覆盖 > env > 默认）。

    trade 进程里 ``PUT /tdx/config`` 会同时改 ``settings`` 与 ``os.environ``，
    且启动期 ``apply_runtime_config()`` 把 Redis 持久化配置灌回两处——所以
    每次调用现取即可拿到最新配置，不必缓存。
    """
    url = token = ""
    try:
        from backend.services.trade_shared.trade_config import settings

        url = str(getattr(settings, "TDX_BRIDGE_URL", "") or "").strip()
        token = str(getattr(settings, "TDX_BRIDGE_TOKEN", "") or "").strip()
    except Exception:  # noqa: BLE001 - 独立运行（脚本）时无 settings 也照常
        pass
    url = url or os.getenv("TDX_BRIDGE_URL", "http://127.0.0.1:8550")
    token = token or os.getenv("TDX_BRIDGE_TOKEN", "")
    return url.rstrip("/"), token


def _classify_error_text(text: str) -> str:
    """失败文本 → kind（rate_limited / blocked / bridge 三选一）。"""
    if _RATE_LIMIT_MARK in text:
        return "rate_limited"
    if _BLOCKED_MARK in text:
        return "blocked"
    return "bridge"


class TdxBridgeClient:
    """httpx 异步客户端（懒建、可复用；每次调用自动加 ``_nc`` 破缓存）。"""

    def __init__(self, *, timeout_s: float | None = None) -> None:
        if timeout_s is None:
            try:
                timeout_s = float(
                    str(os.getenv("TDX_INGEST_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
                )
            except (TypeError, ValueError):
                timeout_s = DEFAULT_TIMEOUT_S
        self._timeout_s = max(5.0, float(timeout_s))
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=_CONNECT_TIMEOUT_S)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict:
        """调一次桥方法，返回 ``result`` 字典（None/ErrorId!=0 一律抛 BridgeError）。

        入参 ``params`` 不被修改（拷贝后附加 ``_nc``）。
        """
        base_url, token = _resolve_bridge()
        if not base_url or not token:
            raise BridgeError("bridge", "TDX_BRIDGE_URL/TOKEN 未配置", method=method)
        body = dict(params or {})
        body[_CACHE_BUSTER] = str(time.time_ns())
        url = f"{base_url}/api/v1/tdx/call"
        headers = {"Authorization": f"Bearer {token}"}

        last_exc: Exception | None = None
        for attempt in range(_NETWORK_RETRIES + 1):
            try:
                resp = await self._http().post(
                    url, json={"method": method, "params": body}, headers=headers
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < _NETWORK_RETRIES:
                    logger.warning(
                        "[TdxIngest] 桥调用 %s 传输失败（重试 1 次）: %s", method, exc
                    )
                    await asyncio.sleep(2.0)
                    continue
                raise BridgeError(
                    "network", f"{method} 传输失败: {exc}", method=method
                ) from exc
            return self._parse_response(method, resp)
        raise BridgeError("network", f"{method} 传输失败: {last_exc}", method=method)

    def _parse_response(self, method: str, resp: httpx.Response) -> dict:
        """HTTP 响应 → result 字典；一切失败路径抛 BridgeError（带 kind）。"""
        # 失败状态码：桥统一 json_error 信封 {success:false, error:{code,message}}
        if resp.status_code != 200:
            detail = ""
            code = ""
            try:
                payload = resp.json()
                err = payload.get("error") or {}
                code = str(err.get("code") or "")
                detail = str(err.get("message") or "")
            except Exception:  # noqa: BLE001 - 非 JSON body 退到原始文本
                detail = resp.text[:300]
            text = f"{code} {detail}"
            kind = _classify_error_text(text)
            if code == _RATE_LIMIT_MARK or resp.status_code == 429:
                kind = "rate_limited"
            raise BridgeError(
                kind, f"{method} HTTP {resp.status_code}: {text.strip()}", method=method
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BridgeError(
                "bridge", f"{method} 响应非 JSON: {exc}", method=method
            ) from exc

        if not payload.get("success"):
            text = str(payload.get("error") or payload)[:300]
            raise BridgeError(
                _classify_error_text(text), f"{method} 桥失败: {text}", method=method
            )
        result = payload.get("result")
        if result is None:
            # 探针教训：success:true 但 result=None 是桥折叠失败信封的形态
            raise BridgeError(
                "bridge", f"{method} 返回 result=None（失败折叠）", method=method
            )
        if isinstance(result, dict):
            error_id = str(result.get("ErrorId", "0") or "0")
            if error_id not in ("0", ""):
                text = str(result)[:300]
                raise BridgeError(
                    _classify_error_text(text),
                    f"{method} ErrorId={error_id}: {text}",
                    method=method,
                )
        return result


__all__ = ["BridgeError", "DEFAULT_TIMEOUT_S", "TdxBridgeClient"]
