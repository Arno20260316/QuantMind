/**
 * 机会雷达面板（HkOpportunityPanel）单测。
 *
 * 钉住的口径纪律（展示面用词 + 缺失语义）：
 * - 柱缺失显示 —（无数据），绝不显示成 0；综合分照常展示（分数与缺柱解耦）
 * - 面板是研究评分卡：不出现买入/卖出执行词、不写「前 N%」
 * - 行点击 → onPick(symbol)；行业榜点行 → 个股榜按该行业过滤（后端 sector 参数）
 * - 口径条如实披露候选池门槛与非预测定性
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { HkOpportunityPanel } from '../components/HkOpportunityPanel';
import {
  getOpportunityStocks,
  getOpportunitySectors,
  getOpportunitySummary,
  getRiskWatch,
} from '../services/api';
import type {
  HkOpportunityStock,
  HkOpportunityStocks,
  HkOpportunitySummary,
} from '../types';

vi.mock('../services/api', () => ({
  getOpportunityStocks: vi.fn(),
  getOpportunitySectors: vi.fn(),
  getOpportunitySummary: vi.fn(),
  getRiskWatch: vi.fn(),
}));

const STOCK: HkOpportunityStock = {
  symbol: '0700.HK',
  name: '腾讯控股',
  sector: '资讯科技业',
  opp_score: 91.3,
  pct_change: 1.1,
  amount_yi: 88.2,
  pillars: { mom: 88, trend: 75, south: null, ccass: 60, liq: 95, risk: 40 },
  pillars_covered: 5,
  tags: ['动量居前'],
  sb_chg_d5_pp: 0.12,
  rank_in_sector: 1,
  sector_size: 42,
};

function stocksResponse(items: HkOpportunityStock[]): HkOpportunityStocks {
  return { trade_date: '2026-10-14', items, universe: { eligible: 612, scored: 598 } };
}

const SUMMARY: HkOpportunitySummary = {
  available: true,
  trade_date: '2026-10-14',
  pct_base: '2026-10-14',
  thresholds: { min_price_hkd: 0.5, min_amount_hkd: 5e7, min_pillars: 4 },
  pillar_weights: { mom: 0.2, trend: 0.1, south: 0.15, ccass: 0.2, liq: 0.25, risk: 0.1 },
  pillar_coverage: {},
  listed_total: 2600,
  eligible_total: 612,
  factors: { l1: ['mom_ret_60d'], south: ['sb_pct_d5'], ccass: ['ca_top10_pct'] },
  excluded_factors: { ca_broker_pct: 'U 形' },
  data_as_of: {
    l1_factors: '2026-10-14',
    south_factors: '2026-10-14',
    ccass_factors: '2026-10-13',
    quote: '2026-10-14',
  },
  disclaimer: '研究评分，非投资建议',
};

const mockedStocks = vi.mocked(getOpportunityStocks);
const mockedSectors = vi.mocked(getOpportunitySectors);
const mockedSummary = vi.mocked(getOpportunitySummary);
const mockedRisk = vi.mocked(getRiskWatch);

beforeEach(() => {
  vi.clearAllMocks();
  mockedStocks.mockResolvedValue(stocksResponse([STOCK]));
  mockedSectors.mockResolvedValue({
    trade_date: '2026-10-14',
    items: [
      {
        name: '资讯科技业',
        opp_score_median: 82.4,
        opportunity_breadth: 0.4,
        stock_count: 12,
        south_chg_d5_median_pp: 0.1,
        top_stocks: [{ symbol: '0700.HK', name: '腾讯控股', opp_score: 91.3, pct_change: 1.1 }],
      },
    ],
  });
  mockedSummary.mockResolvedValue(SUMMARY);
  mockedRisk.mockResolvedValue({
    trade_date: '2026-10-14',
    items: [
      {
        symbol: '9988.HK',
        name: '阿里巴巴-W',
        sector: '非必需性消费',
        risk_score: 71.2,
        pct_change: -1.8,
        amount_yi: 55.0,
        reasons: ['近5日弱势', '南向减持'],
      },
    ],
  });
});

describe('HkOpportunityPanel · 六柱缺失语义', () => {
  it('缺失柱 title 显示 —（无数据），有值柱显示分值', async () => {
    const { container } = render(<HkOpportunityPanel />);
    await screen.findByTitle('打开 0700.HK 个股终端');

    // 南向柱缺失（null）→ 灰槽 + —（无数据），绝不显示成 0
    expect(container.querySelector('[title="南向: —（无数据）"]')).not.toBeNull();
    // 有值柱照常
    expect(container.querySelector('[title="动量: 88"]')).not.toBeNull();
    expect(container.querySelector('[title="流动性: 95"]')).not.toBeNull();
    // 缺柱不影响综合分展示（91.3 一位小数）
    expect(screen.getByText('91.3')).toBeInTheDocument();
  });

  it('口径条如实披露：非模型预测定性 + 候选池门槛 + 数据日期', async () => {
    render(<HkOpportunityPanel />);

    expect(await screen.findByText(/研究评分卡（非模型预测、非交易指令）/)).toBeInTheDocument();
    expect(screen.getByText(/候选池 612\/2600 只/)).toBeInTheDocument();
    expect(screen.getByText('数据 2026-10-14')).toBeInTheDocument();
  });

  it('展示面不得出现执行面用词与百分比阈值标签', async () => {
    const { container } = render(<HkOpportunityPanel />);
    await screen.findByTitle('打开 0700.HK 个股终端');

    const text = container.textContent || '';
    expect(text).not.toMatch(/[买卖][入出]/);
    expect(text).not.toMatch(/[前后]\s*\d+\s*%/);
  });
});

describe('HkOpportunityPanel · 点击接线', () => {
  it('点个股行 → onPick(symbol)', async () => {
    const onPick = vi.fn();
    render(<HkOpportunityPanel onPick={onPick} />);
    const row = await screen.findByTitle('打开 0700.HK 个股终端');

    // Act
    fireEvent.click(row);

    // Assert
    expect(onPick).toHaveBeenCalledWith('0700.HK');
  });

  it('点行业榜行 → 个股榜按该行业过滤（带 sector 参数重新请求）', async () => {
    render(<HkOpportunityPanel />);
    await screen.findByTitle('打开 0700.HK 个股终端');

    // Act
    fireEvent.click(screen.getByTitle('只看「资讯科技业」的个股机会榜'));

    // Assert
    await waitFor(() => expect(mockedStocks).toHaveBeenCalledWith(30, '资讯科技业'));
    expect(await screen.findByTitle('清除行业过滤')).toBeInTheDocument();
  });

  it('行业榜龙头 chips 点击 → onPick(symbol) 且不触发行业过滤', async () => {
    const onPick = vi.fn();
    render(<HkOpportunityPanel onPick={onPick} />);
    await screen.findByTitle('打开 0700.HK 个股终端');

    // Act：龙头 chip（title 带「打开个股终端」）
    fireEvent.click(screen.getByTitle(/0700\.HK · 综合分 91\.3 · 打开个股终端/));

    // Assert
    expect(onPick).toHaveBeenCalledWith('0700.HK');
    expect(mockedStocks).toHaveBeenCalledTimes(1); // 只有首次加载，未被过滤请求覆盖
  });

  it('风险警示榜行 → onPick(symbol)，reasons 为事实短语', async () => {
    const onPick = vi.fn();
    render(<HkOpportunityPanel onPick={onPick} />);

    // Act
    fireEvent.click(await screen.findByText('阿里巴巴-W'));

    // Assert
    expect(onPick).toHaveBeenCalledWith('9988.HK');
    expect(screen.getByText('近5日弱势')).toBeInTheDocument();
    expect(screen.getByText('南向减持')).toBeInTheDocument();
  });
});
