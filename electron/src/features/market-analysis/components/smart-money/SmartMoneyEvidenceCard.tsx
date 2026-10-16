/** 聪明钱 · 验证证据台 v2（细案 §7：B5 h5/h10 视野切换 / B10 空态去脚本名 /
 * 新：IC 衰减图（h=2..20，含复合分）+ 复合分分年表 + 相关性塌缩审计）。
 *
 * 判据：sign(IC)=sign(q5−q1) ∧ |IC|≥0.01 ∧ |q5−q1|>1bp ∧ n≥60（h=10 终审）；
 * 方向列 = 入柱方向（h10 实测 IC 符号），负向柱分位在评分链中已取反。
 * 方向 chip 用中性紫/蓝（方向=身份不是涨跌）；IC 数值正负才用红绿。
 */

import React, { useMemo, useState } from 'react';
import { FlaskConical, GitMerge, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Tooltip } from 'antd';
import { EmptyHint, FaceCard, PeriodChips } from '../../../market-analysis-shared/ui';
import type {
  SmartEvidenceResponse,
  SmartEvidenceSeg,
  SmartEvidenceSignal,
} from '../../services/smartMoneyService';
import {
  MISSING,
  directionHint,
  directionLabel,
  fmtNum,
  pillarLabel,
} from '../../smartMoneyVocabulary';
import { AXIS_LABEL, BASE_GRID, CHART_COLORS, SmartChart, tipNum } from './charts';

const DECAY_H_ORDER = ['2', '3', '5', '10', '20'];
const HORIZON_OPTIONS = [
  { id: '5', label: 'h5' },
  { id: '10', label: 'h10' },
];
/** 衰减图各入柱线色（紫系深浅；复合分最粗最紫） */
const SIGNAL_LINE_COLORS = ['#a78bfa', '#8b5cf6', '#c4b5fd', '#7c3aed', '#6d28d9', '#ddd6fe'];

function fmtBp(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${Number(v) > 0 ? '+' : ''}${fmtNum(v, 1)}`;
}

function fmtIc(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${Number(v) > 0 ? '+' : ''}${fmtNum(v, 4)}`;
}

const DirectionChip: React.FC<{ direction: number | null }> = ({ direction }) => {
  const tone =
    direction === 1
      ? 'bg-purple-50 text-purple-700 border-purple-100'
      : direction === -1
        ? 'bg-blue-50 text-blue-700 border-blue-100'
        : 'bg-slate-50 text-slate-400 border-slate-100';
  return (
    <Tooltip title={directionHint(direction)}>
      <span className={`px-1.5 py-0.5 rounded-full text-[9px] font-bold border ${tone} cursor-help`}>
        {directionLabel(direction)}
      </span>
    </Tooltip>
  );
};

const SegCells: React.FC<{ seg: SmartEvidenceSeg | null | undefined }> = ({ seg }) => (
  <>
    <span className={`text-right text-[10px] font-mono ${seg?.rank_ic != null && seg.rank_ic < 0 ? 'text-green-600' : 'text-red-600'}`}>
      {fmtIc(seg?.rank_ic)}
    </span>
    <span className={`text-right text-[10px] font-mono ${seg?.q5_minus_q1_bp != null && seg.q5_minus_q1_bp < 0 ? 'text-green-600' : 'text-red-600'}`}>
      {fmtBp(seg?.q5_minus_q1_bp)}
    </span>
    <span className="text-right text-[10px] font-mono text-slate-400">{seg?.n_days ?? MISSING}</span>
  </>
);

interface SmartMoneyEvidenceCardProps {
  evidence: SmartEvidenceResponse | null;
  loading?: boolean;
  /** true = 无卡壳（统一大框内的标签面） */
  bare?: boolean;
}

