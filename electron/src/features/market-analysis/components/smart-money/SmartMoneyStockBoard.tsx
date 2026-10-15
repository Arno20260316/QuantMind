/** 聪明钱 · 选股榜（池内复合分降序；行点击 → 个股终端浮窗）。
 *
 * 展示口径（细案 §6.2）：
 * - 分与分位是研究评分（中性紫色阶），不是涨跌；金额正负才用红绿；
 * - 柱分位为**方向对齐后**值（负向柱已取反，tooltip 说明）；缺失「—」不补 0；
 * - 「最新事件」为事实标签（龙虎榜/机构席位/大宗交易/机构调研），不是操作提示。
 */

import React, { useMemo, useState } from 'react';
import { Crosshair, ListFilter } from 'lucide-react';
import { Tooltip } from 'antd';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import type { SmartPillarMeta, SmartStockItem } from '../../services/smartMoneyService';
import {
  MISSING,
  directionHint,
  eventTypeLabel,
  fmtScore,
  fmtYiFromWan,
  fmtYiFromYuan,
  pillarLabel,
  pillarShort,
  scoreCls,
  signCls,
} from '../../smartMoneyVocabulary';

const EVENT_FILTER_ORDER = ['lhb', 'inst', 'block_trade', 'survey'] as const;

interface SmartMoneyStockBoardProps {
  items: SmartStockItem[] | null;
  pillars: SmartPillarMeta[] | null;
  total: number | null;
  poolSize: number | null;
  loading?: boolean;
  onPick?: (symbol: string) => void;
}

