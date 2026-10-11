/**
 * 经验记忆/禁区（T-FM-04）契约：
 * - 拒因聚类与饱和方向按后端顺序渲染（次数/最近/来源、命中/已物化/对手）；
 * - 缺什么显「—」：lastAt 缺失、topOpponent 缺失绝不当 0 上屏；
 * - 全空（available=false）给引导文案，不假装统计存在；
 * - 失败给可重试的错误文案，重试后恢复。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ExperienceMemoryPanel } from '../ExperienceMemoryPanel';
import type { ExperienceMemoryView } from '../../services-v2/experienceMemoryApi';

const apiMocks = vi.hoisted(() => ({ getExperienceMemory: vi.fn() }));

vi.mock('../../services-v2/experienceMemoryApi', () => ({
  getExperienceMemory: apiMocks.getExperienceMemory,
  EXPERIENCE_MEMORY_DEFAULT_MARKET: 'a_share',
}));

const view = (over: Partial<ExperienceMemoryView> = {}): ExperienceMemoryView => ({
  available: true,
  market: 'a_share',
  generatedAt: '2026-10-11T05:00:00Z',
  reasonBuckets: [
    {
      bucket: 'dup_library',
      label: '值级重复（库内已有高度相关列）',
      count: 4,
      lastAt: '2026-09-29T00:00:00Z',
      sampleDirs: ['尾盘资金流强度'],
      sampleColumns: ['rd_reversal_10d'],
      sources: ['manifest'],
    },
    {
      bucket: 'stat:fdr',
      label: '统计派·多重检验未过（BH-FDR）',
      count: 1,
      lastAt: null,
      sampleDirs: [],
      sampleColumns: [],
      sources: ['report'],
    },
  ],
  saturatedDirections: [
    {
      direction: '尾盘资金流强度',
      label: '尾盘资金流强度',
      dupHits: 3,
      dupHits90d: 2,
      topOpponent: 'a158_SUMN10',
      materializedCount: 1,
    },
    {
      direction: '(未归因)',
      label: '(未归因)',
      dupHits: 3,
      dupHits90d: 1,
      topOpponent: null,
      materializedCount: 0,
    },
  ],
  directionStats: [],
  sources: {
    tasks: 3,
    manifestEntries: 236,
    report: '/refs/T-FM-01_x.json',
    reportGeneratedAt: '2026-10-11T01:27:30Z',
  },
  ...over,
});

const ok = (data: ExperienceMemoryView) => ({ success: true, data });

beforeEach(() => {
  apiMocks.getExperienceMemory.mockReset();
});

describe('ExperienceMemoryPanel', () => {
  it('渲染拒因聚类与饱和方向：计数/最近/来源 + 命中/已物化/对手', async () => {
    apiMocks.getExperienceMemory.mockResolvedValue(ok(view()));

    render(<ExperienceMemoryPanel />);

    expect(await screen.findByText('值级重复（库内已有高度相关列）')).toBeTruthy();
    expect(screen.getByText('统计派·多重检验未过（BH-FDR）')).toBeTruthy();
    expect(apiMocks.getExperienceMemory).toHaveBeenCalledWith({ market: 'a_share' });

    // 桶行：mono 代码、×次数、来源标注
    expect(screen.getByText('dup_library')).toBeTruthy();
    expect(screen.getByText('×4')).toBeTruthy();
    expect(screen.getByText('×1')).toBeTruthy();
    expect(screen.getAllByText(/最近 /)).toHaveLength(2);
    // lastAt 缺失显「—」（不渲染 0/空）
    expect(screen.getByText('最近 —')).toBeTruthy();
    expect(screen.getByText(/来源：manifest/)).toBeTruthy();
    expect(screen.getByText(/来源：report/)).toBeTruthy();

    // 饱和方向行
    expect(screen.getByText('尾盘资金流强度')).toBeTruthy();
    expect(screen.getByText('(未归因)')).toBeTruthy();
    expect(screen.getByText(/命中 3（90日 2）/)).toBeTruthy();
    expect(screen.getByText('已物化 1')).toBeTruthy();
    expect(screen.getByText(/最强对手 a158_SUMN10/)).toBeTruthy();
    expect(screen.getByText(/最强对手 —/)).toBeTruthy();

    // 头部生成时间与来源出处
    expect(screen.getByText(/生成于/)).toBeTruthy();
    expect(screen.getByText(/任务史 3 条/)).toBeTruthy();
    expect(screen.getByText(/物化清单 236 条/)).toBeTruthy();
    expect(screen.getByText(/统计派报告 有/)).toBeTruthy();
  });

  it('计数缺失（null）→ 全落「—」，绝不上屏 0（0 是统计值，缺失是没统计）', async () => {
    apiMocks.getExperienceMemory.mockResolvedValue(
      ok(
        view({
          reasonBuckets: [
            {
              bucket: 'dup_library',
              label: '值级重复',
              count: null,
              lastAt: null,
              sampleDirs: [],
              sampleColumns: [],
              sources: ['manifest'],
            },
          ],
          saturatedDirections: [
            {
              direction: '尾盘资金流强度',
              label: '尾盘资金流强度',
              dupHits: null,
              dupHits90d: null,
              topOpponent: null,
              materializedCount: null,
            },
            {
              direction: '量价背离确认',
              label: '量价背离确认',
              dupHits: 0,
              dupHits90d: 0,
              topOpponent: null,
              materializedCount: 0,
            },
          ],
          sources: { tasks: null, manifestEntries: null, report: null, reportGeneratedAt: null },
        }),
      ),
    );

    render(<ExperienceMemoryPanel />);

    expect(await screen.findByText('值级重复')).toBeTruthy();
    expect(screen.getByText('×—')).toBeTruthy();
    expect(screen.getByText(/命中 —（90日 —）/)).toBeTruthy();
    expect(screen.getByText('已物化 —')).toBeTruthy();
    expect(screen.getByText(/任务史 — 条/)).toBeTruthy();
    expect(screen.getByText(/物化清单 — 条/)).toBeTruthy();
    // 真值 0 仍上屏为 0，不被吞成「—」（0 是统计值，缺失才是没统计）
    expect(screen.getByText(/命中 0（90日 0）/)).toBeTruthy();
    expect(screen.getByText('已物化 0')).toBeTruthy();
  });

  it('全空（available=false）给引导文案，不假装统计存在', async () => {
    apiMocks.getExperienceMemory.mockResolvedValue(
      ok(
        view({
          available: false,
          generatedAt: null,
          reasonBuckets: [],
          saturatedDirections: [],
          sources: { tasks: 0, manifestEntries: 0, report: null, reportGeneratedAt: null },
        }),
      ),
    );

    render(<ExperienceMemoryPanel />);

    expect(await screen.findByText(/暂无历史统计/)).toBeTruthy();
    expect(screen.getByText(/暂无可用历史统计/)).toBeTruthy();
    // 报告缺失时头部不渲染「生成于」（没有可展示的时刻）
    expect(screen.queryByText(/生成于/)).toBeNull();
  });

  it('拉取失败给错误文案与重试；重试后恢复', async () => {
    apiMocks.getExperienceMemory.mockRejectedValueOnce(new Error('后端不可达'));
    apiMocks.getExperienceMemory.mockResolvedValueOnce(ok(view()));

    render(<ExperienceMemoryPanel />);

    expect(await screen.findByText(/经验记忆读取失败：后端不可达/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(await screen.findByText('值级重复（库内已有高度相关列）')).toBeTruthy();
    expect(apiMocks.getExperienceMemory).toHaveBeenCalledTimes(2);
  });
});