export const SmartMoneyEvidenceCard: React.FC<SmartMoneyEvidenceCardProps> = ({
  evidence,
  loading,
  bare = false,
}) => {
  const [h, setH] = useState('10');

  const decayOption = useMemo(() => {
    const decay = evidence?.decay;
    if (!decay) return null;
    const names = ['composite', ...(evidence?.signals ?? []).filter((s) => s.admitted).map((s) => s.name)];
    const usable = names.filter((n) => decay[n] && DECAY_H_ORDER.some((k) => decay[n]?.[k]));
    if (!usable.length) return null;
    const series = usable.map((n, i) => {
      const isComp = n === 'composite';
      const rows = DECAY_H_ORDER.map((k) => {
        const pt = decay[n]?.[k];
        return pt && pt.ic !== null ? Number(pt.ic) : null;
      });
      return {
        name: isComp ? '复合分' : pillarLabel(n),
        type: 'line' as const,
        data: rows,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        connectNulls: false,
        lineStyle: isComp
          ? { width: 2.4, color: '#7c3aed' }
          : { width: 1.2, color: SIGNAL_LINE_COLORS[(i - 1) % SIGNAL_LINE_COLORS.length] },
        itemStyle: { color: isComp ? '#7c3aed' : SIGNAL_LINE_COLORS[(i - 1) % SIGNAL_LINE_COLORS.length] },
        z: isComp ? 3 : 1,
      };
    });
    return {
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (ps: Array<{ seriesName: string; dataIndex: number }>) => {
          const k = DECAY_H_ORDER[ps[0]?.dataIndex ?? 0];
          const lines = [`h=${k}（T+${k} 收盘出场）`];
          ps.forEach((p) => {
            const n = usable.find((x) => (x === 'composite' ? '复合分' : pillarLabel(x)) === p.seriesName);
            const pt = n ? decay[n]?.[k] : null;
            lines.push(
              `${p.seriesName} IC ${tipNum(pt?.ic ?? null, (v) => fmtNum(v, 4))}${pt?.n_days ? `（n=${pt.n_days}）` : ''}`,
            );
          });
          return lines.join('<br/>');
        },
      },
      legend: {
        show: true,
        type: 'scroll',
        top: 0,
        right: 0,
        itemWidth: 10,
        itemHeight: 6,
        textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
      },
      grid: { ...BASE_GRID, top: 24 },
      xAxis: {
        type: 'category',
        data: DECAY_H_ORDER.map((k) => `h${k}`),
        boundaryGap: false,
        axisLabel: AXIS_LABEL,
      },
      yAxis: {
        type: 'value',
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => v.toFixed(2) },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } },
      },
      series,
    };
  }, [evidence]);

  if (!evidence) {
    return (
      <FaceCard
        bare={bare}
        icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />}
        title="验证证据台（双过闸审计）"
      >
        <EmptyHint
          text={loading ? '加载中…' : '验证数据尚未生成（随每日构建产出）'}
          loading={loading}
        />
      </FaceCard>
    );
  }

  const seg = (s: SmartEvidenceSignal): SmartEvidenceSeg | null | undefined => s.results?.[h];
  const admitted = evidence.signals.filter((s) => s.admitted);
  const rejected = evidence.rejected ?? [];
  const comp = evidence.composite?.results?.[h];
  const compYears = evidence.composite?.by_year?.[h] ?? {};
  const collapsed = evidence.correlation?.collapsed ?? [];
  const win = evidence.window;

  return (
    <FaceCard
      bare={bare}
      icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          验证证据台（双过闸审计）
          <span className="text-[10px] font-normal text-slate-400">
            判据：sign(IC)=sign(q5−q1) ∧ |IC|≥0.01 ∧ |q5−q1|&gt;1bp ∧ n≥60（h=10 终审）
          </span>
        </span>
      }
      extra={
        <span className="flex items-center gap-2">
          <PeriodChips options={HORIZON_OPTIONS} value={h} onChange={setH} />
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            {win ? `${win.eval_start} → ${win.eval_end}` : MISSING}
            {evidence.validated_at ? ` · 验证于 ${evidence.validated_at}` : ''}
          </span>
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        {/* 复合分验证条（随 h 切换） */}
        <div className="rounded-xl bg-purple-50/60 border border-purple-100 px-3 py-2 flex items-center gap-x-5 gap-y-1 flex-wrap">
          <span className="text-[10px] font-extrabold text-purple-800 flex items-center gap-1">
            <ShieldCheck className="w-3.5 h-3.5" />
            复合分（入柱等权·方向对齐后）h{h} 验证
          </span>
          <span className="text-[10px] text-slate-500">
            IC <span className="font-mono font-bold text-slate-800">{fmtIc(comp?.rank_ic)}</span>
          </span>
          <span className="text-[10px] text-slate-500">
            ICIR <span className="font-mono font-bold text-slate-800">{fmtNum(comp?.icir, 2)}</span>
          </span>
          <span className="text-[10px] text-slate-500">
            q5−q1 <span className="font-mono font-bold text-slate-800">{fmtBp(comp?.q5_minus_q1_bp)}bp</span>
          </span>
          <span className="text-[10px] text-slate-500">
            IC&gt;0 占比 <span className="font-mono font-bold text-slate-800">{fmtNum(comp?.ic_pos_rate, 2)}</span>
          </span>
          <span className="text-[10px] text-slate-500">
            评估日数 <span className="font-mono font-bold text-slate-800">{comp?.n_days ?? MISSING}</span>
          </span>
          <span className="text-[10px] text-slate-500">
            平均池 <span className="font-mono font-bold text-slate-800">
              {evidence.composite?.avg_pool ?? MISSING}
            </span>
          </span>
          <span className="text-[10px] text-slate-500">
            平均覆盖 <span className="font-mono font-bold text-slate-800">
              {evidence.composite?.avg_covered == null
                ? MISSING
                : fmtNum(evidence.composite.avg_covered, 1)}
            </span>
          </span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 min-w-0">
          {/* 入柱表（h 切换） */}
          <div className="min-w-0">
            <div className="grid grid-cols-[minmax(120px,1.4fr)_48px_72px_80px_40px] gap-2 px-1 pb-1.5 text-[9px] font-extrabold text-slate-400 border-b border-slate-100">
              <span>入柱（{admitted.length}）</span>
              <span>方向</span>
              <span className="text-right">IC·h{h}</span>
              <span className="text-right">q5−q1·bp</span>
              <span className="text-right">n</span>
            </div>
            {admitted.map((s) => (
              <div
                key={s.name}
                className="grid grid-cols-[minmax(120px,1.4fr)_48px_72px_80px_40px] gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 items-center"
              >
                <Tooltip title={s.name}>
                  <span className="text-[10px] font-bold text-slate-700 truncate">
                    {pillarLabel(s.name)}
                  </span>
                </Tooltip>
                <DirectionChip direction={s.direction} />
                <SegCells seg={seg(s)} />
              </div>
            ))}
            {admitted.length === 0 && <EmptyHint text="当前无入柱（全部被否决）" />}
          </div>

          {/* 否决清单 */}
          <div className="min-w-0">
            <div className="grid grid-cols-[minmax(110px,1.2fr)_minmax(90px,1fr)_72px_72px] gap-2 px-1 pb-1.5 text-[9px] font-extrabold text-slate-400 border-b border-slate-100">
              <span className="flex items-center gap-1">
                <ShieldAlert className="w-3 h-3" />
                否决（{rejected.length}）
              </span>
              <span>原因</span>
              <span className="text-right">IC·h10</span>
              <span className="text-right">q5−q1·bp</span>
            </div>
            {rejected.map((r) => (
              <div
                key={r.name}
                className="grid grid-cols-[minmax(110px,1.2fr)_minmax(90px,1fr)_72px_72px] gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 items-center"
              >
                <Tooltip title={r.name}>
                  <span className="text-[10px] text-slate-500 truncate">{pillarLabel(r.name)}</span>
                </Tooltip>
                <span className="text-[9px] text-slate-400 truncate">{r.reason || MISSING}</span>
                <span className="text-right text-[10px] font-mono text-slate-400">{fmtIc(r.rank_ic_10)}</span>
                <span className="text-right text-[10px] font-mono text-slate-400">{fmtBp(r.q5_minus_q1_bp_10)}</span>
              </div>
            ))}
            {rejected.length === 0 && <EmptyHint text="无否决记录" />}
          </div>
        </div>

        {/* IC 衰减 + 复合分分年 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 min-w-0">
          <div className="min-w-0">
            <div className="text-[10px] font-extrabold text-slate-600 px-1">
              IC 衰减（h=2..20；h=1 在 T+1 入场口径下恒 0 不出）
            </div>
            <SmartChart option={decayOption} height={170} emptyText="衰减数据尚未生成" />
          </div>

          <div className="min-w-0">
            <div className="text-[10px] font-extrabold text-slate-600 px-1 pb-1">
              复合分分年（h{h}）
            </div>
            <div className="grid grid-cols-[48px_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,0.9fr)_44px] gap-1.5 px-1 pb-1 text-[9px] font-extrabold text-slate-400 border-b border-slate-100">
              <span>年</span>
              <span className="text-right">IC</span>
              <span className="text-right">ICIR</span>
              <span className="text-right">IC&gt;0率</span>
              <span className="text-right">q5−q1·bp</span>
              <span className="text-right">n</span>
            </div>
            {Object.keys(compYears).length ? (
              Object.entries(compYears).map(([y, segv]) => (
                <div
                  key={y}
                  className="grid grid-cols-[48px_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,0.9fr)_44px] gap-1.5 px-1 py-1 items-baseline border-b border-slate-50 last:border-0"
                >
                  <span className="text-[10px] font-bold text-slate-600">{y}</span>
                  <span className={`text-right text-[10px] font-mono ${segv?.rank_ic != null && segv.rank_ic < 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {fmtIc(segv?.rank_ic)}
                  </span>
                  <span className="text-right text-[10px] font-mono text-slate-500">{fmtNum(segv?.icir, 2)}</span>
                  <span className="text-right text-[10px] font-mono text-slate-500">{fmtNum(segv?.ic_pos_rate, 2)}</span>
                  <span className={`text-right text-[10px] font-mono ${segv?.q5_minus_q1_bp != null && segv.q5_minus_q1_bp < 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {fmtBp(segv?.q5_minus_q1_bp)}
                  </span>
                  <span className="text-right text-[10px] font-mono text-slate-400">{segv?.n_days ?? MISSING}</span>
                </div>
              ))
            ) : (
              <EmptyHint text="分年数据尚未生成" />
            )}
          </div>
        </div>

        {/* 相关性塌缩审计 */}
        <div className="rounded-xl bg-slate-50/80 border border-slate-100 px-3 py-2">
          <span className="text-[9px] font-extrabold text-slate-500 flex items-center gap-1">
            <GitMerge className="w-3 h-3" />
            相关性塌缩审计（两两秩相关日中位）
          </span>
          {collapsed.length ? (
            <div className="flex flex-col gap-1 mt-1">
              {collapsed.map((c, i) => (
                <div key={`${c.signal}-${i}`} className="flex items-start gap-2 text-[10px]">
                  <span className="font-bold text-slate-600 whitespace-nowrap">
                    {pillarLabel(c.signal)}
                  </span>
                  <span className="text-slate-400">
                    与 {pillarLabel(c.kept)} ρ={fmtNum(c.rho, 2)} · {c.reason}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[10px] text-slate-400 mt-0.5">
              无塌缩（入柱间相关性未触发降维阈值）
            </div>
          )}
        </div>

        {/* evidence_only 引述（仅展示，不入柱） */}
        {(evidence.evidence_only ?? []).length > 0 && (
          <div className="rounded-xl bg-slate-50/80 border border-slate-100 px-3 py-2">
            <span className="text-[9px] font-extrabold text-slate-500">仅展示证据（不入柱、不进分）</span>
            <div className="flex flex-col gap-1 mt-1">
              {(evidence.evidence_only ?? []).map((e) => (
                <div key={e.name} className="flex items-start gap-2 text-[10px]">
                  <span className="font-bold text-slate-600 whitespace-nowrap">{pillarLabel(e.name)}</span>
                  <span className="text-slate-400">
                    {e.conclusion}
                    {e.use ? ` · ${e.use}` : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </FaceCard>
  );
};
