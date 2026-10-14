"""风险三文件 → 提示词文本（Stage 2 第二批）：risk_block / industry_caution 槽的渲染。

入参 = feeds 层读到的**原始 JSON**（``data/risk_block.json`` /
``industry_risk.json`` / ``fundamental_flags.json``，cron 在产）与持仓宇宙。
本模块纯渲染：不取数、不读文件、不连 Redis——与 ``decision_blocks`` 同一批纪律。

签名对齐上游（``gen_decision_prompt_golden.py`` 里钉过的
``risk_warning_block(held_codes=, pool_codes=, names=)``）：**持仓与候选两侧都
收**——持仓命中 = 提示退出，候选命中 = 本轮勿买（闸门虽已硬拦买入，块里写出来
才省一轮无效决策）。两侧都空 → 空串（无标的可命中）。

两条降级纪律：

1. **文件缺失 → 对应块空串**（``risk``/``industry``/``fund`` 为 ``None``）：
   降级原因由调用方进丁8 新鲜度警告——硬约束段落**宁可整段不出，也不拿半截
   数据吓模型**；未接线（``wired=False``，旧替身）→ 空串，提示词逐字节等于历史。
2. **命中才出块**：宇宙（持仓∪候选）在名单里一条都没命中 → 空串（默认路径，
   历史字节冻结）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

#: 块内最多列几条命中（超出计数收尾）——风险清单 1769 条，命中多时不许淹没提示词。
RISK_MAX_LINES = 8

#: 行业软提示最多列几个行业（按剔除率降序）。
INDUSTRY_MAX_LINES = 5

#: 单条理由的截断长度（文件里最长的理由 200+ 字，块里只留判定所需）。
_REASON_CAP = 80

#: 基本面红旗 tag → 中文（文件 flags 实测 ``["fin", "trend"]``）。
_FLAG_ZH = {"fin": "财务", "trend": "趋势"}

_RISK_HEADER = "【事件风险警示（系统风险清单，硬约束）】"
_INDUSTRY_HEADER = "【行业整体劣化（赛道级软提示，不硬拦）】"


def _bare6(code: object) -> str:
    """suffix/前缀码 → 裸 6 位（三文件的键都是裸码）；非 6 位数（港美）→ 空串。"""
    text = str(code or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    elif text[:2] in ("SH", "SZ", "BJ"):
        text = text[2:]
    return text if len(text) == 6 and text.isdigit() else ""


def _reason_text(reason: object) -> str:
    text = str(reason or "").strip()
    if len(text) > _REASON_CAP:
        text = text[:_REASON_CAP] + "…"
    return text


def _code_entries(
    held_codes: Sequence[str],
    pool_codes: Sequence[str],
    names: Mapping[str, str] | None,
    lookup: Mapping[str, Any],
) -> list[tuple[str, str, bool]]:
    """宇宙 × 名单求交：``[(suffix 码, 名称, 是否持仓侧)]``（持仓先、去重、稳定序）。"""
    names = names or {}
    out: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for held, codes in ((True, held_codes), (False, pool_codes)):
        for code in codes:
            suffix = str(code or "").strip().upper()
            if not suffix or suffix in seen or _bare6(suffix) not in lookup:
                continue
            seen.add(suffix)
            out.append((suffix, str(names.get(suffix) or ""), held))
    return out


def _line(code: str, name: str, body: str, *, held: bool) -> str:
    who = f"{code} {name}".strip()
    tag = "" if held else "（候选池命中，本轮勿买）"
    return f"- {who}：{body}{tag}"


def build_risk_block(
    risk: Mapping[str, Any] | None,
    fund: Mapping[str, Any] | None,
    held_codes: Sequence[str],
    pool_codes: Sequence[str],
    names: Mapping[str, str] | None = None,
    now: datetime | None = None,
    *,
    wired: bool = True,
) -> str:
    """``risk_block.json`` + ``fundamental_flags.json`` → 【事件风险警示】文本。

    三段：长期排除清单（items，硬）→ 观察名单（watch，监管/事件）→ 警告
    （warns，如高质押）→ 基本面红旗（fundamental_flags）。宇宙两侧都收（见模块
    docstring）。行数封顶 :data:`RISK_MAX_LINES`，超出计数收尾。
    """
    if not wired or (risk is None and fund is None):
        return ""
    if not held_codes and not pool_codes:
        return ""
    risk = risk or {}
    fund = fund or {}
    items = risk.get("items") if isinstance(risk.get("items"), Mapping) else {}
    watch = risk.get("watch") if isinstance(risk.get("watch"), Mapping) else {}
    warns = risk.get("warns") if isinstance(risk.get("warns"), Mapping) else {}
    flags = fund.get("items") if isinstance(fund.get("items"), Mapping) else {}

    lines: list[str] = []
    overflow = 0

    def _push(text: str) -> None:
        nonlocal overflow
        if len(lines) < RISK_MAX_LINES:
            lines.append(text)
        else:
            overflow += 1

    for code, name, held in _code_entries(held_codes, pool_codes, names, items):
        entry = items.get(_bare6(code)) or {}
        if not isinstance(entry, Mapping):  # 坏值按无细节渲染，不炸提示词
            entry = {}
        body = _reason_text(entry.get("reason"))
        expire = str(entry.get("expire") or "").strip()
        if expire:
            body += f"（有效期至 {expire}）"
        _push(_line(code, name, body, held=held))
    for code, name, held in _code_entries(held_codes, pool_codes, names, watch):
        entry = watch.get(_bare6(code)) or {}
        if not isinstance(entry, Mapping):
            entry = {}
        body = _reason_text(entry.get("reason"))
        until = str(entry.get("until") or "").strip()
        if until:
            body += f"（观察至 {until}）"
        _push(_line(code, name, body, held=held))
    for code, name, held in _code_entries(held_codes, pool_codes, names, warns):
        _push(_line(code, name, _reason_text(warns.get(_bare6(code))), held=held))
    for code, name, held in _code_entries(held_codes, pool_codes, names, flags):
        entry = flags.get(_bare6(code)) or {}
        if not isinstance(entry, Mapping):
            entry = {}
        raw_flags = entry.get("flags") if isinstance(entry.get("flags"), list) else []
        tags = "/".join(_FLAG_ZH.get(str(f), str(f)) for f in raw_flags)
        body = f"基本面红旗（{tags or '—'}）：{_reason_text(entry.get('reason'))}"
        _push(_line(code, name, body, held=held))

    if not lines:
        return ""
    asof = str(risk.get("asof") or fund.get("asof") or "").strip()
    head = _RISK_HEADER
    if asof:
        stale = ""
        if now is not None and asof != now.date().isoformat():
            stale = "，非当日"
        head += f"（清单日期 {asof}{stale}）"
    if overflow:
        lines.append(f"- （另有 {overflow} 条命中未列，按名单执行）")
    return "\n".join([head, *lines])


def build_industry_caution_block(
    industry: Mapping[str, Any] | None,
    held_codes: Sequence[str],
    pool_codes: Sequence[str],
    *,
    wired: bool = True,
) -> str:
    """``industry_risk.json`` → 【行业整体劣化】（行业级，不逐票）。

    ``warn_codes``（裸码→行业）圈出宇宙涉及的行业，再按 ``items`` 的剔除率取行
    （降序、封顶 :data:`INDUSTRY_MAX_LINES`）。宇宙不在任何劣化行业 → 空串。
    """
    if not wired or industry is None:
        return ""
    if not held_codes and not pool_codes:
        return ""
    warn_codes = (
        industry.get("warn_codes")
        if isinstance(industry.get("warn_codes"), Mapping)
        else {}
    )
    sectors: set[str] = set()
    for code in (*held_codes, *pool_codes):
        sector = warn_codes.get(_bare6(code))
        if sector:
            sectors.add(str(sector))
    if not sectors:
        return ""
    rows = [
        row
        for row in (industry.get("items") or [])
        if isinstance(row, Mapping) and str(row.get("industry") or "") in sectors
    ]
    rows.sort(key=lambda r: float(r.get("rate") or 0.0), reverse=True)
    if not rows:
        return ""
    lines = []
    for row in rows[:INDUSTRY_MAX_LINES]:
        try:
            rate = f"{float(row.get('rate')):.1%}"
        except (TypeError, ValueError):
            rate = "—"
        lines.append(
            f"- {row.get('industry')}：长期排除清单剔除率 {rate}"
            f"（{row.get('n')}/{row.get('total')} 只）——赛道整体劣化，新开仓谨慎"
        )
    head = _INDUSTRY_HEADER
    asof = str(industry.get("asof") or "").strip()
    if asof:
        head += f"（清单日期 {asof}）"
    return "\n".join([head, *lines])
