/** 港股聪明钱 · 类型效力矩阵（24 信号 = 6 类型 × 4 变体；细案 §6）。
 *
 * 行 = 类型·变体（点击展开裁定明细）；列 = h5/h10/h20 前瞻 IC（中性色，入柱加粗）、
 * h10 尾部价差 q5−q1（bp，红绿符号）、增持/减持历史胜率（中性）、裁定徽标。
 * IC 与胜率是统计量不是涨跌——绝不套红绿；缺失「—」绝不为 0。
 */

import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Table2 } from 'lucide-react';
import type { HkSmSignal, HkSmTypesResponse } from '../../services/api';
import { EmptyHint, SectionCard, fmtInt } from '../shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  ADMIT_NOTE,
  CATEGORY_ORDER,
  HIT_NOTE,
  MISSING,
  categoryLabel,
  directionWord,
  fmtBp,
  fmtNum,
  fmtPct,
  icCls,
  signCls,
} from '../../hkSmartMoneyVocabulary';

interface HkSmTypeBoardProps {
  data: HkSmTypesResponse | null;
  loading: boolean;
  error: string | null;
}

const HORIZONS = ['5', '10', '20'] as const;

const GRID_COLS =
  'grid grid-cols-[minmax(140px,1.5fr)_50px_50px_50px_62px_54px_54px_72px] items-center gap-1';

