/**
 * 港股面板「个股点击弹窗」接线与 AH 溢价停更横幅单测。
 *
 * 钉住：
 * - RankRow 的可选 onClick 语义（有回调 = 可点按钮态；无回调 = 纯展示行，向后兼容）
 * - AH 溢价面板行点击 → onPick(h_symbol)
 * - 停更横幅是**动态判据**（距今 >10 自然日才出现）：源侧恢复更新后横幅自动消失，
 *   不做任何硬编码日期
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RankRow } from '../../market-analysis-shared/ui';
import { HkAhPremiumPanel } from '../components/HkAhPremiumPanel';
import { getAhPremium } from '../services/api';

vi.mock('../services/api', () => ({
  getAhPremium: vi.fn(),
}));

const DAY_MS = 24 * 3600 * 1000;

/** 距今天 N 天的 ISO 日期（动态生成，测试不随日历腐坏） */
function daysAgo(n: number): string {
  return new Date(Date.now() - n * DAY_MS).toISOString().slice(0, 10);
}

const mockedPremium = vi.mocked(getAhPremium);

function premiumResponse(tradeDate: string) {
  return {
    trade_date: tradeDate,
    premium: [
      {
        h_symbol: '0939.HK',
        h_name: '建设银行',
        a_symbol: '601939',
        a_close: 7.8,
        h_close: 6.2,
        fx_hkd_cny: 0.92,
        premium_pct: 24.5,
      },
    ],
    discount: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedPremium.mockResolvedValue(premiumResponse(daysAgo(1)));
});

describe('RankRow · 可选 onClick（跨市场后向兼容）', () => {
  it('传入 onClick 时行是可点按钮态，点击触发回调', () => {
    const onClick = vi.fn();
    render(<RankRow rank={1} name="腾讯控股" main={null} onClick={onClick} title="打开 0700.HK" />);

    // Act
    fireEvent.click(screen.getByTitle('打开 0700.HK'));

    // Assert
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('不传 onClick 时无按钮语义（既有调用方零破坏）', () => {
    render(<RankRow rank={1} name="汇丰控股" main={null} />);

    expect(screen.queryByRole('button')).toBeNull();
    // 渲染本身不抛错即通过（点击无 handler）
    fireEvent.click(screen.getByText('汇丰控股'));
  });
});

describe('HkAhPremiumPanel · 行点击与停更横幅', () => {
  it('点溢价行 → onPick(h_symbol)', async () => {
    const onPick = vi.fn();
    render(<HkAhPremiumPanel onPick={onPick} />);

    // Act
    fireEvent.click(await screen.findByTitle('打开 0939.HK 个股终端'));

    // Assert
    expect(onPick).toHaveBeenCalledWith('0939.HK');
  });

  it('数据停更（距今 >10 天）显示琥珀横幅且带实际日期', async () => {
    const staleDate = daysAgo(40);
    mockedPremium.mockResolvedValue(premiumResponse(staleDate));
    render(<HkAhPremiumPanel />);

    // Assert（横幅内直接写明最新数据日期，不硬编码）
    expect(await screen.findByText(/该数据源已停止更新/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`最新数据 ${staleDate}`))).toBeInTheDocument();
    expect(screen.getByText(/不可作为当日比价依据/)).toBeInTheDocument();
  });

  it('数据新鲜（T-1）不显示停更横幅', async () => {
    render(<HkAhPremiumPanel />);
    await screen.findByText('建设银行');

    expect(screen.queryByText(/该数据源已停止更新/)).toBeNull();
  });

  it('数据获取失败降级为空提示，不抛错', async () => {
    mockedPremium.mockRejectedValue(new Error('boom'));
    render(<HkAhPremiumPanel />);

    // Assert
    await waitFor(() => expect(screen.getByText('AH 溢价数据不可用')).toBeInTheDocument());
  });
});
