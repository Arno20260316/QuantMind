/**
 * 热力矩形图谱着色纯函数测试
 *
 * A 股口径：涨红跌绿。阈值语义要与「涨/跌停=±10%」对齐，
 * 且 `flow_net` / 面积为**亿元**（与后端 /heatmap/drill 契约一致）。
 */
import { describe, it, expect } from 'vitest';
import {
  pctToColor,
  flowToColor,
  heatmapCellColor,
  heatmapLabelValue,
  HEATMAP_COLOR_LABEL,
} from '../components/heatmapColor';

describe('pctToColor', () => {
  it('涨用红、跌用绿（A 股口径）', () => {
    expect(pctToColor(3)).toMatch(/^#(dc2626|ef4444|f87171)$/);
    expect(pctToColor(-3)).toMatch(/^#(059669|10b981|34d399)$/);
  });

  it('涨停/跌停档位用最深色', () => {
    expect(pctToColor(10)).toBe('#dc2626');
    expect(pctToColor(11)).toBe('#dc2626');
    expect(pctToColor(-10)).toBe('#059669');
  });

  it('平盘用中性灰', () => {
    expect(pctToColor(0)).toBe('#94a3b8');
  });

  it('缺失/非法值按 0 处理（中性灰，不崩）', () => {
    expect(pctToColor(null)).toBe('#94a3b8');
    expect(pctToColor(undefined)).toBe('#94a3b8');
    expect(pctToColor(NaN)).toBe('#94a3b8');
  });
});

describe('flowToColor', () => {
  it('净流入红、净流出绿（单位：亿元）', () => {
    expect(flowToColor(2)).toBe('#dc2626');
    expect(flowToColor(0.5)).toBe('#f87171');
    expect(flowToColor(-0.5)).toBe('#34d399');
    expect(flowToColor(-2)).toBe('#059669');
  });

  it('零值中性灰', () => {
    expect(flowToColor(0)).toBe('#94a3b8');
  });
});

describe('heatmapCellColor', () => {
  it('按维度分派到对应取色器', () => {
    const item = { pct_change: 8, flow_net: -3 };
    expect(heatmapCellColor(item, 'pct')).toBe(pctToColor(8));
    expect(heatmapCellColor(item, 'flow')).toBe(flowToColor(-3));
  });
});

describe('heatmapLabelValue', () => {
  it('涨跌幅带符号 + 百分号', () => {
    expect(heatmapLabelValue({ pct_change: 3.14159 }, 'pct')).toBe('+3.14%');
    expect(heatmapLabelValue({ pct_change: -2 }, 'pct')).toBe('-2.00%');
  });

  it('主力净额带符号 + 亿', () => {
    expect(heatmapLabelValue({ flow_net: 1.234 }, 'flow')).toBe('+1.23亿');
    expect(heatmapLabelValue({ flow_net: -0.5 }, 'flow')).toBe('-0.50亿');
  });

  it('缺失一律显示破折号（不显示成 0）', () => {
    expect(heatmapLabelValue({ pct_change: null }, 'pct')).toBe('—');
    expect(heatmapLabelValue({ flow_net: undefined }, 'flow')).toBe('—');
  });
});

describe('HEATMAP_COLOR_LABEL', () => {
  it('两个维度都有中文文案', () => {
    expect(HEATMAP_COLOR_LABEL.pct).toBe('涨跌幅');
    expect(HEATMAP_COLOR_LABEL.flow).toBe('主力净额');
  });
});
