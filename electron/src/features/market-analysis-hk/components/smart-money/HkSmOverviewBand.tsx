/** 港股聪明钱 · 类型净流概览带（细案 §6：数据日 + 新鲜度 + 6 类型 MiniCard + 入柱徽标）。
 *
 * 词表纪律见 hkSmartMoneyVocabulary：净流入/净流出为事实语义；「入柱」= h10 双过闸
 * 裁定徽标；缺失一律「—」绝不显示 0。
 */

import React from 'react';
import { Radar } from 'lucide-react';
import type { HkSmCategoryRow, HkSmOverviewResponse } from '../../services/api';
import { EmptyHint, SectionCard, fmtInt } from '../shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  ADMIT_NOTE,
  MISSING,
  categoryLabel,
  directionWord,
  flowWord,
  fmtNum,
  fmtSigned,
  signCls,
} from '../../hkSmartMoneyVocabulary';

interface HkSmOverviewBandProps {
  data: HkSmOverviewResponse | null;
  loading: boolean;
}

/** 「5日净流入 +98.6亿」的标签段；数值缺失时退回中性「净额」 */
function netLabel(days: number, v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return `${days}日净额`;
  return `${days}日${flowWord(v)}`;
}

function CategoryMiniCard({ row }: { row: HkSmCategoryRow }) {
  const color = INST_CATEGORY_COLORS[row.category] || '#94a3b8';
  const label = categoryLabel(row.category, row.category_label ?? row.label);
  const net5 = row.net5_yi;
  const net20 = row.net20_yi;
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2 flex flex-col gap-1 min-w-0">
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
      <div className="flex items-baseline justify-between gap-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">{netLabel(5, net5)}</span>
        <span className={`text-[11px] font-mono font-extrabold ${signCls(net5)}`}>
          {fmtSigned(net5, 1)}亿
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">{netLabel(20, net20)}</span>
        <span className={`text-[11px] font-mono font-extrabold ${signCls(net20)}`}>
          {fmtSigned(net20, 1)}亿
        </span>
      </div>
      <div className="flex items-baseline justify-between gap-1 border-t border-slate-100 pt-1">
        <span className="text-[9px] text-slate-400 whitespace-nowrap">
          持仓 {row.n_symbols != null ? `${fmtInt(row.n_symbols)} 只` : MISSING}
        </span>
        <span className="text-[10px] font-mono font-bold text-slate-600 whitespace-nowrap">
          {fmtNum(row.holding_yi, 0)}亿
        </span>
      </div>
      {row.best && (
        <div
          className="text-[8px] font-mono text-slate-400 truncate"
          title={`最强 h10 变体：${row.best.variant_label ?? row.best.variant ?? MISSING} · IC ${fmtNum(row.best.h10_ic, 4)} · 尾部 ${row.best.h10_tail_ok ? '同号' : '未过'}`}
        >
          最强 {row.best.variant ?? MISSING} · IC {fmtNum(row.best.h10_ic, 3)}
        </div>
      )}
    </div>
  );
}

export const HkSmOverviewBand: React.FC<HkSmOverviewBandProps> = ({ data, loading }) => {
  if (!data) {
    return (
      <SectionCard icon={<Radar className="w-3.5 h-3.5 text-purple-600" />} title="类型净流概览">
        <EmptyHint loading={loading} text="类型概览不可用" />
      </SectionCard>
    );
  }

  const fresh = data.freshness;
  const counts = data.counts_30d;

  return (
    <SectionCard
      icon={<Radar className="w-3.5 h-3.5 text-purple-600" />}
      title="类型净流概览"
      extra={
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
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
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            行情 {fresh?.kline_date ?? MISSING} · CCASS {fresh?.ccass_date ?? MISSING} · 南向{' '}
            {fresh?.south_date ?? MISSING}
            {fresh?.ccass_lag_days != null ? `（CCASS 滞后 ${fresh.ccass_lag_days} 交易日）` : ''}
          </span>
        </div>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
        {data.categories.map((c) => (
          <CategoryMiniCard key={c.category} row={c} />
        ))}
      </div>
      {counts && (
        <div className="flex items-center gap-3 flex-wrap text-[9px] text-slate-400 border-t border-slate-100 pt-1.5">
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
    </SectionCard>
  );
};
