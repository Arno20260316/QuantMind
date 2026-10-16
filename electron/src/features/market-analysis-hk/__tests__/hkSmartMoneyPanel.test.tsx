/**
 * 港股聪明钱面板接线单测 v2.1（细案 §4/§5 验收）。
 *
 * 钉住：
 * - 404（产物未构建）→ 全局 notBuilt 空态 + 重新加载，不白屏不伪装；
 * - 统一大框五面：默认面 = 机构优势（席位榜+类别优势+散点），role=tab/tabpanel；
 * - 懒取数纪律：types/consensus/evidence/persistence 切到该面才请求（初始零请求），
 *   切换后 echarts 图数达标（面 1 ≥ 2 图、面 2 ≥ 2 图）；
 * - evidence-only 真裁定渲染：概览带徽标常显、共识榜「不发布」空态、证据台警告；
 * - 单面失败独立结算：types 面失败出错误条（概览照常）；席位榜失败绝不伪装「无席位」；
 * - 席位行点击 → 席位档案抽屉取数；头部输入框 → 个股档案抽屉（代码原样透传）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-mock" />,
}));

vi.mock('../services/api', () => {
  class HkSmartMoneyError extends Error {
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
    HkSmartMoneyError,
    getHkSmOverview: vi.fn(),
    getHkSmTypes: vi.fn(),
    getHkSmSeats: vi.fn(),
    getHkSmSeatProfile: vi.fn(),
    getHkSmPersistence: vi.fn(),
    getHkSmConsensus: vi.fn(),
    getHkSmStock: vi.fn(),
    getHkSmEvidence: vi.fn(),
  };
});

import { HkSmartMoneyPanel } from '../components/smart-money/HkSmartMoneyPanel';
import {
  HkSmartMoneyError,
  getHkSmConsensus,
  getHkSmEvidence,
  getHkSmOverview,
  getHkSmPersistence,
  getHkSmSeatProfile,
  getHkSmSeats,
  getHkSmStock,
  getHkSmTypes,
  type HkSmConsensusResponse,
  type HkSmEvidenceResponse,
  type HkSmOverviewResponse,
  type HkSmPersistenceResponse,
  type HkSmSeatItem,
  type HkSmSeatProfileResponse,
  type HkSmSeatsResponse,
  type HkSmStockResponse,
  type HkSmTypesResponse,
} from '../services/api';

const mockOverview = vi.mocked(getHkSmOverview);
const mockTypes = vi.mocked(getHkSmTypes);
const mockSeats = vi.mocked(getHkSmSeats);
const mockSeatProfile = vi.mocked(getHkSmSeatProfile);
const mockPersistence = vi.mocked(getHkSmPersistence);
const mockConsensus = vi.mocked(getHkSmConsensus);
const mockStock = vi.mocked(getHkSmStock);
const mockEvidence = vi.mocked(getHkSmEvidence);

/** 切到某个分面（标签栏按钮；role=tab） */
const openFace = async (name: string) => {
  await userEvent.click(screen.getByRole('tab', { name }));
};

