/** 港股聪明钱 · 席位优势榜 v2（细案 §4.2 面 1：窗口 × 排序 × 门槛 × 类型四筛，服务端计算）。
 *
 * 核心答问：「哪些机构更有优势」——主排序 = 方向技能 90% 下界（小样本自动沉底），
 * 并列示 技能 / 大钱技能（vw）/ 命中率 / 单笔均额 / 最近活跃；行尾微型条可视化下界。
 *
 * 自持取数（onMeta 供面 1 散点图共用同一批响应）；失败进错误条，绝不清空后
 * 伪装「无席位行为」。分类 chips 不含「南向」——南向为 hsgt_south 披露口径，
 * 不在 CCASS 事件仓内。缺失一律「—」绝不为 0。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Medal, RefreshCw } from 'lucide-react';
import { getHkSmSeats, type HkSmSeatItem, type HkSmSeatsResponse } from '../../services/api';
import { EmptyHint, FaceCard, PeriodChips } from '../../../market-analysis-shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  HIT_NOTE,
  LB_NOTE,
  MISSING,
  SAMPLE_NOTE,
  SEAT_MIN_OPTIONS,
  SEAT_SORT_OPTIONS,
  SEAT_WINDOW_OPTIONS,
  SKILL_NOTE,
  VW_NOTE,
  categoryLabel,
  fmtBp,
  fmtInt,
  fmtPct,
  fmtValueM,
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

const MIN_OPTION_CHIPS = SEAT_MIN_OPTIONS.map((o) => ({ id: String(o.id), label: o.label }));

const ROW_GRID =
  'grid grid-cols-[minmax(150px,1.5fr)_52px_58px_64px_58px_50px_58px_44px_56px] items-center gap-1.5';

/** 行尾微型条：以当前页最大 |下界| 归一，零轴居中（红=正/绿=负）；缺失「—」 */
function LbBar({ v, maxAbs }: { v: number | null; maxAbs: number }) {
  if (v === null || maxAbs <= 0) {
    return <span className="text-[9px] text-slate-300">{MISSING}</span>;
  }
  const half = (Math.min(Math.abs(v) / maxAbs, 1) * 100) / 2;
  const pos = v >= 0;
  return (
    <span className="relative block w-full h-[6px] rounded-full bg-slate-100/90 overflow-hidden">
      <span className="absolute left-1/2 top-0 h-full w-px bg-slate-300" />
      <span
        className={`absolute top-0 h-full ${pos ? 'left-1/2 bg-red-500/80' : 'right-1/2 bg-green-600/80'}`}
        style={{ width: `${half}%` }}
      />
    </span>
  );
}

