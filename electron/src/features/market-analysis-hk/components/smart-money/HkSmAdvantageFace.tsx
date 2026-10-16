/** 港股聪明钱 · 面 1「机构优势」（细案 §4.2——核心答「哪些机构更有优势」）。
 *
 * 图 A 类别优势：技能均值 + 大钱技能（vw）双序列横向条，正负分色；
 * 3 枚 mini stat：加权技能 / 技能为正类型数 / 类型极差（全部由 categories 现算）；
 * 表 B 席位优势榜（自持取数，onMeta 回传本面共用）；
 * 图 C 技能散点：x=log10(可测样本 n)、y=技能(bp)、色=类别、点径=单笔均额，
 * 右侧样本 < 50 区打灰 + 零轴——「样本越小越向两端散布」的可视化。
 */

import React, { useMemo, useState } from 'react';
import { HelpCircle, TrendingUp } from 'lucide-react';
import type { HkSmSeatItem, HkSmSeatsResponse, HkSmOverviewResponse } from '../../services/api';
import { FaceCard } from '../../../market-analysis-shared/ui';
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
  MISSING,
  SKILL_NOTE,
  VW_NOTE,
  categoryLabel,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtValueM,
  signCls,
} from '../../hkSmartMoneyVocabulary';
import { HkSmSeatBoard } from './HkSmSeatBoard';

const CCASS_ROWS = ['cn_broker', 'hk', 'us_eu', 'apac', 'other'] as const;

interface HkSmAdvantageFaceProps {
  overview: HkSmOverviewResponse | null;
  overviewLoading: boolean;
  refreshToken: number;
  onPickSeat: (participantId: string) => void;
}

/** 图 A：类别优势（技能 vs 大钱技能双序列，逐条正负分色） */
function buildCategoryOption(overview: HkSmOverviewResponse | null) {
  const rows = (overview?.categories ?? []).filter(
    (c) => (c.skill_bp !== null && c.skill_bp !== undefined) || (c.vw_skill_bp !== null && c.vw_skill_bp !== undefined),
  );
  if (!rows.length) return null;
  const names = rows.map((r) => categoryLabel(r.category, r.category_label ?? r.label));
  const bar = (pick: (r: (typeof rows)[number]) => number | null | undefined, label: string) => ({
    name: label,
    type: 'bar' as const,
    barWidth: 9,
    barGap: '30%',
    data: rows.map((r) => {
      const v = pick(r);
      const n = v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v);
      return {
        value: n,
        itemStyle: { color: n === null ? CHART_COLORS.axis : signedColor(n) },
      };
    }),
    label: {
      show: true,
      position: 'right' as const,
      fontSize: 8,
      color: CHART_COLORS.neutral,
      formatter: (p: { value?: unknown }) => tipNum(p.value as number, (n) => fmtNum(n, 0)),
    },
  });
  return {
    grid: { left: 78, right: 42, top: 26, bottom: 20 },
    legend: {
      top: 0,
      right: 4,
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
        const lines = ps.map(
          (p) =>
            `${p.seriesName ?? ''}：${tipNum(p.value as number, (n) => `${fmtBp(n)} bp`)}`,
        );
        return [`${head}（事件级方向技能，bp）`, ...lines].join('<br/>');
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
    },
    yAxis: {
      type: 'category',
      data: names,
      inverse: true,
      axisLabel: { ...AXIS_LABEL, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
    },
    series: [bar((r) => r.skill_bp, '技能（均值）'), bar((r) => r.vw_skill_bp, '大钱技能（|Δvalue|加权）')],
  };
}

/** 面 1 mini stat：加权技能 / 正技能类型数 / 类型极差（categories 现算，缺失即「—」） */
function derivedStats(overview: HkSmOverviewResponse | null) {
  const rows = (overview?.categories ?? []).filter(
    (c) => CCASS_ROWS.includes(c.category as (typeof CCASS_ROWS)[number]) && c.skill_bp !== null,
  );
  let weighted: number | null = null;
  const wsum = rows.reduce((s, r) => s + (r.n_lead_events ?? 0), 0);
  if (wsum > 0) {
    weighted =
      rows.reduce((s, r) => s + (r.skill_bp ?? 0) * (r.n_lead_events ?? 0), 0) / wsum;
  }
  const vw = rows
    .map((r) => r.vw_skill_bp)
    .filter((v): v is number => v !== null && v !== undefined);
  return {
    weighted,
    posCount: rows.filter((r) => (r.skill_bp ?? 0) > 0).length,
    totalCount: rows.length,
    vwSpread: vw.length >= 2 ? Math.max(...vw) - Math.min(...vw) : null,
  };
}

