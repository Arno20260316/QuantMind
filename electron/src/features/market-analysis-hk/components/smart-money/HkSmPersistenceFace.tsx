/** 港股聪明钱 · 面 5「稳健性与口径」（细案 §4.5——机构级升级的诚实底座）。
 *
 * 图 J 前/后半持续性散点：x=前半技能、y=后半技能、四象限 + 秩相关标注——
 *   点云若无对角线趋势即为「席位层弱持续」的实证（描述性≠可跟随）；
 * 图 K 四分位 OOS 柱：入柱池按某分位分四组，验证组序与事后技能/胜率的单调性；
 * 类别前后半对比条：类型层（而非席位层）的持续性——类型结论比单席位稳；
 * 口径与裁决（证据台并入）+ 口径脚注全集（各面文案唯一出处在此收口）。
 * 缺失一律「—」绝不为 0。
 */

import React, { useMemo } from 'react';
import { ShieldCheck, Scale } from 'lucide-react';
import type {
  HkSmEvidenceResponse,
  HkSmPersistenceResponse,
} from '../../services/api';
import { EmptyHint, FaceCard } from '../../../market-analysis-shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  AXIS_LABEL,
  CHART_COLORS,
  SPLIT_LINE,
  SmartChart,
  signedColor,
  tipNum,
} from './charts';
import {
  ADMIT_NOTE,
  EPISODE_NOTE,
  FWD_NOTE,
  HIT_NOTE,
  LB_NOTE,
  MISSING,
  PERSIST_NOTE,
  SAMPLE_NOTE,
  SHRINK_NOTE,
  SKILL_NOTE,
  SUSPECT_NOTE,
  VW_NOTE,
  categoryLabel,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtPct,
} from '../../hkSmartMoneyVocabulary';
import { HkSmEvidenceCard } from './HkSmEvidenceCard';

interface HkSmPersistenceFaceProps {
  data: HkSmPersistenceResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** 证据台数据（本面并入展示；由面板常持） */
  evidence: HkSmEvidenceResponse | null;
  evidenceLoading: boolean;
}

const HALF_FIRST = '#cbd5e1'; // 前半·灰
const HALF_SECOND = '#7c3aed'; // 后半·紫

