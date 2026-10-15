"""因子研究 —— 因子语义分类（唯一实现）。

左栏「大文件夹 → 类别 → 因子」第三级的数据来源（2026-10-15 细案
``docs/因子研究_因子自动分类细案_20261015.md``）。三条纪律：

1. **零推断**：类别全部来自现成的确定性映射，链条固定、可单测 ——
   ① 手工表（每日特征 44 例 + 散码，穷举锁死）；
   ② ``<data>/factor1300/name_map.json``（f13_/Alpha101_/Alpha191_ 的 23 源类，
      且 f13 带现成中文名）；
   ③ ``<data>/factor_defs/formulas.json``（feat_ 前缀的 10 源类）；
   ④ 前缀规则表（L1/L2/Alpha360/经典库等）；
   ⑤ 挖掘因子词根表（rd_* 等透明英文词根）。
2. **缺失降级不装**：词表文件缺 → 该层跳过 + warning（catalog 照常出，绝不 500）；
   全链未命中 → ``other``（前端「其他/未分类」如实展示，可计数可审计）。
   **源词表已标「未分类」的不再硬猜** —— 上游明确说了没分类，规则去猜是造假。
3. **读取时富化**：不动 factors.json 产物；catalog / leaderboard 输出时经
   :func:`enrich` 补 ``category``（id）与 ``category_name``（中文），
   并把 name_map 里有中文名的 ``name_cn`` 补进去（只补空/等于代码的，不覆盖真名）。

数据根 = ``resolve_quantdb_dir().parent``（容器 ``/data``、仓库 ``data/`` 同规，
与 quantdb_paths.py 的解析语义一致）。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.shared.quantdb_paths import resolve_quantdb_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Category:
    id: str
    name: str


# 展示顺序即定义顺序（前端用同一顺序排组；id 是稳定键，改名不影响过滤）。
CATEGORY_LIST: tuple[Category, ...] = (
    Category("momentum", "动量趋势"),
    Category("volatility", "波动风险"),
    Category("pattern", "技术形态"),
    Category("price_volume", "量价基础"),
    Category("alpha_classic", "经典Alpha库"),
    Category("micro", "微观结构"),
    Category("flow", "资金流与情绪"),
    Category("concept", "概念与事件"),
    Category("valuation", "估值"),
    Category("quality", "财务质量"),
    Category("growth", "成长预期"),
    Category("style", "风格与行业"),
    Category("other", "其他/未分类"),
)
CATEGORY_BY_ID: dict[str, Category] = {c.id: c for c in CATEGORY_LIST}
OTHER_ID = "other"

# ── ③ 词表 A：factor1300 name_map 的 23 源类 → 12 类 ───────────────────
_NM_TO_CAT: dict[str, str] = {
    "Alpha因子": "alpha_classic",
    "技术因子": "pattern",
    "基本面因子": "quality",
    "情绪/资金流因子": "flow",
    "盈利/质量因子": "quality",
    "杠杆/风险因子": "quality",
    "成长因子": "growth",
    "波动性因子": "volatility",
    "动量因子": "momentum",
    "资产结构因子": "quality",
    "效率因子": "quality",
    "估值因子": "valuation",
    "乖离率因子": "pattern",
    "Smart Beta因子": "style",
    "流动性因子": "price_volume",
    "分析师预期因子": "growth",
    "形态因子": "pattern",
    "费用因子": "quality",
    "ESG因子": "quality",
    "筹码结构因子": "flow",
    "规模因子": "style",
    "盈利惊喜因子": "growth",
    "预期成长因子": "growth",
}

# ── ③ 词表 B：factor_defs formulas 的 10 源类 → 12 类 ──────────────────
_FD_TO_CAT: dict[str, str] = {
    "高频/盘口微结构": "micro",
    "其他/未分类": "other",
    "日频量价": "price_volume",
    "动量/反转/趋势": "momentum",
    "资金流/持仓筹码": "flow",
    "波动/风险": "volatility",
    "市场情绪/羊群": "flow",
    "风格/中性化/截面": "style",
    "非流动性/交易成本": "price_volume",
    "行业景气/财务": "quality",
}

# ── ① 手工表：每日特征 44 例 + 散码（穷举锁死，优先于一切规则） ────────
# 每日特征里的 future_return_* 是前视收益标签（非因子），不给任何语义类。
_EXACT: dict[str, str] = {
    # 每日特征（技术+估值）44
    "amount_ma_5": "flow",
    "annual_net_profit": "quality",
    "net_profit_ttm": "quality",
    "revenue_ttm": "quality",
    "equity": "quality",
    "is_st": "quality",
    "dividend_rate": "valuation",
    "pe_static": "valuation",
    "pe_ttm": "valuation",
    "pb": "valuation",
    "ps_ttm": "valuation",
    "total_capital": "style",
    "float_mv": "style",
    "total_mv": "style",
    "free_float_shares": "style",
    "circulating_capital": "style",
    "beta_20": "volatility",
    "volume_ma_3": "price_volume",
    "volume_trend_3d": "price_volume",
    "vol_std_60": "volatility",
    "vol_to_ma20": "price_volume",
    "vol_to_ma5": "price_volume",
    "kdj_d": "pattern",
    "kdj_j": "pattern",
    "kdj_k": "pattern",
    "ma5": "pattern",
    "ma10": "pattern",
    "ma20": "pattern",
    "ma60": "pattern",
    "ma_gap_5": "pattern",
    "ma_gap_10": "pattern",
    "ma_gap_20": "pattern",
    "macd_dea": "pattern",
    "macd_dif": "pattern",
    "macd_hist": "pattern",
    "rsi_14": "pattern",
    "rsi_6": "pattern",
    "pct_change": "momentum",
    "future_return_1d": "other",
    "future_return_3d": "other",
    "future_return_5d": "other",
    "future_return_10d": "other",
    "future_return_20d": "other",
    "future_return_60d": "other",
    # 散码（候选池裸码 / 空档挖掘 / factor_defs 裸价码）
    "limit_down": "concept",
    "prev_close": "price_volume",
    "vpc_20": "price_volume",
    "spatio_temporal_time": "micro",
    "Close": "price_volume",
    "Open": "price_volume",
    "MG2_BALCHG_3M": "flow",
    "NEWS_N20": "flow",
    "NEWS_N5": "flow",
    "NEWS_PM": "flow",
}

# ── ④ 前缀规则表（有序，先匹配先得） ────────────────────────────────────
_PREFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 经典公式库（多逻辑混合，不硬拆成动量/波动）
    (re.compile(r"^(a101_|gtja_|a158_|Alpha95|Alpha101_|Alpha191_)"), "alpha_classic"),
    # Alpha360 裸窗口（CLOSE0..59 等六族）
    (re.compile(r"^(CLOSE|OPEN|HIGH|LOW|VOLUME|VWAP)\d+$"), "price_volume"),
    (re.compile(r"^(JQ110_|TDXGS_)"), "pattern"),
    (re.compile(r"^micro_|^spatio_"), "micro"),
    (re.compile(r"^flow_cancel_|^flow_order_"), "micro"),
    (re.compile(r"^flow_"), "flow"),
    (re.compile(r"^(amt_|mfi_|obv_)"), "flow"),
    (re.compile(r"^mom_"), "momentum"),
    (re.compile(r"^vol_"), "volatility"),
    (re.compile(r"^tech_"), "pattern"),
    (re.compile(r"^(turn_|liq_)"), "price_volume"),
    (re.compile(r"^(ind_|style_|IND_)"), "style"),
    (re.compile(r"^(fun_|FIN_)"), "quality"),
    (re.compile(r"^chip_"), "flow"),
    (re.compile(r"^(concept_|CONCEPT_)"), "concept"),
    (re.compile(r"^HD_"), "flow"),
    (re.compile(r"^(lu_|ld_)"), "concept"),
    (re.compile(r"^(mg_|sl_|MG)"), "flow"),
    (re.compile(r"^NEWS_"), "flow"),
)

# ── ⑤ 挖掘因子词根表（有序；仅对前四级未命中的透明英文码生效） ──────────
_KEYWORD_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"volatil|semivar|varasym|drawdown|maxdd|\bvar(?:\d|_)|tail"),
        "volatility",
    ),
    # Amihud/ILLIQ/liq 是非流动性度量 —— 与词表口径一致归量价基础（L1 liq_ 同族）；
    # vp_* 是量价（volume-price）组合族，同归。
    (re.compile(r"amihud|illiq|liquid|liq|(?:^|_)vp"), "price_volume"),
    (re.compile(r"turnover|\bturn|money|netflow|mfi|obv|amt|accum"), "flow"),
    (re.compile(r"concept|news|sentiment|herd|hot_|_hot|limit"), "concept"),
    (re.compile(r"jump|spread|depth|imbalance|tick|order|intraday"), "micro"),
    (re.compile(r"overnight"), "momentum"),
    (re.compile(r"pe_|pb_|roe|profit|earn|grow|debt|asset|fin_|bp_|value"), "quality"),
    (re.compile(r"float_mv|total_mv|\bcirc|size|beta"), "style"),
    (
        re.compile(
            r"ret\d|ret_|\bret\b|mom|trend|rev\d|rev_|roc|alpha|"
            r"ma\d|ma_(?:align|div|fan)|ema|macd|rsi|kdj|boll|cci|clv|amp|range|atr|std|"
            r"vol|skew|kurt|close|high|low|open|vwap|corr|position"
        ),
        "pattern",
    ),
)


# ── 词表装载（mtime 键缓存：词表重建后自动失效，不用重启） ──────────────
_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def _load_json(path: Path):
    """读 JSON；文件缺失/损坏 → None（调用方降级，不当异常）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("因子分类词表读取失败（该层降级）%s: %s", path, e)
        return None
    with _lock:
        _cache[key] = (mtime, obj)
    return obj


