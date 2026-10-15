/** 期货市场分析 · Tab5 持仓分析
 *
 * 布局口径（用户拍板 2026-10-15）：
 * - 左列 = CFTC 双面（投机仓·非商业 / 商业仓·套保），**点击切换**同一图位；
 * - 右列 = 增减仓四象限（国内品种）；两框**等高对齐**（同行 grid + 内容限高内滚）。
 * - 下方 = 会员席位龙虎榜（新浪口径，SHFE/CZCE 33 品种；左净多榜 + 右三榜明细）。
 *
 * 四象限排序按 |持仓额变化|——不同品种手数不可比。
 */

import React, { useEffect, useMemo, useState } from 'react';
import type * as echarts from 'echarts';
import { Scale, Info, Users } from 'lucide-react';
import { PctText, NumText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getCftc, getMembers, getOiChanges } from '../services/api';
import type { FuturesCftc, FuturesMembers, FuturesOiChanges, MemberRow, OiQuadrant } from '../types';
import { Badge, QUADRANT_META, fmt } from '../format';
import { AXIS_LABEL, TOOLTIP_BASE, FuturesMiniChart } from './MiniChart';

const DAY_OPTIONS = [1, 3, 5, 10];
const QUADRANT_ORDER: OiQuadrant[] = ['build_long', 'build_short', 'short_covering', 'long_unwind'];
/** 两框对齐：内容限高内滚，卡片随行等高 */
const BOX_MAX_H = 'max-h-[460px]';

type CftcSide = 'speculative' | 'commercial';

const CFTC_SIDES: Array<{ id: CftcSide; label: string; hint: string }> = [
  {
    id: 'speculative',
    label: '投机仓（非商业）',
    hint: '非商业持仓：基金等投机资金；净多头增持通常伴随趋势行情',
  },
  {
    id: 'commercial',
    label: '商业仓（套保）',
    hint: '商业持仓：产业套保盘，与投机盘通常是净仓位的对手方',
  },
];

