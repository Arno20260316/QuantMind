/**
 * 市场分析 · 板块热力矩形图谱 + 个股下钻容器
 *
 * 两层结构（同一张图内切换，不是 ECharts 嵌套 treemap）：
 *   L1 板块层：通达信二级行业 / 一级行业 / 概念板块（面积=板块总市值，颜色=涨跌幅或主力净额）
 *   L2 个股层：点板块钻取成分股（面积=个股总市值，颜色同口径），面包屑返回 L1
 *
 * 为什么不用 ECharts 原生嵌套 treemap：板块层组件本就在与 ECharts 内部布局 API
 * 打交道（标签居中），再嵌一层会把 hack 复杂度翻倍；用 React 状态驱动分层 + 面包屑，
 * 返回行为、颜色切换、逐块取色都更可控（可维护性优先）。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Layers, ChevronRight, ArrowLeft, Loader2 } from 'lucide-react';
import {
  SectorHeatmapChart,
  type SectorHeatmapItem,
} from '../../market-analysis-shared/SectorHeatmapChart';
import {
  getHeatmapDrill,
  getHeatmapTop,
  type HeatmapDrillCategory,
  type HeatmapDrillResponse,
} from '../services/moneyFlowService';
import {
  heatmapCellColor,
  heatmapLabelValue,
  HEATMAP_COLOR_LABEL,
  type HeatmapColorMode,
} from './heatmapColor';

/** 顶层板块分类切换项（键名与后端 /heatmap 的 category 一致）。 */
const CATEGORY_TABS: { id: HeatmapDrillCategory; label: string }[] = [
  { id: 'shenwan', label: '二级行业' },
  { id: 'industry1', label: '一级行业' },
  { id: 'concept', label: '概念板块' },
];

interface HeatmapDrillCardProps {
  /** 顶层板块热力图数据（由页面预取，用于 shenwan 分类的首屏即时渲染） */
  topItems: SectorHeatmapItem[];
  /** 快照日期 YYYY-MM-DD（透传给下钻接口，支持历史回溯） */
  date?: string;
  /** 点击个股 → 打开个股资金流详情 */
  onStockClick?: (symbol: string, name: string) => void;
}

