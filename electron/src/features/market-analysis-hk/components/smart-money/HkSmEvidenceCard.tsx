/** 港股聪明钱 · 证据台（细案 §6：裁定协议 / 标定网格 / 排除计数 / 对账 / 方法学 / 警告）。
 *
 * 全部字段原样透传自 evidence.json：网格未落目标区间、零入柱等均为**真裁定**，
 * 如实呈现（不美化、不隐藏）；缺失一律「—」。
 */

import React from 'react';
import { AlertTriangle, FlaskConical } from 'lucide-react';
import type { HkSmCalibration, HkSmEvidenceResponse } from '../../services/api';
import { EmptyHint, SectionCard, fmtInt } from '../shared/ui';
import { ADMIT_NOTE, MISSING, fmtHkdWan, fmtNum, fmtShares } from '../../hkSmartMoneyVocabulary';

interface HkSmEvidenceCardProps {
  evidence: HkSmEvidenceResponse | null;
  loading: boolean;
}

const METHOD_LABELS: Record<string, string> = {
  event_gate: '事件门槛',
  transfer_suspect: '过户嫌疑判定',
  episode: 'episode 归并',
  forward: '事后收益口径',
  gate: '入柱闸门',
  seat_shrink: '席位收缩',
  composite: '复合分',
};

const GRID_COLS =
  'grid grid-cols-[64px_50px_58px_50px_66px_50px_58px] items-center gap-1';

function isSelected(g: { min_value_hkd: number | null; min_pct: number | null }, cal: HkSmCalibration): boolean {
  if (!cal.selected) return false;
  return (
    g.min_value_hkd === cal.selected.min_value_hkd && g.min_pct === cal.selected.min_pct
  );
}

