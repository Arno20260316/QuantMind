"""决策提示词**补块**构建（Stage 2 第一批）：盘中可获取的数据 → 提示词文本块。

细案 ``docs/arena总控回路回归_细案_20261014.md``「Stage 2 第一批」的落点：用户点名
「交易时候获取到的**模型分数、市场状态**，分数都是能获取到的」——两条数据实测都在产
（``engine_signal_scores`` 实时行 / db0 ``qm:regime:intraday``），但都不进决策提示词。
本模块把「分数快照 / regime 快照」变成两段可直接拼进提示词的文本：

* :func:`attach_signal_scores` —— 把 ``rank_pct`` 折算成研究评分刻度（0–100，
  ``research_score`` 同一公式）填进 :class:`PoolQuote.signal_score`，渲染成行情行内
  ``· 信号分 88.2`` 后缀（渲染器不动，上游金样逐字节不破）；:func:`build_signal_meta_block`
  给出处/刻度/覆盖率——分数是**该行所属推理批次内部**的截面分位（2026-10-14 实测：
  ``mode=off`` 读口径下快照混四批，口径句必须如实说「可能混合」），口径句必须跟着走。
* :func:`build_market_state_block` —— ``qm:regime:intraday``（engine 60 秒刷新）→
  【盘中市场状态】块：状态/建议仓位系数/20 日收益波动/指数现价/广度/快照年龄/notes。

两条纪律（与 ``decision/context.py`` 的「缺失一律 ``—``，绝不显示成 0」同源）：

1. **绝不编数**：``rank_pct`` 为 NULL 不给「信号分」；指数快照没读到就说「未取到」；
   regime 整包读不到 → 块渲染「本轮不可得」一行——静默消失会让「agent 看不见」
   这件事再次不可见。
2. **纯函数**：本模块不取数、不连 Redis、不读文件；快照由调用方（决策轮 IO 层）注入。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from backend.shared.decision.context import PoolQuote
from backend.shared.decision_context_source import day_change_pct

#: regime 快照「新鲜」窗口（分钟）：引擎 60 秒刷新，超过 5 分钟没动 = 服务停摆或
#: 非交易时段（盘前轮拿到的多是昨收后那一版）——如实标旧，不静默当实时。
MARKET_STATE_STALE_MIN = 5.0

#: regime 三态中文化（``market_regime.STATES`` 的展示面）。表外的原样回显——
#: 不认识就照抄，绝不猜。
_STATE_ZH = {"bull": "偏强", "neutral": "中性", "bear": "偏弱"}


def _f(value: object) -> float | None:
    """快照字段 → float；空/脏一律 ``None``（口径同 ``decision_context_source._f``）。

    注意 Hash 值一律是字符串，且写侧 ``str(None)`` 会留下字面 ``"None"``——
    ``float("None")`` 抛错、``int("None")`` 同样，故数值字段一律先过这里。
    """
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _int_or(value: object, default: int = 0) -> int:
    """计数字段 → int；空/脏 → ``default``（快照/元信息坏值不许炸穿提示词）。"""
    number = _f(value)
    return int(number) if number is not None else default


def attach_signal_scores(
    quotes: Sequence[PoolQuote],
    score_map: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[tuple[PoolQuote, ...], int]:
    """行情行 → 填 ``signal_score``（研究评分刻度 0–100）；返回 (新元组, 有分条数)。

    ``score_map`` 来自 :func:`backend.shared.signal_scores.load_score_snapshot`
    （prefix 键，条目带 ``rank_pct``）。折算走 :func:`research_score` ——
    与前端研究评分同公式、同金样，且与上游金样行内格式 ``· 信号分 {:.1f}``
    （0–100 刻度）**刻度兼容**：raw ``fusion_score``（量级 ±1）塞进去会渲染成
    无意义的「0.3」。

    **不编**：标的在 map 中查无、``rank_pct`` 为 NULL、``rank_pct`` 越界
    （``research_score`` 对越界返回 None——防调用方把百分数当分位传），
    三种情况一视同仁：该行不给「信号分」后缀。不可变：返回新元组。
    """
    out: list[PoolQuote] = []
    filled = 0
    if not quotes:
        return tuple(quotes), 0
    from backend.shared.research_score import research_score
    from backend.shared.signal_scores import normalize_a_share_symbol

    for q in quotes:
        score: float | None = None
        sym = normalize_a_share_symbol(q.code)
        if sym and score_map:
            entry = score_map.get(sym)
            if isinstance(entry, Mapping):
                score = research_score(entry.get("rank_pct"))
        if score is None:
            out.append(q)
            continue
        filled += 1
        out.append(replace(q, signal_score=score))
    return tuple(out), filled


def build_signal_meta_block(
    meta: Mapping[str, Any] | None, *, filled: int, total: int
) -> str:
    """【盘中模型信号】：行情行内「信号分」的出处、刻度、覆盖率。

    ``meta`` 是 ``load_score_snapshot`` 的元信息（``ok``/``reason``/``model_id``/
    ``signal_date``/``rows``/``realtime_rows``/``fallback``）。快照不可得时**必须
    说出来**并警告不要凭空引用分数——一句「没有信号分」和一个「所有票分数都是 0」
    在模型眼里相差极大。
    """
    info = meta or {}
    # ``ok=True`` 但无 ``signal_date`` = 查得到、表里没数据（推理从未落库）——
    # 对提示词而言同样是「不可得」，不能渲染成「0 只覆盖」的正常快照。
    if not info.get("ok") or not info.get("signal_date"):
        reason = str(info.get("reason") or "未接线")
        return (
            "【盘中模型信号】本轮不可得（" + reason + "）——行情行内没有「信号分」，"
            "不要凭空引用模型分数。"
        )
    model = str(info.get("model_id") or "未标注")
    date = str(info.get("signal_date") or "—")
    rows = _int_or(info.get("rows"))
    realtime_rows = _int_or(info.get("realtime_rows"))
    fallback = "，回退日（当日覆盖不足）" if info.get("fallback") else ""
    return (
        "【盘中模型信号】（行情行内「信号分」的出处）\n"
        f"- 模型 {model}（信号日 {date}{fallback} ｜ 覆盖 {rows} 只，"
        f"其中盘中实时行 {realtime_rows}）\n"
        f"- 「信号分」= 该行所属推理批次内部的截面分位（0–100，越高越靠前）；"
        "一次快照可能混合多个推理批次（各批标的集与分母不同），"
        f"跨批次/跨模型不可直接比较；本轮行情块 {int(total)} 只中 {int(filled)} 只"
        "有分，无分的不显示该字段。"
    )


def _json_obj(raw: object) -> Mapping[str, Any] | None:
    """Hash 里的 JSON 字段 → dict（写侧 ``json.dumps`` 过；替身直接给 dict 也认）。"""
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, Mapping) else None
    return None


def _json_list(raw: object) -> list[Any]:
    """Hash 里的 JSON 数组字段 → list；坏 JSON/非数组 → 空表（不炸提示词）。"""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            return []
        return list(value) if isinstance(value, (list, tuple)) else []
    return []


def _age_text(generated: datetime, now: datetime) -> str:
    """快照年龄 → 「N 分钟前 / N.N 小时前」；超新鲜窗口追加标记（不嵌套括号）。"""
    minutes = max(0.0, (now - generated).total_seconds()) / 60.0
    if minutes < 120:
        age = f"{minutes:.0f} 分钟前"
    else:
        age = f"{minutes / 60:.1f} 小时前"
    if minutes > MARKET_STATE_STALE_MIN:
        return age + "，超新鲜窗口：引擎可能停摆或非交易时段"
    return age


def build_market_state_block(
    regime: Mapping[str, Any] | None,
    index_snap: Mapping[str, Any] | None,
    now: datetime,
) -> str:
    """盘中 regime 快照 → 【盘中市场状态】块；读不到 → 如实渲染「本轮不可得」。

    ``regime`` = db0 ``qm:regime:intraday`` Hash（全字符串；写侧见
    ``services/engine/realtime_regime.py``——breadth/notes 是 JSON 编码的字段）。
    ``index_snap`` = 同一批行情快照里 ``regime["index"]`` 那一只（可缺）。

    渲染字段全部来自快照本身，**不做二次判定**（state 用快照值不重算：
    写侧 ``classify_regime`` 才是唯一分级实现）；``inputs_ok=False`` 时状态段
    附「输入不完整」标记——那时写侧的 neutral 是缺省值而不是判定值，标出来
    防模型把缺省当结论。
    """
    if not regime:
        return (
            "【盘中市场状态】本轮不可得（regime 快照缺失或读取失败：引擎实时 regime "
            "服务未开）——不要凭空断言大盘强弱。"
        )
    state_raw = str(regime.get("state") or "").strip()
    state = _STATE_ZH.get(state_raw, state_raw or "—")
    state_disp = (
        f"{state}（{state_raw}）" if state_raw and state != state_raw else state
    )
    inputs_ok = regime.get("inputs_ok")
    if inputs_ok is not None and str(inputs_ok).strip().lower() not in ("true", "1"):
        state_disp += "（输入不完整：以上为缺省判定）"

    segments = [f"状态：{state_disp}"]
    hint = _f(regime.get("position_hint"))
    if hint is not None:
        segments.append(f"建议仓位系数 {hint:.1f}")
    ret = _f(regime.get("ret"))
    window = _f(regime.get("window"))
    win_txt = f"{int(window)} 日" if window else ""
    if ret is not None:
        segments.append(f"{win_txt or '窗口'}收益 {ret * 100:+.2f}%")
    vol = _f(regime.get("vol"))
    if vol is not None:
        vol_win = f"（{win_txt}窗口）" if win_txt else ""
        segments.append(f"日波动率{vol_win}{vol * 100:.2f}%")
    lines = [
        "【盘中市场状态】（实时 regime，引擎 60 秒刷新）",
        "- " + " ｜ ".join(segments),
    ]

    index_code = str(regime.get("index") or "").strip()
    if index_code:
        price = (
            _f(
                index_snap.get("Now")
                if index_snap.get("Now") is not None
                else index_snap.get("now")
            )
            if isinstance(index_snap, Mapping)
            else None
        )
        if price is not None and price > 0:
            chg = day_change_pct(index_snap)
            chg_txt = f"（{chg:+.2f}%）" if chg is not None else ""
            lines.append(f"- 指数 {index_code}：现价 ¥{price:.2f}{chg_txt}")
        else:
            lines.append(f"- 指数 {index_code}：本轮行情未取到（不参与定价）")

    breadth = _json_obj(regime.get("breadth"))
    if breadth:
        parts = []
        for key, label in (
            ("up", "涨"),
            ("down", "跌"),
            ("flat", "平"),
        ):
            value = breadth.get(key)
            if value is not None:
                parts.append(f"{label} {value}")
        tail = []
        for key, label in (("limit_up", "涨停"), ("limit_down", "跌停")):
            value = breadth.get(key)
            if value is not None:
                tail.append(f"{label} {value}")
        sample = breadth.get("sample")
        sample_txt = f"热集样本 {sample}，" if sample is not None else ""
        line = f"- 广度（{sample_txt}不参与状态判定）：" + " ／ ".join(parts)
        if tail:
            line += "（" + " ／ ".join(tail) + "）"
        lines.append(line)

    stamp = ""
    generated = regime.get("generated_at")
    if isinstance(generated, datetime):
        moment = generated
    else:
        moment = None
        try:
            moment = datetime.fromisoformat(str(generated)) if generated else None
        except ValueError:
            moment = None
    if moment is None:
        ts = _f(regime.get("ts"))
        if ts is not None and ts > 0:
            moment = datetime.fromtimestamp(ts, tz=now.tzinfo)
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    if moment is not None:
        stamp = f"- 快照生成于 {moment:%F %T}（{_age_text(moment, now)}）"
    notes = [str(n) for n in _json_list(regime.get("notes")) if str(n).strip()]
    if stamp and notes:
        lines.append(stamp + " ｜ 注：" + "；".join(notes))
    elif stamp:
        lines.append(stamp)
    elif notes:
        lines.append("- 注：" + "；".join(notes))
    return "\n".join(lines)