function CftcTable({ block }: { block: FuturesCftc['speculative'] | undefined }) {
  const rows = block?.rows ?? [];
  if (rows.length === 0) return <EmptyHint text="CFTC 数据缺失" />;
  return (
    <div className={`flex flex-col ${BOX_MAX_H} overflow-y-auto pr-0.5`}>
      <div className="flex items-center gap-2 px-1 text-[9px] font-bold text-slate-400 sticky top-0 bg-white/95 backdrop-blur-sm py-0.5">
        <span className="flex-1">品种</span>
        <span className="w-16 text-right">周变化</span>
        <span className="w-20 text-right">净仓位（张）</span>
        <span className="w-24 text-right hidden md:block">多 / 空</span>
        <span className="w-20 text-right hidden lg:block">上期净仓</span>
      </div>
      {rows.map((r) => (
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
          <span className="w-20 text-right font-mono text-[10px] text-slate-400 hidden lg:block whitespace-nowrap">
            {fmt(r.net_prev, 0)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 席位龙虎榜：左右两卡（净多榜 + 选中品种明细） */
function MembersSection({ reloadKey }: { reloadKey: number }) {
  const [list, setList] = useState<FuturesMembers | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [detail, setDetail] = useState<FuturesMembers | null>(null);
  const [side, setSide] = useState<'long' | 'short' | 'volume'>('long');
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getMembers()
      .then((d) => {
        if (alive) {
          setList(d);
          setFailed(false);
          setSymbol((prev) => prev ?? (d.rows.some((r) => r.symbol === 'RB') ? 'RB' : (d.rows[0]?.symbol ?? null)));
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
  }, [reloadKey]);

  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    getMembers(symbol)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch(() => {
        if (alive) setDetail(null);
      });
    return () => {
      alive = false;
    };
  }, [symbol, reloadKey]);

  const rows: MemberRow[] = list?.rows ?? [];
  const current = rows.find((r) => r.symbol === symbol);
  const board = detail?.board ?? null;
  const seatLabels = detail?.side_labels ?? {};
  const sideLabel = (s: 'long' | 'short' | 'volume') =>
    seatLabels[s] ?? (s === 'long' ? '多单' : s === 'short' ? '空单' : '成交');

  const trendOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = detail?.net_trend ?? [];
    if (pts.length < 2) return null;
    return {
      grid: { left: 8, right: 8, top: 10, bottom: 2, containLabel: true },
      animationDuration: 300,
      xAxis: {
        type: 'category',
        data: pts.map((p) => p.date),
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: { ...AXIS_LABEL, hideOverlap: true },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => fmt(v, 0) },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          return pt ? `<b>${pt.date}</b><br/>前 20 席位净多: <b>${fmt(pt.net_wan)} 万手</b>` : '';
        },
      },
      series: [
        {
          type: 'line',
          symbolSize: 5,
          data: pts.map((p) => p.net_wan),
          lineStyle: { width: 2, color: '#d97706' },
          itemStyle: { color: '#d97706' },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#cbd5e1', type: 'dashed' },
            label: { show: false },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    };
  }, [detail]);

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-stretch">
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5 text-amber-600" />
            席位净多榜
            <span className="text-[9px] font-normal text-slate-400">前 20 名多−空 · 点击看明细</span>
          </span>
        }
        extra={<DateBadge label="数据日期" date={list?.as_of} />}
      >
        {rows.length === 0 ? (
          <EmptyHint loading={loading} text={failed ? '加载失败' : '席位数据不可用'} />
        ) : (
          <div className={`flex flex-col ${BOX_MAX_H} overflow-y-auto pr-0.5`}>
            {rows.map((r, i) => {
              const active = r.symbol === symbol;
              return (
                <button
                  key={`${r.venue}-${r.symbol}`}
                  onClick={() => setSymbol(r.symbol)}
                  title={`${r.venue ?? ''} ${r.contract ?? ''} · 多 ${fmt(r.long_wan)} / 空 ${fmt(r.short_wan)} 万手 · 前五集中度 多 ${fmt(r.top5_long_share_pct, 1)}% 空 ${fmt(r.top5_short_share_pct, 1)}%`}
                  className={`flex items-center gap-2 px-1.5 py-1.5 rounded-lg border-b border-slate-50 last:border-0 text-left transition-colors cursor-pointer ${
                    active ? 'bg-amber-50/80' : 'hover:bg-slate-50/70'
                  }`}
                >
                  <span
                    className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold flex-shrink-0 ${
                      i < 3 ? 'bg-amber-600 text-white' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex flex-col leading-tight">
                    <span className="text-xs font-bold text-slate-800 whitespace-nowrap">
                      {r.name} <span className="text-[9px] font-mono text-slate-400">{r.symbol}</span>
                    </span>
                    <span className="text-[9px] font-mono text-slate-400">
                      {r.venue ?? '—'} {r.contract ?? ''}
                    </span>
                  </span>
                  <span className="ml-auto flex flex-col items-end leading-tight flex-shrink-0">
                    <span className="text-[11px] font-extrabold font-mono tabular-nums">
                      <NumText value={r.net_wan} suffix="万手" className="text-[11px]" />
                    </span>
                    <span className="text-[9px] font-mono">
                      <NumText value={r.net_delta_wan} suffix="" className="text-[9px]" />
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
        <div className="text-[9px] text-slate-400 leading-snug">
          新浪席位口径（会员前 20 名，日频），仅覆盖 SHFE / CZCE；DCE / CFFEX / INE 无免费数据源。
          样本自 {list?.as_of ? `${list.sample_days} 个交易日` : '—'}，短样本不做历史分位。
        </div>
      </SectionCard>

      <SectionCard
        className="xl:col-span-2 !gap-2"
        title={
          <span className="flex items-center gap-1.5">
            席位明细
            {current && (
              <span className="text-[10px] font-bold text-slate-500">
                {current.name}
                <span className="font-mono text-slate-400 ml-1">
                  {current.venue} {current.contract}
                </span>
              </span>
            )}
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
              {(['long', 'short', 'volume'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setSide(s)}
                  className={`px-3 py-1 rounded-full text-[10px] font-extrabold transition-all cursor-pointer ${
                    side === s
                      ? 'bg-white text-amber-700 shadow-2xs border border-amber-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {sideLabel(s)}
                </button>
              ))}
            </div>
            <DateBadge label="数据日期" date={detail?.as_of} />
          </div>
        }
      >
        {!current ? (
          <EmptyHint loading={loading} text="选择左侧品种查看席位明细" />
        ) : (
          <>
            <div className="flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">净多合计</span>
                <NumText value={current.net_wan} suffix="万手" className="text-[11px]" />
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">日变化</span>
                <NumText value={current.net_delta_wan} suffix="万手" className="text-[11px]" />
              </span>
              <span className="flex items-baseline gap-1" title="前五名席位的多/空持仓集中度">
                <span className="font-bold text-slate-400">前五集中度</span>
                <span className="font-mono text-slate-600">
                  多 {fmt(current.top5_long_share_pct, 1)}% / 空 {fmt(current.top5_short_share_pct, 1)}%
                </span>
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">席位数</span>
                <span className="font-mono text-slate-600">
                  {current.n_long} 多 / {current.n_short} 空
                </span>
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 items-start">
              <div className={`flex flex-col ${BOX_MAX_H} overflow-y-auto pr-0.5`} key={side}>
                <div className="flex items-center gap-2 px-1 text-[9px] font-bold text-slate-400 sticky top-0 bg-white/95 backdrop-blur-sm py-0.5">
                  <span className="w-5">#</span>
                  <span className="flex-1">会员</span>
                  <span className="w-20 text-right">持仓（万手）</span>
                  <span className="w-20 text-right">日变化</span>
                </div>
                {(board?.[side] ?? []).map((seat) => (
                  <div
                    key={`${side}-${seat.rank}-${seat.member}`}
                    className="flex items-center gap-2 px-1 py-1 border-b border-slate-50 last:border-0 text-[11px] hover:bg-slate-50/70"
                  >
                    <span className="w-5 font-mono text-slate-400">{seat.rank}</span>
                    <span className="flex-1 font-bold text-slate-700 truncate" title={seat.member}>
                      {seat.member}
                    </span>
                    <span className="w-20 text-right font-mono text-slate-600 tabular-nums">
                      {fmt(seat.size_wan, 2)}
                    </span>
                    <span className="w-20 text-right">
                      <NumText value={seat.delta_wan} digits={2} className="text-[10px]" />
                    </span>
                  </div>
                ))}
                {(board?.[side] ?? []).length === 0 && <EmptyHint text="该侧席位榜缺失" />}
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[10px] font-bold text-slate-400">前 20 席位净多（万手）逐日趋势</span>
                <FuturesMiniChart option={trendOption} height={200} emptyText="净多趋势样本不足（日频约一周）" />
              </div>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
}

export const FuturesPositionsPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  const [cftc, setCftc] = useState<FuturesCftc | null>(null);
  const [cftcSide, setCftcSide] = useState<CftcSide>('speculative');
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
  const cftcBlock = cftc?.[cftcSide];
  const cftcMeta = CFTC_SIDES.find((s) => s.id === cftcSide);

  return (
    <div className="flex flex-col gap-2.5">
      {/* 左右双框（等高对齐）：左 CFTC 点击切换 / 右增减仓四象限 */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5 items-stretch">
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Scale className="w-3.5 h-3.5 text-amber-600" />
              CFTC 国际持仓
              <span className="text-[9px] font-normal text-slate-400">周度，净仓位 = 多头 − 空头（张）</span>
            </span>
          }
          extra={
            <div className="flex items-center gap-2">
              <DateBadge label="报告期" date={cftcBlock?.report_date} />
              <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
                {CFTC_SIDES.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setCftcSide(s.id)}
                    title={s.hint}
                    className={`px-3 py-1 rounded-full text-[10px] font-extrabold transition-all cursor-pointer ${
                      cftcSide === s.id
                        ? 'bg-white text-amber-700 shadow-2xs border border-amber-200'
                        : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          }
        >
          <div className="text-[10px] text-slate-400 leading-snug">{cftcMeta?.hint}</div>
          <CftcTable block={cftcBlock} />
          <div className="text-[9px] text-slate-400 leading-snug">
            上期报告期：{cftcBlock?.prev_date ?? '—'}；点击右上角切换「投机仓（非商业）/ 商业仓（套保）」。
          </div>
        </SectionCard>

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
                    className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold transition-all cursor-pointer ${
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
                className="text-[10px] text-slate-400 hover:text-slate-600 font-bold px-1 cursor-pointer"
              >
                清除筛选
              </button>
            )}
          </div>

          {filtered.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
          ) : (
            <div className={`flex flex-col ${BOX_MAX_H} overflow-y-auto pr-0.5`}>
              <div className="flex items-center gap-2 px-1 text-[9px] font-bold text-slate-400 sticky top-0 bg-white/95 backdrop-blur-sm py-0.5">
                <span className="flex-1">品种</span>
                <span className="w-14 text-right">涨跌幅</span>
                <span className="w-20 text-right">持仓变化（万手）</span>
                <span className="w-16 text-right hidden md:block">持仓变化率</span>
                <span className="w-20 text-right">持仓额变化（亿）</span>
                <span className="w-16 text-center">象限</span>
              </div>
              {filtered.map((r) => {
                const meta = r.quadrant ? QUADRANT_META[r.quadrant] : null;
                return (
                  <div
                    key={r.symbol}
                    className="flex items-center gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 text-[11px] hover:bg-slate-50/70"
                    title={`${r.name} ${r.symbol} · ${r.sector_name} · 持仓额 ${fmt(r.oi_amount_yi)} 亿`}
                  >
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
                    <span className="w-20 text-right">
                      <NumText value={r.oi_amount_change_yi} className="text-[11px]" />
                    </span>
                    <span className="w-16 text-center">
                      {meta && <Badge text={meta.label} cls={meta.cls} title={meta.hint} />}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          <div className="flex items-center gap-1.5 text-[9px] text-slate-400 leading-snug">
            <Info className="w-3 h-3 flex-shrink-0" />
            <span>
              持仓变化 = 最新完整交易日 − {days} 个交易日前；持仓额变化 = 变化手数 × 最新价 × 合约乘数
              （不同品种手数不可比，故按金额排序）。
            </span>
          </div>
        </SectionCard>
      </div>

      {/* 会员席位龙虎榜 */}
      <MembersSection reloadKey={reloadKey} />
    </div>
  );
};