function SeatRow({
  rank,
  item,
  maxAbsLb,
  onClick,
}: {
  rank: number;
  item: HkSmSeatItem;
  maxAbsLb: number;
  onClick?: () => void;
}) {
  const color = INST_CATEGORY_COLORS[item.category] || '#94a3b8';
  return (
    <div
      role={onClick ? 'button' : undefined}
      onClick={onClick}
      className={`${ROW_GRID} px-1 py-1.5 border-b border-slate-50 last:border-0 transition-colors ${
        onClick ? 'cursor-pointer hover:bg-purple-50/60' : 'hover:bg-slate-50/70'
      }`}
      title={`点击查看 ${item.participant_name ?? item.participant_id} 席位档案（三窗统计/月度战绩/活跃标的）`}
    >
      <span className="min-w-0 flex items-center gap-1.5">
        <span
          className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold flex-shrink-0 ${
            rank <= 3 ? 'bg-purple-600 text-white' : 'bg-slate-100 text-slate-500'
          }`}
        >
          {rank}
        </span>
        <span className="min-w-0 flex flex-col leading-tight">
          <span className="flex items-center gap-1 min-w-0">
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
          </span>
          <span className="text-[8px] font-mono text-slate-400 truncate">
            {item.participant_id} · {fmtInt(item.n_symbols)} 标的 · 最近 {item.last_event_date ?? MISSING}
          </span>
        </span>
      </span>
      <span
        className="text-right text-[10px] font-mono text-slate-500"
        title={`窗口内事件 ${fmtInt(item.n_events)}（增持 ${fmtInt(item.n_buy)} / 减持 ${fmtInt(item.n_sell)}）· 事后 10 日可测 ${fmtInt(item.n_exc10)}`}
      >
        {fmtInt(item.n_events)}
      </span>
      <span
        className={`text-right text-[10px] font-mono font-bold ${signCls(item.skill_bp)}`}
        title={`${SKILL_NOTE}\n技能 t=${item.skill_t ?? MISSING}`}
      >
        {fmtBp(item.skill_bp)}
      </span>
      <span
        className={`text-right text-[11px] font-mono font-extrabold ${signCls(item.lb_skill_bp)}`}
        title={LB_NOTE}
      >
        {fmtBp(item.lb_skill_bp)}
      </span>
      <span
        className={`text-right text-[10px] font-mono ${signCls(item.vw_skill_bp)}`}
        title={VW_NOTE}
      >
        {fmtBp(item.vw_skill_bp)}
      </span>
      <span
        className="text-right text-[10px] font-mono text-slate-600"
        title={`${HIT_NOTE}（本列为原始命中率，可测样本 ${fmtInt(item.n_exc10)}）`}
      >
        {fmtPct(item.hit, 0)}
      </span>
      <span
        className="text-right text-[10px] font-mono text-slate-500"
        title="单笔分量级 = 窗口内事件 |变动金额| 均值（百万 HKD / 亿自适应）"
      >
        {fmtValueM(item.avg_value_m)}
      </span>
      <span className="text-right text-[9px] font-mono text-slate-400">
        {item.last_event_date ? String(item.last_event_date).slice(5) : MISSING}
      </span>
      <LbBar v={item.lb_skill_bp} maxAbs={maxAbsLb} />
    </div>
  );
}

interface HkSmSeatBoardProps {
  /** true = 无卡壳（统一大框内的标签面） */
  bare?: boolean;
  /** 外部刷新信号（父级「刷新」按钮递增值触发重取） */
  refreshToken?: number;
  /** 行点击 → 席位档案抽屉 */
  onPickSeat?: (participantId: string) => void;
  /** 取数结算回调（面 1 散点图共用同一批响应；失败不回调） */
  onMeta?: (resp: HkSmSeatsResponse) => void;
  className?: string;
}

export const HkSmSeatBoard: React.FC<HkSmSeatBoardProps> = ({
  bare = false,
  refreshToken = 0,
  onPickSeat,
  onMeta,
  className = '',
}) => {
  const [windowId, setWindowId] = useState('all');
  const [sort, setSort] = useState('lb_skill');
  const [minEvents, setMinEvents] = useState('50');
  const [category, setCategory] = useState('all');
  const [data, setData] = useState<HkSmSeatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await getHkSmSeats({
        window: windowId,
        category: category === 'all' ? null : category,
        sort,
        minEvents: Number(minEvents),
        limit: 100,
      });
      if (my !== seq.current) return; // 过期响应丢弃（快速连点不看错批）
      setData(resp);
      onMeta?.(resp);
    } catch (e) {
      if (my !== seq.current) return;
      // 失败进错误条，绝不清空后伪装「无席位行为」
      setError(e instanceof Error ? e.message : '席位数据加载失败');
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [windowId, sort, minEvents, category, refreshToken, onMeta]);

  useEffect(() => {
    void load();
  }, [load]);

  const items = data?.items ?? [];
  const maxAbsLb = items.reduce(
    (m, it) => Math.max(m, it.lb_skill_bp !== null ? Math.abs(it.lb_skill_bp) : 0),
    0,
  );
  const sortLabel = SEAT_SORT_OPTIONS.find((o) => o.id === sort)?.label ?? sort;

  return (
    <FaceCard
      bare={bare}
      className={className}
      icon={<Medal className="w-3.5 h-3.5 text-purple-600" />}
      title={
        <span className="flex items-center gap-1.5">
          席位优势榜
          <span className="text-[10px] font-normal text-slate-400">
            历史战绩 · 描述性统计（买 +事后超额 / 卖 −事后超额）
          </span>
        </span>
      }
      extra={
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
          <PeriodChips options={SEAT_WINDOW_OPTIONS} value={windowId} onChange={setWindowId} />
          <PeriodChips options={SEAT_SORT_OPTIONS} value={sort} onChange={setSort} />
          <button
            type="button"
            onClick={() => void load()}
            className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-2 min-w-0">
        {/* 门槛 + 类型筛选 */}
        <div className="flex items-center gap-1.5 flex-wrap px-1">
          <PeriodChips options={MIN_OPTION_CHIPS} value={minEvents} onChange={setMinEvents} />
          <span className="w-px h-3.5 bg-slate-200 mx-0.5" />
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
          <span className="text-[8px] text-slate-400 ml-1" title={SAMPLE_NOTE}>
            南向不在 CCASS 事件仓；默认只看 n≥50
          </span>
        </div>

        {error && (
          <div className="mx-1 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5">
            <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
            <span className="text-[10px] text-amber-800 min-w-0 truncate">
              席位数据不可用：{error}
            </span>
            <button
              type="button"
              onClick={() => void load()}
              className="ml-auto shrink-0 text-[10px] font-bold text-amber-700 hover:text-amber-900"
            >
              重试
            </button>
          </div>
        )}

        {!data && !error ? (
          <EmptyHint loading={loading} text="席位数据不可用" />
        ) : (
          data && (
            <>
              <div className="max-h-[420px] overflow-auto custom-scrollbar rounded-lg border border-slate-100/80">
                <div className="min-w-[820px] flex flex-col">
                  <div
                    className={`${ROW_GRID} px-1 py-1 text-[9px] font-extrabold text-slate-400 border-b border-slate-100 sticky top-0 bg-white/95 backdrop-blur z-[5]`}
                  >
                    <span>席位</span>
                    <span className="text-right">事件</span>
                    <span className="text-right" title={SKILL_NOTE}>
                      技能bp
                    </span>
                    <span className="text-right" title={LB_NOTE}>
                      90%下界
                    </span>
                    <span className="text-right" title={VW_NOTE}>
                      大钱技能
                    </span>
                    <span className="text-right" title={HIT_NOTE}>
                      命中率
                    </span>
                    <span className="text-right" title="窗口内事件 |变动金额| 均值">
                      单笔均额
                    </span>
                    <span className="text-right">最近</span>
                    <span className="text-right" title="下界归一微型条（零轴居中）">
                      下界条
                    </span>
                  </div>
                  <div className={`flex flex-col ${loading ? 'opacity-60' : ''} transition-opacity`}>
                    {items.length === 0 && <EmptyHint text="当前筛选下无达标席位" />}
                    {items.map((it, i) => (
                      <SeatRow
                        key={it.participant_id}
                        rank={i + 1}
                        item={it}
                        maxAbsLb={maxAbsLb}
                        onClick={onPickSeat ? () => onPickSeat(it.participant_id) : undefined}
                      />
                    ))}
                  </div>
                </div>
              </div>
              <p className="text-[9px] text-slate-400 leading-relaxed">
                共 {fmtInt(data.total)} 席位达「事件数 ≥ {fmtInt(data.min_events)}」门槛（
                {windowLabel(data.window ?? windowId)} 窗，按{sortLabel}排序）。技能口径：{SKILL_NOTE}
                。下界口径：{LB_NOTE}。命中率口径：{HIT_NOTE}。
              </p>
            </>
          )
        )}
      </div>
    </FaceCard>
  );
};
