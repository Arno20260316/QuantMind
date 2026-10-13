import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useStartEligibility } from '../useStartEligibility';
import type { StrategyFile } from '../../../../../../types/backtest/strategy';

vi.mock('../../../../../../features/eval-center/services/evalCenterService', () => ({
    getStrategyHealth: vi.fn(),
}));

import { getStrategyHealth } from '../../../../../../features/eval-center/services/evalCenterService';

const mockGetHealth = vi.mocked(getStrategyHealth);

const strategy = (overrides: Partial<StrategyFile> = {}): StrategyFile =>
    ({
        id: '42',
        name: 'topk 策略',
        source: 'personal',
        code: '',
        ...overrides,
    }) as StrategyFile;

/** 造一个 `/eval/health/{id}` 响应；`eligibility` 只放 `mode` 那一档。 */
const healthResponse = (
    mode: 'SIMULATION' | 'REAL',
    eligibility: Record<string, unknown> | undefined,
) =>
    ({
        success: true,
        data: {
            strategy_id: '42',
            latest: null,
            history: [],
            gate: { mode: 'SIMULATION', allowed: true, note: '' },
            eligibility: eligibility ? { [mode]: eligibility } : undefined,
            eligibility_mode: mode,
        },
        meta: { count: 0 },
    }) as never;

const realItems = [
    {
        key: 'lifecycle',
        label: '生命周期状态',
        value: 'VERIFIED',
        passed: false,
        reason: '策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证',
        next_step: '先切到模拟盘启动一次，完成模拟验证后再进实盘',
    },
];

beforeEach(() => {
    mockGetHealth.mockReset();
});

describe('useStartEligibility', () => {
    it('未选策略 → 不请求，直接放行并说明原因', async () => {
        // Arrange & Act
        const { result } = renderHook(() => useStartEligibility(undefined, 'REAL'));

        // Assert
        expect(result.current.allowed).toBe(true);
        expect(result.current.items).toEqual([]);
        expect(result.current.uncheckedReason).toBe('未选择策略');
        expect(mockGetHealth).not.toHaveBeenCalled();
    });

    it('内置模板不吃门禁 → 不请求，也不禁用按钮', async () => {
        // Arrange & Act
        const { result } = renderHook(() =>
            useStartEligibility(strategy({ id: 'sys_alpha', is_system: true }), 'REAL'),
        );

        // Assert
        expect(result.current.allowed).toBe(true);
        expect(result.current.uncheckedReason).toBe('内置模板策略不吃门禁');
        expect(mockGetHealth).not.toHaveBeenCalled();
    });

    it('VERIFIED 策略进实盘 → 拦下并原样透出逐项理由与下一步', async () => {
        // Arrange
        mockGetHealth.mockResolvedValue(
            healthResponse('REAL', { allowed: false, mode: 'REAL', items: realItems, next_step: '先切到模拟盘启动一次，完成模拟验证后再进实盘' }),
        );

        // Act
        const { result } = renderHook(() => useStartEligibility(strategy(), 'REAL'));

        // Assert
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.allowed).toBe(false);
        expect(result.current.uncheckedReason).toBe('');
        expect(result.current.items[0].reason).toBe(
            '策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证',
        );
        expect(result.current.nextStep).toBe('先切到模拟盘启动一次，完成模拟验证后再进实盘');
    });

    it('按 mode 取对应那一档：REAL 请求带 mode=REAL——拿错档会给出伪资格', async () => {
        // Arrange：SIM 放行、REAL 拦截，两档结论相反
        mockGetHealth.mockImplementation(async (_id, _limit, mode) =>
            mode === 'REAL'
                ? healthResponse('REAL', { allowed: false, mode: 'REAL', items: realItems, next_step: '先切模拟' })
                : healthResponse('SIMULATION', { allowed: true, mode: 'SIMULATION', items: [], next_step: '' }),
        );

        // Act
        const { result } = renderHook(() => useStartEligibility(strategy(), 'REAL'));

        // Assert
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(mockGetHealth).toHaveBeenCalledWith('42', 1, 'REAL');
        expect(result.current.allowed).toBe(false);
    });

    it('接口读失败 → 不拦（真正的闸门在执行点，点启动仍会给逐项 400）', async () => {
        // Arrange
        mockGetHealth.mockRejectedValue(new Error('评估接口失败 500'));

        // Act
        const { result } = renderHook(() => useStartEligibility(strategy(), 'REAL'));

        // Assert
        await waitFor(() => expect(result.current.uncheckedReason).toContain('读取失败'));
        expect(result.current.allowed).toBe(true);
        expect(result.current.items).toEqual([]);
    });

    it('响应缺该模式的资格清单 → 不拦，说明「未返回」而不是假装通过', async () => {
        // Arrange
        mockGetHealth.mockResolvedValue(healthResponse('REAL', undefined));

        // Act
        const { result } = renderHook(() => useStartEligibility(strategy(), 'REAL'));

        // Assert
        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.allowed).toBe(true);
        expect(result.current.uncheckedReason).toBe('接口未返回该模式的资格清单');
    });

    it('enabled=false 直接不校验（运行中/停用态没必要提示）', () => {
        // Arrange & Act
        const { result } = renderHook(() => useStartEligibility(strategy(), 'REAL', false));

        // Assert
        expect(result.current.allowed).toBe(true);
        expect(result.current.uncheckedReason).toBe('当前模式不校验启动资格');
        expect(mockGetHealth).not.toHaveBeenCalled();
    });

    it('换策略后重拉：不能把上一个策略的结论留在屏幕上', async () => {
        // Arrange：第一个策略有清单，第二个没有
        mockGetHealth.mockResolvedValueOnce(
            healthResponse('REAL', { allowed: false, mode: 'REAL', items: realItems, next_step: '先切模拟' }),
        );
        mockGetHealth.mockResolvedValueOnce(
            healthResponse('REAL', { allowed: true, mode: 'REAL', items: [], next_step: '' }),
        );

        const { result, rerender } = renderHook(
            ({ id }: { id: string }) => useStartEligibility(strategy({ id }), 'REAL'),
            { initialProps: { id: '42' } },
        );
        await waitFor(() => expect(result.current.allowed).toBe(false));

        // Act
        rerender({ id: '43' });

        // Assert
        await waitFor(() => expect(result.current.allowed).toBe(true));
        expect(result.current.items).toEqual([]);
        expect(mockGetHealth).toHaveBeenLastCalledWith('43', 1, 'REAL');
    });
});
