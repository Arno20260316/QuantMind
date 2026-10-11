/**
 * 板块⇄个股 双向联动工作台（细案 v2 §5）。
 *
 * 左 master=板块榜：**默认排序 = 延续状态驱动**（streak≥2 命中在前 → 连流天数 →
 * 净额），不按裸净额排（预研结论：裸净额大小无预测力）；行 = 排位 + 名称 +
 * 「连N」徽章（P，hover 证据卡）+ 双向净额条 + 净额比 + 涨幅 + 成分数。
 * 右 detail=板块明细：窗口汇总 + 20 日趋势 + 成分贡献榜（点击成分 → 个股详情卡）。
 */

import React, { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import { Spin } from 'antd';
import { Layers, ChevronRight } from 'lucide-react';
import {
  fmtYi,
  fmtPct,
  fmtRatio,
  flowTextClass,
  type FlowSectorDetailResponse,
  type FlowSectorRow,
} from '../services/moneyFlowService';
import { EvidenceBadge } from './EvidenceBadge';

const INFLOW_COLOR = '#e11d48';
const OUTFLOW_COLOR = '#059669';

export interface SectorFlowWorkbenchProps {
  rows: FlowSectorRow[];
  selectedSector: string | null;
  onSelectSector: (name: string) => void;
  detail: FlowSectorDetailResponse | null;
  detailLoading: boolean;
  detailError: string | null;
  onSelectStock: (symbol: string, name: string) => void;
  compact?: boolean;
}

/** 双向净额条：以 0 为轴心，红向右（净流入）、绿向左（净流出）。 */
const BipolarBar: React.FC<{ value: number; maxAbs: number; width?: string }> = ({ value, maxAbs, width = 'w-20' }) => {
  const pct = maxAbs > 0 ? Math.min(50, (Math.abs(value) / maxAbs) * 50) : 0;
  return (
    <div className={`${width} h-2 bg-slate-100 rounded-full relative overflow-hidden shrink-0`}>
      <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
      <div
        className={`absolute inset-y-0 rounded-full ${value >= 0 ? 'bg-rose-500/80' : 'bg-emerald-500/80'}`}
        style={
          value >= 0
            ? { left: '50%', width: `${pct}%` }
            : { right: '50%', width: `${pct}%` }
        }
      />
    </div>
  );
};

const TrendChart: React.FC<{ trend: FlowSectorDetailResponse['trend']; height?: number }> = ({ trend, height = 130 }) => {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    if (!inst.current) inst.current = echarts.init(ref.current);
    const chart = inst.current;
    chart.setOption(
      {
        grid: { left: 40, right: 40, top: 16, bottom: 18 },
        tooltip: {
          trigger: 'axis',
          formatter: (ps: any[]) => {
            const idx = ps?.[0]?.dataIndex ?? 0;
            const p = trend[idx];
            if (!p) return '';
            return (
              `<div style="font-size:11px"><b>${p.date}</b><br/>主力净额：` +
              `<b style="color:${p.net_main >= 0 ? INFLOW_COLOR : OUTFLOW_COLOR}">${p.net_main >= 0 ? '+' : ''}${p.net_main.toFixed(2)} 亿</b><br/>` +
              `净额比：${p.net_ratio === null ? '—' : `${(p.net_ratio * 100).toFixed(2)}%`}</div>`
            );
          },
        },
        xAxis: {
          type: 'category',
          data: trend.map((t) => t.date.slice(5)),
          axisLabel: { fontSize: 9, color: '#94a3b8' },
          axisTick: { show: false },
          axisLine: { lineStyle: { color: '#e2e8f0' } },
        },
        yAxis: [
          {
            type: 'value',
            name: '亿',
            nameTextStyle: { fontSize: 9, color: '#94a3b8' },
            axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => v.toFixed(0) },
            splitLine: { lineStyle: { color: '#f1f5f9' } },
          },
          {
            type: 'value',
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        ],
        series: [
          {
            name: '主力净额',
            type: 'bar',
            data: trend.map((t) => ({
              value: t.net_main,
              itemStyle: { color: t.net_main >= 0 ? INFLOW_COLOR : OUTFLOW_COLOR, borderRadius: 2 },
            })),
            barMaxWidth: 12,
          },
          {
            name: '净额比',
            type: 'line',
            yAxisIndex: 1,
            data: trend.map((t) => t.net_ratio),
            symbolSize: 3,
            connectNulls: true,
            lineStyle: { color: '#6366f1', width: 1.2, opacity: 0.8 },
            itemStyle: { color: '#6366f1' },
          },
        ],
      },
      { notMerge: true },
    );
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [trend]);

  useEffect(
    () => () => {
      inst.current?.dispose();
      inst.current = null;
    },
    [],
  );

  return <div ref={ref} className="w-full min-w-0" style={{ height }} />;
};

