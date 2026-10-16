/** 港股聪明钱 · 面 3「生态趋势」（细案 §4.4）。
 *
 * 数据源 = overview.trends（60 交易日 × 6 类型逐日 net_yi / n_symbols，构建产物直读）。
 * 图 G 净流正负对称堆叠面积：正值向上、负值向下——类型间此消彼长一眼可见；
 * 图 H 当日变动标的数堆叠柱：参与广度（哪些类型在扩/收覆盖）；
 * 图 I 持仓市值横向条：存量格局。类型 chip 可摘除/恢复（三图联动同一 muted 集合）。
 * 缺失断线绝不补 0；净额为事实语义（红涨绿跌仅在数值正负）。
 */

import React, { useMemo, useState } from 'react';
import { Activity, BarChart3, Layers } from 'lucide-react';
import type { HkSmOverviewResponse } from '../../services/api';
import { FaceCard } from '../../../market-analysis-shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import { AXIS_LABEL, CHART_COLORS, SPLIT_LINE, SmartChart, tipNum } from './charts';
import {
  MISSING,
  categoryLabel,
  fmtInt,
  fmtNum,
  fmtYi,
} from '../../hkSmartMoneyVocabulary';

interface HkSmEcoFaceProps {
  overview: HkSmOverviewResponse | null;
  loading: boolean;
}

type TrendPoint = { date: string; net_yi: number | null; n_symbols: number | null };

/** 全类型日期并集（升序）——各类型序列按它重排，缺失即 null（断线） */
function alignSeries(
  trends: HkSmOverviewResponse['trends'],
): { dates: string[]; byCat: Record<string, TrendPoint[]>; cats: string[] } {
  const byCatRaw = trends?.categories ?? {};
  const cats = Object.keys(byCatRaw);
  const all = new Set<string>();
  for (const c of cats) for (const p of byCatRaw[c] ?? []) all.add(p.date);
  const dates = [...all].sort();
  const byCat: Record<string, TrendPoint[]> = {};
  for (const c of cats) {
    const m = new Map((byCatRaw[c] ?? []).map((p) => [p.date, p]));
    byCat[c] = dates.map(
      (d) => m.get(d) ?? { date: d, net_yi: null, n_symbols: null },
    );
  }
  return { dates, byCat, cats };
}