def _data_root() -> Path:
    """数据根：quantdb 目录的父级（容器 /data、仓库 data/）。"""
    return resolve_quantdb_dir().parent


def _name_map() -> dict:
    obj = _load_json(_data_root() / "factor1300" / "name_map.json")
    return obj if isinstance(obj, dict) else {}


def _formula_cats() -> dict[str, str]:
    obj = _load_json(_data_root() / "factor_defs" / "formulas.json")
    if not isinstance(obj, list):
        return {}
    return {
        e["name"]: (e.get("category") or "")
        for e in obj
        if isinstance(e, dict) and e.get("name")
    }


_CJK = re.compile(r"[一-鿿]")


def classify_code(code: str) -> str:
    """因子代码 → 类别 id（全链未命中 → ``other``，绝不抛）。"""
    code = str(code or "").strip()
    if not code:
        return OTHER_ID
    if code in _EXACT:
        return _EXACT[code]
    nm = _name_map()
    if code in nm:
        entry = nm[code] if isinstance(nm[code], dict) else {}
        mapped = _NM_TO_CAT.get(entry.get("category") or "")
        if mapped:
            return mapped
    cat = _formula_cats().get(code)
    if cat:
        mapped = _FD_TO_CAT.get(cat)
        if mapped:
            # 源词表标「其他/未分类」的按原样落 other（不硬猜）
            return mapped
    for rx, cid in _PREFIX_RULES:
        if rx.match(code):
            return cid
    for rx, cid in _KEYWORD_RULES:
        if rx.search(code):
            return cid
    return OTHER_ID