export const SmartMoneyStockBoard: React.FC<SmartMoneyStockBoardProps> = ({
  items,
  pillars,
  total,
  poolSize,
  loading,
  onPick,
}) => {
  const [eventFilter, setEventFilter] = useState<string | null>(null);

  const pillarList = pillars ?? [];

  const filtered = useMemo(() => {
    const list = items ?? [];
    const byEvent = eventFilter
      ? list.filter((it) => it.latest_event?.type === eventFilter)
      : list;
    // 服务端已按 score 降序；此处防御性重排（score 缺失沉底，绝不按 0 参与）
    return [...byEvent].sort((a, b) => {
      const sa = a.score === null ? -Infinity : a.score;
      const sb = b.score === null ? -Infinity : b.score;
      return sb - sa;
    });
  }, [items, eventFilter]);

  return (
    <SectionCard
      className="xl:col-span-2"
      title={
        <span className="flex items-center gap-1.5">
          <Crosshair className="w-3.5 h-3.5 text-purple-600" />
          聪明钱选股榜（池内复合分降序）
          <span className="text-[10px] font-normal text-slate-400">
            柱分位=池内截面 0–100（负向柱已按历史方向取反）· 缺失「{MISSING}」
          </span>
        </span>
      }
      extra={
        <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
          事件池 {poolSize === null ? MISSING : poolSize} 只 · 共 {total === null ? MISSING : total} 只
        </span>
      }
    >
      {loading && !items?.length ? (
        <EmptyHint loading />
      ) : filtered.length ? (
        <div className="flex flex-col gap-2 min-w-0">
          {/* 事件类型筛选（事实标签过滤，非强弱排序） */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="flex items-center gap-1 text-[9px] text-slate-400 font-bold">
              <ListFilter className="w-3 h-3" />
              最新事件
            </span>
            <button
              type="button"
              onClick={() => setEventFilter(null)}
              className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border transition-colors ${
                eventFilter === null
                  ? 'bg-purple-600 text-white border-purple-600'
                  : 'bg-white text-slate-500 border-slate-200 hover:border-purple-300'
              }`}
            >
              全部
            </button>
            {EVENT_FILTER_ORDER.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setEventFilter(eventFilter === t ? null : t)}
                className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border transition-colors ${
                  eventFilter === t
                    ? 'bg-purple-600 text-white border-purple-600'
                    : 'bg-white text-slate-500 border-slate-200 hover:border-purple-300'
                }`}
              >
                {eventTypeLabel(t)}
              </button>
            ))}
          </div>

          <div className="overflow-x-auto">
            <div className="min-w-[860px] flex flex-col">
              <div className="grid grid-cols-[24px_minmax(150px,1.2fr)_84px_88px_minmax(150px,1fr)_84px_84px_78px_64px_60px] gap-2 px-1 pb-1.5 text-[9px] font-extrabold text-slate-400 border-b border-slate-100 sticky top-0 bg-white/95 backdrop-blur z-[5]">
                <span>#</span>
                <span>标的 / 行业</span>
                <span>最新事件</span>
                <span className="text-right">机构净买·5日</span>
                <span>柱分位</span>
                <span className="text-right">龙虎榜·5日</span>
                <span className="text-right">两融·5日</span>
                <span className="text-right">北向Δ(季)</span>
                <span className="text-right">覆盖</span>
                <span className="text-right">分</span>
              </div>

              {filtered.map((it, i) => (
                <div
                  key={it.symbol}
                  onClick={() => onPick?.(it.symbol)}
                  title={`打开 ${it.symbol} 个股终端`}
                  className="grid grid-cols-[24px_minmax(150px,1.2fr)_84px_88px_minmax(150px,1fr)_84px_84px_78px_64px_60px] gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 items-center cursor-pointer hover:bg-purple-50/60 transition-colors"
                >
                  <span
                    className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold ${
                      i < 3 ? 'bg-purple-600 text-white' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {i + 1}
                  </span>

                  <div className="min-w-0 flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-bold text-slate-800 truncate">
                      {it.name || MISSING}
                    </span>
                    <span className="text-[9px] font-mono text-slate-400">{it.symbol}</span>
                    {it.industry && (
                      <span className="text-[9px] text-slate-400 bg-slate-50 border border-slate-100 rounded px-1">
                        {it.industry}
                      </span>
                    )}
                  </div>

                  <span className="text-[10px] text-slate-500 truncate">
                    {it.latest_event ? (
                      <>
                        {eventTypeLabel(it.latest_event.type)}
                        <span className="text-[9px] font-mono text-slate-400 ml-1">
                          {it.latest_event.date?.slice(5)}
                        </span>
                      </>
                    ) : (
                      MISSING
                    )}
                  </span>

                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(it.inst_net_5d)}`}>
                    {fmtYiFromYuan(it.inst_net_5d)}
                  </span>

                  <span className="flex items-center gap-0.5 flex-wrap">
                    {pillarList.map((p) => {
                      const v = it.pct?.[p.name];
                      const missing = v === null || v === undefined;
                      return (
                        <Tooltip
                          key={p.name}
                          title={`${p.label}：${
                            missing ? `${MISSING}（该柱当日无值）` : `分位 ${fmtScore(v)}`
                          } · 方向 ${directionHint(p.direction)}`}
                        >
                          <span
                            className={`px-1 rounded text-[9px] font-mono font-bold border whitespace-nowrap ${
                              missing
                                ? 'bg-slate-50 text-slate-300 border-slate-100'
                                : 'bg-purple-50 text-purple-700 border-purple-100'
                            }`}
                          >
                            {pillarShort(p.name)} {missing ? MISSING : Math.round(v)}
                          </span>
                        </Tooltip>
                      );
                    })}
                  </span>

                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(it.lhb_net_5d)}`}>
                    {fmtYiFromYuan(it.lhb_net_5d)}
                  </span>
                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(it.margin_net_5d)}`}>
                    {fmtYiFromWan(it.margin_net_5d)}
                  </span>
                  <span className={`text-right text-[10px] font-mono ${signCls(it.north_q_delta_pp)}`}>
                    {it.north_q_delta_pp !== null && it.north_q_delta_pp !== undefined
                      ? `${it.north_q_delta_pp > 0 ? '+' : ''}${fmtScore(it.north_q_delta_pp)}`
                      : MISSING}
                  </span>
                  <Tooltip
                    title={`有值柱数 ${it.covered ?? MISSING}/${it.n_pillars ?? MISSING}（缺失柱按 50 计入复合分，此处披露实际覆盖）`}
                  >
                    <span className="text-right text-[10px] font-mono text-slate-400">
                      {it.covered ?? MISSING}/{it.n_pillars ?? MISSING}
                    </span>
                  </Tooltip>
                  <span className={`text-right text-xs font-extrabold font-mono ${scoreCls(it.score)}`}>
                    {fmtScore(it.score)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {eventFilter && filtered.length === 0 && (
            <EmptyHint text={`最新事件为「${eventTypeLabel(eventFilter)}」的标的暂不在榜`} />
          )}
        </div>
      ) : (
        <EmptyHint text="当日事件池为空（无聪明钱事件触发）" />
      )}
    </SectionCard>
  );
};
