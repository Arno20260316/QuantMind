/** 美股市场分析 · GICS 板块热力图卡片（两层：板块 → 成分个股下钻）
 *
 * L1 板块层：11 个 GICS 大类 + 「未分类」（面积=板块成分总市值，亿美元）
 * L2 个股层：点板块钻取成分个股（面积=个股市值），点个股回调宿主打开个股终端
 *
 * 与 A 股 HeatmapDrillCard 同一交互范式（同一张图内两层切换 + 面包屑返回），
 * 但美股没有 L2 资金流（无「主力净额」着色）、也没有多分类 tab，故独立实现而非复用；
 * 图表本体一律走共享 SectorHeatmapChart（三市场数据形状一致）。
 *
 * 口径：板块涨跌幅=成分**中位数**（未复权原始价，拆股跳变对中位数稳健，
 * 也与页面其余面板口径一致）；面积对市值缺失的标的回落成交额，tooltip 如实披露。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Layers, ChevronRight, ArrowLeft, Loader2, AlertTriangle } from 'lucide-react';
import {
  SectorHeatmapChart,
  type SectorHeatmapItem,
} from '../../market-analysis-shared/SectorHeatmapChart';
import { getHeatmapDrill } from '../services/api';
import type { UsSectorDrill, UsSectorHeatItem } from '../types';

interface UsSectorHeatmapCardProps {
  /** L1 板块层数据（页面预取） */
  items: UsSectorHeatItem[];
  /** 点击个股 → 宿主打开美股个股终端 */
  onStockClick?: (symbol: string, name: string) => void;
  className?: string;
}

