/**
 * 延续证据台（细案 v2 §6）：命中榜 → 信号档案（过闸条件全统计 + 动量分层 + 否决清单）
 * → 口径与披露。
 *
 * 展示纪律：
 * - 数字一律来自离线产物（由后端 /money-flow/evidence 统一出口），缺失显 —，绝不回落 0；
 * - 未过闸条件照常展示统计但明确标注「未过闸」；否决项常驻「已实测、不具预测力」；
 * - 命中榜陈旧（落后最新行情）时清榜并显式披露，陈旧 streak 比没有更坏；
 * - 点击命中榜行联动上方桑基与工作台（证据只在行业二级测量，回调由父级切体系）。
 */

import React, { useEffect, useRef, useState } from 'react';
import { Spin } from 'antd';
import { AlertCircle, Flame, History, Info, ShieldAlert, ShieldCheck } from 'lucide-react';
import { EvidenceBadge, bpClass, fmtBp } from './EvidenceBadge';
import {
  MoneyFlowError,
  flowTextClass,
  fmtRatio,
  fmtYi,
  getFlowEvidence,
  type FlowEvidenceCondition,
  type FlowEvidenceEnvSplit,
  type FlowEvidenceHit,
  type FlowEvidenceRejected,
  type FlowEvidenceResponse,
  type FlowEvidenceStats,
} from '../services/moneyFlowService';

/** 口径键 → 中文标签（未登记键回落原键，产物新增字段不丢）。 */
const CONVENTION_LABELS: Record<string, string> = {
  sector_set: '测量体系',
  sector_set_label: '体系名称',
  price_source: '价格口径',
  fwd_definition: '对照口径',
  exec_definition: '主口径（可执行）',
  amount_unit: '金额单位',
  signal: '信号定义',
  sector_aggregation: '板块聚合',
  strength_note: '强度说明',
  stock_layer_note: '个股层结论',
};

const VERDICT_META: Record<string, { label: string; cls: string }> = {
  reject: { label: '否决', cls: 'bg-rose-50 text-rose-600 border-rose-100' },
  background: { label: '背景', cls: 'bg-slate-50 text-slate-500 border-slate-200' },
  control: { label: '对照', cls: 'bg-amber-50 text-amber-700 border-amber-200/70' },
};

function fmtHitPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US');
}

function fmtRawDate(s: string | null | undefined): string {
  if (!s) return '—';
  return s.length === 8 && /^\d{8}$/.test(s)
    ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`
    : s;
}

/** 统计格：超额 bp + n（可执行主口径附日均）。 */
const StatCell: React.FC<{ stats?: FlowEvidenceStats | null; showDaily?: boolean }> = ({
  stats,
  showDaily,
}) => (
  <div className="flex flex-col items-end gap-0.5">
    <span className={`text-xs font-mono font-extrabold ${bpClass(stats?.excess_bp)}`}>
      {fmtBp(stats?.excess_bp)}
    </span>
    <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">
      n={fmtCount(stats?.n)}
      {showDaily && stats?.excess_daily_bp !== null && stats?.excess_daily_bp !== undefined && (
        <> · 日均 {fmtBp(stats.excess_daily_bp)}</>
      )}
    </span>
  </div>
);

/** 分年超额微条（坏年显式可见，不藏）。 */
const YearChips: React.FC<{ yearly?: FlowEvidenceStats['yearly'] }> = ({ yearly }) => {
  const years = yearly ? Object.keys(yearly).sort() : [];
  if (years.length === 0) return <span className="text-[10px] text-slate-300">—</span>;
  return (
    <div className="flex flex-wrap gap-1 justify-end">
      {years.map((y) => {
        const row = yearly?.[y];
        const v = row?.excess_bp ?? null;
        const cls =
          v === null
            ? 'bg-slate-50 text-slate-400 border-slate-200'
            : v >= 0
              ? 'bg-rose-50 text-rose-600 border-rose-100'
              : 'bg-emerald-50 text-emerald-600 border-emerald-100';
        return (
          <span
            key={y}
            title={`${y}：n=${fmtCount(row?.n)} · 超额 ${fmtBp(v)} · 命中 ${fmtHitPct(row?.hit)}（基准 ${fmtHitPct(row?.base_hit)}）`}
            className={`px-1 py-px rounded border font-mono text-[10px] font-bold whitespace-nowrap ${cls}`}
          >
            {y.slice(2)} {v === null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(1)}`}
          </span>
        );
      })}
    </div>
  );
};

