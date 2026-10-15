/**
 * 「相对基准超额」页签 —— 基准候选的市场分层（2026-10-16）。
 *
 * 候选基准曾经是写死的 A 股三码。分层之后候选**由后端 `excess_block.benchmarks[]`
 * 按市场下发**（含可用性与缺数原因），前端只负责两件事，且两件都怕静默错：
 *
 * 1. 不拿 A 股代码去填别的市场 —— 港股/美股页面上出现「沪深300」候选，
 *    点下去要么 422、要么后端顺位回退到别的指数而页面显示点了沪深300；
 * 2. chips 顺序必须稳（= 未指名那次的响应顺序）。后端把点名者排到 `benchmarks[]`
 *    首位，照抄就会「点一下跳一下」，用户点第二个时它跑到第一位去。
 *
 * 期货无指数基准：后端整块 `available:false` + 原因，候选条不渲染。
 * 兜底只留给 CN 旧快照（响应没带 benchmarks[]）。
 */
import React from 'react';
import { describe, test, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ExcessTab } from '../ExcessTab';
import type { FactorBlocks } from '../../../../types/factorReport';

// ECharts 在 jsdom 里没有画布：本文件测的是候选条与降级面，图本身换桩
vi.mock('../../../../../../components/common/EChartsChart', () => ({
  EChartsChart: () => <div data-testid="chart" />,
}));

const blocksWith = (excess_block: unknown): FactorBlocks =>
  ({ excess_block } as unknown as FactorBlocks);

/** 候选条上的按钮文本（DOM 顺序 = 后端下发的候选顺序） */
const chipTexts = (): string[] =>
  Array.from(document.querySelectorAll('[data-testid^="bench-chip-"]')).map(
    (el) => (el.textContent || '').replace('⚠', '').trim(),
  );

describe('ExcessTab：基准候选按市场', () => {
  test('港股：候选来自后端下发的本市场基准，绝不出现 A 股三码', () => {
    render(
      <ExcessTab
        market="HK"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: true,
          bench_symbol: 'HSI.HK',
          bench_name: '恒生指数',
          dates: [],
          benchmarks: [
            { symbol: 'HSI.HK', name: '恒生指数', available: true },
            { symbol: 'HSCEI.HK', name: '恒生国企指数', available: true },
          ],
        })}
      />,
    );

    expect(chipTexts()).toEqual(['恒生指数', '恒生国企指数']);
    expect(screen.queryByText('沪深300')).toBeNull();
    expect(screen.queryByText('中证500')).toBeNull();
  });

  test('点名的基准取不到：chips 仍按首见顺序，不乱跳；缺数原因摊开', () => {
    const pinnedFirst = {
      available: true,
      bench_symbol: 'HSI.HK',
      bench_name: '恒生指数',
      dates: [],
      // 后端把点名者（HSCEI）排到首位 —— 顺序不可信
      benchmarks: [
        { symbol: 'HSCEI.HK', name: '恒生国企指数', available: false, reason: '该指数日线缺失' },
        { symbol: 'HSI.HK', name: '恒生指数', available: true },
      ],
    };

    const { rerender } = render(
      <ExcessTab
        market="HK"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: true,
          bench_symbol: 'HSI.HK',
          bench_name: '恒生指数',
          dates: [],
          benchmarks: [
            { symbol: 'HSI.HK', name: '恒生指数', available: true },
            { symbol: 'HSCEI.HK', name: '恒生国企指数', available: false, reason: '该指数日线缺失' },
          ],
        })}
      />,
    );
    expect(chipTexts()).toEqual(['恒生指数', '恒生国企指数']);

    // 点名 HSCEI：响应顺序翻转（点名者在前），chips 顺序必须还是首见顺序
    rerender(
      <ExcessTab market="HK" bench="HSCEI.HK" onBench={vi.fn()} definitions={{}} blocks={blocksWith(pinnedFirst)} />,
    );
    expect(chipTexts()).toEqual(['恒生指数', '恒生国企指数']);
    // 点名 ≠ 实际：页面必须挑明这次用的是哪一个
    expect(screen.getByText(/本次没取到/)).toBeTruthy();
    expect(screen.getByText(/该指数日线缺失/)).toBeTruthy();
  });

  test('美股：同上，候选与口径文案都不带 A 股', () => {
    render(
      <ExcessTab
        market="US"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: true,
          bench_symbol: 'SPX.US',
          bench_name: '标普500',
          dates: [],
          benchmarks: [{ symbol: 'SPX.US', name: '标普500', available: true }],
        })}
      />,
    );

    expect(chipTexts()).toEqual(['标普500']);
    expect(screen.queryByText(/沪深300 →/)).toBeNull();
  });

  test('期货：整块降级（无指数基准），候选条不渲染、不拿 A 股充数', () => {
    render(
      <ExcessTab
        market="FUTURES"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: false,
          reason: '[FUTURES] 该市场暂无指数基准数据，相对基准超额不可用',
          benchmarks: [],
        })}
      />,
    );

    expect(screen.getByText('该项数据不可用')).toBeTruthy();
    expect(screen.getByText(/该市场暂无指数基准数据/)).toBeTruthy();
    expect(chipTexts()).toEqual([]);
    expect(screen.queryByText('沪深300')).toBeNull();
  });

  test('CN 旧快照（响应没带 benchmarks[]）：退到 A 股静态候选', () => {
    render(
      <ExcessTab
        market="CN"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: true,
          bench_symbol: '000300.SH',
          dates: [],
        })}
      />,
    );

    expect(chipTexts()).toEqual(['沪深300', '中证500', '中证1000']);
  });

  test('非 CN 旧快照（响应没带 benchmarks[]）：没有候选条（宁缺勿造）', () => {
    render(
      <ExcessTab
        market="HK"
        bench=""
        onBench={vi.fn()}
        definitions={{}}
        blocks={blocksWith({
          available: true,
          bench_symbol: 'HSI.HK',
          dates: [],
        })}
      />,
    );

    expect(chipTexts()).toEqual([]);
    expect(screen.queryByText('沪深300')).toBeNull();
    // 实际采用的基准仍要显示（它来自响应，不依赖候选表）
    expect(screen.getAllByText(/HSI\.HK/).length).toBeGreaterThan(0);
  });
});
