/** 期货市场分析 · Tab1 行情总览
 *
 * 市场背景行（中证商品期货指数基准 + 交易所官方成交额 + 数据质检）
 * + 领涨/领跌榜（主力连续口径，含换月跳空）+ 主力榜单（成交额/持仓额/增减仓切换）
 * + 板块温度条（强势/弱势板块中位涨幅）。
 *
 * 布局口径（用户拍板）：三个榜单卡片等高，列表超出时**卡片内滚动**，
 * 不把整页撑长；质检列表同样限高内滚。
 */

import React, { useEffect, useMemo, useState } from 'react';
import type * as echarts from 'echarts';
import { ArrowDownRight, ArrowUpRight, Flame, Snowflake, Trophy, Landmark, Banknote, ShieldCheck } from 'lucide-react';
import { PctText, NumText, RankRow, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getRanking } from '../services/api';
import type {
  FuturesBenchmark,
  FuturesDatasetQuality,
  FuturesFeedStatus,
  FuturesMarketTurnover,
  FuturesOverview,
  FuturesRankRow,
  FuturesRanking,
  RankingKind,
} from '../types';
import { Badge, FRESHNESS_META, fmt, fmtUnit } from '../format';
import { AXIS_LABEL, FuturesMiniChart } from './MiniChart';

const METRIC_KINDS: Array<{ id: RankingKind; label: string; hint: string }> = [
  { id: 'turnover', label: '成交额', hint: '名义成交额（价 × 量 × 乘数），非真实资金流水' },
  { id: 'oi_amount', label: '持仓额', hint: '名义持仓额（价 × 仓 × 乘数）' },
  { id: 'oi_change', label: '增减仓', hint: '持仓量较昨日变化（万手）' },
];

const ROW_LIMIT = 10;
/** 榜单列表限高（约 10 行）：三卡等高，超出内滚（用户口径） */
const LIST_MAX_H = 'max-h-[336px]';

