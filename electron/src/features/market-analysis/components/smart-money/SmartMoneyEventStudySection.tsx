/** 聪明钱 · 事件研究区（细案 §6.2-5：4 族 CAR 曲线，族内当日池分位 q5 vs q1）。
 *
 * 口径：事件=T+2..T+car_days+1 收盘出场（k=i+2；k=2 ⇔ 1 个交易日持有）；
 * 曲线=超额累计（减全市场等权），bp；q5=当日池分位 ≥80、q1 ≤20。
 * 组=身份不是涨跌 → 紫(q5)/蓝(q1) 双色，不占红绿语义；缺失断线不补 0。
 */

import React, { useMemo } from 'react';
import { FlaskRound } from 'lucide-react';
import { EmptyHint, FaceCard } from '../../../market-analysis-shared/ui';
import type { SmartEventFamily, SmartEventStudyResponse } from '../../services/smartMoneyService';
import { CAR_STRONG_COLOR, CAR_WEAK_COLOR, MISSING, fmtSigned } from '../../smartMoneyVocabulary';
import { AXIS_LABEL, BASE_GRID, CHART_COLORS, SmartChart, tipNum } from './charts';

const FAMILY_ORDER = ['龙虎榜', '机构席位', '大宗交易', '机构调研'];

interface SmartMoneyEventStudySectionProps {
  data: SmartEventStudyResponse | null;
  loading?: boolean;
  /** true = 无卡壳（统一大框内的标签面） */
  bare?: boolean;
}

const FamilyCard: React.FC<{ name: string; fam: SmartEventFamily; carDays: number }> = ({
  name,
  fam,
  carDays,
}) => {
  const option = useMemo(() => {
    const ks = Array.from({ length: carDays }, (_, i) => i + 2);
    const labels = ks.map((k) => `T+${k}`);
    const q5 = fam.car_q5_bp.map((v) => (v === null ? null : Number(v)));
    const q1 = fam.car_q1_bp.map((v) => (v === null ? null : Number(v)));
    return {
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (ps: Array<{ dataIndex: number }>) => {
          const i = ps[0]?.dataIndex ?? 0;
          const k = i + 2;
          const a = q5[i];
          const b = q1[i];
          const lines = [`${name} · T+${k} 收盘`];
          lines.push(`分位≥80 组 ${tipNum(a, (n) => `${fmtSigned(n, 1)}bp`)}`);
          lines.push(`分位≤20 组 ${tipNum(b, (n) => `${fmtSigned(n, 1)}bp`)}`);
          if (a !== null && b !== null) {
            lines.push(`差 ${fmtSigned(a - b, 1)}bp`);
          }
          return lines.join('<br/>');
        },
      },
      legend: {
        show: true,
        top: 0,
        right: 0,
        itemWidth: 10,
        itemHeight: 6,
        textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
        data: ['分位≥80（强）', '分位≤20（弱）'],
      },
      grid: { ...BASE_GRID, top: 22 },
      xAxis: {
        type: 'category',
        data: labels,
        boundaryGap: false,
        axisLabel: { ...AXIS_LABEL, interval: 4 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${v.toFixed(0)}` },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      },
      series: [
        {
          name: '分位≥80（强）',
          type: 'line',
          data: q5,
          smooth: true,
          symbol: 'none',
          connectNulls: false,
          lineStyle: { width: 1.8, color: CAR_STRONG_COLOR },
          itemStyle: { color: CAR_STRONG_COLOR },
        },
        {
          name: '分位≤20（弱）',
          type: 'line',
          data: q1,
          smooth: true,
          symbol: 'none',
          connectNulls: false,
          lineStyle: { width: 1.6, color: CAR_WEAK_COLOR },
          itemStyle: { color: CAR_WEAK_COLOR },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: CHART_COLORS.axis, type: 'dashed' },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    };
  }, [name, fam, carDays]);

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-1 px-1">
        <span className="text-[10px] font-extrabold text-slate-600">{name}</span>
        <span className="text-[9px] text-slate-400 font-mono">
          事件 {fam.n_events} · 入池 {fam.n_scored}（q5 {fam.n_q5 ?? MISSING}/q1 {fam.n_q1 ?? MISSING}）
        </span>
      </div>
      <SmartChart option={option} height={140} />
    </div>
  );
};

export const SmartMoneyEventStudySection: React.FC<SmartMoneyEventStudySectionProps> = ({
  data,
  loading,
  bare = false,
}) => {
  const families = data?.families ?? {};
  const names = FAMILY_ORDER.filter((n) => families[n]).concat(
    Object.keys(families).filter((n) => !FAMILY_ORDER.includes(n)),
  );
  return (
    <FaceCard
      bare={bare}
      icon={<FlaskRound className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          事件研究 · CAR（历史回溯统计，非预测）
          <span className="text-[10px] font-normal text-slate-400">
            同类事件标的按当日池分位分组 · 超额=减全市场等权（bp）· T+k 收盘出场
          </span>
        </span>
      }
      extra={
        data?.as_of ? (
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            截至 {data.as_of}
          </span>
        ) : null
      }
    >
      {loading && !data ? (
        <EmptyHint loading />
      ) : !names.length ? (
        <EmptyHint text="事件研究尚未生成（随每日构建产出）" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 min-w-0">
          {names.map((n) => (
            <FamilyCard
              key={n}
              name={n}
              fam={families[n]}
              carDays={families[n].car_q5_bp?.length ?? data?.car_days ?? 20}
            />
          ))}
        </div>
      )}
    </FaceCard>
  );
};
