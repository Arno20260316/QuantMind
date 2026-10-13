/**
 * 启动门禁拒绝弹窗（「失败后」面）——常驻说明，不是 toast。
 *
 * 为什么不走端到端：`CommandBar` 的启动按钮在资格条已经判死时是 `disabled` 的
 * （`isDeployDisabled = !selectedStrategyId || !gateAllowed`），点不动 → 走不到
 * `/start` 的 400。所以本弹窗只在「预检没拦住、服务端 400 打回」那条路上出现。
 * 浏览器的预检那半段由 `useStartEligibility` 的单测 + 实测覆盖；
 * 这里用合成信封把弹窗自己的那一面钉住（逐项 ✗ / 闸门归属 / 唯一入口 / 工单号纪律）。
 */

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// antd Modal 走 portal 挂到 document.body，RTL 的 `container` 里是空的 —— 文案断言一律取整棵文档树。
// 注意 `screen` 不带 `baseElement`（本版 @testing-library/dom），直接读 document.body。
const bodyText = () => document.body.textContent || '';

import StartGateFailedModal from '../StartGateFailedModal';
import type { StartGateFailure } from '../../../services/realTradingService';

/** 真实探针形状：VERIFIED 策略进实盘，被生命周期状态机拦下。 */
const lifecycleFailure = (): StartGateFailure => ({
    message: '策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证',
    gate: 'lifecycle',
    mode: 'REAL',
    items: [
        {
            key: 'lifecycle',
            label: '生命周期状态',
            value: 'VERIFIED',
            passed: false,
            reason: '策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证',
            next_step: '先切到模拟盘启动一次，完成模拟验证后再进实盘',
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
    next_step: '先切到模拟盘启动一次，完成模拟验证后再进实盘',
});

describe('StartGateFailedModal', () => {
    it('渲染逐项未通过理由与下一步，已通过项另列', () => {
        // Arrange & Act
        render(<StartGateFailedModal failure={lifecycleFailure()} onClose={vi.fn()} />);

        // Assert
        const failed = screen.getByTestId('start-gate-failed-items');
        expect(failed.textContent).toContain('生命周期状态');
        expect(failed.textContent).toContain('VERIFIED');
        expect(failed.textContent).toContain('REAL 须先经模拟盘（SIM）验证');

        const passed = screen.getByTestId('start-gate-passed-items');
        expect(passed.textContent).toContain('回测体检');
        expect(passed.textContent).toContain('A · 可信度 88/100');
        // 已通过项不得混进未通过清单
        expect(failed.textContent).not.toContain('回测体检');
    });

    it('说明是哪一个闸门拦的，并给出唯一入口', () => {
        // Arrange & Act
        render(<StartGateFailedModal failure={lifecycleFailure()} onClose={vi.fn()} />);

        // Assert
        expect(bodyText()).toContain('生命周期状态机');
        expect(bodyText()).toContain('唯一入口：');
        expect(bodyText()).toContain('先切到模拟盘启动一次');
        expect(bodyText()).toContain('启动未通过：实盘');
    });

    it('晋级门禁拦下时说「回测体检晋级门禁」，与生命周期状态机分开', () => {
        // Arrange
        const failure: StartGateFailure = {
            ...lifecycleFailure(),
            gate: 'promotion',
            message: '回测体检结论 E：证据不足，不得晋级实盘',
        };

        // Act
        render(<StartGateFailedModal failure={failure} onClose={vi.fn()} />);

        // Assert
        expect(bodyText()).toContain('回测体检晋级门禁');
        expect(bodyText()).not.toContain('生命周期状态机');
    });

    it('后端没给逐项清单时不假装有项，只留原话', () => {
        // Arrange
        const failure: StartGateFailure = {
            message: '当前策略不满足启动条件',
            gate: '',
            mode: 'REAL',
            items: [],
            next_step: '',
        };

        // Act
        render(<StartGateFailedModal failure={failure} onClose={vi.fn()} />);

        // Assert
        expect(screen.queryByTestId('start-gate-failed-items')).toBeNull();
        expect(bodyText()).toContain('后端未返回逐项拆解');
    });

    it('failure 为空 → 整块不渲染（不残留空弹窗）', () => {
        // Arrange & Act
        render(<StartGateFailedModal failure={null} onClose={vi.fn()} />);

        // Assert
        // 断在 baseElement 上：portal 里的东西也在这棵树上，若真残留空弹窗这里非空。
        expect(document.querySelector('.ant-modal')).toBeNull();
        expect(screen.queryByText(/启动未通过/)).toBeNull();
        expect(bodyText()).toBe('');
    });

    it('面向用户文案不含内部工单号（T-Pnn 一律不出现在界面）', () => {
        // Arrange & Act
        render(<StartGateFailedModal failure={lifecycleFailure()} onClose={vi.fn()} />);

        // Assert
        // 先证明弹窗文案确实在扫描范围内，否则这条断言会因「扫了个空串」而假通过。
        expect(bodyText()).toContain('生命周期状态机');
        expect(bodyText()).not.toMatch(/T-P\d/);
    });
});
