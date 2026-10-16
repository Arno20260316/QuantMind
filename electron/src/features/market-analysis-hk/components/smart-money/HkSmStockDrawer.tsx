/** 港股聪明钱 · 个股档案抽屉（细案 §6：时间线 + 权益结构 + 统计 + 打开个股终端）。
 *
 * 任意港股代码都可打开：无事件时后端返回 200 全零/空清单（有什么给什么）——
 * 空态不是错误。方向为席位行为（增持/减持）；事后超额为封存展示列。
 */

import React, { useEffect, useState } from 'react';
import { Drawer } from 'antd';
import { ExternalLink, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react';
import { getHkSmStock, type HkSmStockResponse, type HkSmStockStats, type HkSmTimelineRow } from '../../services/api';
import { EmptyHint } from '../shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  EPISODE_NOTE,
  FWD_NOTE,
  HIT_NOTE,
  MISSING,
  SUSPECT_NOTE,
  categoryLabel,
  directionWord,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtShares,
  fmtSigned,
  signCls,
} from '../../hkSmartMoneyVocabulary';

interface HkSmStockDrawerProps {
  symbol: string | null;
  open: boolean;
  onClose: () => void;
  onOpenTerminal?: (symbol: string) => void;
}

const TIMELINE_COLS =
  'grid grid-cols-[40px_14px_minmax(0,1fr)_58px_48px_56px_14px] items-center gap-1';

function TimelineRow({ row }: { row: HkSmTimelineRow }) {
  const color = INST_CATEGORY_COLORS[row.category] || '#94a3b8';
  const dv = row.delta_value;
  const dvYi = dv != null ? `${fmtSigned(dv / 1e8, 2)}亿` : MISSING;
  return (
    <div
      className={`${TIMELINE_COLS} px-1 py-1 border-b border-slate-50 last:border-0 hover:bg-slate-50/70 transition-colors`}
      title={`${directionWord(row.direction)} ${row.delta_qty != null ? fmtShares(Math.abs(row.delta_qty)) : MISSING} · 占其持仓 ${fmtPct(row.delta_pct, 2)} · 价 ${fmtNum(row.price, 2)} · 持仓市值 ${fmtNum(row.value_yi, 2)}亿`}
    >
      <span className="flex items-center gap-0.5">
        {row.first_of_episode && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-purple-500 flex-shrink-0"
            title={`episode 首日（统计口径）：${EPISODE_NOTE}`}
          />
        )}
        <span className="text-[9px] font-mono text-slate-500">{row.date.slice(5)}</span>
      </span>
      <span className="grid place-items-center">
        {row.direction === 1 ? (
          <TrendingUp className="w-3 h-3 text-red-500" />
        ) : row.direction === -1 ? (
          <TrendingDown className="w-3 h-3 text-green-600" />
        ) : (
          <span className="text-[9px] text-slate-300">·</span>
        )}
      </span>
      <span className="min-w-0 flex items-center gap-1.5">
        <span className="text-[10px] font-bold text-slate-700 truncate">
          {row.participant_name ?? row.participant_id}
        </span>
        <span
          className="px-1 py-px rounded text-[8px] font-bold flex-shrink-0"
          style={{ background: `${color}1a`, color }}
        >
          {categoryLabel(row.category, row.category_label)}
        </span>
      </span>
      <span className={`text-right text-[10px] font-mono font-bold ${signCls(dv)}`}>{dvYi}</span>
      <span
        className="text-right text-[9px] font-mono text-slate-500"
        title="变动金额占当日成交额比"
      >
        {fmtPct(row.pct_of_amount, 1)}
      </span>
      <span
        className={`text-right text-[10px] font-mono font-bold ${signCls(row.fwd_exc_10_bp)}`}
        title={FWD_NOTE}
      >
        {fmtBp(row.fwd_exc_10_bp)}
      </span>
      <span className="grid place-items-center" title={SUSPECT_NOTE}>
        {row.transfer_suspect && <ShieldAlert className="w-3 h-3 text-amber-500" />}
      </span>
    </div>
  );
}

