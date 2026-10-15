/** 期货市场分析 · 类型定义（/api/v1/market-analysis-futures）
 *
 * 口径约定（与后端 feed 模块一致，页面必须如实呈现）：
 * - 所有金额字段**已预换算**：`*_yi` 为亿、`*_wan` 为万手/万张，前端只做展示不再缩放
 * - 国际品种与上金所无量仓数据 → 对应字段为 null（显示「—」，不是 0）
 * - `pct_change` 为主力连续口径，含换月跳空（页面已注明）
 */

// ---- /status ----

/** 数据集质检行（/status.datasets）：新鲜度三色由后端判定 */
export interface FuturesDatasetQuality {
  key: string;
  name: string;
  latest: string | null;
  coverage: string;
  freshness: 'fresh' | 'stale' | 'unavailable';
  note: string | null;
}

export interface FuturesFeedStatus {
  available: boolean;
  data_dir: string;
  /** 最新 K 线分区（可能含夜盘先行落盘的「不完整」分区） */
  kline_latest: string | null;
  /** 最新「完整交易日」（国内品种齐全的分区） */
  kline_latest_full: string | null;
  contracts_latest: string | null;
  contracts_varieties: number;
  receipts_latest: string | null;
  receipts_venues: string[];
  cftc_latest: string | null;
  fx_latest: string | null;
  universe_size: number;
  sectors: Record<string, string>;
  notes: Record<string, string>;
  /** 十项数据集质检（后端 P1 新增，旧响应可能缺省） */
  datasets?: FuturesDatasetQuality[];
}

// ---- Tab1 行情总览 ----

export interface FuturesGroupStat {
  total: number;
  up: number;
  down: number;
  flat: number;
  median_pct: number | null;
  /** 有效涨跌幅的品种数（排除停牌/无数据） */
  active: number;
  turnover_yi: number | null;
  oi_amount_yi: number | null;
  oi_change_wan: number | null;
}

export interface FuturesSectorRef {
  sector: string;
  name: string;
  median_pct: number | null;
}

export interface FuturesOverview {
  trade_date: string | null;
  totals: FuturesGroupStat;
  domestic: FuturesGroupStat;
  international: FuturesGroupStat;
  sge: FuturesGroupStat;
  top_sector: FuturesSectorRef | null;
  bottom_sector: FuturesSectorRef | null;
  /** 大盘基准：中证商品期货指数（官方口径区间/年化收益） */
  benchmark?: FuturesBenchmark | null;
  /** 交易所官方真实成交额（最新完整日；区别于名义估算口径） */
  market_turnover?: FuturesMarketTurnover | null;
}

export interface FuturesBenchmark {
  name: string;
  code: string | null;
  date: string;
  close: number | null;
  pct_change: number | null;
  change_1m: number | null;
  change_3m: number | null;
  change_6m: number | null;
  ytd_change: number | null;
  annual_return_1y: number | null;
  annual_return_3y: number | null;
  annual_return_5y: number | null;
  annual_return_10y: number | null;
  history: Array<{ date: string; close: number | null }>;
  source: string;
}

export interface FuturesMarketTurnover {
  date: string;
  /** 四所合计（亿元；turnover 源侧万元已换算） */
  total_yi: number | null;
  by_venue_yi: Record<string, number>;
  row_count: number;
  /** 源侧无接口的交易所（恒缺，如实列出） */
  venues_missing: string[];
}

export type RankingKind = 'change' | 'turnover' | 'oi_amount' | 'oi_change';

export interface FuturesRankRow {
  symbol: string;
  name: string;
  sector: string;
  sector_name: string;
  exchange: string;
  unit: string;
  close: number | null;
  pct_change: number | null;
  volume_wan: number | null;
  oi_wan: number | null;
  oi_change_wan: number | null;
  turnover_yi: number | null;
  oi_amount_yi: number | null;
}

export interface FuturesRanking {
  trade_date: string | null;
  kind: RankingKind;
  gainers: FuturesRankRow[];
  losers: FuturesRankRow[];
  rows: FuturesRankRow[];
}

// ---- Tab2 板块热力 ----

export interface FuturesHeatItem {
  symbol: string;
  name: string;
  sector: string;
  sector_name: string;
  exchange: string;
  pct_change: number;
  close: number | null;
  oi_amount_yi: number | null;
  turnover_yi: number | null;
  has_oi: boolean;
}

