/** 期货市场分析 · Tab7 跨市与基差
 *
 * 布局口径（用户拍板 2026-10-15）：**左列表 + 右图表**——左列三组
 * （内外盘价差 9 组 / 贵金属期现 2 组 / 现货基差 52 品种），点选某组，
 * 右侧出该组价差率（或基差率）历史曲线（如「黄金 · 内外盘价差率 60 个交易日」）。
 *
 * 现货基差历史按需现取（/basis?symbol=），250d 时序分位为品种自身口径。
 */

import React, { useEffect, useMemo, useState } from 'react';
import type * as echarts from 'echarts';
import { ArrowLeftRight, Info } from 'lucide-react';
import { PctText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getBasis, getCrossMarket, getPreciousBasis } from '../services/api';
import type {
  BasisHistoryPoint,
  FuturesBasis,
  FuturesCrossMarket,
  FuturesPreciousBasis,
} from '../types';
import { BASIS_STATE_META, Badge, PctBar, fmt, fmtPctS, num } from '../format';
import { AXIS_LABEL, TOOLTIP_BASE, FuturesMiniChart } from './MiniChart';

const CHART_COLOR = '#d97706'; // amber-600
const LIST_MAX_H = 'max-h-[680px]';

interface DetailPoint {
  date: string;
  pct: number | null;
  lines: string[];
}

interface Detail {
  title: string;
  caption: string;
  pctLabel: string;
  points: DetailPoint[];
  chips?: React.ReactNode;
}

type Sel = { kind: 'cross' | 'precious' | 'spot'; key: string };

function GroupHeader({ label, hint, count }: { label: string; hint: string; count: number }) {
  return (
    <div className="flex items-center gap-1.5 px-1.5 pt-2 pb-1 sticky top-0 bg-white/95 backdrop-blur-sm z-[1]">
      <span className="text-[10px] font-extrabold text-slate-600">{label}</span>
      <span className="text-[9px] text-slate-400">{hint}</span>
      <span className="ml-auto text-[9px] font-mono text-slate-400">{count}</span>
    </div>
  );
}