function StatsStrip({ stats }: { stats: HkSmStockStats }) {
  const cats = stats.by_category ?? [];
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10px]">
      <div>
        <div className="text-[9px] text-slate-400">事件（增 / 减）</div>
        <div className="font-mono font-extrabold text-slate-700">
          {fmtInt(stats.events)}{' '}
          <span className="font-bold text-slate-400">
            （增 {fmtInt(stats.n_buy)} / 减 {fmtInt(stats.n_sell)}）
          </span>
        </div>
      </div>
      <div>
        <div className="text-[9px] text-slate-400">净额（窗口内事件净额）</div>
        <div className={`font-mono font-extrabold ${signCls(stats.net_value_yi)}`}>
          {fmtSigned(stats.net_value_yi, 2)} 亿
        </div>
      </div>
      <div>
        <div className="text-[9px] text-slate-400" title={FWD_NOTE}>
          平均事后超额（10 日）
        </div>
        <div className={`font-mono font-extrabold ${signCls(stats.avg_exc_10_bp)}`}>
          {fmtBp(stats.avg_exc_10_bp)} bp
        </div>
      </div>
      <div>
        <div className="text-[9px] text-slate-400" title={HIT_NOTE}>
          历史胜率（增持 / 减持）
        </div>
        <div className="font-mono font-extrabold text-slate-700">
          {fmtPct(stats.buy_hit, 0)} / {fmtPct(stats.sell_hit, 0)}
        </div>
      </div>
      {cats.length > 0 && (
        <div className="col-span-2 border-t border-slate-100 pt-1.5 flex flex-col gap-0.5">
          <div className="flex items-center gap-2 text-[8px] text-slate-400">
            <span className="w-20" />
            <span>增/减</span>
            <span className="ml-auto">净额</span>
            <span className="w-16 text-right">增持后超额</span>
            <span className="w-16 text-right">减持后超额</span>
          </div>
          {cats.map((r) => (
            <div key={r.category} className="flex items-center gap-2 text-[9px] font-mono">
              <span
                className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                style={{ background: INST_CATEGORY_COLORS[r.category] || '#94a3b8' }}
              />
              <span className="font-bold text-slate-600 w-[74px] truncate">
                {categoryLabel(r.category, r.category_label)}
              </span>
              <span className="text-slate-400">
                {fmtInt(r.n_buy)}/{fmtInt(r.n_sell)}
              </span>
              <span className={`ml-auto ${signCls(r.net_value_yi)}`}>
                {fmtSigned(r.net_value_yi, 1)}亿
              </span>
              <span className={`w-16 text-right ${signCls(r.avg_exc_10_bp_buy)}`}>
                {fmtBp(r.avg_exc_10_bp_buy)}
              </span>
              <span className={`w-16 text-right ${signCls(r.avg_exc_10_bp_sell)}`}>
                {fmtBp(r.avg_exc_10_bp_sell)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const HkSmStockDrawer: React.FC<HkSmStockDrawerProps> = ({
  symbol,
  open,
  onClose,
  onOpenTerminal,
}) => {
  const [data, setData] = useState<HkSmStockResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !symbol) return;
    let alive = true;
    setLoading(true);
    setError(null);
    setData(null);
    getHkSmStock(symbol, 60)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : '个股档案加载失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, symbol]);

  const stats = data?.stats ?? null;
  const timeline = data?.timeline ?? [];
  const holdings = data?.holdings ?? [];

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={560}
      title={
        <div className="flex items-center gap-2 flex-wrap pr-6">
          <span className="text-sm font-extrabold text-slate-800">
            {data?.name || symbol || ''}
          </span>
          <span className="text-[10px] font-mono text-slate-400">{data?.symbol ?? symbol}</span>
          <span className="text-[9px] text-slate-400">
            近 {data?.window?.days != null ? fmtInt(data.window.days) : MISSING} 交易日事件档案
          </span>
        </div>
      }
      extra={
        onOpenTerminal && symbol ? (
          <button
            type="button"
            onClick={() => {
              onOpenTerminal(data?.symbol ?? symbol);
              onClose();
            }}
            className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold text-purple-700 bg-purple-50 border border-purple-100 hover:bg-purple-100 transition-colors"
          >
            <ExternalLink className="w-3 h-3" />
            打开个股终端
          </button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3">
        {loading && !data && <EmptyHint loading text="加载中…" />}
        {error && (
          <div className="rounded-xl bg-red-50/80 border border-red-200 px-3 py-2 text-[11px] text-red-700 font-bold">
            {error}
          </div>
        )}
        {data && (
          <>
            {stats && <StatsStrip stats={stats} />}

            <div className="flex flex-col gap-1.5">
              <div className="text-[10px] font-extrabold text-slate-700">
                权益结构（CCASS 席位 · 最近披露）
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {holdings.length === 0 && (
                  <span className="text-[10px] text-slate-400">无披露持仓结构</span>
                )}
                {holdings.map((h) => {
                  const color = INST_CATEGORY_COLORS[h.category] || '#94a3b8';
                  return (
                    <span
                      key={h.category}
                      className="flex items-center gap-1 px-2 py-1 rounded-full text-[9px] font-bold border"
                      style={{ background: `${color}14`, borderColor: `${color}33`, color }}
                    >
                      {categoryLabel(h.category, h.category_label)} {fmtShares(h.qty_sum)}
                      {h.n_seats != null ? ` · ${fmtInt(h.n_seats)} 席` : ''}
                      <span className="font-mono text-slate-400">{h.date ?? ''}</span>
                    </span>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <div className="text-[10px] font-extrabold text-slate-700">
                事件时间线（最近在前）
              </div>
              <div className={`${TIMELINE_COLS} px-1 pb-1 border-b border-slate-100 text-[8px] font-extrabold text-slate-400`}>
                <span>日期</span>
                <span />
                <span>席位 · 类型</span>
                <span className="text-right">变动金额</span>
                <span className="text-right">占成交</span>
                <span className="text-right">10日超额</span>
                <span />
              </div>
              {timeline.length === 0 && (
                <EmptyHint text="近窗无席位事件（未被前 50 席位大幅增减持，或已过滤过户嫌疑）" />
              )}
              <div className="max-h-[320px] overflow-y-auto">
                {timeline.map((row, i) => (
                  <TimelineRow key={`${row.date}-${row.participant_id}-${i}`} row={row} />
                ))}
              </div>
            </div>

            <p className="text-[9px] text-slate-400 leading-relaxed">
              {FWD_NOTE}；{EPISODE_NOTE}；{SUSPECT_NOTE}。方向词为席位行为（增持/减持），
              历史胜率为事后统计，均不构成交易指令。
            </p>
          </>
        )}
      </div>
    </Drawer>
  );
};
