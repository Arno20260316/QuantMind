/** 期货市场分析 · API 服务层（/api/v1/market-analysis-futures） */

import { SERVICE_ENDPOINTS } from '../../../config/services';
import type {
  FuturesBasis,
  FuturesCftc,
  FuturesCrossMarket,
  FuturesFeedStatus,
  FuturesHeatmap,
  FuturesInventoryEm,
  FuturesMembers,
  FuturesOiChanges,
  FuturesOutlook,
  FuturesOverview,
  FuturesPreciousBasis,
  FuturesRanking,
  FuturesReceipts,
  FuturesSectorStats,
  FuturesTermRanking,
  FuturesTermStructure,
  FuturesVarietyProfile,
  RankingKind,
} from '../types';

const FUT_API = `${SERVICE_ENDPOINTS.USER_SERVICE}/market-analysis-futures`;

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token') || '';
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${FUT_API}${path}`, { headers: authHeaders() });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`期货市场分析接口失败 ${res.status}: ${detail.slice(0, 120)}`);
  }
  return res.json() as Promise<T>;
}

export function getStatus(): Promise<FuturesFeedStatus> {
  return getJson<FuturesFeedStatus>('/status');
}

export function getOverview(): Promise<FuturesOverview> {
  return getJson<FuturesOverview>('/overview');
}

export function getRanking(kind: RankingKind, limit = 20): Promise<FuturesRanking> {
  return getJson<FuturesRanking>(`/ranking?kind=${kind}&limit=${limit}`);
}

export function getHeatmap(limit?: number): Promise<FuturesHeatmap> {
  const q = limit ? `?limit=${limit}` : '';
  return getJson<FuturesHeatmap>(`/heatmap${q}`);
}

export function getSectorStats(): Promise<FuturesSectorStats> {
  return getJson<FuturesSectorStats>('/sector-stats');
}

export function getTerm(symbol: string): Promise<FuturesTermStructure> {
  return getJson<FuturesTermStructure>(`/term?symbol=${encodeURIComponent(symbol)}`);
}

export function getTermRanking(limit = 100): Promise<FuturesTermRanking> {
  return getJson<FuturesTermRanking>(`/term-ranking?limit=${limit}`);
}

export function getCftc(limit = 15): Promise<FuturesCftc> {
  return getJson<FuturesCftc>(`/cftc?limit=${limit}`);
}

export function getOiChanges(days = 1, limit = 30): Promise<FuturesOiChanges> {
  return getJson<FuturesOiChanges>(`/oi-changes?days=${days}&limit=${limit}`);
}

export function getReceipts(variety?: string, days = 90): Promise<FuturesReceipts> {
  const q = new URLSearchParams();
  if (variety) q.set('variety', variety);
  q.set('days', String(days));
  return getJson<FuturesReceipts>(`/receipts?${q.toString()}`);
}

export function getCrossMarket(days = 60): Promise<FuturesCrossMarket> {
  return getJson<FuturesCrossMarket>(`/cross-market?days=${days}`);
}

export function getPreciousBasis(days = 60): Promise<FuturesPreciousBasis> {
  return getJson<FuturesPreciousBasis>(`/precious-basis?days=${days}`);
}

// ---- P1 新增九域端点 ----

export function getOutlook(sector?: string, limit = 120): Promise<FuturesOutlook> {
  const q = new URLSearchParams();
  if (sector) q.set('sector', sector);
  q.set('limit', String(limit));
  return getJson<FuturesOutlook>(`/outlook?${q.toString()}`);
}

export function getVariety(symbol: string): Promise<FuturesVarietyProfile> {
  return getJson<FuturesVarietyProfile>(`/variety?symbol=${encodeURIComponent(symbol)}`);
}

export function getBasis(symbol?: string, limit = 120, days = 500): Promise<FuturesBasis> {
  const q = new URLSearchParams();
  if (symbol) q.set('symbol', symbol);
  q.set('limit', String(limit));
  q.set('days', String(days));
  return getJson<FuturesBasis>(`/basis?${q.toString()}`);
}

export function getMembers(symbol?: string, limit = 120): Promise<FuturesMembers> {
  const q = new URLSearchParams();
  if (symbol) q.set('symbol', symbol);
  q.set('limit', String(limit));
  return getJson<FuturesMembers>(`/members?${q.toString()}`);
}

export function getInventoryEm(symbol?: string, limit = 120): Promise<FuturesInventoryEm> {
  const q = new URLSearchParams();
  if (symbol) q.set('symbol', symbol);
  q.set('limit', String(limit));
  return getJson<FuturesInventoryEm>(`/inventory?${q.toString()}`);
}

export function refreshMarket(): Promise<{ status: string; trade_date: string; message: string }> {
  return fetch(`${FUT_API}/refresh`, { method: 'POST', headers: authHeaders() }).then((res) => {
    if (!res.ok) throw new Error(`刷新失败 ${res.status}`);
    return res.json();
  });
}
