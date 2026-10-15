/**
 * 排行榜「去重」契约（2026-10-15）：
 *
 * 用户：「因子排行增加一个去重选择按钮、就是筛选那一样、去掉重复的就行。然后重新排名」。
 * 于是筛选栏给一个去重开关，口径与筛选页一致——逐月截面 Spearman 均值 |ρ| ≥ 0.9
 * 的同源因子只留榜单更靠前的一个，剔除后由后端重新编号名次。
 *
 * 这里守三条：
 * 1. 开关默认关、点击把切换交回页面（组件不自己管状态、不自己过滤——被剔的行与
 *    新名次都来自带 dedup 参数重新请求的响应，本地不可能算对）；
 * 2. 开启后按钮必须带上生效证据「剔除 N」（来自响应 meta.dedup.dropped）——只变色
 *    不报数，用户分辨不出「真去重了」和「矩阵没覆盖、一个都没剔」；
 * 3. meta 缺失（请求失败/旧缓存）时按钮照常工作，但不显示剔除计数——不拿 0 撒谎。
 */
import React from 'react';
import { describe, test, expect, vi } from 'vitest';
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
  row({ code: 'MOM_A', name_cn: '动量A' }),
  row({ code: 'REV_B', name_cn: '反转B' }),
];

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
      dedup={false}
      onToggleDedup={vi.fn()}
      {...over}
    />,
  );
}

describe('LeaderboardTab：去重开关', () => {
  test('默认关闭：芯片在筛选栏、标出判据 |ρ|≥0.9、不显示剔除计数', () => {
    renderTab();

    const chip = screen.getByTestId('lb-dedup');
    expect(chip.getAttribute('aria-pressed')).toBe('false');
    expect(chip.textContent).toContain('去重');
    expect(chip.textContent).toContain('|ρ|≥0.9');
    expect(chip.textContent).not.toContain('剔除');
  });

  test('点击把切换交回页面（状态与取数都归页面，组件只发事件）', () => {
    const onToggleDedup = vi.fn();
    renderTab({ onToggleDedup });

    fireEvent.click(screen.getByTestId('lb-dedup'));

    expect(onToggleDedup).toHaveBeenCalledTimes(1);
  });

  test('开启且响应 meta 生效：按钮报出「剔除 N」（去重不是只变色，要有实数）', () => {
    renderTab({ dedup: true, meta: { dedup: { applied: true, dropped: 12 } } });

    const chip = screen.getByTestId('lb-dedup');
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(chip.textContent).toContain('剔除 12');
    expect(chip.getAttribute('title')).toContain('取消去重');
  });

  test('meta 缺失（请求失败/冷启动）时不显示剔除计数——不拿 0 当「零剔除」', () => {
    renderTab({ dedup: true });

    const chip = screen.getByTestId('lb-dedup');
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(chip.textContent).not.toContain('剔除');
  });

  test('meta 未生效（applied=false）时同样不报数', () => {
    renderTab({ dedup: true, meta: { dedup: { applied: false } } });

    expect(screen.getByTestId('lb-dedup').textContent).not.toContain('剔除');
  });
});