export interface FuturesHeatmap {
  trade_date: string | null;
  items: FuturesHeatItem[];
}

export interface FuturesSectorStat {
  sector: string;
  name: string;
  count: number;
  up: number;
  down: number;
  median_pct: number | null;
  turnover_yi: number | null;
  oi_amount_yi: number | null;
  oi_change_wan: number | null;
  leader: { symbol: string; name: string; pct_change: number | null } | null;
  laggard: { symbol: string; name: string; pct_change: number | null } | null;
}

export interface FuturesSectorStats {
  trade_date: string | null;
  sectors: FuturesSectorStat[];
}

// ---- Tab3 期限结构 ----

export type CurveStructure = 'contango' | 'backwardation' | 'flat';

export interface FuturesCurvePoint {
  contract: string;
  /** 到期月 YYMM（如 2612） */
  ym: string;
  close: number | null;
  settlement: number | null;
  volume_wan: number | null;
  oi_wan: number | null;
  oi_amount_yi: number | null;
  /** 到期参考日（contract_meta 真值 → 当月 15 日估计） */
  expiry_date?: string | null;
  days_to_expiry?: number | null;
  /** true = 无 meta 品种的显式估计（DCE/GFEX 全部走估计） */
  expiry_estimated?: boolean;
}

export interface FuturesTermStructure {
  stem: string;
  name: string;
  sector: string;
  sector_name: string;
  trade_date: string | null;
  curve: FuturesCurvePoint[];
  structure: CurveStructure | null;
  near_far_spread_pct: number | null;
  /** 年化展期收益 %（统一月差口径；正 = Back 结构多头展期收益） */
  carry_annual_pct?: number | null;
  carry_months?: number | null;
  near_expiry_date?: string | null;
  near_days_to_expiry?: number | null;
  far_expiry_date?: string | null;
  far_days_to_expiry?: number | null;
  main_contract: string | null;
  /** 'oi'（持仓量最大）或 'volume'（成交量最大，持仓缺失时兜底） */
  main_by: string | null;
  total_oi_wan: number | null;
  near_contract: string | null;
  near_price: number | null;
  far_contract: string | null;
  far_price: number | null;
}

export interface FuturesTermRankRow {
  stem: string;
  name: string;
  sector: string;
  sector_name: string;
  contracts: number;
  total_oi_wan: number | null;
  carry_annual_pct?: number | null;
  carry_months?: number | null;
  /** 年化 carry 的截面分位（0-100，分母 = 当期有 carry 的品种数） */
  carry_pct?: number | null;
  near_days_to_expiry?: number | null;
  near_contract: string | null;
  near_price: number | null;
  far_contract: string | null;
  far_price: number | null;
  near_far_spread_pct: number | null;
  structure: CurveStructure | null;
}

export interface FuturesTermRanking {
  trade_date: string | null;
  rows: FuturesTermRankRow[];
}

// ---- Tab4 持仓分析 ----

export interface CftcRow {
  item: string;
  net: number | null;
  net_prev: number | null;
  net_change: number | null;
  long: number | null;
  short: number | null;
}

export interface CftcBlock {
  report_date: string | null;
  prev_date: string | null;
  rows: CftcRow[];
}

export interface FuturesCftc {
  speculative: CftcBlock;
  commercial: CftcBlock;
}

export type OiQuadrant = 'build_long' | 'build_short' | 'short_covering' | 'long_unwind';

export interface OiChangeRow {
  symbol: string;
  name: string;
  sector: string;
  sector_name: string;
  close: number | null;
  pct_change_n: number | null;
  oi_wan: number | null;
  oi_change_wan: number | null;
  oi_pct_n: number | null;
  oi_amount_yi: number | null;
  oi_amount_change_yi: number | null;
  quadrant: OiQuadrant | null;
  quadrant_label: string | null;
}

export interface FuturesOiChanges {
  trade_date: string | null;
  base_date: string | null;
  days: number;
  rows: OiChangeRow[];
  quadrants: Record<OiQuadrant, number>;
}

// ---- Tab5 仓单库存 ----

export interface ReceiptRow {
  variety: string;
  name: string;
  venue: string;
  venue_name: string;
  sector: string;
  receipt_qty_wan: number | null;
  change_wan: number | null;
}

