/**
 * P 级证据徽章（全站一份实现，细案 v2 §4/§6）。
 *
 * 展示纪律（不要绕过本组件手写徽章）：
 * - `streak` 为 null 时**什么都不渲染**——不编造、不回落 0；
 * - `badge` 为 null 但 streak≥1 时只给灰色「连N」灰皿（延续状态存在但未过证据闸门，
 *   或证据产物未生成），hover 说明原因；
 * - badge 存在时给彩色徽章 + hover 证据卡（n / 全窗与日均超额 / hit 与基准 /
 *   分年微条 / 可执行口径对照列），常驻「历史超额≠未来收益」。
 * - 徽章只挂板块层；个股层实测否决（见后端 disclosure），此处不给个股用法。
 */

import React from 'react';
import { Popover } from 'antd';
import { Flame, ShieldQuestion } from 'lucide-react';
import type { FlowEvidenceBadge, FlowEvidenceStats } from '../services/moneyFlowService';

export interface EvidenceBadgeProps {
  /** 连续净流入天数（离线产物；null=无数据，不渲染）。 */
  streak: number | null | undefined;
  /** 证据徽章（pick_badge 输出；null=未过闸/未生成）。 */
  badge: FlowEvidenceBadge | null | undefined;
  size?: 'sm' | 'md';
}

function fmtBp(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}bp`;
}

function bpClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'text-slate-500';
  return v > 0 ? 'text-rose-600' : 'text-emerald-600';
}

/** 分年微条：按年 excess_bp 横向条（红正绿负），2024 负超额显式可见不藏。 */
const YearlyStrip: React.FC<{ yearly?: Record<string, { n: number; excess_bp: number | null }> }> = ({ yearly }) => {
  if (!yearly) return null;
  const years = Object.keys(yearly).sort();
  if (years.length === 0) return null;
  const maxAbs = Math.max(1e-9, ...years.map((y) => Math.abs(yearly[y]?.excess_bp ?? 0)));
  return (
    <div className="flex flex-col gap-1 mt-1.5">
      <span className="text-[10px] font-bold text-slate-400">分年超额（bp）</span>
      {years.map((y) => {
        const v = yearly[y]?.excess_bp ?? null;
        const w = v === null ? 0 : Math.max(3, (Math.abs(v) / maxAbs) * 100);
        return (
          <div key={y} className="flex items-center gap-2">
            <span className="w-9 text-[10px] font-mono text-slate-500">{y}</span>
            <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${(v ?? 0) >= 0 ? 'bg-rose-400' : 'bg-emerald-400'}`}
                style={{ width: `${w}%` }}
              />
            </div>
            <span className={`w-14 text-right text-[10px] font-mono font-bold ${bpClass(v)}`}>{fmtBp(v)}</span>
            <span className="w-14 text-right text-[10px] font-mono text-slate-400">n={yearly[y]?.n ?? '—'}</span>
          </div>
        );
      })}
    </div>
  );
};

function fmtHitPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

const StatRow: React.FC<{ label: string; stats: FlowEvidenceStats | null; note?: string }> = ({ label, stats, note }) => (
  <div className="flex flex-col gap-0.5">
    <div className="flex items-center justify-between gap-3">
      <span className="text-[11px] font-bold text-slate-600">{label}</span>
      <span className={`text-[11px] font-mono font-extrabold ${bpClass(stats?.excess_bp)}`}>
        {fmtBp(stats?.excess_bp)}
        {stats?.excess_daily_bp !== null && stats?.excess_daily_bp !== undefined && (
          <span className="text-slate-400 font-bold">（日均 {fmtBp(stats.excess_daily_bp)}）</span>
        )}
      </span>
    </div>
    <div className="flex items-center justify-between gap-3 text-[10px] font-mono text-slate-400">
      <span>
        n={stats?.n ?? '—'} · 命中 {fmtHitPct(stats?.hit)}（基准 {fmtHitPct(stats?.base_hit)}）
      </span>
      {note && <span>{note}</span>}
    </div>
  </div>
);

const EvidenceCard: React.FC<{ badge: FlowEvidenceBadge }> = ({ badge }) => (
  <div className="w-72 flex flex-col gap-2">
    <div className="flex items-center gap-1.5">
      <Flame className="w-3.5 h-3.5 text-amber-500" />
      <span className="text-xs font-extrabold text-slate-800">{badge.label}</span>
    </div>
    <StatRow label={`探针口径 t→t+${badge.horizon}`} stats={badge.fwd} note="对照列" />
    {badge.exec && <StatRow label={`可执行口径 T+1 收盘建仓`} stats={badge.exec} note="主口径" />}
    <YearlyStrip yearly={badge.fwd?.yearly} />
    <div className="pt-1.5 border-t border-slate-100 flex flex-col gap-0.5">
      <span className="text-[10px] font-mono text-slate-400">
        样本窗 {badge.window?.[0] ?? '—'} ~ {badge.window?.[1] ?? '—'} · 门槛 n≥{badge.min_n}
      </span>
      <span className="text-[10px] text-slate-400 leading-snug">
        口径：板块按当前成分回溯聚合（存在幸存者偏差）；qfq；bp 级强度。历史超额 ≠ 未来收益。
      </span>
    </div>
  </div>
);

export const EvidenceBadge: React.FC<EvidenceBadgeProps> = ({ streak, badge, size = 'sm' }) => {
  if (streak === null || streak === undefined) return null;
  const pad = size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-[11px]';

  if (!badge) {
    return (
      <Popover
        content={
          <div className="w-64 text-[11px] text-slate-500 leading-snug">
            该板块当前连续净流入 {streak} 日；此状态未过证据闸门（样本不足或未通过全窗/分年检验），
            或证据产物尚未生成。不展示条件超额。
          </div>
        }
        trigger="hover"
      >
        <span
          className={`inline-flex items-center gap-0.5 rounded-full bg-slate-100 text-slate-500 font-bold font-mono border border-slate-200 cursor-help ${pad}`}
        >
          <ShieldQuestion className="w-3 h-3" />
          连{streak}
        </span>
      </Popover>
    );
  }

  return (
    <Popover content={<EvidenceCard badge={badge} />} trigger="hover" placement="top">
      <span
        className={`inline-flex items-center gap-0.5 rounded-full bg-amber-50 text-amber-700 font-extrabold font-mono border border-amber-200/80 cursor-help ${pad}`}
      >
        <Flame className="w-3 h-3 text-amber-500" />
        连{streak}
        <span className="text-amber-500/90">{fmtBp(badge.fwd?.excess_bp)}/{badge.horizon}日</span>
      </span>
    </Popover>
  );
};
