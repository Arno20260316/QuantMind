/**
 * 期货适配器单测：代码归一 6 条规则 + 契约字段。
 *
 * 归一规则为什么必须逐条锁死：期货 symbol 三形态（CL.FUT 国际 / RB0.CN 国内主力 /
 * Au99.99 上金所）在 QuantFutures parquet 里是 DuckDB **精确匹配**（大小写敏感），
 * 归一化改写过头 → 查空；目录吸附失败 → 中文名输入变 404。两种错都表现为
 * 「K 线空白」，没有人会注意到。
 */
import { describe, expect, test, vi } from 'vitest';

const HEATMAP_ITEMS = [
  { symbol: 'RB0.CN', name: '螺纹钢', sector: '黑色系', sector_name: '黑色系' },
  { symbol: 'Au99.99', name: '黄金T+D', sector: '上金所' },
  { symbol: 'CL.FUT', name: 'WTI原油', sector: '国际能源' },
  { symbol: 'AU0.CN', name: '沪金主力', sector: '贵金属' },
];

/** 每个测试组拿全新的模块实例（目录快照是模块级状态，跨用例会串） */
async function loadAdapter({ catalogFails = false, items = HEATMAP_ITEMS } = {}) {
  vi.resetModules();
  vi.doMock('../pages/InferenceCenterShell', () => ({ InferenceCenterShell: () => null }));
  vi.doMock('../../market-analysis-futures/services/api', () => ({
    getHeatmap: catalogFails
      ? vi.fn().mockRejectedValue(new Error('offline'))
      : vi.fn().mockResolvedValue({ items }),
  }));
  return await import('../../../pages/InferenceCenterFuturesPage');
}

describe('normalizeFuturesSymbol —— 目录未加载（冷启动 / 热力图失败）', () => {
  test('裸国内主力形（RB0/AU0/IF0/PS0）→ 追 .CN，大小写都收', async () => {
    const { normalizeFuturesSymbol } = await loadAdapter();
    expect(normalizeFuturesSymbol('RB0')).toBe('RB0.CN');
    expect(normalizeFuturesSymbol('rb0')).toBe('RB0.CN');
    expect(normalizeFuturesSymbol('IF0')).toBe('IF0.CN');
    expect(normalizeFuturesSymbol('PS0')).toBe('PS0.CN');
  });

  test('裸国际形（CL/GC/SI/HG/XAU）→ 追 .FUT', async () => {
    const { normalizeFuturesSymbol } = await loadAdapter();
    expect(normalizeFuturesSymbol('CL')).toBe('CL.FUT');
    expect(normalizeFuturesSymbol('gc')).toBe('GC.FUT');
    expect(normalizeFuturesSymbol('XAU')).toBe('XAU.FUT');
  });

  test('含点形态原样 —— 绝不改写大小写（Au99.99 大写化后查空）', async () => {
    const { normalizeFuturesSymbol } = await loadAdapter();
    expect(normalizeFuturesSymbol('Au99.99')).toBe('Au99.99');
    expect(normalizeFuturesSymbol('au99.99')).toBe('au99.99');
    expect(normalizeFuturesSymbol('RB0.CN')).toBe('RB0.CN');
    expect(normalizeFuturesSymbol('cl.fut')).toBe('cl.fut');
  });

  test('含括号/连字符等特殊字符原样（Au(T+D) 既不加后缀也不改大小写）', async () => {
    const { normalizeFuturesSymbol } = await loadAdapter();
    expect(normalizeFuturesSymbol('Au(T+D)')).toBe('Au(T+D)');
    expect(normalizeFuturesSymbol('ag(T+D)')).toBe('ag(T+D)');
    expect(normalizeFuturesSymbol('螺纹钢')).toBe('螺纹钢');
  });

  test('首尾空白 trim，空串原样返回', async () => {
    const { normalizeFuturesSymbol } = await loadAdapter();
    expect(normalizeFuturesSymbol('  RB0  ')).toBe('RB0.CN');
    expect(normalizeFuturesSymbol('')).toBe('');
    expect(normalizeFuturesSymbol('   ')).toBe('');
  });
});

