/** 期货市场分析 · Tab1 行情总览
 *
 * 领涨榜 / 领跌榜（主力连续口径，含换月跳空）+ 主力榜单（成交额/持仓额/增减仓切换）
 * + 板块温度条（强势/弱势板块中位涨幅）。
 */

import React, { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Flame, Snowflake, Trophy } from 'lucide-react';
import { PctText, NumText, RankRow, SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getRanking } from '../services/api';
import type { FuturesOverview, FuturesRankRow, FuturesRanking, RankingKind } from '../types';
import { fmt, fmtUnit } from '../format';

const METRIC_KINDS: Array<{ id: RankingKind; label: string; hint: string }> = [
  { id: 'turnover', label: '成交额', hint: '名义成交额（价 × 量 × 乘数），非真实资金流水' },
  { id: 'oi_amount', label: '持仓额', hint: '名义持仓额（价 × 仓 × 乘数）' },
  { id: 'oi_change', label: '增减仓', hint: '持仓量较昨日变化（万手）' },
];

const ROW_LIMIT = 10;

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
  reloadKey: number;
}> = ({ overview, reloadKey }) => {
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
            gainers.map((r, i) => <ListRow key={r.symbol} row={r} rank={i + 1} kind="change" />)
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
            losers.map((r, i) => <ListRow key={r.symbol} row={r} rank={i + 1} kind="change" />)
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
                  className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold transition-all ${
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
            metricRows.map((r, i) => <ListRow key={r.symbol} row={r} rank={i + 1} kind={kind} />)
          )}
        </SectionCard>
      </div>
    </div>
  );
};
