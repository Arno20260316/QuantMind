"""期货品种宇宙 —— 名称 / 板块 / 交易所 / 合约乘数 / 量仓可用性（唯一出处）。

三张基础表 + 一张推导总表 `FUTURES_META`：
  - `CN_MAIN` / `FOREIGN_SYMBOLS` / `SGE_SYMBOLS`：symbol 清单与中文名
    （原在 backend/scripts/akshare_futures_sync.py，2026-10-15 迁来共享层，
    脚本层 re-import 保持兼容）
  - `CN_MULTIPLIER`：国内品种合约乘数 —— 由 akshare `futures_fees_info()`
    实测生成（2026-10-15 快照，90 品种乘数唯一，含 CFFEX 金融期货）；
    仅 `LR`（晚籼稻，无成交合约）不在接口返回中，按同类稻谷口径补 20
  - `CN_SECTOR`：84 个国内品种 → 9 大板块手工分类

量仓可用性（实测口径，2026-10-15）：
  - `.CN` 国内主力：持仓量/成交量可用（futures_main_sina 提供）
  - `.FUT` 国际：`futures_foreign_hist` 的 volume/position/settlement 实测恒 0
    → 只做价格分析，量仓列不展示
  - SGE 上金所现货：`spot_hist_sge` 仅 OHLC 五列 → 无任何量仓
"""

from __future__ import annotations

# ---- symbol 清单（中文名） ----

# 国际期货品种: (akshare 代码, 名称)
FOREIGN_SYMBOLS: dict[str, str] = {
    "CL": "NYMEX原油",
    "OIL": "布伦特原油(II)",
    "NG": "NYMEX天然气",
    "GC": "COMEX黄金",
    "SI": "COMEX白银",
    "XAU": "伦敦金现货",
    "XAG": "伦敦银现货",
    "XPT": "伦敦铂现货",
    "XPD": "伦敦钯现货",
    "HG": "COMEX铜",
    "AHD": "LME铝迷你",
    "CAD": "LME铜迷你",
    "ZSD": "LME锌迷你",
    "NID": "LME镍迷你",
    "SND": "LME锡迷你",
    "PBD": "LME铅迷你",
    "S": "美大豆",
    "W": "美小麦",
    "C": "美玉米",
    "BO": "美豆油",
    "SM": "美豆粕",
    "LHC": "美活牛",
    "CT": "ICE棉花",
    "FCPO": "马来棕榈油",
    "RSS3": "日胶RSS3",
    "RS": "国际菜籽",
    "TRB": "国际废钢",
    "FEF": "欧元外汇期货",
    "EUA": "欧盟碳配额",
    "BTC": "比特币期货",
}

# 国内主力连续: (新浪代码, 品种名)
CN_MAIN: dict[str, str] = {
    # 大商所 dce
    "V0": "PVC", "P0": "棕榈油", "B0": "豆二", "M0": "豆粕", "I0": "铁矿石",
    "JD0": "鸡蛋", "L0": "塑料", "PP0": "聚丙烯", "FB0": "纤维板", "Y0": "豆油",
    "C0": "玉米", "A0": "豆一", "J0": "焦炭", "JM0": "焦煤", "CS0": "淀粉",
    "EG0": "乙二醇", "RR0": "粳米", "EB0": "苯乙烯", "PG0": "液化石油气",
    "LH0": "生猪", "LG0": "原木", "BZ0": "纯苯",
    # 郑商所 czce
    "TA0": "PTA", "OI0": "菜油", "RS0": "菜籽", "RM0": "菜粕", "WH0": "强麦",
    "JR0": "粳稻", "SR0": "白糖", "CF0": "棉花", "RI0": "早籼稻", "MA0": "甲醇",
    "FG0": "玻璃", "LR0": "晚籼稻", "SF0": "硅铁", "SM0": "锰硅", "CY0": "棉纱",
    "AP0": "苹果", "CJ0": "红枣", "UR0": "尿素", "SA0": "纯碱", "PF0": "短纤",
    "PK0": "花生", "SH0": "烧碱", "PX0": "对二甲苯", "PR0": "瓶片", "PL0": "丙烯",
    # 上期所 shfe / 能源中心 ine
    "FU0": "燃料油", "SC0": "上海原油", "AL0": "沪铝", "RU0": "天然橡胶",
    "ZN0": "沪锌", "CU0": "沪铜", "AU0": "沪金", "RB0": "螺纹钢", "PB0": "沪铅",
    "AG0": "沪银", "BU0": "沥青", "HC0": "热轧卷板", "SN0": "沪锡", "NI0": "沪镍",
    "SP0": "纸浆", "NR0": "20号胶", "SS0": "不锈钢", "LU0": "低硫燃料油",
    "BC0": "国际铜", "AO0": "氧化铝", "BR0": "丁二烯橡胶", "EC0": "集运指数欧线",
    "AD0": "铸造铝合金", "OP0": "胶版印刷纸",
    # 中金所 cffex
    "IF0": "沪深300指数", "IH0": "上证50指数", "IC0": "中证500指数",
    "IM0": "中证1000指数", "TF0": "5年期国债", "TS0": "2年期国债",
    "T0": "10年期国债", "TL0": "30年期国债",
    # 广期所 gfex
    "SI0": "工业硅", "LC0": "碳酸锂", "PS0": "多晶硅", "PT0": "铂", "PD0": "钯",
}

