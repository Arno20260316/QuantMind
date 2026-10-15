/** 期货市场分析 · 三层市场统计卡（全市场 / 国内 / 国际 / 上金所）
 *
 * 口径：名义额 = 价 × 量仓 × 合约乘数（后端已换算为亿）；国际品种与上金所
 * 无量仓数据 → 「—」而非 0。中位涨幅取有效涨跌幅品种的中位数。
 */

import React from 'react';
import { Globe2, Landmark, MapPin, Coins } from 'lucide-react';
import { PctText } from '../../market-analysis-shared/ui';
import type { FuturesGroupStat, FuturesOverview } from '../types';
import { fmtUnit } from '../format';

interface CardMeta {
  key: keyof Pick<FuturesOverview, 'totals' | 'domestic' | 'international' | 'sge'>;
  label: string;
  icon: React.ReactNode;
  hint: string;
}

const CARDS: CardMeta[] = [
  {
    key: 'totals',
    label: '全市场',
    icon: <Globe2 className="w-3.5 h-3.5 text-amber-600" />,
    hint: '全市场 = 国内品种 + 国际品种 + 上金所；名义额为国内口径合计',
  },
  {
    key: 'domestic',
    label: '国内品种',
    icon: <MapPin className="w-3.5 h-3.5 text-red-500" />,
    hint: '国内期货主力连续（.CN）。名义额 = 价 × 手数 × 合约乘数，非真实资金流水',
  },
  {
    key: 'international',
    label: '国际品种',
    icon: <Landmark className="w-3.5 h-3.5 text-sky-500" />,
    hint: '国际品种（.FUT）：多数源不提供持仓量，无乘数故无名义额 → 显示「—」',
  },
  {
    key: 'sge',
    label: '上金所现货',
    icon: <Coins className="w-3.5 h-3.5 text-yellow-600" />,
    hint: '上海黄金交易所现货合约（Au99.99 等），仅价格数据，无量仓',
  },
];

function GroupCard({ meta, stat }: { meta: CardMeta; stat: FuturesGroupStat | null }) {
  const s = stat;
  const up = s?.up ?? 0;
  const down = s?.down ?? 0;
  const flat = s?.flat ?? 0;
  return (
    <div
      title={meta.hint}
      className="bg-white/90 backdrop-blur-md rounded-2xl px-3.5 py-2.5 border border-amber-200/60 shadow-sm flex flex-col gap-1"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-extrabold text-slate-700">
          {meta.icon}
          <span>{meta.label}</span>
        </span>
        <span className="text-[10px] font-mono font-extrabold text-slate-400 whitespace-nowrap">
          {s ? `${s.total} 品种` : '—'}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] font-bold text-slate-400 whitespace-nowrap">中位涨幅</span>
        <span className="text-base leading-tight">
          <PctText value={s?.median_pct ?? null} />
        </span>
        <span className="text-[10px] font-mono text-slate-400 ml-auto whitespace-nowrap">
          有效 {s?.active ?? '—'}
        </span>
      </div>
      <div className="flex items-center gap-2 text-[11px] font-mono font-extrabold">
        <span className="text-red-600">涨 {up}</span>
        <span className="text-green-600">跌 {down}</span>
        <span className="text-slate-400">平 {flat}</span>
      </div>
      <div className="grid grid-cols-3 gap-1 text-[10px] font-mono">
        <span className="flex flex-col" title="名义成交额（亿）：价 × 量 × 乘数合计">
          <span className="text-slate-400 font-sans font-bold">成交额</span>
          <span className="font-extrabold text-slate-700">{fmtUnit(s?.turnover_yi, '亿')}</span>
        </span>
        <span className="flex flex-col" title="名义持仓额（亿）：价 × 仓 × 乘数合计">
          <span className="text-slate-400 font-sans font-bold">持仓额</span>
          <span className="font-extrabold text-slate-700">{fmtUnit(s?.oi_amount_yi, '亿')}</span>
        </span>
        <span className="flex flex-col" title="持仓量日变化（万手）">
          <span className="text-slate-400 font-sans font-bold">仓差</span>
          <span className="font-extrabold text-slate-700">{fmtUnit(s?.oi_change_wan, '万手')}</span>
        </span>
      </div>
    </div>
  );
}

export const FuturesStatCards: React.FC<{ overview: FuturesOverview | null }> = ({ overview }) => (
  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2.5">
    {CARDS.map((meta) => (
      <GroupCard key={meta.key} meta={meta} stat={overview ? overview[meta.key] : null} />
    ))}
  </div>
);
