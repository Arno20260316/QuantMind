/** 期货市场分析 · echarts 生命周期胶囊
 *
 * 统一 init / setOption(notMerge) / ResizeObserver / dispose；调用方用
 * useMemo 构建 option 传入即可。option 为 null 时渲染空态提示。
 */

import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

/** 轴标签统一样式 */
export const AXIS_LABEL = { fontSize: 10, color: '#94a3b8', fontFamily: 'monospace' };

/** tooltip 统一底色 */
export const TOOLTIP_BASE = {
  trigger: 'axis' as const,
  backgroundColor: 'rgba(255,255,255,0.96)',
  borderColor: '#e2e8f0',
  textStyle: { fontSize: 11, color: '#334155' },
};

export const FuturesMiniChart: React.FC<{
  option: echarts.EChartsOption | null;
  height: number;
  emptyText?: string;
  className?: string;
}> = ({ option, height, emptyText = '暂无数据', className = '' }) => {
  const ref = useRef<HTMLDivElement>(null);
  const instRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !option) return;
    if (!instRef.current) instRef.current = echarts.init(el);
    instRef.current.setOption(option, true);
    const ro = new ResizeObserver(() => instRef.current?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [option]);

  useEffect(
    () => () => {
      instRef.current?.dispose();
      instRef.current = null;
    },
    [],
  );

  if (!option) {
    return (
      <div
        className={`grid place-items-center text-[11px] text-slate-400 font-medium ${className}`}
        style={{ height }}
      >
        {emptyText}
      </div>
    );
  }
  return <div ref={ref} className={className} style={{ width: '100%', height }} />;
};
