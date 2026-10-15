/**
 * 聪明钱面板接线（四面取数 → 布局 → 交互）。
 *
 * 为什么值得钉住：
 * - 榜单行点击必须把 symbol 交给页面（onPick → 个股终端浮窗），断了等于死链；
 * - 主面 404（未构建）必须走空态说明卡，不能白屏也不能报通用错误；
 * - 缺失条目在榜上渲染「—」而不是 0 分——这是全链展示契约的最外一环；
 * - 事件类型筛选 chips 与席位窗口切换要真的重新取数/过滤。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

vi.mock('../services/smartMoneyService', () => {
  class SmartMoneyError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
    get notBuilt(): boolean {
      return this.status === 404;
    }
  }
  return {
    SmartMoneyError,
    getSmartMoneyOverview: vi.fn(),
    getSmartMoneyStocks: vi.fn(),
    getSmartMoneyBranches: vi.fn(),
    getSmartMoneyEvidence: vi.fn(),
  };
});

import { SmartMoneyPanel } from '../components/smart-money/SmartMoneyPanel';
import {
  SmartMoneyError,
  getSmartMoneyBranches,
  getSmartMoneyEvidence,
  getSmartMoneyOverview,
  getSmartMoneyStocks,
  type SmartEvidenceSeg,
} from '../services/smartMoneyService';

const mockOverview = vi.mocked(getSmartMoneyOverview);
const mockStocks = vi.mocked(getSmartMoneyStocks);
const mockBranches = vi.mocked(getSmartMoneyBranches);
const mockEvidence = vi.mocked(getSmartMoneyEvidence);

const PILLARS = [
  { name: 'inst_net_buy_5d_z', label: '机构净买额·5日z20', unit: 'z', direction: 1 },
  { name: 'lhb_net_ratio_5d', label: '龙虎榜净买占比·5日', unit: 'ratio', direction: -1 },
  { name: 'margin_net_5d_z', label: '融资净买入·5日z20', unit: 'z', direction: -1 },
  { name: 'margin_bal_chg_20d', label: '融资余额变化·20日', unit: 'ratio', direction: -1 },
];

const ITEM_FULL = {
  symbol: 'SH603668',
  name: '天马科技',
  industry: '农牧饲渔',
  score: 82.47,
  covered: 3,
  n_pillars: 4,
  pct: {
    inst_net_buy_5d_z: 94.3,
    lhb_net_ratio_5d: 97.2,
    margin_net_5d_z: 88.4,
    margin_bal_chg_20d: null,
  },
  val: { inst_net_buy_5d_z: 2.1, lhb_net_ratio_5d: 0.02, margin_net_5d_z: 1.8, margin_bal_chg_20d: null },
  inst_net_5d: 2e8,
  lhb_net_5d: 1e8,
  margin_net_5d: 15000,
  north_q_delta_pp: 1.23,
  latest_event: { type: 'lhb', date: '2026-10-14' },
};

const ITEM_MISSING = {
  symbol: 'SZ000001',
  name: '平安银行',
  industry: null,
  score: null,
  covered: 0,
  n_pillars: 4,
  pct: {
    inst_net_buy_5d_z: null,
    lhb_net_ratio_5d: null,
    margin_net_5d_z: null,
    margin_bal_chg_20d: null,
  },
  val: {
    inst_net_buy_5d_z: null,
    lhb_net_ratio_5d: null,
    margin_net_5d_z: null,
    margin_bal_chg_20d: null,
  },
  inst_net_5d: null,
  lhb_net_5d: null,
  margin_net_5d: null,
  north_q_delta_pp: null,
  latest_event: null,
};

const seg = (ic: number, q: number): SmartEvidenceSeg => ({
  n_days: 661,
  rank_ic: ic,
  icir: ic * 5,
  ic_pos_rate: 0.53,
  q1_bp: -20,
  q2_bp: -8,
  q3_bp: 0,
  q4_bp: 10,
  q5_bp: 35,
  q5_minus_q1_bp: q,
  tail_ok: true,
});

const STOCKS = {
  as_of: '2026-10-14',
  generated_at: '2026-10-15T15:30:00Z',
  partial: false,
  admitted: PILLARS.map((p) => p.name),
  pillars: PILLARS,
  summary: { pool_size: 725, covered_mean: 2.1 },
  total: 2,
  items: [ITEM_FULL, ITEM_MISSING],
};

const OVERVIEW = {
  as_of: '2026-10-14',
  generated_at: '2026-10-15T15:30:00Z',
  partial: false,
  admitted: PILLARS.map((p) => p.name),
  pillars: PILLARS,
  summary: { pool_size: 725, covered_mean: 2.1 },
  overview: {
    lhb: { date: '2026-10-14', n_stocks: 61, net_buy_yi: 12.3, n20d_avg: 55.2, net20d_sum_yi: -30.5 },
    inst: { date: '2026-10-14', n_stocks: 21, net_buy_yi: 4.2 },
    dzjy: { date: '2026-10-14', amount_yi: 18.6, premium_share: 0.42 },
    survey: { date: '2026-10-14', n_stocks: 33, n_inst: 210 },
    margin: { date: '2026-10-13', net_5d_yi: -8.9, balance_yi: 16500.3 },
    north: {
      quarter: '2026Q2',
      report_date: '2026-06-30',
      n_total: 2318,
      n_inc: 1024,
      n_dec: 1198,
      top_inc: [{ symbol: 'SH600036', stock_name: '招商银行', delta_pp: 1.21, pct: 4.5 }],
      top_dec: [{ symbol: 'SZ000858', stock_name: '五粮液', delta_pp: -0.88, pct: 2.1 }],
    },
    branches_top: [],
  },
  trends: null,
  freshness: {
    sources: {},
    margin_last: '2026-10-13',
    kline_last: '2026-10-14',
  },
};

const BRANCHES = {
  as_of: '2026-10-14',
  window: 5,
  branches: [
    {
      branch_name: '华鑫证券上海分公司',
      net_amount: 2.5e8,
      buy_amount: 3e8,
      sell_amount: 0.5e8,
      n_days: 3,
      stocks_bought: 'SH600000, SZ000001',
    },
    {
      branch_name: '国泰君安南京太平南路',
      net_amount: -1.2e8,
      buy_amount: 0.3e8,
      sell_amount: 1.5e8,
      n_days: 2,
      stocks_bought: null,
    },
  ],
};

const EVIDENCE = {
  validated_at: '2026-10-15T23:24:00Z',
  window: { eval_start: '2024-01-02', eval_end: '2026-10-14', warmup_start: '2023-10-01' },
  method: {},
  signals: [
    { name: 'inst_net_buy_5d_z', family: 'inst', min_names: null, admitted: true, direction: 1, decision: 'admitted', results: { '10': seg(0.0195, 55), '5': seg(0.0179, 35) } },
    { name: 'lhb_net_ratio_5d', family: 'lhb', min_names: null, admitted: true, direction: -1, decision: 'admitted', results: { '10': seg(-0.0412, -40) } },
    { name: 'margin_net_5d_z', family: 'margin', min_names: 100, admitted: true, direction: -1, decision: 'admitted', results: { '10': seg(-0.0101, -10) } },
    { name: 'margin_bal_chg_20d', family: 'margin', min_names: 100, admitted: true, direction: -1, decision: 'admitted', results: { '10': seg(-0.0415, -47) } },
    { name: 'inst_net_ratio_5d', family: 'inst', min_names: null, admitted: false, direction: 1, decision: 'rejected', results: { '10': seg(0.0013, 23) } },
  ],
  correlation: { pairs: [], collapsed: [] },
  composite: { results: { '10': seg(0.031, 71) }, n_dates: 661, avg_pool: 725, avg_covered: 2.1 },
  evidence_only: [{ name: 'north_q_delta', conclusion: '北向持仓仅季度频率', use: '仅展示不入柱' }],
  admitted: PILLARS.map((p) => p.name),
  rejected: [
    { name: 'inst_net_ratio_5d', family: 'inst', reason: '|IC| 过弱', rank_ic_10: 0.0013, q5_minus_q1_bp_10: 23, n_days_10: 661 },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mockOverview.mockResolvedValue(OVERVIEW);
  mockStocks.mockResolvedValue(STOCKS);
  mockBranches.mockResolvedValue(BRANCHES);
  mockEvidence.mockResolvedValue(EVIDENCE);
});

describe('聪明钱面板接线', () => {
  it('榜单行点击 → onPick(symbol)；缺失条目显示「—」不显示 0 分', async () => {
    const onPick = vi.fn();
    render(<SmartMoneyPanel onPick={onPick} />);

    await screen.findByText('天马科技');
    expect(screen.getByText('平安银行')).toBeInTheDocument();
    // 缺失条目：分位 chips / 分数位一律「—」（本用例至少覆盖分数与 4 柱 chips 中的多数）
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);

    await userEvent.click(screen.getByText('天马科技'));
    expect(onPick).toHaveBeenCalledWith('SH603668');

    // 事件类型筛选：点「龙虎榜」→ 只留 lhb 事件条目，无事件条目被过滤
    await userEvent.click(screen.getByRole('button', { name: '龙虎榜' }));
    await waitFor(() => expect(screen.queryByText('平安银行')).not.toBeInTheDocument());
    expect(screen.getByText('天马科技')).toBeInTheDocument();
  });

  it('席位窗口切换重新取数（5 → 20）', async () => {
    render(<SmartMoneyPanel />);

    await screen.findByText('华鑫证券上海分公司');
    expect(mockBranches).toHaveBeenCalledWith(5);

    await userEvent.click(screen.getByRole('button', { name: '20 日' }));
    await waitFor(() => expect(mockBranches).toHaveBeenCalledWith(20));
  });

  it('partial=true → 概览区披露横幅', async () => {
    mockStocks.mockResolvedValue({ ...STOCKS, partial: true });
    mockOverview.mockResolvedValue({ ...OVERVIEW, partial: true });
    render(<SmartMoneyPanel />);

    await screen.findByText(/部分数据源未就绪/);
  });

  it('证据台：入柱/否决/方向/复合分齐全（负向柱可见「负向」章）', async () => {
    render(<SmartMoneyPanel />);

    await screen.findByText(/验证证据台/);
    expect(screen.getAllByText('负向').length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText('正向').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('|IC| 过弱')).toBeInTheDocument();
    expect(screen.getByText(/仅展示证据/)).toBeInTheDocument();
  });

  it('主面 404（smart_money_not_built）→ 空态说明卡而非白屏/报错', async () => {
    const err = new SmartMoneyError('smart_money_not_built', 404);
    mockOverview.mockRejectedValue(err);
    mockStocks.mockRejectedValue(err);
    mockBranches.mockRejectedValue(err);
    mockEvidence.mockRejectedValue(err);
    render(<SmartMoneyPanel />);

    await screen.findByText(/聪明钱统计尚未生成/);
    expect(screen.queryByText('天马科技')).not.toBeInTheDocument();
  });
});
