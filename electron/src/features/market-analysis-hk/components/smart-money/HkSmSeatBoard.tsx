/** 港股聪明钱 · 席位榜（细案 §6：窗口/分类/排序三重筛选，服务端筛选分页）。
 *
 * 自持取数（对齐 A 股席位榜 idiom）：失败进错误条，绝不清空后伪装「无席位行为」。
 * 分类 chips 不含「南向」——南向为 hsgt_south 披露口径，不在 CCASS 事件仓内。
 * 行内：事件数=当前窗原值；胜率/超额=收缩估计（w=n/(n+20)，tooltip 说明）；稳定性点
 * = 前后半样本平均超额同号。
 */

import React, { useEffect, useState } from 'react';
import { Medal, RefreshCw } from 'lucide-react';
import { getHkSmSeats, type HkSmSeatItem, type HkSmSeatsResponse } from '../../services/api';
import { EmptyHint, PeriodChips, SectionCard, fmtInt } from '../shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  HIT_NOTE,
  MISSING,
  SEAT_SORT_OPTIONS,
  SEAT_WINDOW_OPTIONS,
  SHRINK_NOTE,
  categoryLabel,
  fmtBp,
  fmtNum,
  fmtPct,
  kindLabel,
  signCls,
  windowLabel,
} from '../../hkSmartMoneyVocabulary';

const CATEGORY_CHIPS: Array<{ id: string; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'cn_broker', label: '内资·券商' },
  { id: 'hk', label: '港资' },
  { id: 'us_eu', label: '外资·欧美' },
  { id: 'apac', label: '外资·亚太' },
  { id: 'other', label: '其他' },
];

function SeatRow({ rank, item }: { rank: number; item: HkSmSeatItem }) {
  const color = INST_CATEGORY_COLORS[item.category] || '#94a3b8';
  return (
    <div
      className="flex items-center gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 hover:bg-slate-50/70 transition-colors"
      title={`t=${fmtNum(item.t, 2)}（相对中性）· 平均超额 ${fmtBp(item.avg_exc_bp)}bp · 事件后 10 日样本 ${fmtInt(item.n_exc10)}`}
    >
      <span
        className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold flex-shrink-0 ${
          rank <= 3 ? 'bg-purple-600 text-white' : 'bg-slate-100 text-slate-500'
        }`}
      >
        {rank}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-[11px] font-bold text-slate-800 truncate">
            {item.participant_name ?? item.participant_id}
          </span>
          <span
            className="px-1 py-px rounded text-[8px] font-bold flex-shrink-0"
            style={{ background: `${color}1a`, color }}
          >
            {categoryLabel(item.category, item.category_label)}
          </span>
          <span className="text-[8px] text-slate-400 flex-shrink-0">{kindLabel(item.kind)}</span>
        </div>
        <div className="text-[8px] font-mono text-slate-400 truncate">
          {item.participant_id} · 最近 {item.last_event_date ?? MISSING}
        </div>
      </div>
      <span
        className="w-11 text-right text-[10px] font-mono text-slate-500 flex-shrink-0"
        title={`增持 ${fmtInt(item.n_buy)} / 减持 ${fmtInt(item.n_sell)}`}
      >
        {fmtInt(item.n_events)}
      </span>
      <span
        className="w-12 text-right text-[11px] font-mono font-extrabold text-slate-700 flex-shrink-0"
        title={SHRINK_NOTE}
      >
        {fmtPct(item.shrunk_hit, 0)}
      </span>
      <span
        className={`w-16 text-right text-[11px] font-mono font-extrabold flex-shrink-0 ${signCls(item.shrunk_exc_bp)}`}
        title={SHRINK_NOTE}
      >
        {fmtBp(item.shrunk_exc_bp)}
      </span>
      <span
        className="w-3 flex-shrink-0 grid place-items-center"
        title={
          item.stability
            ? '稳定性：前后半样本平均超额同号（样本内一致）'
            : '稳定性：前后半样本平均超额异号（样本内不稳定）'
        }
      >
        <span
          className={`block w-2 h-2 rounded-full ${item.stability ? 'bg-emerald-500' : 'bg-slate-300'}`}
        />
      </span>
    </div>
  );
}

export const HkSmSeatBoard: React.FC = () => {
  const [windowId, setWindowId] = useState('60');
  const [category, setCategory] = useState('all');
  const [sort, setSort] = useState('shrunk_hit');
  const [data, setData] = useState<HkSmSeatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getHkSmSeats({
      window: windowId,
      category: category === 'all' ? null : category,
      sort,
      limit: 30,
      minEvents: 5,
    })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        // 失败进错误条，绝不清空后伪装「无席位行为」
        setError(e instanceof Error ? e.message : '席位数据加载失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [windowId, category, sort, refreshToken]);

  const items = data?.items ?? [];

  return (
    <SectionCard
      icon={<Medal className="w-3.5 h-3.5 text-purple-600" />}
      title="席位榜 · 事件后表现排行"
      extra={
        <button
          type="button"
          onClick={() => setRefreshToken((n) => n + 1)}
          className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      }
    >
      <div className="flex items-center gap-2 flex-wrap">
        <PeriodChips options={SEAT_WINDOW_OPTIONS} value={windowId} onChange={setWindowId} />
        <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
          {SEAT_SORT_OPTIONS.map((o) => (
            <button
              key={o.id}
              type="button"
              onClick={() => setSort(o.id)}
              className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold transition-all ${
                sort === o.id
                  ? 'bg-white text-purple-700 shadow-2xs border border-purple-200'
                  : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-1 flex-wrap">
        {CATEGORY_CHIPS.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => setCategory(c.id)}
            className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold transition-all ${
              category === c.id
                ? 'bg-purple-600 text-white shadow-sm'
                : 'bg-slate-100 text-slate-500 hover:text-slate-800 hover:bg-slate-200'
            }`}
          >
            {c.label}
          </button>
        ))}
        <span className="text-[8px] text-slate-400 ml-1">南向不在 CCASS 事件仓（无席位事件）</span>
      </div>

      {error && (
        <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
          席位数据不可用：{error}
        </div>
      )}

      <div className="flex items-center gap-2 px-1 pb-1 border-b border-slate-100 text-[9px] font-extrabold text-slate-400">
        <span className="w-5" />
        <span className="flex-1">席位</span>
        <span className="w-11 text-right">事件</span>
        <span className="w-12 text-right" title={HIT_NOTE}>
          历史胜率
        </span>
        <span className="w-16 text-right" title={SHRINK_NOTE}>
          平均超额
        </span>
        <span className="w-3 text-center" title="稳定性：前后半样本平均超额同号">
          稳
        </span>
      </div>

      {!data && !error && <EmptyHint loading={loading} text="席位数据不可用" />}
      {data && (
        <div className="max-h-[430px] overflow-y-auto">
          {items.length === 0 && <EmptyHint text="当前筛选下无达标席位" />}
          {items.map((it, i) => (
            <SeatRow key={it.participant_id} rank={i + 1} item={it} />
          ))}
        </div>
      )}

      {data && (
        <p className="text-[9px] text-slate-400 leading-relaxed">
          共 {fmtInt(data.total)} 席位达「事件数 ≥ {fmtInt(data.min_events)}」门槛（{windowLabel(windowId)}
          窗，按{SEAT_SORT_OPTIONS.find((o) => o.id === sort)?.label ?? sort}排序）。胜率与超额为
          收缩估计：{SHRINK_NOTE}。胜负口径：{HIT_NOTE}。
        </p>
      )}
    </SectionCard>
  );
};