function overviewFixture(): HkSmOverviewResponse {
  return {
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:40:00Z',
    partial: false,
    version: 'v1',
    evidence_only: true,
    admitted: [],
    directions: {},
    freshness: {
      ccass_date: '2026-10-14',
      south_date: '2026-10-15',
      kline_date: '2026-10-15',
      ccass_lag_days: 1,
      partial: false,
    },
    categories: [
      {
        category: 'cn_broker',
        label: '内资·券商',
        category_label: '内资·券商',
        holding_yi: 1234.5,
        holding_date: '2026-10-14',
        n_symbols: 812,
        net5_yi: -12.3,
        net5_days: 5,
        net20_yi: 45.6,
        net20_days: 20,
        skill_bp: 18.2,
        vw_skill_bp: 25.4,
        n_lead_events: 52000,
        admitted: false,
        best: {
          h10_ic: 0.005,
          h10_tail_ok: false,
          variant: 'net5',
          variant_label: '5日净额',
          direction: 1,
          admitted: false,
        },
      },
      {
        category: 'southbound',
        label: '内资·港股通',
        category_label: '内资·港股通',
        holding_yi: 57962.1,
        holding_date: '2026-10-15',
        n_symbols: 1900,
        net5_yi: null,
        net5_days: null,
        net20_yi: 210.5,
        net20_days: 20,
        skill_bp: null,
        vw_skill_bp: null,
        n_lead_events: null,
        admitted: false,
        best: null,
      },
    ],
    counts_30d: {
      days: 21,
      events: 48211,
      transfer_suspect: 2617,
      first_seen: 812,
      exit_boundary: 143,
    },
    summary: { scores_date: null, pool_size: null, covered_mean: null },
    trends: {
      window: 5,
      categories: {
        cn_broker: [
          { date: '2026-10-14', net_yi: -12.3, n_symbols: 812 },
          { date: '2026-10-15', net_yi: 3.1, n_symbols: 640 },
        ],
      },
    },
    notes: { hit: '…', shrink: '…', suspect: '…' },
  };
}

function typesFixture(): HkSmTypesResponse {
  return {
    validated_at: '2026-10-16T01:35:00Z',
    as_of: '2026-10-15',
    admit_horizon: 10,
    horizon: '10',
    window: {
      eval_start: '2024-10-01',
      eval_end: '2026-10-15',
      warmup_days: 60,
      event_days: 466,
      event_rows: 931291,
    },
    admitted: [],
    calibration: null,
    signals: [
      {
        name: 'cn_broker::net5',
        category: 'cn_broker',
        category_label: '内资·券商',
        variant: 'net5',
        variant_label: '净额5日',
        coverage: { avg_names_per_day: 812.5, days: 466 },
        direction: 1,
        admitted: false,
        decision: 'h10 IC=0.0051 与尾部同号=否 → 未入柱（零入柱为真裁定）',
        results: {
          '10': {
            n_days: 466,
            rank_ic: 0.0051,
            icir: 0.12,
            ic_pos_rate: 0.522,
            q1_bp: -3.1,
            q2_bp: -1.2,
            q3_bp: 0.4,
            q4_bp: 1.1,
            q5_bp: 2.0,
            q5_minus_q1_bp: 5.1,
            buy_hit: 0.505,
            sell_hit: 0.494,
            asym_bp: 1.0,
            n_top_obs: 100,
            n_bot_obs: 100,
            tail_ok: false,
          },
        },
      },
    ],
    notes: { hit: '…' },
  };
}

function consensusFixture(): HkSmConsensusResponse {
  return {
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:35:00Z',
    adopted: [],
    directions: {},
    evidence_only: true,
    total: 0,
    items: [],
    notes: { method: '柱分位 = 池内截面 0–100 分位', missing: '…' },
  };
}

function evidenceFixture(): HkSmEvidenceResponse {
  return {
    validated_at: '2026-10-16T01:35:00Z',
    version: 'v1',
    window: {
      eval_start: '2024-10-01',
      eval_end: '2026-10-15',
      warmup_days: 60,
      event_days: 466,
      event_rows: 931291,
    },
    method: { event_gate: '|Δ市值| ≥ 100 万 HKD ∧ |Δ股数|/持股 ≥ 5%' },
    admit_horizon: 10,
    signals: [],
    correlation: null,
    calibration: null,
    exclusions: {
      days: 466,
      south_days: 466,
      events: 931291,
      transfer_suspect: 52633,
      first_seen: 1200,
      exit_boundary: 300,
      raw_moves: 1050000,
      transfer_suspect_share: 0.0565,
    },
    reconciliation: {
      as_of: '2026-10-15',
      a_seat_qty_sum: 1.2e12,
      south_qty_sum: 1.2206e12,
      ratio: 0.9831,
    },
    composite: null,
    admitted: [],
    evidence_only: true,
    warnings: ['无类型信号通过 h10 双过闸（零入柱）——共识榜不发布'],
  };
}

