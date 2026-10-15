/**
 * 推理中心（期货）。
 *
 * 页面实现在 features/inference-center-shared（四市场共用）；本文件只声明期货的市场
 * 常量、代码联想源与 K 线取数。
 *
 * - 联想源：期货市场分析热力图 /market-analysis-futures/heatmap（全品种目录，
 *   服务端 300s 缓存）——与「期货市场分析」栏目同源同口径，不另建品种表。
 * - K 线：/market/kline?market=FUTURES（QuantFutures 本地 parquet，主力连续日K）。
 * - 符号三形态（CL.FUT 国际 / RB0.CN 国内主力 / Au99.99 上金所）大小写与括号
 *   都敏感，后端 DuckDB 精确匹配：归一化宁可不改、不可改错，规则见
 *   normalizeFuturesSymbol 头注释。
 */

import { InferenceCenterProvider, type InferenceCenterAdapter, type SuggestionItem } from '../features/inference-center-shared/adapter';
import { InferenceCenterShell } from '../features/inference-center-shared/pages/InferenceCenterShell';
import { getHeatmap } from '../features/market-analysis-futures/services/api';
import { inferenceCenterService } from '../services/inferenceCenterService';

/** 品种目录快照：normalize 是同步函数，「目录吸附」需要同步可读的缓存 */
let catalogSnapshot: SuggestionItem[] = [];

/** 品种目录单飞加载：失败不缓存（下次搜索可重试，避免一次网络抖动锁死联想整个会话） */
let catalogPromise: Promise<SuggestionItem[]> | null = null;

function loadFuturesCatalog(): Promise<SuggestionItem[]> {
  if (!catalogPromise) {
    catalogPromise = getHeatmap()
      .then((data) => {
        const items = (data.items || []).map((it) => ({
          symbol: it.symbol,
          name: it.name,
          market: it.sector_name || it.sector,
        }));
        catalogSnapshot = items;
        return items;
      })
      .catch((e) => {
        console.warn('期货品种目录加载失败:', e);
        catalogPromise = null;
        return [] as SuggestionItem[];
      });
  }
  return catalogPromise;
}

/**
 * 期货代码归一（顺序执行，宁可不改、不可改错）：
 * ① 空串原样返回；
 * ② 目录吸附：与品种目录**大小写不敏感全等**（代码或中文名）→ 目录规范形
 *    （"rb0.cn" → RB0.CN，"螺纹钢" → RB0.CN）；
 * ③ 含点 → 原样（RB0.CN / CL.FUT / Au99.99 已是规范形；大小写敏感绝不改写）；
 * ④ 裸主力形（RB0/AU0/IF0/PS0）→ 大写后追 .CN；
 * ⑤ 裸国际形（CL/GC/SI/HG/XAU/S）→ 大写后追 .FUT；
 * ⑥ 其余原样（Au(T+D) 等含括号的原始合约名绝不改写大小写；查空即诚实 404，不猜）。
 *
 * 关键风险（改本函数前必读）：quantfutures_hub.fetch_daily_kline 是 DuckDB
 * 精确匹配 `symbol = '...'`，大写化/加错后缀是**查空**（K 线区诚实 404，
 * 不会静默给错数据），所以 ④⑤ 只对「无点无括号」的裸形态做补齐，其余一律不碰。
 */
export function normalizeFuturesSymbol(raw: string): string {
  const s = raw.trim();
  if (!s) return s;
  // ② 目录吸附（全等：中文名输入直接落到品种代码；不猜前缀、不做包含匹配）
  const lower = s.toLowerCase();
  const hit = catalogSnapshot.find(
    (it) => it.symbol.toLowerCase() === lower || (it.name || '').toLowerCase() === lower,
  );
  if (hit) return hit.symbol;
  // ③ 含点：原样（Au99.99 这类大小写敏感形态）
  if (s.includes('.')) return s;
  // ④ 裸主力形 → .CN
  const domestic = /^([A-Za-z]{1,3}\d{1,2})$/.exec(s);
  if (domestic) return `${domestic[1].toUpperCase()}.CN`;
  // ⑤ 裸国际形 → .FUT
  const international = /^([A-Za-z]{1,6})$/.exec(s);
  if (international) return `${international[1].toUpperCase()}.FUT`;
  // ⑥ 其余原样（含括号/连字符等特殊字符的原始合约名绝不改写）
  return s;
}

export const FUTURES_ADAPTER: InferenceCenterAdapter = {
  market: 'FUTURES',
  marketLabel: '期货市场',
  calendar: 'CME',
  currencySymbol: '¥',
  defaultSymbol: 'RB0.CN',
  searchPlaceholder: '输入代码/名称搜索 (如 RB0.CN 或 螺纹钢)',

  preload: () => loadFuturesCatalog().then(() => undefined),
  search: async (kw) => {
    const q = kw.trim().toLowerCase();
    if (!q) return [];
    const catalog = await loadFuturesCatalog();
    return catalog
      .filter(
        (s) =>
          s.symbol.toLowerCase().includes(q) ||
          (s.name || '').toLowerCase().includes(q),
      )
      .slice(0, 8);
  },
  suggestionLabel: (s) => s.symbol,
  toSymbol: (s) => normalizeFuturesSymbol(s.symbol),
  normalize: normalizeFuturesSymbol,

  // QuantFutures 本地 parquet（主力连续，前复权口径不适用——数据面 factor 恒 1.0）
  fetchKline: (symbol, days, endDate, startDate) =>
    inferenceCenterService.getMarketKline('FUTURES', symbol, days, endDate, startDate),
  // 期货 symbol 本身即数据面形态（CL.FUT / RB0.CN / Au99.99 原样）
  toSuffixSymbol: (symbol) => normalizeFuturesSymbol(symbol),
};

export const InferenceCenterFuturesPage = () => (
  <InferenceCenterProvider adapter={FUTURES_ADAPTER}>
    <InferenceCenterShell />
  </InferenceCenterProvider>
);

export default InferenceCenterFuturesPage;
