"""决策轮**补块装配**（Stage 2 第一批）：快照/分数 → 提示词增量的取数与口径。

``decision_round.py`` 与 ``decision_round_io.py`` 都已顶到 800 行预算顶（分层守卫
``test_modules_stay_within_the_file_budget_and_layering``），本批「模型分数 +
市场状态进提示词」的装配（要价清单、regime/分数取数、降级留痕、补块文本）独立成
这一块，**与 io 同层**：只许 import core 与 shared（要调 ``RoundDeps`` 上的取数
闭包，所以进不了纯函数 core），不许碰编排/调度/驱动/io。

两条纪律（细节在各函数 docstring，渲染在 ``shared/decision_blocks``）：

1. **降级不拦轮**：regime/分数都是增益块——读失败 → 「本轮不可得」如实渲染并
   留痕审计，绝不静默消失、绝不编 0（``signal_score=0`` 的语义是「垫底」）。
2. **未接线 = 历史字节**：``deps`` 上对应闭包为 ``None``（旧替身）→ 对应块整段
   空串，提示词与升级前逐字节一致（上游金样不破）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from backend.services.trade.services.decision_round_core import TENANT_ID, RoundDeps
from backend.shared.decision.context import PoolQuote
from backend.shared.decision_blocks import (
    attach_signal_scores,
    build_market_state_block,
    build_signal_meta_block,
)
from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)


def load_round_snapshots(
    deps: RoundDeps,
    round_id: str,
    quote_client: Any,
    positions_list: Sequence[Any],
    pool_rows: Sequence[Any],
    quotes_failure: str,
) -> tuple[dict[str, Any] | None, Mapping[str, Mapping[str, Any]], str]:
    """③b+④ 盘中 regime + 行情快照（要价清单装配在编排层之外，见细案）。

    要价清单（suffix 形）= 持仓（原始 payload 直接取 symbol，不必先建持仓行）+
    池 + regime 指数——指数是【盘中市场状态】块「指数现价」行的原料，缺了它
    那一行会如实渲染「未取到」。regime（db0 ``qm:regime:intraday``）是**增益
    块**：未接线/读失败 → ``None``（不拦轮、指数不进清单），降级渲染与留痕在
    :func:`enrich_quotes_with_blocks` 收口。

    快照读取失败同样**不在这里拦轮**（守护轮与已有持仓仍要靠行情；「池子整段
    没有价」能否继续由 ⑦ 的行情闸门判）：原因追加进 ``quotes_failure`` 原样返回。
    """
    regime: dict[str, Any] | None = None
    if deps.load_regime is not None:
        try:
            regime = deps.load_regime()
        except Exception as exc:  # noqa: BLE001 增益块异常按不可得降级
            logger.warning("[DecisionRound] %s 盘中 regime 读取异常: %s", round_id, exc)
    index = str((regime or {}).get("index") or "").strip()
    codes = sorted(
        {
            StockCodeUtil.to_suffix(str(p.get("symbol") or "").strip())
            for p in positions_list
            if isinstance(p, Mapping) and p.get("symbol")
        }
        | {str(r.code) for r in pool_rows}
        | ({StockCodeUtil.to_suffix(index)} if index else set())
    )
    snapped: Mapping[str, Mapping[str, Any]] = {}
    if quote_client is not None and codes:
        try:
            snapped = deps.read_snaps(quote_client, codes) or {}
        except Exception as exc:  # noqa: BLE001 读失败先记因，是否中止见 ⑦ 的行情闸门
            quotes_failure = f"行情快照读取失败：{type(exc).__name__}: {exc}"
            logger.warning("[DecisionRound] %s（按无行情继续）", quotes_failure)
    return regime, snapped, quotes_failure


async def enrich_quotes_with_blocks(
    deps: RoundDeps,
    round_id: str,
    user: str,
    regime: Mapping[str, Any] | None,
    snapped: Mapping[str, Mapping[str, Any]],
    quotes: Sequence[PoolQuote],
    now: datetime,
    notes: list[str],
) -> tuple[tuple[PoolQuote, ...], str, str]:
    """⑦b+⑦c 行情行填「信号分」+ 两个提示词补块。

    返回 ``(填分后的行情, 【盘中市场状态】块, 【盘中模型信号】块)``。口径（细案
    Stage 2 第一批，渲染在 ``shared/decision_blocks``）：

    1. 分数取数只有 ``load_score_snapshot`` 一个入口（``deps.load_scores``）；
       抛异常或 ``ok=False`` → **降级不拦轮**：行内无后缀、信号块如实说
       「不可得」，原因追进 ``notes``（审计）与日志。
    2. 「信号分」= ``rank_pct`` 折算研究评分（0–100 刻度）；**绝不编 0**——
       ``signal_score=0`` 的语义是「垫底/明确不入场」。
    3. 未接线（``None``，旧替身/历史行为）→ 对应块整段空串：提示词逐字节不变。
    """
    score_map: dict[str, dict[str, Any]] = {}
    score_meta: dict[str, Any] = {"ok": False, "reason": "分数快照未接线"}
    scores_wired = deps.load_scores is not None
    if scores_wired:
        try:
            score_map, score_meta = await deps.load_scores(TENANT_ID, user)
        except Exception as exc:  # noqa: BLE001 取数异常按不可得降级（不拦轮）
            logger.warning("[DecisionRound] %s 分数快照读取异常: %s", round_id, exc)
            score_meta = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not score_meta.get("ok"):
            note = f"模型分数不可得（{score_meta.get('reason')}）：行情行内无「信号分」"
            notes.append(note)
            logger.warning("[DecisionRound] %s %s", round_id, note)
    scored_quotes, scored_n = attach_signal_scores(quotes, score_map)

    market_state_block = ""
    if deps.load_regime is not None:
        index = str((regime or {}).get("index") or "").strip()
        index_snap = snapped.get(StockCodeUtil.to_suffix(index)) if index else None
        market_state_block = build_market_state_block(regime, index_snap, now)
        if regime is None:
            note = "盘中 regime 不可得：市场状态块本轮为「不可得」降级"
            notes.append(note)
            logger.warning("[DecisionRound] %s %s", round_id, note)
    signal_meta_block = (
        build_signal_meta_block(score_meta, filled=scored_n, total=len(scored_quotes))
        if scores_wired
        else ""
    )
    return scored_quotes, market_state_block, signal_meta_block
