/**
 * 排行榜「自选」契约（2026-10-10；市场分层 2026-10-16）：
 *
 * 用户：几百个因子里挑出来看的就那十几个，每次都要重新翻。
 * 于是每行最前面加 ★ 收藏（按**市场 × 数据集**持久化到 localStorage），筛选栏给
 * 「自选 N」开关一键只看收藏；「清除筛选」连自选视图一起复位。
 * 点星标只切换收藏，**不触发**行跳转（和行内标签芯片同一条纪律）。
 *
 * 市场必须进键：各市场代码空间不同（A 股 6 位 / 港股 5 位），共用键会把别市场的
 * 代码算进「自选 N」，筛出来的行还一条都对不上——**静默错**，界面上完全看不出来。
 * 旧键（无市场段）只在 CN 且新键从未写过时迁移一次。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LeaderboardTab } from '../LeaderboardTab';
import type { LeaderboardRow } from '../../types/factorResearch';

function row(over: Partial<LeaderboardRow>): LeaderboardRow {
  return {
    code: 'x',
    rank: 1,
    name_cn: 'x',
    l1: '大类',
    l2: '子类',
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
    ...over,
  } as LeaderboardRow;
}

const ROWS: LeaderboardRow[] = [
  row({ code: 'FAV_1', name_cn: '动量A' }),
  row({ code: 'FAV_2', name_cn: '反转B' }),
  row({ code: 'FAV_3', name_cn: '波动C' }),
];

/** CN 私人库的收藏键（与因子报告左栏的键是两套集合，互不串） */
const KEY = 'qm:factor-research:lb-favs:CN:private';

function renderTab(over: Partial<React.ComponentProps<typeof LeaderboardTab>> = {}) {
  return render(
    <LeaderboardTab
      rows={ROWS}
      loading={false}
      error={null}
      selected={[]}
      tagFilter={[]}
      categoryFilter={null}
      onClearCategoryFilter={vi.fn()}
      n={30}
      onNChange={vi.fn()}
      onToggle={vi.fn()}
      onToggleTag={vi.fn()}
      onSendCompare={vi.fn()}
      onSendCompose={vi.fn()}
      onOpenSingle={vi.fn()}
      meta={{}}
      dataset="private"
      market="CN"
      dedup={false}
      onToggleDedup={vi.fn()}
      {...over}
    />,
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe('LeaderboardTab：自选收藏', () => {
  test('点行首星标收藏/取消：写入 localStorage（按数据集），计数同步', () => {
    renderTab();

    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));
    expect(JSON.parse(localStorage.getItem(KEY) || '[]')).toEqual(['FAV_1']);
    expect(screen.getByTestId('lb-favs-only').textContent).toContain('1');

    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));
    expect(JSON.parse(localStorage.getItem(KEY) || '[]')).toEqual([]);
    expect(screen.getByTestId('lb-favs-only').textContent).toContain('0');
  });

  test('「只看自选」：列表只剩收藏行，再点恢复全量', () => {
    renderTab();
    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));
    fireEvent.click(screen.getByTestId('lb-fav-FAV_3'));

    fireEvent.click(screen.getByTestId('lb-favs-only'));
    expect(screen.getByText('动量A')).toBeTruthy();
    expect(screen.getByText('波动C')).toBeTruthy();
    expect(screen.queryByText('反转B')).toBeNull();

    fireEvent.click(screen.getByTestId('lb-favs-only'));
    expect(screen.getByText('反转B')).toBeTruthy();
  });

  test('「清除筛选」把只看自选也一并复位', () => {
    renderTab();
    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));
    fireEvent.click(screen.getByTestId('lb-favs-only'));
    expect(screen.queryByText('反转B')).toBeNull();

    fireEvent.click(screen.getByTestId('lb-clear-filters'));
    expect(screen.getByText('反转B')).toBeTruthy();
  });

  test('数据集隔离：经典库的收藏不出现在私人库', () => {
    localStorage.setItem('qm:factor-research:lb-favs:CN:classic', JSON.stringify(['FAV_1']));
    renderTab(); // dataset="private"

    expect(screen.getByTestId('lb-favs-only').textContent).toContain('0');
  });

  test('市场隔离：港股的自选不出现在 A 股（代码空间不同，混算就是虚高）', () => {
    localStorage.setItem('qm:factor-research:lb-favs:HK:private', JSON.stringify(['00700']));
    renderTab(); // market="CN"

    expect(screen.getByTestId('lb-favs-only').textContent).toContain('0');
    // 也读不到别市场的集合：CN 点收藏写的是 CN 键
    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));
    expect(JSON.parse(localStorage.getItem(KEY) || '[]')).toEqual(['FAV_1']);
    expect(JSON.parse(localStorage.getItem('qm:factor-research:lb-favs:HK:private') || '[]')).toEqual(['00700']);
  });

  test('CN：旧键（无市场段）里的收藏搬进新键，一次迁移不丢', () => {
    localStorage.setItem('qm:factor-research:lb-favs:private', JSON.stringify(['FAV_1']));

    renderTab(); // CN

    expect(screen.getByTestId('lb-favs-only').textContent).toContain('1');
    expect(JSON.parse(localStorage.getItem(KEY) || '[]')).toEqual(['FAV_1']);
  });

  test('非 CN 不搬旧键（旧键里只可能是 A 股代码，搬过去就是让「自选 N」虚高）', () => {
    localStorage.setItem('qm:factor-research:lb-favs:private', JSON.stringify(['FAV_1']));

    renderTab({ market: 'HK' });

    expect(screen.getByTestId('lb-favs-only').textContent).toContain('0');
    expect(localStorage.getItem('qm:factor-research:lb-favs:HK:private')).toBeNull();
  });

  test('一个收藏都没有时打开「只看自选」：给出怎么收藏的提示', () => {
    renderTab();
    fireEvent.click(screen.getByTestId('lb-favs-only'));

    expect(screen.getByText(/还没有收藏因子/)).toBeTruthy();
  });

  test('点星标不触发行跳转', () => {
    const onOpenSingle = vi.fn();
    renderTab({ onOpenSingle });

    fireEvent.click(screen.getByTestId('lb-fav-FAV_1'));

    expect(onOpenSingle).not.toHaveBeenCalled();
  });
});