/** 席位行（v2 全 30 列） */
function seatItemFixture(): HkSmSeatItem {
  return {
    participant_id: 'C00019',
    participant_name: '香港上海汇丰银行',
    category: 'hk',
    category_label: '港资',
    kind: 'custodian',
    window: 'all',
    n_events: 42,
    n_buy: 25,
    n_sell: 17,
    n_exc10: 40,
    avg_exc_bp: 35.2,
    hit: 0.6,
    buy_hit: 0.64,
    sell_hit: 0.53,
    t: 2.1,
    shrunk_exc_bp: 23.8,
    shrunk_hit: 0.568,
    stability: true,
    eligible: true,
    first_event_date: '2024-10-08',
    last_event_date: '2026-10-14',
    skill_bp: 28.5,
    skill_t: 3.29,
    lb_skill_bp: 17.4,
    vw_skill_bp: 41.2,
    exc5_bp: 12.1,
    exc20_bp: 30.7,
    buy_exc_bp: 35.0,
    sell_exc_bp: -22.0,
    n_symbols: 18,
    avg_value_m: 240.0,
  };
}

function seatsFixture(): HkSmSeatsResponse {
  return {
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:35:00Z',
    window: 'all',
    category: null,
    sort: 'lb_skill',
    min_events: 50,
    horizon: 10,
    total: 430,
    items: [seatItemFixture()],
    notes: { hit: '…', shrink: '…', suspect: '…', episode: '…', eligible: '…' },
  };
}

function seatProfileFixture(): HkSmSeatProfileResponse {
  return {
    participant: {
      participant_id: 'C00019',
      participant_name: '香港上海汇丰银行',
      category: 'hk',
      category_label: '港资',
      kind: 'custodian',
      first_event_date: '2024-10-08',
      last_event_date: '2026-10-14',
    },
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:40:00Z',
    windows: { all: seatItemFixture() },
    monthly: [
      { month: '2026-09', n: 12, skill_bp: 22.0, vw_skill_bp: 30.0, hit: 0.58 },
    ],
    top_symbols: [
      {
        symbol: '0700.HK',
        name: '腾讯控股',
        n: 5,
        skill_bp: 40.1,
        vw_skill_bp: 35.2,
        last_event_date: '2026-10-14',
      },
    ],
    recent_events: [
      {
        date: '2026-10-14',
        symbol: '0700.HK',
        name: '腾讯控股',
        kind: 'custodian',
        direction: 1,
        delta_value: 1.2e8,
        value_yi: 1.2,
        delta_pct: 0.12,
        price: 640.0,
        transfer_suspect: false,
        first_of_episode: true,
        fwd_exc_5_bp: 12.0,
        fwd_exc_10_bp: 25.0,
        fwd_exc_20_bp: null,
      },
    ],
    notes: {},
  };
}

function persistenceFixture(): HkSmPersistenceResponse {
  return {
    generated_at: '2026-10-16T01:40:00Z',
    as_of: '2026-10-15',
    split_date: '2025-10-01',
    scatter: [
      {
        participant_id: 'C00019',
        name: '香港上海汇丰银行',
        category: 'hk',
        n1: 20,
        n2: 22,
        skill1_bp: 30.1,
        skill2_bp: 18.4,
      },
    ],
    spearman: { h10: 0.12, h20: 0.09, hit: 0.05 },
    quartiles: [
      { q: 1, n_seats: 40, n_events: 3000, forward_skill_bp: 8.1, forward_hit: 0.52 },
    ],
    category_halves: [
      { category: 'hk', n: 300, first_half_vw_skill_bp: 21.2, second_half_vw_skill_bp: 12.5 },
    ],
    method: { skill: '…', scatter: '…', quartile: '…', note: '前/后半切分实测弱正' },
  };
}

