/**
 * 晋级门槛卡：两档门禁逐项渲染 + **面向用户文案不得出现内部工单号**。
 *
 * 口径（记忆 `ui-no-internal-ticket-codes`）：标题/标签/按钮里的 `T-P3-05`、`T-P4-06`
 * 这类内部编号会把「差哪一项」淹没掉，用户在界面上看到就是一串看不懂的工单号。
 * 后端 `promotion_gate` 的说明串会原样流进本卡的 `reason`，所以这里钉一条文本扫描。
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../features/eval-center/services/evalCenterService', () => ({
  getStrategyHealth: vi.fn(),
}));

import { getStrategyHealth } from '../../../features/eval-center/services/evalCenterService';
import { PromotionGateCard } from '../PromotionGateCard';

const mockGetHealth = vi.mocked(getStrategyHealth);

/** 一次真实探针形状的响应：SIM 放行、REAL 因模拟交易日不足拦截。 */
const archiveResponse = () =>
  ({
    success: true,
    data: {
      strategy_id: '2',
      latest: {
        verdict: 'A',
        confidence: 88,
        evidence_source: 'backtest',
        snapshot_date: '2026-10-13',
        reasons: ['IC 显著为正'],
        suggestions: [],
      },
      history: [],
      gate: { mode: 'SIMULATION', allowed: true, note: '体检结论 A（可信度 88/100）' },
      eligibility: {
        SIMULATION: {
          allowed: true,
          mode: 'SIMULATION',
          items: [
            {
              key: 'lifecycle',
              label: '生命周期状态',
              value: 'SIM',
              passed: true,
              reason: '',
              next_step: '',
            },
            {
              key: 'health',
              label: '回测体检',
              value: 'A · 可信度 88/100',
              passed: true,
              reason: '',
              next_step: '',
            },
          ],
          next_step: '',
        },
        REAL: {
          allowed: false,
          mode: 'REAL',
          items: [
            {
              key: 'lifecycle',
              label: '生命周期状态',
              value: 'SIM',
              passed: true,
              reason: '',
              next_step: '',
            },
            {
              key: 'health',
              label: '回测体检',
              value: 'A · 可信度 88/100',
              passed: true,
              reason: '',
              next_step: '',
            },
            {
              key: 'sim_days',
              label: '模拟交易日',
              value: '12 / 20 交易日',
              passed: false,
              reason: '模拟盘运行 12 交易日 < 门槛 20 日（晋级门槛），请继续模拟验证。',
              next_step: '继续模拟运行，累计满 20 个交易日后再进实盘',
            },
            {
              key: 'tracking_error',
              label: '跟踪误差',
              value: '无记录（只提示不拦）',
              passed: true,
              reason: '',
              next_step: '',
            },
          ],
          next_step: '继续模拟运行，累计满 20 个交易日后再进实盘',
        },
      },
      eligibility_mode: 'SIMULATION',
    },
    meta: { count: 0 },
  }) as never;

beforeEach(() => {
  mockGetHealth.mockReset();
});

describe('PromotionGateCard', () => {
  it('两档门禁分开列：进模拟盘放行、进实盘拦截', async () => {
    // Arrange
    mockGetHealth.mockResolvedValue(archiveResponse());

    // Act
    const { container } = render(<PromotionGateCard strategyId="2" />);
    await waitFor(() => expect(screen.getByTestId('promotion-gate-REAL')).toBeTruthy());

    // Assert
    expect(screen.getByTestId('promotion-gate-SIMULATION').dataset.allowed).toBe('true');
    expect(screen.getByTestId('promotion-gate-REAL').dataset.allowed).toBe('false');
    expect(container.textContent).toContain('模拟交易日');
    expect(container.textContent).toContain('12 / 20 交易日');
    expect(screen.queryByText(/尚无体检留档/)).toBeNull();
  });

  it('缺记录如实显示「无记录」，绝不渲染成 0', async () => {
    // Arrange
    mockGetHealth.mockResolvedValue(archiveResponse());

    // Act
    const { container } = render(<PromotionGateCard strategyId="2" />);
    await waitFor(() => expect(screen.getByTestId('promotion-gate-REAL')).toBeTruthy());

    // Assert
    expect(container.textContent).toContain('无记录（只提示不拦）');
  });

  it('面向用户文案不含内部工单号（T-Pnn 一律不出现在界面）', async () => {
    // Arrange
    mockGetHealth.mockResolvedValue(archiveResponse());

    // Act
    const { container } = render(<PromotionGateCard strategyId="2" />);
    await waitFor(() => expect(screen.getByTestId('promotion-gate-REAL')).toBeTruthy());

    // Assert
    expect(container.textContent).not.toMatch(/T-P\d/);
  });
});
