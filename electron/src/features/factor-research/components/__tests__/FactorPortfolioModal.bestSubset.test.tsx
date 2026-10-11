/**
 * 组合构建弹窗的「精选子集（T-FE-02）」节行为契约。
 *
 * 守的性质（只有真渲染才暴露得出来）：
 * 1. 可用块 → K*、三行基准对照（精选子集 / 等权 1/N / 现行规则）、过拟合旗标、
 *    K 扫描线、notes 都要如实出现；
 * 2. 旧 JSON 没有 `best_subset` 字段（undefined）→ 整节不渲染（不是空表冒充）；
 * 3. 有字段但降级（available:false）→ 必须显示 reason，不用 0 或空数组糊弄；
 * 4. beats_* 为 false 或 null 时不粉饰：false 显示「≤ 基准」，null 什么都不显示；
 *    现行规则与候选无交集（current_rule:null）→ 表格不出现该行；
 * 5. 因子 chip 点击 = 打开因子报告并关闭弹窗（与权重列表同语义）；
 * 6. 复制权重 JSON 必须带 best_subset（训练侧要能直接拿到两组清单）。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FactorPortfolioModal } from '../factor-report/FactorPortfolioModal';
import type { FactorPortfolioResponse, BestSubsetBlock } from '../../types/factorReport';

const { getPortfolioMock } = vi.hoisted(() => ({ getPortfolioMock: vi.fn() }));

vi.mock('../../services/factorReportService', () => ({
  getFactorPortfolio: getPortfolioMock,
}));

const BEST: BestSubsetBlock = {
  available: true,
  method: 'greedy_forward_swap',
  train_frac: 0.7,
  k_range: [10, 15],
  k: 10,
  chosen: { k: 10, train_sharpe: 13.99, holdout_sharpe: 2.63, full_sharpe: 8.45 },
  factors: [
    { name: 'gtja_015', direction: 1 },
    { name: 'a101_069', direction: -1 },
  ],
  curve: [
    { k: 10, train_sharpe: 13.99, holdout_sharpe: 2.63 },
    { k: 11, train_sharpe: 13.8, holdout_sharpe: 2.72 },
  ],
  benchmarks: {
    equal_weight_all: { n_factors: 213, train_sharpe: 6.7, holdout_sharpe: 1.11, full_sharpe: 4.42 },
    current_rule: { n_factors: 30, train_sharpe: 8.38, holdout_sharpe: 1.54, full_sharpe: 3.1 },
  },
  overfit_warning: true,
  sharpe_ratio_train_holdout: 5.32,
  vs_benchmarks: { beats_equal_weight: true, beats_current_rule: true },
  n_days: { train: 775, holdout: 333, total: 1108 },
  universe: { n_candidates: 213, n_excluded_short_history: 0 },
  notes: ['train/holdout Sharpe 比 5.32 > 3.0——过拟合风险高（样本内外比 >3 判据），谨慎对待'],
  rule: { holding_days: 20, round_trip_cost: 0.00155, annualization: 252 },
};

const BASE: FactorPortfolioResponse = {
  available: true,
  dataset: 'alpha_library',
  summary: {
    n_universe: 429,
    n_passed: 320,
    n_selected: 30,
    n_rejected: 399,
    composite_ic: 0.0698,
    composite_icir: 0.78,
    single_icir_avg: 0.4,
  },
  factors: [{ name: 'f1', weight: 0.5, direction: 1 }],
  rejected: [],
  rule: { icir_min: 0.15, coverage_min: 0.6, holding_days: 20, corr_threshold: 0.9 },
};

function renderModal(resp: FactorPortfolioResponse) {
  getPortfolioMock.mockResolvedValue(resp);
  const onClose = vi.fn();
  const onPick = vi.fn();
  render(
    <FactorPortfolioModal
      open
      dataset="alpha_library"
      datasetLabel="AlphaLibrary"
      onClose={onClose}
      onPick={onPick}
    />,
  );
  return { onClose, onPick };
}

const writeText = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  getPortfolioMock.mockReset();
  writeText.mockClear();
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
});

describe('FactorPortfolioModal · 精选子集节', () => {
  test('可用块：K*、三行基准对照、过拟合旗标、K 扫描与 notes 如实呈现', async () => {
    renderModal({ ...BASE, best_subset: BEST });

    expect(await screen.findByText('精选子集 · 组合 Sharpe 口径')).toBeInTheDocument();
    expect(screen.getByText('K* = 10（区间 10–15）')).toBeInTheDocument();
    expect(screen.getByText(/train\/holdout = 5\.32，过拟合风险高/)).toBeInTheDocument();
    expect(screen.getByText('留出窗 > 等权 1/N')).toBeInTheDocument();
    expect(screen.getByText('留出窗 > 现行规则')).toBeInTheDocument();

    // 三行对照：精选子集 / 1/N / 现行规则
    expect(screen.getByText('精选子集 K*=10')).toBeInTheDocument();
    expect(screen.getByText('等权 1/N（213 个）')).toBeInTheDocument();
    expect(screen.getByText('现行规则（30 个）')).toBeInTheDocument();
    expect(screen.getByText('+13.99')).toBeInTheDocument();
    expect(screen.getByText('+2.63')).toBeInTheDocument();

    // K 扫描小字
    expect(screen.getByText(/K 扫描 train\/holdout：10 \+13\.99\/\+2\.63 · 11 \+13\.80\/\+2\.72/)).toBeInTheDocument();
    expect(screen.getByText(/过拟合风险高（样本内外比 >3 判据）/)).toBeInTheDocument();
  });

  test('点击因子 chip → onPick 因子名并关闭弹窗', async () => {
    const { onClose, onPick } = renderModal({ ...BASE, best_subset: BEST });

    fireEvent.click(await screen.findByRole('button', { name: 'gtja_015' }));

    expect(onPick).toHaveBeenCalledWith('gtja_015');
    expect(onClose).toHaveBeenCalled();
  });

  test('旧 JSON 无 best_subset 字段 → 整节不渲染', async () => {
    renderModal({ ...BASE });

    // 等主体渲染完，再断言该节完全缺席
    expect(await screen.findByText(/组合 IC/)).toBeInTheDocument();
    expect(screen.queryByText('精选子集 · 组合 Sharpe 口径')).not.toBeInTheDocument();
  });

  test('降级块（available:false）→ 显示 reason，不渲染表格', async () => {
    renderModal({
      ...BASE,
      best_subset: { available: false, reason: '缺 ls_20 列或无可用候选序列（快照未含该持有期）' },
    });

    expect(await screen.findByText(/精选子集不可用：缺 ls_20 列/)).toBeInTheDocument();
    expect(screen.queryByText('精选子集 K*=10')).not.toBeInTheDocument();
  });

  test('beats 为 false 显示「≤ 基准」；为 null 与 current_rule:null 时不渲染对应元素', async () => {
    renderModal({
      ...BASE,
      best_subset: {
        ...BEST,
        overfit_warning: false,
        sharpe_ratio_train_holdout: null,
        vs_benchmarks: { beats_equal_weight: false, beats_current_rule: null },
        benchmarks: { ...BEST.benchmarks, current_rule: null },
        notes: [],
      },
    });

    expect(await screen.findByText('留出窗 ≤ 等权 1/N')).toBeInTheDocument();
    expect(screen.queryByText(/留出窗.*现行规则/)).not.toBeInTheDocument();
    expect(screen.queryByText(/现行规则（30 个）/)).not.toBeInTheDocument();
    expect(screen.queryByText(/⚠ train\/holdout/)).not.toBeInTheDocument();
  });

  test('复制权重 JSON 包含 best_subset', async () => {
    renderModal({ ...BASE, best_subset: BEST });

    fireEvent.click(await screen.findByRole('button', { name: /复制权重 JSON/ }));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const payload = JSON.parse(writeText.mock.calls[0][0] as string);
    expect(payload.best_subset.k).toBe(10);
    expect(payload.best_subset.factors.map((f: { name: string }) => f.name)).toEqual(['gtja_015', 'a101_069']);
  });
});
