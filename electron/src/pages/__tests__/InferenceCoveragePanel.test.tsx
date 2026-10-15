/**
 * 推理覆盖面板的「市场日历」口径。
 *
 * 为什么值得钉住：面板曾经把交易日历硬编码成 'SSE'。对美股/期货/加密模型，
 * 拿上交所日历判覆盖会把感恩节、圣诞、CME 假日标成「待补缺口」——补全按钮
 * 于是永远可按、缺口数永远不归零，用户看见的是一张永远补不完的日历。
 * 现在日历随 `marketConfig.calendar` 传入，三条行为必须同时成立：
 *   1) 传 NYSE/CME 等日历 → 批量判日走该日历，绝不回落 SSE；
 *   2) 不传 → 默认 SSE（兼容旧调用点）；
 *   3) '24/7'（加密，无休市）→ 根本不打后端（后端也答不了 24/7），本地全开。
 * 断言落在用户看得见的效果上：全休市日历下所有历史日期都是「已覆盖」态，
 * 而 NYSE（后端地图为空时）一个覆盖格都没有——两条路径的网格必须可区分。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

vi.mock('../../services/modelTrainingService', () => ({
  modelTrainingService: {
    getInferenceCoverage: vi.fn(),
    batchCheckTradingDays: vi.fn(),
    getBackfillStatus: vi.fn(),
    triggerInferenceBackfill: vi.fn(),
  },
}));

import { InferenceCoveragePanel } from '../InferenceCoveragePanel';
import { modelTrainingService } from '../../services/modelTrainingService';

const getInferenceCoverage = vi.mocked(modelTrainingService.getInferenceCoverage);
const batchCheckTradingDays = vi.mocked(modelTrainingService.batchCheckTradingDays);

/** 固定到 2026-03-11（周三）：月窗口 = 2026-01 / 02 / 03（31+28+31=90 天） */
const COVERAGE = {
  min_date: '2026-01-05',
  max_date: '2026-03-11',
  count: 48,
  gap_dates: [] as string[],
  latest_trade_date: '2026-03-11',
  is_up_to_date: true,
};

beforeEach(() => {
  // 同一文件串行复用 mock：不清调用史，上一用例的 NYSE/SSE 调用会漏进本用例断言
  vi.clearAllMocks();
  localStorage.clear();
  getInferenceCoverage.mockResolvedValue(COVERAGE);
  batchCheckTradingDays.mockResolvedValue({});
  vi.mocked(modelTrainingService.getBackfillStatus).mockResolvedValue({ status: 'idle' });
  vi.mocked(modelTrainingService.triggerInferenceBackfill).mockResolvedValue({});
});

describe('InferenceCoveragePanel 日历口径', () => {
  it('NYSE：批量判日走 NYSE，绝不回落 SSE；文案如实显示 NYSE 日历', async () => {
    const { container } = render(<InferenceCoveragePanel modelId="mld_us" calendar="NYSE" />);

    await waitFor(() => expect(batchCheckTradingDays).toHaveBeenCalled());
    for (const [market] of batchCheckTradingDays.mock.calls) {
      expect(market).toBe('NYSE');
    }
    // 覆盖数据就绪后的最后一轮：窗口是覆盖数据锚定的 2026-01~03（90 天）
    const lastDates = batchCheckTradingDays.mock.calls.at(-1)![1];
    expect(lastDates).toHaveLength(90);
    expect(lastDates).toContain('2026-01-05');

    await screen.findByText('共 48 个交易日');
    // 后端地图为空（本用例 mock 返回 {}）→ 没有任何一天显示为已覆盖：
    // 证明网格确实吃日历结果，不是「无论如何都画绿」
    expect(container.querySelectorAll('div[class*="bg-emerald-500/30"]')).toHaveLength(0);

    await userEvent.click(screen.getByText('口径说明'));
    expect((await screen.findAllByText(/NYSE 日历/)).length).toBeGreaterThan(0);
  });

  it('不传日历：默认 SSE（兼容旧调用点）', async () => {
    render(<InferenceCoveragePanel modelId="mld_cn" />);

    await waitFor(() => expect(batchCheckTradingDays).toHaveBeenCalled());
    for (const [market] of batchCheckTradingDays.mock.calls) {
      expect(market).toBe('SSE');
    }
  });

  it('24/7：不打后端日历，本地全开——周末/假日也记为交易日（历史日期全「已覆盖」）', async () => {
    const { container } = render(<InferenceCoveragePanel modelId="mld_crypto" calendar="24/7" />);

    await screen.findByText('共 48 个交易日');
    // 2026-01-01 ~ 2026-03-11 连续 70 天全部为已覆盖（含周日 03-08）
    await waitFor(() => {
      expect(container.querySelectorAll('div[class*="bg-emerald-500/30"]')).toHaveLength(70);
    });
    expect(batchCheckTradingDays).not.toHaveBeenCalled();

    await userEvent.click(screen.getByText('口径说明'));
    expect((await screen.findAllByText(/24\/7 无休市/)).length).toBeGreaterThan(0);
  });
});