function stockFixture(): HkSmStockResponse {
  return {
    symbol: '0700.HK',
    name: '腾讯控股',
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:35:00Z',
    window: { days: 60, start: '2026-07-16' },
    timeline: [],
    holdings: [],
    stats: null,
    notes: {},
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockOverview.mockResolvedValue(overviewFixture());
  mockTypes.mockResolvedValue(typesFixture());
  mockSeats.mockResolvedValue(seatsFixture());
  mockSeatProfile.mockResolvedValue(seatProfileFixture());
  mockPersistence.mockResolvedValue(persistenceFixture());
  mockConsensus.mockResolvedValue(consensusFixture());
  mockEvidence.mockResolvedValue(evidenceFixture());
  mockStock.mockResolvedValue(stockFixture());
});

describe('HkSmartMoneyPanel · 产物未构建（404）', () => {
  it('overview 404 → 全局 notBuilt 空态（不是红错误条），且带重新加载', async () => {
    mockOverview.mockRejectedValue(
      new HkSmartMoneyError('港股聪明钱接口失败 404: hk_smart_money_not_built', 404),
    );
    render(<HkSmartMoneyPanel />);

    expect(await screen.findByText(/港股聪明钱统计尚未生成/)).toBeInTheDocument();
    expect(screen.getByText('重新加载')).toBeInTheDocument();
    expect(screen.queryByText(/加载失败/)).toBeNull();
  });
});

describe('HkSmartMoneyPanel · 统一大框五标签面（v2.1）', () => {
  it('默认面 = 机构优势：五 tab 就位、席位榜与散点渲染、懒面零请求', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    // 五面 tab 与默认选中
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([
      '机构优势',
      '类型效力',
      '生态趋势',
      '共识榜',
      '稳健性与口径',
    ]);
    expect(screen.getByRole('tab', { name: '机构优势' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // 默认面内容：席位榜（自持取数）+ 类别优势图
    expect(await screen.findByText('香港上海汇丰银行')).toBeInTheDocument();
    expect(screen.getByText(/席位优势榜/)).toBeInTheDocument();
    expect(screen.getByText('类别优势')).toBeInTheDocument();
    // 席位榜以 v2 默认参数取数（全史窗 / n≥50 / 按 90% 下界）
    expect(mockSeats).toHaveBeenCalledWith(
      expect.objectContaining({ window: 'all', sort: 'lb_skill', minEvents: 50 }),
    );

    // 懒取数纪律：非激活面零请求
    expect(mockTypes).not.toHaveBeenCalled();
    expect(mockConsensus).not.toHaveBeenCalled();
    expect(mockEvidence).not.toHaveBeenCalled();
    expect(mockPersistence).not.toHaveBeenCalled();

    // 面 1 双图（类别优势 + 技能散点，散点吃席位响应）
    await waitFor(() =>
      expect(screen.getAllByTestId('echarts-mock').length).toBeGreaterThanOrEqual(2),
    );
  });

  it('切「类型效力」才请求 types：公示条与双图（热力+分层）出现', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    await openFace('类型效力');
    await waitFor(() => expect(mockTypes).toHaveBeenCalledTimes(1));
    expect(mockConsensus).not.toHaveBeenCalled();
    expect(mockPersistence).not.toHaveBeenCalled();

    // evidence-only 公示条（本面信号零入柱）
    expect(await screen.findByText(/本期裁定：零入柱/)).toBeInTheDocument();
    // 热力图 + 分层柱
    await waitFor(() =>
      expect(screen.getAllByTestId('echarts-mock').length).toBeGreaterThanOrEqual(2),
    );
    // 检定表行：点击展开裁定明细
    fireEvent.click(screen.getByTitle('cn_broker::net5'));
    expect(await screen.findByText(/零入柱为真裁定/)).toBeInTheDocument();
  });

  it('切「共识榜」才请求 consensus；evidence-only 为「不发布」空态', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    await openFace('共识榜');
    await waitFor(() => expect(mockConsensus).toHaveBeenCalledWith(50));
    expect(await screen.findByText(/共识榜不发布/)).toBeInTheDocument();
    expect(screen.queryByText(/共识榜数据不可用/)).toBeNull();
  });

  it('切「稳健性与口径」：持续性审计与证据台一并取数渲染', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    await openFace('稳健性与口径');
    await waitFor(() => expect(mockPersistence).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockEvidence).toHaveBeenCalledTimes(1));

    expect(await screen.findByText(/持续性审计 · 前半强/)).toBeInTheDocument();
    expect(screen.getByText(/秩相关 h10 0.120/)).toBeInTheDocument();
    // 证据台并入：零入柱徽标 + 警告 + 口径脚注全集
    expect(screen.getAllByText(/零入柱（evidence-only）/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/共识榜不发布/)).toBeInTheDocument();
    expect(screen.getByText('口径脚注全集（各面用词唯一出处）')).toBeInTheDocument();
  });
});

