/** 港股聪明钱 · 面 2「类型效力」（细案 §4.3）。
 *
 * 公示条：本期过闸裁定（N 信号 × h5/h10/h20 全未过闸 = evidence-only 数据结论）；
 * 图 D IC 热力图：24 行（6 类型 × 4 变体）× h5/h10/h20，中性灰-紫发散色阶
 *   （负=灰/正=紫，IC 是统计量绝不套红绿），点击单元格选中联动图 E 与表 F；
 * 图 E q1→q5 分层柱：选中信号 h10 五档事后超额（bp，收益类量走红绿），
 *   双过闸判定（入柱/尾部同号）徽标挂图；
 * 表 F 24 行检定表（点击行 = 展开裁定明细 + 选中该信号）。
 * 缺失一律「—」绝不为 0；「未过闸」为真裁定不是缺数。
 */

import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Grid3X3, Table2 } from 'lucide-react';
import type { HkSmSignal, HkSmTypesResponse } from '../../services/api';
import { EmptyHint, FaceCard } from '../../../market-analysis-shared/ui';
import { INST_CATEGORY_COLORS } from '../InstitutionalTrendChart';
import {
  AXIS_LABEL,
  CHART_COLORS,
  SPLIT_LINE,
  SmartChart,
  signedColor,
  tipNum,
  type ChartEventParams,
} from './charts';
import {
  ADMIT_NOTE,
  CATEGORY_ORDER,
  HIT_NOTE,
  MISSING,
  categoryLabel,
  directionWord,
  fmtBp,
  fmtInt,
  fmtNum,
  fmtPct,
  icCls,
  signCls,
} from '../../hkSmartMoneyVocabulary';

interface HkSmTypeBoardProps {
  data: HkSmTypesResponse | null;
  loading: boolean;
  error: string | null;
  /** true = 无卡壳（统一大框内的标签面） */
  bare?: boolean;
}

const HORIZONS = ['5', '10', '20'] as const;

const GRID_COLS =
  'grid grid-cols-[minmax(140px,1.5fr)_50px_50px_50px_62px_54px_54px_72px] items-center gap-1';

const sigKey = (s: HkSmSignal) => `${s.category}-${s.variant}`;

