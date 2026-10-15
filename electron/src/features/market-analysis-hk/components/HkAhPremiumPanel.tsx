/** AH 溢价榜 —— 同一资产两地比价（H 折价 / 倒挂两角） */

import React, { useEffect, useState } from 'react';
import { Scale, AlertTriangle } from 'lucide-react';
import type { HkAhPremium } from '../types';
import { getAhPremium } from '../services/api';
import { EmptyHint, RankRow, SectionCard } from './shared/ui';

/** 数据陈旧判据：交易日距今 > 10 个自然日视为停更（动态计算，源侧修复后自动消失） */
const STALE_DAYS = 10;

function isStale(dateStr: string): boolean {
  if (!dateStr) return false;
  const t = Date.parse(dateStr.replace(/-/g, '/'));
  if (Number.isNaN(t)) return false;
  return Date.now() - t > STALE_DAYS * 24 * 3600 * 1000;
}

export const HkAhPremiumPanel: React.FC<{ onPick?: (symbol: string) => void }> = ({ onPick }) => {
  const [data, setData] = useState<HkAhPremium | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getAhPremium(20)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const renderList = (items: typeof data.premium, isPremium: boolean) =>
    items.slice(0, 10).map((it, i) => (
      <RankRow
        key={it.h_symbol + String(isPremium)}
        rank={i + 1}
        name={it.h_name}
        nameSub={it.h_symbol}
        onClick={onPick ? () => onPick(it.h_symbol) : undefined}
        title={onPick ? `打开 ${it.h_symbol} 个股终端` : undefined}
        main={
          <span className="text-[9px] font-mono text-slate-400 truncate">
            {it.a_symbol}
          </span>
        }
        right={
          <span className={`text-[11px] font-extrabold font-mono flex-shrink-0 ${isPremium ? 'text-amber-600' : 'text-sky-600'}`}>
            {isPremium ? 'A贵+H' : '倒挂'}{it.premium_pct.toFixed(0)}%
          </span>
        }
      />
    ));

  return (
    <SectionCard
      title={
        <span className="flex items-center gap-1.5">
          <Scale className="w-3.5 h-3.5 text-purple-600" />
          AH 溢价 · 两地比价
        </span>
      }
      extra={
        <div className="flex items-center gap-1">
          {data && <span className="text-[9px] font-mono text-slate-400">{data.trade_date}</span>}
        </div>
      }
    >
      {loading && !data ? (
        <EmptyHint loading />
      ) : data ? (
        <div className="flex flex-col gap-2">
          {isStale(data.trade_date) && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2">
              <AlertTriangle className="w-3.5 h-3.5 text-amber-600 flex-shrink-0 mt-0.5" />
              <span className="text-[10px] leading-relaxed text-amber-800">
                <b>该数据源已停止更新</b>（最新数据 {data.trade_date}）。
                AH 溢价生产者不在每日同步链内，溢价数值为历史快照 —— 仅作结构参考，
                不可作为当日比价依据。
              </span>
            </div>
          )}
          <div className="flex items-center justify-between px-1">
            <span className="text-[10px] font-extrabold text-amber-600 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              H 折价（A 贵，买 H 划算）
            </span>
            <span className="text-[9px] text-slate-400">溢价↑</span>
          </div>
          <div className="rounded-xl border border-amber-100/70 bg-amber-50/30 p-1">
            {data.premium.length ? renderList(data.premium, true) : <EmptyHint text="暂无数据" />}
          </div>
          <div className="flex items-center justify-between px-1 pt-1">
            <span className="text-[10px] font-extrabold text-sky-600 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-sky-500" />
              倒挂（H 贵，A 折价）
            </span>
            <span className="text-[9px] text-slate-400">溢价↓</span>
          </div>
          <div className="rounded-xl border border-sky-100/70 bg-sky-50/30 p-1">
            {data.discount.length ? renderList(data.discount, false) : <EmptyHint text="暂无数据" />}
          </div>
        </div>
      ) : (
        <EmptyHint text="AH 溢价数据不可用" />
      )}
    </SectionCard>
  );
};