export interface ReceiptHistoryPoint {
  date: string;
  receipt_qty_wan: number | null;
  venue: string;
}

export interface ReceiptHistory {
  variety: string;
  name: string;
  points: ReceiptHistoryPoint[];
  latest: string | null;
  latest_qty_wan: number | null;
  change_wan: number | null;
  change_days: number | null;
}

export interface FuturesReceipts {
  latest_date: string | null;
  prev_date: string | null;
  venues: string[];
  venue_names: Record<string, string>;
  variety_count: number;
  rows: ReceiptRow[];
  history: ReceiptHistory | null;
}

// ---- Tab6 内外盘与期现价差 ----

export interface CrossMarketPoint {
  date: string;
  domestic: number | null;
  intl_cny: number | null;
  spread: number | null;
  spread_pct: number | null;
}

export interface CrossMarketPair {
  key: string;
  name: string;
  sector: string;
  sector_name: string;
  domestic_symbol: string;
  domestic_name: string;
  domestic_unit: string;
  intl_symbol: string;
  intl_name: string;
  intl_unit: string;
  /** 折算公式说明（页面原样展示） */
  formula: string;
  /** 税制口径说明（页面原样展示） */
  tax: string;
  trade_date: string | null;
  domestic_close: number | null;
  intl_close_native: number | null;
  intl_close_cny: number | null;
  spread: number | null;
  spread_pct: number | null;
  points: CrossMarketPoint[];
}

export interface FuturesCrossMarket {
  trade_date: string | null;
  fx: { date: string | null; rate: number | null } | null;
  pairs: CrossMarketPair[];
}

export type BasisState = 'premium' | 'discount' | 'flat';

export interface PreciousBasisPoint {
  date: string;
  spot: number | null;
  fut: number | null;
  basis: number | null;
  basis_pct: number | null;
}

export interface PreciousBasisPair {
  key: string;
  name: string;
  spot_symbol: string;
  spot_name: string;
  fut_symbol: string;
  fut_name: string;
  unit: string;
  trade_date: string | null;
  spot_close: number | null;
  fut_close: number | null;
  basis: number | null;
  basis_pct: number | null;
  /** premium=现货升水 / discount=现货贴水 / flat=平水 */
  state: BasisState | null;
  points: PreciousBasisPoint[];
}

export interface FuturesPreciousBasis {
  trade_date: string | null;
  pairs: PreciousBasisPair[];
}

// ---- Tab3 前瞻视角（五因子截面分位合成） ----

export type OutlookTier = 'leading' | 'mid' | 'lagging';

/** 展示面三档文案：只报位置，不给买卖词汇（平台军规） */
export const TIER_LABEL: Record<OutlookTier, string> = {
  leading: '靠前',
  mid: '居中',
  lagging: '靠后',
};

/** 合成因子键（member_net 为纯事实展示，不入合成） */
export type OutlookFactorKey = 'carry' | 'basis' | 'inv_chg' | 'mom_20d' | 'seasonality' | 'member_net';

export interface OutlookFactor {
  /** 因子原始值（单位见 factor_meta；member_net 为手） */
  value: number | null;
  /** 0-100 分位（参照系见 factor_meta.ref）；member_net 恒 null */
  pct: number | null;
  as_of?: string | null;
  sample_days?: number | null;
  years?: number | null;
  /** 仅 member_net：净多额（万手）与日变化 */
  value_wan?: number | null;
  delta_wan?: number | null;
}

export interface OutlookRow {
  symbol: string;
  symbol_cn: string;
  name: string;
  sector: string;
  sector_name: string;
  /** 可用因子分位等权均值（0-100）；覆盖 < 2 因子 → null（「—」显形） */
  composite: number | null;
  coverage: number;
  tier: OutlookTier | null;
  factors: Record<OutlookFactorKey, OutlookFactor>;
}

export interface OutlookFactorMeta {
  name: string;
  /** 参照系：cs=当日截面分位 / ts250=自身250d时序分位 / ts_inv=自身全史分位取反 / fact=纯事实 */
  ref: 'cs' | 'ts250' | 'ts_inv' | 'fact';
  unit: string;
  direction: 'positive' | 'negative' | 'none';
  definition: string;
  source: string;
  note: string;
}