/** 图 D：IC 热力图（中性灰-紫发散色阶；点击格 → 选中信号） */
function buildHeatOption(signals: HkSmSignal[], selectedKey: string | null) {
  if (!signals.length) return null;
  const rows = signals.map(
    (s) => `${categoryLabel(s.category, s.category_label)} · ${s.variant_label ?? s.variant}`,
  );
  const pts: Array<[number, number, number]> = [];
  let maxAbs = 0;
  signals.forEach((s, yi) => {
    HORIZONS.forEach((h, xi) => {
      const ic = s.results?.[h]?.rank_ic;
      if (ic === null || ic === undefined || Number.isNaN(Number(ic))) return; // 缺失留空，不补 0
      maxAbs = Math.max(maxAbs, Math.abs(Number(ic)));
      pts.push([xi, yi, Number(ic)]);
    });
  });
  if (!pts.length) return null;
  const bound = Math.max(maxAbs, 0.005);
  const selYi = signals.findIndex((s) => sigKey(s) === selectedKey);
  return {
    grid: { left: 128, right: 10, top: 8, bottom: 36 },
    tooltip: {
      textStyle: { fontSize: 10 },
      formatter: (p: { value?: unknown }) => {
        const v = p.value as [number, number, number] | undefined;
        if (!v) return MISSING;
        const s = signals[v[1]];
        const h = HORIZONS[v[0]];
        const sg = s?.results?.[h];
        return [
          `${rows[v[1]]}`,
          `h${h} 秩相关 IC：${fmtNum(v[2], 3)}`,
          `n ${fmtInt(sg?.n_days)} 日 · ICIR ${fmtNum(sg?.icir, 2)} · IC&gt;0 占比 ${fmtPct(sg?.ic_pos_rate, 0)}`,
          sg?.tail_ok == null ? '' : `尾部同号：${sg.tail_ok ? '是' : '否'}`,
        ]
          .filter(Boolean)
          .join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: HORIZONS.map((h) => `h${h}`),
      axisLabel: { ...AXIS_LABEL, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
      splitArea: { show: false },
    },
    yAxis: {
      type: 'category',
      data: rows,
      inverse: true,
      axisLabel: { ...AXIS_LABEL, fontSize: 8 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
      splitArea: { show: false },
    },
    visualMap: {
      min: -bound,
      max: bound,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      itemWidth: 10,
      itemHeight: 60,
      textStyle: { fontSize: 8, color: CHART_COLORS.neutral },
      text: [`+${fmtNum(bound, 3)}`, `−${fmtNum(bound, 3)}`],
      inRange: { color: ['#64748b', '#cbd5e1', '#f8fafc', '#ddd6fe', '#7c3aed'] },
    },
    series: [
      {
        type: 'heatmap',
        data: pts,
        label: {
          show: true,
          fontSize: 7,
          color: '#475569',
          formatter: (p: { value?: unknown }) => {
            const v = p.value as [number, number, number] | undefined;
            return v ? fmtNum(v[2], 2) : MISSING;
          },
        },
        itemStyle: { borderColor: '#ffffff', borderWidth: 1, borderRadius: 2 },
        emphasis: { itemStyle: { shadowBlur: 5, shadowColor: 'rgba(100,116,139,0.45)' } },
        markArea: selYi >= 0
          ? {
              silent: true,
              itemStyle: { color: 'rgba(124,58,237,0.10)' },
              data: [[{ yAxis: selYi - 0.5 }, { yAxis: selYi + 0.5 }]],
            }
          : undefined,
      },
    ],
  };
}

/** 图 E：选中信号 h10 的 q1→q5 分层柱（bp，收益类量走红绿） */
function buildQuantileOption(sig: HkSmSignal | null) {
  const h10 = sig?.results?.['10'] ?? null;
  const vals = [h10?.q1_bp, h10?.q2_bp, h10?.q3_bp, h10?.q4_bp, h10?.q5_bp];
  if (!sig || !h10 || vals.every((v) => v === null || v === undefined)) return null;
  return {
    grid: { left: 46, right: 12, top: 22, bottom: 20 },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: 10 },
      formatter: (ps: Array<{ name?: string; value?: unknown; dataIndex?: number }>) => {
        const p = ps[0];
        const idx = p?.dataIndex ?? 0;
        const obs = idx >= 3 ? h10.n_top_obs : idx <= 1 ? h10.n_bot_obs : null;
        return [
          `${p?.name ?? ''} 分档（h10 事后超额）`,
          `${tipNum(p?.value as number, (n) => `${fmtBp(n, 0)} bp`)}`,
          obs !== null ? `观测 n=${fmtInt(obs)}` : '',
        ]
          .filter(Boolean)
          .join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: ['q1', 'q2', 'q3', 'q4', 'q5'],
      axisLabel: { ...AXIS_LABEL, fontSize: 10 },
      axisLine: { lineStyle: { color: CHART_COLORS.axis } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: 'bp',
      nameTextStyle: { fontSize: 8, color: CHART_COLORS.sub },
    },
    series: [
      {
        type: 'bar',
        barWidth: 22,
        data: vals.map((v) => ({
          value: v ?? null,
          itemStyle: {
            color: v === null || v === undefined ? CHART_COLORS.axis : signedColor(Number(v)),
            borderRadius: [3, 3, 0, 0],
          },
        })),
        label: {
          show: true,
          position: 'top' as const,
          fontSize: 8,
          color: CHART_COLORS.neutral,
          formatter: (p: { value?: unknown }) => tipNum(p.value as number, (n) => fmtBp(n, 0)),
        },
      },
    ],
    // 尾部同号线：q5−q1 的关系由柱形直接读出，零轴走 markLine
  };
}

function SignalRow({
  sig,
  selected,
  onSelect,
}: {
  sig: HkSmSignal;
  selected: boolean;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const seg = (h: string) => sig.results?.[h] ?? null;
  const h10 = seg('10');
  const q5q1 = h10?.q5_minus_q1_bp ?? null;

  return (
    <div className={`border-b border-slate-50 last:border-0 ${selected ? 'bg-purple-50/40' : ''}`}>
      <div
        role="button"
        onClick={() => {
          setExpanded((v) => !v);
          onSelect();
        }}
        className={`${GRID_COLS} px-1 py-1 cursor-pointer hover:bg-purple-50/50 transition-colors`}
      >
        <div className="flex items-center gap-1 min-w-0">
          {expanded ? (
            <ChevronDown className="w-3 h-3 text-slate-400 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-3 h-3 text-slate-400 flex-shrink-0" />
          )}
          <span className="text-[10px] font-bold text-slate-700 truncate" title={sig.name}>
            {sig.variant_label ?? sig.variant}
          </span>
        </div>
        {HORIZONS.map((h) => {
          const sg = seg(h);
          return (
            <span
              key={h}
              className={`text-right text-[10px] font-mono ${sg?.rank_ic != null ? 'font-bold' : ''} ${icCls(sg?.rank_ic)}`}
              title={
                sg
                  ? `h${h}：n=${fmtInt(sg.n_days)} 日 · ICIR ${fmtNum(sg.icir, 2)} · IC>0 占比 ${fmtPct(sg.ic_pos_rate, 0)}`
                  : `h${h} 缺失`
              }
            >
              {fmtNum(sg?.rank_ic, 3)}
            </span>
          );
        })}
        <span
          className={`text-right text-[10px] font-mono font-bold ${signCls(q5q1)}`}
          title={
            h10
              ? `h10 分位价差（bp）：q1 ${fmtBp(h10.q1_bp)} / q2 ${fmtBp(h10.q2_bp)} / q3 ${fmtBp(h10.q3_bp)} / q4 ${fmtBp(h10.q4_bp)} / q5 ${fmtBp(h10.q5_bp)}`
              : 'h10 缺失'
          }
        >
          {fmtBp(q5q1, 1)}
        </span>
        <span
          className="text-right text-[10px] font-mono font-bold text-slate-600"
          title={HIT_NOTE}
        >
          {fmtPct(h10?.buy_hit, 0)}
        </span>
        <span
          className="text-right text-[10px] font-mono font-bold text-slate-600"
          title={HIT_NOTE}
        >
          {fmtPct(h10?.sell_hit, 0)}
        </span>
        <span className="flex justify-end">
          {sig.admitted ? (
            <span
              className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-600 text-white whitespace-nowrap"
              title={sig.decision ?? ADMIT_NOTE}
            >
              入柱
            </span>
          ) : (
            <span
              className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-slate-100 text-slate-400 border border-slate-200 whitespace-nowrap"
              title={sig.decision ?? ADMIT_NOTE}
            >
              未过闸
            </span>
          )}
        </span>
      </div>
      {expanded && (
        <div className="mx-1 mb-1.5 rounded-lg bg-slate-50/80 border border-slate-100 px-2.5 py-1.5 flex flex-col gap-1 text-[9px] text-slate-500 leading-relaxed">
          <div className="font-bold text-slate-700">{sig.decision ?? ADMIT_NOTE}</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5 font-mono">
            <span>方向：{directionWord(sig.direction)}（按 h10 IC 符号）</span>
            <span>
              覆盖：日均 {fmtNum(sig.coverage?.avg_names_per_day, 0)} 名 ·{' '}
              {fmtInt(sig.coverage?.days)} 日
            </span>
            {HORIZONS.map((h) => {
              const sg = sig.results?.[h];
              return (
                <span key={h}>
                  h{h}：n {fmtInt(sg?.n_days)} · ICIR {fmtNum(sg?.icir, 2)} · IC&gt;0 占比{' '}
                  {fmtPct(sg?.ic_pos_rate, 0)} · 尾部同号{' '}
                  {sg?.tail_ok == null ? MISSING : sg.tail_ok ? '是' : '否'}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export const HkSmTypeBoard: React.FC<HkSmTypeBoardProps> = ({
  data,
  loading,
  error,
  bare = false,
}) => {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const ordered = useMemo(() => {
    if (!data) return [] as HkSmSignal[];
    const order = new Map<string, number>(CATEGORY_ORDER.map((c, i) => [c, i] as const));
    return [...(data.signals ?? [])].sort(
      (a, b) =>
        (order.get(a.category) ?? 99) - (order.get(b.category) ?? 99) ||
        String(a.variant).localeCompare(String(b.variant)),
    );
  }, [data]);

  const admittedCount = ordered.filter((s) => s.admitted).length;
  const defaultKey = useMemo(() => {
    const first = ordered.find((s) => s.admitted) ?? ordered[0];
    return first ? sigKey(first) : null;
  }, [ordered]);
  const activeKey = selectedKey ?? defaultKey;
  const activeSig = ordered.find((s) => sigKey(s) === activeKey) ?? null;

  const heatOption = useMemo(
    () => buildHeatOption(ordered, activeKey),
    [ordered, activeKey],
  );
  const quantileOption = useMemo(() => buildQuantileOption(activeSig), [activeSig]);

  const heatEvents = useMemo(
    () => ({
      click: (p: ChartEventParams) => {
        const v = p.data as [number, number, number] | undefined;
        if (!v) return;
        const s = ordered[v[1]];
        if (s) setSelectedKey(sigKey(s));
      },
    }),
    [ordered],
  );

  const window = data?.window;
  const h10 = activeSig?.results?.['10'] ?? null;

  return (
    <FaceCard
      bare={bare}
      icon={<Table2 className="w-3.5 h-3.5 text-purple-600" />}
      title="类型效力"
      extra={
        window ? (
          <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
            事件窗 {window.eval_start ?? MISSING} → {window.eval_end ?? MISSING} · 事件{' '}
            {fmtInt(window.event_days)} 日 / {fmtInt(window.event_rows)} 行
          </span>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3 min-w-0">
        {error && (
          <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
            类型效力数据不可用：{error}
          </div>
        )}
        {!data && !error && <EmptyHint loading={loading} text="类型效力数据不可用" />}

        {data && ordered.length > 0 && (
          <>
            {/* 公示条：过闸裁定 */}
            <div
              className={`rounded-xl px-3 py-1.5 text-[10px] font-bold border ${
                admittedCount === 0
                  ? 'bg-slate-50 border-slate-200 text-slate-600'
                  : 'bg-purple-50/70 border-purple-100 text-purple-800'
              }`}
              title={ADMIT_NOTE}
            >
              {admittedCount === 0
                ? `本期裁定：零入柱（evidence-only）—— ${fmtInt(ordered.length)} 信号 × h5/h10/h20 全未过双闸。这是数据结论，不是链路故障。`
                : `本期入柱 ${fmtInt(admittedCount)} / ${fmtInt(ordered.length)} 信号（h10 双过闸：IC 显著 ∧ 尾部 q5−q1 同号）。`}
            </div>

            {/* 图 D IC 热力图 + 图 E 分层柱 */}
            <div className="grid grid-cols-1 xl:grid-cols-[1.5fr_1fr] gap-4 items-start min-w-0">
              <div className="min-w-0 flex flex-col gap-1.5">
                <div className="flex items-center gap-2 px-1">
                  <Grid3X3 className="w-3 h-3 text-slate-400" />
                  <span className="text-[10px] font-extrabold text-slate-600">
                    前瞻 IC 热力 · {fmtInt(ordered.length)} 信号 × h5/h10/h20
                  </span>
                  <span className="text-[9px] text-slate-400">
                    点击单元格联动下方分层与表格（色阶：负灰 → 正紫）
                  </span>
                </div>
                <SmartChart
                  option={heatOption}
                  height={Math.max(240, ordered.length * 19 + 60)}
                  emptyText="IC 数据不可用"
                />
              </div>

              <div className="min-w-0 flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5 flex-wrap px-1">
                  <span className="text-[10px] font-extrabold text-slate-600">
                    h10 分层 · {activeSig ? (activeSig.variant_label ?? activeSig.variant) : MISSING}
                  </span>
                  <span
                    className="px-1.5 py-px rounded-full text-[8px] font-extrabold"
                    style={{
                      background: `${INST_CATEGORY_COLORS[activeSig?.category ?? ''] || '#94a3b8'}1a`,
                      color: INST_CATEGORY_COLORS[activeSig?.category ?? ''] || '#94a3b8',
                    }}
                  >
                    {activeSig
                      ? categoryLabel(activeSig.category, activeSig.category_label)
                      : MISSING}
                  </span>
                  <span
                    className={`px-1.5 py-px rounded-full text-[8px] font-extrabold ${
                      activeSig?.admitted
                        ? 'bg-purple-600 text-white'
                        : 'bg-slate-100 text-slate-400 border border-slate-200'
                    }`}
                    title={activeSig?.decision ?? ADMIT_NOTE}
                  >
                    {activeSig?.admitted ? '入柱' : '未过闸'}
                  </span>
                  <span
                    className={`px-1.5 py-px rounded-full text-[8px] font-extrabold border ${
                      h10?.tail_ok == null
                        ? 'bg-slate-50 text-slate-400 border-slate-200'
                        : h10.tail_ok
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-100'
                          : 'bg-amber-50 text-amber-700 border-amber-100'
                    }`}
                    title="尾部同号 = q5−q1 与信号方向一致（双过闸第二闸）"
                  >
                    尾部{h10?.tail_ok == null ? ` ${MISSING}` : h10.tail_ok ? '同号' : '异号'}
                  </span>
                </div>
                <SmartChart
                  option={quantileOption}
                  height={Math.max(240, ordered.length * 19 + 60)}
                  emptyText="选中信号无 h10 分层数据"
                />
                <p className="text-[9px] text-slate-400 leading-relaxed px-1">
                  q1→q5 = 按信号值由弱到强的五档，柱高为事后 10 日市场调整超额（bp）。
                  单调爬升（q1 低、q5 高，或减持变体相反）才是方向证据；q5−q1
                  与 IC 符号一致即「尾部同号」。
                </p>
              </div>
            </div>

            {/* 表 F 检定表 */}
            <div className="overflow-x-auto">
              <div className="min-w-[660px]">
                <div
                  className={`${GRID_COLS} px-1 pb-1 border-b border-slate-100 text-[9px] font-extrabold text-slate-400`}
                >
                  <span>类型 · 变体（点击展开并联动上图）</span>
                  <span className="text-right">h5 IC</span>
                  <span className="text-right">h10 IC</span>
                  <span className="text-right">h20 IC</span>
                  <span className="text-right" title="h10 分位五档：q5 超额 − q1 超额（bp）">
                    h10 q5−q1
                  </span>
                  <span className="text-right" title={HIT_NOTE}>
                    增持胜率
                  </span>
                  <span className="text-right" title={HIT_NOTE}>
                    减持胜率
                  </span>
                  <span className="text-right" title={ADMIT_NOTE}>
                    裁定
                  </span>
                </div>
                <div className="max-h-[430px] overflow-y-auto">
                  {CATEGORY_ORDER.map((cat) => {
                    const rows = ordered.filter((s) => s.category === cat);
                    if (!rows.length) return null;
                    return (
                      <React.Fragment key={cat}>
                        <div className="flex items-center gap-1.5 px-1 pt-2 pb-1 sticky top-0 bg-white/95 backdrop-blur z-10">
                          <span
                            className="w-1.5 h-1.5 rounded-full"
                            style={{ background: INST_CATEGORY_COLORS[cat] || '#94a3b8' }}
                          />
                          <span className="text-[10px] font-extrabold text-slate-700">
                            {categoryLabel(cat, rows[0]?.category_label)}
                          </span>
                          {rows.some((r) => r.admitted) && (
                            <span className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-50 text-purple-700 border border-purple-100">
                              入柱 {rows.filter((r) => r.admitted).length}
                            </span>
                          )}
                        </div>
                        {rows.map((s) => (
                          <SignalRow
                            key={`${cat}-${s.variant}`}
                            sig={s}
                            selected={sigKey(s) === activeKey}
                            onSelect={() => setSelectedKey(sigKey(s))}
                          />
                        ))}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            </div>
          </>
        )}

        {data && data.notes && (
          <p className="text-[9px] text-slate-400 leading-relaxed">
            历史胜率 = 事件后 10 日超额与方向同号的占比（事后统计，非预测）；IC 与胜率为统计量（中性色），
            q5−q1 为分位价差（bp）。入柱判据见裁定列 tooltip——「未过闸」为真裁定，不是缺数。
          </p>
        )}
      </div>
    </FaceCard>
  );
};
