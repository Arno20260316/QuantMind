/**
 * 决策轨迹 API 桥（T-FM-07）契约：
 * - 请求形态：`/alpha-agent/tasks/{id}/trajectory`（与 tasks/{id} 同族）；
 * - snake→camel 归一（节点/因子/轮次/顶层逐键）；
 * - **缺失一律 null / []，绝不补 0**——`selected` 缺省必须是 null（未知），
 *   绝不能变 false（会被界面读成「未进入实现」的确定结论）；
 * - 404 与网络错误分开报（轨迹不存在 ≠ 读取失败）。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: getMock },
}));

import { getTaskTrajectory } from '../api';

const RAW = {
  task_id: 'd9a6e6a16a0445de',
  direction: '概念热度轮动',
  status: 'completed',
  market: 'a_share',
  partial: false,
  attribution: 'loop',
  loops: [
    {
      loop: 0,
      nodes: [
        {
          phase: 'hypothesis',
          status: 'ok',
          start: '2026-10-08T02:00:00Z',
          end: '2026-10-08T02:00:36Z',
          duration_s: 36.123,
          pkl_count: 1,
        },
        { phase: 'running', status: 'partial', start: null, end: null, duration_s: null, pkl_count: 0 },
      ],
      factors: [
        { name: 'ma_cross', selected: true, outcome: 'materialized', reason: null },
        { name: 'persistence', selected: false, outcome: 'rejected_duplicate', reason: '与 lib_9 高度相关（ρ=0.93）' },
        { name: 'zscore', selected: null, outcome: null, reason: null },
      ],
      flagged: true,
      flags: ['no_selected'],
      flag_reason: '本循环产出因子均未进入实现',
    },
  ],
  factors: [],
  truncated: true,
  dropped_loops: 3,
  generated_at: '2026-10-12T08:00:00Z',
};

const okRes = (data: unknown) => ({ data: { code: 200, data } });

beforeEach(() => {
  getMock.mockReset();
});

describe('请求形态', () => {
  test('URL = /alpha-agent/tasks/{id}/trajectory', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    await getTaskTrajectory('d9a6e6a16a0445de');
    expect(getMock.mock.calls[0][0]).toBe(
      '/alpha-agent/tasks/d9a6e6a16a0445de/trajectory',
    );
  });

  test('任务 id 逐段编码（特殊字符不拼进路径）', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    await getTaskTrajectory('t/1 2');
    expect(getMock.mock.calls[0][0]).toBe(
      '/alpha-agent/tasks/t%2F1%202/trajectory',
    );
  });
});

describe('snake→camel 归一与缺失纪律', () => {
  test('完整回包逐键归一（节点 durationS/pklCount、轮次 flagReason、顶层 droppedLoops）', async () => {
    getMock.mockResolvedValue(okRes(RAW));

    const res = await getTaskTrajectory('d9a6e6a16a0445de');
    expect(res.success).toBe(true);
    const d = res.data!;
    expect(d.taskId).toBe('d9a6e6a16a0445de');
    expect(d.direction).toBe('概念热度轮动');
    expect(d.status).toBe('completed');
    expect(d.market).toBe('a_share');
    expect(d.partial).toBe(false);
    expect(d.attribution).toBe('loop');
    expect(d.truncated).toBe(true);
    expect(d.droppedLoops).toBe(3);
    expect(d.generatedAt).toBe('2026-10-12T08:00:00Z');

    const loop = d.loops[0];
    expect(loop.loop).toBe(0);
    expect(loop.flagged).toBe(true);
    expect(loop.flags).toEqual(['no_selected']);
    expect(loop.flagReason).toBe('本循环产出因子均未进入实现');
    expect(loop.nodes[0]).toEqual({
      phase: 'hypothesis',
      status: 'ok',
      start: '2026-10-08T02:00:00Z',
      end: '2026-10-08T02:00:36Z',
      durationS: 36.123,
      pklCount: 1,
    });
    expect(loop.nodes[1].durationS).toBeNull();
    expect(loop.factors[0]).toEqual({
      name: 'ma_cross',
      selected: true,
      outcome: 'materialized',
      reason: null,
    });
    expect(loop.factors[1].reason).toContain('高度相关');
  });

  test('缺键一律 null/[]/false，selected 缺省 = null（未知），绝不当 false', async () => {
    getMock.mockResolvedValue(
      okRes({ task_id: 't-1', loops: [{ loop: 0, factors: [{ name: 'x' }] }] }),
    );

    const res = await getTaskTrajectory('t-1');
    const d = res.data!;
    expect(d.direction).toBeNull();
    expect(d.status).toBeNull();
    expect(d.market).toBeNull();
    expect(d.partial).toBe(false);
    expect(d.attribution).toBe('loop');
    // task_id 缺席才回退入参；此处给了 t-1 就用它
    expect(d.taskId).toBe('t-1');
    expect(d.truncated).toBe(false);
    expect(d.droppedLoops).toBe(0);
    expect(d.generatedAt).toBeNull();
    expect(d.factors).toEqual([]);
    expect(d.loops[0].flagged).toBe(false);
    expect(d.loops[0].flags).toEqual([]);
    expect(d.loops[0].flagReason).toBeNull();
    expect(d.loops[0].nodes).toEqual([]);
    const f = d.loops[0].factors[0];
    expect(f.selected).toBeNull();
    expect(f.outcome).toBeNull();
    expect(f.reason).toBeNull();
  });

  test('task_id 缺席回退入参 id（界面头部仍可锚定）', async () => {
    getMock.mockResolvedValue(okRes({ loops: [] }));
    const res = await getTaskTrajectory('fallback-1');
    expect(res.data!.taskId).toBe('fallback-1');
    expect(res.data!.loops).toEqual([]);
  });

  test('坏形状（loops 非数组/节点非对象）不炸，按缺席处理', async () => {
    getMock.mockResolvedValue(okRes({ loops: 'oops', factors: [null, { name: 'ok' }] }));
    const res = await getTaskTrajectory('t-2');
    const d = res.data!;
    expect(d.loops).toEqual([]);
    expect(d.factors).toEqual([
      { name: '', selected: null, outcome: null, reason: null },
      { name: 'ok', selected: null, outcome: null, reason: null },
    ]);
  });
});

describe('失败面', () => {
  test('404 → 「轨迹不存在」专用文案（不是网络错误）', async () => {
    getMock.mockRejectedValue({ response: { status: 404 } });
    const res = await getTaskTrajectory('missing-1');
    expect(res.success).toBe(false);
    expect(res.error).toContain('轨迹不存在');
    expect(res.error).toContain('日志目录');
  });

  test('其他错误 → 「轨迹读取失败」+ 原文', async () => {
    getMock.mockRejectedValue(new Error('Network Error'));
    const res = await getTaskTrajectory('t-3');
    expect(res.success).toBe(false);
    expect(res.error).toBe('轨迹读取失败：Network Error');
  });
});
