/**
 * 因子报告常驻头「单因子分析」反跳入口的行为线（2026-10-15）。
 *
 * 正向入口（单因子页 → 因子报告）在 SingleFactorTab；反向（报告 → 单因子）在这里，
 * 两者对称。守两件事：
 *
 * 1. **给了 onOpenSingle 才渲染**。父级只在「这个因子在单因子目录里真有其位」时才
 *    传回调 —— 查不到还渲染入口，用户点过去只会得到一屏「该因子不可用」，
 *    入口本身就是个谎；
 * 2. title 指向**当前因子**：换因子后按钮还在旧目标上时，点它就会跳到刚离开的
 *    那个因子（与正向入口同一条纪律）。
 */
import React from 'react';
import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FactorHeadline } from '../detail/FactorHeadline';

function renderHeadline(onOpenSingle?: () => void) {
  render(
    <FactorHeadline
      factor="amt_log"
      summary={null}
      library={[]}
      blocks={null}
      nDates={100}
      start="2026-01-01"
      end="2026-09-30"
      longGroup={3}
      shortGroup={9}
      costBps={20}
      bench="000300.SH"
      onParams={() => {}}
      definitions={{}}
      staleSchema={false}
      onOpenSingle={onOpenSingle}
    />,
  );
}

describe('FactorHeadline：「单因子分析」反跳入口', () => {
  test('onOpenSingle 给出时渲染入口，title 指向当前因子，点击回调一次', () => {
    const fn = vi.fn();
    renderHeadline(fn);

    const btn = screen.getByTestId('open-single-factor');
    expect(btn.getAttribute('title')).toContain('amt_log');
    fireEvent.click(btn);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  test('不给 onOpenSingle：不渲染入口（因子不在单因子目录里时由父级不传实现）', () => {
    renderHeadline();
    expect(screen.queryByTestId('open-single-factor')).toBeNull();
  });
});
