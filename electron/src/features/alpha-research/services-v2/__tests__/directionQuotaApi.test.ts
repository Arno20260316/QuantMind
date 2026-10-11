/**
 * 方向配额预览 API 桥（T-FE-10）契约：
 * - snake→camel 归一（打分表 / 三档名额 / 档位预演）；
 * - **缺失一律 null**（绝不上屏 0——「没有统计」与「统计为 0」是两回事）；
 * - 请求形态：directions 重复键、剥空白弃空串、n 整数化；
 * - 失败 → success:false + 原文（不编空壳让界面假成功）。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: apiGetMock },
}));

import {
  DIRECTION_QUOTA_DEFAULT_MARKET,
  DIRECTION_QUOTA_DEFAULT_N,
  QUOTA_TIERS,
  getDirectionQuotaPreview,
} from '../directionQuotaApi';

beforeEach(() => {
  apiGetMock.mockReset();
});

const RAW = {
  available: true,
  market: 'a_share',
  n: 3,
  quota: { explore: 0.3, exploit: 0.5, mutate: 0.2 },
  slots: { explore: 1, exploit: 2, mutate: 0 },
  saturated_threshold: 2,
  recent_success_tasks: 5,
  directions: [
    {
      direction: '尾盘资金流强度',
      attempts: 9,
      n_tasks: 4,
      n_success: 1,
      p: 0.75,
      dup_hits_90d: 0,
      sat_penalty: 0,
      score: 0.75,
      saturated: false,
    },
    {
      direction: '量价背离确认',
      attempts: 3,
      n_tasks: 1,
      n_success: 0,
      p: 0.125,
      dup_hits_90d: 2,
      sat_penalty: 0.5,
      score: 0.0625,
      saturated: true,
    },
  ],
  tiers: {
    explore: { pool: ['尾盘资金流强度'], weighting: 'blankness', fallback: null },
    exploit: { pool: ['尾盘资金流强度'], weighting: 'proxy_score', fallback: null },
    mutate: { pool: [], weighting: 'uniform', fallback: 'mutate_empty' },
  },
  recent_success_dirs: ['尾盘资金流强度'],
  error: null,
};

const okRes = (data: unknown) => ({ data: { code: 200, data } });

describe('常量与后端同口径', () => {
  test('默认市场 a_share、默认名额 3、三档顺序 explore/exploit/mutate', () => {
    expect(DIRECTION_QUOTA_DEFAULT_MARKET).toBe('a_share');
    expect(DIRECTION_QUOTA_DEFAULT_N).toBe(3);
    expect(QUOTA_TIERS).toEqual(['explore', 'exploit', 'mutate']);
  });
});

describe('snake→camel 映射与缺失纪律', () => {
  test('完整回包：打分表/名额/档位/最近成功方向逐键归一', async () => {
    apiGetMock.mockResolvedValue(okRes(RAW));

    const res = await getDirectionQuotaPreview();
    expect(res.success).toBe(true);
    const d = res.data!;
    expect(d.available).toBe(true);
    expect(d.market).toBe('a_share');
    expect(d.n).toBe(3);
    expect(d.quota).toEqual({ explore: 0.3, exploit: 0.5, mutate: 0.2 });
    expect(d.slots).toEqual({ explore: 1, exploit: 2, mutate: 0 });
    expect(d.saturatedThreshold).toBe(2);
    expect(d.recentSuccessTasks).toBe(5);
    expect(d.recentSuccessDirs).toEqual(['尾盘资金流强度']);
    expect(d.error).toBeNull();

    const [a, b] = d.directions;
    expect(a).toEqual({
      direction: '尾盘资金流强度',
      attempts: 9,
      nTasks: 4,
      nSuccess: 1,
      p: 0.75,
      dupHits90d: 0,
      satPenalty: 0,
      score: 0.75,
      saturated: false,
    });
    expect(b.saturated).toBe(true);
    expect(b.satPenalty).toBe(0.5);

    expect(d.tiers!.explore).toEqual({
      pool: ['尾盘资金流强度'],
      weighting: 'blankness',
      fallback: null,
    });
    expect(d.tiers!.mutate.fallback).toBe('mutate_empty');
  });

  test('缺键/缺值一律 null 与 false，绝不补 0', async () => {
    apiGetMock.mockResolvedValue(
      okRes({
        available: false,
        market: 'a_share',
        directions: [{ direction: '半截行' }],
        error: '方向史/饱和数据读取出错，配额预览暂不可用（详见服务端日志）',
      }),
    );

    const res = await getDirectionQuotaPreview();
    const d = res.data!;
    expect(d.available).toBe(false);
    expect(d.n).toBeNull();
    expect(d.quota).toEqual({ explore: null, exploit: null, mutate: null });
    expect(d.slots).toEqual({ explore: null, exploit: null, mutate: null });
    expect(d.saturatedThreshold).toBeNull();
    expect(d.recentSuccessTasks).toBeNull();
    expect(d.tiers).toBeNull();
    expect(d.recentSuccessDirs).toEqual([]);
    expect(d.error).toContain('配额预览暂不可用');
    expect(d.directions[0]).toEqual({
      direction: '半截行',
      attempts: null,
      nTasks: null,
      nSuccess: null,
      p: null,
      dupHits90d: null,
      satPenalty: null,
      score: null,
      saturated: false,
    });
  });

  test('tiers 缺失 → null（候选词表为空 = 只出全史表，无档位预演）', async () => {
    apiGetMock.mockResolvedValue(okRes({ ...RAW, directions: [], tiers: null }));
    const res = await getDirectionQuotaPreview({ directions: [] });
    expect(res.data!.tiers).toBeNull();
    expect(res.data!.directions).toEqual([]);
  });
});

describe('请求形态', () => {
  test('directions 重复键 + 剥空白弃空串；n 整数化；market 透传', async () => {
    apiGetMock.mockResolvedValue(okRes(RAW));

    await getDirectionQuotaPreview({
      market: 'us_stock',
      directions: [' 甲 ', '', '乙'],
      n: 5.9,
    });

    const url = apiGetMock.mock.calls[0][0] as string;
    expect(url).toContain('/alpha-agent/direction-quota?');
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('market')).toBe('us_stock');
    expect(qs.getAll('directions')).toEqual(['甲', '乙']);
    expect(qs.get('n')).toBe('5');
  });

  test('全空参数：不带任何 query 键（与后端默认口径一致）', async () => {
    apiGetMock.mockResolvedValue(okRes(RAW));
    await getDirectionQuotaPreview();
    expect(apiGetMock.mock.calls[0][0]).toBe('/alpha-agent/direction-quota');
  });
});

describe('失败面', () => {
  test('网络/接口失败 → success:false + 原文，不编空壳', async () => {
    apiGetMock.mockRejectedValue(new Error('Network Error'));
    const res = await getDirectionQuotaPreview();
    expect(res.success).toBe(false);
    expect(res.error).toBe('Network Error');
    expect(res.data).toBeUndefined();
  });
});