const MasterRow: React.FC<{
  row: FlowSectorRow;
  rank: number;
  maxAbs: number;
  active: boolean;
  onClick: () => void;
}> = ({ row, rank, maxAbs, active, onClick }) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-xl text-left transition-colors cursor-pointer ${
      active ? 'bg-purple-50 border border-purple-200/80 shadow-2xs' : 'hover:bg-slate-50 border border-transparent'
    }`}
  >
    <span className={`w-5 text-[10px] font-mono font-bold shrink-0 ${rank <= 3 ? 'text-purple-600' : 'text-slate-400'}`}>
      {rank}
    </span>
    <div className="w-24 min-w-0 flex flex-col gap-0.5">
      <span className="text-[11px] font-extrabold text-slate-700 truncate flex items-center gap-1">
        {row.name}
        <EvidenceBadge streak={row.streak} badge={row.evidence} />
      </span>
      <span className="text-[9px] font-mono text-slate-400 truncate">
        {row.members} 只 · 比 {fmtRatio(row.net_ratio)}
      </span>
    </div>
    <BipolarBar value={row.net_main} maxAbs={maxAbs} />
    <span className={`w-[74px] text-right text-[11px] font-mono font-extrabold shrink-0 ${flowTextClass(row.net_main)}`}>
      {fmtYi(row.net_main)}
    </span>
    <span className={`w-[52px] text-right text-[10px] font-mono shrink-0 ${flowTextClass(row.pct_change)}`}>
      {fmtPct(row.pct_change)}
    </span>
    <ChevronRight className={`w-3 h-3 shrink-0 ${active ? 'text-purple-500' : 'text-slate-300'}`} />
  </button>
);

export const SectorFlowWorkbench: React.FC<SectorFlowWorkbenchProps> = ({
  rows,
  selectedSector,
  onSelectSector,
  detail,
  detailLoading,
  detailError,
  onSelectStock,
  compact = false,
}) => {
  const maxAbs = useMemo(() => Math.max(1, ...rows.map((r) => Math.abs(r.net_main))), [rows]);
  const detailMaxAbs = useMemo(
    () => (detail ? Math.max(1, ...detail.members.map((m) => Math.abs(m.net_main))) : 1),
    [detail],
  );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 min-w-0">
      {/* ── 左 master：板块榜（延续状态驱动排序，后端已排） ── */}
      <div className={`${compact ? 'lg:col-span-4' : 'lg:col-span-5'} bg-white/95 backdrop-blur-md rounded-2xl border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col min-w-0`}>
        <div className="flex items-center justify-between px-3.5 pt-3 pb-2 border-b border-slate-100">
          <span className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-purple-600" />
            板块榜 · 延续状态优先
          </span>
          <span className="text-[10px] font-mono text-slate-400">连N 命中在前 → 净额</span>
        </div>
        <div className="overflow-y-auto custom-scrollbar px-1.5 py-1.5" style={{ maxHeight: compact ? 420 : 520 }}>
          {rows.length === 0 && <div className="px-3 py-6 text-center text-xs text-slate-400">暂无板块数据</div>}
          {rows.map((r, i) => (
            <MasterRow
              key={r.name}
              row={r}
              rank={i + 1}
              maxAbs={maxAbs}
              active={selectedSector === r.name}
              onClick={() => onSelectSector(r.name)}
            />
          ))}
        </div>
      </div>

      {/* ── 右 detail：板块明细 + 成分 ── */}
      <div className={`${compact ? 'lg:col-span-8' : 'lg:col-span-7'} bg-white/95 backdrop-blur-md rounded-2xl border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col min-w-0`}>
        {!selectedSector && (
          <div className="flex-1 flex items-center justify-center py-16 text-xs text-slate-400">
            点击左侧板块查看明细与成分贡献；点成分股下钻个股资金流
          </div>
        )}
        {selectedSector && detailLoading && (
          <div className="flex-1 flex items-center justify-center py-16">
            <Spin size="small" />
          </div>
        )}
        {selectedSector && !detailLoading && detailError && (
          <div className="flex-1 flex items-center justify-center py-16 text-xs text-slate-400">{detailError}</div>
        )}
        {selectedSector && !detailLoading && !detailError && detail && (
          <div className="flex flex-col gap-2.5 p-3.5 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-extrabold text-slate-900 truncate">{detail.name}</span>
                <span className="text-[10px] font-mono text-slate-400 shrink-0">
                  {detail.sector_set_label} · {detail.trade_date} · {detail.summary.members} 只（有资金流 {detail.summary.data_symbols}）
                </span>
              </div>
              <EvidenceBadge streak={detail.summary.streak} badge={detail.summary.evidence} size="md" />
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-slate-50/80 rounded-xl px-3 py-2 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-400">窗口主力净额</div>
                <div className={`text-sm font-extrabold font-mono ${flowTextClass(detail.summary.net_main)}`}>
                  {fmtYi(detail.summary.net_main)}
                </div>
              </div>
              <div className="bg-slate-50/80 rounded-xl px-3 py-2 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-400">净额比</div>
                <div className="text-sm font-extrabold font-mono text-purple-700">{fmtRatio(detail.summary.net_ratio)}</div>
              </div>
              <div className="bg-slate-50/80 rounded-xl px-3 py-2 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-400">成分均涨幅</div>
                <div className={`text-sm font-extrabold font-mono ${flowTextClass(detail.summary.pct_change)}`}>
                  {fmtPct(detail.summary.pct_change)}
                </div>
              </div>
              <div className="bg-slate-50/80 rounded-xl px-3 py-2 border border-slate-100">
                <div className="text-[10px] font-bold text-slate-400">主力广度</div>
                <div className="text-sm font-extrabold font-mono text-slate-700">
                  {detail.summary.breadth === null ? '—' : `${(detail.summary.breadth * 100).toFixed(0)}%`}
                </div>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-[10px] font-bold text-slate-400">20 日主力净额与净额比</span>
                <span className="text-[10px] font-mono text-slate-400">红=净流入 绿=净流出（亿）</span>
              </div>
              <TrendChart trend={detail.trend} />
            </div>

            <div className="flex flex-col gap-1 min-w-0">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-bold text-slate-400">
                  成分贡献榜（窗口净额 · 点击下钻个股）
                </span>
                <span className="text-[10px] font-mono text-slate-400">前排为净额绝对值最大</span>
              </div>
              <div className="overflow-y-auto custom-scrollbar" style={{ maxHeight: compact ? 260 : 320 }}>
                {detail.members.map((m) => (
                  <button
                    key={m.symbol}
                    onClick={() => onSelectStock(m.symbol, m.name)}
                    className="w-full flex items-center gap-2 px-2 py-1 rounded-lg text-left hover:bg-purple-50/70 transition-colors cursor-pointer"
                  >
                    <span className="w-6 text-[10px] font-mono text-slate-400 shrink-0">#{m.inner_rank}</span>
                    <span className="w-[92px] min-w-0 text-[11px] font-bold text-slate-700 truncate shrink-0">{m.name || '—'}</span>
                    <span className="w-[68px] text-[10px] font-mono text-slate-400 shrink-0">{m.symbol}</span>
                    <BipolarBar value={m.net_main} maxAbs={detailMaxAbs} width="w-16" />
                    <span className={`w-[72px] text-right text-[11px] font-mono font-extrabold shrink-0 ${flowTextClass(m.net_main)}`}>
                      {fmtYi(m.net_main)}
                    </span>
                    <span className="w-[46px] text-right text-[10px] font-mono text-slate-400 shrink-0">
                      {m.share === null ? '—' : `${(m.share * 100).toFixed(1)}%`}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
