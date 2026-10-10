"""统计派 · 入选因子相关性去重 + 稳定因子身份（移植 Tier 3 / T-S3-03）。

两件事：

1. **值级相关性去重**（:func:`dedup_selected`）——入选集内两两「逐日截面
   Spearman 的时间均值」（``ic_stats.daily_rank_corr``，与 IC 同族口径）。
   按 |RankIC| 降序贪心：与任何**已保留**者 |ρ| ≥ ``reject``（0.9，与
   ``rd_mined_materialize`` 的值级去重同阈值）→ 拒；``[warn, reject)`` →
   保留但记警。**不补位**：Top-K 是闸不是指标，被去重挤掉就是挤掉。
   ρ 算不出的对（无重叠日/全并列）按「无证据」处理：保留并以 NaN 记账，
   绝不把「没算出相关」当「不相关」之外的结论。

2. **稳定因子身份**（:func:`stable_factor_id`）——与 P3 任务指纹合流：
   ``md5(statistical:user:market:universe:candidate_key:task_fingerprint)``。
   同一份挖掘配置（P3 指纹相同）重跑 → 同一 ``factor_id`` → 落库走
   ``ON CONFLICT DO UPDATE`` **幂等刷新**，不再每次重跑堆一行重复因子；
   不同配置 = 不同实验，各留一行（跨版本同候选的值级查重仍归物化器，
   那是离线全库操作，不在这里做）。无指纹（手工直跑/旧任务）如实退回
   任务级身份（``scheme="task"``），metadata 记录 scheme，可追溯。

   为什么不再挂 ``factor_identity.find_duplicate``：统计派候选是**固定目录**
   （28 个 key 恒定），名称天然跨任务重复——用名称级查重会把「同候选的
   新一次实验」全部判重拒掉。统计派的正确语义是「同一实验幂等刷新 +
   值级去重挤重复」，不是「名字重复就不许落库」。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ic_stats as _ic

#: 值级去重阈值（与 ``rd_mined_materialize`` 的 0.9 拒 / 0.8 警同口径）
DEFAULT_CORR_REJECT = 0.9
DEFAULT_CORR_WARN = 0.8

#: 稳定身份前缀（与 RD-Agent 系因子的 id 空间区分开）
IDENTITY_PREFIX = "statistical"
#: 身份口径：config = 由 P3 任务指纹定身份；task = 无指纹退回任务级
SCHEME_CONFIG = "config"
SCHEME_TASK = "task"


@dataclass(frozen=True)
class DedupReport:
    """一次入选集去重的全量记录（保留序 = 原选择序）。"""

    kept: tuple[str, ...]
    #: (key, matched_key, ρ)——被拒者及其命中对象
    dropped: tuple[tuple[str, str, float], ...]
    #: (key_a, key_b, ρ)——保留但对内 ρ ∈ [warn, reject)
    warnings: tuple[tuple[str, str, float], ...]
    n_pairs: int  # 实际评估过的对数（含算不出 ρ 的对）
    reject: float = DEFAULT_CORR_REJECT
    warn: float = DEFAULT_CORR_WARN
    #: 保留 key → 与其他保留者的最大 |ρ|（对称口径；算不出 → 不在表里）
    max_corr: dict[str, float] = field(default_factory=dict)

    def max_abs_corr(self, key: str) -> float:
        """该保留因子与其他保留因子的最大 |ρ|（单因子入选/算不出 → NaN）。"""
        value = self.max_corr.get(key)
        return float(value) if value is not None else float("nan")

    def warn_note(self, key: str) -> str:
        """该保留因子的相关性警语（无 → 空串）。"""
        hits = [
            (other if key == a else a, rho)
            for a, other, rho in self.warnings
            if key in (a, other)
        ]
        if not hits:
            return ""
        other, rho = max(hits, key=lambda item: abs(item[1]))
        return f"与 {other} 相关偏高（ρ={rho:.2f}，阈值 {self.reject:g}）"


def dedup_selected(
    factors: Mapping[str, pd.Series],
    *,
    order: Sequence[str],
    reject: float = DEFAULT_CORR_REJECT,
    warn: float = DEFAULT_CORR_WARN,
    min_names: int = _ic.DEFAULT_MIN_NAMES_PER_DAY,
) -> DedupReport:
    """入选因子按 ``order``（|RankIC| 降序）贪心去重 → :class:`DedupReport`。

    ``factors``：key → 挖掘窗内（可交易 ∧ 中性化后）的因子值序列；缺 key
    即抛 KeyError（调用方有 bug，不许静默跳过一对）。
    """
    if not 0.0 <= warn < reject <= 1.0:
        raise ValueError(f"相关性阈值非法：warn={warn!r} reject={reject!r}")
    kept: list[str] = []
    dropped: list[tuple[str, str, float]] = []
    warnings: list[tuple[str, str, float]] = []
    pairs: list[tuple[str, str, float]] = []
    n_pairs = 0
    for key in order:
        series = factors[key]
        best_rho = float("nan")
        best_against = ""
        for champion in kept:
            rho, _n_days = _ic.daily_rank_corr(series, factors[champion], min_names=min_names)
            n_pairs += 1
            if np.isfinite(rho):
                pairs.append((key, champion, float(rho)))
                if not np.isfinite(best_rho) or abs(rho) > abs(best_rho):
                    best_rho, best_against = float(rho), champion
        if np.isfinite(best_rho) and abs(best_rho) >= reject:
            dropped.append((key, best_against, best_rho))
            continue
        if np.isfinite(best_rho) and abs(best_rho) >= warn:
            warnings.append((key, best_against, best_rho))
        kept.append(key)
    kept_set = set(kept)
    max_corr: dict[str, float] = {}
    for a, b, rho in pairs:
        for side in (a, b):
            if side in kept_set:
                max_corr[side] = max(max_corr.get(side, 0.0), abs(rho))
    return DedupReport(
        kept=tuple(kept),
        dropped=tuple(dropped),
        warnings=tuple(warnings),
        n_pairs=n_pairs,
        reject=reject,
        warn=warn,
        max_corr=max_corr,
    )


def stable_factor_id(
    *,
    user_id: str,
    market: str,
    universe: str,
    candidate_key: str,
    task_fingerprint: str = "",
    task_id: str = "",
) -> tuple[str, str]:
    """稳定因子 id → ``(factor_id, scheme)``（P3 指纹优先，任务 id 兜底）。

    同输入恒同 id（md5 内容哈希，跨进程稳定）；缺指纹又缺 task_id 时仍能出 id
    （纯配置身份）——但正常链路至少给一个，scheme 如实标出用的是哪个。
    """
    scope = [IDENTITY_PREFIX, str(user_id), str(market), str(universe), str(candidate_key)]
    fingerprint = str(task_fingerprint or "").strip()
    if fingerprint:
        raw = ":".join([*scope, fingerprint])
        return hashlib.md5(raw.encode("utf-8")).hexdigest(), SCHEME_CONFIG
    raw = ":".join([*scope, str(task_id)])
    return hashlib.md5(raw.encode("utf-8")).hexdigest(), SCHEME_TASK