describe('normalizeFuturesSymbol —— 目录吸附（preload 之后）', () => {
  test('大小写不敏感全等吸附为目录规范形', async () => {
    const mod = await loadAdapter();
    await mod.FUTURES_ADAPTER.preload();
    expect(mod.normalizeFuturesSymbol('rb0.cn')).toBe('RB0.CN');
    expect(mod.normalizeFuturesSymbol('au99.99')).toBe('Au99.99');
    expect(mod.normalizeFuturesSymbol('cl.fut')).toBe('CL.FUT');
  });

  test('中文名全等吸附为品种代码', async () => {
    const mod = await loadAdapter();
    await mod.FUTURES_ADAPTER.preload();
    expect(mod.normalizeFuturesSymbol('螺纹钢')).toBe('RB0.CN');
    expect(mod.normalizeFuturesSymbol('WTI原油')).toBe('CL.FUT');
  });

  test('目录加载失败时静默降级：normalize 仍走形态规则，不抛错', async () => {
    const mod = await loadAdapter({ catalogFails: true });
    await mod.FUTURES_ADAPTER.preload();
    expect(mod.normalizeFuturesSymbol('RB0')).toBe('RB0.CN');
    expect(mod.normalizeFuturesSymbol('螺纹钢')).toBe('螺纹钢');
  });
});

describe('FUTURES_ADAPTER 契约', () => {
  test('市场/日历/货币/默认标的与细案一致', async () => {
    const { FUTURES_ADAPTER } = await loadAdapter();
    expect(FUTURES_ADAPTER.market).toBe('FUTURES');
    expect(FUTURES_ADAPTER.marketLabel).toBe('期货市场');
    expect(FUTURES_ADAPTER.calendar).toBe('CME');
    expect(FUTURES_ADAPTER.currencySymbol).toBe('¥');
    expect(FUTURES_ADAPTER.defaultSymbol).toBe('RB0.CN');
  });

  test('search：代码与中文名都命中，大小写不敏感，最多 8 条', async () => {
    const items = [
      ...HEATMAP_ITEMS,
      ...Array.from({ length: 10 }, (_, i) => ({ symbol: `RB${i + 1}.CN`, name: `螺纹钢${i + 1}` })),
    ];
    const { FUTURES_ADAPTER } = await loadAdapter({ items });
    expect((await FUTURES_ADAPTER.search('rb0')).map((s) => s.symbol)).toEqual(['RB0.CN']);
    expect((await FUTURES_ADAPTER.search('螺纹钢')).length).toBe(8);
  });

  test('空关键词返回空列表（不整表倒出）', async () => {
    const { FUTURES_ADAPTER } = await loadAdapter();
    expect(await FUTURES_ADAPTER.search('   ')).toEqual([]);
  });

  test('suggestionLabel 显示规范代码；toSymbol 落到目录规范形', async () => {
    const mod = await loadAdapter();
    await mod.FUTURES_ADAPTER.preload();
    expect(mod.FUTURES_ADAPTER.suggestionLabel({ symbol: 'RB0.CN', name: '螺纹钢' })).toBe('RB0.CN');
    expect(mod.FUTURES_ADAPTER.toSymbol({ symbol: 'RB0.CN', name: '螺纹钢' })).toBe('RB0.CN');
  });

  test('fetchKline 走 /market/kline?market=FUTURES，symbol 原样透传（大小写不丢）', async () => {
    vi.resetModules();
    const getMarketKline = vi.fn().mockResolvedValue([]);
    vi.doMock('../pages/InferenceCenterShell', () => ({ InferenceCenterShell: () => null }));
    vi.doMock('../../market-analysis-futures/services/api', () => ({
      getHeatmap: vi.fn().mockResolvedValue({ items: HEATMAP_ITEMS }),
    }));
    vi.doMock('../../../services/inferenceCenterService', () => ({
      inferenceCenterService: { getMarketKline },
    }));
    const { FUTURES_ADAPTER } = await import('../../../pages/InferenceCenterFuturesPage');

    await FUTURES_ADAPTER.fetchKline('Au99.99', 120, '2026-10-14', '2026-07-06');
    expect(getMarketKline).toHaveBeenCalledWith('FUTURES', 'Au99.99', 120, '2026-10-14', '2026-07-06');
  });

  test('toSuffixSymbol 原样透传三形态（多模型曲线按数据面形态取数）', async () => {
    const { FUTURES_ADAPTER } = await loadAdapter();
    expect(FUTURES_ADAPTER.toSuffixSymbol('RB0.CN')).toBe('RB0.CN');
    expect(FUTURES_ADAPTER.toSuffixSymbol('Au99.99')).toBe('Au99.99');
  });
});
