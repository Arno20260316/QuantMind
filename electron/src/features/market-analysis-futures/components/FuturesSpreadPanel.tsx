/** 期货市场分析 · Tab6 内外盘与期现价差
 *
 * 内外盘跨市场价差 9 组（国际价按当日汇率折算人民币，税制口径逐组标注）
 * + 贵金属期现基差 2 组（上金所现货 vs 沪期主力）。点击卡片看价差率历史。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { ArrowLeftRight, Info } from 'lucide-react';
import { PctText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getCrossMarket, getPreciousBasis } from '../services/api';
import type { FuturesCrossMarket, FuturesPreciousBasis } from '../types';
import { BASIS_STATE_META, Badge, fmt, fmtPctS, num } from '../format';

const CHART_COLOR = '#d97706'; // amber-600

interface DetailPoint {
  date: string;
  pct: number | null;
  lines: string[];
}

export const FuturesSpreadPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  const [cross, setCross] = useState<FuturesCrossMarket | null>(null);
  const [basis, setBasis] = useState<FuturesPreciousBasis | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([getCrossMarket(60), getPreciousBasis(60)])
      .then(([cm, pb]) => {
        if (alive) {
          setCross(cm);
          setBasis(pb);
          setFailed(false);
          setSelected((prev) => prev || cm.pairs[0]?.key || pb.pairs[0]?.key || '');
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

  const crossPairs = cross?.pairs ?? [];
  const basisPairs = basis?.pairs ?? [];

  const selectedCross = crossPairs.find((p) => p.key === selected) ?? null;
  const selectedBasis = basisPairs.find((p) => p.key === selected) ?? null;

  // 明细序列：跨市场 → 价差率；期现 → 基差率
  const detail: { title: string; caption: string; points: DetailPoint[]; pctLabel: string } | null =
    useMemo(() => {
      if (selectedCross) {
        return {
          title: `${selectedCross.name} · 内外盘价差率`,
          caption: `折算：${selectedCross.formula}；税制：${selectedCross.tax}。价差率 =（国内 − 折算国际）/ 折算国际，正值国内升水。`,
          pctLabel: '价差率',
          points: selectedCross.points.map((p) => ({
            date: p.date,
            pct: p.spread_pct,
            lines: [
              `国内 ${fmt(p.domestic)}（${selectedCross.domestic_unit}）`,
              `折算国际 ${fmt(p.intl_cny)}（${selectedCross.intl_unit} → 人民币）`,
              `价差 ${fmt(p.spread)}`,
            ],
          })),
        };
      }
      if (selectedBasis) {
        return {
          title: `${selectedBasis.name} · 基差率`,
          caption: `上金所现货（${selectedBasis.spot_symbol}）收盘 vs 沪期主力连续（${selectedBasis.fut_symbol}）收盘；基差 = 现货 − 期货，正值现货升水。两市场收盘时刻略不同，为收盘对收盘口径。`,
          pctLabel: '基差率',
          points: selectedBasis.points.map((p) => ({
            date: p.date,
            pct: p.basis_pct,
            lines: [
              `现货 ${fmt(p.spot)}（${selectedBasis.unit}）`,
              `期货 ${fmt(p.fut)}（${selectedBasis.unit}）`,
              `基差 ${fmt(p.basis)}`,
            ],
          })),
        };
      }
      return null;
    }, [selectedCross, selectedBasis]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || !detail || detail.points.length === 0) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(el);
    }
    const chart = instanceRef.current;
    const pts = detail.points;
    chart.setOption(
      {
        grid: { left: 8, right: 14, top: 26, bottom: 4, containLabel: true },
        animationDuration: 300,
        xAxis: {
          type: 'category',
          data: pts.map((p) => p.date),
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#e2e8f0' } },
          axisTick: { show: false },
          axisLabel: {
            fontSize: 9,
            color: '#94a3b8',
            fontFamily: 'monospace',
            interval: (i: number) => i % Math.max(1, Math.ceil(pts.length / 6)) === 0 || i === pts.length - 1,
          },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => `${v}%` },
          splitLine: { lineStyle: { color: '#f1f5f9' } },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(255,255,255,0.96)',
          borderColor: '#e2e8f0',
          textStyle: { fontSize: 11, color: '#334155' },
          formatter: (params: any) => {
            const p = Array.isArray(params) ? params[0] : params;
            const pt = pts[p?.dataIndex];
            if (!pt) return '';
            return [`<b>${pt.date}</b>`, ...pt.lines, `${detail.pctLabel}: <b>${fmtPctS(pt.pct)}</b>`].join('<br/>');
          },
        },
        series: [
          {
            name: detail.pctLabel,
            type: 'line',
            smooth: true,
            symbol: 'none',
            connectNulls: false,
            data: pts.map((p) => num(p.pct)),
            lineStyle: { width: 2, color: CHART_COLOR },
            itemStyle: { color: CHART_COLOR },
            areaStyle: { opacity: 0.07, color: CHART_COLOR },
            markLine: {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#cbd5e1', type: 'dashed' },
              label: { formatter: '0', fontSize: 9, color: '#94a3b8' },
              data: [{ yAxis: 0 }],
            },
          },
        ],
      },
      true,
    );

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [detail]);

  useEffect(
    () => () => {
      instanceRef.current?.dispose();
      instanceRef.current = null;
    },
    [],
  );

  const fx = cross?.fx;
  const dataDate = cross?.trade_date || basis?.trade_date || '';

  return (
    <div className="flex flex-col gap-2.5">
      {/* 汇率与口径条 */}
      <div className="bg-white/90 backdrop-blur-md rounded-2xl px-3.5 py-2 border border-amber-200/50 shadow-sm flex items-center gap-4 flex-wrap text-[11px]">
        <span className="flex items-center gap-1.5">
          <ArrowLeftRight className="w-3.5 h-3.5 text-amber-600" />
          <span className="font-extrabold text-slate-600">USD/CNY 折算汇率</span>
          <span className="font-mono font-extrabold text-slate-800">{fmt(fx?.rate, 4)}</span>
          {fx?.date && <span className="text-[10px] font-mono text-slate-400">（{fx.date}）</span>}
        </span>
        <span className="text-[10px] text-slate-400 leading-snug">
          国际价为美元报价，先按当日中行汇率折人民币（逐日对齐、缺失日跳过该点），再按单位换算到与国内同一量纲；有色按进口到岸口径加 13% 增值税。
        </span>
        <DateBadge label="数据日期" date={dataDate} />
      </div>

      {/* 内外盘 9 组卡片 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            内外盘价差
            <span className="text-[9px] font-normal text-slate-400">点击卡片看价差率历史</span>
          </span>
        }
      >
        {(crossPairs ?? []).length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {crossPairs.map((p) => {
              const active = selected === p.key;
              return (
                <button
                  key={p.key}
                  onClick={() => setSelected(p.key)}
                  title={`${p.domestic_name}（${p.domestic_symbol}）vs ${p.intl_name}（${p.intl_symbol}）；${p.tax}`}
                  className={`text-left rounded-xl border px-3 py-2 transition-all cursor-pointer ${
                    active
                      ? 'border-amber-300 bg-amber-50/60 shadow-xs ring-1 ring-amber-200'
                      : 'border-slate-200/80 bg-white hover:border-amber-200 hover:bg-amber-50/30'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-extrabold text-slate-800">{p.name}</span>
                    <PctText value={p.spread_pct} />
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-slate-500 flex-wrap">
                    <span title="国内主力连续收盘">
                      内 {fmt(p.domestic_close)} <span className="text-slate-400">{p.domestic_unit}</span>
                    </span>
                    <span className="text-slate-300">|</span>
                    <span title={`国际收盘 ${fmt(p.intl_close_native)} ${p.intl_unit}，按汇率折算`}>
                      外 {fmt(p.intl_close_cny)} <span className="text-slate-400">{p.domestic_unit}</span>
                    </span>
                  </div>
                  <div className="mt-1 text-[9px] text-slate-400 truncate" title={`${p.formula}；${p.tax}`}>
                    {p.tax}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* 贵金属期现基差 2 组 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            贵金属期现基差
            <span className="text-[9px] font-normal text-slate-400">上金所现货 vs 沪期主力；点击看基差率历史</span>
          </span>
        }
      >
        {(basisPairs ?? []).length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {basisPairs.map((p) => {
              const active = selected === p.key;
              const st = p.state ? BASIS_STATE_META[p.state] : null;
              return (
                <button
                  key={p.key}
                  onClick={() => setSelected(p.key)}
                  className={`text-left rounded-xl border px-3 py-2 transition-all cursor-pointer ${
                    active
                      ? 'border-amber-300 bg-amber-50/60 shadow-xs ring-1 ring-amber-200'
                      : 'border-slate-200/80 bg-white hover:border-amber-200 hover:bg-amber-50/30'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-extrabold text-slate-800">{p.name}</span>
                    {st && <Badge text={st.label} cls={st.cls} />}
                    <span className="ml-auto">
                      <PctText value={p.basis_pct} />
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-slate-500 flex-wrap">
                    <span title={`上金所现货 ${p.spot_symbol} 收盘`}>
                      现货 {fmt(p.spot_close)}
                    </span>
                    <span className="text-slate-300">|</span>
                    <span title={`沪期主力连续 ${p.fut_symbol} 收盘`}>期货 {fmt(p.fut_close)}</span>
                    <span className="text-slate-300">|</span>
                    <span title="基差 = 现货 − 期货">
                      基差 {fmt(p.basis)} <span className="text-slate-400">{p.unit}</span>
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* 明细曲线 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            {detail ? detail.title : '价差历史'}
            {detail && (
              <span className="text-[9px] font-normal text-slate-400">
                {detail.points.length} 个交易日
              </span>
            )}
          </span>
        }
      >
        {!detail || detail.points.length === 0 ? (
          <EmptyHint text="选择上方任一组价差查看历史" loading={loading} />
        ) : (
          <>
            <div ref={chartRef} style={{ width: '100%', height: 320 }} />
            <div className="flex items-center gap-1.5 text-[10px] text-slate-400 leading-snug">
              <Info className="w-3 h-3 flex-shrink-0" />
              <span>{detail.caption}</span>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
};
