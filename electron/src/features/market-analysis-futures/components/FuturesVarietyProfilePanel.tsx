/** 期货市场分析 · 品种画像（/variety 聚合）
 *
 * 六段：前瞻因子明细 · 期限曲线 · carry 历史 · 现货基差历史 · 库存历史 ·
 * 价格/持仓 · 席位净额与龙虎榜。缺失段一律空态显形，绝不冒充数据。
 */

import React, { useEffect, useMemo, useState } from 'react';
import type * as echarts from 'echarts';
import { LineChart, Activity, Package, Users, TrendingUp, Sparkles, RotateCcw } from 'lucide-react';
import { SectionCard, EmptyHint } from '../../market-analysis-shared/ui';
import { getVariety } from '../services/api';
import type { FuturesVarietyProfile, OutlookFactorKey } from '../types';
import { Badge, PctBar, STRUCTURE_META, TIER_META, fmt, num } from '../format';
import { AXIS_LABEL, TOOLTIP_BASE, FuturesMiniChart } from './MiniChart';

const CHART_COLOR = '#d97706'; // amber-600
const MAIN_COLOR = '#dc2626'; // red-600
const OI_COLOR = '#0284c7'; // sky-600

const FACTOR_LABEL: Record<OutlookFactorKey, string> = {
  carry: '年化 carry',
  basis: '主力基差率',
  inv_chg: '20 日库存变化',
  mom_20d: '20 日动量',
  seasonality: '同月季节性',
  member_net: '席位净多额',
};
const FACTOR_KEYS: OutlookFactorKey[] = ['carry', 'basis', 'inv_chg', 'mom_20d', 'seasonality', 'member_net'];

const DATE_AXIS = {
  type: 'category' as const,
  axisLine: { lineStyle: { color: '#e2e8f0' } },
  axisTick: { show: false },
  axisLabel: { ...AXIS_LABEL, hideOverlap: true },
};
const VALUE_AXIS = {
  type: 'value' as const,
  scale: true,
  axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => fmt(v, 1) },
  splitLine: { lineStyle: { color: '#f1f5f9' } },
};
const MARK_ZERO = {
  silent: true,
  symbol: 'none',
  lineStyle: { color: '#cbd5e1', type: 'dashed' as const },
  label: { show: false },
  data: [{ yAxis: 0 }],
};