function fmtYi(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${Math.round(v).toLocaleString('zh-CN')} 亿`;
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

export const UsSectorHeatmapCard: React.FC<UsSectorHeatmapCardProps> = ({
  items,
  onStockClick,
  className,
}) => {
  // 下钻板块用英文类名（接口键）；显示名取接口回传的 sector_cn
  const [drillSector, setDrillSector] = useState<string | null>(null);
  const [drill, setDrill] = useState<UsSectorDrill | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillError, setDrillError] = useState<string | null>(null);

  // 板块显示名 → 英文类名（treemap 点击回传的是显示名）
  const sectorByName = useMemo(() => {
    const m = new Map<string, string>();
    items.forEach((it) => m.set(it.name, it.sector));
    return m;
  }, [items]);

  useEffect(() => {
    if (!drillSector) {
      setDrill(null);
      setDrillError(null);
      return;
    }
    let cancelled = false;
    setDrillLoading(true);
    setDrillError(null);
    setDrill(null);
    getHeatmapDrill(drillSector)
      .then((d) => {
        if (!cancelled) setDrill(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setDrillError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setDrillLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [drillSector]);

  const l1Data: SectorHeatmapItem[] = useMemo(
    () =>
      items.map((h) => ({
        name: h.name,
        value: h.value,
        pct_change: h.pct_change,
        leader: h.leader,
        leader_pct: h.leader_pct,
        extra: {
          stock_count: h.stock_count,
          advance_count: h.advance_count,
          amount_yi: h.amount_yi,
          value_basis: h.value_basis,
        },
      })),
    [items],
  );

  // 个股层：名称 → symbol（点击开终端用）
  const symbolByName = useMemo(() => {
    const m = new Map<string, string>();
    (drill?.items || []).forEach((it) => m.set(it.name, it.symbol));
    return m;
  }, [drill]);

  const l2Data: SectorHeatmapItem[] = useMemo(
    () =>
      (drill?.items || []).map((it) => ({
        name: it.name,
        value: it.value,
        pct_change: it.pct_change,
        extra: {
          symbol: it.symbol,
          industry_cn: it.industry_cn || it.industry,
          rvol: it.rvol,
          market_cap_yi: it.market_cap_yi,
          amount_yi: it.amount_yi,
          value_basis: it.value_basis,
        },
      })),
    [drill],
  );

  const handleL1Click = (name: string) => {
    const sector = sectorByName.get(name);
    if (sector) setDrillSector(sector);
  };
  const handleL2Click = (name: string) => {
    const symbol = symbolByName.get(name);
    if (symbol && onStockClick) onStockClick(symbol, name);
  };

  const backToTop = () => setDrillSector(null);
  const drillName = drill?.sector_cn || drillSector || '';
  const summary = drill?.summary;

  return (
    <div
      className={`bg-white/90 backdrop-blur-md rounded-2xl p-2.5 border border-slate-200/80 shadow-sm flex flex-col gap-1.5 min-h-0 ${className || ''}`}
    >
      {/* 头部：标题（下钻层为面包屑）+ 计数 */}
      <div className="flex items-center justify-between gap-2 min-w-0">
        <h3 className="text-[11px] font-extrabold text-slate-800 flex items-center gap-1.5 min-w-0">
          <Layers className="w-3.5 h-3.5 text-blue-600 shrink-0" />
          {drillSector ? (
            <span className="flex items-center gap-1 min-w-0">
              <button
                type="button"
                onClick={backToTop}
                className="flex items-center gap-0.5 text-blue-600 hover:text-blue-800 hover:underline shrink-0 cursor-pointer"
                title="返回板块层"
              >
                <ArrowLeft className="w-3 h-3" />
                板块热力图
              </button>
              <ChevronRight className="w-3 h-3 text-slate-300 shrink-0" />
              <span className="truncate">{drillName}</span>
            </span>
          ) : (
            <span className="truncate">GICS 板块热力图（中位涨幅 / 总市值 / 领涨龙头）</span>
          )}
        </h3>
        {!drillSector && (
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap shrink-0">
            {items.length} 个板块 · 点击方块下钻成分股
          </span>
        )}
      </div>

      {/* 口径披露条 */}
      <div className="flex items-center justify-between px-0.5 gap-2 min-w-0">
        <span className="text-[9px] text-slate-400 font-mono truncate">
          {drillSector
            ? '面积=个股市值 · 颜色=涨跌幅 · 点击个股打开个股终端'
            : '面积=板块总市值 · 颜色=中位涨跌幅 · 未复权原始价'}
        </span>
        {drillSector && summary && !drillLoading && (
          <span className="text-[9px] text-slate-400 font-mono whitespace-nowrap shrink-0">
            {drill?.trade_date} · {summary.shown}/{summary.members} 只
            {summary.truncated ? '（按市值截断）' : ''} · 中位 {fmtPct(summary.median_pct)}
            {summary.leader ? ` · 领涨 ${summary.leader} ${fmtPct(summary.leader_pct)}` : ''}
          </span>
        )}
      </div>

      {/* 图区 */}
      <div className="flex-1 min-h-0 relative">
        {drillLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 backdrop-blur-xs">
            <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />
          </div>
        )}
        {drillSector && drillError && !drillLoading ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-[11px] text-slate-500 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            <span>「{drillName}」成分数据加载失败：{drillError.slice(0, 80)}</span>
            <button
              type="button"
              onClick={backToTop}
              className="px-3 py-0.5 rounded-full bg-blue-600 text-white text-[10px] font-extrabold hover:bg-blue-500 cursor-pointer"
            >
              返回板块层
            </button>
          </div>
        ) : drillSector ? (
          drill && l2Data.length > 0 ? (
            <SectorHeatmapChart
              data={l2Data}
              height="100%"
              valueLabel="总市值"
              pctLabel="涨跌幅"
              renderKey={`us-drill-${drillSector}`}
              onItemClick={handleL2Click}
              tooltipExtraOf={(item) => {
                const ex = item.extra || {};
                const parts: string[] = [];
                if (ex.industry_cn) parts.push(String(ex.industry_cn));
                if (typeof ex.rvol === 'number' && Number.isFinite(ex.rvol)) {
                  parts.push(`量比 ${ex.rvol.toFixed(2)}`);
                }
                const amount = ex.amount_yi;
                if (typeof amount === 'number' && Number.isFinite(amount)) {
                  parts.push(`成交额 ${fmtYi(amount)}`);
                }
                if (ex.value_basis === 'amount') parts.push('市值缺省·按成交额铺面积');
                return parts
                  .map((p) => `<span style="color:#cbd5e1;">${p}</span>`)
                  .join(' · ');
              }}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-[11px] text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200">
              该板块暂无成分数据
            </div>
          )
        ) : items.length > 0 ? (
          <SectorHeatmapChart
            data={l1Data}
            height="100%"
            valueLabel="总市值"
            pctLabel="中位涨跌幅"
            renderKey="us-heatmap-top"
            onItemClick={handleL1Click}
            tooltipExtraOf={(item) => {
              const ex = item.extra || {};
              const parts: string[] = [];
              if (typeof ex.stock_count === 'number') {
                parts.push(
                  `成分 ${ex.advance_count}/${ex.stock_count} 只上涨`,
                );
              }
              if (typeof ex.amount_yi === 'number' && Number.isFinite(ex.amount_yi)) {
                parts.push(`成交额 ${fmtYi(ex.amount_yi)}`);
              }
              if (ex.value_basis === 'amount') parts.push('市值缺省·按成交额铺面积');
              return parts
                .map((p) => `<span style="color:#cbd5e1;">${p}</span>`)
                .join(' · ');
            }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-[11px] text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200">
            热力图加载中…
          </div>
        )}
      </div>
    </div>
  );
};
