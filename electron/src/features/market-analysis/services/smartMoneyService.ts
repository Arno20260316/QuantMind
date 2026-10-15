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
}

export interface SmartStocksResponse {
  as_of: string | null;
  generated_at: string | null;
  partial: boolean | null;
  admitted: string[] | null;
  pillars: SmartPillarMeta[] | null;
  summary: SmartSummary | null;
  total: number;
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

export interface SmartEvidenceResponse {
  validated_at: string | null;
  window: { eval_start: string; eval_end: string; warmup_start: string } | null;
  method: Record<string, unknown> | null;
  signals: SmartEvidenceSignal[];
  correlation: {
    pairs: Array<{ a: string; b: string; rho: number | null }>;
    collapsed: Array<{ signal: string; kept: string; rho: number | null; reason: string }>;
  } | null;
  composite: {
    results: Record<string, SmartEvidenceSeg | null>;
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

export function getSmartMoneyStocks(limit = 200): Promise<SmartStocksResponse> {
  const n = Math.min(Math.max(Math.trunc(limit), 1), 500);
  return _get<SmartStocksResponse>(`/smart-money/stocks?limit=${n}`);
}

export function getSmartMoneyBranches(window: 5 | 20 = 5): Promise<SmartBranchesResponse> {
  return _get<SmartBranchesResponse>(`/smart-money/branches?window=${window}`);
}

export function getSmartMoneyEvidence(): Promise<SmartEvidenceResponse> {
  return _get<SmartEvidenceResponse>('/smart-money/evidence');
}