export const FuturesVarietyProfilePanel: React.FC<{
  symbol: string;
  reloadKey: number;
}> = ({ symbol, reloadKey }) => {
  const [data, setData] = useState<FuturesVarietyProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setData(null);
    getVariety(symbol)
      .then((d) => {
        if (alive) {
          setData(d);
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
  }, [symbol, reloadKey, retryTick]);

  const termOption = useMemo<echarts.EChartsOption | null>(() => {
    const curve = data?.term.curve ?? [];
    if (curve.length === 0) return null;
    const yms = curve.map((c) => c.ym);
    const closes = curve.map((c) => num(c.close));
    const mainIdx = data?.term.main_contract
      ? curve.findIndex((c) => c.contract === data.term.main_contract)
      : -1;
    return {
      grid: { left: 10, right: 16, top: 26, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: yms },
      yAxis: VALUE_AXIS,
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const c = curve[p?.dataIndex];
          if (!c) return '';
          return [
            `<b>${c.contract}</b>${c.contract === data?.term.main_contract ? ' 主力' : ''}`,
            `收盘: <b>${fmt(c.close)}</b>`,
            c.expiry_date
              ? `到期参考: ${c.expiry_date}${c.expiry_estimated ? '（估计）' : ''}`
              : '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      },
      series: [
        {
          type: 'line',
          symbol: 'circle',
          symbolSize: 6,
          data: closes,
          lineStyle: { width: 2, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          areaStyle: { opacity: 0.06, color: CHART_COLOR },
          markPoint:
            mainIdx >= 0
              ? {
                  symbol: 'pin',
                  symbolSize: 34,
                  itemStyle: { color: MAIN_COLOR },
                  label: { fontSize: 9, fontWeight: 'bold', color: '#fff', formatter: '主力' },
                  data: [{ name: '主力', coord: [mainIdx, closes[mainIdx] ?? 0] }],
                }
              : undefined,
        },
      ],
    };
  }, [data]);

  const carryOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = data?.carry_history ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 10, right: 10, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: pts.map((p) => p.date) },
      yAxis: { ...VALUE_AXIS, axisLabel: { fontSize: 9, color: '#94a3b8', formatter: '{value}%' } },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          if (!pt) return '';
          return `<b>${pt.date}</b><br/>年化 carry: <b>${fmt(pt.carry_annual_pct)}%</b><br/>${pt.near_contract} → ${pt.far_contract}（${pt.months} 个月差）`;
        },
      },
      series: [
        {
          type: 'line',
          showSymbol: false,
          data: pts.map((p) => num(p.carry_annual_pct)),
          lineStyle: { width: 1.5, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          areaStyle: { opacity: 0.06, color: CHART_COLOR },
          markLine: MARK_ZERO,
        },
      ],
    };
  }, [data]);

  const basisOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = data?.basis.history?.points ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 10, right: 10, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: pts.map((p) => p.date) },
      yAxis: { ...VALUE_AXIS, axisLabel: { fontSize: 9, color: '#94a3b8', formatter: '{value}%' } },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          if (!pt) return '';
          return [
            `<b>${pt.date}</b>`,
            `基差率(主力): <b>${fmt(pt.dom_basis_rate_pct)}%</b>`,
            `现货: ${fmt(pt.spot_price)}　主力: ${fmt(pt.dom_price)}`,
          ].join('<br/>');
        },
      },
      series: [
        {
          type: 'line',
          showSymbol: false,
          data: pts.map((p) => num(p.dom_basis_rate_pct)),
          lineStyle: { width: 1.5, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          markLine: MARK_ZERO,
        },
      ],
    };
  }, [data]);

  const inventoryOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = data?.inventory.history?.points ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 10, right: 10, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: pts.map((p) => p.date) },
      yAxis: VALUE_AXIS,
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          if (!pt) return '';
          return `<b>${pt.date}</b><br/>库存: <b>${fmt(pt.inventory)}</b><br/>环比: ${fmt(pt.delta)}`;
        },
      },
      series: [
        {
          type: 'line',
          showSymbol: false,
          data: pts.map((p) => num(p.inventory)),
          lineStyle: { width: 1.5, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          areaStyle: { opacity: 0.08, color: CHART_COLOR },
        },
      ],
    };
  }, [data]);

  const priceOiOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = data?.price_oi ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 10, right: 10, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: pts.map((p) => p.date) },
      yAxis: [
        { ...VALUE_AXIS, axisLabel: { fontSize: 9, color: '#d97706' } },
        {
          ...VALUE_AXIS,
          splitLine: { show: false },
          axisLabel: { fontSize: 9, color: '#0284c7' },
        },
      ],
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const list = Array.isArray(params) ? params : [params];
          const pt = pts[list[0]?.dataIndex];
          if (!pt) return '';
          return `<b>${pt.date}</b><br/>收盘: <b>${fmt(pt.close)}</b><br/>持仓: ${fmt(pt.position)}`;
        },
      },
      series: [
        {
          name: '收盘',
          type: 'line',
          showSymbol: false,
          data: pts.map((p) => num(p.close)),
          lineStyle: { width: 1.5, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
        },
        {
          name: '持仓',
          type: 'line',
          yAxisIndex: 1,
          showSymbol: false,
          data: pts.map((p) => num(p.position)),
          lineStyle: { width: 1.5, color: OI_COLOR, type: 'dashed' },
          itemStyle: { color: OI_COLOR },
        },
      ],
      legend: { show: false },
    };
  }, [data]);

  const netTrendOption = useMemo<echarts.EChartsOption | null>(() => {
    const pts = data?.members.net_trend ?? [];
    if (pts.length === 0) return null;
    return {
      grid: { left: 10, right: 10, top: 16, bottom: 4, containLabel: true },
      animationDuration: 300,
      xAxis: { ...DATE_AXIS, data: pts.map((p) => p.date) },
      yAxis: { ...VALUE_AXIS, axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v: number) => fmt(v, 1) } },
      tooltip: {
        ...TOOLTIP_BASE,
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = pts[p?.dataIndex];
          if (!pt) return '';
          return `<b>${pt.date}</b><br/>前 20 席位净多: <b>${fmt(pt.net_wan)} 万手</b>`;
        },
      },
      series: [
        {
          type: 'line',
          symbolSize: 5,
          data: pts.map((p) => num(p.net_wan)),
          lineStyle: { width: 2, color: CHART_COLOR },
          itemStyle: { color: CHART_COLOR },
          markLine: MARK_ZERO,
        },
      ],
    };
  }, [data]);

  if (failed && !data) {
    return (
      <SectionCard title="品种画像">
        <div className="flex flex-col items-center gap-2 py-10">
          <span className="text-xs text-slate-400">加载失败</span>
          <button
            onClick={() => setRetryTick((v) => v + 1)}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-amber-600 text-white text-[11px] font-extrabold hover:bg-amber-700 transition-colors cursor-pointer"
          >
            <RotateCcw className="w-3 h-3" /> 重试
          </button>
        </div>
      </SectionCard>
    );
  }

  if (!data) {
    return (
      <SectionCard title="品种画像">
        <EmptyHint loading={loading} text="暂无数据" />
      </SectionCard>
    );
  }

  const outlook = data.outlook;
  const tier = outlook?.tier ? TIER_META[outlook.tier] : null;
  const structMeta = data.term.structure ? STRUCTURE_META[data.term.structure] : null;
  const invRow = data.inventory.rows.find((r) => r.symbol === data.symbol);
  const basisPts = data.basis.history?.points ?? [];
  const basisLast = basisPts.length > 0 ? basisPts[basisPts.length - 1] : null;
  const members = data.members;
  const seatLabels = members.side_labels ?? {};

  return (
    <div className="flex flex-col gap-2.5">
      {/* 头部 + 因子明细 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-amber-600" />
            {data.name} 品种画像
            <span className="text-[10px] font-mono text-slate-400">
              {data.symbol} · {data.sector_name} · {data.exchange}
            </span>
            {tier && outlook?.composite !== null && (
              <Badge
                text={`${tier.label} ${outlook?.composite?.toFixed(1) ?? ''}`}
                cls={tier.cls}
                title={`合成分（可用 ${outlook?.coverage ?? 0} 个因子等权）`}
              />
            )}
            {structMeta && <Badge text={structMeta.label} cls={structMeta.cls} title={structMeta.hint} />}
          </span>
        }
        extra={<span className="text-[10px] font-mono text-slate-400">单位：{data.unit}</span>}
      >
        {outlook ? (
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-1.5">
            {FACTOR_KEYS.map((k) => {
              const f = outlook.factors[k];
              const isFact = k === 'member_net';
              return (
                <div
                  key={k}
                  className={`rounded-lg border px-2 py-1.5 ${
                    isFact ? 'border-slate-100 bg-slate-50/60' : 'border-amber-100 bg-amber-50/40'
                  }`}
                >
                  <div className="text-[9px] font-bold text-slate-400">{FACTOR_LABEL[k]}</div>
                  <div className="text-[12px] font-extrabold font-mono text-slate-800 tabular-nums">
                    {isFact ? fmt(f.value_wan ?? f.value) : fmt(f.value)}
                    {isFact && f.delta_wan !== null && f.delta_wan !== undefined && (
                      <span className="text-[9px] font-bold text-slate-400 ml-1">
                        Δ{fmt(f.delta_wan)}
                      </span>
                    )}
                  </div>
                  {isFact ? (
                    <div className="text-[9px] text-slate-400 leading-snug">
                      纯事实 · {f.sample_days ?? 0} 日样本
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 mt-0.5">
                      <PctBar pct={f.pct} width={44} showValue />
                      <span className="text-[9px] text-slate-400">分位</span>
                    </div>
                  )}
                  <div className="text-[9px] text-slate-400 leading-snug mt-0.5">
                    {f.as_of ?? (f.years ? `${f.years} 年样本` : '')}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyHint text="该品种覆盖不足，未入前瞻板" />
        )}
      </SectionCard>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5 items-start">
        {/* 期限曲线 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-amber-600" />
              期限曲线
            </span>
          }
          extra={<span className="text-[10px] font-mono text-slate-400">{data.term.trade_date ?? ''}</span>}
        >
          <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-[10px]">
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">年化 carry</span>
              <span className="font-extrabold font-mono text-slate-800">
                {data.term.carry_annual_pct === null || data.term.carry_annual_pct === undefined
                  ? '—'
                  : `${fmt(data.term.carry_annual_pct)}%`}
              </span>
            </span>
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">近月距到期</span>
              <span className="font-mono text-slate-600">
                {data.term.near_days_to_expiry === null || data.term.near_days_to_expiry === undefined
                  ? '—'
                  : `${data.term.near_days_to_expiry} 天`}
              </span>
            </span>
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">近远月价差</span>
              <span className="font-mono text-slate-600">{fmt(data.term.near_far_spread_pct)}%</span>
            </span>
          </div>
          <FuturesMiniChart option={termOption} height={230} emptyText="该品种暂无合约曲线" />
        </SectionCard>

        {/* carry 历史 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-amber-600" />
              年化 carry 历史
              <span className="text-[9px] font-normal text-slate-400">
                {data.carry_history.length} 个交易日 · 统一月差口径
              </span>
            </span>
          }
        >
          <div className="text-[10px] text-slate-400 leading-snug">
            正 = Back 结构（近高远低，多头展期收益方向）；负 = Contango。
          </div>
          <FuturesMiniChart option={carryOption} height={230} emptyText="carry 历史重建中（依赖合约曲线深度）" />
        </SectionCard>

        {/* 现货基差 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <LineChart className="w-3.5 h-3.5 text-amber-600" />
              现货基差率历史
              <span className="text-[9px] font-normal text-slate-400">生意社现货 vs 期货主力</span>
            </span>
          }
          extra={
            basisLast ? (
              <span className="text-[10px] font-mono text-slate-400">
                最新 {basisLast.date} · {fmt(basisLast.dom_basis_rate_pct)}%
              </span>
            ) : null
          }
        >
          <FuturesMiniChart option={basisOption} height={200} emptyText="该品种无现货基差数据" />
        </SectionCard>

        {/* 库存 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Package className="w-3.5 h-3.5 text-amber-600" />
              库存历史（东财口径）
            </span>
          }
          extra={
            invRow ? (
              <span className="text-[10px] font-mono text-slate-400">
                {invRow.as_of}
                {invRow.out_of_sync ? ' · 源侧停更' : ''}
              </span>
            ) : null
          }
        >
          {invRow && (
            <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-[10px]">
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">5 日</span>
                <span className="font-mono text-slate-600">{fmt(invRow.chg_5d_pct)}%</span>
              </span>
              <span className="flex items-baseline gap-1">
                <span className="font-bold text-slate-400">20 日</span>
                <span className="font-mono text-slate-600">{fmt(invRow.chg_20d_pct)}%</span>
              </span>
              <span className="flex items-center gap-1">
                <span className="font-bold text-slate-400">历史分位</span>
                <PctBar pct={invRow.pct_hist} width={44} showValue />
              </span>
            </div>
          )}
          <FuturesMiniChart option={inventoryOption} height={200} emptyText="该品种无东财库存历史" />
        </SectionCard>

        {/* 价格/持仓 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-amber-600" />
              价格 / 持仓
              <span className="text-[9px] font-normal text-slate-400">
                {data.price_oi.length} 个交易日 · 主力连续口径（换月跳空）
              </span>
            </span>
          }
        >
          <FuturesMiniChart option={priceOiOption} height={200} emptyText="暂无价量数据" />
        </SectionCard>

        {/* 席位 */}
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-amber-600" />
              会员席位（前 20 名净多）
              <span className="text-[9px] font-normal text-slate-400">新浪口径 · 仅 SHFE/CZCE</span>
            </span>
          }
          extra={
            members.as_of ? (
              <span className="text-[10px] font-mono text-slate-400">
                {members.as_of} · {members.sample_days} 日样本
              </span>
            ) : null
          }
        >
          {members.board ? (
            <>
              <FuturesMiniChart option={netTrendOption} height={120} emptyText="净多趋势样本不足" />
              <div className="grid grid-cols-2 gap-2">
                {(['long', 'short'] as const).map((side) => (
                  <div key={side}>
                    <div className="text-[10px] font-extrabold text-slate-500 mb-1">
                      {seatLabels[side] ?? (side === 'long' ? '多单前五' : '空单前五')}
                    </div>
                    {(members.board?.[side] ?? []).slice(0, 5).map((seat) => (
                      <div
                        key={`${side}-${seat.rank}-${seat.member}`}
                        className="flex items-center gap-1 text-[10px] py-[1px]"
                      >
                        <span className="w-4 text-slate-400 font-mono">{seat.rank}</span>
                        <span className="flex-1 truncate text-slate-700 font-bold">{seat.member}</span>
                        <span className="font-mono text-slate-500 tabular-nums">
                          {fmt(seat.size_wan, 1)} 万
                        </span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <EmptyHint text="该品种无席位数据（覆盖 SHFE/CZCE 33 品种）" />
          )}
        </SectionCard>
      </div>
    </div>
  );
};