/** 环境分层格（市场 20 日环境上行/下行两档）。 */
const EnvCell: React.FC<{ env?: Record<string, FlowEvidenceEnvSplit> }> = ({ env }) => {
  const rows: Array<{ key: string; label: string }> = [
    { key: 'up', label: '上行' },
    { key: 'down', label: '下行' },
  ];
  if (!env) return <span className="text-[10px] text-slate-300">—</span>;
  return (
    <div className="flex flex-col items-end gap-0.5">
      {rows.map(({ key, label }) => {
        const seg = env[key];
        return (
          <span
            key={key}
            title={seg ? `n=${fmtCount(seg.n)} · 命中 ${fmtHitPct(seg.hit)}` : '无样本'}
            className="text-[10px] font-mono whitespace-nowrap text-slate-400"
          >
            {label} <span className={`font-extrabold ${bpClass(seg?.excess_bp)}`}>{fmtBp(seg?.excess_bp)}</span>
          </span>
        );
      })}
    </div>
  );
};

/** 闸门格：过闸/未过闸 + 年份数 + 判据与逐项结果（tooltip）。 */
const GateCell: React.FC<{ cond: FlowEvidenceCondition }> = ({ cond }) => {
  const pass = cond.gate?.pass === true;
  const failed = Object.entries(cond.gate?.checks ?? {})
    .filter(([, ok]) => !ok)
    .map(([k]) => k);
  const title = [cond.gate?.criteria, failed.length ? `未过项：${failed.join(' / ')}` : '']
    .filter(Boolean)
    .join('\n');
  return (
    <div className="flex flex-col items-center gap-0.5" title={title}>
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border font-bold text-[10px] whitespace-nowrap ${
          pass
            ? 'bg-emerald-50 text-emerald-700 border-emerald-200/80'
            : 'bg-slate-50 text-slate-500 border-slate-200'
        }`}
      >
        {pass ? <ShieldCheck className="w-3 h-3" /> : <ShieldAlert className="w-3 h-3" />}
        {pass ? '过闸' : '未过闸'}
      </span>
      <span className="text-[10px] font-mono text-slate-400">{cond.years} 个年份</span>
    </div>
  );
};

/** 信号档案主行 +（streak_3）动量分层子行。 */
const ConditionRows: React.FC<{ cond: FlowEvidenceCondition }> = ({ cond }) => {
  const h = (map: Record<string, FlowEvidenceStats> | undefined, n: number) => map?.[String(n)] ?? null;
  const h3 = h(cond.fwd, 3);
  const sub = cond.sub_splits;
  return (
    <>
      <tr className="border-b border-slate-100 align-top hover:bg-purple-50/30 transition-colors">
        <td className="py-2 pr-3">
          <div className="text-xs font-extrabold text-slate-800 whitespace-nowrap">{cond.label}</div>
          <div className="text-[10px] text-slate-400">板块主力净额连续为正</div>
        </td>
        <td className="py-2 px-2">
          <GateCell cond={cond} />
        </td>
        <td className="py-2 px-2">
          <StatCell stats={h(cond.fwd, 1)} />
        </td>
        <td className="py-2 px-2 bg-purple-50/20">
          <StatCell stats={h(cond.fwd, 3)} />
        </td>
        <td className="py-2 px-2">
          <StatCell stats={h(cond.fwd, 5)} />
        </td>
        <td className="py-2 px-2 bg-purple-50/20">
          <StatCell stats={h(cond.exec, 3)} showDaily />
        </td>
        <td className="py-2 px-2 text-right whitespace-nowrap">
          <div className="text-xs font-mono font-extrabold text-slate-700">{fmtHitPct(h3?.hit)}</div>
          <div className="text-[10px] font-mono text-slate-400">基准 {fmtHitPct(h3?.base_hit)}</div>
        </td>
        <td className="py-2 px-2">
          <YearChips yearly={h3?.yearly} />
        </td>
        <td className="py-2 pl-2">
          <EnvCell env={cond.env} />
        </td>
      </tr>
      {sub && (
        <tr className="border-b border-slate-100 bg-slate-50/60">
          <td className="py-1.5 pr-3" />
          <td colSpan={8} className="py-1.5 px-2">
            <span className="text-[10px] font-bold text-slate-500" title={sub.note}>
              动量中位分层（3 日对照口径）：
            </span>
            <span className="ml-2 text-[10px] font-mono text-slate-500">
              低动量 <span className={`font-extrabold ${bpClass(sub.low_mom?.excess_bp)}`}>{fmtBp(sub.low_mom?.excess_bp)}</span>
              （n={fmtCount(sub.low_mom?.n)}）
            </span>
            <span className="ml-3 text-[10px] font-mono text-slate-500">
              高动量 <span className={`font-extrabold ${bpClass(sub.high_mom?.excess_bp)}`}>{fmtBp(sub.high_mom?.excess_bp)}</span>
              （n={fmtCount(sub.high_mom?.n)}）
            </span>
          </td>
        </tr>
      )}
    </>
  );
};

/** 否决/背景/对照行。 */
const RejectedRow: React.FC<{ row: FlowEvidenceRejected }> = ({ row }) => {
  const meta = VERDICT_META[row.verdict] ?? VERDICT_META.background;
  const m = row.metrics ?? {};
  const metric =
    m.ic_mean !== undefined
      ? `IC 均值 ${m.ic_mean?.toFixed(4)}${m.icir !== null && m.icir !== undefined ? ` · ICIR ${m.icir.toFixed(2)}` : ''}${m.n_days ? ` · ${fmtCount(m.n_days)} 天` : ''}`
      : m.n !== undefined
        ? `n=${fmtCount(m.n)} · 全窗超额 ${fmtBp(m.excess_bp)}${m.excess_daily_bp !== null && m.excess_daily_bp !== undefined ? ` · 日均 ${fmtBp(m.excess_daily_bp)}` : ''}`
        : '';
  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className={`mt-px shrink-0 px-1.5 py-0.5 rounded-full border text-[10px] font-bold ${meta.cls}`}>
        {meta.label}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <span className="text-[11px] font-bold text-slate-600">{row.label}</span>
          {metric && <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">{metric}</span>}
        </div>
        {row.note && <div className="text-[10px] text-slate-400 leading-snug mt-0.5">{row.note}</div>}
      </div>
    </div>
  );
};

const HitCard: React.FC<{ hit: FlowEvidenceHit; onClick: () => void }> = ({ hit, onClick }) => (
  <button
    onClick={onClick}
    className="group text-left rounded-xl border border-slate-200/80 bg-white px-3 py-2 flex flex-col gap-1 hover:border-purple-300 hover:shadow-sm hover:shadow-purple-500/10 transition-all cursor-pointer min-w-0"
    title={`${hit.name} · 连续净流入 ${hit.streak} 日 · 点击联动桑基与工作台`}
  >
    <div className="flex items-center justify-between gap-2 min-w-0">
      <span className="text-xs font-extrabold text-slate-800 group-hover:text-purple-700 truncate">
        {hit.name}
      </span>
      <EvidenceBadge streak={hit.streak} badge={hit.evidence} size="sm" />
    </div>
    <div className="flex items-center gap-2.5 text-[10px] font-mono whitespace-nowrap">
      <span className={`font-extrabold ${flowTextClass(hit.net_main)}`}>{fmtYi(hit.net_main)}</span>
      <span className="text-slate-400">净额比 {fmtRatio(hit.net_ratio)}</span>
      <span className="text-slate-400">广度 {fmtRatio(hit.breadth, 0)}</span>
      <span className="text-slate-400">{fmtCount(hit.members)} 只</span>
    </div>
  </button>
);

export interface FlowEvidencePanelProps {
  /** 页面快照日期（ISO yyyy-mm-dd）；undefined=最新。命中榜随快照日回溯。 */
  date?: string;
  /** 命中榜/信号行点击 → 联动桑基与工作台（父级负责切到行业二级体系）。 */
  onSelectSector: (name: string) => void;
}

export const FlowEvidencePanel: React.FC<FlowEvidencePanelProps> = ({ date, onSelectSector }) => {
  const [doc, setDoc] = useState<FlowEvidenceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const s = ++seq.current;
    setLoading(true);
    setError(null);
    getFlowEvidence({ date })
      .then((d) => {
        if (s === seq.current) setDoc(d);
      })
      .catch((e: unknown) => {
        if (s !== seq.current) return;
        setDoc(null);
        setError(e instanceof MoneyFlowError ? `证据台取数失败：${e.message}` : '证据台取数失败，请稍后重试');
      })
      .finally(() => {
        if (s === seq.current) setLoading(false);
      });
  }, [date]);

  const conditions = doc?.conditions ?? {};
  const condOrder = ['streak_5', 'streak_3', 'streak_2'].filter((k) => conditions[k]);
  const rejected = doc?.rejected ?? [];
  const hits = doc?.hits ?? [];
  const hitsRetro = !!doc?.requested_date && doc.requested_date !== doc.hits_trade_date;

  return (
    <div className="bg-white/95 backdrop-blur-md rounded-2xl p-3.5 border border-purple-100/80 shadow-xs flex flex-col gap-3 min-w-0">
      {/* ── 头部：标题 + 产物元信息 ── */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
          <Flame className="w-3.5 h-3.5 text-amber-500" />
          <span>延续证据台 · 命中榜与信号档案</span>
          <span className="font-normal text-slate-400">（哪些「连N」状态值得跟 — 全史条件统计，非预测）</span>
        </h3>
        {doc?.available && (
          <div className="flex items-center gap-1.5 flex-wrap text-[10px] font-mono">
            <span className="px-2 py-0.5 rounded-full bg-slate-50 text-slate-600 border border-slate-200/70">
              产物交易日 {fmtRawDate(doc.trade_date_raw ?? doc.trade_date)}
            </span>
            {doc.window && (
              <span className="px-2 py-0.5 rounded-full bg-slate-50 text-slate-600 border border-slate-200/70">
                样本窗 {fmtRawDate(doc.window[0])} ~ {fmtRawDate(doc.window[1])}
              </span>
            )}
            {doc.coverage && (
              <span className="px-2 py-0.5 rounded-full bg-purple-50 text-purple-700 border border-purple-200/70">
                {doc.coverage.days} 日 × {doc.coverage.sectors} 板块（{fmtCount(doc.coverage.panel_rows)} 行）
              </span>
            )}
          </div>
        )}
      </div>

      {loading && !doc && (
        <div className="flex justify-center py-8">
          <Spin size="small" />
        </div>
      )}

      {!loading && error && (
        <div className="flex items-center gap-2 text-xs text-rose-500 font-bold py-2">
          <AlertCircle className="w-4 h-4" />
          {error}
        </div>
      )}

      {!error && doc && !doc.available && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500 leading-snug">
          <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            延续证据产物尚未生成（离线构建任务未运行）——命中榜与信号档案不可用；
            上方桑基/工作台不受影响，仅不展示「连N」条件超额。
          </span>
        </div>
      )}

      {!error && doc?.available && (
        <>
          {/* ── 一、命中榜 ── */}
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-[11px] font-extrabold text-slate-700 flex items-center gap-1.5">
                <History className="w-3.5 h-3.5 text-purple-500" />
                延续命中榜（连续净流入 ≥ {doc.badge_levels?.[doc.badge_levels.length - 1] ?? 2} 日）
              </h4>
              <span className="text-[10px] font-mono text-slate-400">
                榜单日 {fmtRawDate(doc.hits_trade_date)}{hitsRetro ? '（快照日回溯）' : ''} · 点击联动桑基与工作台
              </span>
            </div>

            {doc.hits_state === 'stale' && (
              <div className="flex items-start gap-1.5 px-3 py-1.5 rounded-xl border bg-amber-50/90 text-amber-700 border-amber-200/70 text-[11px] font-bold leading-snug">
                <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
                <span>命中榜产物停留在 {fmtRawDate(doc.trade_date_raw ?? doc.trade_date)}，落后于最新行情——本次不展示命中榜（陈旧 streak 不参与展示）</span>
              </div>
            )}
            {doc.hits_state === 'no_rows' && (
              <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
                所选日期没有日面板数据（非交易日或超出样本窗），命中榜不可用。
              </div>
            )}
            {doc.hits_state === 'ok' && hits.length === 0 && (
              <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
                榜单日无连续净流入达标的板块。
              </div>
            )}
            {doc.hits_state === 'ok' && hits.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
                {hits.map((h) => (
                  <HitCard key={h.name} hit={h} onClick={() => onSelectSector(h.name)} />
                ))}
              </div>
            )}
          </div>

          {/* ── 二、信号档案 ── */}
          <div className="flex flex-col gap-2">
            <h4 className="text-[11px] font-extrabold text-slate-700">
              信号档案 · 板块「连N」历史条件统计（{doc.conditions && condOrder.length} 档；闸门 = 全窗/分年/环境多重检验）
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-[11px] border-collapse">
                <thead>
                  <tr className="text-slate-500 border-b border-slate-200 text-[10px]">
                    <th className="text-left font-bold py-1.5 pr-3 whitespace-nowrap">延续状态</th>
                    <th className="font-bold py-1.5 px-2 whitespace-nowrap">闸门</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap">探针 1 日</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap bg-purple-50/40 rounded-t">探针 3 日</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap">探针 5 日</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap bg-purple-50/40 rounded-t">可执行 3 日（主口径）</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap">命中率 3 日</th>
                    <th className="font-bold py-1.5 px-2 text-right whitespace-nowrap">分年超额（3 日，bp）</th>
                    <th className="font-bold py-1.5 pl-2 text-right whitespace-nowrap">环境分层（3 日）</th>
                  </tr>
                </thead>
                <tbody>
                  {condOrder.map((k) => (
                    <ConditionRows key={k} cond={conditions[k]} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── 三、否决清单 ── */}
          {rejected.length > 0 && (
            <div className="flex flex-col gap-1">
              <h4 className="text-[11px] font-extrabold text-slate-700 flex items-center gap-1.5">
                <ShieldAlert className="w-3.5 h-3.5 text-slate-400" />
                否决清单 · 已实测、不具预测力、不参与展示（{rejected.length} 项，保留统计供审计）
              </h4>
              <div className="flex flex-col divide-y divide-slate-100">
                {rejected.map((r) => (
                  <RejectedRow key={r.key} row={r} />
                ))}
              </div>
            </div>
          )}

          {/* ── 四、口径与披露 ── */}
          <div className="pt-2 border-t border-slate-100 flex flex-col gap-1">
            {Object.entries(doc.conventions ?? {}).map(([k, v]) => (
              <div key={k} className="flex gap-2 text-[10px] leading-snug">
                <span className="shrink-0 font-bold text-slate-500 whitespace-nowrap">
                  {CONVENTION_LABELS[k] ?? k}
                </span>
                <span className="text-slate-400">{v}</span>
              </div>
            ))}
            {(doc.meta?.disclosure ?? []).map((line, i) => (
              <p key={i} className="text-[10px] text-slate-400 leading-snug">
                {line}
              </p>
            ))}
          </div>
        </>
      )}
    </div>
  );
};
