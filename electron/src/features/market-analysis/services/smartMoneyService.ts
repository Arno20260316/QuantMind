/** A 股聪明钱取数层（/api/v1/market-analysis/smart-money/*，细案 §5.2）。
 *
 * 契约：
 * - 404 detail='smart_money_not_built' → `notBuilt`（页面空态，不白屏）；
 *   evidence 生命周期独立（验证先于构建，可能建前即有）；
 * - 缺失字段一律 null 原样透传（界面显示「—」，**绝不补 0**）；
 * - symbol 前缀式（SH600036）；榜单 items 由后端按 score 降序给出。
 */

import { SERVICE_ENDPOINTS } from '../../../config/services';

const MA_API = `${SERVICE_ENDPOINTS.USER_SERVICE}/market-analysis`;

/** 携带状态码的取数错误；404（未构建）走 notBuilt 空态 */
export class SmartMoneyError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'SmartMoneyError';
    this.status = status;
  }

  get notBuilt(): boolean {
    return this.status === 404;
  }
}

export interface SmartPillarMeta {
  name: string;
  label: string;
  unit: string;
  /** 入柱方向（+1/−1；h10 实测 IC 符号）——负向柱分位已取反 */
  direction: number | null;
}

export interface SmartLatestEvent {
  type: string;
  date: string;
}

/** 选股榜条目（金额单位随柱原样：机构/龙虎榜=元，两融=万元） */
export interface SmartStockItem {
  symbol: string;
  name: string | null;
  industry: string | null;
  score: number | null;
  covered: number | null;
  n_pillars: number | null;
  pct: Record<string, number | null>;
  val: Record<string, number | null>;
  inst_net_5d: number | null;
  lhb_net_5d: number | null;
  margin_net_5d: number | null;
  north_q_delta_pp: number | null;
  latest_event: SmartLatestEvent | null;
}

export interface SmartSummary {
  pool_size: number | null;
  covered_mean: number | null;
}

export interface SmartNorthTopRow {
  symbol: string;
  stock_name: string | null;
  delta_pp: number | null;
  pct: number | null;
}

export interface SmartNorthSummary {
  quarter: string | null;
  report_date: string | null;
  n_total: number | null;
  n_inc: number | null;
  n_dec: number | null;
  top_inc: SmartNorthTopRow[];
  top_dec: SmartNorthTopRow[];
}

export interface SmartBranchRow {
  branch_name: string;
  net_amount: number | null;
  buy_amount: number | null;
  sell_amount: number | null;
  n_days: number;
  stocks_bought: string | null;
}

/** 行业净买聚合行（overview.industry；金额=亿元，avg_score=最新池内行业均分） */
export interface SmartIndustryRow {
  industry: string;
  lhb_net_yi: number | null;
  inst_net_yi: number | null;
  dzjy_inst_net_yi: number | null;
  n_stocks: number | null;
  avg_score: number | null;
}

export interface SmartOverviewPayload {
  lhb: {
    date: string | null;
    n_stocks: number | null;
    net_buy_yi: number | null;
    n20d_avg: number | null;
    net20d_sum_yi: number | null;
  };
  inst: { date: string | null; n_stocks: number | null; net_buy_yi: number | null };
  dzjy: { date: string | null; amount_yi: number | null; premium_share: number | null };
  survey: { date: string | null; n_stocks: number | null; n_inst: number | null };
  margin: { date: string | null; net_5d_yi: number | null; balance_yi: number | null };
  north: SmartNorthSummary;
  branches_top: SmartBranchRow[];
  /** 20 日行业净买聚合（Top 按机构+机构大宗净买绝对值降序） */
  industry?: SmartIndustryRow[] | null;
}

/** 趋势序列行：键=原始统计列名（lhb_n_stocks / margin_net_5d_yi 等），缺失 null */
export type SmartTrendRow = Record<string, number | string | null> & { date: string };

export interface SmartFreshness {
  sources: Record<string, { last_date?: string | null; rows?: number | null }>;
  margin_last: string | null;
  kline_last: string | null;
}

export interface SmartOverviewResponse {
  as_of: string | null;
  generated_at: string | null;
  partial: boolean | null;
  admitted: string[] | null;
  pillars: SmartPillarMeta[] | null;
  summary: SmartSummary | null;
  overview: SmartOverviewPayload | null;
  trends: Record<string, SmartTrendRow[]> | null;
  freshness: SmartFreshness | null;
  /** 北向披露边界文案（2024-08-19 起仅季度披露） */
  north_note?: string | null;
}

