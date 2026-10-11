/**
 * 经验记忆 API 桥（T-FM-04）契约：
 * - snake→camel 归一（拒因桶 / 饱和方向 / 方向任务史 / sources）；
 * - **缺失一律 null**（评审 code-review CR3）：`count()` 曾把缺失吞成 0——
 *   「没有统计」与「统计为 0」是两回事，面板按 null 落「—」；
 * - 真值 0 原样透传（0 不是缺失）；
 * - report 只透传（后端已只回 basename，绝对路径不出网）。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: getMock },
}));

import { getExperienceMemory } from '../experienceMemoryApi';

const okRes = (data: unknown) => ({ data: { code: 200, data } });

const RAW = {
  available: true,
  market: 'a_share',
  generated_at: '2026-10-12T08:00:00Z',
  reason_buckets: [
    {
      bucket: 'high_corr',
      label: '与库里因子高相关',
      count: 4,
      last_at: '2026-10-01T00:00:00Z',
      sample_dirs: ['动量反转'],
      sample_columns: ['ts_rank'],
      sources: ['manifest'],
    },
    // 老后端/降级路径：count 缺失 → 必须 null，不得吞成 0
    { bucket: 'no_effect', label: '无显著增量' },
  ],
  saturated_directions: [
    {
      direction: '尾盘资金流强度',
      label: '尾盘资金流强度',
      dup_hits: 3,
      dup_hits_90d: 2,
      top_opponent: 'a158_SUMN10',
      materialized_count: 1,
    },
    { direction: '(未归因)', label: '(未归因)' },
  ],
  direction_stats: [{ direction: '动量反转', label: '动量反转' }],
  sources: {
    tasks: 3,
    manifest_entries: 236,
    report: 'T-FM-01_x.json',
    report_generated_at: null,
  },
};

beforeEach(() => {
  getMock.mockReset();
});

describe('请求形态', () => {
  test('market 给值 → 查询串；缺省 → 无查询串', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    await getExperienceMemory({ market: 'a_share' });
    expect(getMock.mock.calls[0][0]).toBe('/alpha-agent/experience-memory?market=a_share');

    getMock.mockClear();
    getMock.mockResolvedValue(okRes(RAW));
    await getExperienceMemory();
    expect(getMock.mock.calls[0][0]).toBe('/alpha-agent/experience-memory');
  });
});

describe('snake→camel 归一与缺失纪律', () => {
  test('真值逐键归一；count 缺失 → null（不是 0）', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    const res = await getExperienceMemory({ market: 'a_share' });
    expect(res.success).toBe(true);
    const d = res.data!;

    expect(d.available).toBe(true);
    expect(d.generatedAt).toBe('2026-10-12T08:00:00Z');

    expect(d.reasonBuckets[0]).toEqual({
      bucket: 'high_corr',
      label: '与库里因子高相关',
      count: 4,
      lastAt: '2026-10-01T00:00:00Z',
      sampleDirs: ['动量反转'],
      sampleColumns: ['ts_rank'],
      sources: ['manifest'],
    });
    // 缺失桶：count=null、sample 空数组、（label 回退 bucket 名）
    expect(d.reasonBuckets[1]).toEqual({
      bucket: 'no_effect',
      label: '无显著增量',
      count: null,
      lastAt: null,
      sampleDirs: [],
      sampleColumns: [],
      sources: [],
    });

    expect(d.saturatedDirections[0]).toEqual({
      direction: '尾盘资金流强度',
      label: '尾盘资金流强度',
      dupHits: 3,
      dupHits90d: 2,
      topOpponent: 'a158_SUMN10',
      materializedCount: 1,
    });
    expect(d.saturatedDirections[1]).toEqual({
      direction: '(未归因)',
      label: '(未归因)',
      dupHits: null,
      dupHits90d: null,
      topOpponent: null,
      materializedCount: null,
    });

    expect(d.directionStats[0]).toEqual({
      direction: '动量反转',
      label: '动量反转',
      tasks: null,
      completed: null,
      factors: null,
    });

    // report 只透传（后端已只回 basename）
    expect(d.sources).toEqual({
      tasks: 3,
      manifestEntries: 236,
      report: 'T-FM-01_x.json',
      reportGeneratedAt: null,
    });
  });

  test('真值 0 原样透传（0 是统计值，不是缺失）', async () => {
    getMock.mockResolvedValue(
      okRes({
        available: true,
        market: 'a_share',
        reason_buckets: [{ bucket: 'b', label: 'b', count: 0 }],
        sources: { tasks: 0, manifest_entries: 0, report: null },
      }),
    );
    const d = (await getExperienceMemory()).data!;
    expect(d.reasonBuckets[0].count).toBe(0);
    expect(d.sources.tasks).toBe(0);
    expect(d.sources.manifestEntries).toBe(0);
  });

  test('payload 完全缺席（data={}）不炸：sources 全 null、数组为空', async () => {
    getMock.mockResolvedValue(okRes({}));
    const res = await getExperienceMemory();
    expect(res.success).toBe(true);
    const d = res.data!;
    expect(d.available).toBe(false);
    expect(d.reasonBuckets).toEqual([]);
    expect(d.saturatedDirections).toEqual([]);
    expect(d.directionStats).toEqual([]);
    expect(d.sources).toEqual({
      tasks: null,
      manifestEntries: null,
      report: null,
      reportGeneratedAt: null,
    });
  });
});
