"""决策轮**取数席**（Stage 2 第二批）：数据文件/远端序列/审计台账/唤醒上下文 → 原料。

``default_round_deps``（io）把本模块五个函数包成闭包注入 ``RoundDeps``；装配层
（``decision_round_blocks``）只消费返回值。为什么独立成一层：io 已顶 800 行预算
（见测试体量守卫），而「文件怎么读、序列怎么摘、台账怎么查」是几套各自的取数口径，
混进 io 会把「谁是当前该用的那一个」的拼装逻辑淹掉。

- :func:`read_news_files` → 丁4 盘中新闻（``news_brief/latest.json`` + 观察文件）
- :func:`read_risk_files` → 风险两槽（``risk_block.json`` / ``industry_risk.json``
  / ``fundamental_flags.json``，cron 在产、键是**裸 6 位码**）
- :func:`series_summaries` → 丁1/丁3 动量窗原料（``market:series`` 远端 ZSET）
- :func:`load_decision_rows` → 丁6 互看 / 丁9 上一轮（审计台账当日行）
- :func:`read_wake_ctx` → 丙1 唤醒说明文本（写侧在 ``tick.wake_tick``）

分层：与 ``blocks`` 同侧（``io`` → ``feeds``），只许往下（``core``/``wake``/
``shared``/simulation 纯助手）——反向拉 io/round/tick/runner 会成环（源码守卫钉死）。
**本模块不 import io**：唤醒上下文的 Redis 客户端由 io 的 ``native_redis_client()``
构造后**当参数递进来**（:func:`read_wake_ctx` 的 ``client``）——写侧（tick）与读侧
必须同库（交易库 db2），这里自造客户端就是「结构性空转」事故的复现路径
（memory: qq-push-3slot-briefing 同款陷阱）。

降级纪律（与 ``blocks``/``decision_risk_blocks`` 同一批）：

1. **文件不存在 → ``None``**（debug 级）；**坏文件 → ``None``**（warning 级）——
   「读不到」与「没有内容」是两件事，降级留痕由装配层进丁8 置顶。
2. **序列逐码尽力**：单码窗内无成员 → 该码不进返回表（绝不返回零值摘要冒充）。
3. **台账读失败上抛**：装配层捕获后丁6/丁9 两块整段不出（防谎报「今日首轮」）。
   读**主库**（``read_only=False``）：当轮写、当轮读，从库滞后会把上一轮藏掉。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from backend.services.simulation.services.redis_series_quote import (
    recent_traded_shares,
    series_key_for,
)
from backend.services.trade.services.decision_round_core import TENANT_ID
from backend.services.trade.services.decision_round_wake import WAKE_CTX_KEY
from backend.shared.decision_blocks import SeriesSummary
from backend.shared.decision_context_source import reports_dir
from backend.shared.remote_quote_config import make_sync_client

logger = logging.getLogger(__name__)

#: 动量参考窗：现价对 (last_ts − N 分钟) 前最近一条成员。
_MOM_LONG_MIN = 30.0
_MOM_SHORT_MIN = 5.0

#: series 读取窗（秒）：只要最近 1 小时，更早的成员对盘中动量无意义。
_SERIES_WINDOW_S = 3600.0


def data_root() -> Path:
    """数据根：``QM_DATA_DIR`` 优先，缺省取报告目录的父级（容器内都是 ``/data``）。

    三个风险文件与 ``news_brief/`` 都挂在数据根下；``QM_REPORTS_DIR`` 与数据根
    的父子关系是部署契约（``docker-compose.yml`` 同一卷）。
    """
    env = str(os.getenv("QM_DATA_DIR") or "").strip()
    return Path(env) if env else reports_dir().parent


def _load_json(path: Path) -> Mapping[str, Any] | None:
    """JSON 文件 → 对象；不存在/坏文件/非对象 → ``None``（级别区分见模块 docstring）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("[DecisionRound] 数据文件不存在: %s", path)
        return None
    except OSError as exc:
        logger.warning("[DecisionRound] 数据文件读取失败 %s: %s", path, exc)
        return None
    try:
        doc = json.loads(text)
    except ValueError as exc:
        logger.warning("[DecisionRound] 数据文件解析失败 %s: %s", path, exc)
        return None
    if not isinstance(doc, Mapping):
        logger.warning(
            "[DecisionRound] 数据文件不是 JSON 对象 %s（%s）", path, type(doc).__name__
        )
        return None
    return doc


def read_news_files(
    root: Path | None = None,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """``(简报, 观察文件)``；缺谁谁 ``None``（丁4 据此如实降级，见 ``blocks``）。"""
    base = (root or data_root()) / "news_brief"
    return _load_json(base / "latest.json"), _load_json(base / "intraday_watch.json")


def read_risk_files(
    root: Path | None = None,
) -> tuple[
    Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None
]:
    """``(risk_block, industry_risk, fundamental_flags)``；缺谁谁 ``None``。"""
    base = root or data_root()
    return (
        _load_json(base / "risk_block.json"),
        _load_json(base / "industry_risk.json"),
        _load_json(base / "fundamental_flags.json"),
    )


def _member_payload(raw: Any) -> Mapping[str, Any] | None:
    """series 成员（JSON 串）→ 对象；坏成员 → ``None``。"""
    if isinstance(raw, Mapping):
        return raw
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, Mapping) else None


