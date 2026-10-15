/**
 * 因子报告 API 服务层（/api/v1/factor-report，引擎服务经网关转发）。
 *
 * 市场分层（2026-10）：所有调用带 `market`（缺省 'CN' 与后端默认一致）。
 * 数据集清单因市场而异（GET /datasets?market=），未接入市场后端 422 ——
 * 前端不做第二套校验，按页面传入的 market 原样透传。
 */

import { SERVICE_ENDPOINTS } from '../../../config/services';
import type { ResearchMarket } from '../types/factorResearch';
import type {
  FactorBuildStatus,
  FactorClusterResponse,
  FactorPortfolioResponse,
  FactorCorrelation,
  FactorDatasetList,
  FactorDetail,
  FactorDetailParams,
  FactorRelated,
  FactorSummaryResponse,
} from '../types/factorReport';

const BASE = `${SERVICE_ENDPOINTS.USER_SERVICE}/factor-report`;

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('access_token') || '';
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJson<T>(path: string, timeoutMs = 60000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, { headers: authHeaders(), signal: controller.signal });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      throw new Error(`因子报告接口失败 ${res.status}: ${detail.slice(0, 120)}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

/** 可选数据集及各自快照状态（含盘上滞后探测：stale / stale_reason；清单因市场而异） */
export function getFactorDatasets(market: ResearchMarket = 'CN'): Promise<FactorDatasetList> {
  return getJson<FactorDatasetList>(`/datasets?market=${market}`);
}

async function postJson<T>(path: string, timeoutMs = 60000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: authHeaders(),
      signal: controller.signal,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      throw new Error(`因子报告接口失败 ${res.status}: ${detail.slice(0, 120)}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

/** 快照状态：已生成 / 构建中 / 日志最新一行（「重建快照」入口轮询用） */
export function getFactorReportBuildStatus(dataset: string, market: ResearchMarket = 'CN'): Promise<FactorBuildStatus> {
  const qs = new URLSearchParams({ dataset, market });
  return getJson<FactorBuildStatus>(`/build-status?${qs}`);
}

/** 一键重建快照（后台子进程，全部本地计算；已在构建则返回 running） */
export function startFactorReportBuild(
  dataset: string,
  market: ResearchMarket = 'CN',
): Promise<{ started: boolean; running: boolean; pid?: number; dataset?: string }> {
  const qs = new URLSearchParams({ dataset, market });
  return postJson(`/build?${qs}`, 30000);
}

/** 快照摘要：因子排行（IC / ICIR / 分位价差 / 换手） */
export function getFactorSummary(params: {
  dataset?: string;
  library?: string;
  sort?: 'abs_ic' | 'icir' | 'turnover' | 'ls_mean' | 'name';
  limit?: number;
  market?: ResearchMarket;
} = {}): Promise<FactorSummaryResponse> {
  const qs = new URLSearchParams();
  if (params.dataset) qs.set('dataset', params.dataset);
  if (params.library) qs.set('library', params.library);
  if (params.sort) qs.set('sort', params.sort);
  if (params.limit) qs.set('limit', String(params.limit));
  qs.set('market', params.market ?? 'CN');
  const suffix = qs.toString() ? `?${qs}` : '';
  return getJson<FactorSummaryResponse>(`/summary${suffix}`);
}

/**
 * 单因子明细（预计算序列 + 读时派生块，毫秒级）。
 *
 * ⚠️ `longGroup` / `shortGroup` / `costBps` / `bench` 改的是**多空曲线本身**，
 * 后端已把它们纳入缓存键；前端切参数后必须重新请求，不能复用旧响应。
 */
export function getFactorDetail(
  factor: string,
  dataset: string,
  params: FactorDetailParams = {},
  market: ResearchMarket = 'CN',
): Promise<FactorDetail> {
  const qs = new URLSearchParams({
    factor,
    dataset,
    market,
    horizon: params.horizon ?? 'fwd_ret_5',
    lookback: String(params.lookback ?? 250),
    long_group: String(params.longGroup ?? 3),
    short_group: String(params.shortGroup ?? 9),
    cost_bps: String(params.costBps ?? 20),
  });
  if (params.bench) qs.set('bench', params.bench);
  return getJson<FactorDetail>(`/detail?${qs}`, 90000);
}

/** 因子相关性子矩阵 */
export function getFactorCorrelation(factors: string[], dataset: string, market: ResearchMarket = 'CN'): Promise<FactorCorrelation> {
  const qs = new URLSearchParams({ factors: factors.join(','), dataset, market });
  return getJson<FactorCorrelation>(`/correlation?${qs}`);
}

/** 与某因子最相关（含负相关）的因子 */
export function getFactorRelated(factor: string, dataset: string, top = 8, market: ResearchMarket = 'CN'): Promise<FactorRelated> {
  const qs = new URLSearchParams({ factor, dataset, top: String(top), market });
  return getJson<FactorRelated>(`/related?${qs}`);
}

/** 因子去重清单：|ρ| ≥ 阈值 的同源因子簇，每簇留一个代表 */
export function getFactorClusters(
  dataset: string,
  threshold = 0.9,
  keep: 'icir' | 'abs_ic' | 'ls' = 'icir',
  market: ResearchMarket = 'CN',
): Promise<FactorClusterResponse> {
  const qs = new URLSearchParams({ dataset, threshold: String(threshold), keep, market });
  return getJson<FactorClusterResponse>(`/clusters?${qs}`);
}

/** 推荐因子组合（含权重、方向、淘汰理由） */
export function getFactorPortfolio(dataset: string, recompute = false, market: ResearchMarket = 'CN'): Promise<FactorPortfolioResponse> {
  const qs = new URLSearchParams({ dataset, market });
  if (recompute) qs.set('recompute', 'true');
  return getJson<FactorPortfolioResponse>(`/portfolio?${qs}`);
}
