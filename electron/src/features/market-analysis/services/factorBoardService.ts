/** A 股市场分析 · 因子概率板取数层（/api/v1/market-analysis/factor-board）
 *
 * 契约：后端读产物 JSON 原样返回 + available_dates；404=无产物/该决策日无文件
 * （detail 带 available_dates）；409=产物损坏或 decision_date 错位。
 * 展示纪律：判定枚举英文机读（resonant_long 等），前端映射位置词（靠前/靠后），
 * 不写买卖 —— 见 features/shared/signalVocabulary.ts 的边界。
 */

import { SERVICE_ENDPOINTS } from '../../../config/services';

const MA_API = `${SERVICE_ENDPOINTS.USER_SERVICE}/market-analysis`;

/** 产物必需顶层键（与后端金样 factorBoardGolden.json `boardRequiredTop` 共读防漂移）。 */
export const REQUIRED_TOP_KEYS = [
  'schema_version',
  'meta',
  'factors',
  'horizon',
  'validation',
  'resonance',
  'sectors',
  'stocks',
] as const;

export interface FactorBoardFactor {
  name: string;
  lib: string;
  group: string;
  display: string;
  sign: number;
  ic60: number | null;
  win60: number | null;
  icir60: number | null;
  kept_ic: number | null;
  weight: number | null;
  horizon: Record<string, number | null>;
}

export interface FactorBoardSectorItem {
  name: string;
  n: number;
  fac: number | null;
  med: number | null;
  fbreadth: number | null;
  mbreadth: number | null;
  model_cov: number | null;
  verdict: string;
}

export interface FactorBoardSectorGroup {
  q67: number | null;
  q33: number | null;
  items: FactorBoardSectorItem[];
}

export interface FactorBoardStock {
  symbol: string;
  name: string | null;
  composite: number | null;
  model_rank: number | null;
  quadrant: string;
  sector_l2: string | null;
}

export interface FactorBoardMeta {
  decision_date: string;
  trade_date: string;
  generated_at: string;
  data_window?: string[];
  eval_days?: number;
  model: {
    trade_date: string;
    run_id: string;
    n: number;
    source: string;
    stale: boolean;
    by_side?: Record<string, number>;
  };
  coverage: {
    pool: number;
    evaluated: number;
    gated: number;
    gated_long: number;
    gated_short: number;
    selected: number;
    families: number;
    composite_n: number;
    model_cov_n: number;
    close_nonpos: number;
  };
  warnings: string[];
}

export interface FactorBoardValidation {
  stock?: { rank_ic: number | null; icir: number | null; t: number | null; win: number | null; days: number } | null;
  sector?: { rank_ic: number | null; icir: number | null; t: number | null; win: number | null; days: number } | null;
  in_sample?: boolean;
  note?: string;
}

export interface FactorBoardResponse {
  schema_version: string;
  meta: FactorBoardMeta;
  factors: FactorBoardFactor[];
  horizon: {
    global: Record<string, number | null>;
    by_group: Record<string, Record<string, number | null>>;
  };
  validation: FactorBoardValidation;
  resonance: { spearman: number | null; n: number; quadrants: Record<string, number> };
  sectors: {
    industry_l1: FactorBoardSectorGroup | null;
    industry_l2: FactorBoardSectorGroup | null;
    concept: FactorBoardSectorGroup | null;
  };
  stocks: {
    top: FactorBoardStock[];
    bottom: FactorBoardStock[];
    resonant_long: FactorBoardStock[];
    resonant_short: FactorBoardStock[];
  };
  available_dates: string[];
}

/** 携带状态码与可用日期的取数错误（404 时前端可提示「尚未生成」并展示历史日）。 */
export class FactorBoardError extends Error {
  readonly status: number;
  readonly availableDates: string[];

  constructor(message: string, status: number, availableDates: string[] = []) {
    super(message);
    this.name = 'FactorBoardError';
    this.status = status;
    this.availableDates = availableDates;
  }
}

export async function getFactorBoard(date?: string): Promise<FactorBoardResponse> {
  const token = localStorage.getItem('access_token') || '';
  const qs = date ? `?date=${encodeURIComponent(date)}` : '';
  const res = await fetch(`${MA_API}/factor-board${qs}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : (detail as { message?: string } | null)?.message || `因子概率板接口失败 ${res.status}`;
    const dates = (detail as { available_dates?: string[] } | null)?.available_dates || [];
    throw new FactorBoardError(msg, res.status, dates);
  }
  if (body === null || typeof body !== 'object') {
    throw new FactorBoardError('因子概率板产物结构异常（非对象）', res.status);
  }
  const missing = REQUIRED_TOP_KEYS.filter((k) => !(k in (body as Record<string, unknown>)));
  if (missing.length > 0) {
    throw new FactorBoardError(`因子概率板产物结构不完整：缺 ${missing.join('、')}`, res.status);
  }
  return body as FactorBoardResponse;
}
