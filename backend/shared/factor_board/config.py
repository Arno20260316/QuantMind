"""因子概率板：全部阈值/路径/口径常量的唯一出处（T- 市场分析因子概率板 P0）。

口径纪律（改动任何数值前先读 docs/市场分析_因子概率板与预测耦合_细案_20261011.md §四/§六）：
- 价格侧一律 ``1_kline_data/daily_forward``（qfq）；**禁止**用 ``daily_backward``（hfq）
  算跨日收益——它是逐分区拼接的不自洽序列（2026-09-23 铁证，见 docs 与记忆）。
- symbol 一律 ``StockCodeUtil.to_suffix()``；PG ``engine_signal_scores``（CN）是裸 6 位码。
- 缺失一律 JSON ``null``，绝不落成 0。
"""

from __future__ import annotations

import os
from pathlib import Path

SCHEMA_VERSION = "factor_board/1.0"

# ── 因子池 ──────────────────────────────────────────────────────────
# kept-362 联合筛选清单里的日频库（factor_research 经典库为月频评估，不并入）
DAILY_LIBS: tuple[str, ...] = (
    "alpha_library",
    "alpha360",
    "jq110",
    "tdxgs",
    "l1_factors",
    "l2_factors",
    "rd_mined",
)
SELECTION_JSON_REL = Path("factor_research/screening/factor_selection.json")
RD_REPORT_REL = Path("6_ml_datasets/rd_mined/report/factor_report.json")
RD_SUBLIBRARY = "rd挖掘"
MIN_RD_REPORT_IC = 0.02  # rd 报告全窗 |IC| 粗筛门槛

# ── 有效性闸（近效自算 IC） ─────────────────────────────────────────
WINDOW_DATES = 85  # 收盘矩阵取最近 N 个交易日
EVAL_DAYS = 65  # 用于 IC60 的评估日数（需 t+5 前向）
IC_WINDOW = 60  # 汇总时取最后 60 个评估日
MIN_IC_DAYS = 40  # 有效 IC 天数下限，不足则该因子不可评估
MIN_ABS_IC60 = 0.02  # |IC60| 门槛
MIN_WIN_RATE = 0.5  # 方向胜率门槛
DEGRADE_WIN_RATE = 0.45  # 过闸数不足时的降级胜率
MIN_GATED = 15  # 低于此数触发降级
FAMILY_CAP = 3  # 每 (库,子库) 族入选上限
RD_FAMILY_CAP = 4  # rd_mined 族上限
MIN_STOCK_SAMPLE = 800  # 单日 IC 计算的最小标的数

# ── 权重与覆盖 ──────────────────────────────────────────────────────
W_CLIP_LO, W_CLIP_HI = 0.02, 0.15
COVERAGE_GATE = 0.7  # 已覆盖权重 <70% 总权重 → 该股合成置 null

# ── 持有期画像 ──────────────────────────────────────────────────────
HORIZON_DAYS: tuple[int, ...] = (1, 2, 5, 10, 20)
HORIZON_WINDOW = 45  # 最近 N 个评估日（每 h 都需 t+h 前向）

# ── 板块三层判定 ────────────────────────────────────────────────────
SECTOR_TYPES: tuple[str, ...] = ("行业板块(二级)", "行业板块(一级)", "概念板块")
SECTOR_KEY: dict[str, str] = {
    "行业板块(二级)": "industry_l2",
    "行业板块(一级)": "industry_l1",
    "概念板块": "concept",
}
SECTOR_MIN_N = 8  # 板块最小成分数
VERDICT_Q_HI, VERDICT_Q_LO = 0.67, 0.33
FB_HI = 0.5  # 个股广度（composite>0 占比）下限
FB_LO = 0.5  # 个股广度上限（看空侧）
MB_HI = 0.55  # 模型广度（rank_pct≥0.5 占比）下限
MB_LO = 0.45  # 模型广度上限（看空侧）

# ── 个股四象限/名单 ─────────────────────────────────────────────────
QUADRANT_HI, QUADRANT_LO = 0.7, 0.3
STOCK_LIST_N = 50  # top/bottom/共振名单长度
RESONANT_LIST_N = 50

# ── 模型信号 ────────────────────────────────────────────────────────
MODEL_MARKET = "CN"
MODEL_SOURCE = "batch"
MODEL_STALE_DAYS = 7  # 模型信号容忍的最大陈旧天数（超出→回退最新并告警）
MIN_MODEL_INTERSECTION = 500  # 与因子截面交集下限（低于→告警）
MODEL_MIN_ROWS = 2000  # 模型信号行数下限（低于→告警，疑似残缺 run）

# ── 数据质检 ────────────────────────────────────────────────────────
FACTOR_STALE_DAYS = 7  # 因子最新分区距今容忍天数
CLOSE_NONPOS_MAX_FRAC = 0.002  # close 非正值占比上限
MIN_COMPOSITE_TODAY = 3000  # 当日合成覆盖硬门
MIN_COMPOSITE_IC_DAY = 1000  # 单日验证 IC 的最小标的数
MIN_SELECTED = 10  # 入选因子数硬门
VALIDATION_WINDOW = 60  # 验证 IC 的评估日数
SECTOR_IC_MIN_SECTORS = 20  # 板块层验证的最小板块数

# ── 路径 ────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _in_container() -> bool:
    """容器判定与 update_feature_parquet.py 同款（供宿主开发用 HOST_MODE 覆盖）。"""
    return Path("/app").exists() and not os.environ.get("QUANTMINT_HOST_MODE")


def resolve_quantdb_root(explicit: str | Path | None = None) -> Path:
    """QuantDB 数据根：显式参数 > QM_QUANTDB_DATA_DIR > 容器 /data/quantdb > 仓库 data/。"""
    if explicit:
        return Path(explicit)
    env = os.environ.get("QM_QUANTDB_DATA_DIR")
    if env:
        return Path(env)
    if _in_container():
        return Path("/data/quantdb")
    return _REPO_ROOT / "data" / "quantdb"


def resolve_board_dir(explicit: str | Path | None = None) -> Path:
    """产物目录：显式参数 > QM_FACTOR_BOARD_DIR > 容器 /data/… > 仓库 data/…。"""
    if explicit:
        return Path(explicit)
    env = os.environ.get("QM_FACTOR_BOARD_DIR")
    if env:
        return Path(env)
    if _in_container():
        return Path("/data/market-analysis/factor_board")
    return _REPO_ROOT / "data" / "market-analysis" / "factor_board"


def thresholds_snapshot() -> dict:
    """写进产物 meta 的阈值快照（审计用，字段名稳定）。"""
    return {
        "window_dates": WINDOW_DATES,
        "eval_days": EVAL_DAYS,
        "ic_window": IC_WINDOW,
        "min_abs_ic60": MIN_ABS_IC60,
        "min_win_rate": MIN_WIN_RATE,
        "degrade_win_rate": DEGRADE_WIN_RATE,
        "min_gated": MIN_GATED,
        "family_cap": FAMILY_CAP,
        "rd_family_cap": RD_FAMILY_CAP,
        "w_clip": [W_CLIP_LO, W_CLIP_HI],
        "coverage_gate": COVERAGE_GATE,
        "horizon_days": list(HORIZON_DAYS),
        "sector_min_n": SECTOR_MIN_N,
        "verdict_q": [VERDICT_Q_LO, VERDICT_Q_HI],
        "mb_hi": MB_HI,
        "mb_lo": MB_LO,
        "quadrant": [QUADRANT_LO, QUADRANT_HI],
        "close_source": "1_kline_data/daily_forward(qfq)",
    }
