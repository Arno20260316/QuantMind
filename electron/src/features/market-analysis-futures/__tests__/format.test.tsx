/**
 * 期货模块展示词汇与 PctBar 口径单测。
 *
 * 目的：把「展示面只说位置」的用词纪律与后端枚举映射钉住——
 * 后端 tiers（leading/mid/lagging）、库存周期（build/draw/flat）、
 * 新鲜度（fresh/stale/unavailable）与前端徽标一一对应，防止回归到
 * 「前 20%/后 20%」这类与生成路径不符的标签。
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { CYCLE_META, FRESHNESS_META, TIER_META, PctBar, fmtPctS } from '../format';
import { TIER_LABEL, type OutlookTier } from '../types';

describe('TIER_LABEL / TIER_META（前瞻三档用词）', () => {
  const TIERS: OutlookTier[] = ['leading', 'mid', 'lagging'];

  it('应该覆盖后端全部三档，且标签为位置词', () => {
    expect(TIER_LABEL.leading).toBe('靠前');
    expect(TIER_LABEL.mid).toBe('居中');
    expect(TIER_LABEL.lagging).toBe('靠后');
  });

  it('TIER_META 与 TIER_LABEL 对每一档一致', () => {
    for (const t of TIERS) {
      expect(TIER_META[t]).toBeDefined();
      expect(TIER_META[t].label).toBe(TIER_LABEL[t]);
    }
  });

  it('标签不得出现百分比阈值或买卖执行词', () => {
    for (const t of TIERS) {
      const label = TIER_META[t].label;
      expect(label).not.toMatch(/[%％]/);
      expect(label).not.toMatch(/[前后]\s*\d/);
      expect(label).not.toMatch(/[买卖]/);
    }
  });
});

describe('CYCLE_META（库存周期三态）', () => {
  it('应该覆盖 build/draw/flat 且含口径 hint', () => {
    for (const k of ['build', 'draw', 'flat'] as const) {
      expect(CYCLE_META[k]).toBeDefined();
      expect(CYCLE_META[k].hint.length).toBeGreaterThan(0);
    }
    expect(CYCLE_META.build.label).toBe('累库');
    expect(CYCLE_META.draw.label).toBe('去库');
    expect(CYCLE_META.flat.label).toBe('走平');
  });
});

describe('FRESHNESS_META（数据集新鲜度三色）', () => {
  it('应该覆盖 fresh/stale/unavailable', () => {
    expect(FRESHNESS_META.fresh.label).toBe('新鲜');
    expect(FRESHNESS_META.stale.label).toBe('滞后');
    expect(FRESHNESS_META.unavailable.label).toBe('缺失');
  });
});

describe('PctBar（0-100 分位条）', () => {
  it('缺失分位应画空槽且 showValue 显示「—」', () => {
    const { container } = render(<PctBar pct={null} showValue />);
    expect(container.textContent).toContain('—');
    // 无填充段（仅轨道）
    expect(container.querySelectorAll('span.block.h-full').length).toBe(0);
  });

  it('undefined 与 null 等价处理', () => {
    const { container } = render(<PctBar pct={undefined} showValue />);
    expect(container.textContent).toContain('—');
  });

  it('高分位（≥67）用 amber-600，低分位（≤33）用 amber-200，中间用 amber-400', () => {
    const hi = render(<PctBar pct={80} />).container.querySelector('span.block.h-full');
    expect(hi?.className).toContain('bg-amber-600');
    const lo = render(<PctBar pct={20} />).container.querySelector('span.block.h-full');
    expect(lo?.className).toContain('bg-amber-200');
    const mid = render(<PctBar pct={50} />).container.querySelector('span.block.h-full');
    expect(mid?.className).toContain('bg-amber-400');
  });

  it('越界分位应钳制到 0-100 宽度', () => {
    const over = render(<PctBar pct={130} />).container.querySelector('span.block.h-full') as HTMLElement;
    expect(over.style.width).toBe('100%');
    const under = render(<PctBar pct={-20} />).container.querySelector('span.block.h-full') as HTMLElement;
    expect(under.style.width).toBe('0%');
  });

  it('showValue 保留整数展示', () => {
    const { container } = render(<PctBar pct={67.6} showValue />);
    expect(container.textContent).toContain('68');
  });
});

describe('fmtPctS（带号百分数）', () => {
  it('正负号与缺失口径', () => {
    expect(fmtPctS(1.234)).toBe('+1.23%');
    expect(fmtPctS(-0.5)).toBe('-0.50%');
    expect(fmtPctS(null)).toBe('—');
  });
});
