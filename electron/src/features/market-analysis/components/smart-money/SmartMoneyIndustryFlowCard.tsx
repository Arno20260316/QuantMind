/** 聪明钱 · 行业净买（细案 §3.5：20 日机构净买 + 机构大宗接盘，按行业聚合）。
 *
 * 口径：金额=亿元；横柱=机构净买 + 机构专用大宗净买（合并），逐柱按正负红绿；
 * 排序=合并净买绝对值降序取前 12（服务端已按该口径排序，此处复排防御）。
 */

import React, { useMemo } from 'react';
import { Factory } from 'lucide-react';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import type { SmartIndustryRow } from '../../services/smartMoneyService';
import { MISSING, fmtNum, fmtSigned } from '../../smartMoneyVocabulary';
import { AXIS_LABEL, CHART_COLORS, SmartChart } from './charts';

const TOP_N = 12;

interface SmartMoneyIndustryFlowCardProps {
  rows: SmartIndustryRow[] | null | undefined;
  loading?: boolean;
}

interface FlowRow {
  industry: string;
  inst: number | null;
  dzjy: number | null;
  combined: number | null;
  n_stocks: number | null;
  avg_score: number | null;
}

export const SmartMoneyIndustryFlowCard: React.FC<SmartMoneyIndustryFlowCardProps> = ({
  rows,
  loading,
}) => {
  const flow: FlowRow[] = useMemo(() => {
    const list = (rows ?? []).map((r) => {
      const inst = r.inst_net_yi === null ? null : Number(r.inst_net_yi);
      const dzjy = r.dzjy_inst_net_yi === null ? null : Number(r.dzjy_inst_net_yi);
      const combined = inst === null && dzjy === null ? null : (inst ?? 0) + (dzjy ?? 0);
      return {
        industry: r.industry,
        inst,
        dzjy,
        combined,
        n_stocks: r.n_stocks,
        avg_score: r.avg_score,
      };
    });
    return list
      .filter((r) => r.combined !== null)
      .sort((a, b) => Math.abs(b.combined ?? 0) - Math.abs(a.combined ?? 0))
      .slice(0, TOP_N);
  }, [rows]);

  const option = useMemo(() => {
    if (!flow.length) return null;
    // 横向柱自下而上：反转让最大净买在最上
    const ordered = [...flow].reverse();
    const labels = ordered.map((r) => r.industry);
    const vals = ordered.map((r) => r.combined);
    return {
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (ps: Array<{ dataIndex: number }>) => {
          const i = ps[0]?.dataIndex ?? 0;
          const r = ordered[i];
          if (!r) return '';
          return [
            r.industry,
            `机构净买 ${r.inst === null ? MISSING : `${fmtSigned(r.inst, 2)} 亿`}`,
            `机构大宗接盘 ${r.dzjy === null ? MISSING : `${fmtSigned(r.dzjy, 2)} 亿`}`,
            `覆盖标的 ${r.n_stocks ?? MISSING} · 池均分 ${r.avg_score === null ? MISSING : fmtNum(r.avg_score, 1)}`,
          ].join('<br/>');
        },
      },
      grid: { left: 76, right: 40, top: 6, bottom: 18 },
      xAxis: {
        type: 'value',
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${v.toFixed(0)}` },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      },
      yAxis: {
        type: 'category',
        data: labels,
        axisLabel: { fontSize: 9, color: CHART_COLORS.neutral },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      },
      series: [
        {
          type: 'bar',
          data: vals,
          barMaxWidth: 12,
          itemStyle: {
            color: (p: { value: number | null }) =>
              p.value === null
                ? CHART_COLORS.axis
                : p.value > 0
                  ? CHART_COLORS.pos
                  : p.value < 0
                    ? CHART_COLORS.neg
                    : CHART_COLORS.neutral,
          },
          label: {
            show: true,
            position: 'right',
            fontSize: 9,
            color: CHART_COLORS.neutral,
            formatter: (p: { value: number | null }) =>
              p.value === null ? '' : `${p.value > 0 ? '+' : ''}${p.value.toFixed(1)}`,
          },
        },
      ],
    };
  }, [flow]);

  return (
    <SectionCard
      icon={<Factory className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          行业净买（20 日）
          <span className="text-[10px] font-normal text-slate-400">
            机构净买 + 机构大宗接盘（亿）· 按绝对值前 {TOP_N}
          </span>
        </span>
      }
    >
      {loading && !rows ? (
        <EmptyHint loading />
      ) : !flow.length ? (
        <EmptyHint text="行业聚合尚未生成（随每日构建产出）" />
      ) : (
        <>
          <SmartChart option={option} height={Math.max(160, flow.length * 20 + 40)} />
          <div className="text-[9px] text-slate-400 px-1">
            行业=申万一级（rs_hyname）；正=净买入（红），负=净卖出（绿）；机构大宗=买方为「机构专用」的成交。
          </div>
        </>
      )}
    </SectionCard>
  );
};
