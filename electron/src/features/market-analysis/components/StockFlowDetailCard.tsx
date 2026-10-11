/**
 * 个股资金流详情卡（细案 v2 §5，R3 核心）。
 *
 * - 20 日逐日五档序列（C 级：红=净流入、绿=净流出）；
 * - 所属板块归因：板块 chips + 该股在各板块的贡献排名，点 chip 回板块视角
 *   （=「个股资金流返回板块」）；
 * - 个股层不出现资金流预测徽章——实测否决（IC≤0.01），页内明示原因（meta.disclosure）；
 * - factor（因子板共振态）为 P3 对接位；为 null 时不渲染（不编造）。
 */

import React, { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { Spin, Tooltip } from 'antd';
import { X, TrendingUp, ChevronRight } from 'lucide-react';
import {
  getFlowStock,
  fmtYi,
  fmtPct,
  fmtRatio,
  flowTextClass,
  MoneyFlowError,
  type FlowStockDetailResponse,
} from '../services/moneyFlowService';
import { EvidenceBadge } from './EvidenceBadge';

const INFLOW_COLOR = '#e11d48';
const OUTFLOW_COLOR = '#059669';

export interface StockFlowDetailCardProps {
  symbol: string;
  date?: string;
  onClose: () => void;
  /** 点归因 chip → 回板块视角（sectorSet 为 null 时保持当前体系）。 */
  onNavigateSector: (name: string, sectorSet: string | null) => void;
}

const SeriesChart: React.FC<{ detail: FlowStockDetailResponse; height?: number }> = ({ detail, height = 168 }) => {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    if (!inst.current) inst.current = echarts.init(ref.current);
    const chart = inst.current;
    const dates = detail.series.map((p) => p.date.slice(5));
    const main = detail.series.map((p) => p.main ?? null);
    const close = detail.series.map((p) => p.close ?? null);
    const hasClose = close.some((c) => c !== null);
    chart.setOption(
      {
        grid: { left: 46, right: hasClose ? 46 : 12, top: 18, bottom: 20 },
        tooltip: {
          trigger: 'axis',
          formatter: (ps: any[]) => {
            const idx = ps?.[0]?.dataIndex ?? 0;
            const p = detail.series[idx];
            if (!p) return '';
            return (
              `<div style="font-size:11px;line-height:1.6">${p.date}<br/>` +
              `主力净额：<b style="color:${(p.main ?? 0) >= 0 ? INFLOW_COLOR : OUTFLOW_COLOR}">${fmtYi(p.main)}</b><br/>` +
              `超大 ${fmtYi(p.super)} · 大 ${fmtYi(p.large)}<br/>` +
              `中 ${fmtYi(p.medium)} · 小 ${fmtYi(p.small)}<br/>` +
              `净额比：${fmtRatio(p.net_ratio)} · 收盘 ${p.close ?? '—'}（${fmtPct(p.pct_change)}）</div>`
            );
          },
        },
        xAxis: {
          type: 'category',
          data: dates,
          axisLabel: { fontSize: 9, color: '#94a3b8' },
          axisTick: { show: false },
          axisLine: { lineStyle: { color: '#e2e8f0' } },
        },
        yAxis: [
          {
            type: 'value',
            name: '亿',
            nameTextStyle: { fontSize: 9, color: '#94a3b8' },
            axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => v.toFixed(1) },
            splitLine: { lineStyle: { color: '#f1f5f9' } },
          },
          ...(hasClose
            ? [
                {
                  type: 'value',
                  scale: true,
                  axisLabel: { fontSize: 9, color: '#94a3b8' },
                  splitLine: { show: false },
                },
              ]
            : []),
        ],
        series: [
          {
            name: '主力净额',
            type: 'bar',
            data: main.map((v) => ({
              value: v,
              itemStyle: { color: (v ?? 0) >= 0 ? INFLOW_COLOR : OUTFLOW_COLOR, borderRadius: 2 },
            })),
            barMaxWidth: 14,
          },
          ...(hasClose
            ? [
                {
                  name: '收盘价',
                  type: 'line',
                  yAxisIndex: 1,
                  data: close,
                  symbolSize: 4,
                  connectNulls: true,
                  lineStyle: { color: '#6366f1', width: 1.5 },
                  itemStyle: { color: '#6366f1' },
                },
              ]
            : []),
        ],
      },
      { notMerge: true },
    );
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [detail]);

  useEffect(
    () => () => {
      inst.current?.dispose();
      inst.current = null;
    },
    [],
  );

  return <div ref={ref} className="w-full min-w-0" style={{ height }} />;
};

const WindowCard: React.FC<{ label: string; w: FlowStockDetailResponse['windows']['1d'] }> = ({ label, w }) => (
  <div className="flex-1 min-w-0 bg-slate-50/80 rounded-xl px-2.5 py-2 border border-slate-100">
    <div className="text-[10px] font-bold text-slate-400">{label}</div>
    {w ? (
      <>
        <div className={`text-[13px] font-extrabold font-mono ${flowTextClass(w.net_main)}`}>{fmtYi(w.net_main)}</div>
        <div className="text-[10px] font-mono text-slate-400">净额比 {fmtRatio(w.net_ratio)}</div>
      </>
    ) : (
      <div className="text-[13px] font-extrabold font-mono text-slate-300">—</div>
    )}
  </div>
);

