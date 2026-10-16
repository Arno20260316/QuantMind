/** 港股聪明钱 · 席位档案抽屉（细案 §4.6——「这个机构到底强在哪」的钻取面）。
 *
 * 头部=身份与活跃期；窗口 chips（20/60/全史）切换 6 张统计卡（该窗口行，不重新取数）；
 * 图 L 月度战绩（技能 bp 柱 + 命中率 % 线，双轴）；增/减持命中率对比条；
 * 活跃标的 top8（点击下钻个股档案）；最近事件 10 行（点击标的同样下钻）。
 * 查无 → 404 seat_not_found（如实呈现，不伪装空态）；缺失一律「—」。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Drawer } from 'antd';
import { Activity, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react';
import {
  HkSmartMoneyError,
  getHkSmSeatProfile,
  type HkSmSeatProfileResponse,
} from '../../services/api';
import { EmptyHint, PeriodChips } from '../../../market-analysis-shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  AXIS_LABEL,
  CHART_COLORS,
  SPLIT_LINE,
  SmartChart,
  signedColor,
  tipNum,
} from './charts';
import {
  HIT_NOTE,
  LB_NOTE,
  MISSING,
  PROFILE_WINDOW_NOTE,
  SEAT_WINDOW_OPTIONS,
  SHRINK_NOTE,
  SKILL_NOTE,
  VW_NOTE,
  categoryLabel,
  directionWord,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtPct,
  fmtValueM,
  kindLabel,
  signCls,
  windowLabel,
} from '../../hkSmartMoneyVocabulary';

interface HkSmSeatDrawerProps {
  participantId: string | null;
  open: boolean;
  onClose: () => void;
  /** 点击标的 → 打开个股档案抽屉（面板级联） */
  onPickSymbol: (symbol: string) => void;
}

const SYMBOL_COLS = 'grid grid-cols-[minmax(0,1fr)_40px_60px_60px_62px] items-center gap-1';
const EVENT_COLS = 'grid grid-cols-[46px_14px_minmax(0,1fr)_68px_56px_14px] items-center gap-1';

function StatCard({
  label,
  value,
  sub,
  cls,
  tip,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  cls?: string;
  tip?: string;
}) {
  return (
    <div
      className="rounded-xl border border-slate-100 bg-slate-50/60 px-2.5 py-2 flex flex-col gap-0.5 min-w-0"
      title={tip}
    >
      <span className="text-[9px] text-slate-400 whitespace-nowrap">{label}</span>
      <span className={`text-sm font-mono font-extrabold ${cls ?? 'text-slate-700'}`}>
        {value}
      </span>
      {sub && <span className="text-[8px] text-slate-400 truncate">{sub}</span>}
    </div>
  );
}

/** 命中率对比条（0–1 归一，中性紫——命中率是统计量不是涨跌） */
function HitBar({ label, v, n }: { label: string; v: number | null | undefined; n?: number | null }) {
  const pct = v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v);
  return (
    <div className="flex items-center gap-2 text-[9px]">
      <span className="w-14 text-slate-400 whitespace-nowrap">{label}</span>
      <span className="flex-1 h-[7px] rounded-full bg-slate-100 overflow-hidden">
        {pct !== null && (
          <span
            className="block h-full rounded-full"
            style={{ width: `${Math.max(0, Math.min(pct, 1)) * 100}%`, background: CHART_COLORS.score }}
          />
        )}
      </span>
      <span className="w-20 text-right font-mono text-slate-600 whitespace-nowrap">
        {fmtPct(v, 0)}
        {n != null ? <span className="text-slate-400"> n={fmtInt(n)}</span> : null}
      </span>
    </div>
  );
}