describe('HkSmartMoneyPanel · evidence-only 与口径文案', () => {
  it('概览带常显：零入柱徽标在默认面即出现（不切 tab）', async () => {
    render(<HkSmartMoneyPanel />);
    expect(
      await screen.findByText('聪明钱 · 席位行为统计与类型效力'),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/零入柱（evidence-only）/).length).toBeGreaterThanOrEqual(1);
  });

  it('类型流向词：负数=净流出、缺失=净额（缺失绝不显示 0）', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    expect(screen.getByText('5日净流出')).toBeInTheDocument(); // cn_broker net5=-12.3
    expect(screen.getByText('5日净额')).toBeInTheDocument(); // southbound net5=null
    expect(screen.getByText('-12.3亿')).toBeInTheDocument();
  });
});

describe('HkSmartMoneyPanel · 单面失败独立结算', () => {
  it('types 面失败 → 该面错误条，概览照常渲染', async () => {
    mockTypes.mockRejectedValue(new Error('types 面炸了'));
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');
    await openFace('类型效力');

    expect(await screen.findByText(/类型效力数据不可用：types 面炸了/)).toBeInTheDocument();
    expect(screen.getByText('类型净流概览')).toBeInTheDocument();
  });

  it('席位榜自持取数失败 → 错误条，绝不伪装「无席位行为」', async () => {
    mockSeats.mockRejectedValue(new Error('席位面炸了'));
    render(<HkSmartMoneyPanel />);

    expect(await screen.findByText(/席位数据不可用：席位面炸了/)).toBeInTheDocument();
    expect(screen.queryByText('当前筛选下无达标席位')).toBeNull();
  });
});

describe('HkSmartMoneyPanel · 双抽屉', () => {
  it('席位行点击 → 席位档案取数（原样传 participant_id）并渲染月度战绩', async () => {
    render(<HkSmartMoneyPanel />);
    const name = await screen.findByText('香港上海汇丰银行');
    fireEvent.click(name);

    await waitFor(() => expect(mockSeatProfile).toHaveBeenCalledWith('C00019'));
    expect(await screen.findByText('月度战绩')).toBeInTheDocument();
    // 抽屉独有内容：增/减持命中率对比条（全史窗默认）
    expect(screen.getByText(/增 \/ 减持命中率对比（全史窗）/)).toBeInTheDocument();
  });

  it('头部输入框回车 → 个股档案抽屉取数（evidence-only 下仍可达）', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    const input = screen.getByPlaceholderText(/个股档案/);
    await userEvent.type(input, '0700{Enter}');

    await waitFor(() => expect(mockStock).toHaveBeenCalledWith('0700', 60));
  });
});
