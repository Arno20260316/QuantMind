/** 聪明钱 · 生态概览带 v2（细案 §7：B7 带符号统一格式 / B9 北向双向可折叠 / 每卡 mini 走势）。
 *
 * 展示口径：金额正负用 A 股红涨绿跌；家数/占比为中性事实；缺失「—」不补 0；
 * 每卡带数据日期（各源发布节奏不同，如实标注，不做跨源对齐假设）。
 */

import React, { useState } from 'react';
import { Building2, ChevronDown, Compass, Landmark, LineChart, ReceiptText, SearchCheck } from 'lucide-react';
import { Tooltip } from 'antd';
import type { SmartOverviewPayload, SmartTrendRow } from '../../services/smartMoneyService';
import { MISSING, fmtNum, fmtPct, fmtSigned, signCls } from '../../smartMoneyVocabulary';

/** 无轴 mini 走势（SVG 折线；缺失断线；不足 2 点不画） */
const Spark: React.FC<{ values: Array<number | null>; className?: string }> = ({
  values,
  className = '',
}) => {
  const pts = values.filter((v): v is number => v !== null && !Number.isNaN(v));
  if (pts.length < 2) return null;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const W = 100;
  const H = 22;
  const step = W / (values.length - 1);
  const segs: string[] = [];
  let run: string[] = [];
  values.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) {
      if (run.length > 1) segs.push(run.join(' '));
      run = [];
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (H - ((v - min) / span) * (H - 4) - 2).toFixed(1);
    run.push(`${x},${y}`);
  });
  if (run.length > 1) segs.push(run.join(' '));
  if (!segs.length) return null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`w-full h-[22px] ${className}`}>
      {segs.map((s, i) => (
        <polyline
          key={i}
          points={s}
          fill="none"
          stroke="#7c3aed"
          strokeWidth="1.2"
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity="0.75"
        />
      ))}
    </svg>
  );
};

interface StatLineProps {
  label: string;
  value: number | null | undefined;
  unit?: string;
  /** 金额/正负数值 → 着色 +「+」号；家数/占比等中性值不传 */
  signed?: boolean;
  digits?: number;
  /** 占比分数（0–1）→ 百分数展示 */
  pct?: boolean;
}

const StatLine: React.FC<StatLineProps> = ({
  label,
  value,
  unit = '',
  signed = false,
  digits = 2,
  pct = false,
}) => {
  const display = pct ? fmtPct(value, digits) : signed ? fmtSigned(value, digits) : fmtNum(value, digits);
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[10px] text-slate-400 flex-shrink-0">{label}</span>
      <span
        className={`text-xs font-extrabold font-mono ${
          signed
            ? signCls(value)
            : value === null || value === undefined
              ? 'text-slate-400'
              : 'text-slate-700'
        }`}
      >
        {display}
        {unit && value !== null && value !== undefined ? unit : ''}
      </span>
    </div>
  );
};

const MiniCard: React.FC<{
  title: string;
  icon: React.ReactNode;
  date?: string | null;
  titleTip?: string;
  spark?: React.ReactNode;
  children: React.ReactNode;
}> = ({ title, icon, date, titleTip, spark, children }) => (
  <div className="bg-white rounded-xl px-3 py-2.5 border border-slate-200/70 flex flex-col gap-1.5 min-w-0">
    <div className="flex items-center justify-between gap-1">
      <Tooltip title={titleTip}>
        <span className="flex items-center gap-1 text-[11px] font-extrabold text-slate-800 whitespace-nowrap">
          {icon}
          {title}
        </span>
      </Tooltip>
      <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">{date || ''}</span>
    </div>
    {spark}
    {children}
  </div>
);

const NORTH_PREVIEW = 2;
const NORTH_EXPANDED = 5;

interface SmartMoneyOverviewBandProps {
  overview: SmartOverviewPayload | null;
  trends?: Record<string, SmartTrendRow[]> | null;
  northNote?: string | null;
  loading?: boolean;
}

