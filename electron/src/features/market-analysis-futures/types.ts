/** 期货市场分析 · 类型定义（/api/v1/market-analysis-futures）
 *
 * 口径约定（与后端 feed 模块一致，页面必须如实呈现）：
 * - 所有金额字段**已预换算**：`*_yi` 为亿、`*_wan` 为万手/万张，前端只做展示不再缩放
 * - 国际品种与上金所无量仓数据 → 对应字段为 null（显示「—」，不是 0）
 * - `pct_change` 为主力连续口径，含换月跳空（页面已注明）
 */

// ---- /status ----

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
