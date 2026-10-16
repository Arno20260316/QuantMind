/**
 * 港股聪明钱面板接线单测（细案 §6 验收）。
 *
 * 钉住：
 * - 404（产物未构建）→ 全局 notBuilt 空态 + 重新加载，不白屏不伪装；
 * - evidence-only 真裁定渲染：零入柱徽标、共识榜不发布空态、警告如实呈现；
 * - 单面失败独立结算：types 面失败出错误条，概览/席位/共识照常渲染；
 * - 席位榜自持取数失败进错误条（绝不伪装「无席位行为」）；
 * - 头部「个股档案」输入框打开抽屉（代码原样透传取数层）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';

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
  getHkSmSeats,
  getHkSmStock,
  getHkSmTypes,
  type HkSmConsensusResponse,
  type HkSmEvidenceResponse,
  type HkSmOverviewResponse,
  type HkSmSeatsResponse,
  type HkSmStockResponse,
  type HkSmTypesResponse,
} from '../services/api';

const mockOverview = vi.mocked(getHkSmOverview);
const mockTypes = vi.mocked(getHkSmTypes);
const mockSeats = vi.mocked(getHkSmSeats);
const mockConsensus = vi.mocked(getHkSmConsensus);
const mockStock = vi.mocked(getHkSmStock);
const mockEvidence = vi.mocked(getHkSmEvidence);

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
    trends: { window: 5, categories: {} },
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

function seatsFixture(): HkSmSeatsResponse {
  return {
    as_of: '2026-10-15',
    generated_at: '2026-10-16T01:35:00Z',
    window: '60',
    category: null,
    sort: 'shrunk_hit',
    min_events: 5,
    horizon: 10,
    total: 430,
    items: [
      {
        participant_id: 'C00019',
        participant_name: '香港上海汇丰银行',
        category: 'hk',
        category_label: '港资',
        kind: 'custodian',
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
        last_event_date: '2026-10-14',
      },
    ],
    notes: { hit: '…', shrink: '…', suspect: '…', episode: '…', eligible: '…' },
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

describe('HkSmartMoneyPanel · evidence-only 真裁定渲染', () => {
  it('零入柱徽标出现于概览带与证据台；共识榜为「不发布」空态而非错误', async () => {
    render(<HkSmartMoneyPanel />);

    expect(
      await screen.findByText('聪明钱 · 席位行为统计与类型效力'),
    ).toBeInTheDocument();
    // 概览带 + 证据台两处徽标
    expect(screen.getAllByText(/零入柱（evidence-only）/).length).toBeGreaterThanOrEqual(2);
    // 共识榜诚实空态与警告原样透传（共识榜空态与证据台警告各一处，均含「共识榜不发布」）
    expect((await screen.findAllByText(/共识榜不发布/)).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/共识榜数据不可用/)).toBeNull();
  });

  it('类型流向词：负数=净流出、缺失=净额（缺失绝不显示 0）', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    expect(screen.getByText('5日净流出')).toBeInTheDocument(); // cn_broker net5=-12.3
    expect(screen.getByText('5日净额')).toBeInTheDocument(); // southbound net5=null
    expect(screen.getByText('-12.3亿')).toBeInTheDocument();
  });

  it('类型效力矩阵与席位榜照常渲染（矩阵行可展开 / 席位行收缩估计）', async () => {
    render(<HkSmartMoneyPanel />);

    // 矩阵行（title=信号名），点击展开裁定明细
    const row = await screen.findByTitle('cn_broker::net5');
    expect(row).toBeInTheDocument();
    fireEvent.click(row);
    expect(await screen.findByText(/零入柱为真裁定/)).toBeInTheDocument();
    // 席位榜行（自持取数）+ 收缩估计（0.568 → 57%）
    expect(await screen.findByText('香港上海汇丰银行')).toBeInTheDocument();
    expect(screen.getByText('57%')).toBeInTheDocument();
    expect(screen.getByText('+23.8')).toBeInTheDocument(); // shrunk_exc_bp
  });
});

describe('HkSmartMoneyPanel · 单面失败独立结算', () => {
  it('types 面失败 → 该面错误条，概览/席位/共识照常', async () => {
    mockTypes.mockRejectedValue(new Error('types 面炸了'));
    render(<HkSmartMoneyPanel />);

    expect(await screen.findByText(/类型效力数据不可用：types 面炸了/)).toBeInTheDocument();
    expect(screen.getByText('类型净流概览')).toBeInTheDocument();
    expect(await screen.findByText('香港上海汇丰银行')).toBeInTheDocument();
  });

  it('席位榜自持取数失败 → 错误条，绝不伪装「无席位行为」', async () => {
    mockSeats.mockRejectedValue(new Error('席位面炸了'));
    render(<HkSmartMoneyPanel />);

    expect(await screen.findByText(/席位数据不可用：席位面炸了/)).toBeInTheDocument();
    expect(screen.queryByText('当前筛选下无达标席位')).toBeNull();
  });
});

describe('HkSmartMoneyPanel · 个股档案入口', () => {
  it('头部输入框回车 → 以原始代码打开抽屉取数（evidence-only 下抽屉仍可达）', async () => {
    render(<HkSmartMoneyPanel />);
    await screen.findByText('聪明钱 · 席位行为统计与类型效力');

    const input = screen.getByPlaceholderText(/个股档案/);
    await userEvent.type(input, '0700{Enter}');

    await waitFor(() => expect(mockStock).toHaveBeenCalledWith('0700', 60));
  });
});
