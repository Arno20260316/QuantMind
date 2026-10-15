/** 期货市场分析 · Tab3 期限结构
 *
 * 单品种近远月曲线（收盘/结算/量仓明细 + 主力合约标注）+ 全市场期限结构榜
 * （按近远月价差率降序，Back 结构在前）。点击榜单行切换品种。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { TrendingUp, Info } from 'lucide-react';
import { PctText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getTerm, getTermRanking } from '../services/api';
import type { FuturesTermRanking, FuturesTermStructure } from '../types';
import { Badge, STRUCTURE_META, fmt, fmtPctS, num } from '../format';

const CHART_COLOR = '#d97706'; // amber-600
const MAIN_COLOR = '#dc2626'; // red-600

const safeNum = num;

export const FuturesTermPanel: React.FC<{
  stem: string;
  onStemChange: (stem: string) => void;
  reloadKey: number;
}> = ({ stem, onStemChange, reloadKey }) => {
  const [ranking, setRanking] = useState<FuturesTermRanking | null>(null);
  const [term, setTerm] = useState<FuturesTermStructure | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let alive = true;
    getTermRanking(100)
      .then((d) => {
        if (alive) setRanking(d);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setTerm(null);
    getTerm(stem)
      .then((d) => {
        if (alive) {
          setTerm(d);
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
  }, [stem, reloadKey]);

  const rows = ranking?.rows ?? [];
  const curve = term?.curve ?? [];

  // 选择器条目：期限榜（有合约数据的品种）；当前品种若不在榜内则补一条
  const pickerOptions = useMemo(() => {
    const opts = rows.map((r) => ({ stem: r.stem, name: r.name }));
    if (stem && !opts.some((o) => o.stem === stem)) {
      opts.unshift({ stem, name: term?.name ?? stem });
    }
    return opts;
  }, [rows, stem, term?.name]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || curve.length === 0) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(el);
    }
    const chart = instanceRef.current;
    const yms = curve.map((c) => c.ym);
    const closes = curve.map((c) => safeNum(c.close));
    const mainIdx = term?.main_contract ? curve.findIndex((c) => c.contract === term.main_contract) : -1;

    chart.setOption(
      {
        grid: { left: 10, right: 16, top: 30, bottom: 4, containLabel: true },
        animationDuration: 300,
        xAxis: {
          type: 'category',
          data: yms,
          axisLine: { lineStyle: { color: '#e2e8f0' } },
          axisTick: { show: false },
          axisLabel: { fontSize: 10, color: '#94a3b8', fontFamily: 'monospace' },
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => fmt(v, 1) },
          splitLine: { lineStyle: { color: '#f1f5f9' } },
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(255,255,255,0.96)',
          borderColor: '#e2e8f0',
          textStyle: { fontSize: 11, color: '#334155' },
          formatter: (params: any) => {
            const p = Array.isArray(params) ? params[0] : params;
            const c = curve[p?.dataIndex];
            if (!c) return '';
            const isMain = term?.main_contract === c.contract;
            return [
              `<b>${c.contract}</b>${isMain ? ' <span style="color:#dc2626">主力</span>' : ''}`,
              `收盘: <b>${fmt(c.close)}</b>　结算: ${fmt(c.settlement)}`,
              `成交: ${fmt(c.volume_wan)} 万手　持仓: ${fmt(c.oi_wan)} 万手`,
              `持仓额: ${fmt(c.oi_amount_yi)} 亿`,
              c.expiry_date
                ? `到期参考: ${c.expiry_date}${c.expiry_estimated ? '（估计）' : ''}${
                    c.days_to_expiry !== null && c.days_to_expiry !== undefined
                      ? `　距到期 ${c.days_to_expiry} 天`
                      : ''
                  }`
                : '',
            ]
              .filter(Boolean)
              .join('<br/>');
          },
        },
        series: [
          {
            name: '收盘价',
            type: 'line',
            smooth: false,
            symbol: 'circle',
            symbolSize: 6,
            connectNulls: false,
            data: closes,
            lineStyle: { width: 2, color: CHART_COLOR },
            itemStyle: { color: CHART_COLOR },
            areaStyle: { opacity: 0.06, color: CHART_COLOR },
            markPoint:
              mainIdx >= 0
                ? {
                    symbol: 'pin',
                    symbolSize: 34,
                    itemStyle: { color: MAIN_COLOR },
                    label: { fontSize: 9, fontWeight: 'bold', color: '#fff', formatter: '主力' },
                    data: [{ coord: [mainIdx, closes[mainIdx] ?? 0] }],
                  }
                : undefined,
          },
        ],
      },
      true,
    );

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [curve, term?.main_contract]);

  useEffect(
    () => () => {
      instanceRef.current?.dispose();
      instanceRef.current = null;
    },
    [],
  );

  const structMeta = term?.structure ? STRUCTURE_META[term.structure] : null;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-start">
      <SectionCard
        className="xl:col-span-2 !gap-2.5"
        title={
          <span className="flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-amber-600" />
            近远月曲线
            {term && (
              <span className="text-[10px] font-bold text-slate-500">
                {term.name}
                <span className="font-mono text-slate-400 ml-1">{term.stem} · {term.sector_name}</span>
              </span>
            )}
            {structMeta && <Badge text={structMeta.label} cls={structMeta.cls} title={structMeta.hint} />}
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <DateBadge label="数据日期" date={term?.trade_date} />
            <select
              value={stem}
              onChange={(e) => onStemChange(e.target.value)}
              className="px-2.5 py-1 rounded-full border border-amber-200 bg-white text-[11px] font-extrabold text-slate-700 outline-none cursor-pointer hover:border-amber-300 focus:border-amber-400"
              title="选择品种（按期限榜覆盖率列出）"
            >
              {pickerOptions.map((o) => (
                <option key={o.stem} value={o.stem}>
                  {o.name}（{o.stem}）
                </option>
              ))}
            </select>
          </div>
        }
      >
        {/* 摘要指标条 */}
        {term && (
          <div className="rounded-xl border border-amber-100/80 bg-gradient-to-b from-amber-50/50 to-white px-2.5 py-1.5 flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
            <span className="flex items-baseline gap-1 whitespace-nowrap" title="持仓量最大的合约（持仓缺失时按成交量兜底）">
              <span className="font-bold text-slate-400">主力合约</span>
              <span className="text-[12px] font-extrabold font-mono text-slate-800">{term.main_contract ?? '—'}</span>
              {term.main_by && (
                <span className="text-slate-400">{term.main_by === 'oi' ? '按持仓' : '按成交'}</span>
              )}
            </span>
            <span className="flex items-baseline gap-1 whitespace-nowrap" title="活跃合约中最靠近到期的合约">
              <span className="font-bold text-slate-400">近月</span>
              <span className="font-extrabold font-mono text-slate-700">{term.near_contract ?? '—'}</span>
              <span className="font-mono text-slate-500">{fmt(term.near_price)}</span>
            </span>
            <span className="flex items-baseline gap-1 whitespace-nowrap" title="活跃合约中最远的合约">
              <span className="font-bold text-slate-400">远月</span>
              <span className="font-extrabold font-mono text-slate-700">{term.far_contract ?? '—'}</span>
              <span className="font-mono text-slate-500">{fmt(term.far_price)}</span>
            </span>
            <span className="flex items-baseline gap-1 whitespace-nowrap" title="（近月 − 远月）/ 远月；正值为 Back 结构">
              <span className="font-bold text-slate-400">近远月价差</span>
              <PctText value={term.near_far_spread_pct} />
            </span>
            <span
              className="flex items-baseline gap-1 whitespace-nowrap"
              title="年化展期收益 = ln(近月/远月) × 365 / (月差 × 30.44)；正 = Back 结构多头展期收益"
            >
              <span className="font-bold text-slate-400">年化 carry</span>
              <span
                className={`font-extrabold font-mono ${
                  term.carry_annual_pct === null || term.carry_annual_pct === undefined
                    ? 'text-slate-400'
                    : term.carry_annual_pct > 0
                      ? 'text-red-600'
                      : term.carry_annual_pct < 0
                        ? 'text-green-600'
                        : 'text-slate-500'
                }`}
              >
                {term.carry_annual_pct === null || term.carry_annual_pct === undefined
                  ? '—'
                  : `${term.carry_annual_pct > 0 ? '+' : ''}${fmt(term.carry_annual_pct)}%`}
              </span>
            </span>
            <span className="flex items-baseline gap-1 whitespace-nowrap" title="近月合约距到期参考日天数（无 contract_meta 的品种为「当月 15 日」估计口径）">
              <span className="font-bold text-slate-400">近月距到期</span>
              <span className="font-mono text-slate-600">
                {term.near_days_to_expiry === null || term.near_days_to_expiry === undefined
                  ? '—'
                  : `${term.near_days_to_expiry} 天`}
              </span>
            </span>
            <span className="flex items-baseline gap-1 whitespace-nowrap">
              <span className="font-bold text-slate-400">总持仓</span>
              <span className="font-extrabold font-mono text-slate-700">{fmt(term.total_oi_wan)} 万手</span>
            </span>
          </div>
        )}
        {curve.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '该品种暂无合约数据'} />
        ) : (
          <div ref={chartRef} style={{ width: '100%', height: 380 }} />
        )}
        <div className="flex items-center gap-1.5 text-[10px] text-slate-400 leading-snug">
          <Info className="w-3 h-3 flex-shrink-0" />
          <span>
            曲线仅含近期有成交/持仓的活跃合约（7 天新鲜度闸）；价差率 =（近月−远月）/远月，正值为 Back 结构（近高远低）。
            年化 carry = ln(近月/远月) × 365 /（月差 × 30.44）；到期参考日取合约元数据（SHFE/CZCE/INE/CFFEX），
            无元数据品种按当月 15 日估计并在悬停中标注。
          </span>
        </div>
      </SectionCard>

      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            期限结构榜
            <span className="text-[9px] font-normal text-slate-400">按近远月价差率降序</span>
          </span>
        }
        extra={<DateBadge label="数据日期" date={ranking?.trade_date} />}
      >
        {rows.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="flex flex-col max-h-[560px] overflow-y-auto pr-0.5">
            {rows.map((r, i) => {
              const active = r.stem === stem;
              const meta = r.structure ? STRUCTURE_META[r.structure] : null;
              return (
                <button
                  key={r.stem}
                  onClick={() => onStemChange(r.stem)}
                  title={`${r.contracts} 个活跃合约 · 总持仓 ${fmt(r.total_oi_wan)} 万手 · 年化 carry ${
                    r.carry_annual_pct === null || r.carry_annual_pct === undefined
                      ? '—'
                      : `${fmt(r.carry_annual_pct)}%`
                  }（截面分位 ${r.carry_pct === null || r.carry_pct === undefined ? '—' : r.carry_pct.toFixed(0)}） · ${meta?.hint ?? ''}`}
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
                  <span className="flex flex-col leading-tight min-w-0">
                    <span className="text-xs font-bold text-slate-800 whitespace-nowrap">
                      {r.name} <span className="text-[9px] font-mono text-slate-400">{r.stem}</span>
                    </span>
                    <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
                      年化 carry{' '}
                      {r.carry_annual_pct === null || r.carry_annual_pct === undefined
                        ? '—'
                        : `${r.carry_annual_pct > 0 ? '+' : ''}${fmt(r.carry_annual_pct)}%`}
                      {r.near_days_to_expiry !== null && r.near_days_to_expiry !== undefined
                        ? ` · 距到期 ${r.near_days_to_expiry} 天`
                        : ''}
                    </span>
                  </span>
                  {meta && <Badge text={meta.label} cls={meta.cls} />}
                  <span className="ml-auto flex-shrink-0">
                    <PctText value={r.near_far_spread_pct} className="text-[11px]" />
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </SectionCard>
    </div>
  );
};
