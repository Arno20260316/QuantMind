"""桥 JSON → 行记录解析器（纯函数，金样测试的测试面）。

三条铁律
--------
1. **空 → NULL，不填 0**：``""``/``"--"``/``None`` 一律解析成 ``None``；
   字符串 ``"0"`` 是真实值 0。
2. **不丢值**：数字进数值列；数字放不下的（时间/日期/混合数组）原样进
   ``value_text``（长表）或全量进 ``raw``（宽表）。
3. **形状容错**：GP/BK/SC/GO 家族服务端未解锁（``table_list``），返回形态
   只能按同族方法实证推演——:func:`parse_jy_value` 用通用递归同时兼容
   标的维/日期维/字段维三种嵌套与行字典列表两种形态；数据包就位后探针
   实测形态，若有新变体在此补一条金样即可，采集链其余部分零改动。

日期口径：业务日一律 ``VARCHAR(8)`` ``YYYYMMDD``（``2026-10-15``/int
``20261015`` 都归一）；解析不出日期时用调用侧的 ``default_date``（=目标
交易日，range 调用 start=end=目标日时该缺省恒正确）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.shared.stock_utils import StockCodeUtil

logger = logging.getLogger(__name__)

#: 视为「无值」的标量文本（空 → NULL，不填 0）。
_EMPTY_MARKS = frozenset({"", "--", "-", "~", "null", "none", "nan"})

#: 字段码模式：GP02 / BK15 / SC42 / GO3 / FN100。
_FIELD_RE = re.compile(r"^[A-Z]{1,3}\d{1,3}$")

#: 业务日期模式（8 位数字）。
_DATE_RE = re.compile(r"^\d{8}$")

#: 标的代码模式：后缀式 ``600036.SH`` / 前缀式 ``SH600036`` / 裸码 ``600036``。
#: 注意与字段码（含字母无点）和日期（8 位数字）天然不重叠。
_CODE_RE = re.compile(
    r"^(?:\d{6}\.(?:SH|SZ|BJ)|\d{6}|(?:SH|SZ|BJ)\d{6})$", re.IGNORECASE
)

#: 行字典里的标的键名（小写集合）。
_META_CODE_KEYS = frozenset(
    {
        "code",
        "stock_code",
        "stockcode",
        "bk_code",
        "block_code",
        "blockcode",
        "symbol",
        "stock",
        "instrument",
        "fund_code",
        "bond_code",
    }
)

#: 行字典里的日期键名（小写集合）。
_META_DATE_KEYS = frozenset(
    {"date", "trade_date", "tradedate", "datetime", "trade_time", "tradetime", "time"}
)


def num(value: Any) -> float | None:
    """桥值 → float；空/非数字 → None（**不填 0**）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f  # NaN → None
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in _EMPTY_MARKS:
            return None
        try:
            f = float(s.replace(",", ""))
        except ValueError:
            return None
        return None if f != f else f
    return None


def _int(value: Any) -> int | None:
    f = num(value)
    return None if f is None else int(f)


def _norm_date(value: Any) -> str | None:
    """任意日期写法 → ``YYYYMMDD``（识别不了 → None）。"""
    s = str(value).strip().replace("-", "").replace("/", "")
    return s[:8] if _DATE_RE.match(s) else None