export interface SmartStocksResponse {
  as_of: string | null;
  generated_at: string | null;
  partial: boolean | null;
  admitted: string[] | null;
  pillars: SmartPillarMeta[] | null;
  summary: SmartSummary | null;
  /** 历史快照请求时回显原始请求日（as_of 才是真实快照日） */
  requested_date?: string | null;
  total: number;
  offset?: number;
  limit?: number;
  items: SmartStockItem[];
}

export interface SmartBranchesResponse {
  as_of: string | null;
  window: number;
  branches: SmartBranchRow[];
}

/** 双过闸单段统计（h=5/10） */
export interface SmartEvidenceSeg {
  n_days: number;
  rank_ic: number | null;
  icir: number | null;
  ic_pos_rate: number | null;
  q1_bp: number | null;
  q2_bp: number | null;
  q3_bp: number | null;
  q4_bp: number | null;
  q5_bp: number | null;
  q5_minus_q1_bp: number | null;
  tail_ok: boolean;
}

export interface SmartEvidenceSignal {
  name: string;
  family: string | null;
  min_names: number | null;
  coverage_per_day_avg?: number | null;
  admitted: boolean;
  direction: number | null;
  decision: string;
  results: Record<string, SmartEvidenceSeg | null>;
  by_year?: Record<string, Record<string, SmartEvidenceSeg | null>>;
}

/** IC 衰减单点（h → {ic, icir, n_days}；该视野无样本 → null） */
export interface SmartDecayPoint {
  ic: number | null;
  icir: number | null;
  n_days: number;
}

export interface SmartEvidenceResponse {
  validated_at: string | null;
  window: { eval_start: string; eval_end: string; warmup_start: string } | null;
  method: Record<string, unknown> | null;
  signals: SmartEvidenceSignal[];
  /** IC 衰减：{信号名|"composite": {"2"|"3"|"5"|"10"|"20": 点|null}}（h=1 恒 0 不出） */
  decay?: Record<string, Record<string, SmartDecayPoint | null>> | null;
  correlation: {
    pairs: Array<{ a: string; b: string; rho: number | null }>;
    collapsed: Array<{ signal: string; kept: string; rho: number | null; reason: string }>;
  } | null;
  composite: {
    results: Record<string, SmartEvidenceSeg | null>;
    /** 分年（键=视野 h，值=年份 → 段统计） */
    by_year?: Record<string, Record<string, SmartEvidenceSeg | null>> | null;
    n_dates?: number;
    avg_pool?: number;
    avg_covered?: number | null;
  } | null;
  evidence_only: Array<{ name: string; conclusion: string; use: string }>;
  admitted: string[];
  rejected: Array<{
    name: string;
    family: string | null;
    reason: string;
    rank_ic_10: number | null;
    q5_minus_q1_bp_10: number | null;
    n_days_10: number | null;
  }>;
}

