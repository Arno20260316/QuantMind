/**
 * 报告面板「跳回单因子分析」的接线契约（2026-10-15）。
 *
 * 反跳资格（这个因子在单因子页打不打得开）**不由面板自己判**：`locateSingle` 是
 * 页面提供的目录索引查询（= 单因子页真实可打开集合的唯一真源）。报告数据集名
 * （l1_l2_factors / rd_mined / custom…）与单因子目录（private/classic）不是同一
 * 命名空间，按数据集名硬映射会在这些名字上静默错判，故一律走 code 级查表。
 * 面板的责任只有两件：**查得到才透出入口**、点击回传**整个落点**（code + 数据集）
 * 让页面按它切库。
 *
 * 守的失败模式：查不到还透出入口 —— 用户点过去只会得到一屏「该因子不可用」，
 * 界面上没有任何地方说得出是为什么。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { FactorReportPanel } from '../FactorReportPanel';

const { summaryMock, datasetsMock, detailMock, relatedMock, corrMock, buildStatusMock, startBuildMock } =
  vi.hoisted(() => ({
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

// 重子组件换成桩：榜单桩负责「换因子」，详情桩把 onOpenSingle 摊成一个按钮
vi.mock('../FactorRankList', () => ({
  FactorRankList: (p: { factors: Array<{ name: string }>; onSelect: (name: string) => void }) => (
    <div data-testid="rank-list">
      {p.factors.map((f) => (
        <button key={f.name} onClick={() => p.onSelect(f.name)}>{`桩-选 ${f.name}`}</button>
      ))}
    </div>
  ),
}));
vi.mock('../FactorDetailTabs', () => ({
  FactorDetailTabs: (p: { factor: string; onOpenSingle?: () => void }) => (
    <div data-testid="detail" data-factor={p.factor}>
      {p.onOpenSingle && <button data-testid="detail-jump" onClick={p.onOpenSingle} />}
    </div>
  ),
}));
vi.mock('../FactorClusterModal', () => ({ FactorClusterModal: () => null }));
vi.mock('../FactorPortfolioModal', () => ({ FactorPortfolioModal: () => null }));

const DATASETS = {
  default: 'alpha_library',
  items: [{ dataset: 'alpha_library', label: 'Alpha 库', available: true }],
};
/** 顶栏会逐字段读它（universe / horizon.replace(...)…），给全字段免得踩空。 */
const META = {
  universe: '全 A 非 ST',
  horizon: 'fwd_ret_5',
  start: '20220104',
  end: '20260827',
  n_dates: 1000,
  generated_at: '2026-09-19 20:20:50',
};
const summaryFor = (factors: Array<{ name: string }>) => ({
  available: true,
  dataset: 'alpha_library',
  meta: META,
  total: factors.length,
  factors,
});

beforeEach(() => {
  summaryMock.mockReset();
  datasetsMock.mockReset();
  detailMock.mockReset();
  relatedMock.mockReset();
  corrMock.mockReset();
  buildStatusMock.mockReset();
  startBuildMock.mockReset();
  datasetsMock.mockResolvedValue(DATASETS);
  summaryMock.mockResolvedValue(summaryFor([{ name: 'f1' }, { name: 'f2' }]));
  detailMock.mockResolvedValue({ factor: 'f1' });
  relatedMock.mockResolvedValue({ related: [] });
  corrMock.mockResolvedValue({});
  buildStatusMock.mockResolvedValue({ dataset: 'alpha_library', exists: true, running: false, step: '' });
  startBuildMock.mockResolvedValue({ started: true, running: true });
});

const LOC = { dataset: 'private' as const, code: 'f1' };

describe('FactorReportPanel：跳回单因子分析的接线', () => {
  test('选中因子查得到落点：入口透到详情头，点击回传整个落点', async () => {
    const openSingle = vi.fn();
    const locateSingle = vi.fn((c: string) => (c === 'f1' ? LOC : null));
    render(<FactorReportPanel locateSingle={locateSingle} openSingle={openSingle} />);

    // 面板拿当前选中（首个因子 f1）查了落点才透出入口
    await waitFor(() => expect(locateSingle).toHaveBeenCalledWith('f1'));
    fireEvent.click(await screen.findByTestId('detail-jump'));

    expect(openSingle).toHaveBeenCalledTimes(1);
    expect(openSingle).toHaveBeenCalledWith(LOC);
  });

  test('查不到落点：不透出入口（点过去只会是一屏「该因子不可用」）', async () => {
    render(<FactorReportPanel locateSingle={() => null} openSingle={vi.fn()} />);

    await screen.findByTestId('detail');
    expect(screen.queryByTestId('detail-jump')).toBeNull();
  });

  test('换因子：落点跟着新因子重查，入口改指新库', async () => {
    const openSingle = vi.fn();
    const locateSingle = vi.fn((c: string) =>
      c === 'f2' ? { dataset: 'classic' as const, code: 'f2' } : null,
    );
    render(<FactorReportPanel locateSingle={locateSingle} openSingle={openSingle} />);

    await screen.findByTestId('detail');
    expect(screen.queryByTestId('detail-jump')).toBeNull(); // f1 查不到 → 没有入口

    fireEvent.click(screen.getByText('桩-选 f2'));
    fireEvent.click(await screen.findByTestId('detail-jump'));

    expect(openSingle).toHaveBeenCalledWith({ dataset: 'classic', code: 'f2' });
  });

  test('半边接线（只给查询不给回调）：不出入口——点了没反应的按钮等于陷阱', async () => {
    render(<FactorReportPanel locateSingle={() => LOC} />);

    await screen.findByTestId('detail');
    expect(screen.queryByTestId('detail-jump')).toBeNull();
  });

  test('不传这一对 prop：行为不变（老调用方兼容）', async () => {
    render(<FactorReportPanel />);

    await screen.findByTestId('detail');
    expect(screen.queryByTestId('detail-jump')).toBeNull();
  });
});