export const HkSmEcoFace: React.FC<HkSmEcoFaceProps> = ({ overview, loading }) => {
  const [muted, setMuted] = useState<Set<string>>(new Set());

  const toggle = (cat: string) =>
    setMuted((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });

  const aligned = useMemo(() => alignSeries(overview?.trends), [overview]);
  const shown = aligned.cats.filter((c) => !muted.has(c));

  const xDates = aligned.dates.map((d) => String(d).slice(5)); // MM-DD

  /** 图 G：净流正负对称堆叠面积 */
  const flowOption = useMemo(() => {
    if (!aligned.dates.length || !shown.length) return null;
    return {
      grid: { left: 44, right: 12, top: 22, bottom: 20 },
      legend: {
        top: 0,
        left: 0,
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
      },
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (
          ps: Array<{ seriesName?: string; value?: unknown; name?: string }>,
        ) => {
          const head = ps[0]?.name ?? '';
          const lines = ps.map(
            (p) =>
              `${p.seriesName ?? ''}：${tipNum(p.value as number, (n) => `${fmtYi(n, 1)} 亿`)}`,
          );
          return [`${head} 逐日类型净额（亿 HKD）`, ...lines].join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: xDates,
        axisLabel: { ...AXIS_LABEL, interval: Math.max(0, Math.floor(xDates.length / 8) - 1) },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: AXIS_LABEL,
        splitLine: SPLIT_LINE,
        name: '亿 HKD',
        nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
      },
      series: shown.map((c) => ({
        name: categoryLabel(c),
        type: 'line',
        stack: 'flow',
        symbol: 'none',
        smooth: false,
        connectNulls: false,
        lineStyle: { width: 0 },
        areaStyle: { opacity: 0.72 },
        emphasis: { focus: 'series' },
        color: INST_CATEGORY_COLORS[c] || '#94a3b8',
        data: aligned.byCat[c].map((p) => p.net_yi),
      })),
    };
  }, [aligned, shown, xDates]);

  /** 图 H：当日变动标的数堆叠柱（参与广度） */
  const breadthOption = useMemo(() => {
    if (!aligned.dates.length || !shown.length) return null;
    return {
      grid: { left: 38, right: 12, top: 22, bottom: 20 },
      legend: {
        top: 0,
        left: 0,
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
      },
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (ps: Array<{ seriesName?: string; value?: unknown; name?: string }>) => {
          const head = ps[0]?.name ?? '';
          const total = ps.reduce(
            (s, p) => s + (typeof p.value === 'number' && Number.isFinite(p.value) ? p.value : 0),
            0,
          );
          return [
            `${head} 当日变动标的数`,
            ...ps.map((p) => `${p.seriesName ?? ''}：${tipNum(p.value as number, (n) => fmtInt(n))}`),
            `合计：${fmtInt(total)}`,
          ].join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: xDates,
        axisLabel: { ...AXIS_LABEL, interval: Math.max(0, Math.floor(xDates.length / 8) - 1) },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        axisTick: { show: false },
      },
      yAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
      series: shown.map((c) => ({
        name: categoryLabel(c),
        type: 'bar',
        stack: 'breadth',
        barMaxWidth: 8,
        emphasis: { focus: 'series' },
        color: INST_CATEGORY_COLORS[c] || '#94a3b8',
        data: aligned.byCat[c].map((p) => p.n_symbols),
      })),
    };
  }, [aligned, shown, xDates]);

  /** 图 I：持仓市值横向条（存量格局） */
  const holdingOption = useMemo(() => {
    const rows = (overview?.categories ?? []).filter(
      (c) => c.holding_yi !== null && c.holding_yi !== undefined,
    );
    if (!rows.length) return null;
    const total = rows.reduce((s, r) => s + Math.abs(Number(r.holding_yi)), 0);
    return {
      grid: { left: 74, right: 56, top: 8, bottom: 18 },
      tooltip: {
        textStyle: { fontSize: 10 },
        formatter: (p: { name?: string; value?: unknown; data?: { pct?: number | null; n?: number | null } }) =>
          [
            p.name ?? '',
            `持仓 ${tipNum(p.value as number, (n) => `${fmtYi(n, 1)} 亿`)}`,
            `占比 ${p.data?.pct != null ? `${fmtNum(p.data.pct, 1)}%` : MISSING}`,
            `覆盖 ${fmtInt(p.data?.n ?? null)} 只`,
          ].join('<br/>'),
      },
      xAxis: {
        type: 'value',
        axisLabel: AXIS_LABEL,
        splitLine: SPLIT_LINE,
      },
      yAxis: {
        type: 'category',
        data: rows.map((r) => categoryLabel(r.category, r.category_label ?? r.label)),
        inverse: true,
        axisLabel: { ...AXIS_LABEL, fontSize: 10 },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barWidth: 12,
          data: rows.map((r) => ({
            value: r.holding_yi,
            pct: total > 0 ? (Math.abs(Number(r.holding_yi)) / total) * 100 : null,
            n: r.n_symbols,
            itemStyle: {
              color: INST_CATEGORY_COLORS[r.category] || '#94a3b8',
              borderRadius: [0, 3, 3, 0],
            },
          })),
          label: {
            show: true,
            position: 'right' as const,
            fontSize: 8,
            color: CHART_COLORS.neutral,
            formatter: (p: { value?: unknown }) =>
              tipNum(p.value as number, (n) => `${fmtNum(n, 0)}亿`),
          },
        },
      ],
    };
  }, [overview]);

  const windowDays = overview?.trends?.window ?? null;

  return (
    <div className="flex flex-col gap-4 min-w-0">
      {/* 类型开关（三图联动） */}
      <div className="flex items-center gap-1.5 flex-wrap px-1">
        <span className="text-[9px] font-extrabold text-slate-400">
          类型（点击摘除/恢复，三图联动）
        </span>
        {aligned.cats.map((c) => {
          const off = muted.has(c);
          const color = INST_CATEGORY_COLORS[c] || '#94a3b8';
          return (
            <button
              key={c}
              type="button"
              onClick={() => toggle(c)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-extrabold border transition-all ${
                off
                  ? 'bg-white text-slate-300 border-slate-200 line-through'
                  : 'bg-white text-slate-700 border-slate-200 hover:border-purple-300'
              }`}
            >
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: off ? '#cbd5e1' : color }}
              />
              {categoryLabel(c)}
            </button>
          );
        })}
        <span className="ml-auto text-[9px] font-mono text-slate-400">
          窗口 {windowDays != null ? `${fmtInt(windowDays)} 交易日` : MISSING}
        </span>
      </div>

      {/* 图 G 净流对称堆叠面积 */}
      <FaceCard
        bare
        icon={<Activity className="w-3.5 h-3.5 text-purple-600" />}
        title={
          <span className="flex items-center gap-1.5">
            类型净流生态
            <span className="text-[10px] font-normal text-slate-400">
              逐日净额正负对称堆叠（亿 HKD）· 正上负下
            </span>
          </span>
        }
      >
        <SmartChart
          option={flowOption}
          height={240}
          emptyText={loading ? '加载中…' : '趋势数据不可用'}
        />
        <p className="text-[9px] text-slate-400 leading-relaxed">
          堆叠高度 = 类型杠杆方向；正堆叠加负堆叠的镜像形状即类型间此消彼长。
          南向按有披露日累计（缺披露日断线不补 0）。
        </p>
      </FaceCard>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start min-w-0">
        {/* 图 H 参与广度 */}
        <FaceCard
          bare
          icon={<BarChart3 className="w-3.5 h-3.5 text-purple-600" />}
          title={
            <span className="flex items-center gap-1.5">
              参与广度
              <span className="text-[10px] font-normal text-slate-400">当日变动标的数</span>
            </span>
          }
        >
          <SmartChart
            option={breadthOption}
            height={200}
            emptyText={loading ? '加载中…' : '趋势数据不可用'}
          />
          <p className="text-[9px] text-slate-400 leading-relaxed">
            堆叠柱 = 当日各类型有净变动的标的数合计——柱体变高说明盘面参与面在扩大，
            单一类型独高说明变动集中在它的重仓股。
          </p>
        </FaceCard>

        {/* 图 I 持仓存量 */}
        <FaceCard
          bare
          icon={<Layers className="w-3.5 h-3.5 text-purple-600" />}
          title={
            <span className="flex items-center gap-1.5">
              持仓存量格局
              <span className="text-[10px] font-normal text-slate-400">各类型持仓市值（亿 HKD）</span>
            </span>
          }
        >
          <SmartChart
            option={holdingOption}
            height={200}
            emptyText={loading ? '加载中…' : '持仓数据不可用'}
          />
          <p className="text-[9px] text-slate-400 leading-relaxed">
            存量口径：最新披露持仓市值，与流量（净额）互补——流量看动作、存量看体量。
          </p>
        </FaceCard>
      </div>
    </div>
  );
};
