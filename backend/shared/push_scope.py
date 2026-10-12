"""QQ 推送面范围过滤：只推「当前选定券商的实盘持仓」（唯一实现）。

背景（2026-10-12 用户指令）：QQ 告警在推三路并集（模拟盘 ∪ 实盘 ∪ 手工自选，
实测 140 只），且**不区分券商**——选定 TDX 时仍在推 QMT 的 54 只；用户要求
「实盘有的股票才推送、切换通达信就推通达信的、切 QMT 就推 QMT 的」。口径：

* 范围集 = **选定券商**（``broker:selected:CN``，db2）快照的持仓代码
  （prefix 归一化）。切 TDX 推 TDX 的仓，切 QMT 推 QMT 的仓；
* 模拟盘/手工自选的标的**站内照旧**（交易台卡片、留痕表、实时情报面板全量
  可查），QQ 面不再**即时**出现——2026-10-12 拍板重规划后，非持仓/大盘情报
  改走「情报速览」档期缓冲（08:35/11:35/15:15 卡片合集），不再逐条即时推；
* 选定券商快照缺失/停更 → 回退「实盘各源并集」并留 WARNING——账户停更另有
  一级健康告警，推送面不静默吞；选定源在线且确实空仓 → 空集（没有实盘股票
  就什么都不推），与「读不到」是两个语义；
* 无任何实盘快照 / 读取失败 → ``None``（不设闸，保持旧行为，留 WARNING）
  ——过滤故障不许把告警面打成全哑。

消费方（两个 QQ 摘要出口；站内面不经过本模块）：
* ``holding_sentinel._persist`` → 「持仓预警」摘要（按 (tenant,user) 分账）；
* ``sentinel_alert_service._decide_push`` → 范围**内**走「📌 持仓情报」即时
  摘要（收件人=admin）；范围**外**与市场级事件 → 情报速览档期缓冲
  （``qq_briefing``，2026-10-12 重规划）。

kill switch：``QM_QQ_LIVE_SCOPE``（唯一读法 ``env_flags.env_flag``，默认开；
词表只认小写 ``true``——写 ``false`` 或任何其它值即关）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from backend.shared.env_flags import env_flag
from backend.shared.logging_config import get_logger
from backend.shared.real_positions import active_broker_type
from backend.shared.signal_scores import normalize_position_symbol

logger = get_logger(__name__)

#: 范围闸开关（默认开）：规范词表只认小写 true（见 env_flags 模块注释）。
ENV_FLAG = "QM_QQ_LIVE_SCOPE"

#: 持仓集缓存 TTL（秒）：快照 30s 级刷新，推送面 60s 粒度足够；持仓哨兵按用户、
#: 情报面按 admin 各调一次/轮，缓存把库读摊平（失败(None) 也缓存——库故障时
#: 不许每行告警都怼一次读库 + WARNING 风暴）。
_CACHE_TTL_S = 60.0

_cache: dict[tuple[str, str], tuple[float, set[str] | None]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """清空缓存（测试用；生产无需调用——TTL 自然过期）。"""
    with _cache_lock:
        _cache.clear()


def _load_positions(tenant_id: str, user_id: str) -> tuple[dict[str, dict], dict]:
    """同步读实盘持仓：sync_db 会话 + real_positions 的同一段 SQL / 同一份 merge。

    本函数在 trade 进程（告警线程）里同步执行（异步栈在 worker 线程不可用，
    见 sync_db 模块注释）。**不许另写 SQL**：多源并集 / 相对停更剔除 / 同票取量
    的唯一实现是 ``merge_real_sources``，这里只换执行会话。
    """
    from sqlalchemy import text

    from backend.shared.real_positions import _SNAPSHOT_SQL, merge_real_sources
    from backend.shared.sync_db import sync_session

    with sync_session() as session:
        rows = session.execute(
            text(_SNAPSHOT_SQL), {"tid": tenant_id, "uid": user_id}
        ).fetchall()
    return merge_real_sources(
        [(str(r[0]), r[1], r[2]) for r in rows],
        active_source=active_broker_type(),
    )


def live_symbols_for_push(tenant_id: str, user_id: str) -> set[str] | None:
    """QQ 推送面放行集：选定券商实盘持仓（prefix 归一化）。``None``=不设闸。

    语义见模块文档；**绝不抛**——出任何错都按「不设闸」处理并留 WARNING，
    唯一例外是显式关闸（env）直接返回 ``None``。
    """
    if not env_flag(ENV_FLAG, default=True):
        return None
    key = (str(tenant_id or ""), str(user_id or ""))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    try:
        positions, meta = _load_positions(*key)
        # merge_real_sources 的 meta["active_broker"] 已是**快照源名**
        # （tdx_bridge/qmt_exec），与 positions 行的 sources 同一词表。
        active_src = str((meta or {}).get("active_broker") or "") or None
        sources_meta = (meta or {}).get("sources") or {}
        entry = sources_meta.get(active_src) if active_src else None
        if isinstance(entry, dict) and not entry.get("stale"):
            allowed: set[str] | None = {
                sym
                for sym, pos in positions.items()
                if active_src in (pos.get("sources") or [pos.get("source")])
            }
        else:
            allowed = set(positions) if positions else None
            if active_src and allowed is not None:
                logger.warning(
                    "[push_scope] 选定券商快照 %s 缺失/停更，QQ 面回退实盘并集（%d 只）",
                    active_src,
                    len(allowed),
                )
    except Exception as exc:  # noqa: BLE001 - 过滤故障不许把告警面打成全哑
        logger.warning("[push_scope] QQ 推送范围读取失败，本轮不设闸: %s", exc)
        allowed = None
    with _cache_lock:
        _cache[key] = (now, allowed)
    return allowed


def symbol_allowed_for_push(symbol: Any, allowed: set[str] | None) -> bool:
    """标的级放行判定（纯函数）。``allowed=None`` 不设闸。

    * 无标的（``''``/``'*'``）：放行——市场级事件不是「某只股票的推送」，
      不在本闸语义内；
    * 非 A 股规范形（港股/美股/解析不出）：拒——本账户体系只承 A 股实盘
      持仓（归一同样返回 None），这类标的必然不在放行集里；
    * 其余：归一后查放行集。
    """
    if allowed is None:
        return True
    raw = str(symbol or "").strip()
    if not raw or raw == "*":
        return True
    sym = normalize_position_symbol(raw)
    return bool(sym) and sym in allowed
