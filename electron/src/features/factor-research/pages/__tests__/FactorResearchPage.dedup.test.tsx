/**
 * 因子研究页 × 真实 LeaderboardTab：去重开关的**接线契约**（2026-10-15）。
 *
 * 组件测试证明了芯片会发回调、service 测试证明了参数会进 URL，但**页面把两者
 * 接起来**这一段（回调 → state 翻转 → 带 dedup 重取数 → 视图换成去重后的行）
 * 谁都测不到：任何一处断线，界面上都只是「点了没反应」。
 *
 * 所以这里必须用**真的** LeaderboardTab（桩掉它，这条缝就又被藏起来了），
 * 假的是 service：按第 4 个参数 dedup 返回不同榜单，验证——
 * 1. 点击芯片后确实带着 dedup=0.9 重新请求；
 * 2. 回来的去重榜单（少了重复行、meta 带 dropped）真实反映到界面上；
 * 3. 再点一次取消，回到不带参数的请求。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import FactorResearchPage from '../FactorResearchPage';

const { catalogMock, leaderboardMock, statusMock, buildMock } = vi.hoisted(() => ({
  catalogMock: vi.fn(),
  leaderboardMock: vi.fn(),
  statusMock: vi.fn(),
  buildMock: vi.fn(),
}));

vi.mock('../../services/factorResearchService', async () => {
  const actual = await vi.importActual<typeof import('../../services/factorResearchService')>(
    '../../services/factorResearchService',
  );
  return {
    ...actual,
    getCatalog: catalogMock,
    getLeaderboard: leaderboardMock,
    getSnapshotStatus: statusMock,
    postBuildSnapshot: buildMock,
  };
});

// 页面读两个选择器：市场（uiSlice）与 is_admin（auth）。按选择器对假 state 求值，
// 别返回与语义无关的 false（那会让页面落到「市场未接入」阻断面 —— 断言失败时看不出根因）。
vi.mock('../../../../store', () => ({
  useAppSelector: (sel: (s: unknown) => unknown) =>
    sel({ ui: { currentMarket: 'CN' }, auth: { user: { is_admin: false } } }),
  // 页面头部挂了真 MarketSelector（页内市场切换入口）：它要 useAppDispatch。
  // 本文件不测切市场，给 no-op（点击→dispatch 的接线由 markets 用例守）。
  useAppDispatch: () => vi.fn(),
}));

vi.mock('../../components/CatalogSidebar', () => ({ CatalogSidebar: () => <div /> }));
vi.mock('../../components/SingleFactorTab', () => ({ SingleFactorTab: () => null }));
vi.mock('../../components/CompareTab', () => ({ CompareTab: () => null }));
vi.mock('../../components/ComposeTab', () => ({ ComposeTab: () => null }));
vi.mock('../../components/ScreeningTab', () => ({ ScreeningTab: () => null }));
vi.mock('../../components/RegisterToTrainingModal', () => ({ RegisterToTrainingModal: () => null }));
vi.mock('../../components/factor-report/FactorReportPanel', () => ({ FactorReportPanel: () => null }));
vi.mock('../../components/ScanPanel', () => ({ ScanPanel: () => null }));
// LeaderboardTab 不桩 —— 它正是被测的那一段接线

function row(code: string, name: string, rank: number) {
  return {
    code,
    rank,
    name_cn: name,
    l1: '私人因子库',
    l2: 'alpha_library',
    composite: 1,
    annual_return: 0.1,
    sharpe: 1,
    max_drawdown: -0.1,
    win_rate: 0.5,
    excess_300: 0.01,
    excess_800: 0.01,
    ic_mean: 0.03,
    ic_ir: 0.3,
    median_mv_yi: 100,
    env_tag: '',
    time_tag: '',
  };
}

/** 未去重：两行（MOM 与它的重复同源 DUP）；去重：只剩 MOM、meta 报剔除 1。 */
const FULL = { leaderboard: [row('MOM_A', '动量A', 1), row('DUP_B', '重复B', 2)], meta: {} };
const DEDUPED = {
  leaderboard: [row('MOM_A', '动量A', 1)],
  meta: { dedup: { applied: true, threshold: 0.9, n_before: 2, dropped: 1, covered: 2 } },
};

const lastDedupArg = () => {
  const calls = leaderboardMock.mock.calls;
  return calls.length ? calls[calls.length - 1][3] : undefined;
};

beforeEach(() => {
  catalogMock.mockReset();
  leaderboardMock.mockReset();
  statusMock.mockReset();
  buildMock.mockReset();
  catalogMock.mockResolvedValue({ factors: [], l1_order: [], l2_order: {}, meta: {} });
  // 按 dedup 参数分流：0/undefined → 全量榜单；>0 → 去重榜单
  leaderboardMock.mockImplementation(async (_r, _n, _ds, dedup) => (dedup ? DEDUPED : FULL));
  statusMock.mockResolvedValue({
    exists: true,
    running: false,
    built_at: '2026-10-15 09:00:00',
    window: ['2016-01-04', '2026-10-14'],
    n_factors: 4011,
    n_dates: 129,
    step: 'done',
    log_tail: [],
  });
  buildMock.mockResolvedValue({ started: true, running: true });
});

describe('FactorResearchPage：去重开关接线', () => {
  test('点「去重 |ρ|≥0.9」：带 0.9 重取数，视图换成去重榜单且报出剔除数', async () => {
    render(
      <MemoryRouter>
        <FactorResearchPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText('重复B')).toBeTruthy();
    expect(lastDedupArg()).toBeFalsy(); // 首屏未去重

    fireEvent.click(screen.getByTestId('lb-dedup'));

    await waitFor(() => expect(lastDedupArg()).toBe(0.9));
    await waitFor(() => expect(screen.queryByText('重复B')).toBeNull()); // 被剔的行不再显示
    expect(await screen.findByText(/剔除 1/)).toBeTruthy(); // meta.dropped 上屏
  });

  test('再点一次取消去重：回到不带 dedup 的请求，被剔的行回来', async () => {
    render(
      <MemoryRouter>
        <FactorResearchPage />
      </MemoryRouter>,
    );
    await screen.findByText('重复B');

    fireEvent.click(screen.getByTestId('lb-dedup'));
    await waitFor(() => expect(lastDedupArg()).toBe(0.9));
    await waitFor(() => expect(screen.queryByText('重复B')).toBeNull());

    fireEvent.click(screen.getByTestId('lb-dedup'));
    await waitFor(() => expect(lastDedupArg()).toBeFalsy());
    expect(await screen.findByText('重复B')).toBeTruthy();
  });
});