def _positive(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (out > 0) or out in (float("inf"), float("nan")):
        return None
    return out


def _summarize(rows: list[tuple[str, float]]) -> SeriesSummary | None:
    """窗口成员（score 升序）→ :class:`SeriesSummary`；末条不可验 → ``None``。

    动量为 ``None`` 不是错误（窗口不足）：渲染端按 ``—`` 如实出，绝不编 0。
    """
    if not rows:
        return None
    payload = _member_payload(rows[-1][0])
    if payload is None:
        return None
    last_ts = float(rows[-1][1])
    last_price = _positive(payload.get("price"))
    if last_price is None:
        return None

    def _ref_pct(minutes: float) -> float | None:
        """(last_ts − N 分钟) 前最近一条的价 → 涨跌 %；窗内没有 → ``None``。"""
        cutoff = last_ts - minutes * 60.0
        ref: float | None = None
        for raw, score in rows:
            if score > cutoff:
                break
            price = _positive((_member_payload(raw) or {}).get("price"))
            if price is not None:
                ref = price
        if ref is None:
            return None
        return round((last_price - ref) / ref * 100.0, 4)

    vol_rows = [
        (raw, score) for raw, score in rows if score >= last_ts - _MOM_SHORT_MIN * 60.0
    ]
    return SeriesSummary(
        last_ts=last_ts,
        last_price=last_price,
        mom30m_pct=_ref_pct(_MOM_LONG_MIN),
        mom5m_pct=_ref_pct(_MOM_SHORT_MIN),
        vol5m_shares=recent_traded_shares(vol_rows),
        source=str(payload.get("source") or ""),
    )


def series_summaries(
    codes: Sequence[str],
    *,
    now_ts: float | None = None,
    client: Any = None,
) -> Mapping[str, SeriesSummary]:
    """持仓码 → ``market:series`` 摘要表（丁1 涨速 / 丁3 分钟特征的原料）。

    一条管线读全部键（持仓规模个位数，无批量压力）；**任何一环失败都不抛**——
    序列是增益面，装配层对空表有降级渲染与留痕。``client=None`` 时现造
    （``make_sync_client``，未配置远端行情 → 空表）。
    """
    keys = {str(code): key for code in codes if (key := series_key_for(str(code)))}
    if not keys:
        return {}
    own = client is None
    if own:
        try:
            client = make_sync_client()
        except Exception as exc:  # noqa: BLE001 构造失败按不可得降级
            logger.warning("[DecisionRound] 远端行情客户端构造失败: %s", exc)
            return {}
    if client is None:
        return {}
    now_s = float(now_ts) if now_ts is not None else time.time()
    try:
        pipe = client.pipeline()
        for key in keys.values():
            pipe.zrangebyscore(key, now_s - _SERIES_WINDOW_S, "+inf", withscores=True)
        raw_results = pipe.execute()
    except Exception as exc:  # noqa: BLE001 读失败按不可得降级（不拦轮）
        logger.warning("[DecisionRound] 行情序列读取失败: %s", exc)
        raw_results = []
    finally:
        if own:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    out: dict[str, SeriesSummary] = {}
    for code, rows in zip(keys, raw_results, strict=False):
        summary = _summarize(list(rows or []))
        if summary is not None:
            out[code] = summary
    return out


async def load_decision_rows(trade_date: date | str, user: str) -> Sequence[Any]:
    """当日审计行（丁6/丁9 原料）。过滤口径 = 写侧 ``build_records``（tenant+user）。

    读**主库**（``read_only=False``）：上一轮的结果当轮可见是块的全部意义，从库
    滞后会把「上一轮」藏成「今日首轮」——谎报比缺块危险。失败**上抛**由装配层
    收口（两块整段不出 + 丁8 留痕）。
    """
    from backend.shared.database_manager_v2 import get_session
    from backend.shared.decision_ledger_store import load_day

    async with get_session(read_only=False) as session:
        return await load_day(session, trade_date, tenant_id=TENANT_ID, user_id=user)


def read_wake_ctx(client: Any, round_id: str) -> str:
    """丙1 唤醒说明文本（合成槽轮开跑前由 ``wake_tick`` SET）；无记录/读失败 → ``""``。

    ``client`` 由 io 侧用 ``native_redis_client()``（交易库 db2）构造后传入——
    与写侧同库是契约（见模块 docstring）。
    """
    if client is None or not round_id:
        return ""
    try:
        raw = client.get(WAKE_CTX_KEY.format(round_id=round_id))
    except Exception as exc:  # noqa: BLE001 读失败按非唤醒轮降级
        logger.warning("[DecisionRound] 唤醒上下文读取失败 %s: %s", round_id, exc)
        return ""
    return str(raw or "")
