/**
 * 因子研究页 —— 市场分层的接线契约（2026-10-16）。
 *
 * 页面跟随全局市场（uiSlice.currentMarket），且头部左侧挂有本页自己的
 * MarketSelector（页内直切入口）：CN/HK/US/FUTURES 各一份快照集与目录；
 * CRYPTO 未接入 → 诚实阻断面（不发请求，也不假装有数据）。
 *
 * 这里守的是「换市场」这条缝上的三件静默错——坏了都不报错，只是**看着像对的**：
 *
 * 1. 换市场后请求必须带新市场。不带的话后端给 CN 默认（或新的 CN 数据），
 *    用户切到港股看到的还是 A 股因子，界面上没有任何异常；
 * 2. 数据集胶囊按市场收敛。经典库只有 A 股供给（catalog.py 内置清单），
 *    非 CN 面渲染了它，点进去得到的是一块永远空的榜；
 * 3. 换市场必须清掉上一市场的选择/榜单/目录缓存。不清的话新市场首帧会把
 *    旧市场的行喂出来，而它们**看起来**完全正常（同一批因子代码的域外数据）。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import FactorResearchPage from '../FactorResearchPage';
import { setMarket } from '../../../../store/slices/uiSlice';

const { catalogMock, leaderboardMock, marketRef, dispatchSpy } = vi.hoisted(() => ({
  catalogMock: vi.fn(),
  leaderboardMock: vi.fn(),
  marketRef: { value: 'CN' as string },
  dispatchSpy: vi.fn(),
}));

vi.mock('../../services/factorResearchService', async () => {
  const actual = await vi.importActual<typeof import('../../services/factorResearchService')>(
    '../../services/factorResearchService',
  );
  return { ...actual, getCatalog: catalogMock, getLeaderboard: leaderboardMock };
});

// 选择器对假 state 求值：市场取 marketRef.value（测试中途改它 + 强制重渲染 = 换市场）
vi.mock('../../../../store', () => ({
  useAppSelector: (sel: (s: unknown) => unknown) =>
    sel({ ui: { currentMarket: marketRef.value }, auth: { user: { is_admin: false } } }),
  // 页面头部现在挂真 MarketSelector（页内切换入口），它要 useAppDispatch。
  // 本文件只验证「点击 → dispatch 了正确的 setMarket」这一跳；状态推进由
  // marketRef 模拟（真实链路 e2e 探针守）。
  useAppDispatch: () => dispatchSpy,
}));

// 子组件桩：本文件测的是页面对市场的分派，不是各页签怎么画（各有各的用例）
vi.mock('../../components/CatalogSidebar', () => ({ CatalogSidebar: () => <div data-testid="sidebar" /> }));
vi.mock('../../components/LeaderboardTab', () => ({ LeaderboardTab: () => <div data-testid="tab-leaderboard" /> }));
vi.mock('../../components/SingleFactorTab', () => ({ SingleFactorTab: () => null }));
vi.mock('../../components/CompareTab', () => ({ CompareTab: () => null }));
vi.mock('../../components/ComposeTab', () => ({ ComposeTab: () => null }));
vi.mock('../../components/ScreeningTab', () => ({ ScreeningTab: () => null }));
vi.mock('../../components/RegisterToTrainingModal', () => ({ RegisterToTrainingModal: () => null }));
vi.mock('../../components/factor-report/FactorReportPanel', () => ({ FactorReportPanel: () => null }));
vi.mock('../../components/SnapshotPanel', () => ({ SnapshotPanel: () => null }));
vi.mock('../../components/ScanPanel', () => ({ ScanPanel: () => null }));

/** 换市场的唯一手段：改 marketRef.value 再强制重渲染（选择器在渲染期读它） */
let rerenderPage: () => void = () => undefined;

function Harness() {
  const [, setTick] = React.useState(0);
  rerenderPage = () => setTick((t) => t + 1);
  return <FactorResearchPage />;
}

const switchMarket = async (market: string) => {
  await act(async () => {
    marketRef.value = market;
    rerenderPage();
  });
};