/** 图 J：持续性散点（四象限 + 零轴） */
function buildScatterOption(data: HkSmPersistenceResponse | null) {
  const pts = (data?.scatter ?? []).filter(
    (p) => p.skill1_bp !== null && p.skill2_bp !== null,
  );
  if (!pts.length) return null;
  const xs = pts.map((p) => Number(p.skill1_bp));
  const ys = pts.map((p) => Number(p.skill2_bp));
  const xMin = Math.min(0, ...xs);
  const xMax = Math.max(0, ...xs);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(0, ...ys);
  return {
    grid: { left: 44, right: 14, top: 14, bottom: 26 },
    tooltip: {
      textStyle: { fontSize: 10 },
      formatter: (p: {
        data?: {
          name?: string;
          value?: [number | null, number | null];
          n1?: number | null;
          n2?: number | null;
        };
      }) => {
        const d = p.data;
        if (!d) return MISSING;
        const [x, y] = d.value ?? [null, null];
        return [
          d.name ?? MISSING,
          `前半技能 ${tipNum(x, (n) => `${fmtBp(n)} bp`)}（n ${fmtInt(d.n1 ?? null)}）`,
          `后半技能 ${tipNum(y, (n) => `${fmtBp(n)} bp`)}（n ${fmtInt(d.n2 ?? null)}）`,
        ].join('<br/>');
      },
    },
    xAxis: {
      type: 'value',
      min: xMin,
      max: xMax,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: '前半技能（bp）',
      nameLocation: 'middle',
      nameGap: 18,
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    yAxis: {
      type: 'value',
      min: yMin,
      max: yMax,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: '后半技能（bp）',
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    series: [
      {
        type: 'scatter',
        symbolSize: 7,
        data: pts.map((p) => ({
          value: [p.skill1_bp, p.skill2_bp],
          name: p.name ?? p.participant_id,
          n1: p.n1,
          n2: p.n2,
          itemStyle: {
            color: INST_CATEGORY_COLORS[p.category || ''] || '#94a3b8',
            opacity: 0.85,
          },
        })),
        markArea: {
          silent: true,
          label: { show: true, fontSize: 8, color: CHART_COLORS.sub },
          data: [
            [
              { xAxis: 0, yAxis: 0, itemStyle: { color: 'rgba(124,58,237,0.06)' }, label: { formatter: '两半皆正' } },
              { xAxis: xMax, yAxis: yMax },
            ],
            [
              { xAxis: xMin, yAxis: yMin, itemStyle: { color: 'rgba(148,163,184,0.10)' }, label: { formatter: '两半皆负' } },
              { xAxis: 0, yAxis: 0 },
            ],
          ],
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: CHART_COLORS.axis, type: 'dashed', width: 1 },
          label: { show: false },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
        },
      },
    ],
  };
}

/** 图 K：四分位 OOS 柱（技能 bp 柱 + 胜率 % 线，双轴） */
function buildQuartileOption(data: HkSmPersistenceResponse | null) {
  const rows = [...(data?.quartiles ?? [])].sort((a, b) => Number(a.q) - Number(b.q));
  if (!rows.length) return null;
  return {
    grid: { left: 46, right: 46, top: 24, bottom: 20 },
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
        ps: Array<{ seriesName?: string; value?: unknown; name?: string; dataIndex?: number }>,
      ) => {
        const idx = ps[0]?.dataIndex ?? 0;
        const r = rows[idx];
        return [
          `q${r?.q ?? MISSING} · 席位 ${fmtInt(r?.n_seats)} / 事件 ${fmtInt(r?.n_events)}`,
          ...ps.map((p) => {
            const v = p.value as number | null;
            return `${p.seriesName ?? ''}：${
              p.seriesName === '事后胜率'
                ? tipNum(v, (n) => fmtPct(n, 1))
                : tipNum(v, (n) => `${fmtBp(n)} bp`)
            }`;
          }),
        ].join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((r) => `q${r.q ?? MISSING}`),
      axisLabel: { ...AXIS_LABEL, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
      name: '按入柱池分位分四组',
      nameLocation: 'middle',
      nameGap: 20,
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    yAxis: [
      {
        type: 'value',
        axisLabel: AXIS_LABEL,
        splitLine: SPLIT_LINE,
        name: 'bp',
        nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
      },
      {
        type: 'value',
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${fmtNum(v * 100, 0)}%` },
        splitLine: { show: false },
        min: 0,
        max: 1,
      },
    ],
    series: [
      {
        name: '事后技能',
        type: 'bar',
        barWidth: 24,
        data: rows.map((r) => ({
          value: r.forward_skill_bp,
          itemStyle: {
            color:
              r.forward_skill_bp === null ? CHART_COLORS.axis : signedColor(r.forward_skill_bp),
            borderRadius: [3, 3, 0, 0],
          },
        })),
        label: {
          show: true,
          position: 'top' as const,
          fontSize: 8,
          color: CHART_COLORS.neutral,
          formatter: (p: { value?: unknown }) => tipNum(p.value as number, (n) => fmtBp(n, 0)),
        },
      },
      {
        name: '事后胜率',
        type: 'line',
        yAxisIndex: 1,
        symbolSize: 5,
        lineStyle: { width: 1.5, color: CHART_COLORS.score },
        itemStyle: { color: CHART_COLORS.score },
        data: rows.map((r) => r.forward_hit),
      },
    ],
  };
}

/** 类别前后半对比：分组横条（灰=前半 / 紫=后半） */
function buildHalvesOption(data: HkSmPersistenceResponse | null) {
  const rows = data?.category_halves ?? [];
  if (!rows.length) return null;
  const bar = (pick: (r: (typeof rows)[number]) => number | null, name: string, color: string) => ({
    name,
    type: 'bar' as const,
    barWidth: 9,
    data: rows.map((r) => ({
      value: pick(r),
      itemStyle: { color: pick(r) === null ? CHART_COLORS.axis : color },
    })),
    label: {
      show: true,
      position: 'right' as const,
      fontSize: 8,
      color: CHART_COLORS.neutral,
      formatter: (p: { value?: unknown }) => tipNum(p.value as number, (n) => fmtBp(n, 0)),
    },
  });
  return {
    grid: { left: 74, right: 40, top: 24, bottom: 18 },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      textStyle: { fontSize: 10 },
      formatter: (ps: Array<{ seriesName?: string; value?: unknown; name?: string }>) => {
        const head = ps[0]?.name ?? '';
        return [
          `${head}（vw 技能，bp）`,
          ...ps.map(
            (p) => `${p.seriesName ?? ''}：${tipNum(p.value as number, (n) => fmtBp(n))}`,
          ),
        ].join('<br/>');
      },
    },
    xAxis: { type: 'value', axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: {
      type: 'category',
      data: rows.map((r) => categoryLabel(r.category ?? '')),
      inverse: true,
      axisLabel: { ...AXIS_LABEL, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
    },
    series: [
      bar((r) => r.first_half_vw_skill_bp, '前半', HALF_FIRST),
      bar((r) => r.second_half_vw_skill_bp, '后半', HALF_SECOND),
    ],
  };
}

/** 口径脚注全集（各面用词唯一出处收口） */
const ALL_NOTES: Array<{ k: string; v: string }> = [
  { k: '方向技能', v: SKILL_NOTE },
  { k: '90% 下界', v: LB_NOTE },
  { k: '大钱技能', v: VW_NOTE },
  { k: '历史胜率', v: HIT_NOTE },
  { k: '收缩估计', v: SHRINK_NOTE },
  { k: 'episode 归并', v: EPISODE_NOTE },
  { k: '过户嫌疑', v: SUSPECT_NOTE },
  { k: '事后超额', v: FWD_NOTE },
  { k: '入柱判据', v: ADMIT_NOTE },
  { k: '样本纪律', v: SAMPLE_NOTE },
  { k: '持续性审计', v: PERSIST_NOTE },
];

export const HkSmPersistenceFace: React.FC<HkSmPersistenceFaceProps> = ({
  data,
  loading,
  error,
  onRetry,
  evidence,
  evidenceLoading,
}) => {
  const scatterOption = useMemo(() => buildScatterOption(data), [data]);
  const quartileOption = useMemo(() => buildQuartileOption(data), [data]);
  const halvesOption = useMemo(() => buildHalvesOption(data), [data]);
  const sp = data?.spearman;

  return (
    <div className="flex flex-col gap-4 min-w-0">
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5">
          <span className="text-[10px] text-amber-800 min-w-0 truncate">
            持续性审计数据不可用：{error}
          </span>
          <button
            type="button"
            onClick={onRetry}
            className="ml-auto shrink-0 text-[10px] font-bold text-amber-700 hover:text-amber-900"
          >
            重试
          </button>
        </div>
      )}
      {!data && !error && (
        <EmptyHint
          loading={loading}
          text="持续性审计未构建（首次构建后展示）"
        />
      )}

      {data && (
        <>
          {/* 图 J 持续性散点 */}
          <FaceCard
            bare
            icon={<ShieldCheck className="w-3.5 h-3.5 text-purple-600" />}
            title={
              <span className="flex items-center gap-1.5 flex-wrap">
                持续性审计 · 前半强 → 后半还强吗
                <span className="text-[10px] font-normal text-slate-400 font-mono">
                  秩相关 h10 {fmtNum(sp?.h10, 3)} · h20 {fmtNum(sp?.h20, 3)} · 命中{' '}
                  {fmtNum(sp?.hit, 3)}
                </span>
              </span>
            }
            extra={
              <span className="text-[9px] font-mono text-slate-400">
                切分日 {data.split_date ?? MISSING} · 席位 {fmtInt(data.scatter?.length ?? null)} 个
              </span>
            }
          >
            <div className="grid grid-cols-1 xl:grid-cols-[1.5fr_1fr] gap-4 items-start min-w-0">
              <SmartChart
                option={scatterOption}
                height={250}
                emptyText="持续性散点不可用"
              />
              <p className="text-[9px] text-slate-400 leading-relaxed">
                {data.method?.scatter ??
                  '每点一个席位：x=前半场技能、y=后半场技能。若优势可跟随，点云应沿对角线排列；实测秩相关≈0.12（弱正）——点云近乎圆形，这就是「描述性盘点」的实证。'}
                <br />
                <span className="font-bold">两半皆正</span>的席位（右上角）仍需结合 n
                阅读：小样本的前半强往往只是噪声。
              </p>
            </div>
          </FaceCard>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start min-w-0">
            {/* 图 K 四分位 OOS */}
            <FaceCard
              bare
              icon={<Scale className="w-3.5 h-3.5 text-purple-600" />}
              title={
                <span className="flex items-center gap-1.5">
                  四分位 OOS 检验
                  <span className="text-[10px] font-normal text-slate-400">
                    组序 vs 事后技能/胜率单调性
                  </span>
                </span>
              }
            >
              <SmartChart
                option={quartileOption}
                height={220}
                emptyText="四分位数据不可用"
              />
              <p className="text-[9px] text-slate-400 leading-relaxed">
                {data.method?.quartile ??
                  '入柱池按当日某分位分四组，观察组序与事后技能的单调关系——若 q4 不比 q1 好，说明该分位的组内区分度有限。'}
              </p>
            </FaceCard>

            {/* 类别前后半对比 */}
            <FaceCard
              bare
              title={
                <span className="flex items-center gap-1.5">
                  类别前后半对比
                  <span className="text-[10px] font-normal text-slate-400">
                    类型层持续性（vw 技能，bp）
                  </span>
                </span>
              }
            >
              <SmartChart
                option={halvesOption}
                height={220}
                emptyText="类别前后半数据不可用"
              />
              <p className="text-[9px] text-slate-400 leading-relaxed">
                席位层弱持续 ≠ 类型层也弱：若某类型的灰条与紫条同号，该类型的方向判断比
                单席位更站得住——机构级结论优先落在类型层。
              </p>
            </FaceCard>
          </div>
        </>
      )}

      {data?.method?.note && (
        <p className="text-[9px] text-slate-400 leading-relaxed px-1">{data.method.note}</p>
      )}

      {/* 口径与裁决（证据台并入） */}
      <HkSmEvidenceCard evidence={evidence} loading={evidenceLoading} bare />

      {/* 口径脚注全集 */}
      <FaceCard bare title="口径脚注全集（各面用词唯一出处）">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-5 gap-y-1 px-1">
          {ALL_NOTES.map(({ k, v }) => (
            <div key={k} className="text-[9px] text-slate-500 leading-relaxed">
              <span className="font-bold text-slate-600">{k}：</span>
              {v}
            </div>
          ))}
        </div>
      </FaceCard>
    </div>
  );
};
