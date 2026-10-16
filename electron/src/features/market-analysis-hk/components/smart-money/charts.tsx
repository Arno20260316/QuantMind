/** 港股聪明钱 · ECharts 薄封装（图表纪律唯一主题出处，对齐 A 股 SmartChart）。
 *
 * 纪律（各面一律经这里出图，不再就地拼 option 主题）：
 * - 缺失值断线**绝不补 0**：data 里 null → ECharts 断点，`connectNulls` 钉死 false；
 * - 港股红涨绿跌（pos/neg）只用于涨跌与金额正负；技能/IC/命中率是统计量，
 *   用中性色（neutral/score 紫阶），绝不套红绿；
 * - 字号 9–10、网格线浅灰，与面板其余卡片密度一致；
 * - tooltip 由各图自备 formatter（承载细节），但空值行统一显示「—」。
 */

import React from 'react';
import ReactECharts from 'echarts-for-react';
import { MISSING } from '../../hkSmartMoneyVocabulary';

/** 全站图表色板（港股口径红涨绿跌；统计量走中性紫/灰） */
export const CHART_COLORS = {
  pos: '#dc2626',
  neg: '#16a34a',
  score: '#7c3aed',
  scoreFill: 'rgba(124,58,237,0.10)',
  band: 'rgba(124,58,237,0.05)',
  neutral: '#64748b',
  sub: '#94a3b8',
  grid: '#f1f5f9',
  axis: '#cbd5e1',
} as const;

/** 数值正负 → 色（涨跌/金额/技能数值口径；0 与 null 走中性） */
export function signedColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v)) || Number(v) === 0) {
    return CHART_COLORS.neutral;
  }
  return Number(v) > 0 ? CHART_COLORS.pos : CHART_COLORS.neg;
}

/** 通用坐标轴样式 */
export const AXIS_LABEL = { fontSize: 9, color: CHART_COLORS.neutral } as const;
export const SPLIT_LINE = { lineStyle: { color: CHART_COLORS.grid } } as const;

/** 通用网格（紧凑布局；窄卡内也放得下） */
export const BASE_GRID = {
  left: 42,
  right: 12,
  top: 16,
  bottom: 20,
} as const;

/** 缺失安全 tooltip 数值格式（null → 「—」，绝不出 0） */
export function tipNum(
  v: number | null | undefined,
  fmt: (n: number) => string,
): string {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? MISSING : fmt(Number(v));
}

/** ECharts 事件参数（点击热力单元格取 data 数组；其余图不用） */
export interface ChartEventParams {
  data?: unknown;
  name?: string;
  seriesIndex?: number;
}

interface SmartChartProps {
  /** ECharts option（主题经本模块常量拼装） */
  option: Record<string, unknown> | null;
  /** 图高（px）；默认 160（卡内小图） */
  height?: number;
  /** 空态文案（option 为 null 时渲染；不传则渲染空占位） */
  emptyText?: string;
  className?: string;
  /** 交互事件（如热力图 click 联动）；键 = ECharts 事件名 */
  onEvents?: Record<string, (params: ChartEventParams) => void>;
}

/** 薄封装：响应式 + 不合并旧 option（切换数据源防残留）+ 空态。 */
export const SmartChart: React.FC<SmartChartProps> = ({
  option,
  height = 160,
  emptyText,
  className,
  onEvents,
}) => {
  if (!option) {
    return (
      <div
        className={`grid place-items-center text-[10px] text-slate-300 font-bold ${className ?? ''}`}
        style={{ height }}
      >
        {emptyText ?? MISSING}
      </div>
    );
  }
  return (
    <ReactECharts
      option={option}
      notMerge
      lazyUpdate
      style={{ height, width: '100%' }}
      className={className}
      onEvents={onEvents}
    />
  );
};