function CalibrationTable({ cal }: { cal: HkSmCalibration }) {
  const target = cal.target_daily_events;
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-[10px] font-extrabold text-slate-700">标定网格</span>
        <span className="text-[9px] font-mono text-slate-400">
          样本 {cal.sample_start ?? MISSING} → {cal.sample_end ?? MISSING}（{fmtInt(cal.sample_days)} 日）·
          目标日均 {target ? `${fmtNum(target[0], 0)}–${fmtNum(target[1], 0)}` : MISSING} 事件
        </span>
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[440px]">
          <div
            className={`${GRID_COLS} px-1 pb-1 border-b border-slate-100 text-[8px] font-extrabold text-slate-400 text-right [&>span:first-child]:text-left`}
          >
            <span>金额门槛</span>
            <span>占比</span>
            <span>日均</span>
            <span>中位</span>
            <span>覆盖票均</span>
            <span>嫌疑</span>
            <span>状态</span>
          </div>
          <div className="max-h-[260px] overflow-y-auto">
            {cal.grid.map((g, i) => {
              const selected = isSelected(g, cal);
              return (
                <div
                  key={`${g.min_value_hkd}-${g.min_pct}-${i}`}
                  className={`${GRID_COLS} px-1 py-1 border-b border-slate-50 last:border-0 text-[9px] font-mono text-right [&>span:first-child]:text-left ${
                    selected ? 'bg-purple-50/70' : ''
                  }`}
                >
                  <span className={selected ? 'font-extrabold text-purple-700' : 'text-slate-600'}>
                    {fmtHkdWan(g.min_value_hkd)}
                  </span>
                  <span className="text-slate-500">{fmtNum(g.min_pct != null ? g.min_pct * 100 : null, 1)}%</span>
                  <span className="text-slate-600 font-bold">{fmtNum(g.mean_daily, 1)}</span>
                  <span className="text-slate-500">{fmtNum(g.median_daily, 0)}</span>
                  <span className="text-slate-500">{fmtNum(g.covered_symbols_mean, 1)}</span>
                  <span className="text-slate-500">{fmtNum(g.suspect_total, 0)}</span>
                  <span className="flex justify-end gap-1">
                    {selected && (
                      <span className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-600 text-white">
                        选定
                      </span>
                    )}
                    <span
                      className={`px-1.5 py-px rounded-full text-[8px] font-extrabold border ${
                        g.in_target
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-amber-50 text-amber-700 border-amber-200'
                      }`}
                    >
                      {g.in_target ? '落目标' : '未落'}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {cal.note && <p className="text-[9px] text-slate-400 leading-relaxed">{cal.note}</p>}
    </div>
  );
}

export const HkSmEvidenceCard: React.FC<HkSmEvidenceCardProps> = ({ evidence, loading }) => {
  if (!evidence) {
    return (
      <SectionCard
        icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />}
        title="证据台 · 裁定与标定"
      >
        <EmptyHint loading={loading} text="证据面不可用" />
      </SectionCard>
    );
  }

  const win = evidence.window;
  const exc = evidence.exclusions;
  const rec = evidence.reconciliation;
  const ratioOk = rec?.ratio != null && rec.ratio >= 0.98 && rec.ratio <= 1.02;
  const methodEntries = Object.entries(evidence.method ?? {});

  return (
    <SectionCard
      icon={<FlaskConical className="w-3.5 h-3.5 text-purple-600" />}
      title="证据台 · 裁定与标定"
      extra={
        <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
          裁定时间 {evidence.validated_at ?? MISSING} · 版本 {evidence.version ?? MISSING}
        </span>
      }
    >
      {/* 裁定协议行 */}
      <div className="flex items-center gap-2 flex-wrap text-[9px] font-mono text-slate-500">
        <span>
          事件窗 {win?.eval_start ?? MISSING} → {win?.eval_end ?? MISSING}
        </span>
        <span>· 事件 {fmtInt(win?.event_days)} 日 / {fmtInt(win?.event_rows)} 行</span>
        {evidence.evidence_only ? (
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
            入柱 {fmtInt(evidence.admitted?.length ?? null)} 项
          </span>
        )}
        <span title={ADMIT_NOTE}>入柱判据 h{evidence.admit_horizon ?? MISSING}（IC 显著 ∧ 尾部同号）</span>
      </div>

      {evidence.calibration && <CalibrationTable cal={evidence.calibration} />}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 min-w-0">
        {/* 排除计数 */}
        <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2 flex flex-col gap-1">
          <div className="text-[10px] font-extrabold text-slate-700">排除计数（事件仓汇总）</div>
          {exc ? (
            <>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">原始变动行</span>
                <span className="font-mono font-bold text-slate-600">{fmtInt(exc.raw_moves)}</span>
              </div>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">过户嫌疑（只标记不计入）</span>
                <span className="font-mono font-bold text-slate-600">
                  {fmtInt(exc.transfer_suspect)}
                  {exc.transfer_suspect_share != null
                    ? `（${fmtNum(exc.transfer_suspect_share * 100, 1)}%）`
                    : ''}
                </span>
              </div>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">首现 / 退出边界</span>
                <span className="font-mono font-bold text-slate-600">
                  {fmtInt(exc.first_seen)} / {fmtInt(exc.exit_boundary)}
                </span>
              </div>
              <div className="flex items-baseline justify-between text-[9px] border-t border-slate-100 pt-1">
                <span className="text-slate-400">事件（入统）</span>
                <span className="font-mono font-extrabold text-slate-700">{fmtInt(exc.events)}</span>
              </div>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">事件日 / 南向日</span>
                <span className="font-mono text-slate-500">
                  {fmtInt(exc.days)} / {fmtInt(exc.south_days)}
                </span>
              </div>
            </>
          ) : (
            <EmptyHint text="排除计数缺失" />
          )}
        </div>

        {/* 对账 */}
        <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2 flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-extrabold text-slate-700">南向对账（A 席 vs 披露）</span>
            {rec?.ratio != null && (
              <span
                className={`px-1.5 py-px rounded-full text-[8px] font-extrabold border ${
                  ratioOk
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-amber-50 text-amber-700 border-amber-200'
                }`}
                title="对账 = 港股通结算席位（A 席）持仓合计 vs hsgt_south 披露合计；比值贴近 1 表示两侧口径互洽"
              >
                {ratioOk ? '±2% 内' : '超 ±2% 披露'}
              </span>
            )}
          </div>
          {rec ? (
            <>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">A 席持仓合计</span>
                <span className="font-mono font-bold text-slate-600">{fmtShares(rec.a_seat_qty_sum)}</span>
              </div>
              <div className="flex items-baseline justify-between text-[9px]">
                <span className="text-slate-400">南向披露合计</span>
                <span className="font-mono font-bold text-slate-600">{fmtShares(rec.south_qty_sum)}</span>
              </div>
              <div className="flex items-baseline justify-between text-[9px] border-t border-slate-100 pt-1">
                <span className="text-slate-400">比值（{rec.as_of ?? MISSING}）</span>
                <span className="font-mono font-extrabold text-slate-700">{fmtNum(rec.ratio, 4)}</span>
              </div>
              <p className="text-[8px] text-slate-400 leading-relaxed">
                南向为 hsgt_south 直读披露口径，不在 CCASS 事件仓内（席位榜不含量化南向席位）。
              </p>
            </>
          ) : (
            <EmptyHint text="对账缺失" />
          )}
        </div>
      </div>

      {/* 方法学 */}
      {methodEntries.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="text-[10px] font-extrabold text-slate-700">方法学（唯一出处）</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5 text-[9px] text-slate-500 leading-relaxed">
            {methodEntries.map(([k, v]) => (
              <div key={k}>
                <span className="font-bold text-slate-600">{METHOD_LABELS[k] ?? k}：</span>
                {v}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 警告 */}
      {(evidence.warnings ?? []).length > 0 && (
        <div className="flex flex-col gap-1">
          {evidence.warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 rounded-lg bg-amber-50/80 border border-amber-200 px-2.5 py-1.5 text-[10px] text-amber-800 font-bold leading-relaxed"
            >
              <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
};
