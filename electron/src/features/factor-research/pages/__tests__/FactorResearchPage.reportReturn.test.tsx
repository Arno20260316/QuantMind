/**
 * 因子研究页 —— 「因子报告 → 单因子分析」反跳的页面接线（2026-10-15）。
 *
 * 正向（单因子页的「因子报告」按钮）走 openReport + 深链（initialDataset/Code）；
 * 反向复用 openSingle 这条既有通道。页面只提供给面板两件事，面板不自行做口径判断：
 *   1. `locateSingle` = catalogIndex 的 **code 级**查表（private/classic 两目录合并）。
 *      报告数据集名与单因子目录名不是同一命名空间，按数据集名硬映射会把
 *      l1_l2_factors 之类的因子错判 —— 查得到才是「单因子页真实打得开」的真源；
 *   2. `openSingle` = 按落点切库 + 切页签（与筛选页点进单因子同一条通道）。
 *
 * 守两件事（坏了都不报错、只会静默做错）：
 * 1. 报告页签与筛选页签一样要把**缺的那份目录**补齐 —— 否则经典库因子的反跳
 *    入口会静默缺席（不是错误面，是入口消失）；
 * 2. 反跳后落到单因子页的是**这个因子**、库也切对了，不被「补第一个因子」顶掉。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import FactorResearchPage from '../FactorResearchPage';

const { catalogMock, leaderboardMock, isAdminRef } = vi.hoisted(() => ({
  catalogMock: vi.fn(),
  leaderboardMock: vi.fn(),
  isAdminRef: { value: true },
}));

vi.mock('../../services/factorResearchService', async () => {
  const actual = await vi.importActual<typeof import('../../services/factorResearchService')>(
    '../../services/factorResearchService',
  );
  return { ...actual, getCatalog: catalogMock, getLeaderboard: leaderboardMock };
});

// 选择器对假 state 求值：市场恒 CN（本文件与分层无关），is_admin 由 isAdminRef 控制
vi.mock('../../../../store', () => ({
  useAppSelector: (sel: (s: unknown) => unknown) =>
    sel({ ui: { currentMarket: 'CN' }, auth: { user: { is_admin: isAdminRef.value } } }),
  // 页面头部挂了真 MarketSelector（页内市场切换入口）：它要 useAppDispatch。
  // 本文件不测切市场，给 no-op（点击→dispatch 的接线由 markets 用例守）。
  useAppDispatch: () => vi.fn(),
}));

// 子组件桩：把页面传下去的 props 摊在 DOM 上，本文件测的就是这些 props
vi.mock('../../components/CatalogSidebar', () => ({ CatalogSidebar: () => <div data-testid="sidebar" /> }));
vi.mock('../../components/LeaderboardTab', () => ({ LeaderboardTab: () => <div data-testid="tab-leaderboard" /> }));
vi.mock('../../components/CompareTab', () => ({ CompareTab: () => null }));
vi.mock('../../components/ComposeTab', () => ({ ComposeTab: () => null }));
vi.mock('../../components/SnapshotPanel', () => ({ SnapshotPanel: () => <div data-testid="panel-snapshot" /> }));
vi.mock('../../components/ScanPanel', () => ({ ScanPanel: () => <div data-testid="panel-scan" /> }));
vi.mock('../../components/SingleFactorTab', () => ({
  SingleFactorTab: (p: { code: string | null; dataset: string }) => (
    <div data-testid="single" data-code={p.code} data-dataset={p.dataset} />
  ),
}));
vi.mock('../../components/RegisterToTrainingModal', () => ({ RegisterToTrainingModal: () => null }));
vi.mock('../../components/ScreeningTab', () => ({ ScreeningTab: () => <div data-testid="screening" /> }));

// 报告面板桩：复刻真面板的两步——先 locateSingle 查落点，查得到才让用户点入口
vi.mock('../../components/factor-report/FactorReportPanel', () => ({
  FactorReportPanel: (p: {
    locateSingle?: (code: string) => { dataset: string; code: string } | null;
    openSingle?: (loc: { dataset: string; code: string }) => void;
  }) => {
    const hit = (c: string) => (p.locateSingle?.(c) ? '1' : '0');
    return (
      <div data-testid="factor-report">
        <span data-testid="loc-private" data-hit={hit('a101_001')} />
        <span data-testid="loc-classic" data-hit={hit('ABTURN')} />
        <span data-testid="loc-ghost" data-hit={hit('GHOST')} />
        <button
          onClick={() => {
            const loc = p.locateSingle?.('ABTURN');
            if (loc && p.openSingle) p.openSingle(loc);
          }}
        >
          桩-反跳经典
        </button>
      </div>
    );
  },
}));

const PRIVATE_CATALOG = {
  factors: [{ code: 'a101_001', name_cn: 'a101_001', l2: 'alpha_library', available: true }],
  l1_order: ['Alpha 因子库'],
  l2_order: {},
  meta: {},
};
const CLASSIC_CATALOG = {
  factors: [{ code: 'ABTURN', name_cn: '换手率', l2: '动量', available: true }],
  l1_order: ['动量'],
  l2_order: {},
  meta: {},
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <FactorResearchPage />
    </MemoryRouter>,
  );

const gotoReport = async () => {
  fireEvent.click(await screen.findByRole('button', { name: '因子报告' }));
  return screen.findByTestId('factor-report');
};

beforeEach(() => {
  catalogMock.mockReset();
  leaderboardMock.mockReset();
  isAdminRef.value = true;
  catalogMock.mockImplementation((ds: string) =>
    Promise.resolve(ds === 'private' ? PRIVATE_CATALOG : CLASSIC_CATALOG),
  );
  // 榜单永远有一行：若 activeCode 被误清空，「补第一个因子」会把它顶上来
  leaderboardMock.mockResolvedValue({ leaderboard: [{ code: 'AUTO_FIRST', rank: 1 }], meta: {} });
});

describe('FactorResearchPage：报告页签的反跳接线', () => {
  test('进报告页签补齐缺的目录：经典库因子查得到落点，不在目录里的查不到', async () => {
    renderPage();
    await waitFor(() => expect(catalogMock).toHaveBeenCalledWith('private', 'CN'));
    // 报告页签只要目录索引，不依赖区间/榜单工具条
    expect(catalogMock).not.toHaveBeenCalledWith('classic', 'CN');

    await gotoReport();

    await waitFor(() => expect(catalogMock).toHaveBeenCalledWith('classic', 'CN'));
    await waitFor(() => expect(screen.getByTestId('loc-classic').getAttribute('data-hit')).toBe('1'));
    expect(screen.getByTestId('loc-private').getAttribute('data-hit')).toBe('1');
    expect(screen.getByTestId('loc-ghost').getAttribute('data-hit')).toBe('0');
  });

  test('反跳：落到单因子页的是这个因子、库切对了，不被「补第一个因子」顶掉', async () => {
    renderPage();
    await waitFor(() => expect(catalogMock).toHaveBeenCalledWith('private', 'CN'));
    await gotoReport();
    await waitFor(() => expect(screen.getByTestId('loc-classic').getAttribute('data-hit')).toBe('1'));

    fireEvent.click(screen.getByText('桩-反跳经典'));

    const single = await screen.findByTestId('single');
    await waitFor(() => expect(single.getAttribute('data-dataset')).toBe('classic'));
    // 排掉慢一拍的回写：若 activeCode 被切库 effect 清掉，新库榜单落地后
    // 「补第一个因子」会把 AUTO_FIRST 顶上来 —— 只断言首帧会漏掉它。
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(single.getAttribute('data-code')).toBe('ABTURN');
  });
});
