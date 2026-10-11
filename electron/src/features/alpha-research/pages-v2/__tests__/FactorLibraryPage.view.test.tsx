/**
 * FactorLibraryPage —— 视图切换与「列表默认」的用户反馈落点。
 *
 * 用户原话：「因子库界面难看；显示方式可以选择吗，现在是方块的、列表不能显示吗？」
 * 钉死的边：
 * - 默认 = 列表（FactorTable 密集表），且 13 个因子全部可见（第 11 个也不截）；
 * - 切到卡片后持久化到 localStorage（qa_factor_lib_view='cards'），重新挂载仍在卡片；
 * - 「AI 解读」「导出IDE」两个被移除的入口在因子库上不复现。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FactorLibraryPage } from '../FactorLibraryPage';

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  backtestClient: { get: vi.fn(), post: vi.fn() },
}));

// vi.mock 工厂被提升到文件顶部执行——夹具必须走 vi.hoisted，否则工厂里
// 引用普通 const 会在初始化前被求值（ReferenceError）
const { libraryFactors } = vi.hoisted(() => {
  type FixtureRow = {
    factorId: string;
    factorName: string;
    factorExpression: string;
    factorDescription: string;
    quality: 'medium';
    market: string;
    universe: string;
    ic?: number;
    round: number;
    direction: string;
    createdAt: string;
    lifecycle?: unknown;
    tags?: Record<string, string>;
  };
  const rows: FixtureRow[] = Array.from({ length: 13 }, (_, i) => ({
    factorId: `f${i + 1}`,
    factorName: `因子${i + 1}`,
    factorExpression: 'close/mean(close,5)',
    factorDescription: '',
    quality: 'medium' as const,
    market: 'a_share',
    universe: 'csi300',
    ic: i === 0 ? 0.0321 : undefined, // 缺失保持 undefined（显「—」）
    round: 0,
    direction: '正向',
    createdAt: '2026-10-01T00:00:00Z',
  }));
  // 生命周期/六维标签装饰（T-FE-06/07；后端行装配字段）：
  // f1 = 盲窗观察（带「回归哨兵」last_eval——后端若回归泄漏读数，前端仍
  // 不得显示，评审 MEDIUM-2 的防御）；f2 = 已降权退役（带证据）；
  // f11 = 实验态盲窗未到期；f12 = 实验态无锚点（盲窗行必须「—」）
  rows[0] = {
    ...rows[0],
    lifecycle: {
      state: 'observing',
      maturity: '观察',
      blind_until: '2026-11-09T00:00:00Z',
      last_eval: {
        month_end: '2026-10-31',
        n_days: 199,
        icir: 0.8765,
        status: 'ok',
        streak: 0,
        decision: 'hold',
        evaluated_at: '2026-11-01T00:00:00Z',
      },
    },
    tags: {
      data_source: 'statistical',
      frequency: 'daily',
      family: 'momentum',
      transform: 'unknown',
      target: 'fwd_ret_1d',
      maturity: '观察',
    },
  };
  rows[1] = {
    ...rows[1],
    lifecycle: {
      state: 'downweighted',
      maturity: '退役',
      blind_until: '2026-07-09T00:00:00Z',
      last_eval: {
        month_end: '2026-09-30',
        n_days: 241,
        icir: -0.0916,
        status: 'ok',
        streak: 4,
        decision: 'downweight',
        evaluated_at: '2026-10-11T00:00:00Z',
      },
    },
  };
  rows[10] = {
    ...rows[10],
    lifecycle: {
      state: 'experiment',
      maturity: '实验',
      blind_until: '2099-01-01T00:00:00Z',
      last_eval: null,
    },
  };
  rows[11] = {
    ...rows[11],
    lifecycle: {
      state: 'experiment',
      maturity: '实验',
      blind_until: null,
      last_eval: null,
    },
  };
  return { libraryFactors: rows };
});

vi.mock('../../services-v2/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services-v2/api')>();
  return {
    ...actual, // classifyQuality / UNIVERSE_LABELS 等保持真实
    getFactors: vi.fn().mockResolvedValue({
      success: true,
      data: {
        factors: libraryFactors,
        total: libraryFactors.length,
        limit: 200,
        offset: 0,
        serverLimit: 500,
      },
    }),
    getUniverses: vi.fn().mockResolvedValue({ success: true, data: { universes: [] } }),
    getFactoryFactors: vi.fn().mockResolvedValue({
      success: true,
      data: { factors: [], generatedAt: null },
    }),
    getFactorDetail: vi.fn().mockResolvedValue({ success: true, data: { factor: null } }),
  };
});

vi.mock('../../services/alphaAgentService', () => ({
  alphaAgentService: {
    listMarkets: vi.fn().mockResolvedValue([]),
    promoteByExpression: vi.fn(),
  },
}));

vi.mock('../../context-v2/TaskContext', () => ({
  useTaskContext: () => ({
    attachBacktestTask: vi.fn().mockResolvedValue(undefined),
    refreshMiningFactors: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('../../context-v2/RunQueueContext', () => ({
  useBacktestQueue: () => ({
    entries: {},
    activeCount: 0,
    enqueue: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
  }),
  useMaterializeRun: () => ({
    running: false,
    runningIds: new Set<string>(),
    lastResult: null,
    warning: null,
    start: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
    clearResult: vi.fn(),
  }),
}));

const noop = vi.fn();

beforeEach(() => {
  localStorage.clear();
  noop.mockReset();
});

describe('FactorLibraryPage：列表默认 + 视图切换持久化', () => {
  test('默认列表视图：13 行全渲染（第 11 个也可见），localStorage 记 list', async () => {
    const { container } = render(<FactorLibraryPage onNavigate={noop} />);

    // 等清单落地
    await screen.findByText('因子13');
    expect(screen.getByText(/共 13 行/)).toBeTruthy();
    expect(container.querySelectorAll('tbody tr')).toHaveLength(13);
    expect(screen.getByText('因子11')).toBeTruthy();

    expect(localStorage.getItem('qa_factor_lib_view')).toBe('list');
    // 被移除的两个入口不复现
    expect(screen.queryByText(/AI 解读/)).toBeNull();
    expect(screen.queryByText(/导出IDE/)).toBeNull();
  });

  test('切到卡片 → 持久化为 cards；重新挂载仍是卡片；再切回列表', async () => {
    const first = render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    fireEvent.click(screen.getByTitle('卡片视图'));

    expect(localStorage.getItem('qa_factor_lib_view')).toBe('cards');
    // 表格（列表视图）消失，卡片仍在
    expect(screen.queryByText(/共 13 行/)).toBeNull();
    expect(screen.getByText('因子11')).toBeTruthy();

    // 重新挂载 = 刷新页面：视图从 localStorage 恢复
    first.unmount();
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');
    await waitFor(() =>
      expect(screen.getByTitle('卡片视图').getAttribute('aria-pressed')).toBe('true'),
    );
    expect(screen.queryByText(/共 13 行/)).toBeNull();

    // 切回列表
    fireEvent.click(screen.getByTitle('列表视图'));
    expect(localStorage.getItem('qa_factor_lib_view')).toBe('list');
    await screen.findByText(/共 13 行/);
  });
});

describe('FactorLibraryPage：生命周期与六维标签（T-FE-06/07）', () => {
  test('列表视图：观察/退役 chip 随行渲染，无 lifecycle 行零影响', async () => {
    const { container } = render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    expect(screen.getAllByText('观察').length).toBe(1);
    expect(screen.getAllByText('退役').length).toBe(1);
    // 第 3 行无 lifecycle → 状态列空态仍是「—」
    const thirdRow = container.querySelectorAll('tbody tr')[2];
    expect(thirdRow.textContent).toContain('—');
  });

  test('详情弹窗：观察因子 → chip + 盲窗倒计时 + 六维标签（unknown 显「—」）', async () => {
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    fireEvent.click(screen.getByText('因子1'));

    expect(await screen.findByText('生命周期与标签')).toBeTruthy();
    // 弹窗头部 chip（读数行 chip 之外再出 1 个）
    expect(screen.getAllByText('观察').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/盲窗观察期至 2026-11-09（剩 \d+ 天）/)).toBeTruthy();
    // 六维标签：值原样展示，unknown → 「—」
    expect(screen.getByText('数据源')).toBeTruthy();
    expect(screen.getByText('statistical')).toBeTruthy();
    expect(screen.getByText('fwd_ret_1d')).toBeTruthy();
    expect(screen.getByText('变换').parentElement?.textContent).toContain('—');
    // 盲窗内不出现任何评估读数——fixture 刻意带「回归哨兵」last_eval
    // （评审 MEDIUM-2：后端若回归泄漏，前端仍不得显示）
    expect(screen.queryByText(/最近评估/)).toBeNull();
    expect(screen.queryByText(/0\.8765/)).toBeNull();
  });

  test('详情弹窗：退役因子 → 显示最近评估证据行（无 tags 也不丢证据）', async () => {
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    fireEvent.click(screen.getByText('因子2'));

    expect(await screen.findByText('生命周期与标签')).toBeTruthy();
    expect(
      screen.getByText('最近评估：2026-09-30（241 天） ICIR -0.0916 · 连续 4 期偏弱'),
    ).toBeTruthy();
    // 盲窗行按实际终点分支（LOW-3）：退役 ≠ 凭空「已过盲窗」，带日期
    expect(screen.getByText('盲窗:').parentElement?.textContent).toContain(
      '已过（2026-07-09）',
    );
    // 该因子无 tags → 六维标签网格不渲染
    expect(screen.queryByText('数据源')).toBeNull();
  });

  test('详情弹窗：实验态盲窗未到期 → 「未到期（至 …）」（不再冒充已过盲窗）', async () => {
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    fireEvent.click(screen.getByText('因子11'));

    expect(await screen.findByText('生命周期与标签')).toBeTruthy();
    expect(screen.getByText('盲窗:').parentElement?.textContent).toContain(
      '未到期（至 2099-01-01）',
    );
  });

  test('详情弹窗：实验态无时间锚点 → 盲窗行「—」（宁缺勿造）', async () => {
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    fireEvent.click(screen.getByText('因子12'));

    expect(await screen.findByText('生命周期与标签')).toBeTruthy();
    expect(screen.getByText('盲窗:').parentElement?.textContent).toContain('—');
    expect(screen.queryByText(/已过盲窗/)).toBeNull();
  });

  test('卡片视图：生命周期 chip 进卡片徽章行', async () => {
    localStorage.setItem('qa_factor_lib_view', 'cards');
    render(<FactorLibraryPage onNavigate={noop} />);
    await screen.findByText('因子13');

    expect(screen.getAllByText('观察').length).toBe(1);
    expect(screen.getAllByText('退役').length).toBe(1);
  });
});