/** 贴右对齐的迷你「标签 + 数值」格 */
function MetricCell({ label, value, suffix = '%', title }: { label: string; value: number | null | undefined; suffix?: string; title?: string }) {
  const v = typeof value === 'number' && Number.isFinite(value) ? value : null;
  const color = v === null ? 'text-slate-400' : v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-slate-500';
  return (
    <span className="flex flex-col leading-tight" title={title}>
      <span className="text-[9px] font-bold text-slate-400">{label}</span>
      <span className={`text-[11px] font-extrabold font-mono tabular-nums ${color}`}>
        {v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}${suffix}`}
      </span>
    </span>
  );
}

/** 大盘背景条：中证商品期货指数（官方口径区间/年化收益）+ 4 年走势迷你线 */
function BenchmarkCard({ bm }: { bm: FuturesBenchmark | null | undefined }) {
  const option = useMemo<echarts.EChartsOption | null>(() => {
    const hist = bm?.history ?? [];
    if (hist.length < 10) return null;
    return {
      grid: { left: 6, right: 6, top: 6, bottom: 2, containLabel: true },
      animationDuration: 300,
      xAxis: {
        type: 'category',
        data: hist.map((p) => p.date),
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: { ...AXIS_LABEL, hideOverlap: true, showMaxLabel: true },
      },
      yAxis: { type: 'value', scale: true, axisLabel: { show: false }, splitLine: { show: false } },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e2e8f0',
        textStyle: { fontSize: 11, color: '#334155' },
        formatter: (params: any) => {
          const p = Array.isArray(params) ? params[0] : params;
          const pt = hist[p?.dataIndex];
          return pt ? `<b>${pt.date}</b><br/>${fmt(pt.close, 1)}` : '';
        },
      },
      series: [
        {
          type: 'line',
          showSymbol: false,
          data: hist.map((p) => p.close),
          lineStyle: { width: 1.5, color: '#d97706' },
          areaStyle: { opacity: 0.08, color: '#d97706' },
        },
      ],
    };
  }, [bm]);

  return (
    <SectionCard
      className="!gap-2"
      title={
        <span className="flex items-center gap-1.5">
          <Landmark className="w-3.5 h-3.5 text-amber-600" />
          大盘基准
          <span className="text-[9px] font-normal text-slate-400">{bm?.name ?? '中证商品期货指数'}</span>
        </span>
      }
      extra={bm ? <DateBadge label="数据日期" date={bm.date} /> : null}
    >
      {!bm ? (
        <EmptyHint text="基准指数数据不可用" />
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-extrabold font-mono text-slate-800 tabular-nums">{fmt(bm.close, 1)}</span>
            <PctText value={bm.pct_change} className="text-xs" />
            <span className="ml-auto text-[9px] text-slate-400" title={`数据源：${bm.source}`}>
              {bm.source}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-x-2 gap-y-1">
            <MetricCell label="1 月" value={bm.change_1m} title="近 1 个月涨跌幅" />
            <MetricCell label="3 月" value={bm.change_3m} title="近 3 个月涨跌幅" />
            <MetricCell label="6 月" value={bm.change_6m} title="近 6 个月涨跌幅" />
            <MetricCell label="年初至今" value={bm.ytd_change} title="年初至今涨跌幅" />
            <MetricCell label="近 1 年年化" value={bm.annual_return_1y} title="近 1 年区间年化收益（官方口径）" />
            <MetricCell label="近 3 年年化" value={bm.annual_return_3y} title="近 3 年区间年化收益（官方口径）" />
            <MetricCell label="近 5 年年化" value={bm.annual_return_5y} title="近 5 年区间年化收益（官方口径）" />
            <MetricCell label="近 10 年年化" value={bm.annual_return_10y} title="近 10 年区间年化收益（官方口径）" />
          </div>
          <FuturesMiniChart option={option} height={88} emptyText="基准历史不足" />
        </>
      )}
    </SectionCard>
  );
}

/** 交易所官方真实成交额条（与名义估算口径区分） */
function TurnoverCard({ mt }: { mt: FuturesMarketTurnover | null | undefined }) {
  const venues = mt ? Object.entries(mt.by_venue_yi ?? {}).sort((a, b) => b[1] - a[1]) : [];
  const total = mt?.total_yi ?? null;
  return (
    <SectionCard
      className="!gap-2"
      title={
        <span className="flex items-center gap-1.5">
          <Banknote className="w-3.5 h-3.5 text-amber-600" />
          交易所官方成交额
          <span className="text-[9px] font-normal text-slate-400">真实口径 · 区别于名义估算</span>
        </span>
      }
      extra={mt ? <DateBadge label="数据日期" date={mt.date} /> : null}
    >
      {!mt ? (
        <EmptyHint text="官方成交额数据不可用" />
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-extrabold font-mono text-slate-800 tabular-nums">
              {fmtUnit(mt.total_yi, '亿')}
            </span>
            <span className="text-[10px] text-slate-400">四所合计 · {mt.row_count} 条合约记录</span>
          </div>
          <div className="flex flex-col gap-1">
            {venues.map(([venue, yi]) => {
              const share = total && total > 0 ? Math.round((yi / total) * 100) : null;
              return (
                <div key={venue} className="flex items-center gap-2 text-[11px]">
                  <span className="w-12 font-extrabold text-slate-600">{venue}</span>
                  <span className="h-1.5 flex-1 rounded-full bg-slate-100 overflow-hidden">
                    <span
                      className="block h-full rounded-full bg-amber-500"
                      style={{ width: `${share ?? 0}%` }}
                    />
                  </span>
                  <span className="w-20 text-right font-mono font-bold text-slate-700 tabular-nums">
                    {fmt(yi)} 亿
                  </span>
                  <span className="w-8 text-right font-mono text-slate-400">{share === null ? '—' : `${share}%`}</span>
                </div>
              );
            })}
          </div>
          <div className="text-[9px] text-slate-400 leading-snug">
            {(mt.venues_missing ?? []).length > 0
              ? `${mt.venues_missing.join(' / ')} 源侧无官方成交额接口（恒缺，如实列出）。`
              : ''}
            此处为交易所日度真实成交额；榜单卡片的「成交额」为名义估算（价 × 量 × 乘数），两者不可混读。
          </div>
        </>
      )}
    </SectionCard>
  );
}

/** 数据质检条（/status.datasets 十项：最新日期 / 覆盖 / 新鲜度三色） */
function QualityCard({ status }: { status: FuturesFeedStatus | null }) {
  const datasets: FuturesDatasetQuality[] = status?.datasets ?? [];
  return (
    <SectionCard
      className="!gap-2"
      title={
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 text-amber-600" />
          数据质检
          <span className="text-[9px] font-normal text-slate-400">数据集新鲜度与覆盖显形</span>
        </span>
      }
      extra={<span className="text-[10px] font-bold text-slate-400">{datasets.length} 项</span>}
    >
      {datasets.length === 0 ? (
        <EmptyHint text="质检数据不可用（/status.datasets）" />
      ) : (
        <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
          {datasets.map((d) => {
            const meta = FRESHNESS_META[d.freshness] ?? FRESHNESS_META.unavailable;
            return (
              <div
                key={d.key}
                className="flex items-center gap-2 py-1.5 border-b border-slate-50 last:border-0"
                title={d.note ?? undefined}
              >
                <span className="text-[11px] font-bold text-slate-700 w-[76px] whitespace-nowrap truncate">
                  {d.name}
                </span>
                <Badge text={meta.label} cls={meta.cls} />
                <span className="text-[10px] font-mono text-slate-500 tabular-nums whitespace-nowrap">
                  {d.latest ?? '—'}
                </span>
                <span className="ml-auto text-[10px] font-mono text-slate-400 whitespace-nowrap">
                  {d.coverage}
                </span>
              </div>
            );
          })}
          <div className="text-[9px] text-slate-400 pt-1.5 leading-snug">
            新鲜度以交易日为参照（新鲜 = 最近交易日；滞后 = 落后数日；缺失 = 无数据）。
            滞后项多为源侧停更，页面各处以「—」显形，不冒充数据。
          </div>
        </div>
      )}
    </SectionCard>
  );
}

function rowRight(row: FuturesRankRow, kind: 'change' | RankingKind): React.ReactNode {
  if (kind === 'turnover') {
    return (
      <span className="font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap" title="名义成交额">
        {fmtUnit(row.turnover_yi, '亿')}
      </span>
    );
  }
  if (kind === 'oi_amount') {
    return (
      <span className="font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap" title="名义持仓额">
        {fmtUnit(row.oi_amount_yi, '亿')}
      </span>
    );
  }
  if (kind === 'oi_change') {
    return (
      <span className="whitespace-nowrap text-xs" title="持仓量较昨日变化（万手）">
        <NumText value={row.oi_change_wan} suffix="万手" />
      </span>
    );
  }
  return (
    <span className="font-mono text-xs font-extrabold text-slate-700 whitespace-nowrap" title={`收盘价（${row.unit}）`}>
      {fmt(row.close)}
    </span>
  );
}

function ListRow({
  row,
  rank,
  kind,
}: {
  row: FuturesRankRow;
  rank: number;
  kind: 'change' | RankingKind;
}) {
  return (
    <RankRow
      rank={rank}
      name={row.name}
      nameSub={`${row.symbol} · ${row.sector_name}`}
      main={<PctText value={row.pct_change} className="text-[11px]" />}
      right={rowRight(row, kind)}
    />
  );
}

/** 板块温度条：强势/弱势板块（国内口径中位涨幅首尾） */
function SectorTempStrip({ overview }: { overview: FuturesOverview | null }) {
  const top = overview?.top_sector;
  const bottom = overview?.bottom_sector;
  if (!top && !bottom) return null;
  return (
    <div className="bg-white/90 backdrop-blur-md rounded-2xl px-3.5 py-2 border border-amber-200/50 shadow-sm flex items-center gap-4 flex-wrap text-[11px]">
      <span className="font-extrabold text-slate-500 whitespace-nowrap">板块温度</span>
      {top && (
        <span className="flex items-center gap-1.5" title="当日中位涨幅最高的板块（国内品种口径）">
          <Flame className="w-3.5 h-3.5 text-red-500" />
          <span className="font-bold text-slate-700">{top.name}</span>
          <PctText value={top.median_pct} />
        </span>
      )}
      {bottom && (
        <span className="flex items-center gap-1.5" title="当日中位涨幅最低的板块（国内品种口径）">
          <Snowflake className="w-3.5 h-3.5 text-sky-500" />
          <span className="font-bold text-slate-700">{bottom.name}</span>
          <PctText value={bottom.median_pct} />
        </span>
      )}
      <span className="text-[10px] text-slate-400 font-mono ml-auto hidden md:inline">
        板块中位涨幅为国内品种口径
      </span>
    </div>
  );
}

export const FuturesOverviewPanel: React.FC<{
  overview: FuturesOverview | null;
  status: FuturesFeedStatus | null;
  reloadKey: number;
}> = ({ overview, status, reloadKey }) => {
  const [ranking, setRanking] = useState<FuturesRanking | null>(null);
  const [kind, setKind] = useState<RankingKind>('turnover');
  const [metricData, setMetricData] = useState<FuturesRanking | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  // 涨跌双榜（固定 kind=change，随刷新重取）
  useEffect(() => {
    let alive = true;
    setLoading(true);
    getRanking('change', ROW_LIMIT)
      .then((d) => {
        if (alive) {
          setRanking(d);
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
  }, [reloadKey]);

  // 主力榜单（kind 切换）
  useEffect(() => {
    let alive = true;
    getRanking(kind, 20)
      .then((d) => {
        if (alive) {
          setMetricData(d);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [kind, reloadKey]);

  const dataDate = ranking?.trade_date || overview?.trade_date || '';
  const gainers = ranking?.gainers ?? [];
  const losers = ranking?.losers ?? [];
  const metricRows = metricData?.rows ?? [];
  const metricMeta = METRIC_KINDS.find((k) => k.id === kind);

  return (
    <div className="flex flex-col gap-2.5">
      <SectorTempStrip overview={overview} />

      {/* 市场背景行：基准 / 官方成交额 / 数据质检 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-start">
        <BenchmarkCard bm={overview?.benchmark} />
        <TurnoverCard mt={overview?.market_turnover} />
        <QualityCard status={status} />
      </div>

      {/* 三榜单（等高·内滚） */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-start">
        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <ArrowUpRight className="w-3.5 h-3.5 text-red-500" />
              领涨榜 TOP{ROW_LIMIT}
              <span className="text-[9px] font-normal text-slate-400">主力连续含换月跳空</span>
            </span>
          }
          extra={<DateBadge label="数据日期" date={dataDate} />}
        >
          {gainers.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              {gainers.map((r, i) => (
                <ListRow key={r.symbol} row={r} rank={i + 1} kind="change" />
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <ArrowDownRight className="w-3.5 h-3.5 text-green-600" />
              领跌榜 TOP{ROW_LIMIT}
              <span className="text-[9px] font-normal text-slate-400">主力连续含换月跳空</span>
            </span>
          }
          extra={<DateBadge label="数据日期" date={dataDate} />}
        >
          {losers.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              {losers.map((r, i) => (
                <ListRow key={r.symbol} row={r} rank={i + 1} kind="change" />
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          className="!gap-2"
          title={
            <span className="flex items-center gap-1.5">
              <Trophy className="w-3.5 h-3.5 text-amber-600" />
              主力榜单
              <span className="text-[9px] font-normal text-slate-400">{metricMeta?.hint}</span>
            </span>
          }
          extra={
            <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
              {METRIC_KINDS.map((k) => (
                <button
                  key={k.id}
                  onClick={() => setKind(k.id)}
                  title={k.hint}
                  className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold transition-all cursor-pointer ${
                    kind === k.id
                      ? 'bg-white text-amber-700 shadow-2xs border border-amber-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {k.label}
                </button>
              ))}
            </div>
          }
        >
          {metricRows.length === 0 ? (
            <EmptyHint loading={loading} text={failed ? '加载失败' : '暂无数据'} />
          ) : (
            <div className={`flex flex-col ${LIST_MAX_H} overflow-y-auto pr-0.5`}>
              {metricRows.map((r, i) => (
                <ListRow key={r.symbol} row={r} rank={i + 1} kind={kind} />
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
};
