/** 期货市场分析 · Tab5 仓单库存
 *
 * 交易所注册仓单（日度，品种级）：最新两日增减榜 + 单品种历史曲线。
 * 覆盖所以最新日期实际收录的交易所为准（源侧缺数据时如实为空，不落 0）。
 */

import React, { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { Package, Info } from 'lucide-react';
import { NumText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getReceipts } from '../services/api';
import type { FuturesReceipts } from '../types';
import { Badge, fmt, num } from '../format';

const CHART_COLOR = '#d97706'; // amber-600

export const FuturesInventoryPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  const [data, setData] = useState<FuturesReceipts | null>(null);
  const [variety, setVariety] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getReceipts(variety ?? undefined, 90)
      .then((d) => {
        if (alive) {
          setData(d);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [variety, reloadKey]);

  const history = data?.history ?? null;
  const points = history?.points ?? [];

  useEffect(() => {
    const el = chartRef.current;
    if (!el || points.length === 0) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(el);
    }
    const chart = instanceRef.current;
    chart.setOption(
      {
        grid: { left: 8, right: 14, top: 26, bottom: 4, containLabel: true },
        animationDuration: 300,
        xAxis: {
          type: 'category',
          data: points.map((p) => p.date),
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#e2e8f0' } },
          axisTick: { show: false },
          axisLabel: {
            fontSize: 9,
            color: '#94a3b8',
            fontFamily: 'monospace',
            interval: (i: number) => i % Math.max(1, Math.ceil(points.length / 6)) === 0 || i === points.length - 1,
          },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => fmt(v, 0) },
          splitLine: { lineStyle: { color: '#f1f5f9' } },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(255,255,255,0.96)',
          borderColor: '#e2e8f0',
          textStyle: { fontSize: 11, color: '#334155' },
          formatter: (params: any) => {
            const p = Array.isArray(params) ? params[0] : params;
            const pt = points[p?.dataIndex];
            if (!pt) return '';
            return [
              `<b>${pt.date}</b>`,
              `仓单量: <b>${fmt(pt.receipt_qty_wan)} 万张</b>`,
              `主要交易所: ${pt.venue}`,
            ].join('<br/>');
          },
        },
        series: [
          {
            name: '仓单量',
            type: 'line',
            smooth: true,
            symbol: 'none',
            connectNulls: false,
            data: points.map((p) => num(p.receipt_qty_wan)),
            lineStyle: { width: 2, color: CHART_COLOR },
            itemStyle: { color: CHART_COLOR },
            areaStyle: { opacity: 0.08, color: CHART_COLOR },
          },
        ],
      },
      true,
    );

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [points]);

  useEffect(
    () => () => {
      instanceRef.current?.dispose();
      instanceRef.current = null;
    },
    [],
  );

  const rows = data?.rows ?? [];

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5 items-start">
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            <Package className="w-3.5 h-3.5 text-amber-600" />
            注册仓单榜
            <span className="text-[9px] font-normal text-slate-400">按仓单量降序；点击行看历史</span>
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <DateBadge label="数据日期" date={data?.latest_date} />
            <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">
              {data?.variety_count ?? '—'} 品种
            </span>
          </div>
        }
      >
        {/* 覆盖所如实标注 */}
        <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
          <span className="font-bold text-slate-400">覆盖所</span>
          {(data?.venues ?? []).map((v) => (
            <Badge key={v} text={data?.venue_names?.[v] ?? v} cls="bg-amber-50 text-amber-700 border-amber-200" />
          ))}
          {(data?.venues ?? []).length === 0 && <span className="text-slate-400">—</span>}
        </div>
        {rows.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="flex flex-col max-h-[560px] overflow-y-auto pr-0.5">
            <div className="flex items-center gap-2 px-1.5 text-[9px] font-bold text-slate-400">
              <span className="w-5" />
              <span className="flex-1">品种</span>
              <span className="w-14 text-right">交易所</span>
              <span className="w-24 text-right">仓单量（万张）</span>
              <span className="w-20 text-right">变化（万张）</span>
            </div>
            {rows.map((r, i) => {
              const active = variety === r.variety;
              return (
                <button
                  key={r.variety}
                  onClick={() => setVariety(active ? null : r.variety)}
                  title={`${r.name} ${r.variety} · ${r.venue_name}；点击查看仓单历史`}
                  className={`flex items-center gap-2 px-1.5 py-1.5 rounded-lg border-b border-slate-50 last:border-0 text-left transition-colors cursor-pointer ${
                    active ? 'bg-amber-50/80' : 'hover:bg-slate-50/70'
                  }`}
                >
                  <span
                    className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold flex-shrink-0 ${
                      i < 3 ? 'bg-amber-600 text-white' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {i + 1}
                  </span>
                  <span className="flex-1 min-w-0 flex items-center gap-1.5">
                    <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{r.name}</span>
                    <span className="text-[10px] font-mono text-slate-400">{r.variety}</span>
                  </span>
                  <span className="w-14 text-right text-[10px] font-bold text-slate-500 whitespace-nowrap">
                    {r.venue_name}
                  </span>
                  <span className="w-24 text-right font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap">
                    {fmt(r.receipt_qty_wan)}
                  </span>
                  <span className="w-20 text-right">
                    <NumText value={r.change_wan} className="text-[11px]" />
                  </span>
                </button>
              );
            })}
            <div className="flex items-center gap-1.5 text-[9px] text-slate-400 pt-1.5 leading-snug">
              <Info className="w-3 h-3 flex-shrink-0" />
              <span>
                仓单 = 交易所注册标准仓单（显性库存官方口径）；数据可能滞后，覆盖所以最新日期实际收录者为准，缺数据不落 0。
              </span>
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            仓单历史
            {history && (
              <span className="text-[10px] font-bold text-slate-500">
                {history.name}
                <span className="font-mono text-slate-400 ml-1">{history.variety}</span>
              </span>
            )}
          </span>
        }
        extra={history ? <DateBadge label="最新" date={history.latest} /> : undefined}
      >
        {!history ? (
          <EmptyHint text="点击左侧品种查看仓单历史" loading={false} />
        ) : points.length === 0 ? (
          <EmptyHint text="该品种暂无仓单历史" />
        ) : (
          <>
            <div className="flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
              <span className="flex items-baseline gap-1 whitespace-nowrap">
                <span className="font-bold text-slate-400">最新仓单</span>
                <span className="text-[13px] font-extrabold font-mono text-slate-800">
                  {fmt(history.latest_qty_wan)} 万张
                </span>
              </span>
              <span className="flex items-baseline gap-1 whitespace-nowrap">
                <span className="font-bold text-slate-400">日变化</span>
                <NumText value={history.change_wan} suffix="万张" className="text-[12px]" />
              </span>
              <span className="text-slate-400 font-mono ml-auto">
                {points.length} 个交易日
              </span>
            </div>
            <div ref={chartRef} style={{ width: '100%', height: 350 }} />
          </>
        )}
      </SectionCard>
    </div>
  );
};
