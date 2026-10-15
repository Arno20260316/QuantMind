/**
 * 恒生行业热力图 · 两层下钻（L1 行业 → L2 成分股 → 点个股弹终端浮窗）
 *
 * 口径（与后端同源快照保证父子对账，见 hk_heatmap_drill.py）：
 * - 面积 = **当日成交额（亿）**。港股无日频市值（l1 市值列为空，垃圾列），
 *   任何位置不得把面积标注成「市值」。
 * - L1 颜色：涨跌幅（成分股平均） / 机会分（行业中位分，机会雷达口径）
 * - L2 颜色：涨跌幅 / 南向 5 日持股变化（百分点）
 * - L2 面积下限由图表兜底（0.5 亿保可见），tooltip 显示真实值。
 *
 * 缺失语义：南向变化对非港股通标的为 null → 灰色「—」，不冒充 0。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Layers, ChevronRight, ArrowLeft, Loader2 } from 'lucide-react';
import {
  SectorHeatmapChart,
  type SectorHeatmapItem,
} from '../../market-analysis-shared/SectorHeatmapChart';
import { getHeatmapDrill, getOpportunitySectors } from '../services/api';
import type {
  HkHeatmapDrill,
  HkOpportunitySector,
  HkSectorHeatItem,
} from '../types';

type L1Mode = 'pct' | 'score';
type L2Mode = 'pct' | 'south';

const L1_MODE_LABEL: Record<L1Mode, string> = { pct: '涨跌幅', score: '机会分' };
const L2_MODE_LABEL: Record<L2Mode, string> = { pct: '涨跌幅', south: '南向5日变化' };

/** 机会分散色阶（深紫=高分；灰=未评分/成分不足） */
function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '#cbd5e1';
  if (v >= 85) return '#5b21b6';
  if (v >= 70) return '#7c3aed';
  if (v >= 55) return '#a78bfa';
  return '#ddd6fe';
}

/** 南向 5 日持股变化散色（红=增持，绿=减持；灰=非港股通标的无数据） */
function southColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '#cbd5e1';
  if (v >= 0.05) return '#ef4444';
  if (v > 0) return '#f87171';
  if (v === 0) return '#94a3b8';
  if (v > -0.05) return '#34d399';
  return '#10b981';
}

interface HkHeatmapDrillCardProps {
  /** L1 行业层数据（页面预取，与 breadth 同快照日期） */
  sectors: HkSectorHeatItem[];
  /** 点击 L2 个股色块 → 打开港股个股终端浮窗 */
  onStockClick?: (symbol: string, name: string) => void;
  /** 图表区高度（默认 560，与页面既有布局一致） */
  chartHeight?: number;
  className?: string;
}

