/** 期货市场分析 · Tab6 仓单库存
 *
 * 两对「左榜右图」（列表均默认选中第一个，右侧图表不空置等待点击）：
 * - 东财库存主榜（59 品种：5/20 日变化、全历史分位、累/去库周期徽标、
 *   品种 as-of 停更显形）+ 单品种库存历史；
 * - 交易所注册仓单对照（CZCE 覆盖）+ 单品种仓单历史。
 *
 * 口径：东财库存单位随品种（只做相对分位与变化，不做跨品种绝对值比较）；
 * 仓单为交易所官方显性库存口径，缺数据不落 0。
 */

import React, { useEffect, useMemo, useState } from 'react';
import type * as echarts from 'echarts';
import { Package, Warehouse, Info, AlertTriangle } from 'lucide-react';
import { NumText, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getInventoryEm, getReceipts } from '../services/api';
import type { FuturesInventoryEm, FuturesReceipts } from '../types';
import { Badge, CYCLE_META, PctBar, fmt, num } from '../format';
import { AXIS_LABEL, TOOLTIP_BASE, FuturesMiniChart } from './MiniChart';

const LIST_MAX_H = 'max-h-[440px]';
const CHART_H = 250;

/** 库存历史线（东财口径） */
function InventoryHistoryCard({ detail, loading }: { detail: FuturesInventoryEm | null; loading: boolean }) {
  const pts = detail?.history?.points ?? [];
  const option = useMemo<echarts.EChartsOption | null>(() => {
    if (pts.length === 0) return null;
    return {
      grid: { left: 8, right: 14, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: {
        type: 'category',
        data: pts.map((p) => p.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: {
          ...AXIS_LABEL,
          interval: (i: number) => i % Math.max(1, Math.ceil(pts.length / 6)) === 0 || i === pts.length - 1,
        },
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
          return pt ? `<b>${pt.date}</b><br/>库存: <b>${fmt(pt.inventory)}</b><br/>环比: ${fmt(pt.delta)}` : '';
        },
      },
      series: [
        {
          type: 'line',
          symbol: 'none',
          connectNulls: false,
          data: pts.map((p) => num(p.inventory)),
          lineStyle: { width: 2, color: '#d97706' },
          itemStyle: { color: '#d97706' },
          areaStyle: { opacity: 0.08, color: '#d97706' },
        },
      ],
    };
  }, [pts]);

  const row = detail?.rows?.find((r) => r.symbol === detail?.history?.symbol);
  const cycle = row?.cycle ? CYCLE_META[row.cycle] : null;

  return (
    <SectionCard
      className="!gap-2"
      title={
        <span className="flex items-center gap-1.5">
          <Warehouse className="w-3.5 h-3.5 text-amber-600" />
          库存历史（东财口径）
          {detail?.history && (
            <span className="text-[10px] font-bold text-slate-500">
              {detail.history.name}
              <span className="font-mono text-slate-400 ml-1">{detail.history.symbol}</span>
            </span>
          )}
          {cycle && <Badge text={cycle.label} cls={cycle.cls} title={cycle.hint} />}
        </span>
      }
      extra={<DateBadge label="最新" date={detail?.as_of} />}
    >
      {!detail?.history ? (
        <EmptyHint loading={loading} text="选择左侧品种查看库存历史" />
      ) : pts.length === 0 ? (
        <EmptyHint text="该品种暂无库存历史" />
      ) : (
        <>
          {row && (
            <div className="flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">库存</span>
                <span className="text-[13px] font-extrabold font-mono text-slate-800">
                  {fmt(row.inventory)}
                </span>
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">日变化</span>
                <NumText value={row.delta_1d} digits={0} className="text-[11px]" />
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">5 日</span>
                <span className="font-mono text-slate-600">{fmt(row.chg_5d_pct)}%</span>
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">20 日</span>
                <span className="font-mono text-slate-600">{fmt(row.chg_20d_pct)}%</span>
              </span>
              <span className="flex items-center gap-1" title="当前库存在该品种自身全历史的分位">
                <span className="font-bold text-slate-400">历史分位</span>
                <PctBar pct={row.pct_hist} width={44} showValue />
              </span>
              {row.out_of_sync && (
                <span className="flex items-center gap-1 text-amber-600 font-bold">
                  <AlertTriangle className="w-3 h-3" />
                  源侧停更（{row.as_of}）
                </span>
              )}
            </div>
          )}
          <FuturesMiniChart option={option} height={CHART_H} />
          <div className="text-[9px] text-slate-400 leading-snug">
            东财库存数据（{pts.length} 个交易日）；单位随品种口径不同，仅做自身分位与变化，不做跨品种绝对值比较。
          </div>
        </>
      )}
    </SectionCard>
  );
}

export const FuturesInventoryPanel: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
  // 东财库存主榜
  const [emList, setEmList] = useState<FuturesInventoryEm | null>(null);
  const [emSymbol, setEmSymbol] = useState<string | null>(null);
  const [emDetail, setEmDetail] = useState<FuturesInventoryEm | null>(null);
  // 注册仓单对照
  const [receipts, setReceipts] = useState<FuturesReceipts | null>(null);
  const [variety, setVariety] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([getInventoryEm(), getReceipts(undefined, 90)])
      .then(([em, rc]) => {
        if (!alive) return;
        setEmList(em);
        setReceipts(rc);
        setFailed(false);
        // 默认选中第一个（用户口径：右侧图表不空置等待点击）
        setEmSymbol((prev) => prev ?? (em.rows[0]?.symbol ?? null));
        setVariety((prev) => prev ?? (rc.rows[0]?.variety ?? null));
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
    if (!emSymbol) return;
    let alive = true;
    getInventoryEm(emSymbol)
      .then((d) => {
        if (alive) setEmDetail(d);
      })
      .catch(() => {
        if (alive) setEmDetail(null);
      });
    return () => {
      alive = false;
    };
  }, [emSymbol, reloadKey]);

  useEffect(() => {
    if (!variety) return;
    let alive = true;
    getReceipts(variety, 90)
      .then((d) => {
        if (alive) setReceipts((prev) => (prev ? { ...prev, history: d.history } : d));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [variety, reloadKey]);

  const emRows = emList?.rows ?? [];
  const receiptRows = receipts?.rows ?? [];
  const receiptHistory = receipts?.history ?? null;
  const receiptPoints = receiptHistory?.points ?? [];

  const receiptOption = useMemo<echarts.EChartsOption | null>(() => {
    if (receiptPoints.length === 0) return null;
    return {
      grid: { left: 8, right: 14, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: {
        type: 'category',
        data: receiptPoints.map((p) => p.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: {
          ...AXIS_LABEL,
          interval: (i: number) =>
            i % Math.max(1, Math.ceil(receiptPoints.length / 6)) === 0 || i === receiptPoints.length - 1,
        },
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
          const pt = receiptPoints[p?.dataIndex];
          if (!pt) return '';
          return `<b>${pt.date}</b><br/>仓单量: <b>${fmt(pt.receipt_qty_wan)} 万张</b><br/>主要交易所: ${pt.venue}`;
        },
      },
      series: [
        {
          type: 'line',
          smooth: true,
          symbol: 'none',
          connectNulls: false,
          data: receiptPoints.map((p) => num(p.receipt_qty_wan)),
          lineStyle: { width: 2, color: '#d97706' },
          itemStyle: { color: '#d97706' },
          areaStyle: { opacity: 0.08, color: '#d97706' },
        },
      ],
    };
  }, [receiptPoints]);

  return (
    <div className="flex flex-col gap-2.5">
      {/* 行 1：东财库存主榜 + 库存历史 */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5 items-stretch">
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Warehouse className="w-3.5 h-3.5 text-amber-600" />
              库存主榜（东财口径）
              <span className="text-[9px] font-normal text-slate-400">默认选中第一行，点击切换</span>
            </span>
          }
          extra={
            <div className="flex items-center gap-2">
              <DateBadge label="数据日期" date={emList?.as_of} />
              <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">
                {emList?.variety_count ?? '—'} 品种
                {typeof emList?.fresh_count === 'number' && emList.fresh_count < emList.variety_count
                  ? `（${emList.variety_count - emList.fresh_count} 停更）`
                  : ''}
              </span>
            </div>
          }
        >
          {emRows.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              <div className="flex items-center gap-2 px-1.5 text-[9px] font-bold text-slate-400 sticky top-0 bg-white/95 backdrop-blur-sm py-0.5">
                <span className="w-5" />
                <span className="flex-1">品种</span>
                <span className="w-16 text-right">库存</span>
                <span className="w-14 text-right">20 日</span>
                <span className="w-16 text-right">分位</span>
                <span className="w-12 text-center">周期</span>
              </div>
              {emRows.map((r, i) => {
                const active = emSymbol === r.symbol;
                const cycle = r.cycle ? CYCLE_META[r.cycle] : null;
                return (
                  <button
                    key={r.symbol}
                    onClick={() => setEmSymbol(r.symbol)}
                    title={`${r.name} ${r.symbol} · as-of ${r.as_of}${r.out_of_sync ? '（源侧停更）' : ''} · 5 日变化 ${fmt(r.chg_5d_pct)}% · 日变化 ${fmt(r.delta_1d)}`}
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
                    <span className="flex-1 min-w-0 flex items-center gap-1.5">
                      <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{r.name}</span>
                      <span className="text-[10px] font-mono text-slate-400">{r.symbol}</span>
                      {r.out_of_sync && (
                        <AlertTriangle className="w-3 h-3 text-amber-500 flex-shrink-0" aria-label="源侧停更" />
                      )}
                    </span>
                    <span className="w-16 text-right font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap">
                      {fmt(r.inventory, 0)}
                    </span>
                    <span className="w-14 text-right">
                      <NumText value={r.chg_20d_pct} suffix="%" digits={1} className="text-[10px]" />
                    </span>
                    <span className="w-16 flex justify-end">
                      <PctBar pct={r.pct_hist} width={36} />
                    </span>
                    <span className="w-12 flex justify-center">
                      {cycle ? <Badge text={cycle.label} cls={cycle.cls} title={cycle.hint} /> : <span className="text-[10px] text-slate-300">—</span>}
                    </span>
                  </button>
                );
              })}
              <div className="flex items-center gap-1.5 text-[9px] text-slate-400 pt-1.5 leading-snug">
                <Info className="w-3 h-3 flex-shrink-0" />
                <span>
                  周期徽标按 20 日库存变化判定（累库 &gt; +0.5% / 去库 &lt; −0.5% / 其间走平）；
                  分位为品种自身全历史位置；⚠ 为源侧停更品种（as-of 落后全局最新逾 7 天），保留展示不冒充新鲜。
                </span>
              </div>
            </div>
          )}
        </SectionCard>

        <InventoryHistoryCard detail={emDetail} loading={loading} />
      </div>

      {/* 行 2：注册仓单对照 + 仓单历史 */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5 items-stretch">
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Package className="w-3.5 h-3.5 text-amber-600" />
              注册仓单对照
              <span className="text-[9px] font-normal text-slate-400">按仓单量降序；默认选中第一行</span>
            </span>
          }
          extra={
            <div className="flex items-center gap-2">
              <DateBadge label="数据日期" date={receipts?.latest_date} />
              <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">
                {receipts?.variety_count ?? '—'} 品种
              </span>
            </div>
          }
        >
          <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
            <span className="font-bold text-slate-400">覆盖所</span>
            {(receipts?.venues ?? []).map((v) => (
              <Badge key={v} text={receipts?.venue_names?.[v] ?? v} cls="bg-amber-50 text-amber-700 border-amber-200" />
            ))}
            {(receipts?.venues ?? []).length === 0 && <span className="text-slate-400">—</span>}
          </div>
          {receiptRows.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据（仓单源仅覆盖部分交易所）'} />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              <div className="flex items-center gap-2 px-1.5 text-[9px] font-bold text-slate-400 sticky top-0 bg-white/95 backdrop-blur-sm py-0.5">
                <span className="w-5" />
                <span className="flex-1">品种</span>
                <span className="w-14 text-right">交易所</span>
                <span className="w-24 text-right">仓单量（万张）</span>
                <span className="w-20 text-right">变化（万张）</span>
              </div>
              {receiptRows.map((r, i) => {
                const active = variety === r.variety;
                return (
                  <button
                    key={r.variety}
                    onClick={() => setVariety(r.variety)}
                    title={`${r.name} ${r.variety} · ${r.venue_name}；点击查看仓单历史`}
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
                    <span className="flex-1 min-w-0 flex items-center gap-1.5">
                      <span className="text-xs font-bold text-slate-800 whitespace-nowrap">{r.name}</span>
                      <span className="text-[10px] font-mono text-slate-400">{r.variety}</span>
                    </span>
                    <span className="w-14 text-right text-[10px] font-bold text-slate-500 whitespace-nowrap">
                      {r.venue_name}
                    </span>
                    <span className="w-24 text-right font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap">
                      {fmt(r.receipt_qty_wan)}
                    </span>
                    <span className="w-20 text-right">
                      <NumText value={r.change_wan} className="text-[11px]" />
                    </span>
                  </button>
                );
              })}
              <div className="flex items-center gap-1.5 text-[9px] text-slate-400 pt-1.5 leading-snug">
                <Info className="w-3 h-3 flex-shrink-0" />
                <span>
                  仓单 = 交易所注册标准仓单（显性库存官方口径）；覆盖所以最新日期实际收录者为准，缺数据不落 0。
                </span>
              </div>
            </div>
          )}
        </SectionCard>

        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              仓单历史
              {receiptHistory && (
                <span className="text-[10px] font-bold text-slate-500">
                  {receiptHistory.name}
                  <span className="font-mono text-slate-400 ml-1">{receiptHistory.variety}</span>
                </span>
              )}
            </span>
          }
          extra={receiptHistory ? <DateBadge label="最新" date={receiptHistory.latest} /> : undefined}
        >
          {!receiptHistory ? (
            <EmptyHint loading={loading} text="选择左侧品种查看仓单历史" />
          ) : receiptPoints.length === 0 ? (
            <EmptyHint text="该品种暂无仓单历史" />
          ) : (
            <>
              <div className="flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
                <span className="flex items-baseline gap-1 whitespace-nowrap">
                  <span className="font-bold text-slate-400">最新仓单</span>
                  <span className="text-[13px] font-extrabold font-mono text-slate-800">
                    {fmt(receiptHistory.latest_qty_wan)} 万张
                  </span>
                </span>
                <span className="flex items-baseline gap-1 whitespace-nowrap">
                  <span className="font-bold text-slate-400">
                    {receiptHistory.change_days ? `近 ${receiptHistory.change_days} 日变化` : '区间变化'}
                  </span>
                  <NumText value={receiptHistory.change_wan} suffix="万张" className="text-[12px]" />
                </span>
                <span className="text-slate-400 font-mono ml-auto">{receiptPoints.length} 个交易日</span>
              </div>
              <FuturesMiniChart option={receiptOption} height={CHART_H} />
            </>
          )}
        </SectionCard>
      </div>
    </div>
  );
};