async function _get<T>(path: string): Promise<T> {
  const token = localStorage.getItem('access_token') || '';
  const res = await fetch(`${MA_API}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const msg = typeof detail === 'string' ? detail : `聪明钱接口失败 ${res.status}`;
    throw new SmartMoneyError(msg, res.status);
  }
  if (body === null || typeof body !== 'object') {
    throw new SmartMoneyError('聪明钱接口返回结构异常（非对象）', res.status);
  }
  return body as T;
}

export function getSmartMoneyOverview(): Promise<SmartOverviewResponse> {
  return _get<SmartOverviewResponse>('/smart-money/overview');
}

/** 选股榜查询（v2：服务端筛选 + 分页；date= 走历史快照，as_of 返回真实快照日） */
export interface SmartStocksQuery {
  limit?: number;
  offset?: number;
  /** YYYY-MM-DD；命中最近 ≤date 的计分分区 */
  date?: string | null;
  /** 最新事件类型：lhb / inst / block_trade / survey */
  event?: string | null;
  industry?: string | null;
  minScore?: number | null;
}

export function getSmartMoneyStocks(q: SmartStocksQuery = {}): Promise<SmartStocksResponse> {
  const params = new URLSearchParams();
  const limit = Math.min(Math.max(Math.trunc(q.limit ?? 50), 1), 500);
  params.set('limit', String(limit));
  if (q.offset) params.set('offset', String(Math.max(Math.trunc(q.offset), 0)));
  if (q.date) params.set('date', q.date);
  if (q.event) params.set('event', q.event);
  if (q.industry) params.set('industry', q.industry);
  if (q.minScore !== null && q.minScore !== undefined) {
    params.set('min_score', String(q.minScore));
  }
  return _get<SmartStocksResponse>(`/smart-money/stocks?${params.toString()}`);
}

export function getSmartMoneyBranches(window: 5 | 20 = 5): Promise<SmartBranchesResponse> {
  return _get<SmartBranchesResponse>(`/smart-money/branches?window=${window}`);
}

export function getSmartMoneyEvidence(): Promise<SmartEvidenceResponse> {
  return _get<SmartEvidenceResponse>('/smart-money/evidence');
}

// ---------------------------------------------------------------- v2 端点族

/** 战绩追踪窗口汇总（近 20/60/120/全史） */
export interface SmartTrackSummary {
  n_days: number;
  mean_excess_bp: number | null;
  hit_rate: number | null;
  mean_ret_bp: number | null;
}

export interface SmartTrackWindow {
  window: string;
  h5: SmartTrackSummary | null;
  h10: SmartTrackSummary | null;
}

/** 逐日行：date + ret_{h}/bench_{h}/excess_{h}（h∈2,3,5,10；收益为小数） */
export type SmartTrackDaily = Record<string, number | string | null> & { date: string };

export interface SmartTrackBucket {
  labels: string[];
  midpoints?: number[];
  mean_excess_bp: (number | null)[];
  n_days: number[];
  /** 箱中值 vs 箱均值的 pearson（∈[−1,1]）；不单调如实披露 */
  monotonicity: number | null;
}

/** 最近案例（近 10 计分日 × Top5，含进行中） */
export interface SmartTrackRecent {
  date: string;
  /** 前缀式（与 items 同契约） */
  symbol: string;
  name: string | null;
  score: number | null;
  ret_2?: number | null;
  ret_5: number | null;
  ret_10: number | null;
  /** 「已实现h10」|「持有中」 */
  status: string;
  hold_days: number;
  /** 持有中 → 入场至今浮动收益（含未到期案例的实况） */
  ret_so_far: number | null;
}

export interface SmartRollingIc {
  h: number;
  window: number;
  series: Array<{ date: string; ic: number | null }>;
}

export interface SmartTrackRecordResponse {
  as_of: string | null;
  generated_at: string | null;
  top_n: number;
  horizons: number[];
  benchmark: string;
  daily: SmartTrackDaily[];
  windows: SmartTrackWindow[];
  /** 累计超额曲线（展示口径：重叠窗叠加，非无重叠资金曲线） */
  cum_excess: Record<string, Array<{ date: string; cum: number | null }>>;
  buckets: SmartTrackBucket;
  recent: SmartTrackRecent[];
  rolling_ic: SmartRollingIc | null;
}

export function getSmartMoneyTrackRecord(): Promise<SmartTrackRecordResponse> {
  return _get<SmartTrackRecordResponse>('/smart-money/track-record');
}

/** 事件族 CAR（族内当日池分位 q5 vs q1；bp，点位 k=i+2） */
export interface SmartEventFamily {
  n_events: number;
  n_scored: number;
  n_q5?: number;
  n_q1?: number;
  car_q5_bp: (number | null)[];
  car_q1_bp: (number | null)[];
  spread_bp: (number | null)[];
}

export interface SmartEventStudyResponse {
  as_of: string | null;
  generated_at: string | null;
  car_days: number;
  benchmark: string;
  families: Record<string, SmartEventFamily>;
}

export function getSmartMoneyEventStudy(): Promise<SmartEventStudyResponse> {
  return _get<SmartEventStudyResponse>('/smart-money/event-study');
}

/** 席位窗口统计（n=净买样本数；win_rate/avg_excess_bp 按已闭合 fwd h5 口径） */
export interface SmartSeatWinStat {
  n: number;
  win_rate: number | null;
  avg_excess_bp: number | null;
}

export interface SmartSeatClassStat {
  class: string;
  label: string;
  n_events: number;
  n_seats: number;
  win_rate: number | null;
  avg_excess_bp: number | null;
  total_net_yi: number | null;
}

export interface SmartSeatRow {
  seat_name: string;
  class: string;
  class_label: string;
  total_net_yi: number | null;
  last_seen: string | null;
  /** 键 = 窗口标签（'20'/'60'/'all'） */
  windows: Record<string, SmartSeatWinStat | null>;
}

export interface SmartSeatsResponse {
  as_of: string | null;
  horizon: number | null;
  min_trades: number | null;
  windows: string[] | null;
  window: string;
  sort: string;
  class: string | null;
  /** 选中窗口的池内基线胜率（对照披露） */
  baseline_win_rate: number | null;
  n_events: number | null;
  classes: SmartSeatClassStat[];
  total: number;
  items: SmartSeatRow[];
}

export interface SmartSeatsQuery {
  window?: '20' | '60' | 'all';
  sort?: 'net' | 'win_rate';
  seatClass?: string | null;
  limit?: number;
}

export function getSmartMoneySeats(q: SmartSeatsQuery = {}): Promise<SmartSeatsResponse> {
  const params = new URLSearchParams();
  params.set('window', q.window ?? 'all');
  params.set('sort', q.sort ?? 'win_rate');
  if (q.seatClass) params.set('class', q.seatClass);
  const limit = Math.min(Math.max(Math.trunc(q.limit ?? 50), 1), 300);
  params.set('limit', String(limit));
  return _get<SmartSeatsResponse>(`/smart-money/seats?${params.toString()}`);
}

/** 事件时间线行（四族事件；lhb 项带 fwd_ret_* 仅展示—已封存不入信号） */
export interface SmartTimelineEvent {
  type: string;
  label: string;
  date: string;
  reason?: string | null;
  interpretation?: string | null;
  close?: number | null;
  pct_chg?: number | null;
  net_buy?: number | null;
  net_ratio?: number | null;
  inst_buy?: number | null;
  inst_sell?: number | null;
  inst_net_buy?: number | null;
  inst_net_ratio?: number | null;
  inst_buy_seats?: number | null;
  inst_sell_seats?: number | null;
  deal_price?: number | null;
  premium?: number | null;
  amount?: number | null;
  amount_ratio?: number | null;
  n_trades?: number | null;
  inst_count?: number | null;
  reception_type?: string | null;
  reception_place?: string | null;
  fwd_ret_1d?: number | null;
  fwd_ret_2d?: number | null;
  fwd_ret_5d?: number | null;
  fwd_ret_10d?: number | null;
}

/** 最近 1 次龙虎榜逐席构成（side=buy 先、net_amount 降序；金额=元，占比=分数） */
export interface SmartStockSeatRow {
  seat_name: string | null;
  side: string;
  class: string;
  class_label: string;
  buy_amount: number | null;
  sell_amount: number | null;
  net_amount: number | null;
  buy_ratio: number | null;
  sell_ratio: number | null;
}

/** 大宗明细（premium/amount_ratio 为分数；buyer_is_inst=买方为机构专用） */
export interface SmartDzjyRow {
  date: string;
  deal_price: number | null;
  close: number | null;
  premium: number | null;
  volume: number | null;
  amount: number | null;
  amount_ratio: number | null;
  seat_buyer: string | null;
  seat_seller: string | null;
  buyer_is_inst: boolean;
  seller_is_inst: boolean;
}

export interface SmartNorthRow {
  quarter: string;
  report_date: string;
  pct_pp: number | null;
  chg_pp: number | null;
}

export interface SmartStockDetailResponse {
  symbol: string;
  name: string | null;
  industry: string | null;
  as_of: string | null;
  generated_at: string | null;
  partial: boolean | null;
  on_board: boolean;
  item: SmartStockItem | null;
  pillars: SmartPillarMeta[] | null;
  timeline: SmartTimelineEvent[];
  north: SmartNorthRow[];
  seats: { date: string; seats: SmartStockSeatRow[] } | null;
  dzjy: SmartDzjyRow[];
  study_ref: { families: string[]; endpoint: string; note: string } | null;
}

export function getSmartMoneyStock(symbol: string): Promise<SmartStockDetailResponse> {
  return _get<SmartStockDetailResponse>(`/smart-money/stock/${encodeURIComponent(symbol)}`);
}

export interface SmartDatesResponse {
  dates: string[];
  as_of: string | null;
  generated_at: string | null;
}

/** 可用评分快照日期（升序；含当前产物 as_of） */
export function getSmartMoneyDates(): Promise<SmartDatesResponse> {
  return _get<SmartDatesResponse>('/smart-money/dates');
}