export interface FuturesOutlook {
  as_of: string | null;
  weights: Record<string, number>;
  composite_quantiles: { q33: number | null; q67: number | null };
  factor_meta: Record<OutlookFactorKey, OutlookFactorMeta>;
  universe_size: number;
  covered_size: number;
  /** 覆盖不足被排除的品种说明（null = 全数入板） */
  excluded_note: string | null;
  rows: OutlookRow[];
}

// ---- 现货基差（生意社 52 品种） ----

export interface BasisRow {
  symbol: string;
  name: string;
  sector: string;
  sector_name: string;
  as_of: string;
  is_latest: boolean;
  spot_price: number | null;
  near_contract: string | null;
  near_price: number | null;
  near_basis_rate_pct: number | null;
  dom_contract: string | null;
  dom_price: number | null;
  dom_basis_rate_pct: number | null;
  /** 主力基差率在自身近 250 交易日的时序分位（样本 < 60 日 → null） */
  dom_pct_250: number | null;
  sample_days: number;
}

export interface BasisHistoryPoint {
  date: string;
  spot_price: number | null;
  dom_price: number | null;
  dom_basis_rate_pct: number | null;
  near_basis_rate_pct: number | null;
}

export interface FuturesBasis {
  as_of: string | null;
  rows: BasisRow[];
  variety_count: number;
  history: { symbol: string; name: string; points: BasisHistoryPoint[] } | null;
  notes: string[];
}

// ---- 席位排名（新浪，SHFE/CZCE） ----

export interface MemberRow {
  symbol: string;
  name: string;
  sector: string;
  sector_name: string;
  venue: string | null;
  contract: string | null;
  long_wan: number | null;
  short_wan: number | null;
  net_wan: number | null;
  net_delta_wan: number | null;
  top5_long_share_pct: number | null;
  top5_short_share_pct: number | null;
  n_long: number;
  n_short: number;
}

export interface MemberSeat {
  rank: number | null;
  member: string;
  size_wan: number | null;
  delta_wan: number | null;
}

export interface MembersBoard {
  venue: string;
  contract: string;
  long: MemberSeat[];
  short: MemberSeat[];
  volume: MemberSeat[];
}

export interface FuturesMembers {
  as_of: string | null;
  sample_days: number;
  rows: MemberRow[];
  variety_count?: number;
  board: MembersBoard | null;
  net_trend: Array<{ date: string; net_wan: number | null }>;
  side_labels?: Record<string, string>;
  notes: string[];
}

// ---- 东财库存（59 品种，与仓单互为印证） ----

export type InventoryCycle = 'build' | 'draw' | 'flat';

export interface InventoryEmRow {
  symbol: string;
  name: string;
  as_of: string;
  /** 品种 as-of 落后全局最新 > 7 天（源侧停更，显形不冒充） */
  out_of_sync: boolean;
  inventory: number | null;
  delta_1d: number | null;
  chg_5d_pct: number | null;
  chg_20d_pct: number | null;
  /** 自身全历史分位（0-100） */
  pct_hist: number | null;
  cycle: InventoryCycle | null;
  sample_days: number;
}

export interface FuturesInventoryEm {
  as_of: string | null;
  rows: InventoryEmRow[];
  variety_count: number;
  fresh_count?: number;
  history: {
    symbol: string;
    name: string;
    points: Array<{ date: string; inventory: number | null; delta: number | null }>;
  } | null;
  notes: string[];
}

// ---- 品种画像（/variety 聚合） ----

export interface CarryHistoryPoint {
  date: string;
  carry_annual_pct: number | null;
  months: number;
  near_contract: string;
  far_contract: string;
}

export interface PriceOiPoint {
  date: string;
  close: number | null;
  position: number | null;
  volume: number | null;
}

export interface FuturesVarietyProfile {
  symbol: string;
  symbol_cn: string;
  name: string;
  sector: string;
  sector_name: string;
  exchange: string;
  unit: string;
  outlook: OutlookRow | null;
  term: FuturesTermStructure;
  carry_history: CarryHistoryPoint[];
  basis: FuturesBasis;
  inventory: FuturesInventoryEm;
  price_oi: PriceOiPoint[];
  members: FuturesMembers;
}
