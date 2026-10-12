"""QQ 情报速览（2026-10-12 用户拍板重规划）——非持仓情报只在固定档期合集送达。

背景：QQ 推送新闻/情报过多（实测近 2 天 891 条告警、139 条推达），且碎片化
（16:18 还在单发「1 条」）。用户拍板三级重规划：

* **持仓相关** → 仍即时推（走 ``qq_digest`` 窗口摘要，行带「· 利空/利好」类别）；
* **非持仓/大盘情报** → 不即时推，进本模块的**档期缓冲**，每日 3 档合集：
  ``08:35 盘前 / 11:35 午间 / 15:15 盘后``（北京时间）；
* 排版=**卡片式**（用户选定样张）::

    📰 情报速览 · 午间 · 11:35 · 24条
    ━━━━━━━━━━━━━━
    ▼ 贵州茅台(600519) 批价下调传闻 · 利空
    ▲ 宁德时代(300750) 海外储能大单 · 利好
    ━━━━━━━━━━━━━━
    另有 20 条 · 交易台 › 实时情报

口径：
* 缓冲在 **Redis**（``qm:qq:briefing:{market}`` 列表），进程重启不丢；写入
  永不抛（旁路不许掀翻哨兵主链）。读侧（flush）失败返回 ``None``，由调用方
  决策**不落 done 键**、下个周期重试——绝不把「读不到」渲染成「没有」。
* **新鲜度上限** ``BRIEFING_MAX_AGE_S``（20h）：>上限的条目在 flush 时丢弃。
  20h 覆盖正常隔夜（15:15 → 次日 08:35 = 17.3h），又让周末的旧闻在周一
  盘前档自然过期（周五 15:15 → 周一 08:35 = 65h）。
* **排序 = 利空/风险优先**（用户拍板「利空优先」）：风险 > 利空/下行 >
  一般 > 利好 > 大盘；同档按时间倒序（新的在前）。行文本相同者去重。
* 展示上限 ``BRIEFING_MAX_LINES``=15，超出折叠「另有 N 条 · {尾注}」。
* 纯文本优先：QQ 平台对 markdown 常拒（实测 ``markdown not allowed``），
  卡片分隔用 ``━`` 字符，不用任何 markdown 语法。
* 档期判定与交易日门在调用方（``intel_briefing_task``，trade 常驻任务）：
  本模块只认「现在几点、该发哪个档」与组合/冲刷，方便纯函数测试。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: 每日 3 档（北京时间）。键用于 done 键与测试；hhmm 用于卡片标题。
BRIEFING_SLOTS: tuple[tuple[str, str, str, int, int], ...] = (
    ("morning", "盘前", "08:35", 8, 35),
    ("noon", "午间", "11:35", 11, 35),
    ("close", "盘后", "15:15", 15, 15),
)

#: 明细行展示上限（超出折叠）。
BRIEFING_MAX_LINES = 15
#: 单市场缓冲条数上限（防风暴占内存；只留最新）。
BRIEFING_MAX_BUFFERED = 500
#: 新鲜度上限：超龄条目 flush 时丢弃（覆盖隔夜 17.3h；掐掉周末旧闻）。
BRIEFING_MAX_AGE_S = 20 * 3600.0
#: 卡片分隔线（纯文本，14 个全角横 —— 与用户选定样张一致）。
BRIEFING_SEPARATOR = "━" * 14
#: 卡片尾注（与 qq_digest 的 footer 同风格）。
BRIEFING_FOOTER = "交易台 › 实时情报"
#: 缓冲键前缀 / done 键前缀。
_BUFFER_KEY_PREFIX = "qm:qq:briefing:"
_DONE_KEY_PREFIX = "qm:qq:briefing:done:"
#: done 键存活（跨日自动过期；48h 足够覆盖补跑窗口）。
DONE_TTL_S = 48 * 3600

#: 排序档位：风险 > 利空/下行 > 一般 > 利好 > 大盘（数值小者先展示）。
_PRI_RISK = 0
_PRI_NEGATIVE = 1
_PRI_NEUTRAL = 2
_PRI_POSITIVE = 3
_PRI_REGIME = 4


@dataclass(frozen=True)
class BriefingSlot:
    key: str
    label: str
    hhmm: str
    hour: int
    minute: int


def _slot(raw: tuple[str, str, str, int, int]) -> BriefingSlot:
    key, label, hhmm, hour, minute = raw
    return BriefingSlot(key=key, label=label, hhmm=hhmm, hour=hour, minute=minute)


def latest_due_slot(now_hhmm: tuple[int, int]) -> BriefingSlot | None:
    """当天「最近已到点」的档；未到第一档返回 None。**纯函数**。

    只认最近一档：服务中途启动（如 16:00 部署）不会把早/午/盘后三档连着发
    三遍——错过档期的条目留在缓冲里，由下一档合集兜住（有新鲜度上限）。
    """
    due: BriefingSlot | None = None
    for raw in BRIEFING_SLOTS:
        cand = _slot(raw)
        if (now_hhmm[0], now_hhmm[1]) >= (cand.hour, cand.minute):
            due = cand
    return due


def _priority(alert_type: str, direction: str) -> int:
    atype = str(alert_type or "").strip()
    if atype == "news:risk_event":
        return _PRI_RISK
    if atype == "news:negative" or str(direction or "").strip() == "down":
        return _PRI_NEGATIVE
    if atype == "regime":
        return _PRI_REGIME
    if atype == "news:positive" or str(direction or "").strip() == "up":
        return _PRI_POSITIVE
    return _PRI_NEUTRAL


def build_entry(
    *,
    line: str,
    alert_type: str = "",
    direction: str = "",
    ts: float | None = None,
) -> dict[str, Any]:
    """一条速览条目（JSON 可序列化）。**纯函数**。"""
    return {
        "ts": float(ts if ts is not None else time.time()),
        "line": str(line or "").strip(),
        "pri": _priority(alert_type, direction),
        "type": str(alert_type or "").strip(),
    }


def buffer_key(market: str) -> str:
    return _BUFFER_KEY_PREFIX + (str(market or "CN").strip().lower() or "cn")


def done_key(market: str, day: str, slot_key: str) -> str:
    """档期完成标记：``qm:qq:briefing:done:{market}:{YYYYMMDD}:{slot}``。"""
    return f"{_DONE_KEY_PREFIX}{str(market or 'CN').strip().lower()}:{day}:{slot_key}"


def _market_channel(market: str) -> str:
    m = str(market or "CN").strip().upper()
    if m in ("HK", "US"):
        return m.lower()
    return "default"


def enqueue_briefing(
    client: Any,
    market: str,
    *,
    line: str,
    alert_type: str = "",
    direction: str = "",
    ts: float | None = None,
) -> bool:
    """非持仓情报入缓冲。**永不抛**（旁路不许掀翻哨兵主链）；返回是否入队。"""
    text = str(line or "").strip()
    if not text:
        return False
    try:
        entry = build_entry(
            line=text, alert_type=alert_type, direction=direction, ts=ts
        )
        key = buffer_key(market)
        payload = json.dumps(entry, ensure_ascii=False)
        pipe = client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -BRIEFING_MAX_BUFFERED, -1)
        pipe.expire(key, 7 * 24 * 3600)
        pipe.execute()
        return True
    except Exception as exc:  # noqa: BLE001 - 入队失败只记日志，站内/留痕不受影响
        logger.warning("[QQBriefing] 速览入队失败: %s", exc)
        return False


def _load_entries(client: Any, market: str) -> list[dict[str, Any]] | None:
    """读缓冲（不删）；读失败返回 None ≠ 空列表。"""
    try:
        raw_items = client.lrange(buffer_key(market), 0, -1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[QQBriefing] 速览读取失败: %s", exc)
        return None
    entries: list[dict[str, Any]] = []
    for raw in raw_items or []:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            continue  # 脏条目跳过，绝不因为一条坏数据丢一整档
        if isinstance(item, dict) and str(item.get("line") or "").strip():
            entries.append(item)
    return entries


def _fresh_ranked(
    entries: Sequence[Mapping[str, Any]], *, now_ts: float
) -> list[Mapping[str, Any]]:
    """过滤超龄 + 去重（同行留新）+ 排序（利空/风险优先，档内新在前）。"""
    fresh = [
        e for e in entries if now_ts - float(e.get("ts") or 0) <= BRIEFING_MAX_AGE_S
    ]

    def _rank(e: Mapping[str, Any]) -> tuple[int, float]:
        # 注意：pri=0 是合法档位（风险），绝不能用 `or` 兜底——会被吞成一般档
        pri = e.get("pri")
        return (
            int(pri) if pri is not None else _PRI_NEUTRAL,
            -float(e.get("ts") or 0),
        )

    ranked = sorted(fresh, key=_rank)
    seen: set[str] = set()
    deduped: list[Mapping[str, Any]] = []
    for entry in ranked:
        text = str(entry.get("line") or "").strip()
        if text in seen:
            continue
        seen.add(text)
        deduped.append(entry)
    return deduped


def compose_briefing(
    *,
    market: str,
    market_label: str,
    slot: BriefingSlot,
    entries: Sequence[Mapping[str, Any]],
    now_ts: float | None = None,
    max_lines: int = BRIEFING_MAX_LINES,
) -> tuple[str, str] | None:
    """（标题, 正文）卡片；无新鲜条目返回 None。**纯函数**（时间可注入）。"""
    now = float(now_ts if now_ts is not None else time.time())
    ranked = _fresh_ranked(entries, now_ts=now)
    if not ranked:
        return None
    total = len(ranked)
    shown = ranked[: max(1, int(max_lines))]
    hidden = total - len(shown)
    market_cn = str(market or "CN").strip().upper()
    prefix = "" if market_cn == "CN" else f"{market_label} · "
    title = f"📰 情报速览 · {prefix}{slot.label} · {slot.hhmm} · {total}条"
    tail = f"另有 {hidden} 条 · " if hidden > 0 else ""
    body = "\n".join(
        [
            BRIEFING_SEPARATOR,
            *[str(e.get("line") or "").strip() for e in shown],
            BRIEFING_SEPARATOR,
            f"{tail}{BRIEFING_FOOTER}",
        ]
    )
    return title, body


def flush_market(
    client: Any,
    market: str,
    slot: BriefingSlot,
    *,
    market_label: str = "",
    now_ts: float | None = None,
) -> int | None:
    """冲刷单市场缓冲并用卡片发 QQ。

    返回：发出则 ≥1（明细条数）；缓冲空/全超龄返回 0（调用方可落 done）；
    读失败返回 **None**（调用方**不得**落 done，下周期重试）。永不抛。
    """
    entries = _load_entries(client, market)
    if entries is None:
        return None
    now = float(now_ts if now_ts is not None else time.time())
    fresh = _fresh_ranked(entries, now_ts=now)
    # 无论新鲜与否，读到的缓冲都已消费——否则超龄条目永远堵在队头。
    try:
        client.delete(buffer_key(market))
    except Exception as exc:  # noqa: BLE001 - 删不掉最多下次超龄再清
        logger.warning("[QQBriefing] 速览清队失败: %s", exc)
    if not fresh:
        return 0
    composed = compose_briefing(
        market=market,
        market_label=market_label,
        slot=slot,
        entries=entries,
        now_ts=now,
    )
    if composed is None:  # 理论不可达（fresh 非空则 compose 必出）；防御性如实返回
        return 0
    title, body = composed
    try:
        from backend.shared.qq_notify import notify_async

        notify_async(title, body, channel=_market_channel(market))
    except Exception as exc:  # noqa: BLE001 - 发送失败只记日志
        logger.warning("[QQBriefing] 速览发送失败: %s", exc)
        return None
    return len(fresh)