export const StockFlowDetailCard: React.FC<StockFlowDetailCardProps> = ({
  symbol,
  date,
  onClose,
  onNavigateSector,
}) => {
  const [detail, setDetail] = useState<FlowStockDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqSeq = useRef(0);

  useEffect(() => {
    const seq = ++reqSeq.current;
    if (!symbol) return;
    setLoading(true);
    setError(null);
    getFlowStock(symbol, { days: 20, date })
      .then((d) => {
        if (seq !== reqSeq.current) return;
        setDetail(d);
      })
      .catch((e: unknown) => {
        if (seq !== reqSeq.current) return;
        setDetail(null);
        if (e instanceof MoneyFlowError && e.status === 404) {
          setError('该代码在 L2 资金流分区中查无数据（可能为北交所/新代码或数据未覆盖）');
        } else {
          setError('个股资金流取数失败，请稍后重试');
        }
      })
      .finally(() => {
        if (seq === reqSeq.current) setLoading(false);
      });
  }, [symbol, date]);

  return (
    <div className="bg-white/95 backdrop-blur-md rounded-2xl border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col min-w-0">
      <div className="flex items-center justify-between px-4 pt-3.5 pb-2 border-b border-slate-100">
        <div className="flex items-center gap-2 min-w-0">
          <TrendingUp className="w-3.5 h-3.5 text-purple-600 shrink-0" />
          <span className="text-xs font-extrabold text-slate-800 truncate">
            {detail?.name || '个股资金流详情'}
          </span>
          <span className="font-mono text-[11px] text-purple-700 bg-purple-50 px-1.5 py-0.5 rounded border border-purple-200 shrink-0">
            {symbol}
          </span>
          {detail && (
            <span className={`text-[12px] font-extrabold font-mono shrink-0 ${flowTextClass(detail.pct_change)}`}>
              {detail.close ?? '—'}（{fmtPct(detail.pct_change)}）
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-700 p-1 rounded-lg hover:bg-slate-100 shrink-0"
          aria-label="关闭个股详情"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-10">
          <Spin size="small" />
        </div>
      )}

      {!loading && error && <div className="px-4 py-6 text-xs text-slate-400 text-center">{error}</div>}

      {!loading && !error && detail && (
        <div className="flex flex-col gap-2.5 p-3.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-mono text-slate-400">{detail.trade_date} · 单位：亿元</span>
          </div>
          <SeriesChart detail={detail} />

          <div className="flex items-stretch gap-2">
            <WindowCard label="当日" w={detail.windows['1d']} />
            <WindowCard label="5 日" w={detail.windows['5d']} />
            <WindowCard label="20 日" w={detail.windows['20d']} />
          </div>

          <div className="flex flex-col gap-1.5 pt-1.5 border-t border-slate-100">
            <span className="text-[10px] font-bold text-slate-400">所属板块归因（点击回板块视角）</span>
            <div className="flex flex-wrap gap-1.5 max-h-[168px] overflow-y-auto custom-scrollbar pr-1">
              {detail.attribution.length === 0 && (
                <span className="text-[11px] text-slate-400">无板块归属数据</span>
              )}
              {detail.attribution.map((a) => (
                <Tooltip
                  key={`${a.type}-${a.name}`}
                  title={`板块窗口净额 ${fmtYi(a.net_main)} · 该股贡献占比 ${a.share === null ? '—' : `${(a.share * 100).toFixed(1)}%`}`}
                >
                  <button
                    onClick={() => onNavigateSector(a.name, a.sector_set)}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold transition-colors cursor-pointer ${
                      a.streak !== null && a.streak !== undefined && a.streak >= 2
                        ? 'bg-amber-50 text-amber-700 border border-amber-200/80 hover:bg-amber-100'
                        : 'bg-slate-50 text-slate-600 border border-slate-200/80 hover:bg-slate-100'
                    }`}
                  >
                    <span className="text-slate-400">{a.type.replace('板块', '').replace('(', '').replace(')', '')}</span>
                    <span>{a.name}</span>
                    <span className="font-mono text-slate-400">
                      {a.inner_rank}/{a.inner_total}
                    </span>
                    <EvidenceBadge streak={a.streak} badge={a.evidence} />
                    <ChevronRight className="w-2.5 h-2.5 opacity-60" />
                  </button>
                </Tooltip>
              ))}
            </div>
          </div>

          <p className="text-[10px] text-slate-400 leading-snug border-t border-slate-100 pt-1.5">
            {detail.meta.disclosure?.[0] ??
              '个股层资金流实测不具预测力，个体预测以因子概率板与模型为准。'}
          </p>
        </div>
      )}
    </div>
  );
};
