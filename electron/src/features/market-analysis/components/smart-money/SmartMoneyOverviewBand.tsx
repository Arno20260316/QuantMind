/** 聪明钱 · 生态概览带（龙虎榜/机构席位/两融/大宗/调研/北向 六卡）。
 *
 * 展示口径：金额正负用 A 股红涨绿跌；家数/占比为中性事实；缺失「—」不补 0；
 * 每卡带数据日期（各源发布节奏不同，日期如实标注，不做跨源对齐假设）。
 */

import React from 'react';
import { Building2, Landmark, LineChart, ReceiptText, SearchCheck, Compass } from 'lucide-react';
import type { SmartOverviewPayload } from '../../services/smartMoneyService';
import {
  MISSING,
  fmtNum,
  signCls,
} from '../../smartMoneyVocabulary';

interface StatLineProps {
  label: string;
  value: number | null | undefined;
  unit?: string;
  /** 金额/正负数值 → 着色；家数/占比等中性值不传 */
  signed?: boolean;
  digits?: number;
}

const StatLine: React.FC<StatLineProps> = ({ label, value, unit = '', signed = false, digits = 2 }) => (
  <div className="flex items-baseline justify-between gap-2">
    <span className="text-[10px] text-slate-400 flex-shrink-0">{label}</span>
    <span
      className={`text-xs font-extrabold font-mono ${
        signed ? signCls(value) : value === null || value === undefined ? 'text-slate-400' : 'text-slate-700'
      }`}
    >
      {fmtNum(value, digits)}
      {unit && value !== null && value !== undefined ? unit : ''}
    </span>
  </div>
);

const MiniCard: React.FC<{
  title: string;
  icon: React.ReactNode;
  date?: string | null;
  children: React.ReactNode;
}> = ({ title, icon, date, children }) => (
  <div className="bg-white/90 backdrop-blur-md rounded-2xl px-3 py-2.5 border border-slate-200/80 shadow-sm flex flex-col gap-1.5 min-w-0">
    <div className="flex items-center justify-between gap-1">
      <span className="flex items-center gap-1 text-[11px] font-extrabold text-slate-800 whitespace-nowrap">
        {icon}
        {title}
      </span>
      <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">{date || ''}</span>
    </div>
    {children}
  </div>
);

interface SmartMoneyOverviewBandProps {
  overview: SmartOverviewPayload | null;
  loading?: boolean;
}

export const SmartMoneyOverviewBand: React.FC<SmartMoneyOverviewBandProps> = ({ overview, loading }) => {
  if (!overview) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="bg-white/70 rounded-2xl px-3 py-2.5 border border-slate-200/60 min-h-[86px] grid place-items-center"
          >
            <span className="text-[10px] text-slate-300 font-bold">{loading ? '加载中…' : MISSING}</span>
          </div>
        ))}
      </div>
    );
  }

  const { lhb, inst, dzjy, survey, margin, north } = overview;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
      <MiniCard title="龙虎榜生态" icon={<ReceiptText className="w-3 h-3 text-purple-600" />} date={lhb.date}>
        <StatLine label="上榜家数" value={lhb.n_stocks} unit=" 家" digits={0} />
        <StatLine label="净买额" value={lhb.net_buy_yi} unit=" 亿" signed />
        <StatLine label="20日均家数" value={lhb.n20d_avg} unit=" 家" digits={1} />
        <StatLine label="20日累计净买" value={lhb.net20d_sum_yi} unit=" 亿" signed digits={1} />
      </MiniCard>

      <MiniCard title="机构席位" icon={<Building2 className="w-3 h-3 text-indigo-600" />} date={inst.date}>
        <StatLine label="机构上榜家数" value={inst.n_stocks} unit=" 家" digits={0} />
        <StatLine label="机构净买额" value={inst.net_buy_yi} unit=" 亿" signed />
      </MiniCard>

      <MiniCard title="两融" icon={<Landmark className="w-3 h-3 text-sky-600" />} date={margin.date}>
        <StatLine label="净买入·5日" value={margin.net_5d_yi} unit=" 亿" signed />
        <StatLine label="融资余额" value={margin.balance_yi} unit=" 亿" />
      </MiniCard>

      <MiniCard title="大宗交易" icon={<LineChart className="w-3 h-3 text-amber-600" />} date={dzjy.date}>
        <StatLine label="成交额" value={dzjy.amount_yi} unit=" 亿" />
        <StatLine label="溢价成交占比" value={dzjy.premium_share === null ? null : dzjy.premium_share * 100} unit="%" digits={1} />
      </MiniCard>

      <MiniCard title="机构调研" icon={<SearchCheck className="w-3 h-3 text-emerald-600" />} date={survey.date}>
        <StatLine label="获调研家数" value={survey.n_stocks} unit=" 家" digits={0} />
        <StatLine label="接待机构数" value={survey.n_inst} unit=" 家次" digits={0} />
      </MiniCard>

      <MiniCard title="北向持仓（季度）" icon={<Compass className="w-3 h-3 text-rose-500" />} date={north.quarter}>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[10px] text-slate-400 flex-shrink-0">增持/减持</span>
          <span className="text-[10px] font-mono">
            <span className="text-red-600 font-extrabold">{fmtNum(north.n_inc, 0)}</span>
            <span className="text-slate-300"> / </span>
            <span className="text-green-600 font-extrabold">{fmtNum(north.n_dec, 0)}</span>
            <span className="text-slate-400"> 家</span>
          </span>
        </div>
        {north.top_inc.length > 0 ? (
          <div className="flex flex-col gap-0.5">
            {north.top_inc.slice(0, 2).map((r) => (
              <div key={r.symbol} className="flex items-baseline justify-between gap-1">
                <span className="text-[9px] text-slate-500 truncate">{r.stock_name || r.symbol}</span>
                <span className={`text-[9px] font-mono font-bold ${signCls(r.delta_pp)}`}>
                  {r.delta_pp !== null && Number(r.delta_pp) > 0 ? '+' : ''}
                  {fmtNum(r.delta_pp, 2)}pp
                </span>
              </div>
            ))}
          </div>
        ) : (
          <span className="text-[9px] text-slate-300">季度变动 {MISSING}</span>
        )}
      </MiniCard>
    </div>
  );
};
