/** 聪明钱 · 验证证据台（双过闸审计，对量化研究员展示口径与证据）。
 *
 * 内容：因子→h10 前瞻收益双过闸（sign(IC)==sign(q5−q1) ∧ |IC|≥0.01 ∧
 * |q5−q1|>1bp ∧ n≥60）的入柱/否决全量披露 + 复合分验证 + evidence_only 引述。
 * 方向列 = 入柱方向（h10 实测 IC 符号）；负向柱分位在评分链中已取反。
 */

import React from 'react';
import { FlaskConical, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Tooltip } from 'antd';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import type { SmartEvidenceResponse, SmartEvidenceSeg, SmartEvidenceSignal } from '../../services/smartMoneyService';
import { MISSING, directionHint, directionLabel, fmtNum, pillarLabel } from '../../smartMoneyVocabulary';

/** bp 值 → 带符号 1 位小数 */
function fmtBp(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${Number(v) > 0 ? '+' : ''}${fmtNum(v, 1)}`;
}

/** IC → 4 位小数（研究精度） */
function fmtIc(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return MISSING;
  return `${Number(v) > 0 ? '+' : ''}${fmtNum(v, 4)}`;
}

const DirectionChip: React.FC<{ direction: number | null }> = ({ direction }) => {
  const tone =
    direction === 1
      ? 'bg-red-50 text-red-700 border-red-100'
      : direction === -1
        ? 'bg-green-50 text-green-700 border-green-100'
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
    <span className={`text-right text-[10px] font-mono ${
      seg && seg.rank_ic !== null && seg.rank_ic < 0 ? 'text-green-600' : 'text-red-600'
    }`}>
      {fmtIc(seg?.rank_ic)}
    </span>
    <span className={`text-right text-[10px] font-mono ${
      seg && seg.q5_minus_q1_bp !== null && seg.q5_minus_q1_bp < 0 ? 'text-green-600' : 'text-red-600'
    }`}>
      {fmtBp(seg?.q5_minus_q1_bp)}
    </span>
    <span className="text-right text-[10px] font-mono text-slate-400">{seg?.n_days ?? MISSING}</span>
  </>
);

interface SmartMoneyEvidenceCardProps {
  evidence: SmartEvidenceResponse | null;
  loading?: boolean;
}

export const SmartMoneyEvidenceCard: React.FC<SmartMoneyEvidenceCardProps> = ({ evidence, loading }) => {
  if (!evidence) {
    return (
      <SectionCard icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />} title="验证证据台（双过闸审计）">
        <EmptyHint text={loading ? '加载中…' : '暂无验证记录（先运行 smart_money_validate.py）'} loading={loading} />
      </SectionCard>
    );
  }

  const h10 = (s: SmartEvidenceSignal): SmartEvidenceSeg | null | undefined => s.results?.['10'];
  const admitted = evidence.signals.filter((s) => s.admitted);
  const rejected = evidence.rejected ?? [];
  const comp = evidence.composite?.results?.['10'];
  const win = evidence.window;

  return (
    <SectionCard
      icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          验证证据台（双过闸审计）
          <span className="text-[10px] font-normal text-slate-400">
            判据：sign(IC)=sign(q5−q1) ∧ |IC|≥0.01 ∧ |q5−q1|&gt;1bp ∧ n≥60（h=10 前瞻）
          </span>
        </span>
      }
      extra={
        <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
          {win ? `${win.eval_start} → ${win.eval_end}（预热自 ${win.warmup_start}）` : MISSING}
          {evidence.validated_at ? ` · 验证于 ${evidence.validated_at}` : ''}
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        {/* 复合分验证条 */}
        <div className="rounded-xl bg-purple-50/60 border border-purple-100 px-3 py-2 flex items-center gap-x-5 gap-y-1 flex-wrap">
          <span className="text-[10px] font-extrabold text-purple-800 flex items-center gap-1">
            <ShieldCheck className="w-3.5 h-3.5" />
            复合分（入柱等权·方向对齐后）h10 验证
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
            胜率 <span className="font-mono font-bold text-slate-800">{fmtNum(comp?.ic_pos_rate, 2)}</span>
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
              {evidence.composite?.avg_covered === null || evidence.composite?.avg_covered === undefined
                ? MISSING
                : fmtNum(evidence.composite.avg_covered, 1)}
            </span>
          </span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 min-w-0">
          {/* 入柱表 */}
          <div className="min-w-0">
            <div className="grid grid-cols-[minmax(120px,1.4fr)_48px_72px_80px_40px] gap-2 px-1 pb-1.5 text-[9px] font-extrabold text-slate-400 border-b border-slate-100">
              <span>入柱（{admitted.length}）</span>
              <span>方向</span>
              <span className="text-right">IC·h10</span>
              <span className="text-right">q5−q1·bp</span>
              <span className="text-right">n</span>
            </div>
            {admitted.map((s) => (
              <div
                key={s.name}
                className="grid grid-cols-[minmax(120px,1.4fr)_48px_72px_80px_40px] gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 items-center"
              >
                <Tooltip title={s.name}>
                  <span className="text-[10px] font-bold text-slate-700 truncate">{pillarLabel(s.name)}</span>
                </Tooltip>
                <DirectionChip direction={s.direction} />
                <SegCells seg={h10(s)} />
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
    </SectionCard>
  );
};
