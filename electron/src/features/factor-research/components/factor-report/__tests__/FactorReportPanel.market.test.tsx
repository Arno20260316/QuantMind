/**
 * 因子报告面板 —— 换市场的接线契约（2026-10-16 市场分层）。
 *
 * 面板持有的东西**全部是按市场取的**：数据集清单、因子摘要、明细、相关、基准。
 * 换市场这条缝上错了不报错，只是把人领到别市场的数上：
 *
 * 1. 换市场后每个请求都要带新市场。少带一处 = 那一块还是 A 股口径，
 *    而界面**完全正常**（这是本文件最核心的一条）；
 * 2. 选中的因子必须清空重选：因子代码跨市场会重名（同名不同域），
 *    不清就是「点 A 看 B」的静默错领；
 * 3. 点名过的基准参数必须清掉：它是**上一市场的代码**（如 000300.SH），
 *    带去新市场只会让后端按本市场基准表顺位回退，界面上却显示点了某个基准；
 * 4. 深链钉住的数据集只在**首装**钉：其它市场不一定有同名快照集
 *    （l2_factors 是 A 股的），不放松就会落到一个永远空的数据集上。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { FactorReportPanel } from '../FactorReportPanel';

const { summaryMock, datasetsMock, detailMock, relatedMock, corrMock, buildStatusMock, startBuildMock } = vi.hoisted(() => ({
  summaryMock: vi.fn(),
  datasetsMock: vi.fn(),
  detailMock: vi.fn(),
  relatedMock: vi.fn(),
  corrMock: vi.fn(),
  buildStatusMock: vi.fn(),
  startBuildMock: vi.fn(),
}));

vi.mock('../../../services/factorReportService', () => ({
  getFactorSummary: summaryMock,
  getFactorDatasets: datasetsMock,
  getFactorDetail: detailMock,
  getFactorRelated: relatedMock,
  getFactorCorrelation: corrMock,
  getFactorReportBuildStatus: buildStatusMock,
  startFactorReportBuild: startBuildMock,
}));

vi.mock('../FactorRankList', () => ({
  FactorRankList: (p: {
    factors: Array<{ name: string }>;
    market: string;
    onSelect: (name: string) => void;
  }) => (
    <div data-testid="rank-list" data-market={p.market} data-n={p.factors.length}>
      {p.factors.map((f) => (
        <button key={f.name} onClick={() => p.onSelect(f.name)}>{`桩-选 ${f.name}`}</button>
      ))}
    </div>
  ),
}));

// 明细页签桩：把 market/params 摊在 DOM 上，并给一个「点名基准」的入口
// （等价于 ExcessTab 里的基准候选条：onParams({...params, bench})）
vi.mock('../FactorDetailTabs', () => ({
  FactorDetailTabs: (p: {
    factor: string;
    market: string;
    params: { bench?: string };
    onParams: (x: Record<string, unknown>) => void;
  }) => (
    <div data-testid="detail" data-factor={p.factor} data-market={p.market} data-bench={p.params.bench ?? ''}>
      <button onClick={() => p.onParams({ ...p.params, bench: 'PICKED_BENCH' })}>桩-点名基准</button>
    </div>
  ),
}));
vi.mock('../FactorClusterModal', () => ({ FactorClusterModal: () => null }));
vi.mock('../FactorPortfolioModal', () => ({ FactorPortfolioModal: () => null }));

const CN_DATASETS = {
  default: 'alpha_library',
  items: [
    { dataset: 'alpha_library', label: 'Alpha 库', available: true },
    { dataset: 'l2_factors', label: 'L2 因子', available: true },
  ],
};
const HK_DATASETS = {
  default: 'hk_alpha',
  items: [
    { dataset: 'hk_alpha', label: '港股 Alpha', available: true },
    { dataset: 'hk_l2', label: '港股 L2', available: true },
  ],
};

const META = {
  universe: '全 A 非 ST',
  horizon: 'fwd_ret_5',
  start: '20220104',
  end: '20260827',
  n_dates: 1000,
  generated_at: '2026-09-19 20:20:50',
};

const summaryOf = (dataset: string, names: string[]) => ({
  available: true,
  dataset,
  meta: META,
  total: names.length,
  factors: names.map((n) => ({ name: n })),
});

const detailCall = () => detailMock.mock.calls[detailMock.mock.calls.length - 1];

beforeEach(() => {
  summaryMock.mockReset();
  datasetsMock.mockReset();
  detailMock.mockReset();
  relatedMock.mockReset();
  corrMock.mockReset();
  buildStatusMock.mockReset();
  startBuildMock.mockReset();

  datasetsMock.mockImplementation((mk: string) => Promise.resolve(mk === 'HK' ? HK_DATASETS : CN_DATASETS));
  summaryMock.mockImplementation((p: { dataset: string }) =>
    Promise.resolve(
      p.dataset === 'hk_alpha' ? summaryOf('hk_alpha', ['hk1'])
        : p.dataset === 'hk_l2' ? summaryOf('hk_l2', ['hk_l2_1'])
          : p.dataset === 'l2_factors' ? summaryOf('l2_factors', ['cn_l2_1'])
            : summaryOf('alpha_library', ['cn1']),
    ),
  );
  detailMock.mockResolvedValue({ factor: 'x' });
  relatedMock.mockResolvedValue({ related: [] });
  corrMock.mockResolvedValue({});
  buildStatusMock.mockResolvedValue({ dataset: 'alpha_library', exists: true, running: false, step: '' });
  startBuildMock.mockResolvedValue({ started: true, running: true });
});

describe('FactorReportPanel：换市场', () => {
  test('换市场：清单/摘要/明细全部带新市场，选中因子重选且基准参数清掉', async () => {
    const { rerender } = render(<FactorReportPanel market="CN" />);

    // CN：落到 alpha_library，选中第一个因子
    await waitFor(() => expect(screen.getByTestId('detail').getAttribute('data-factor')).toBe('cn1'));
    expect(screen.getByTestId('detail').getAttribute('data-market')).toBe('CN');

    // 先点名一个基准（上一市场的参数）
    fireEvent.click(screen.getByText('桩-点名基准'));
    await waitFor(() => expect(detailCall()[2]).toMatchObject({ bench: 'PICKED_BENCH' }));

    rerender(<FactorReportPanel market="HK" />);

    // 数据集清单按新市场重取，落到 HK 的第一个可用数据集与第一个因子
    await waitFor(() => expect(screen.getByTestId('detail').getAttribute('data-market')).toBe('HK'));
    await waitFor(() => expect(screen.getByTestId('detail').getAttribute('data-factor')).toBe('hk1'));
    expect(screen.getByTestId('rank-list').getAttribute('data-market')).toBe('HK');
    expect(datasetsMock).toHaveBeenCalledWith('HK');

    // 点名过的基准是上一市场的代码：必须清掉，不能带去新市场
    expect(screen.getByTestId('detail').getAttribute('data-bench')).toBe('');
    const [factor, dataset, params, mk] = detailCall();
    expect([factor, dataset, mk]).toEqual(['hk1', 'hk_alpha', 'HK']);
    expect(params).not.toHaveProperty('bench');
  });

  test('深链钉住的数据集只在首装钉：换市后落到新市场自己的数据集', async () => {
    const { rerender } = render(<FactorReportPanel market="CN" initialDataset="l2_factors" />);

    // 首装被深链钉在 l2_factors（A 股专有）
    await waitFor(() => expect(screen.getByTestId('detail').getAttribute('data-factor')).toBe('cn_l2_1'));

    rerender(<FactorReportPanel market="HK" initialDataset="l2_factors" />);

    // HK 没有 l2_factors：钉住态必须失效，落到 HK 的第一个可用集
    await waitFor(() => expect(screen.getByTestId('detail').getAttribute('data-factor')).toBe('hk1'));
    const summaryDatasets = summaryMock.mock.calls.map((c) => (c[0] as { dataset: string }).dataset);
    expect(summaryDatasets.filter((d) => d === 'l2_factors')).toHaveLength(1); // 只在 CN 首装问过一次
    expect(summaryMock.mock.calls.map((c) => (c[0] as { market: string }).market)).toContain('HK');
  });
});
