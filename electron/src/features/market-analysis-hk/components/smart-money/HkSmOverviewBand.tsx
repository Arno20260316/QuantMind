/** 港股聪明钱 · 类型净流概览带 v2（细案 §4.1：常显不随 tab，6 类迷你卡 + 状态徽标组）。
 *
 * 每卡：色点 + 类型名 + 入柱徽标 → 60 日净流 Spark（trends）→ 持仓亿 / 5日净额 /
 * 方向技能·大钱技能（v2）/ 全史事件数。缺失一律「—」绝不显示 0；净额为事实语义
 * （净流入/净流出），技能为统计量（红绿仅用于数值正负）。
 */

import React from 'react';
import { Radar } from 'lucide-react';
import type { HkSmCategoryRow, HkSmOverviewResponse } from '../../services/api';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  ADMIT_NOTE,
  MISSING,
  SKILL_NOTE,
  VW_NOTE,
  categoryLabel,
  directionWord,
  flowWord,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtSigned,
  signCls,
} from '../../hkSmartMoneyVocabulary';

interface HkSmOverviewBandProps {
  data: HkSmOverviewResponse | null;
  loading: boolean;
}

/** 无轴 mini 走势（SVG 折线；缺失断线；不足 2 点不画） */
const Spark: React.FC<{ values: Array<number | null>; color: string }> = ({
  values,
  color,
}) => {
  const pts = values.filter((v): v is number => v !== null && !Number.isNaN(v));
  if (pts.length < 2) return null;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const W = 100;
  const H = 22;
  const step = W / (values.length - 1);
  const segs: string[] = [];
  let run: string[] = [];
  values.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) {
      if (run.length > 1) segs.push(run.join(' '));
      run = [];
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (H - ((v - min) / span) * (H - 4) - 2).toFixed(1);
    run.push(`${x},${y}`);
  });
  if (run.length > 1) segs.push(run.join(' '));
  if (!segs.length) return null;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full h-[22px]"
      aria-hidden
    >
      {segs.map((s, i) => (
        <polyline
          key={i}
          points={s}
          fill="none"
          stroke={color}
          strokeWidth="1.2"
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity="0.75"
        />
      ))}
    </svg>
  );
};

/** 「5日净流入 +98.6亿」的标签段；数值缺失时退回中性「净额」 */
function netLabel(days: number, v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return `${days}日净额`;
  return `${days}日${flowWord(v)}`;
}

function CategoryMiniCard({
  row,
  trend,
}: {
  row: HkSmCategoryRow;
  trend: Array<{ date: string; net_yi: number | null }> | undefined;
}) {
  const color = INST_CATEGORY_COLORS[row.category] || '#94a3b8';
  const label = categoryLabel(row.category, row.category_label ?? row.label);
  const net5 = row.net5_yi;
  const net20 = row.net20_yi;
  return (
    <div className="rounded-xl border border-slate-100 bg-white px-2.5 py-2 flex flex-col gap-1 min-w-0">
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: color }} />
        <span className="text-[10px] font-extrabold text-slate-700 truncate">{label}</span>
        {row.admitted ? (
          <span
            className="ml-auto px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-600 text-white flex-shrink-0"
            title={`该类型有信号通过 h10 双过闸：${row.best?.variant_label ?? row.best?.variant ?? MISSING}${
              row.best?.direction != null ? `（方向 ${directionWord(row.best.direction)}）` : ''
            }`}
          >
            入柱
          </span>
        ) : null}
      </div>
      {trend && trend.length > 1 && (
        <Spark values={trend.map((r) => r.net_yi)} color={color} />
      )}
      <div className="flex items-baseline justify-between gap-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">
          持仓 {row.n_symbols != null ? `${fmtInt(row.n_symbols)} 只` : MISSING}
        </span>
        <span className="text-[10px] font-mono font-bold text-slate-600 whitespace-nowrap">
          {fmtNum(row.holding_yi, 0)}亿
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">{netLabel(5, net5)}</span>
        <span className={`text-[11px] font-mono font-extrabold ${signCls(net5)}`}>
          {fmtSigned(net5, 1)}亿
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">{netLabel(20, net20)}</span>
        <span className={`text-[11px] font-mono font-bold ${signCls(net20)}`}>
          {fmtSigned(net20, 1)}亿
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-1 border-t border-slate-100 pt-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap" title={SKILL_NOTE}>
          技能 {fmtBp(row.skill_bp)}bp
        </span>
        <span className="text-[9px] font-mono text-slate-500 whitespace-nowrap" title={VW_NOTE}>
          大钱 {fmtBp(row.vw_skill_bp)}
        </span>
      </div>
      <div className="text-[8px] font-mono text-slate-400 truncate">
        全史事件 {fmtInt(row.n_lead_events)}
        {row.best
          ? ` · 最强 ${row.best.variant ?? MISSING} · IC ${fmtNum(row.best.h10_ic, 3)}`
          : ''}
      </div>
    </div>
  );
}