export const FuturesSpreadPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  const [cross, setCross] = useState<FuturesCrossMarket | null>(null);
  const [precious, setPrecious] = useState<FuturesPreciousBasis | null>(null);
  const [spot, setSpot] = useState<FuturesBasis | null>(null);
  const [sel, setSel] = useState<Sel | null>(null);
  const [spotHist, setSpotHist] = useState<{ symbol: string; points: BasisHistoryPoint[] } | null>(null);
  const [spotLoading, setSpotLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([getCrossMarket(60), getPreciousBasis(60), getBasis(undefined, 120)])
      .then(([cm, pb, sb]) => {
        if (!alive) return;
        setCross(cm);
        setPrecious(pb);
        setSpot(sb);
        setFailed(false);
        // 默认选中第一个内外盘组（如「黄金 · 内外盘价差率」）
        setSel((prev) => prev ?? (cm.pairs[0] ? { kind: 'cross', key: cm.pairs[0].key } : null));
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

  // 现货基差历史按需现取（选中断面单品时）
  useEffect(() => {
    if (sel?.kind !== 'spot') return;
    if (spotHist?.symbol === sel.key) return;
    let alive = true;
    setSpotLoading(true);
    getBasis(sel.key, 60, 500)
      .then((d) => {
        if (alive && d.history) setSpotHist({ symbol: d.history.symbol, points: d.history.points });
      })
      .catch(() => {
        if (alive) setSpotHist(null);
      })
      .finally(() => {
        if (alive) setSpotLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sel, spotHist?.symbol, reloadKey]);

  const crossPairs = cross?.pairs ?? [];
  const preciousPairs = precious?.pairs ?? [];
  const spotRows = spot?.rows ?? [];

  const selectedCross = sel?.kind === 'cross' ? (crossPairs.find((p) => p.key === sel.key) ?? null) : null;
  const selectedPrecious = sel?.kind === 'precious' ? (preciousPairs.find((p) => p.key === sel.key) ?? null) : null;
  const selectedSpotRow = sel?.kind === 'spot' ? (spotRows.find((r) => r.symbol === sel.key) ?? null) : null;

  const detail: Detail | null = useMemo(() => {
    if (selectedCross) {
      return {
        title: `${selectedCross.name} · 内外盘价差率`,
        caption: `折算：${selectedCross.formula}；税制：${selectedCross.tax}。价差率 =（国内 − 折算国际）/ 折算国际，正值国内升水。`,
        pctLabel: '价差率',
        points: selectedCross.points.map((p) => ({
          date: p.date,
          pct: p.spread_pct,
          lines: [
            `国内 ${fmt(p.domestic)}（${selectedCross.domestic_unit}）`,
            `折算国际 ${fmt(p.intl_cny)}（${selectedCross.intl_unit} → 人民币）`,
            `价差 ${fmt(p.spread)}`,
          ],
        })),
      };
    }
    if (selectedPrecious) {
      const st = selectedPrecious.state ? BASIS_STATE_META[selectedPrecious.state] : null;
      return {
        title: `${selectedPrecious.name} · 期现基差率`,
        caption: `上金所现货（${selectedPrecious.spot_symbol}）收盘 vs 沪期主力连续（${selectedPrecious.fut_symbol}）收盘；基差 = 现货 − 期货，正值现货升水。两市场收盘时刻略不同，为收盘对收盘口径。`,
        pctLabel: '基差率',
        chips: st ? <Badge text={st.label} cls={st.cls} /> : null,
        points: selectedPrecious.points.map((p) => ({
          date: p.date,
          pct: p.basis_pct,
          lines: [
            `现货 ${fmt(p.spot)}（${selectedPrecious.unit}）`,
            `期货 ${fmt(p.fut)}（${selectedPrecious.unit}）`,
            `基差 ${fmt(p.basis)}`,
          ],
        })),
      };
    }
    if (sel?.kind === 'spot' && selectedSpotRow) {
      const pts = spotHist?.symbol === sel.key ? spotHist.points : [];
      return {
        title: `${selectedSpotRow.name} · 现货基差率`,
        caption:
          '基差率 =（现货 − 期货主力）/ 现货，正 = 现货升水；分位为品种自身近 250 交易日的时序分位（样本不足 60 日不出）。源：生意社现货报价（个别品种现期脱钩，分位口径稳健化）。',
        pctLabel: '基差率',
        chips: (
          <>
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">现货</span>
              <span className="font-mono text-slate-700">{fmt(selectedSpotRow.spot_price)}</span>
            </span>
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">期货主力</span>
              <span className="font-mono text-slate-700">
                {fmt(selectedSpotRow.dom_price)}
                <span className="text-[9px] text-slate-400 ml-1">{selectedSpotRow.dom_contract ?? ''}</span>
              </span>
            </span>
            <span className="flex items-center gap-1" title="主力基差率在自身近 250 交易日的时序分位">
              <span className="font-bold text-slate-400">250 日分位</span>
              <PctBar pct={selectedSpotRow.dom_pct_250} width={44} showValue />
            </span>
            <span className="text-slate-400 font-mono">样本 {selectedSpotRow.sample_days} 日</span>
          </>
        ),
        points: pts.map((p) => ({
          date: p.date,
          pct: p.dom_basis_rate_pct,
          lines: [`现货 ${fmt(p.spot_price)}`, `期货主力 ${fmt(p.dom_price)}`],
        })),
      };
    }
    return null;
  }, [selectedCross, selectedPrecious, selectedSpotRow, sel, spotHist]);

  const option = useMemo<echarts.EChartsOption | null>(() => {
    const pts = detail?.points ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 8, right: 16, top: 20, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: {
        type: 'category',
        data: pts.map((p) => p.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: {
          ...AXIS_LABEL,
          interval: (i: number) => i % Math.max(1, Math.ceil(pts.length / 7)) === 0 || i === pts.length - 1,
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: '#f1f5f9' } },
      },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          if (!pt) return '';
          return [`<b>${pt.date}</b>`, ...pt.lines, `${detail?.pctLabel}: <b>${fmtPctS(pt.pct)}</b>`].join('<br/>');
        },
      },
      series: [
        {
          name: detail?.pctLabel ?? '',
          type: 'line',
          smooth: true,
          symbol: 'none',
          connectNulls: false,
          data: pts.map((p) => num(p.pct)),
          lineStyle: { width: 2, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          areaStyle: { opacity: 0.07, color: CHART_COLOR },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#cbd5e1', type: 'dashed' },
            label: { formatter: '0', fontSize: 9, color: '#94a3b8' },
            data: [{ yAxis: 0 }],
          },
        },
      ],
    };
  }, [detail]);

  const fx = cross?.fx;
  const dataDate = cross?.trade_date || precious?.trade_date || spot?.as_of || '';

  const rowCls = (active: boolean) =>
    `w-full flex items-center gap-2 px-1.5 py-1.5 rounded-lg border-b border-slate-50 last:border-0 text-left transition-colors cursor-pointer ${
      active ? 'bg-amber-50/80' : 'hover:bg-slate-50/70'
    }`;

  return (
    <div className="flex flex-col gap-2.5">
      {/* 汇率与口径条 */}
      <div className="bg-white/90 backdrop-blur-md rounded-2xl px-3.5 py-2 border border-amber-200/50 shadow-sm flex items-center gap-4 flex-wrap text-[11px]">
        <span className="flex items-center gap-1.5">
          <ArrowLeftRight className="w-3.5 h-3.5 text-amber-600" />
          <span className="font-extrabold text-slate-600">USD/CNY 折算汇率</span>
          <span className="font-mono font-extrabold text-slate-800">{fmt(fx?.rate, 4)}</span>
          {fx?.date && <span className="text-[10px] font-mono text-slate-400">（{fx.date}）</span>}
        </span>
        <span className="text-[10px] text-slate-400 leading-snug">
          国际价为美元报价，先按当日中行汇率折人民币（逐日对齐、缺失日跳过该点），再按单位换算到与国内同一量纲；有色按进口到岸口径加 13% 增值税。
        </span>
        <DateBadge label="数据日期" date={dataDate} />
      </div>

      {/* 左列表 + 右图表（用户拍板布局） */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-2.5 items-start">
        <SectionCard
          className="xl:col-span-2 !gap-1"
          title={
            <span className="flex items-center gap-1.5">
              价差列表
              <span className="text-[9px] font-normal text-slate-400">点选看右侧历史曲线</span>
            </span>
          }
          extra={
            <span className="text-[10px] font-mono text-slate-400">
              {crossPairs.length + preciousPairs.length + spotRows.length} 组
            </span>
          }
        >
          {loading && crossPairs.length === 0 ? (
            <EmptyHint loading text="加载中" />
          ) : failed && crossPairs.length === 0 ? (
            <EmptyHint text="加载失败" />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              <GroupHeader label="内外盘价差" hint="国际价折算人民币" count={crossPairs.length} />
              {crossPairs.map((p) => {
                const active = sel?.kind === 'cross' && sel.key === p.key;
                return (
                  <button
                    key={p.key}
                    onClick={() => setSel({ kind: 'cross', key: p.key })}
                    title={`${p.domestic_name}（${p.domestic_symbol}）vs ${p.intl_name}（${p.intl_symbol}）；${p.tax}`}
                    className={rowCls(active)}
                  >
                    <span className="flex-1 min-w-0 flex items-center gap-1.5">
                      <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{p.name}</span>
                      <span className="text-[9px] font-mono text-slate-400 truncate">{p.sector_name}</span>
                    </span>
                    <PctText value={p.spread_pct} className="text-[11px]" />
                  </button>
                );
              })}

              <GroupHeader label="贵金属期现" hint="上金所现货 vs 沪期主力" count={preciousPairs.length} />
              {preciousPairs.map((p) => {
                const active = sel?.kind === 'precious' && sel.key === p.key;
                const st = p.state ? BASIS_STATE_META[p.state] : null;
                return (
                  <button
                    key={p.key}
                    onClick={() => setSel({ kind: 'precious', key: p.key })}
                    title={`${p.spot_name}（${p.spot_symbol}）vs ${p.fut_name}（${p.fut_symbol}）`}
                    className={rowCls(active)}
                  >
                    <span className="flex-1 min-w-0 flex items-center gap-1.5">
                      <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{p.name}</span>
                      {st && <Badge text={st.label} cls={st.cls} />}
                    </span>
                    <PctText value={p.basis_pct} className="text-[11px]" />
                  </button>
                );
              })}

              <GroupHeader label="现货基差 · 生意社" hint="现货 vs 期货主力，点选看 2 年历史" count={spotRows.length} />
              {spotRows.length === 0 && <EmptyHint text="现货基差数据不可用" />}
              {spotRows.map((r) => {
                const active = sel?.kind === 'spot' && sel.key === r.symbol;
                return (
                  <button
                    key={r.symbol}
                    onClick={() => setSel({ kind: 'spot', key: r.symbol })}
                    title={`${r.name} ${r.symbol} · as-of ${r.as_of} · 现货 ${fmt(r.spot_price)} / 主力 ${fmt(r.dom_price)}（${r.dom_contract ?? '—'}） · 250 日分位 ${fmt(r.dom_pct_250, 1)}`}
                    className={rowCls(active)}
                  >
                    <span className="flex-1 min-w-0 flex items-center gap-1.5">
                      <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{r.name}</span>
                      <span className="text-[9px] font-mono text-slate-400">{r.symbol}</span>
                      {!r.is_latest && (
                        <span className="text-[9px] font-bold text-amber-600 whitespace-nowrap" title={`as-of ${r.as_of}（未及全局最新日）`}>
                          {r.as_of}
                        </span>
                      )}
                    </span>
                    <span className="w-12 flex justify-end">
                      <PctBar pct={r.dom_pct_250} width={34} />
                    </span>
                    <PctText value={r.dom_basis_rate_pct} className="text-[11px] w-14 text-right" />
                  </button>
                );
              })}
            </div>
          )}
        </SectionCard>

        <SectionCard
          className="xl:col-span-3 !gap-2"
          title={
            <span className="flex items-center gap-1.5">
              {detail ? detail.title : '价差历史'}
              {detail && detail.points.length > 0 && (
                <span className="text-[10px] font-bold text-slate-500">
                  {detail.points.length} 个交易日
                </span>
              )}
              {detail?.chips}
            </span>
          }
          extra={detail ? <DateBadge label="最新" date={detail.points[detail.points.length - 1]?.date} /> : null}
        >
          {!detail || detail.points.length === 0 ? (
            <EmptyHint
              loading={loading || spotLoading}
              text={sel?.kind === 'spot' ? '该品种现货基差历史缺失' : '点击左侧任一组查看历史曲线'}
            />
          ) : (
            <>
              <FuturesMiniChart option={option} height={460} />
              <div className="flex items-center gap-1.5 text-[10px] text-slate-400 leading-snug">
                <Info className="w-3 h-3 flex-shrink-0" />
                <span>{detail.caption}</span>
              </div>
            </>
          )}
        </SectionCard>
      </div>
    </div>
  );
};