export const HeatmapDrillCard: React.FC<HeatmapDrillCardProps> = ({
  topItems,
  date,
  onStockClick,
}) => {
  const [category, setCategory] = useState<HeatmapDrillCategory>('shenwan');
  const [colorMode, setColorMode] = useState<HeatmapColorMode>('pct');
  const [drillName, setDrillName] = useState<string | null>(null);
  const [drill, setDrill] = useState<HeatmapDrillResponse | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  // 非 shenwan 分类的顶层数据需自行拉取（页面只预取了 shenwan）
  const [ownTop, setOwnTop] = useState<SectorHeatmapItem[]>([]);
  const [topLoading, setTopLoading] = useState(false);

  // 切分类时退出下钻层，避免「二级行业 › 概念板块的板块名」这种跨体系错配
  useEffect(() => {
    setDrillName(null);
    setDrill(null);
  }, [category]);

  // 顶层数据：shenwan 用页面预取值；其它分类走实时全量（快照对概念只存前 50，会漏板块）
  useEffect(() => {
    if (category === 'shenwan') return;
    let cancelled = false;
    setTopLoading(true);
    getHeatmapTop({ category, date, realtime: true })
      .then((items) => {
        if (!cancelled) setOwnTop(items);
      })
      .catch(() => {
        if (!cancelled) setOwnTop([]);
      })
      .finally(() => {
        if (!cancelled) setTopLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [category, date]);

  // 拉取下钻数据
  useEffect(() => {
    if (!drillName) return;
    let cancelled = false;
    setDrillLoading(true);
    getHeatmapDrill({ category, name: drillName, date })
      .then((d) => {
        if (!cancelled) setDrill(d);
      })
      .catch(() => {
        if (!cancelled) setDrill(null);
      })
      .finally(() => {
        if (!cancelled) setDrillLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [drillName, category, date]);

  const topData = category === 'shenwan' ? topItems : ownTop;

  // 个股层：名称 → symbol 映射（treemap 点击回传的是显示名）
  const symbolByName = useMemo(() => {
    const m = new Map<string, string>();
    (drill?.items || []).forEach((it) => m.set(it.name, it.symbol));
    return m;
  }, [drill]);

  const drillChartData: SectorHeatmapItem[] = useMemo(
    () =>
      (drill?.items || []).map((it) => ({
        name: it.name,
        value: it.value,
        pct_change: it.pct_change,
        flow_net: it.flow_net,
        leader: undefined,
      })) as SectorHeatmapItem[],
    [drill],
  );

  const handleTopClick = (name: string) => {
    setDrillName(name);
  };

  const handleDrillClick = (name: string) => {
    const symbol = symbolByName.get(name);
    if (symbol && onStockClick) onStockClick(symbol, name);
  };

  const renderKey = `${category}-${drillName || 'top'}-${colorMode}`;
  const chartData = drillName ? drillChartData : topData;
  const isEmpty = chartData.length === 0;

  return (
    <div className="h-full bg-white/95 backdrop-blur-md rounded-2xl p-3.5 border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col min-h-0">
      {/* 头部：标题 + 面包屑 + 分类/着色切换 */}
      <div className="flex items-center justify-between border-b border-purple-100/60 pb-2 mb-1 shrink-0 gap-2">
        <h3 className="text-xs font-extrabold text-slate-900 flex items-center gap-1.5 min-w-0">
          <Layers className="w-3.5 h-3.5 text-purple-600 shrink-0" />
          {drillName ? (
            <span className="flex items-center gap-1 min-w-0">
              <button
                type="button"
                onClick={() => setDrillName(null)}
                className="flex items-center gap-0.5 text-purple-600 hover:text-purple-800 hover:underline shrink-0"
                title="返回板块层"
              >
                <ArrowLeft className="w-3 h-3" />
                {CATEGORY_TABS.find((t) => t.id === category)?.label}
              </button>
              <ChevronRight className="w-3 h-3 text-slate-300 shrink-0" />
              <span className="truncate">{drillName}</span>
            </span>
          ) : (
            <span className="truncate">板块热力矩形图谱</span>
          )}
        </h3>
        <div className="flex items-center gap-1.5 shrink-0">
          {/* 着色维度切换（两层共用） */}
          <div className="flex rounded-full bg-slate-100 p-0.5">
            {(['pct', 'flow'] as HeatmapColorMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setColorMode(mode)}
                className={`px-2 py-0.5 rounded-full text-[10px] font-bold transition-colors ${
                  colorMode === mode ? 'bg-white text-purple-700 shadow-2xs' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {HEATMAP_COLOR_LABEL[mode]}
              </button>
            ))}
          </div>
          {/* 顶层分类切换（仅板块层显示） */}
          {!drillName && (
            <div className="flex rounded-full bg-slate-100 p-0.5">
              {CATEGORY_TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setCategory(t.id)}
                  className={`px-2 py-0.5 rounded-full text-[10px] font-bold transition-colors ${
                    category === t.id ? 'bg-white text-purple-700 shadow-2xs' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 副标题条：口径与规模如实披露 */}
      <div className="flex items-center justify-between px-0.5 pb-1 shrink-0">
        <span className="text-[10px] text-slate-400 font-mono">
          面积=总市值 · 颜色={HEATMAP_COLOR_LABEL[colorMode]}
          {colorMode === 'flow' && '（主力净额按单笔金额分档估算）'}
        </span>
        {drillName && drill && (
          <span className="text-[10px] text-slate-400 font-mono">
            {drill.trade_date} · {drill.summary.shown}/{drill.summary.total} 只
            {drill.summary.truncated ? '（按市值截断）' : ''} · 均涨 {drill.summary.avg_pct >= 0 ? '+' : ''}
            {drill.summary.avg_pct}%
          </span>
        )}
      </div>

      {/* 图区 */}
      <div className="flex-1 min-h-0 relative">
        {(drillLoading || topLoading) && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 backdrop-blur-xs">
            <Loader2 className="w-5 h-5 text-purple-500 animate-spin" />
          </div>
        )}
        {isEmpty && !drillLoading && !topLoading ? (
          <div className="w-full h-full flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200">
            {drillName ? '该板块暂无成分数据' : '暂无热力图数据'}
          </div>
        ) : (
          <SectorHeatmapChart
            data={chartData}
            height="100%"
            valueLabel={drillName ? '总市值' : '市值权重规模'}
            renderKey={renderKey}
            onItemClick={drillName ? handleDrillClick : handleTopClick}
            colorOf={(item) => heatmapCellColor(item, colorMode)}
            labelValueOf={(item) => heatmapLabelValue(item, colorMode)}
            tooltipExtraOf={(item) => {
              if (colorMode !== 'flow') return '';
              const flow = (item as { flow_net?: number }).flow_net;
              if (typeof flow !== 'number' || !Number.isFinite(flow)) return '';
              const color = flow >= 0 ? '#f87171' : '#34d399';
              return `<span style="color:#cbd5e1;">主力净额: <b style="color:${color};">${flow >= 0 ? '+' : ''}${flow.toFixed(2)}亿</b></span>`;
            }}
          />
        )}
      </div>
    </div>
  );
};