export const SmartMoneyOverviewBand: React.FC<SmartMoneyOverviewBandProps> = ({
  overview,
  trends,
  northNote,
  loading,
}) => {
  const [northExpanded, setNorthExpanded] = useState(false);

  if (!overview) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="bg-white rounded-xl px-3 py-2.5 border border-slate-200/60 min-h-[86px] grid place-items-center"
          >
            <span className="text-[10px] text-slate-300 font-bold">{loading ? '加载中…' : MISSING}</span>
          </div>
        ))}
      </div>
    );
  }

  const { lhb, inst, dzjy, survey, margin, north } = overview;

  const sparkOf = (key: string, col: string): React.ReactNode => {
    const rows = trends?.[key];
    if (!rows?.length) return null;
    return (
      <Spark
        values={rows.map((r) => {
          const v = r[col];
          return v === null || v === undefined ? null : Number(v);
        })}
      />
    );
  };

  const northRows = (rows: typeof north.top_inc, n: number) => rows.slice(0, n);
  const northLimit = northExpanded ? NORTH_EXPANDED : NORTH_PREVIEW;
  const northHasMore =
    north.top_inc.length > NORTH_PREVIEW || north.top_dec.length > NORTH_PREVIEW;

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
      <MiniCard
        title="龙虎榜生态"
        icon={<ReceiptText className="w-3 h-3 text-purple-600" />}
        date={lhb.date}
        spark={sparkOf('lhb', 'lhb_net_buy_yi')}
      >
        <StatLine label="上榜家数" value={lhb.n_stocks} unit=" 家" digits={0} />
        <StatLine label="净买额" value={lhb.net_buy_yi} unit=" 亿" signed />
        <StatLine label="20日均家数" value={lhb.n20d_avg} unit=" 家" digits={1} />
        <StatLine label="20日累计净买" value={lhb.net20d_sum_yi} unit=" 亿" signed digits={1} />
      </MiniCard>

      <MiniCard
        title="机构席位"
        icon={<Building2 className="w-3 h-3 text-indigo-600" />}
        date={inst.date}
        spark={sparkOf('inst', 'inst_net_buy_yi')}
      >
        <StatLine label="机构上榜家数" value={inst.n_stocks} unit=" 家" digits={0} />
        <StatLine label="机构净买额" value={inst.net_buy_yi} unit=" 亿" signed />
      </MiniCard>

      <MiniCard
        title="两融"
        icon={<Landmark className="w-3 h-3 text-sky-600" />}
        date={margin.date}
        titleTip="两融官方披露滞后行情约 1 个交易日；余额类按最近披露值口径"
        spark={sparkOf('margin', 'margin_net_5d_yi')}
      >
        <StatLine label="净买入·5日" value={margin.net_5d_yi} unit=" 亿" signed />
        <StatLine label="融资余额" value={margin.balance_yi} unit=" 亿" />
      </MiniCard>

      <MiniCard
        title="大宗交易"
        icon={<LineChart className="w-3 h-3 text-amber-600" />}
        date={dzjy.date}
        spark={sparkOf('dzjy', 'dzjy_amount_yi')}
      >
        <StatLine label="成交额" value={dzjy.amount_yi} unit=" 亿" />
        <StatLine label="溢价成交占比" value={dzjy.premium_share} pct digits={1} />
      </MiniCard>

      <MiniCard
        title="机构调研"
        icon={<SearchCheck className="w-3 h-3 text-emerald-600" />}
        date={survey.date}
        spark={sparkOf('survey', 'survey_n_stocks')}
      >
        <StatLine label="获调研家数" value={survey.n_stocks} unit=" 家" digits={0} />
        <StatLine label="接待机构数" value={survey.n_inst} unit=" 家次" digits={0} />
      </MiniCard>

      <MiniCard
        title="北向持仓（季度）"
        icon={<Compass className="w-3 h-3 text-rose-500" />}
        date={north.quarter}
        titleTip={
          northNote ||
          '2024-08-19 起北向取消盘中实时与日频买卖明细披露，仅季度持仓（延迟约 3 个月）'
        }
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[10px] text-slate-400 flex-shrink-0">增持/减持</span>
          <span className="text-[10px] font-mono">
            <span className="text-red-600 font-extrabold">{fmtNum(north.n_inc, 0)}</span>
            <span className="text-slate-300"> / </span>
            <span className="text-green-600 font-extrabold">{fmtNum(north.n_dec, 0)}</span>
            <span className="text-slate-400"> 家</span>
          </span>
        </div>
        {/* B9：增持/减持双向展示（默认各 2 行，可展开至各 5 行） */}
        {north.top_inc.length || north.top_dec.length ? (
          <div className="flex flex-col gap-0.5">
            {northRows(north.top_inc, northLimit).map((r) => (
              <div key={`inc-${r.symbol}`} className="flex items-baseline justify-between gap-1">
                <span className="text-[9px] text-slate-500 truncate">{r.stock_name || r.symbol}</span>
                <span className={`text-[9px] font-mono font-bold ${signCls(r.delta_pp)}`}>
                  {fmtSigned(r.delta_pp, 2)}pp
                </span>
              </div>
            ))}
            {northRows(north.top_dec, northLimit).map((r) => (
              <div key={`dec-${r.symbol}`} className="flex items-baseline justify-between gap-1">
                <span className="text-[9px] text-slate-500 truncate">{r.stock_name || r.symbol}</span>
                <span className={`text-[9px] font-mono font-bold ${signCls(r.delta_pp)}`}>
                  {fmtSigned(r.delta_pp, 2)}pp
                </span>
              </div>
            ))}
            {northHasMore && (
              <button
                type="button"
                onClick={() => setNorthExpanded((v) => !v)}
                className="flex items-center justify-center gap-0.5 text-[9px] font-bold text-slate-400 hover:text-purple-700 transition-colors"
              >
                <ChevronDown
                  className={`w-3 h-3 transition-transform ${northExpanded ? 'rotate-180' : ''}`}
                />
                {northExpanded ? '收起' : '增持/减持更多'}
              </button>
            )}
          </div>
        ) : (
          <span className="text-[9px] text-slate-300">季度变动 {MISSING}</span>
        )}
      </MiniCard>
    </div>
  );
};