# 上金所贵金属现货
SGE_SYMBOLS: list[str] = [
    "Au99.99", "Au99.95", "Au100g", "Au(T+D)", "Ag(T+D)", "mAu(T+D)", "Ag99.99", "Pt99.95",
]

# ---- 板块 ----

SECTORS: dict[str, str] = {
    "precious": "贵金属",
    "nonferrous": "有色金属",
    "ferrous": "黑色建材",
    "energy": "能源",
    "chemical": "化工",
    "agri": "农产品",
    "financial": "金融与指数",
    "green": "新能源",
    "other": "其他",
}

_CN_SECTOR: dict[str, str] = {
    # 贵金属
    "AU": "precious", "AG": "precious", "PT": "precious", "PD": "precious",
    # 有色
    "CU": "nonferrous", "AL": "nonferrous", "ZN": "nonferrous", "PB": "nonferrous",
    "NI": "nonferrous", "SN": "nonferrous", "BC": "nonferrous", "AO": "nonferrous",
    "AD": "nonferrous",
    # 黑色建材
    "RB": "ferrous", "HC": "ferrous", "SS": "ferrous", "WR": "ferrous",
    "I": "ferrous", "J": "ferrous", "JM": "ferrous", "SF": "ferrous",
    "SM": "ferrous", "LG": "ferrous", "FB": "ferrous",
    # 能源
    "SC": "energy", "LU": "energy", "FU": "energy", "BU": "energy", "PG": "energy",
    # 化工
    "TA": "chemical", "MA": "chemical", "FG": "chemical", "SA": "chemical",
    "UR": "chemical", "SH": "chemical", "PX": "chemical", "PR": "chemical",
    "PL": "chemical", "PF": "chemical", "L": "chemical", "V": "chemical",
    "PP": "chemical", "EG": "chemical", "EB": "chemical", "BZ": "chemical",
    "RU": "chemical", "NR": "chemical", "BR": "chemical", "SP": "chemical",
    "OP": "chemical",
    # 农产品
    "A": "agri", "B": "agri", "M": "agri", "Y": "agri", "P": "agri", "C": "agri",
    "CS": "agri", "JD": "agri", "LH": "agri", "RR": "agri", "SR": "agri",
    "CF": "agri", "CY": "agri", "OI": "agri", "RM": "agri", "RS": "agri",
    "AP": "agri", "CJ": "agri", "PK": "agri", "WH": "agri", "JR": "agri",
    "RI": "agri", "LR": "agri",
    # 金融与指数
    "IF": "financial", "IH": "financial", "IC": "financial", "IM": "financial",
    "T": "financial", "TF": "financial", "TS": "financial", "TL": "financial",
    "EC": "financial",
    # 新能源
    "SI": "green", "LC": "green", "PS": "green",
}

_FOREIGN_SECTOR: dict[str, str] = {
    "CL": "energy", "OIL": "energy", "NG": "energy", "EUA": "energy",
    "GC": "precious", "SI": "precious", "XAU": "precious", "XAG": "precious",
    "XPT": "precious", "XPD": "precious",
    "HG": "nonferrous", "AHD": "nonferrous", "CAD": "nonferrous",
    "ZSD": "nonferrous", "NID": "nonferrous", "SND": "nonferrous", "PBD": "nonferrous",
    "S": "agri", "W": "agri", "C": "agri", "BO": "agri", "SM": "agri",
    "LHC": "agri", "CT": "agri", "FCPO": "agri", "RSS3": "agri", "RS": "agri",
    "TRB": "ferrous", "FEF": "financial", "BTC": "other",
}

# ---- 合约乘数（akshare futures_fees_info 实测快照，2026-10-15） ----