function SignalRow({ sig }: { sig: HkSmSignal }) {
  const [expanded, setExpanded] = useState(false);
  const seg = (h: string) => sig.results?.[h] ?? null;
  const h10 = seg('10');
  const q5q1 = h10?.q5_minus_q1_bp ?? null;

  return (
    <div className="border-b border-slate-50 last:border-0">
      <div
        role="button"
        onClick={() => setExpanded((v) => !v)}
        className={`${GRID_COLS} px-1 py-1 cursor-pointer hover:bg-purple-50/50 transition-colors`}
      >
        <div className="flex items-center gap-1 min-w-0">
          {expanded ? (
            <ChevronDown className="w-3 h-3 text-slate-400 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-3 h-3 text-slate-400 flex-shrink-0" />
          )}
          <span className="text-[10px] font-bold text-slate-700 truncate" title={sig.name}>
            {sig.variant_label ?? sig.variant}
          </span>
        </div>
        {HORIZONS.map((h) => {
          const sg = seg(h);
          return (
            <span
              key={h}
              className={`text-right text-[10px] font-mono ${sg?.rank_ic != null ? 'font-bold' : ''} ${icCls(sg?.rank_ic)}`}
              title={
                sg
                  ? `h${h}：n=${fmtInt(sg.n_days)} 日 · ICIR ${fmtNum(sg.icir, 2)} · IC>0 占比 ${fmtPct(sg.ic_pos_rate, 0)}`
                  : `h${h} 缺失`
              }
            >
              {fmtNum(sg?.rank_ic, 3)}
            </span>
          );
        })}
        <span
          className={`text-right text-[10px] font-mono font-bold ${signCls(q5q1)}`}
          title={
            h10
              ? `h10 分位价差（bp）：q1 ${fmtBp(h10.q1_bp)} / q2 ${fmtBp(h10.q2_bp)} / q3 ${fmtBp(h10.q3_bp)} / q4 ${fmtBp(h10.q4_bp)} / q5 ${fmtBp(h10.q5_bp)}`
              : 'h10 缺失'
          }
        >
          {fmtBp(q5q1, 1)}
        </span>
        <span
          className="text-right text-[10px] font-mono font-bold text-slate-600"
          title={HIT_NOTE}
        >
          {fmtPct(h10?.buy_hit, 0)}
        </span>
        <span
          className="text-right text-[10px] font-mono font-bold text-slate-600"
          title={HIT_NOTE}
        >
          {fmtPct(h10?.sell_hit, 0)}
        </span>
        <span className="flex justify-end">
          {sig.admitted ? (
            <span
              className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-600 text-white whitespace-nowrap"
              title={sig.decision ?? ADMIT_NOTE}
            >
              入柱
            </span>
          ) : (
            <span
              className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-slate-100 text-slate-400 border border-slate-200 whitespace-nowrap"
              title={sig.decision ?? ADMIT_NOTE}
            >
              未过闸
            </span>
          )}
        </span>
      </div>
      {expanded && (
        <div className="mx-1 mb-1.5 rounded-lg bg-slate-50/80 border border-slate-100 px-2.5 py-1.5 flex flex-col gap-1 text-[9px] text-slate-500 leading-relaxed">
          <div className="font-bold text-slate-700">{sig.decision ?? ADMIT_NOTE}</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
            <span>方向：{directionWord(sig.direction)}（按 h10 IC 符号）</span>
            <span>
              覆盖：日均 {fmtNum(sig.coverage?.avg_names_per_day, 0)} 名 ·{' '}
              {fmtInt(sig.coverage?.days)} 日
            </span>
            {HORIZONS.map((h) => {
              const sg = sig.results?.[h];
              return (
                <span key={h}>
                  h{h}：n {fmtInt(sg?.n_days)} · ICIR {fmtNum(sg?.icir, 2)} · IC&gt;0 占比{' '}
                  {fmtPct(sg?.ic_pos_rate, 0)} · 尾部同号{' '}
                  {sg?.tail_ok == null ? MISSING : sg.tail_ok ? '是' : '否'}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export const HkSmTypeBoard: React.FC<HkSmTypeBoardProps> = ({ data, loading, error }) => {
  const groups = useMemo(() => {
    if (!data) return [] as Array<[string, HkSmSignal[]]>;
    const order = new Map<string, number>(CATEGORY_ORDER.map((c, i) => [c, i] as const));
    const byCat = new Map<string, HkSmSignal[]>();
    for (const s of data.signals ?? []) {
      const list = byCat.get(s.category) ?? [];
      list.push(s);
      byCat.set(s.category, list);
    }
    return [...byCat.entries()].sort(
      (a, b) => (order.get(a[0]) ?? 99) - (order.get(b[0]) ?? 99),
    );
  }, [data]);

  const window = data?.window;

  return (
    <SectionCard
      icon={<Table2 className="w-3.5 h-3.5 text-purple-600" />}
      title="类型效力矩阵"
      extra={
        window ? (
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            事件窗 {window.eval_start ?? MISSING} → {window.eval_end ?? MISSING} · 事件{' '}
            {fmtInt(window.event_days)} 日 / {fmtInt(window.event_rows)} 行
          </span>
        ) : undefined
      }
    >
      {error && (
        <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
          类型效力数据不可用：{error}
        </div>
      )}
      {!data && !error && <EmptyHint loading={loading} text="类型效力数据不可用" />}
      {data && groups.length > 0 && (
        <div className="overflow-x-auto">
          <div className="min-w-[660px]">
            <div
              className={`${GRID_COLS} px-1 pb-1 border-b border-slate-100 text-[9px] font-extrabold text-slate-400`}
            >
              <span>类型 · 变体（点击展开）</span>
              <span className="text-right">h5 IC</span>
              <span className="text-right">h10 IC</span>
              <span className="text-right">h20 IC</span>
              <span className="text-right" title="h10 分位五档：q5 超额 − q1 超额（bp）">
                h10 q5−q1
              </span>
              <span className="text-right" title={HIT_NOTE}>
                增持胜率
              </span>
              <span className="text-right" title={HIT_NOTE}>
                减持胜率
              </span>
              <span className="text-right" title={ADMIT_NOTE}>
                裁定
              </span>
            </div>
            <div className="max-h-[430px] overflow-y-auto">
              {groups.map(([cat, rows]) => (
                <React.Fragment key={cat}>
                  <div className="flex items-center gap-1.5 px-1 pt-2 pb-1 sticky top-0 bg-white/95 backdrop-blur z-10">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ background: INST_CATEGORY_COLORS[cat] || '#94a3b8' }}
                    />
                    <span className="text-[10px] font-extrabold text-slate-700">
                      {categoryLabel(cat, rows[0]?.category_label)}
                    </span>
                    {rows.some((r) => r.admitted) && (
                      <span className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-50 text-purple-700 border border-purple-100">
                        入柱 {rows.filter((r) => r.admitted).length}
                      </span>
                    )}
                  </div>
                  {rows.map((s) => (
                    <SignalRow key={`${cat}-${s.variant}`} sig={s} />
                  ))}
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>
      )}
      {data && data.notes && (
        <p className="text-[9px] text-slate-400 leading-relaxed">
          历史胜率 = 事件后 10 日超额与方向同号的占比（事后统计，非预测）；IC 与胜率为统计量（中性色），
          q5−q1 为分位价差（bp）。入柱判据见裁定列 tooltip——「未过闸」为真裁定，不是缺数。
        </p>
      )}
    </SectionCard>
  );
};
