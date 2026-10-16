/** 聪明钱 · 生态趋势区（细案 §6.2-3：龙虎榜/机构/两融/大宗 4 图，60 交易日窗）。
 *
 * 口径：各源发布节奏不同，日期如实取自家序列（不做跨源对齐假设）；
 * 缺失值断线不补 0；双轴图右轴=占比/余额类（单位各自脚注）。
 */

import React, { useMemo } from 'react';
import { Activity } from 'lucide-react';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import type { SmartTrendRow } from '../../services/smartMoneyService';
import { MISSING, fmtNum } from '../../smartMoneyVocabulary';
import {
  AXIS_LABEL,
  BASE_GRID,
  CHART_COLORS,
  SPLIT_LINE,
  SmartChart,
  tipNum,
} from './charts';

interface SmartMoneyEcoTrendSectionProps {
  trends: Record<string, SmartTrendRow[]> | null;
  loading?: boolean;
}

type TrendDef = {
  key: string;
  title: string;
  /** 左轴序列（金额/家数；key=趋势列名） */
  left: { col: string; name: string; unit: string; color: string; signed?: boolean };
  /** 右轴序列（占比/余额；可缺） */
  right?: { col: string; name: string; unit: string; color: string };
  footnote: string;
};

const DEFS: TrendDef[] = [
  {
    key: 'lhb',
    title: '龙虎榜生态',
    left: { col: 'lhb_n_stocks', name: '上榜家数', unit: '家', color: CHART_COLORS.score },
    right: { col: 'lhb_net_buy_yi', name: '净买额', unit: '亿', color: CHART_COLORS.neutral },
    footnote: '柱=上榜家数（左轴）· 线=净买额亿元（右轴）',
  },
  {
    key: 'inst',
    title: '机构席位',
    left: { col: 'inst_n_stocks', name: '机构上榜家数', unit: '家', color: CHART_COLORS.score },
    right: { col: 'inst_net_buy_yi', name: '机构净买', unit: '亿', color: CHART_COLORS.neutral },
    footnote: '柱=机构上榜家数（左轴）· 线=机构净买额亿元（右轴）',
  },
  {
    key: 'margin',
    title: '两融',
    left: { col: 'margin_net_5d_yi', name: '净买入·5日', unit: '亿', color: CHART_COLORS.score, signed: true },
    right: { col: 'margin_bal_yi', name: '融资余额', unit: '亿', color: CHART_COLORS.sub },
    footnote: '柱=融资净买入 5 日（亿元，红正绿负）· 线=融资余额亿元（右轴）· 余额按最近披露值口径',
  },
  {
    key: 'dzjy',
    title: '大宗交易',
    left: { col: 'dzjy_amount_yi', name: '成交额', unit: '亿', color: CHART_COLORS.score },
    right: { col: 'dzjy_premium_share', name: '溢价成交占比', unit: '%', color: CHART_COLORS.sub },
    footnote: '柱=成交额亿元（左轴）· 线=溢价成交占比 %（右轴）',
  },
];

const TrendCard: React.FC<{ def: TrendDef; rows: SmartTrendRow[] }> = ({ def, rows }) => {
  const option = useMemo(() => {
    const dates = rows.map((r) => String(r.date).slice(5));
    const leftVals = rows.map((r) => {
      const v = r[def.left.col];
      return v === null || v === undefined ? null : Number(v);
    });
    const rightVals = def.right
      ? rows.map((r) => {
          const v = r[def.right!.col];
          if (v === null || v === undefined) return null;
          // 占比列（0–1 分数）→ 百分数展示
          return def.right!.unit === '%' ? Number(v) * 100 : Number(v);
        })
      : null;
    const u = def.left.unit;
    const ru = def.right?.unit ?? '';
    return {
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (ps: Array<{ dataIndex: number }>) => {
          const i = ps[0]?.dataIndex ?? 0;
          const r = rows[i];
          if (!r) return '';
          const lv = leftVals[i];
          const rv = rightVals ? rightVals[i] : null;
          const lines = [String(r.date)];
          lines.push(`${def.left.name} ${tipNum(lv, (n) => `${fmtNum(n, 2)}${u}`)}`);
          if (def.right) lines.push(`${def.right.name} ${tipNum(rv, (n) => `${fmtNum(n, 2)}${ru}`)}`);
          return lines.join('<br/>');
        },
      },
      grid: { ...BASE_GRID, right: def.right ? 42 : 12 },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: true,
        axisLabel: { ...AXIS_LABEL, interval: Math.floor(dates.length / 6) },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      },
      yAxis: [
        { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
        ...(def.right
          ? [
              {
                type: 'value',
                axisLabel: AXIS_LABEL,
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      series: [
        {
          name: def.left.name,
          type: 'bar',
          data: leftVals,
          barMaxWidth: 6,
          itemStyle: def.left.signed
            ? {
                // 金额正负 → A股红涨绿跌（逐柱着色；0/缺失中性）
                color: (p: { value: number | null }) =>
                  p.value === null
                    ? CHART_COLORS.axis
                    : p.value > 0
                      ? CHART_COLORS.pos
                      : p.value < 0
                        ? CHART_COLORS.neg
                        : CHART_COLORS.neutral,
                opacity: 0.85,
              }
            : { color: def.left.color, opacity: 0.85 },
        },
        ...(def.right && rightVals
          ? [
              {
                name: def.right.name,
                type: 'line',
                yAxisIndex: 1,
                data: rightVals,
                smooth: true,
                symbol: 'none',
                connectNulls: false,
                lineStyle: { width: 1.6, color: def.right.color },
                itemStyle: { color: def.right.color },
              },
            ]
          : []),
      ],
    };
  }, [def, rows]);

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-1 px-1">
        <span className="text-[10px] font-extrabold text-slate-600">{def.title}</span>
        <span className="text-[9px] text-slate-400 truncate">{def.footnote}</span>
      </div>
      <SmartChart option={option} height={140} />
    </div>
  );
};

export const SmartMoneyEcoTrendSection: React.FC<SmartMoneyEcoTrendSectionProps> = ({
  trends,
  loading,
}) => {
  const hasAny = DEFS.some((d) => (trends?.[d.key]?.length ?? 0) > 0);
  return (
    <SectionCard
      icon={<Activity className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          生态趋势
          <span className="text-[10px] font-normal text-slate-400">
            各源最近 60 交易日 · 缺失断线（{MISSING} 不为 0）
          </span>
        </span>
      }
    >
      {loading && !hasAny ? (
        <EmptyHint loading />
      ) : !hasAny ? (
        <EmptyHint text="趋势数据尚未生成（随每日构建产出）" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 min-w-0">
          {DEFS.map((d) => (
            <TrendCard key={d.key} def={d} rows={trends?.[d.key] ?? []} />
          ))}
        </div>
      )}
    </SectionCard>
  );
};
