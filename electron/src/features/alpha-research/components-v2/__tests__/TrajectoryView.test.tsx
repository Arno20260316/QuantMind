/**
 * 决策轨迹视图（T-FM-07）契约：
 * - 逐 loop 五环节节点卡（状态点/耗时/pkl 数）：缺环节补占位、不跳格；
 * - 因子结局 chips（实现/未实现 + 物化/拒因中文标签）与定位旗标（tooltip=后端 flag_reason）；
 * - 缺失一律「—」：时长不可解、结局未知绝不当 0/不静默消失；
 * - partial / task_level 降级 / truncated 三种如实标注各自上屏；
 * - 任务 id 只显前 8 位（全文只在 title）；请求失败给重试；
 * - taskId 为空不发起请求、不渲染。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TrajectoryView } from '../TrajectoryView';
import type { TaskTrajectory, TrajectoryLoop } from '../../services-v2/api';

const apiMocks = vi.hoisted(() => ({ getTaskTrajectory: vi.fn() }));

vi.mock('../../services-v2/api', () => ({
  getTaskTrajectory: apiMocks.getTaskTrajectory,
}));

const ok = (data: TaskTrajectory) => ({ success: true, data });

function node(phase: string, over: Partial<TrajectoryLoop['nodes'][number]> = {}) {
  return {
    phase,
    status: 'ok',
    start: '2026-10-08T02:00:00Z',
    end: '2026-10-08T02:00:36Z',
    durationS: 36,
    pklCount: 1,
    ...over,
  };
}

const loop0: TrajectoryLoop = {
  loop: 0,
  nodes: [
    node('hypothesis'),
    node('experiment'),
    node('coding', { durationS: 53, pklCount: 2 }),
    node('running', { durationS: 7, pklCount: 0 }),
    node('feedback', { durationS: 0, pklCount: 1 }),
  ],
  factors: [
    { name: 'ma_cross', selected: true, outcome: 'materialized', reason: null },
    {
      name: 'persistence',
      selected: false,
      outcome: 'rejected_duplicate',
      reason: '与 lib_9 高度相关（ρ=0.93）',
    },
  ],
  flagged: false,
  flags: [],
  flagReason: null,
};

const loop2: TrajectoryLoop = {
  loop: 2,
  nodes: [node('hypothesis', { durationS: 120 })],
  factors: [
    { name: 'zscore', selected: false, outcome: 'rejected_gate', reason: '扣成本后无收益' },
  ],
  flagged: true,
  flags: ['no_selected'],
  flagReason: '本循环产出因子均未进入实现',
};

const view = (over: Partial<TaskTrajectory> = {}): TaskTrajectory => ({
  taskId: 'd9a6e6a16a0445de',
  direction: '概念热度轮动',
  status: 'completed',
  market: 'a_share',
  partial: false,
  attribution: 'loop',
  loops: [loop0, loop2],
  factors: [],
  truncated: false,
  droppedLoops: 0,
  generatedAt: '2026-10-12T08:00:00Z',
  ...over,
});

beforeEach(() => {
  apiMocks.getTaskTrajectory.mockReset();
});

describe('TrajectoryView', () => {
  it('逐 loop 五环节节点卡 + 因子结局 chips + 旗标全部上屏', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(ok(view()));

    render(<TrajectoryView taskId="d9a6e6a16a0445de" />);

    expect(await screen.findByText('首轮')).toBeTruthy();
    expect(apiMocks.getTaskTrajectory).toHaveBeenCalledWith('d9a6e6a16a0445de');
    // 任务 id 只显前 8 位（全文只在 title）
    expect(screen.getByText('d9a6e6a1')).toBeTruthy();
    expect(screen.queryByText('d9a6e6a16a0445de')).toBeNull();

    // 首轮五环节齐全（生成假设 36s ×2：假设+实验共用区间）
    expect(screen.getAllByText('生成假设').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('36s').length).toBe(2);
    expect(screen.getByText('53s')).toBeTruthy();
    expect(screen.getByText('7s')).toBeTruthy();
    expect(screen.getByText('0s')).toBeTruthy();
    // pkl 数上屏
    expect(screen.getByText('完成 · 2件')).toBeTruthy();
    expect(screen.getByText('完成 · 0件')).toBeTruthy();

    // Loop 2 缺四环节 → 补「无记录」占位（不跳格）
    expect(screen.getByText('Loop 2')).toBeTruthy();
    expect(screen.getAllByText('无记录').length).toBe(4);
    expect(screen.getByText('2m00s')).toBeTruthy();

    // 因子 chips：实现/未实现 + 结局中文标签
    expect(screen.getByText('ma_cross')).toBeTruthy();
    expect(screen.getByText('实现')).toBeTruthy();
    expect(screen.getByText('已物化')).toBeTruthy();
    expect(screen.getByText('persistence')).toBeTruthy();
    // persistence（Loop 0）与 zscore（Loop 2）都未进入实现 → 两枚「未实现」
    expect(screen.getAllByText('未实现').length).toBe(2);
    expect(screen.getByText('重复被拒')).toBeTruthy();
    // 结局 chip 的 title 带拒因原文
    expect(screen.getByText('重复被拒').closest('[title]')?.getAttribute('title')).toContain(
      '高度相关',
    );

    // 定位旗标：短标签 + tooltip = 后端 flag_reason
    const flag = screen.getByText('本轮产出均未进入实现');
    expect(flag.getAttribute('title')).toBe('本循环产出因子均未进入实现');
  });

  it('缺失一律「—」：时长不可解/结局未知不伪装成 0', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(
      ok(
        view({
          loops: [
            {
              loop: 0,
              nodes: [node('hypothesis', { durationS: null, pklCount: null, status: 'partial' })],
              factors: [
                { name: 'unknown_f', selected: null, outcome: null, reason: null },
              ],
              flagged: false,
              flags: [],
              flagReason: null,
            },
          ],
        }),
      ),
    );

    render(<TrajectoryView taskId="t-1" />);

    expect(await screen.findByText('unknown_f')).toBeTruthy();
    // durationS=null → 「—」；pklCount=null → 不带「件」
    expect(screen.queryByText(/· \d+件/)).toBeNull();
    // selected=null → 既不显实现也不显未实现
    expect(screen.queryByText('实现')).toBeNull();
    expect(screen.queryByText('未实现')).toBeNull();
    // 时长与结局未知各显「—」
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('部分')).toBeTruthy();
  });

  it('partial（任务未终态）如实标注快照语义', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(ok(view({ partial: true, status: 'running' })));
    render(<TrajectoryView taskId="t-1" />);
    expect(await screen.findByText(/任务尚未结束/)).toBeTruthy();
    expect(screen.getByText('运行中')).toBeTruthy();
  });

  it('task_level 降级：逐轮因子置空的说明 + 任务级产出清单', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(
      ok(
        view({
          attribution: 'task_level',
          loops: [{ ...loop0, factors: [] }],
          factors: [{ name: 'task_level_f', selected: null, outcome: 'materialized', reason: null }],
        }),
      ),
    );

    render(<TrajectoryView taskId="t-1" />);

    expect(await screen.findByText(/逐轮因子归因不可用/)).toBeTruthy();
    expect(screen.getByText('任务级产出清单')).toBeTruthy();
    expect(screen.getByText('task_level_f')).toBeTruthy();
    expect(screen.getByText(/本轮未归因到产出因子/)).toBeTruthy();
  });

  it('truncated：截断如实标注（保留轮数 + 已裁轮数）', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(
      ok(view({ truncated: true, droppedLoops: 5, loops: [loop0] })),
    );
    render(<TrajectoryView taskId="t-1" />);
    expect(await screen.findByText(/仅保留最近 1 轮/)).toBeTruthy();
    expect(screen.getByText(/已裁 5 轮/)).toBeTruthy();
  });

  it('无任何轮次（空日志目录）→ 空态文案，不报错', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValue(ok(view({ loops: [] })));
    render(<TrajectoryView taskId="t-1" />);
    expect(await screen.findByText(/没有扫到决策轨迹记录/)).toBeTruthy();
  });

  it('请求失败（如 404 轨迹不存在）→ 文案 + 重试恢复', async () => {
    apiMocks.getTaskTrajectory.mockResolvedValueOnce({
      success: false,
      error: '轨迹不存在：该任务没有可读的日志目录（或已被清理）',
    });
    apiMocks.getTaskTrajectory.mockResolvedValueOnce(ok(view()));

    render(<TrajectoryView taskId="t-1" />);

    expect(await screen.findByText(/轨迹不存在/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(await screen.findByText('首轮')).toBeTruthy();
    expect(apiMocks.getTaskTrajectory).toHaveBeenCalledTimes(2);
  });

  it('taskId 为空：不发起请求、不渲染', () => {
    const { container } = render(<TrajectoryView taskId="" />);
    expect(container.innerHTML).toBe('');
    expect(apiMocks.getTaskTrajectory).not.toHaveBeenCalled();
  });
});