/** 图 C：技能散点（可从席位响应切换窗口/门槛联动刷新） */
function buildScatterOption(items: HkSmSeatItem[]): {
  option: Record<string, unknown> | null;
  cats: string[];
} {
  const pts = items.filter(
    (it) =>
      it.skill_bp !== null &&
      it.n_exc10 !== null &&
      Number(it.n_exc10) >= 10, // x=log10，n<10 轴端无意义（且 90% 下界门槛在 5）
  );
  if (!pts.length) return { option: null, cats: [] };
  const nMax = Math.max(...pts.map((p) => Number(p.n_exc10)));
  const xMin = 1; // log10(10)
  const xMax = Math.max(Math.log10(nMax), Math.log10(50));
  const n50 = Math.log10(50);
  const cats = [...new Set(pts.map((p) => p.category))];
  const option: Record<string, unknown> = {
    grid: { left: 40, right: 16, top: 18, bottom: 24 },
    tooltip: {
      textStyle: { fontSize: 10 },
      formatter: (p: { data?: { name?: string; n?: number | null; skill?: number | null; vw?: number | null; avgM?: number | null; catLabel?: string } }) => {
        const d = p.data ?? {};
        return [
          d.name ?? MISSING,
          `${d.catLabel ?? ''} · 可测样本 ${fmtInt(d.n ?? null)}`,
          `技能 ${fmtBp(d.skill ?? null)} bp`,
          `大钱技能 ${fmtBp(d.vw ?? null)} bp`,
          `单笔均额 ${fmtValueM(d.avgM ?? null)}`,
        ].join('<br/>');
      },
    },
    xAxis: {
      type: 'value',
      min: xMin,
      max: xMax,
      axisLabel: {
        ...AXIS_LABEL,
        formatter: (v: number) => {
          const n = Math.pow(10, v);
          return n >= 10000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`;
        },
      },
      splitLine: SPLIT_LINE,
      name: '可测样本 n（对数轴）',
      nameLocation: 'end',
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    yAxis: {
      type: 'value',
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: '技能 bp',
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    series: [
      {
        type: 'scatter',
        data: pts.map((p) => ({
          value: [Math.log10(Number(p.n_exc10)), p.skill_bp],
          name: p.participant_name ?? p.participant_id,
          n: p.n_exc10,
          skill: p.skill_bp,
          vw: p.vw_skill_bp,
          avgM: p.avg_value_m,
          catLabel: categoryLabel(p.category, p.category_label),
          itemStyle: { color: INST_CATEGORY_COLORS[p.category] || '#94a3b8', opacity: 0.85 },
        })),
        symbolSize: (_v: unknown, p: { data?: { avgM?: number | null } }) => {
          const m = p.data?.avgM;
          if (m === null || m === undefined || !Number.isFinite(Number(m))) return 6;
          return Math.min(16, Math.max(4, 4 + Math.log10(Math.max(Number(m), 1) + 1) * 3));
        },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(148,163,184,0.10)' },
          label: {
            show: true,
            position: 'insideTopLeft',
            fontSize: 8,
            color: CHART_COLORS.sub,
            formatter: '样本 < 50：噪声区',
          },
          data: [[{ xAxis: xMin }, { xAxis: n50 }]],
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: CHART_COLORS.axis, type: 'dashed', width: 1 },
          label: { fontSize: 8, color: CHART_COLORS.sub, formatter: 'n=50' },
          data: [{ xAxis: n50 }, { yAxis: 0, label: { formatter: '0', fontSize: 8, color: CHART_COLORS.sub } }],
        },
      },
    ],
  };
  // 图例：逐点着色时 ECharts 图例不适用——类别色点行由组件另排
  return { option, cats };
}

function MiniStat({
  label,
  value,
  cls,
  tip,
}: {
  label: string;
  value: React.ReactNode;
  cls?: string;
  tip?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-white px-2.5 py-2 flex flex-col gap-0.5 min-w-0" title={tip}>
      <span className="text-[9px] text-slate-400 whitespace-nowrap">{label}</span>
      <span className={`text-sm font-mono font-extrabold ${cls ?? 'text-slate-700'}`}>{value}</span>
    </div>
  );
}

export const HkSmAdvantageFace: React.FC<HkSmAdvantageFaceProps> = ({
  overview,
  overviewLoading,
  refreshToken,
  onPickSeat,
}) => {
  const [seats, setSeats] = useState<HkSmSeatsResponse | null>(null);

  const catOption = useMemo(() => buildCategoryOption(overview), [overview]);
  const scatter = useMemo(() => buildScatterOption(seats?.items ?? []), [seats]);
  const stats = useMemo(() => derivedStats(overview), [overview]);
  const cats = scatter.cats;

  return (
    <div className="flex flex-col gap-4 min-w-0">
      {/* 图 A 类别优势 + 怎么读 */}
      <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_1fr] gap-4 items-start min-w-0">
        <FaceCard
          bare
          icon={<TrendingUp className="w-3.5 h-3.5 text-purple-600" />}
          title={
            <span className="flex items-center gap-1.5">
              类别优势
              <span className="text-[10px] font-normal text-slate-400">
                事件级方向技能（bp）· CCASS 类型（有技能数据者）
              </span>
            </span>
          }
        >
          <SmartChart
            option={catOption}
            height={186}
            emptyText={overviewLoading ? '加载中…' : '类别技能数据不可用'}
          />
          <p className="text-[9px] text-slate-400 leading-relaxed">
            正技能 = 该类型历史买入事件后跑赢、卖出事件后跑输（方向对）。口径：{SKILL_NOTE}
          </p>
        </FaceCard>

        <FaceCard
          bare
          icon={<HelpCircle className="w-3.5 h-3.5 text-purple-600" />}
          title={<span className="text-slate-700">怎么读这个面</span>}
        >
          <div className="grid grid-cols-3 gap-2">
            <MiniStat
              label="加权技能（按事件数）"
              value={stats.weighted === null ? MISSING : `${fmtBp(stats.weighted)}`}
              cls={signCls(stats.weighted)}
              tip="各类型技能按全史事件数加权——大盘级别的『方向对错』"
            />
            <MiniStat
              label="技能为正的类型"
              value={stats.totalCount ? `${stats.posCount} / ${stats.totalCount}` : MISSING}
              tip="有技能数据的 CCASS 类型中，技能 bp > 0 的类型数"
            />
            <MiniStat
              label="类型极差（vw）"
              value={stats.vwSpread === null ? MISSING : `${fmtNum(stats.vwSpread, 0)} bp`}
              tip="最强与最弱类型的 vw 技能之差——类型间的分化幅度"
            />
          </div>
          <ul className="flex flex-col gap-1 text-[10px] text-slate-500 leading-relaxed">
            <li>
              · 榜主排序 = 技能 <span className="font-bold">90% 下界</span>（技能均值 −
              1.28×标准误）：样本越小越沉底，浮顶被结构性压住。
            </li>
            <li>
              · 「技能」看方向对错，「大钱技能」按 |变动金额| 加权（{VW_NOTE}）。
            </li>
            <li>
              · 散点在榜下方：x=可测样本（对数），y=技能；<span className="font-bold">样本越小越向两端散布</span>
              ——左侧灰区是噪声区，右侧才是证据区。
            </li>
            <li>
              · 席位层持续性弱（第 5 面实测）：本面是<span className="font-bold">描述性盘点</span>，
              不是可跟随信号。
            </li>
          </ul>
        </FaceCard>
      </div>

      {/* 表 B 席位优势榜 v2 */}
      <HkSmSeatBoard
        bare
        refreshToken={refreshToken}
        onPickSeat={onPickSeat}
        onMeta={setSeats}
      />

      {/* 图 C 技能散点 */}
      <FaceCard
        bare
        title={
          <span className="flex items-center gap-1.5">
            技能散点 · 样本量 vs 优势
            <span className="text-[10px] font-normal text-slate-400">
              当前筛选的席位宇宙（与上表同批）
            </span>
          </span>
        }
        extra={
          <span className="flex items-center gap-2 flex-wrap">
            {cats.map((c) => (
              <span key={c} className="flex items-center gap-1 text-[9px] text-slate-500">
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ background: INST_CATEGORY_COLORS[c] || '#94a3b8' }}
                />
                {categoryLabel(c)}
              </span>
            ))}
            <span className="text-[9px] text-slate-400">点径 = 单笔均额</span>
          </span>
        }
      >
        <SmartChart
          option={scatter.option}
          height={230}
          emptyText="当前筛选下无可绘制席位（技能或样本缺失）"
        />
        <p className="text-[9px] text-slate-400 leading-relaxed">
          每点一个席位：x=事后方可测样本 n（对数轴），y=方向技能（bp）。散布形状是
          样本量与噪声的对抗——左侧（n&lt;50）向两端飞的点多是噪声，不等于优势。
        </p>
      </FaceCard>
    </div>
  );
};