def _pick(d: dict, *names: str) -> Any:
    """大小写不敏感取键（桥字段名大小写不统一：fHSL / Zjl_HB / HqDate）。"""
    lowered = {str(k).strip().lower(): v for k, v in d.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _symbol(code: str) -> str:
    """任意写法 → 前缀式；识别不了原样返回（防御，不抛）。"""
    try:
        return StockCodeUtil.to_prefix(code) or code
    except Exception:  # noqa: BLE001
        return code


def _values(v: Any) -> tuple[float | None, float | None, str]:
    """叶子值 → ``(v1, v2, value_text)``（口径见模块 docstring 铁律 2）。

    * 标量数字 → ``(f, None, "")``；标量文本 → ``(None, None, 原样)``；
    * ≤2 元全数字列表 → 双值列（GP/BK/SC 字段语义为「值1 值2」对）；
    * >2 元列表（疑似序列而非值对）→ 数值列留空，整体 JSON 进 ``value_text``
      ——**不做日期猜测**，由探针实测后再定展开规则。
    """
    if isinstance(v, (list, tuple)):
        nums = [num(x) for x in v]
        if 0 < len(v) <= 2 and all(n is not None for n in nums):
            v1 = nums[0] if nums else None
            v2 = nums[1] if len(nums) > 1 else None
            return v1, v2, ""
        if not v:
            return None, None, ""
        return None, None, json.dumps(list(v), ensure_ascii=False, default=str)
    n = num(v)
    if n is not None:
        return n, None, ""
    if isinstance(v, dict):
        return (
            (None, None, json.dumps(v, ensure_ascii=False, default=str))
            if v
            else (
                None,
                None,
                "",
            )
        )
    if v is None or (isinstance(v, str) and v.strip().lower() in _EMPTY_MARKS):
        return None, None, ""
    return None, None, str(v)


# ============ 通用专业序列解析（GP / BK / SC / GO 家族） ============


def parse_jy_value(
    value: Any,
    *,
    fields: tuple[str, ...] | list[str],
    default_date: str,
    entity_col: str | None,
) -> list[dict]:
    """GP/BK/SC/GO 通用递归解析 → 长表行。

    ``entity_col``：实体列名（``stock_code`` / ``bk_code``），``None`` = 无实体
    维（SC 市场级）。返回行键：``{entity_col?, trade_date, field_code, v1, v2,
    value_text}``。同键后到覆盖先到（``dict`` 去重）。

    兼容形态（递归 + 键分类，逐条有金样）：
    * 标的维 ``{code: {field: 值}}``、``{code: {date: {field: 值}}}``；
    * 字段维 ``{field: {date: 值}}``；
    * 日期维 ``{date: {field: 值}}`` / ``{date: {code: {field: 值}}}``；
    * 行字典列表 ``[{Code, Date, GP16: …}, …]``；
    * 值形态：标量 / ``[v1, v2]`` 值对 / 未知包层（下探）。
    """
    field_set = {str(f).strip().upper() for f in fields}
    out: dict[tuple, dict] = {}

    def emit(entity: Any, date: str | None, field: str, leaf: Any) -> None:
        v1, v2, text = _values(leaf)
        if v1 is None and v2 is None and not text:
            return  # 全空不落行（不等于 0）
        row: dict[str, Any] = {
            "trade_date": date or default_date,
            "field_code": field.upper(),
            "v1": v1,
            "v2": v2,
            "value_text": text,
        }
        if entity_col:
            if entity is None:
                return  # 有实体维的表拒收无实体行（宁可缺，不可错列）
            row[entity_col] = entity
        out[(entity, row["trade_date"], row["field_code"])] = row

    def walk(
        node: Any, entity: Any, date: str | None, field: str | None, depth: int
    ) -> None:
        if depth > 6 or node is None:
            return
        if isinstance(node, dict):
            row_field_keys = [
                k
                for k in node
                if isinstance(k, str)
                and (
                    k.strip().upper() in field_set or _FIELD_RE.match(k.strip().upper())
                )
            ]
            # 行字典 vs 字段维容器：字段键的值有非 dict（标量/值对）才是行字典；
            # 全是 dict（={date: 值}）是字段维容器，走下面的通用键分类。
            if row_field_keys and any(
                not isinstance(node[k], dict) for k in row_field_keys
            ):
                # 行字典形态：字段键 + Date/Code 元键
                e, d = entity, date
                for k, v in node.items():
                    if not isinstance(k, str):
                        continue
                    lk = k.strip().lower()
                    if lk in _META_DATE_KEYS:
                        d = _norm_date(v) or d
                    elif (
                        lk in _META_CODE_KEYS
                        and isinstance(v, str)
                        and _CODE_RE.match(v.strip())
                    ):
                        e = v.strip()
                for k in row_field_keys:
                    emit(e, d, k, node[k])
                return
            for k, v in node.items():
                if not isinstance(k, str):
                    continue
                ks = k.strip()
                if field is not None:
                    # 字段已定 → 本层键应为日期；不是日期就近下探（未知包层）
                    if _DATE_RE.match(ks):
                        emit(entity, ks, field, v)
                    else:
                        walk(v, entity, date, field, depth + 1)
                elif _DATE_RE.match(ks):
                    walk(v, entity, ks, None, depth + 1)
                elif ks.upper() in field_set or _FIELD_RE.match(ks.upper()):
                    walk(v, entity, date, ks, depth + 1)
                elif _CODE_RE.match(ks):
                    walk(v, ks, date, None, depth + 1)
                # 其余未知元键：忽略（不猜）
            return
        if isinstance(node, list):
            if field is not None:
                emit(entity, date, field, node)  # 字段叶子：值对/数组，整体交给 _values
                return
            for item in node:
                walk(item, entity, date, None, depth + 1)
            return
        if field is not None:
            emit(entity, date, field, node)

    walk(value, None, None, None, 0)
    return list(out.values())


def parse_gp_value(value: Any, target_date: str) -> list[dict]:
    """个股专业序列（GP01–GP46）。"""
    from backend.services.live_trading.services.tdx_ingest.datasets import GP_FIELDS

    return parse_jy_value(
        value, fields=GP_FIELDS, default_date=target_date, entity_col="stock_code"
    )


def parse_bk_value(value: Any, target_date: str) -> list[dict]:
    """板块专业序列（BK5–BK19）。"""
    from backend.services.live_trading.services.tdx_ingest.datasets import BK_FIELDS

    return parse_jy_value(
        value, fields=BK_FIELDS, default_date=target_date, entity_col="bk_code"
    )


def parse_sc_value(value: Any, target_date: str) -> list[dict]:
    """市场级序列（SC01–SC42，无实体维）。"""
    from backend.services.live_trading.services.tdx_ingest.datasets import SC_FIELDS

    return parse_jy_value(
        value, fields=SC_FIELDS, default_date=target_date, entity_col=None
    )


def parse_go_value(value: Any, target_date: str) -> list[dict]:
    """单股一次性数据（GO1–GO47）→ gp_one_data 行（value_num / value_text）。"""
    from backend.services.live_trading.services.tdx_ingest.datasets import GO_FIELDS

    rows = parse_jy_value(
        value, fields=GO_FIELDS, default_date=target_date, entity_col="stock_code"
    )
    result = []
    for row in rows:
        v1, v2, text = row["v1"], row["v2"], row["value_text"]
        if text:
            value_text = text
        elif v2 is not None:
            value_text = f"{v1},{v2}"
        else:
            value_text = ""
        result.append(
            {
                "stock_code": row["stock_code"],
                "symbol": _symbol(row["stock_code"]),
                "trade_date": row["trade_date"],
                "field_code": row["field_code"],
                "value_num": v1,
                "value_text": value_text,
            }
        )
    return result


# ============ 个股/全市场快照 ============


def parse_pricevol(value: Any, target_date: str) -> list[dict]:
    """``get_pricevol`` → 全市场收盘快照行（probe 实证：``{code: {LastClose,
    Now, Volume}}``，值为字符串；Volume 单位「手」）。``zaf`` 派生 = 涨跌幅 %。
    """
    if not isinstance(value, dict):
        return []
    rows: list[dict] = []
    for code, entry in value.items():
        if not isinstance(entry, dict):
            continue
        sc = str(code).strip()
        if not _CODE_RE.match(sc):
            continue  # 非标量键（防御）
        last_close = num(_pick(entry, "LastClose"))
        now_price = num(_pick(entry, "Now"))
        zaf = None
        if last_close not in (None, 0.0) and now_price is not None:
            zaf = (now_price - last_close) / last_close * 100.0
        rows.append(
            {
                "stock_code": sc,
                "symbol": _symbol(sc),
                "trade_date": target_date,
                "last_close": last_close,
                "now_price": now_price,
                "zaf": zaf,
                "volume_hand": num(_pick(entry, "Volume")),
            }
        )
    return rows


def parse_more_info(value: Any, stock_code: str, target_date: str) -> dict | None:
    """``get_more_info`` 单股全字段（88 项，全字符串值）→ 热列 + raw 全量。

    热列映射（大小写不敏感）：ZAF→zaf、fHSL→f_hsl、Zjl→zjl、Zjl_HB→zjl_hb、
    Ltsz→ltsz、Zsz→zsz、ZTPrice→zt_price、DTPrice→dt_price、HqDate→hq_date。
    """
    if not isinstance(value, dict) or not value:
        return None
    hq = _pick(value, "HqDate")
    return {
        "stock_code": stock_code,
        "symbol": _symbol(stock_code),
        "trade_date": target_date,
        "zaf": num(_pick(value, "ZAF")),
        "f_hsl": num(_pick(value, "fHSL")),
        "zjl": num(_pick(value, "Zjl")),
        "zjl_hb": num(_pick(value, "Zjl_HB")),
        "ltsz": num(_pick(value, "Ltsz")),
        "zsz": num(_pick(value, "Zsz")),
        "zt_price": num(_pick(value, "ZTPrice")),
        "dt_price": num(_pick(value, "DTPrice")),
        "hq_date": _norm_date(hq) or "",
        "raw": value,
    }


def parse_zdt(value: Any, target_date: str) -> list[dict]:
    """``get_zdt_data`` → 涨跌停状态行（probe 实证：``{code: {15 字段}}``）。

    注意：当前该接口**结构性全零**（缺短线宝权限），数据集挂起不入库；
    解析器先备好，解锁后手动 ``--force`` 复测即用。
    """
    if not isinstance(value, dict):
        return []
    rows: list[dict] = []
    for code, entry in value.items():
        if not isinstance(entry, dict):
            continue
        sc = str(code).strip()
        if not _CODE_RE.match(sc):
            continue

        def g(name: str) -> Any:
            return _pick(entry, name)  # noqa: B023 - 闭包仅在同次迭代内立即调用

        def s(name: str) -> str:
            v = g(name)
            return "" if v is None else str(v).strip()

        rows.append(
            {
                "stock_code": sc,
                "symbol": _symbol(sc),
                "trade_date": target_date,
                "zdt_status_now": _int(g("ZDTStatusNow")),
                "zdt_status_ori": _int(g("ZDTStatusOri")),
                "first_time_zt": s("FirstTimeZT"),
                "last_time_zt": s("LastTimeZT"),
                "last_open_time_zt": s("LastOpenTimeZT"),
                "first_time_dt": s("FirstTimeDT"),
                "last_time_dt": s("LastTimeDT"),
                "last_open_time_dt": s("LastOpenTimeDT"),
                "open_times_zt": _int(g("OpenTimesZT")),
                "open_times_dt": _int(g("OpenTimesDT")),
                "fd_vol_max_zt": num(g("FDVolMaxZT")),
                "fd_vol_max_dt": num(g("FDVolMaxDT")),
                "vol_zt": num(g("VolZT")),
                "raw": entry,
            }
        )
    return rows


# ============ 引用面 ============


def parse_stock_list(value: Any, market_key: str, target_date: str) -> list[dict]:
    """``get_stock_list`` → 股票清单快照行（**两种实证形态都收**）。

    * ``list_type=0`` → 纯代码字符串列表 ``["000001.SZ", …]``（probe 01–06 实证，
      market=5 全 A 5581 条就是这一形态）；
    * ``list_type=1`` → ``[{Code, Name}]``（SKILL.md:698 文档口径；名称缺省空串）。
    """
    if not isinstance(value, list):
        return []
    rows: list[dict] = []
    for item in value:
        name = ""
        if isinstance(item, dict):
            code = str(_pick(item, "Code") or "").strip()
            name = str(_pick(item, "Name") or "").strip()[:64]
        elif isinstance(item, str):
            code = item.strip()
        else:
            continue
        if not _CODE_RE.match(code):
            continue
        rows.append(
            {
                "trade_date": target_date,
                "market_key": str(market_key),
                "stock_code": code,
                "symbol": _symbol(code),
                "name": name,
            }
        )
    return rows


__all__ = [
    "num",
    "parse_bk_value",
    "parse_go_value",
    "parse_gp_value",
    "parse_jy_value",
    "parse_more_info",
    "parse_pricevol",
    "parse_sc_value",
    "parse_stock_list",
    "parse_zdt",
]
