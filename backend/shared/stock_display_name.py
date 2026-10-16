"""推送面股票身份显示 —— 「中文名(代码)」的唯一实现（QQ/告警文案共用）。

口径（2026-10-16 设计稿 ``docs/QQ推送_中文名与排版设计_20261016.md``）：

* 身份一律 ``中文名(代码)``；名字查不到**只显示代码**，绝不编造、绝不
  回落成 ``代码(代码)``。
* 显示代码形态：A股前缀式（``SH601857``，与前端/委托台账/成交回执一致）；
  港股 ``0700.HK``（4 位补零，与 ``security_master`` 键同形）；美股 ticker
  大写（``US.AAPL`` / ``AAPL.US`` 归一为 ``AAPL``）。
* 解析分层：调用方提示名 → A股 ``stocks_index.json``（stock_name_mapper）→
  港股/美股 ``security_master`` parquet（``QM_QUANTHK_DATA_DIR`` /
  ``QM_QUANTUS_DATA_DIR``，惰性加载 + mtime 失效）→ 内置指数表 → ``""``。
* 本模块是**消费端**：任何加载/解析失败只降级为「无名字」（告警少一个名字
  可接受，少一条告警不可接受），绝不抛、不阻塞调用方。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

#: 内置指数名（数量有限且稳定，无外部数据源）。键 = 规范形态（A股后缀式 /
#: 港美代码）；查找时把 ``SH000001`` 前缀式与大小写归一，但**裸 6 位码不匹配
#: 指数**——``000001`` 是平安银行不是上证指数，歧义形态必须留给 A股 mapper。
_INDEX_NAMES: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "HSI": "恒生指数",
    "HSTECH": "恒生科技指数",
    "IXIC": "纳斯达克指数",
    "DJI": "道琼斯指数",
    "SPX": "标普500",
    "N225": "日经225",
}

#: 港/美 security_master 相对路径（各市场数据根目录下）。
_MASTER_RELPATH = ("2_base_sector", "security_master", "data.parquet")
#: 市场 → （数据根目录环境变量, 默认叶子目录）。
_MARKET_ROOTS: dict[str, tuple[str, str]] = {
    "hk": ("QM_QUANTHK_DATA_DIR", "quanthk"),
    "us": ("QM_QUANTUS_DATA_DIR", "quantus"),
}

_RE_CN_SUFFIX = re.compile(r"^(\d{6})\.(SH|SZ)$")
_RE_CN_PREFIX = re.compile(r"^(SH|SZ)(\d{6})$")
#: 港股形态：``2555.HK`` / ``0700.HK`` / ``HK.0700`` / ``HK0700`` / 裸数字码。
_RE_HK = re.compile(r"^(?:HK[.:]?)?(\d{1,5})(?:\.HK)?$")
#: 美股形态：字母开头的 ticker（允许 ``BRK.B`` 这类点号/连字符）。
_RE_US = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

#: market → (symbol→名 映射, parquet mtime)
_master_cache: dict[str, tuple[dict[str, str], float]] = {}
_master_lock = threading.Lock()
_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


def _market_root(env_key: str, default_leaf: str) -> Path:
    base = os.environ.get(env_key) or ""
    if base.startswith("/"):
        return Path(base)
    return Path("/data") / default_leaf


def _load_master(market: str) -> dict[str, str]:
    """security_master parquet → ``{symbol: 中文名}``；缺文件/读失败 → ``{}``。

    ``cn_name`` 优先、``en_name`` 兜底；进程内缓存按文件 mtime 失效（标的池
    每日重建，mtime 变了才重读）。加载失败降级为空表并只记一次日志。
    """
    env_key, leaf = _MARKET_ROOTS[market]
    path = _market_root(env_key, leaf).joinpath(*_MASTER_RELPATH)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _warn_once(
            f"missing:{market}",
            "[StockDisplayName] %s 名称表缺失（%s），该市场将不显示名称",
            market,
            path,
        )
        return {}
    cached = _master_cache.get(market)
    if cached is not None and cached[1] == mtime:
        return cached[0]
    try:
        import duckdb

        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT symbol, cn_name, en_name FROM read_parquet(?)", [str(path)]
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - 名字是点缀，读不动就降级
        _warn_once(
            f"load:{market}",
            "[StockDisplayName] %s 名称表加载失败（降级为无名字）: %s",
            market,
            exc,
        )
        return cached[0] if cached is not None else {}
    mapping: dict[str, str] = {}
    for sym, cn, en in rows:
        key = str(sym or "").strip()
        name = str(cn or "").strip() or str(en or "").strip()
        if key and name and name != key:
            mapping[key] = name
    with _master_lock:
        _master_cache[market] = (mapping, mtime)
    return mapping


def _index_key(symbol: str) -> str:
    """符号 → 指数表**候选**规范键（调用点须再查 ``_INDEX_NAMES`` 判成员）。

    A股显式形态（``600519.SH``/``SH600519``）一律返回规范后缀键——是不是指数
    由调用点查表决定；港美形态仅在命中指数表时返回键（``HSI``/``IXIC``）。
    """
    s = str(symbol or "").strip().upper()
    if not s:
        return ""
    m = _RE_CN_SUFFIX.fullmatch(s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = _RE_CN_PREFIX.fullmatch(s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    t = re.sub(r"^(HK|US)[.:]", "", s)
    t = re.sub(r"\.(HK|US)$", "", t)
    return t if t in _INDEX_NAMES else ""


def _hk_key(symbol: str) -> str:
    """港股符号 → ``NNNN.HK``（4 位补零）；不是港股形态返回 ``""``。"""
    m = _RE_HK.fullmatch(str(symbol or "").strip().upper())
    if not m:
        return ""
    return f"{int(m.group(1)):04d}.HK"


def _us_key(symbol: str) -> str:
    """美股符号 → 裸 ticker 大写（去 ``US.`` 前缀 / ``.US`` 后缀）；否则 ``""``。"""
    s = str(symbol or "").strip().upper()
    s = re.sub(r"^US[.:]", "", s)
    s = re.sub(r"\.US$", "", s)
    return s if _RE_US.fullmatch(s) else ""


def _cn_resolve(symbol: str) -> str:
    """A股名（stock_name_mapper）；失败/查不到 → ``""``（不挡住港美解析）。"""
    try:
        from backend.shared.stock_name_mapper import resolve_name

        return str(resolve_name(symbol) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def resolve(symbol: str, hint: str = "") -> str:
    """symbol → 中文名；查不到返回 ``""``（绝不编造）。``hint`` = 调用方已持有的名字。

    ``hint`` 与 symbol 是同一证券（含各层代码形态）时忽略，避免
    ``002074.SZ(002074.SZ)`` 这类「代码当名字」。
    """
    try:
        s = str(symbol or "").strip()
        h = str(hint or "").strip()
        if h and h != s and display_code(h) != display_code(s):
            return h
        if not s:
            return ""
        key = _index_key(s)
        if key and key in _INDEX_NAMES:  # 只有真在表里才算指数，否则落到 A股/港美层
            return _INDEX_NAMES[key]
        name = _cn_resolve(s)
        if name:
            return name
        hk = _hk_key(s)
        if hk:
            name = _load_master("hk").get(hk, "")
            if name:
                return name
        us = _us_key(s)
        if us:
            name = _load_master("us").get(us, "")
            if name:
                return name
        return ""
    except Exception:  # noqa: BLE001 - 深兜底：消费端永不因名字失败
        return ""


def display_code(symbol: str) -> str:
    """符号 → 规范显示代码；认不出的形态回原样（绝不丢身份）。

    A股前缀式（``SH601857``）、港股 ``0700.HK``、美股 ticker 大写。
    """
    s = str(symbol or "").strip()
    if not s:
        return ""
    key = _index_key(s)
    if key and key in _INDEX_NAMES:
        m = _RE_CN_SUFFIX.fullmatch(key)
        if m:  # A股指数 → 前缀式（与 A股 规则一致）
            return f"{m.group(2)}{m.group(1)}"
        return key
    try:
        from backend.shared.stock_utils import StockCodeUtil

        prefix = str(StockCodeUtil.to_prefix(s) or "")
    except Exception:  # noqa: BLE001
        prefix = ""
    if _RE_CN_PREFIX.fullmatch(prefix):
        return prefix
    hk = _hk_key(s)
    if hk:
        return hk
    us = _us_key(s)
    if us:
        return us
    return s


def identity(symbol: str, hint: str = "") -> str:
    """``国轩高科(SZ002074)``；名字查不到只回代码；两者皆无回 ``""``。"""
    try:
        code = display_code(symbol)
        name = resolve(symbol, hint)
        if name and code:
            return f"{name}({code})"
        return code or name or ""
    except Exception:  # noqa: BLE001
        return str(symbol or "").strip()


def reset_cache() -> None:
    """清空名称表缓存与告警去重集（测试用）。"""
    with _master_lock:
        _master_cache.clear()
    _warned.clear()