CN_MULTIPLIER: dict[str, float] = {
    # CZCE
    "AP": 10, "CF": 5, "CJ": 5, "CY": 5, "FG": 20, "JR": 20, "MA": 10, "OI": 10,
    "PF": 5, "PK": 5, "PL": 20, "PR": 15, "PX": 5, "RI": 20, "RM": 10, "RS": 10,
    "SA": 20, "SF": 5, "SH": 30, "SM": 5, "SR": 10, "TA": 5, "UR": 20, "WH": 20,
    "LR": 20,  # 晚籼稻：无成交合约不在接口返回，按同类稻谷补
    # DCE
    "A": 10, "B": 10, "C": 10, "CS": 10, "EB": 5, "EG": 10, "FB": 10, "I": 100,
    "J": 100, "JD": 10, "JM": 60, "L": 5, "LG": 90, "LH": 16, "M": 10, "P": 10,
    "PG": 20, "PP": 5, "RR": 10, "V": 5, "Y": 10, "BZ": 30,
    # SHFE / INE
    "AD": 10, "AG": 15, "AL": 5, "AO": 20, "AU": 1000, "BC": 5, "BR": 5, "BU": 10,
    "CU": 5, "EC": 50, "FU": 10, "HC": 10, "NI": 1, "NR": 10, "OP": 40, "PB": 5,
    "RB": 10, "RU": 10, "SC": 1000, "SN": 1, "SP": 10, "SS": 5, "WR": 10, "ZN": 5,
    "LU": 10,
    # CFFEX
    "IC": 200, "IF": 300, "IH": 300, "IM": 200, "T": 10000, "TF": 10000,
    "TL": 10000, "TS": 20000,
    # GFEX
    "LC": 1, "PD": 1000, "PS": 3, "PT": 1000, "SI": 5,
}

# 报价单位特例（默认「元/吨」）
_CN_UNIT: dict[str, str] = {
    "AU": "元/克", "PT": "元/克", "PD": "元/克",
    "AG": "元/千克",
    "SC": "元/桶", "LU": "元/吨", "EC": "点",
    "IF": "点", "IH": "点", "IC": "点", "IM": "点",
    "T": "元", "TF": "元", "TS": "元", "TL": "元",
    "LG": "元/立方米", "FB": "元/立方米",
    "JD": "元/500千克", "LH": "元/吨",
}

_DCE = {"V", "P", "B", "M", "I", "JD", "L", "PP", "FB", "Y", "C", "A", "J",
        "JM", "CS", "EG", "RR", "EB", "PG", "LH", "LG", "BZ"}
_CZCE = {"TA", "OI", "RS", "RM", "WH", "JR", "SR", "CF", "RI", "MA", "FG", "LR",
         "SF", "SM", "CY", "AP", "CJ", "UR", "SA", "PF", "PK", "SH", "PX", "PR", "PL"}
_CFFEX = {"IF", "IH", "IC", "IM", "TF", "TS", "T", "TL"}
_GFEX = {"SI", "LC", "PS", "PT", "PD"}
_INE = {"SC", "NR", "LU", "BC", "EC"}


def _cn_exchange(stem: str) -> str:
    if stem in _DCE:
        return "DCE"
    if stem in _CZCE:
        return "CZCE"
    if stem in _CFFEX:
        return "CFFEX"
    if stem in _GFEX:
        return "GFEX"
    if stem in _INE:
        return "INE"
    return "SHFE"


def _build_meta() -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for code0, name in CN_MAIN.items():
        stem = code0[:-1]
        meta[f"{code0}.CN"] = {
            "name": name,
            "sector": _CN_SECTOR.get(stem, "other"),
            "exchange": _cn_exchange(stem),
            "multiplier": CN_MULTIPLIER.get(stem),
            "unit": _CN_UNIT.get(stem, "元/吨"),
            "has_oi": True,
            "has_volume": True,
        }
    for code, name in FOREIGN_SYMBOLS.items():
        meta[f"{code}.FUT"] = {
            "name": name,
            "sector": _FOREIGN_SECTOR.get(code, "other"),
            "exchange": "FOREIGN",
            "multiplier": None,
            "unit": "",
            "has_oi": False,
            "has_volume": False,
        }
    for sym in SGE_SYMBOLS:
        unit = "元/千克" if sym.startswith("Ag") or sym == "Ag99.99" else "元/克"
        meta[sym] = {
            "name": f"上金所{sym}",
            "sector": "precious",
            "exchange": "SGE",
            "multiplier": 1,
            "unit": unit,
            "has_oi": False,
            "has_volume": False,
        }
    return meta


FUTURES_META: dict[str, dict] = _build_meta()


def symbol_meta(symbol: str) -> dict:
    """取 symbol 元数据；未知 symbol 返回诚实兜底（other / 无乘数）。"""
    return FUTURES_META.get(
        symbol,
        {
            "name": symbol,
            "sector": "other",
            "exchange": "",
            "multiplier": None,
            "unit": "",
            "has_oi": False,
            "has_volume": False,
        },
    )


def sector_of(symbol: str) -> str:
    return symbol_meta(symbol)["sector"]


def name_of(symbol: str) -> str:
    return symbol_meta(symbol)["name"]
