/** 期货市场分析 · Tab2 板块热力
 *
 * 品种热力矩形图（面积 = 名义持仓额 / 成交额，可切换）+ 9 大板块聚合统计。
 * 仅国内品种有持仓/成交额数据；国际品种与上金所无量仓数据不入图（如实标注数量）。
 * 点击品种色块 → 跳转「期限结构」Tab（页面级回调）。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Flame, Info } from 'lucide-react';
import { SectorHeatmapChart, SectorHeatmapItem } from '../../market-analysis-shared/SectorHeatmapChart';
import { PctText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getHeatmap, getSectorStats } from '../services/api';
import type { FuturesHeatItem, FuturesHeatmap, FuturesSectorStats } from '../types';
import { fmt, fmtUnit } from '../format';

type Metric = 'oi_amount' | 'turnover';

const METRICS: Array<{ id: Metric; label: string; hint: string }> = [
  { id: 'oi_amount', label: '持仓额', hint: '面积 = 名义持仓额（价 × 仓 × 合约乘数）' },
  { id: 'turnover', label: '成交额', hint: '面积 = 名义成交额（价 × 量 × 合约乘数）' },
];

const STEM_OF = (symbol: string): string => symbol.replace(/0\.CN$/, '');

function metricValue(it: FuturesHeatItem, metric: Metric): number | null {
  const v = metric === 'oi_amount' ? it.oi_amount_yi : it.turnover_yi;
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null;
}

export const FuturesHeatmapPanel: React.FC<{
  reloadKey: number;
  onPickStem: (stem: string) => void;
}> = ({ reloadKey, onPickStem }) => {
  const [heatmap, setHeatmap] = useState<FuturesHeatmap | null>(null);
  const [sectorStats, setSectorStats] = useState<FuturesSectorStats | null>(null);
  const [metric, setMetric] = useState<Metric>('oi_amount');
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([getHeatmap(), getSectorStats()])
      .then(([hm, st]) => {
        if (alive) {
          setHeatmap(hm);
          setSectorStats(st);
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
  }, [reloadKey]);

  const items = heatmap?.items ?? [];
  // 面积口径切换时后端原值不变，仅前端换算；useMemo 避免每次渲染重建图表
  const chartItems = useMemo(() => {
    const out: SectorHeatmapItem[] = [];
    for (const it of items) {
      const v = metricValue(it, metric);
      if (v === null) continue;
      out.push({ name: it.name, value: v, pct_change: it.pct_change });
    }
    return out;
  }, [items, metric]);

  const excludedCount = items.length - chartItems.length;
  const coveredCount = items.length - excludedCount;
  // 色块 name → symbol 映射（treemap 回调只回传 name）
  const symbolByName = useMemo(() => {
    const m = new Map<string, string>();
    for (const it of items) m.set(it.name, it.symbol);
    return m;
  }, [items]);

  const dataDate = heatmap?.trade_date || sectorStats?.trade_date || '';

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-start">
      <SectionCard
        className="xl:col-span-2 !gap-2.5"
        title={
          <span className="flex items-center gap-1.5">
            <Flame className="w-3.5 h-3.5 text-amber-600" />
            品种热力图
            <span className="text-[9px] font-normal text-slate-400">
              面积 = {METRICS.find((m) => m.id === metric)?.label}；点击色块看期限结构
            </span>
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <DateBadge label="数据日期" date={dataDate} />
            <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
              {METRICS.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMetric(m.id)}
                  title={m.hint}
                  className={`px-3 py-1 rounded-full text-[10px] font-extrabold transition-all ${
                    metric === m.id
                      ? 'bg-white text-amber-700 shadow-2xs border border-amber-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
        }
      >
        {chartItems.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <SectorHeatmapChart
            data={chartItems}
            height={560}
            valueLabel={metric === 'oi_amount' ? '名义持仓额' : '名义成交额'}
            renderKey={metric}
            onItemClick={(name) => {
              const symbol = symbolByName.get(name);
              if (symbol) onPickStem(STEM_OF(symbol));
            }}
          />
        )}
        <div className="flex items-center gap-1.5 text-[10px] text-slate-400 leading-snug">
          <Info className="w-3 h-3 flex-shrink-0" />
          <span>
            {coveredCount} 个国内品种入图
            {excludedCount > 0 && `；另有 ${excludedCount} 个品种无量仓数据未入图（国际品种/上金所，非零值）`}
          </span>
        </div>
      </SectionCard>

      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            <span className="w-3.5 h-3.5 rounded-sm bg-gradient-to-br from-red-400 to-green-400 inline-block" />
            板块统计（国内口径）
          </span>
        }
        extra={<DateBadge label="数据日期" date={sectorStats?.trade_date} />}
      >
        {(sectorStats?.sectors ?? []).length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="flex flex-col">
            {(sectorStats?.sectors ?? []).map((s) => (
              <div
                key={s.sector}
                className="flex flex-col gap-0.5 px-1 py-1.5 border-b border-slate-50 last:border-0"
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-extrabold text-slate-800 w-14 whitespace-nowrap">
                    {s.name}
                  </span>
                  <span className="text-[10px] font-mono font-extrabold whitespace-nowrap">
                    <span className="text-red-600">{s.up}涨</span>
                    <span className="text-slate-300 mx-0.5">/</span>
                    <span className="text-green-600">{s.down}跌</span>
                  </span>
                  <span className="ml-auto">
                    <PctText value={s.median_pct} className="text-[11px]" />
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500">
                  <span title="名义成交额（亿）">成 {fmt(s.turnover_yi)}</span>
                  <span title="名义持仓额（亿）">仓 {fmt(s.oi_amount_yi)}</span>
                  <span title="持仓量日变化（万手）">差 {fmtUnit(s.oi_change_wan, '')}</span>
                  {s.leader && (
                    <span className="ml-auto text-slate-400 truncate" title="板块内涨幅最大品种">
                      领涨 <span className="text-slate-600 font-bold">{s.leader.name}</span>{' '}
                      {s.leader.pct_change !== null ? `${s.leader.pct_change > 0 ? '+' : ''}${s.leader.pct_change}%` : '—'}
                    </span>
                  )}
                </div>
              </div>
            ))}
            <div className="text-[9px] text-slate-400 pt-1.5 leading-snug">
              中位涨幅为板块内国内品种中位数；国际品种/上金所不参与板块聚合（量仓口径不可比）。
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
};