def category_for(code: str) -> Category:
    """代码 → Category（未知 id 兜底 other）。"""
    return CATEGORY_BY_ID.get(classify_code(code), CATEGORY_BY_ID[OTHER_ID])


def chinese_name(code: str) -> str | None:
    """name_map 里的中文名（仅当确含汉字；Alpha101_1 这类非中文名不采纳）。"""
    entry = _name_map().get(str(code or "").strip())
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name_cn") or "").strip()
    return name if name and _CJK.search(name) else None


def has_chinese(text: str) -> bool:
    """是否含汉字 —— 供调用方判断「机械名」（如 f13 0129 无汉字才可被词表中文名升级）。"""
    return bool(_CJK.search(str(text or "")))


def enrich(entry: dict) -> dict:
    """因子元数据 → 新 dict（不改原对象）：补 ``category``/``category_name``，
    并把「空或等于代码」的 ``name_cn`` 补成词表里的中文名（不覆盖已有真名）。"""
    code = str(entry.get("code") or "")
    out = {**entry}
    cat = category_for(code)
    out["category"] = cat.id
    out["category_name"] = cat.name
    name = str(out.get("name_cn") or "").strip()
    if not name or name == code:
        cn = chinese_name(code)
        if cn:
            out["name_cn"] = cn
    return out