export const HkHeatmapDrillCard: React.FC<HkHeatmapDrillCardProps> = ({
  sectors,
  onStockClick,
  chartHeight = 560,
  className = '',
}) => {
  const [drillName, setDrillName] = useState<string | null>(null);
  const [drill, setDrill] = useState<HkHeatmapDrill | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [l1Mode, setL1Mode] = useState<L1Mode>('pct');
  const [l2Mode, setL2Mode] = useState<L2Mode>('pct');
  // 机会分着色依赖机会雷达行业榜（按需拉取：选「机会分」才请求）
  const [oppSectors, setOppSectors] = useState<HkOpportunitySector[] | null>(null);
  const [oppLoading, setOppLoading] = useState(false);
  const oppRequestedRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  useEffect(() => {
    if (l1Mode !== 'score' || oppRequestedRef.current) return;
    oppRequestedRef.current = true;
    setOppLoading(true);
    // limit=31（全量行业）+ min_stocks=3：着色图尽量全覆盖；成分 <3 只的行业仍为灰
    // 注意：本 effect 内部 setState 会触发重跑，因此不能用「重跑即取消」的
    // cancelled 旗标（会把自身发起的请求结果丢弃、oppLoading 卡真）；
    // 只在卸载时停止写状态，失败允许下次切回「机会分」时重试。
    getOpportunitySectors(31, 3)
      .then((d) => {
        if (mountedRef.current) setOppSectors(d.items);
      })
      .catch(() => {
        if (!mountedRef.current) return;
        setOppSectors([]);
        oppRequestedRef.current = false;
      })
      .finally(() => {
        if (mountedRef.current) setOppLoading(false);
      });
  }, [l1Mode]);

  // L1 点击 → 下钻
  const handleL1Click = (name: string) => {
    setDrillName(name);
  };

  useEffect(() => {
    if (!drillName) {
      setDrill(null);
      return;
    }
    let cancelled = false;
    setDrillLoading(true);
    getHeatmapDrill(drillName)
      .then((d) => {
        if (!cancelled) setDrill(d);
      })
      .catch(() => {
        // 404（行业名不存在）/ 网络失败 → 回退 L1，不让用户卡在空 L2
        if (!cancelled) {
          setDrill(null);
          setDrillName(null);
        }
      })
      .finally(() => {
        if (!cancelled) setDrillLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [drillName]);

  const scoreByName = useMemo(() => {
    const m = new Map<string, number>();
    (oppSectors || []).forEach((s) => m.set(s.name, s.opp_score_median));
    return m;
  }, [oppSectors]);

  // L1 图数据：面积=行业成交额；行业元数据（领涨龙头等）tooltip 由共享组件渲染
  const l1Data: SectorHeatmapItem[] = useMemo(
    () =>
      sectors.map((h) => ({
        name: h.name,
        value: h.value,
        pct_change: h.pct_change,
        leader: h.leader,
        leader_pct: h.leader_pct,
        extra: { stock_count: h.stock_count },
      })),
    [sectors],
  );

  // L2 图数据：面积=个股成交额；重名标的用「名称·代码」消歧（treemap 点击只回传名称）
  const { l2Data, symbolByChartName } = useMemo(() => {
    const items = drill?.items || [];
    const nameCount = new Map<string, number>();
    items.forEach((it) => nameCount.set(it.name, (nameCount.get(it.name) || 0) + 1));
    const symbolMap = new Map<string, string>();
    const data: SectorHeatmapItem[] = items.map((it) => {
      const chartName =
        (nameCount.get(it.name) || 0) > 1 ? `${it.name}·${it.symbol}` : it.name;
      symbolMap.set(chartName, it.symbol);
      return {
        name: chartName,
        value: it.amount_yi,
        pct_change: it.pct_change,
        extra: { symbol: it.symbol, sb: it.sb_chg_d5_pp },
      };
    });
    return { l2Data: data, symbolByChartName: symbolMap };
  }, [drill]);

  const isL2 = Boolean(drillName);
  const renderKey = `${drillName || 'L1'}-${isL2 ? l2Mode : l1Mode}`;
  const chartData = isL2 ? l2Data : l1Data;
  const busy = drillLoading || (!isL2 && l1Mode === 'score' && oppLoading);
  const modeLabel = isL2 ? L2_MODE_LABEL[l2Mode] : L1_MODE_LABEL[l1Mode];

  const handleChartClick = (name: string) => {
    if (!isL2) {
      handleL1Click(name);
      return;
    }
    const symbol = symbolByChartName.get(name);
    if (symbol && onStockClick) onStockClick(symbol, name);
  };

  const colorOf = (item: SectorHeatmapItem): string | undefined => {
    if (!isL2) {
      if (l1Mode === 'pct') return undefined; // 内置涨跌档位色
      return scoreColor(scoreByName.get(item.name));
    }
    if (l2Mode === 'pct') return undefined;
    const sb = (item.extra as { sb?: number | null } | undefined)?.sb;
    return southColor(sb === undefined ? null : sb);
  };

  const labelValueOf = (item: SectorHeatmapItem): string => {
    if (!isL2) {
      if (l1Mode === 'pct') {
        return `${item.pct_change >= 0 ? '+' : ''}${item.pct_change}%`;
      }
      const v = scoreByName.get(item.name);
      return v === undefined ? '机会分 —' : `机会分 ${v.toFixed(0)}`;
    }
    if (l2Mode === 'pct') {
      return `${item.pct_change >= 0 ? '+' : ''}${item.pct_change}%`;
    }
    const sb = (item.extra as { sb?: number | null } | undefined)?.sb;
    if (sb === null || sb === undefined) return '南向 —';
    return `南向 ${sb >= 0 ? '+' : ''}${sb.toFixed(2)}pp`;
  };

  const tooltipExtraOf = (item: SectorHeatmapItem): string => {
    if (!isL2) {
      if (l1Mode !== 'score') return '';
      const v = scoreByName.get(item.name);
      const count = (item.extra as { stock_count?: number } | undefined)?.stock_count;
      const countText =
        typeof count === 'number' ? ` <span style="color:#cbd5e1;">· 成分 ${count} 只</span>` : '';
      if (v === undefined) {
        return `<span style="color:#94a3b8;">机会分: 未评分（成分 &lt;3 只或未达入榜门槛）</span>${countText}`;
      }
      return `<span style="color:#cbd5e1;">机会分中位: <b style="color:#c4b5fd;">${v.toFixed(1)}</b>（因子复合评分，非模型预测）</span>${countText}`;
    }
    const sb = (item.extra as { sb?: number | null } | undefined)?.sb;
    const sbText =
      sb === null || sb === undefined
        ? '<span style="color:#94a3b8;">南向5日: —（非港股通标的）</span>'
        : `<span style="color:#cbd5e1;">南向5日持股变化: <b style="color:${
            sb >= 0 ? '#f87171' : '#34d399'
          };">${sb >= 0 ? '+' : ''}${sb.toFixed(2)}pp</b></span>`;
    return sbText;
  };

  return (
    <div
      className={`bg-white/90 backdrop-blur-md rounded-2xl p-4 border border-slate-200/80 shadow-sm flex flex-col gap-2 ${className}`}
    >
      {/* 头部：面包屑 + 着色 chips */}
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5 min-w-0">
          <Layers className="w-3.5 h-3.5 text-purple-600 shrink-0" />
          {isL2 ? (
            <span className="flex items-center gap-1 min-w-0">
              <button
                type="button"
                onClick={() => setDrillName(null)}
                className="flex items-center gap-0.5 text-purple-600 hover:text-purple-800 hover:underline shrink-0"
                title="返回行业层"
              >
                <ArrowLeft className="w-3 h-3" />
                恒生行业
              </button>
              <ChevronRight className="w-3 h-3 text-slate-300 shrink-0" />
              <span className="truncate">{drillName}</span>
              <span className="text-[10px] font-mono text-slate-400 shrink-0">
                （成分股 · 面积=成交额）
              </span>
            </span>
          ) : (
            <span className="truncate">恒生行业热力图（平均涨幅 / 成交额 / 领涨龙头）</span>
          )}
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex rounded-full bg-slate-100 p-0.5">
            {(isL2 ? (['pct', 'south'] as L2Mode[]) : (['pct', 'score'] as L1Mode[])).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => (isL2 ? setL2Mode(m as L2Mode) : setL1Mode(m as L1Mode))}
                className={`px-2.5 py-0.5 rounded-full text-[10px] font-extrabold transition-colors ${
                  (isL2 ? l2Mode : l1Mode) === m
                    ? 'bg-white text-purple-700 shadow-2xs border border-purple-200'
                    : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {isL2 ? L2_MODE_LABEL[m as L2Mode] : L1_MODE_LABEL[m as L1Mode]}
              </button>
            ))}
          </div>
          {!isL2 && (
            <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap">
              {sectors.length} 个行业
            </span>
          )}
        </div>
      </div>

      {/* 副标题：口径如实披露 */}
      <div className="flex items-center justify-between px-0.5 gap-2 min-w-0">
        <span className="text-[10px] text-slate-400 font-mono truncate">
          面积=当日成交额 · 颜色={modeLabel}
          {!isL2 && l1Mode === 'score' && '（行业中位分，灰=未评分）'}
          {isL2 && l2Mode === 'south' && '（灰=非港股通标的）'}
        </span>
        {isL2 && drill && (
          <span className="text-[10px] text-slate-400 font-mono whitespace-nowrap">
            {drill.trade_date} · {drill.summary.stock_count} 只 · 合计{' '}
            {drill.summary.total_amount_yi.toFixed(1)} 亿
            {drill.summary.truncated ? '（按成交额截断）' : ''}
          </span>
        )}
        {isL2 && !drill && drillLoading && (
          <span className="text-[10px] text-slate-400 font-mono whitespace-nowrap">加载成分股…</span>
        )}
      </div>

      {/* 图区 */}
      <div className="relative" style={{ minHeight: chartHeight }}>
        {busy && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 rounded-2xl">
            <Loader2 className="w-5 h-5 text-purple-500 animate-spin" />
          </div>
        )}
        {chartData.length > 0 ? (
          <SectorHeatmapChart
            data={chartData}
            height={chartHeight}
            valueLabel={isL2 ? '当日成交额' : '行业成交额'}
            pctLabel={isL2 ? '当日涨跌幅' : '平均涨跌幅'}
            renderKey={renderKey}
            onItemClick={handleChartClick}
            colorOf={colorOf}
            labelValueOf={labelValueOf}
            tooltipExtraOf={tooltipExtraOf}
          />
        ) : (
          <div
            className="w-full flex items-center justify-center text-xs text-slate-400 bg-slate-50/60 rounded-2xl border border-dashed border-slate-200"
            style={{ height: chartHeight }}
          >
            {isL2 ? (drillLoading ? '成分股加载中…' : '该行业暂无可展示成分股') : '热力图加载中…'}
          </div>
        )}
      </div>
    </div>
  );
};