export const HkSmSeatDrawer: React.FC<HkSmSeatDrawerProps> = ({
  participantId,
  open,
  onClose,
  onPickSymbol,
}) => {
  const [data, setData] = useState<HkSmSeatProfileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [windowId, setWindowId] = useState('all');

  useEffect(() => {
    if (!open || !participantId) return;
    let alive = true;
    setLoading(true);
    setError(null);
    setData(null);
    setWindowId('all');
    getHkSmSeatProfile(participantId)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        if (e instanceof HkSmartMoneyError && e.notBuilt) {
          setError('未找到该席位（可能已不在事件仓；榜单刷新后重试）');
        } else {
          setError(e instanceof Error ? e.message : '席位档案加载失败');
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, participantId]);

  const row = data?.windows?.[windowId] ?? null;
  const p = data?.participant ?? null;

  /** 图 L：月度战绩（技能 bp 柱 + 命中率 % 线） */
  const monthlyOption = useMemo(() => {
    const rows = data?.monthly ?? [];
    if (!rows.length) return null;
    return {
      grid: { left: 46, right: 42, top: 22, bottom: 20 },
      legend: {
        top: 0,
        left: 0,
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { fontSize: 9, color: CHART_COLORS.neutral },
      },
      tooltip: {
        trigger: 'axis',
        textStyle: { fontSize: 10 },
        formatter: (
          ps: Array<{ seriesName?: string; value?: unknown; name?: string; dataIndex?: number }>,
        ) => {
          const idx = ps[0]?.dataIndex ?? 0;
          const r = rows[idx];
          return [
            `${r?.month ?? MISSING} · 事件 ${fmtInt(r?.n)}`,
            ...ps.map((q) => {
              const v = q.value as number | null;
              return `${q.seriesName ?? ''}：${
                q.seriesName === '命中率'
                  ? tipNum(v, (n) => fmtPct(n, 0))
                  : tipNum(v, (n) => `${fmtBp(n)} bp`)
              }`;
            }),
          ].join('<br/>');
        },
      },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.month ?? '').slice(2)), // YY-MM
        axisLabel: { ...AXIS_LABEL, interval: Math.max(0, Math.floor(rows.length / 10) - 1) },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value',
          axisLabel: AXIS_LABEL,
          splitLine: SPLIT_LINE,
          name: 'bp',
          nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
        },
        {
          type: 'value',
          axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${fmtNum(v * 100, 0)}%` },
          splitLine: { show: false },
          min: 0,
          max: 1,
        },
      ],
      series: [
        {
          name: '方向技能',
          type: 'bar',
          barMaxWidth: 14,
          data: rows.map((r) => ({
            value: r.skill_bp,
            itemStyle: {
              color: r.skill_bp === null ? CHART_COLORS.axis : signedColor(r.skill_bp),
              borderRadius: [2, 2, 0, 0],
            },
          })),
        },
        {
          name: '命中率',
          type: 'line',
          yAxisIndex: 1,
          symbolSize: 4,
          connectNulls: false,
          lineStyle: { width: 1.2, color: CHART_COLORS.score },
          itemStyle: { color: CHART_COLORS.score },
          data: rows.map((r) => r.hit),
        },
      ],
    };
  }, [data]);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={600}
      title={
        <div className="flex items-center gap-2 flex-wrap pr-6">
          <span className="text-sm font-extrabold text-slate-800">
            {p?.participant_name ?? participantId ?? ''}
          </span>
          <span className="text-[10px] font-mono text-slate-400">
            {p?.participant_id ?? participantId}
          </span>
          {p && (
            <>
              <span
                className="px-1.5 py-px rounded-full text-[9px] font-extrabold"
                style={{
                  background: `${INST_CATEGORY_COLORS[p.category] || '#94a3b8'}1a`,
                  color: INST_CATEGORY_COLORS[p.category] || '#94a3b8',
                }}
              >
                {categoryLabel(p.category, p.category_label)}
              </span>
              <span className="text-[9px] text-slate-400">{kindLabel(p.kind)}</span>
            </>
          )}
        </div>
      }
      extra={
        data ? (
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            首现 {p?.first_event_date ?? MISSING} · 最近 {p?.last_event_date ?? MISSING}
          </span>
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

        {data && p && (
          <>
            {/* 窗口切换 + 统计卡 */}
            <div className="flex items-center gap-2 flex-wrap">
              <PeriodChips options={SEAT_WINDOW_OPTIONS} value={windowId} onChange={setWindowId} />
              <span className="text-[9px] text-slate-400">{PROFILE_WINDOW_NOTE}</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              <StatCard
                label={`事件（${windowLabel(windowId)}窗）`}
                value={fmtInt(row?.n_events)}
                sub={`增持 ${fmtInt(row?.n_buy)} / 减持 ${fmtInt(row?.n_sell)} · 可测 ${fmtInt(row?.n_exc10)}`}
              />
              <StatCard
                label="方向技能"
                value={`${fmtBp(row?.skill_bp)}`}
                cls={signCls(row?.skill_bp)}
                sub={`t=${fmtNum(row?.skill_t, 2)}`}
                tip={SKILL_NOTE}
              />
              <StatCard
                label="90% 下界"
                value={`${fmtBp(row?.lb_skill_bp)}`}
                cls={signCls(row?.lb_skill_bp)}
                tip={LB_NOTE}
              />
              <StatCard
                label="大钱技能（vw）"
                value={`${fmtBp(row?.vw_skill_bp)}`}
                cls={signCls(row?.vw_skill_bp)}
                tip={VW_NOTE}
              />
              <StatCard
                label="历史胜率（原始/收缩）"
                value={`${fmtPct(row?.hit, 0)} / ${fmtPct(row?.shrunk_hit, 0)}`}
                sub={`收缩 w=n/(n+20)`}
                tip={`${HIT_NOTE}；${SHRINK_NOTE}`}
              />
              <StatCard
                label="单笔均额"
                value={fmtValueM(row?.avg_value_m)}
                sub={`覆盖 ${fmtInt(row?.n_symbols)} 标的`}
                tip="窗口内事件 |变动金额| 均值（百万 HKD / 亿自适应）"
              />
            </div>

            {/* 增/减持命中率对比条 */}
            <div className="rounded-xl border border-slate-100 bg-white px-3 py-2 flex flex-col gap-1.5">
              <span className="text-[10px] font-extrabold text-slate-700" title={HIT_NOTE}>
                增 / 减持命中率对比（{windowLabel(windowId)}窗）
              </span>
              <HitBar label="增持后" v={row?.buy_hit} />
              <HitBar label="减持后" v={row?.sell_hit} />
              <span className="text-[8px] text-slate-400">
                「减持后命中」= 事件后 10 日下跌（跑输）占比——方向命中即下跌。
              </span>
            </div>

            {/* 图 L 月度战绩 */}
            <div className="flex flex-col gap-1.5">
              <span className="flex items-center gap-1.5 text-[10px] font-extrabold text-slate-700">
                <Activity className="w-3.5 h-3.5 text-purple-600" />
                月度战绩
                <span className="text-[9px] font-normal text-slate-400">
                  柱 = 方向技能（bp）· 线 = 命中率
                </span>
              </span>
              <SmartChart option={monthlyOption} height={190} emptyText="月度数据不可用" />
            </div>

            {/* 活跃标的 top8 */}
            <div className="flex flex-col gap-1.5">
              <span className="text-[10px] font-extrabold text-slate-700">
                活跃标的（事件最多前 {fmtInt(data.top_symbols?.length ?? null)}）
              </span>
              <div
                className={`${SYMBOL_COLS} px-1 pb-1 border-b border-slate-100 text-[8px] font-extrabold text-slate-400`}
              >
                <span>标的（点击下钻）</span>
                <span className="text-right">n</span>
                <span className="text-right" title={SKILL_NOTE}>
                  技能
                </span>
                <span className="text-right" title={VW_NOTE}>
                  大钱
                </span>
                <span className="text-right">最近</span>
              </div>
              {(data.top_symbols ?? []).length === 0 && <EmptyHint text="无活跃标的" />}
              {(data.top_symbols ?? []).map((s) => (
                <div
                  key={s.symbol}
                  role="button"
                  onClick={() => onPickSymbol(s.symbol)}
                  className={`${SYMBOL_COLS} px-1 py-1 border-b border-slate-50 last:border-0 cursor-pointer hover:bg-purple-50/60 transition-colors`}
                  title={`查看 ${s.name || s.symbol} 个股档案`}
                >
                  <span className="min-w-0 flex items-baseline gap-1.5">
                    <span className="text-[10px] font-bold text-slate-700 truncate">
                      {s.name || s.symbol}
                    </span>
                    <span className="text-[8px] font-mono text-slate-400">{s.symbol}</span>
                  </span>
                  <span className="text-right text-[9px] font-mono text-slate-500">
                    {fmtInt(s.n)}
                  </span>
                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(s.skill_bp)}`}>
                    {fmtBp(s.skill_bp)}
                  </span>
                  <span className={`text-right text-[10px] font-mono ${signCls(s.vw_skill_bp)}`}>
                    {fmtBp(s.vw_skill_bp)}
                  </span>
                  <span className="text-right text-[9px] font-mono text-slate-400">
                    {s.last_event_date ?? MISSING}
                  </span>
                </div>
              ))}
            </div>

            {/* 最近事件 */}
            <div className="flex flex-col gap-1.5">
              <span className="text-[10px] font-extrabold text-slate-700">最近事件（最新在前）</span>
              <div
                className={`${EVENT_COLS} px-1 pb-1 border-b border-slate-100 text-[8px] font-extrabold text-slate-400`}
              >
                <span>日期</span>
                <span />
                <span>标的</span>
                <span className="text-right">变动金额</span>
                <span className="text-right" title="事件后 10 日市场调整超额（封存展示列）">
                  10日超额
                </span>
                <span />
              </div>
              {(data.recent_events ?? []).length === 0 && <EmptyHint text="无最近事件" />}
              {(data.recent_events ?? []).map((e, i) => (
                <div
                  key={`${e.date}-${e.symbol}-${i}`}
                  role="button"
                  onClick={() => onPickSymbol(e.symbol)}
                  className={`${EVENT_COLS} px-1 py-1 border-b border-slate-50 last:border-0 cursor-pointer hover:bg-purple-50/60 transition-colors`}
                  title={`${directionWord(e.direction)} · ${e.name || e.symbol} · 变动占比 ${fmtPct(e.delta_pct, 2)} · 价 ${fmtNum(e.price, 2)}`}
                >
                  <span className="flex items-center gap-0.5">
                    {e.first_of_episode && (
                      <span
                        className="w-1.5 h-1.5 rounded-full bg-purple-500 flex-shrink-0"
                        title="episode 首日（统计口径）"
                      />
                    )}
                    <span className="text-[9px] font-mono text-slate-500">
                      {e.date ? e.date.slice(5) : MISSING}
                    </span>
                  </span>
                  <span className="grid place-items-center">
                    {e.direction === 1 ? (
                      <TrendingUp className="w-3 h-3 text-red-500" />
                    ) : e.direction === -1 ? (
                      <TrendingDown className="w-3 h-3 text-green-600" />
                    ) : (
                      <span className="text-[9px] text-slate-300">·</span>
                    )}
                  </span>
                  <span className="min-w-0 flex items-baseline gap-1.5">
                    <span className="text-[10px] font-bold text-slate-700 truncate">
                      {e.name || e.symbol}
                    </span>
                    <span className="text-[8px] font-mono text-slate-400">{e.symbol}</span>
                  </span>
                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(e.value_yi)}`}>
                    {e.value_yi != null ? `${fmtNum(e.value_yi, 2)}亿` : MISSING}
                  </span>
                  <span className={`text-right text-[10px] font-mono font-bold ${signCls(e.fwd_exc_10_bp)}`}>
                    {fmtBp(e.fwd_exc_10_bp)}
                  </span>
                  <span className="grid place-items-center" title="过户嫌疑：只标记不计入统计">
                    {e.transfer_suspect && <ShieldAlert className="w-3 h-3 text-amber-500" />}
                  </span>
                </div>
              ))}
            </div>

            <p className="text-[9px] text-slate-400 leading-relaxed">
              {SKILL_NOTE}。{PROFILE_WINDOW_NOTE}。{LB_NOTE}。
            </p>
          </>
        )}
      </div>
    </Drawer>
  );
};
