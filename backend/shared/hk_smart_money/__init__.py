"""港股聪明钱（席位行为统计与类型效力裁定）· 口径常量唯一出处。

细案：docs/港股市场分析_聪明钱深度分析_细案_20261016.md
数据面：ccass_top50（席位明细）+ hsgt_south（南向直读）+ daily_forward（价/成交额）。

纪律（与 A 股 smart_money 一致）：
- 事件闸/冷却/嫌疑阈值只在本包定义，脚本与 API 一律引用；
- 事后收益列（fwd_*）只作展示与统计，绝不进任何信号构造；
- 缺失一律 None/NaN（展示「—」），绝不补 0。
"""

from __future__ import annotations

#: 统计覆盖的 6 类型（hkscc 防御类不出现在此；southbound 的 A 席不入 CCASS
#: 事件仓，其流向由 hsgt_south 直读——细案 §3.2）
CATEGORIES: tuple[str, ...] = (
    "southbound",
    "cn_broker",
    "us_eu",
    "hk",
    "apac",
    "other",
)

#: CCASS 席差分聚合的 5 类（南向从 CCASS 侧排除，防与 hsgt 双重计数）
CCASS_CATEGORIES: tuple[str, ...] = ("cn_broker", "us_eu", "hk", "apac", "other")

#: 南向结算席（CCASS 中 ≡ 港股通总量；用于与 hsgt_south 对账）
SOUTHBOUND_SEAT_IDS: tuple[str, ...] = ("A00002", "A00003", "A00004", "A00006")

# ---------------------------------------------------------------- 事件层（细案 §3.1）

#: 入事件闸：|Δvalue| 下限（港元）
EVENT_MIN_VALUE_HKD: float = 1_000_000.0
#: 入事件闸：|Δqty|/qty(prev) 下限（相对自席存量）
EVENT_MIN_PCT: float = 0.05
#: 价格门槛（仙股剔除，与 hk_opportunity 一致）
MIN_PRICE_HKD: float = 0.5
#: 过户/划转嫌疑：单席单日 |Δvalue| > 该比例 × 当日全市场成交额
TRANSFER_AMOUNT_RATIO: float = 1.2
#: episode 冷却：同席同股同向事件合并窗口（交易日）
EPISODE_COOLDOWN_DAYS: int = 5
#: 重算窗前的冷却状态回看（交易日；须 > 2×冷却）
EPISODE_SEED_DAYS: int = 15

#: 事件仓列（顺序即 parquet schema）
EVENT_COLUMNS: tuple[str, ...] = (
    "dt",
    "symbol",
    "stock_name",
    "participant_id",
    "participant_name",
    "category",
    "kind",
    "direction",
    "delta_qty",
    "delta_value",
    "delta_pct",
    "price",
    "day_amount",
    "transfer_suspect",
    "episode_lead",
    "fwd_ret_5",
    "fwd_ret_10",
    "fwd_ret_20",
    "fwd_exc_5",
    "fwd_exc_10",
    "fwd_exc_20",
)

#: 逐日类型净流列（全部 6 类型；含南向直读行）
FLOW_COLUMNS: tuple[str, ...] = (
    "dt",
    "symbol",
    "category",
    "net_value",
    "net_qty",
    "n_up",
    "n_dn",
    "n_active",
)

#: 事后收益展示/统计的 horizon（封存列专用；与信号 horizon 独立）
FWD_HORIZONS: tuple[int, ...] = (5, 10, 20)

# ---------------------------------------------------------------- 验证协议（细案 §3.3）
# 与 backend/scripts/hk_opportunity_ic_check.py 判据一字不改

HORIZONS: tuple[int, ...] = (5, 10, 20)
#: 入柱判据取 h=10（h5/h20 一同披露）
ADMIT_HORIZON: int = 10
MIN_ABS_IC: float = 0.01
MIN_DAYS_TAIL: int = 60
MIN_Q51_BP: float = 1.0
#: 入评估的日截面最少标的数
MIN_NAMES_PER_DAY: int = 100
#: 段内最少有效天数（不足 → 该段 None）
MIN_SEG_DAYS: int = 30
#: 相关塌缩阈值（|ρ| > 此值的成对信号保留 |IC| 强者）
CORR_COLLAPSE: float = 0.8
#: 信号暖机（交易日；20d std 与 20d 和的余量）
WARMUP_DAYS: int = 25

#: 类型 × 信号变体（CCASS 5 类）
CCASS_SIGNALS: tuple[str, ...] = ("net5_z", "net5_tt", "net20_tt", "breadth5")
#: 南向专属变体（hsgt 直读；单通道无 breadth）
SOUTH_SIGNALS: tuple[str, ...] = ("net5_z", "net5_tt", "net20_tt", "chg5")

SIGNAL_LABELS: dict[str, str] = {
    "net5_z": "净流入强度·5日z20",
    "net5_tt": "净流入占比·5日",
    "net20_tt": "净流入占比·20日",
    "breadth5": "净增席位广度·5日",
    "chg5": "持股变化·5日",
}

#: 信号名 → (类别, 变体) 解析用前缀
SIGNAL_KEY_SEP = "::"

# ---------------------------------------------------------------- 席位统计（细案 §3.4）

#: 席位榜窗口（交易日；0 = 全窗）
SEAT_WINDOWS: tuple[int, ...] = (20, 60, 0)
#: 参与排序的最少事件数（不足置灰披露）
SEAT_MIN_EVENTS: int = 5
#: 收缩权重 k：w = n/(n+k)（收缩到中性，非类型先验）
SEAT_SHRINK_K: float = 20.0
#: 中性先验（收缩目标）
SEAT_NEUTRAL_HIT: float = 0.5
SEAT_NEUTRAL_EXC: float = 0.0

#: 席位技能 90% 单侧下界 z 值（lb = skill − z×SE）——v2 榜主排序键，
#: 弱惩罚小样本：n 越小下界越低，噪声席位自然沉底（细案 §2 原则）
SEAT_SKILL_Z: float = 1.28
#: 持续性审计：散点/秩相关门槛（前半、后半方向技能可测事件数）
SEAT_PERSIST_MIN_N1: int = 20
SEAT_PERSIST_MIN_N2: int = 10
#: 持续性审计：四分位排名门槛（前半可测事件数；与前端榜默认门槛一致）
SEAT_PERSIST_QUARTILE_MIN_N: int = 50
#: 持续性审计：秩相关最少席数（不足返回 None，绝不由极小样本给结论）
SEAT_PERSIST_MIN_SEATS: int = 8

# ---------------------------------------------------------------- 复合分（细案 §3.5）

#: 发布分数的最少有效信号数（缺柱不计入，缺失绝不填 50）
SCORE_MIN_COVERED: int = 1
#: 共识榜条数
CONSENSUS_TOP_N: int = 200

# ---------------------------------------------------------------- 展示/披露

#: 趋势序列长度（交易日）
TREND_DAYS: int = 60
#: ccass 落后最近行情交易日超过此数（交易日）→ partial
STALE_PARTIAL_TRADE_DAYS: int = 4