export const HkSmOverviewBand: React.FC<HkSmOverviewBandProps> = ({ data, loading }) => {
  if (!data) {
    return (
      <div className="rounded-2xl bg-slate-50/70 border border-slate-100 p-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <Radar className="w-3.5 h-3.5 text-purple-600" />
          <span className="text-[10px] font-extrabold text-slate-600">类型净流概览</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="rounded-xl border border-slate-100 bg-white min-h-[118px] grid place-items-center"
            >
              <span className="text-[10px] text-slate-300 font-bold">
                {loading ? '加载中…' : MISSING}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const fresh = data.freshness;
  const counts = data.counts_30d;
  const trendOf = (cat: string) => data.trends?.categories?.[cat];

  return (
    <div className="rounded-2xl bg-slate-50/70 border border-slate-100 p-2.5 flex flex-col gap-2">
      {/* 状态徽标组 */}
      <div className="flex items-center gap-2 flex-wrap px-1">
        <span className="flex items-center gap-1 text-[10px] font-extrabold text-slate-600">
          <Radar className="w-3.5 h-3.5 text-purple-600" />
          类型净流概览
        </span>
        {data.evidence_only ? (
          <span
            className="px-2 py-0.5 rounded-full text-[9px] font-extrabold bg-slate-100 text-slate-500 border border-slate-200"
            title={ADMIT_NOTE}
          >
            零入柱（evidence-only）
          </span>
        ) : (
          <span
            className="px-2 py-0.5 rounded-full text-[9px] font-extrabold bg-purple-50 text-purple-700 border border-purple-100"
            title={ADMIT_NOTE}
          >
            入柱 {fmtInt(data.admitted?.length ?? null)} 项
          </span>
        )}
        {data.partial && (
          <span className="px-2 py-0.5 rounded-full text-[9px] font-extrabold bg-amber-50 text-amber-700 border border-amber-200">
            部分数据
          </span>
        )}
        <span className="ml-auto text-[9px] font-mono text-slate-400 whitespace-nowrap">
          行情 {fresh?.kline_date ?? MISSING} · CCASS {fresh?.ccass_date ?? MISSING} · 南向{' '}
          {fresh?.south_date ?? MISSING}
          {fresh?.ccass_lag_days != null ? `（CCASS 滞后 ${fresh.ccass_lag_days} 交易日）` : ''}
        </span>
      </div>

      {/* 6 类迷你卡（含 60 日净流 Spark） */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
        {data.categories.map((c) => (
          <CategoryMiniCard key={c.category} row={c} trend={trendOf(c.category)} />
        ))}
      </div>

      {counts && (
        <div className="flex items-center gap-3 flex-wrap text-[9px] text-slate-400 border-t border-slate-200/60 pt-1.5 px-1">
          <span>
            近 {counts.days != null ? fmtInt(counts.days) : MISSING} 交易日事件{' '}
            <span className="font-mono font-bold text-slate-600">{fmtInt(counts.events)}</span>
          </span>
          <span title="过户嫌疑只标记、不计入统计">
            过户嫌疑{' '}
            <span className="font-mono font-bold text-slate-600">
              {fmtInt(counts.transfer_suspect)}
            </span>
          </span>
          <span>
            首现 <span className="font-mono font-bold text-slate-600">{fmtInt(counts.first_seen)}</span>
          </span>
          <span>
            退出边界{' '}
            <span className="font-mono font-bold text-slate-600">{fmtInt(counts.exit_boundary)}</span>
          </span>
          {data.summary && (
            <span>
              复合分快照 {data.summary.scores_date ?? '未发布'} · 池{' '}
              <span className="font-mono font-bold text-slate-600">{fmtInt(data.summary.pool_size)}</span> 只
            </span>
          )}
        </div>
      )}
    </div>
  );
};
