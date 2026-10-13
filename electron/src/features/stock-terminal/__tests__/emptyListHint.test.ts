/**
 * 候选信号列表空态分类测试。
 *
 * 盯的是「把四种空因混成一句『无匹配股票』」这个体验缺陷。分类必须**只依赖后端事实**：
 * - 有分但被滤掉 ≠ 当日没分（前者的下一步是放宽条件，后者放宽也没用）；
 * - 选了模型且没分 ≠ 全市场没分（前者换模型即可，后者得去看推理失败）。
 */

import { describe, expect, it } from 'vitest';
import { emptyListHint, type EmptyListHintInput } from '../emptyListHint';

const base: EmptyListHintInput = {
  searching: false,
  query: '',
  signalRows: 1000,
  signalDate: '2026-10-12',
  filteredTotal: 50,
  narrowingLabels: [],
  sideApplied: false,
  onlyWatchlist: false,
};

describe('emptyListHint · 空列表分类', () => {
  it('搜索无命中时直说「代码或名称没匹配」，不牵扯筛选条件', () => {
    const h = emptyListHint({ ...base, searching: true, query: '  ZZ9999 ' });

    expect(h.title).toContain('ZZ9999');
    expect(h.title).toContain('代码或名称');
    expect(h.canClear).toBe(false);
  });

  it('signal_rows=0 且选了模型 → 是这款模型没分，下一步换模型', () => {
    const h = emptyListHint({ ...base, signalRows: 0, model: 'm_tft', filteredTotal: 0 });

    expect(h.title).toBe('这款模型当日没有打分');
    expect(h.detail).toContain('换一款模型');
    expect(h.canClear).toBe(false); // 放宽条件解决不了，不给误导性按钮
  });

  it('signal_rows=0 且未选模型 → 全市场当日没产分，下一步看系统健康', () => {
    const h = emptyListHint({ ...base, signalRows: 0, filteredTotal: 0 });

    expect(h.title).toContain('2026-10-12');
    expect(h.title).toContain('当日没有推理信号');
    expect(h.detail).toContain('系统健康');
    expect(h.detail).toContain('放宽筛选条件也不会有结果');
    expect(h.canClear).toBe(false);
  });

  it('signal_rows>0 且 total=0 → 有分但被条件滤掉，给出清除按钮', () => {
    const h = emptyListHint({
      ...base,
      filteredTotal: 0,
      narrowingLabels: ['板块 科创板', '趋势 连续上升'],
    });

    expect(h.title).toBe('有打分，但被当前筛选条件滤掉了');
    expect(h.detail).toContain('1000');
    expect(h.canClear).toBe(true);
  });

  it('候选列表的位置闸（侧向）也写进说明与标签', () => {
    const h = emptyListHint({ ...base, filteredTotal: 0, side: 'BUY', sideApplied: true });

    expect(h.labels).toContain('信号 靠前');
    expect(h.detail).toContain('靠前');
  });

  it('位置闸在检索/只看自选下让开时不摆出来（别把没生效的闸说成生效）', () => {
    const h = emptyListHint({ ...base, signalRows: 0, filteredTotal: 0, side: 'BUY', sideApplied: false });

    expect(h.labels).not.toContain('信号 靠前');
  });

  it('只看自选作为标签出现（它也是「为什么空」的一环）', () => {
    const h = emptyListHint({ ...base, signalRows: 0, filteredTotal: 0, onlyWatchlist: true });

    expect(h.labels).toContain('只看自选');
  });

  it('老后端无 signal_rows → 只给一句诚实的兜底，不假装知道空因', () => {
    const h = emptyListHint({ ...base, signalRows: undefined, filteredTotal: 0 });

    expect(h.title).toBe('当前条件下没有股票');
    expect(h.detail).toBe('');
    expect(h.canClear).toBe(false);
  });

  it('有分且被滤空但没有主动缩小条件 → 不渲染无意义的清除按钮', () => {
    const h = emptyListHint({ ...base, filteredTotal: 0, narrowingLabels: [] });

    expect(h.title).toBe('有打分，但被当前筛选条件滤掉了');
    expect(h.canClear).toBe(false);
  });
});