/** 页内市场指示 = 头部切换器里 aria-checked 的那枚 radio（取代旧静态标签） */
const activeMarketLabel = () =>
  screen
    .getByTestId('research-market-switcher')
    .querySelector('button[aria-checked="true"]')
    ?.textContent?.trim();

/** 页内切换器的某枚 radio（如「美股」） */
const pageMarketRadio = (label: string) =>
  within(screen.getByTestId('research-market-switcher')).getByRole('radio', { name: label });

const catalogCalls = () => catalogMock.mock.calls.map((c) => [c[0], c[1]]);
const leaderboardCalls = () => leaderboardMock.mock.calls.map((c) => [c[2], c[4]]);

const renderPage = () =>
  render(
    <MemoryRouter>
      <Harness />
    </MemoryRouter>,
  );

beforeEach(() => {
  catalogMock.mockReset();
  leaderboardMock.mockReset();
  dispatchSpy.mockClear();
  marketRef.value = 'CN';
  catalogMock.mockResolvedValue({ factors: [], l1_order: [], l2_order: {}, meta: {} });
  leaderboardMock.mockResolvedValue({ leaderboard: [], meta: {} });
});

describe('FactorResearchPage：市场分层', () => {
  test('CN：两枚数据集胶囊都在，请求带 CN', async () => {
    renderPage();

    expect(activeMarketLabel()).toBe('A股');
    expect(screen.getByText('经典因子')).toBeTruthy();
    expect(screen.getByText('私人因子库')).toBeTruthy();
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'CN']));
    expect(leaderboardCalls().every(([, mk]) => mk === 'CN')).toBe(true);
  });

  test('非 CN（港股）：只给私人库胶囊，经典库不渲染（它只有 A 股供给）', async () => {
    marketRef.value = 'HK';
    renderPage();

    expect(activeMarketLabel()).toBe('港股');
    expect(screen.queryByText('经典因子')).toBeNull();
    expect(screen.getByText('私人因子库')).toBeTruthy();
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'HK']));
    await waitFor(() => expect(leaderboardCalls()).toContainEqual(['private', 'HK']));
  });

  test('换市场：目录/榜单用新市场重取，页内切换器高亮跟着走', async () => {
    renderPage();
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'CN']));

    await switchMarket('US');

    expect(activeMarketLabel()).toBe('美股');
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'US']));
    await waitFor(() => expect(leaderboardCalls()).toContainEqual(['private', 'US']));
  });

  // 用户报障场景（2026-10-16）：「切美股，因子研究应该切美股」——页内切换器
  // 是这条链的头一跳。half-wired（点了没 dispatch / dispatch 错 payload）不报错，
  // 只是页面完全不动；这里钉死 click → setMarket(US) 这一跳。
  test('页内切换器：点「美股」radio 即 dispatch setMarket(US)', async () => {
    renderPage();

    // act 包住：radio 点击还会启动 framer-motion 高亮滑块动画（内部 state 更新）
    await act(async () => {
      fireEvent.click(pageMarketRadio('美股'));
    });

    expect(dispatchSpy).toHaveBeenCalledWith(setMarket('US'));
  });

  test('停在经典库时换市场：数据集回落到私人库，不拿经典库去问非 CN 市场', async () => {
    renderPage();
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'CN']));

    fireEvent.click(screen.getByText('经典因子'));
    await waitFor(() => expect(catalogCalls()).toContainEqual(['classic', 'CN']));

    await switchMarket('US');

    // 经典库是 CN 内置清单：换到非 CN 后一个 classic 请求都不该再发
    await waitFor(() => expect(catalogCalls()).toContainEqual(['private', 'US']));
    expect(catalogCalls().filter(([ds]) => ds === 'classic')).toEqual([['classic', 'CN']]);
    expect(screen.queryByText('经典因子')).toBeNull();
  });

  test('CRYPTO 未接入：渲染阻断面且一个请求都不发（不拿别市场数据充数）', async () => {
    marketRef.value = 'CRYPTO';
    renderPage();

    expect(screen.getByTestId('research-market-blocked')).toBeTruthy();
    expect(screen.queryByTestId('tab-leaderboard')).toBeNull();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(catalogMock).not.toHaveBeenCalled();
    expect(leaderboardMock).not.toHaveBeenCalled();
  });
});
