/** 期货市场分析 · Tab4 持仓分析
 *
 * CFTC 国际持仓（投机 vs 商业套保，周度）+ 国内 N 日增减仓四象限
 * （涨+增仓=多头增仓 / 跌+增仓=空头增仓 / 涨+减仓=空头回补 / 跌+减仓=多头离场）。
 * 排行按 |持仓额变化| 排序——不同品种手数不可比。
 */

import React, { useEffect, useState } from 'react';
import { Scale, Info } from 'lucide-react';
import { PctText, NumText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getCftc, getOiChanges } from '../services/api';
import type { FuturesCftc, FuturesOiChanges, OiQuadrant } from '../types';
import { Badge, QUADRANT_META, fmt } from '../format';

const DAY_OPTIONS = [1, 3, 5, 10];

const QUADRANT_ORDER: OiQuadrant[] = ['build_long', 'build_short', 'short_covering', 'long_unwind'];

function CftcColumn({
  title,
  hint,
  block,
}: {
  title: string;
  hint: string;
  block: FuturesCftc['speculative'] | undefined;
}) {
  const rows = block?.rows ?? [];
  return (
    <div className="flex flex-col gap-1" title={hint}>
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-extrabold text-slate-700">{title}</span>
        <span className="text-[10px] font-mono text-slate-400 ml-auto whitespace-nowrap">
          {block?.report_date ? `截至 ${block.report_date}` : '—'}
        </span>
      </div>
      <div className="flex items-center gap-2 px-1 text-[9px] font-bold text-slate-400">
        <span className="flex-1">品种</span>
        <span className="w-16 text-right">周变化</span>
        <span className="w-20 text-right">净仓位（张）</span>
        <span className="w-24 text-right hidden md:block">多 / 空</span>
      </div>
      {rows.length === 0 ? (
        <EmptyHint text="CFTC 数据缺失" />
      ) : (
        rows.map((r) => (
          <div
            key={r.item}
            className="flex items-center gap-2 px-1 py-1 border-b border-slate-50 last:border-0 text-[11px] hover:bg-slate-50/70"
          >
            <span className="flex-1 font-bold text-slate-700 truncate" title={r.item}>
              {r.item}
            </span>
            <span className="w-16 text-right whitespace-nowrap">
              <NumText value={r.net_change} digits={0} />
            </span>
            <span className="w-20 text-right font-mono font-extrabold text-slate-700 whitespace-nowrap">
              {fmt(r.net, 0)}
            </span>
            <span className="w-24 text-right font-mono text-[10px] text-slate-400 hidden md:block whitespace-nowrap">
              {fmt(r.long, 0)} / {fmt(r.short, 0)}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

export const FuturesPositionsPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  const [cftc, setCftc] = useState<FuturesCftc | null>(null);
  const [oi, setOi] = useState<FuturesOiChanges | null>(null);
  const [days, setDays] = useState(1);
  const [quadFilter, setQuadFilter] = useState<OiQuadrant | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getCftc(15)
      .then((d) => {
        if (alive) setCftc(d);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getOiChanges(days, 30)
      .then((d) => {
        if (alive) {
          setOi(d);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [days, reloadKey]);

  const rows = oi?.rows ?? [];
  const filtered = quadFilter ? rows.filter((r) => r.quadrant === quadFilter) : rows;

  return (
    <div className="flex flex-col gap-2.5">
      {/* CFTC 双榜 */}
      <SectionCard
        className="!gap-2.5"
        title={
          <span className="flex items-center gap-1.5">
            <Scale className="w-3.5 h-3.5 text-amber-600" />
            CFTC 国际持仓（投机 vs 商业套保）
            <span className="text-[9px] font-normal text-slate-400">周度，每周六发布上一周二数据；净仓位 = 多头 − 空头（张）</span>
          </span>
        }
        extra={
          <DateBadge
            label="报告期"
            date={cftc?.speculative?.report_date ?? cftc?.commercial?.report_date}
          />
        }
      >
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-3">
          <CftcColumn
            title="投机仓（非商业）"
            hint="非商业持仓：基金等投机资金，净多头增持通常伴随趋势行情"
            block={cftc?.speculative}
          />
          <CftcColumn
            title="商业仓（套保）"
            hint="商业持仓：产业套保盘，与投机盘通常是净仓位的对手方"
            block={cftc?.commercial}
          />
        </div>
      </SectionCard>

      {/* 国内增减仓四象限 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            增减仓四象限（国内品种）
            <span className="text-[9px] font-normal text-slate-400">
              按 |持仓额变化| 排序；点击象限可筛选
            </span>
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <DateBadge label="基准" date={oi?.base_date} />
            <DateBadge label="最新" date={oi?.trade_date} />
            <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
              {DAY_OPTIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold transition-all ${
                    days === d
                      ? 'bg-white text-amber-700 shadow-2xs border border-amber-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {d}日
                </button>
              ))}
            </div>
          </div>
        }
      >
        {/* 象限计数 chips（可筛选） */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {QUADRANT_ORDER.map((q) => {
            const meta = QUADRANT_META[q];
            const count = oi?.quadrants?.[q] ?? 0;
            const active = quadFilter === q;
            return (
              <button
                key={q}
                onClick={() => setQuadFilter(active ? null : q)}
                title={`${meta.hint}（再点一次取消筛选）`}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] font-extrabold transition-all cursor-pointer ${meta.cls} ${
                  active ? 'ring-2 ring-amber-400/70 shadow-2xs' : 'opacity-90 hover:opacity-100'
                }`}
              >
                <span>{meta.label}</span>
                <span className="font-mono">{count}</span>
              </button>
            );
          })}
          {quadFilter && (
            <button
              onClick={() => setQuadFilter(null)}
              className="text-[10px] text-slate-400 hover:text-slate-600 font-bold px-1"
            >
              清除筛选
            </button>
          )}
          <span className="ml-auto text-[9px] text-slate-400 font-mono hidden md:inline">
            四象限口径：涨+增仓=多头增仓 / 跌+增仓=空头增仓 / 涨+减仓=空头回补 / 跌+减仓=多头离场
          </span>
        </div>

        {filtered.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
        ) : (
          <div className="flex flex-col">
            <div className="flex items-center gap-2 px-1 text-[9px] font-bold text-slate-400">
              <span className="w-5" />
              <span className="flex-1">品种</span>
              <span className="w-14 text-right">涨跌幅</span>
              <span className="w-20 text-right">持仓变化（万手）</span>
              <span className="w-16 text-right hidden md:block">持仓变化率</span>
              <span className="w-20 text-right hidden lg:block">持仓额（亿）</span>
              <span className="w-20 text-right">持仓额变化（亿）</span>
              <span className="w-16 text-center">象限</span>
            </div>
            {filtered.map((r) => {
              const meta = r.quadrant ? QUADRANT_META[r.quadrant] : null;
              return (
                <div
                  key={r.symbol}
                  className="flex items-center gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 text-[11px] hover:bg-slate-50/70"
                  title={`${r.name} ${r.symbol} · ${r.sector_name}`}
                >
                  <span className="w-5" />
                  <span className="flex-1 min-w-0 flex items-center gap-1.5">
                    <span className="font-bold text-slate-800 whitespace-nowrap">{r.name}</span>
                    <span className="text-[10px] font-mono text-slate-400 truncate">{r.symbol}</span>
                  </span>
                  <span className="w-14 text-right">
                    <PctText value={r.pct_change_n} className="text-[11px]" />
                  </span>
                  <span className="w-20 text-right">
                    <NumText value={r.oi_change_wan} suffix="万手" className="text-[11px]" />
                  </span>
                  <span className="w-16 text-right hidden md:block">
                    <PctText value={r.oi_pct_n} className="text-[11px]" />
                  </span>
                  <span className="w-20 text-right font-mono text-slate-600 hidden lg:block whitespace-nowrap">
                    {fmt(r.oi_amount_yi)}
                  </span>
                  <span className="w-20 text-right">
                    <NumText value={r.oi_amount_change_yi} className="text-[11px]" />
                  </span>
                  <span className="w-16 text-center">
                    {meta && <Badge text={meta.label} cls={meta.cls} title={meta.hint} />}
                  </span>
                </div>
              );
            })}
            <div className="flex items-center gap-1.5 text-[9px] text-slate-400 pt-1.5 leading-snug">
              <Info className="w-3 h-3 flex-shrink-0" />
              <span>
                持仓变化 = 最新完整交易日 − {days} 个交易日前；持仓额变化 = 变化手数 × 最新价 × 合约乘数（不同品种手数不可比，故按金额排序）。
              </span>
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
};
