/**
 * 「已完成」feed 日期与信息完整性（2026-10-15）的渲染闸门：
 * - A股卡片角日期从「只有平仓日」改「买入日 → 平仓日」区间（后端 /live/closed
 *   倒走归因出 entry_date；缺失退化为单日、持仓时长「—」，绝不臆造）；
 * - 持仓时长小时位取小数天尾数（1.84 曾显示成 '1.84D 0H'，信息不完整）；
 * - 时刻列不再裸出「T」（09-24T14:41 → 09-24 14:41，港股卖出卡同口径）。
 * 字号/字重提升在 qm-arena-overrides.css（视觉层，浏览器探针管，本测试不管）。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import CompletedFeed from '../arena/components/CompletedFeed';
import type { LiveClosedRow, LiveTradeLog } from '../arena/api/client';
import {
  fetchFutuClosed,
  fetchFutuTrades,
  fetchLiveClosed,
} from '../arena/api/client';

vi.mock('../arena/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../arena/api/client')>();
  return {
    ...actual,
    fetchLiveClosed: vi.fn().mockResolvedValue([]),
    fetchTradeDetail: vi.fn().mockResolvedValue([]),
    fetchFutuClosed: vi.fn().mockResolvedValue([]),
    fetchFutuTrades: vi.fn().mockResolvedValue([]),
  };
});

const closedRow = (over: Partial<LiveClosedRow>): LiveClosedRow => ({
  symbol: '002074.SZ',
  exit_date: '2026-09-24',
  qty: 100,
  entry_price: 20,
  exit_price: 22,
  notional: 2200,
  fee: 0,
  pnl: 200,
  hold_days: 3.02,
  live: true,
  ts: '2026-09-24T14:41:00+08:00',
  agent: 'alpha',
  ...over,
});

describe('「已完成」日期与持仓时长（A股实盘口径）', () => {
  it('角日期画「买入日 → 平仓日」区间；持仓时长小时位取尾数', async () => {
    vi.mocked(fetchLiveClosed).mockResolvedValue([
      closedRow({ symbol: '002074.SZ', entry_date: '2026-09-21', hold_days: 3.02 }),
      closedRow({
        symbol: '600036.SH',
        exit_date: '2026-09-23',
        entry_date: '2026-09-22',
        hold_days: 1.84,
      }),
    ]);
    render(
      <CompletedFeed agents={['alpha']} market="cn" currency="¥" stockNames={{}} />,
    );

    expect(await screen.findByText('09-21 → 09-24')).toBeInTheDocument();
    expect(screen.getByText('09-22 → 09-23')).toBeInTheDocument();
    expect(screen.getByText('3D 0H')).toBeInTheDocument();
    expect(screen.getByText('1D 20H')).toBeInTheDocument();
  });

  it('entry_date 缺失：只显示平仓日、持仓时长「—」，不臆造', async () => {
    vi.mocked(fetchLiveClosed).mockResolvedValue([
      closedRow({ entry_date: null, hold_days: null }),
    ]);
    render(
      <CompletedFeed agents={['alpha']} market="cn" currency="¥" stockNames={{}} />,
    );

    expect(await screen.findByText('09-24')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});

describe('「已完成」港股卖出卡时刻', () => {
  it('时刻列不再裸出 T（截断后空格分隔）', async () => {
    vi.mocked(fetchFutuClosed).mockResolvedValue([]);
    vi.mocked(fetchFutuTrades).mockResolvedValue([
      {
        ts: '2026-09-24T14:41:38.123456+08:00',
        mode: 'execute',
        code: 'HK.00700',
        name: '腾讯控股',
        volume: 100,
        price: 380,
        side: 'SELL',
      } as LiveTradeLog,
    ]);
    render(
      <CompletedFeed agents={['alpha']} market="hk" currency="HK$" stockNames={{}} />,
    );

    expect(await screen.findByText('09-24 14:41')).toBeInTheDocument();
  });
});
