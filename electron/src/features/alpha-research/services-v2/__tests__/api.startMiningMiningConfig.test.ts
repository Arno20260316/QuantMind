/**
 * evolve 提交携带自定义挖掘配置（移植 P1/P1.5）——query 与 body
 * 两条路径都要带齐，且**未自定义时逐键零回归**（老路径的请求形状一字不变）。
 *
 * 后端契约：`mining_start/mining_end`、`valid_start/valid_end` 两对日期，
 * `datasets` 可重复，`freq`（'1min' 才算自定义；日线默认不发键）；
 * 半截日期对由 miningConfigWire 抛错（提交路径上屏）。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { post: apiPostMock, get: vi.fn(), delete: vi.fn() },
}));

import { dispatchMiningBatch, startMining } from '../api';

beforeEach(() => {
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({
    data: { data: { task_id: 't-99', status: 'pending' } },
  });
});

function queryPairs(url: string): Array<[string, string]> {
  const qs = url.split('?')[1] ?? '';
  return [...new URLSearchParams(qs).entries()];
}

const SELECTION = {
  miningStart: '2021-01-04',
  miningEnd: '2023-12-31',
  validStart: '2024-01-01',
  validEnd: '2025-12-31',
  datasets: ['l1_factors'],
  freq: null,
};

describe('startMining · 自定义挖掘配置', () => {
  test('query 形态：追加在既有键之后，数据集重复键', async () => {
    await startMining({
      direction: '动量因子',
      market: 'a_share',
      universe: 'csi300',
      maxRounds: 3,
      miningConfig: { ...SELECTION, datasets: ['l1_factors', 'features_daily'] },
    });

    const [url, body] = apiPostMock.mock.calls[0];
    expect(body).toBeUndefined();
    expect(queryPairs(String(url))).toEqual([
      ['loop_n', '3'],
      ['direction', '动量因子'],
      ['market', 'a_share'],
      ['universe', 'csi300'],
      ['mining_start', '2021-01-04'],
      ['mining_end', '2023-12-31'],
      ['valid_start', '2024-01-01'],
      ['valid_end', '2025-12-31'],
      ['datasets', 'l1_factors'],
      ['datasets', 'features_daily'],
    ]);
  });

  test('body 形态（docId）：snake_case 字段随 body 下发', async () => {
    await startMining({
      direction: '文档方向',
      docId: 'doc-1',
      miningConfig: SELECTION,
    });

    const [, body] = apiPostMock.mock.calls[0];
    expect(body).toMatchObject({
      doc_id: 'doc-1',
      mining_start: '2021-01-04',
      mining_end: '2023-12-31',
      valid_start: '2024-01-01',
      valid_end: '2025-12-31',
      datasets: ['l1_factors'],
    });
  });

  test('未自定义（缺席/全空）：query 与 body 都不出现任何新键', async () => {
    await startMining({
      direction: 'd',
      maxRounds: 2,
      miningConfig: {
        miningStart: null,
        miningEnd: null,
        validStart: null,
        validEnd: null,
        datasets: null,
      },
    });
    expect(queryPairs(String(apiPostMock.mock.calls[0][0]))).toEqual([
      ['loop_n', '2'],
      ['direction', 'd'],
    ]);

    await startMining({ direction: 'd', docId: 'doc-2' });
    const body = apiPostMock.mock.calls[1][1];
    for (const key of ['mining_start', 'mining_end', 'valid_start', 'valid_end', 'datasets', 'freq']) {
      expect(body).not.toHaveProperty(key);
    }
  });

  test('半截日期对：提交即抛（不静默丢参数）', async () => {
    await expect(
      startMining({
        direction: 'd',
        miningConfig: { ...SELECTION, miningEnd: null },
      }),
    ).rejects.toThrow(/挖掘区间须成对/);
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  test('分钟档：query 与 body 都带 freq=1min（窗口按分钟可用块下发）', async () => {
    const minuteConfig = {
      miningStart: '2026-03-04',
      miningEnd: '2026-08-25',
      validStart: null,
      validEnd: null,
      // 分钟任务不带数据集（面板也收起该档位）——后端会拒
      datasets: null,
      freq: '1min',
    };
    await startMining({ direction: '分钟动量', maxRounds: 2, miningConfig: minuteConfig });
    expect(queryPairs(String(apiPostMock.mock.calls[0][0]))).toEqual([
      ['loop_n', '2'],
      ['direction', '分钟动量'],
      ['freq', '1min'],
      ['mining_start', '2026-03-04'],
      ['mining_end', '2026-08-25'],
    ]);

    await startMining({ direction: '分钟动量', docId: 'doc-3', miningConfig: minuteConfig });
    expect(apiPostMock.mock.calls[1][1]).toMatchObject({
      doc_id: 'doc-3',
      freq: '1min',
      mining_start: '2026-03-04',
      mining_end: '2026-08-25',
    });
    expect(apiPostMock.mock.calls[1][1]).not.toHaveProperty('datasets');

    // 日线档（freq 缺席/null）恒不发键——老路径零回归
    await startMining({ direction: 'd', miningConfig: { ...SELECTION, freq: null } });
    expect(apiPostMock.mock.calls[2][1]).toBeUndefined();
    expect(queryPairs(String(apiPostMock.mock.calls[2][0]))).not.toContainEqual([
      'freq',
      expect.any(String),
    ]);
  });
});

describe('dispatchMiningBatch · 自定义挖掘配置随卡片派发', () => {
  test('body 带四端点 + 数据集；未给不产生键', async () => {
    apiPostMock.mockResolvedValue({ data: { data: { items: [] } } });
    await dispatchMiningBatch({
      directions: ['a', 'b'],
      market: 'a_share',
      universe: 'csi300',
      miningConfig: SELECTION,
    });
    expect(apiPostMock.mock.calls[0][1]).toMatchObject({
      mining_start: '2021-01-04',
      mining_end: '2023-12-31',
      valid_start: '2024-01-01',
      valid_end: '2025-12-31',
      datasets: ['l1_factors'],
    });

    await dispatchMiningBatch({ directions: ['a'] });
    const body = apiPostMock.mock.calls[1][1];
    for (const key of ['mining_start', 'mining_end', 'valid_start', 'valid_end', 'datasets', 'freq']) {
      expect(body).not.toHaveProperty(key);
    }

    // 分钟卡片：批量派发同样带 freq（与单条 evolve 共用 wire 映射）
    await dispatchMiningBatch({
      directions: ['a'],
      miningConfig: {
        miningStart: null,
        miningEnd: null,
        validStart: null,
        validEnd: null,
        datasets: null,
        freq: '1min',
      },
    });
    expect(apiPostMock.mock.calls[2][1]).toMatchObject({ freq: '1min' });
  });
});
