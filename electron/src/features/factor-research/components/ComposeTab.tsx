/**
 * 因子研究 —— 多因子合成页签：逐因子权重 + 可选过滤阈值（z 刻度）→ 区间内实时回测；
 * 「求最优权重」在所选因子上网格搜索（非负、和为 1），夏普 / 年化 / 超额各给一组。
 *
 * 基准面市场化（2026-10）：净值叠加与「超额最大」目标的基准 = 本市场主基准
 * （CN 沪深300 → 旧键 excess_300；其余市场统一 excess_primary，名称由后端
 * bench_primary 下发）。无基准市场（期货）→ 超额目标磁贴不渲染、不叠线。
 */
import React, { useMemo, useState } from 'react';
import { Play, Trash2, Wand2 } from 'lucide-react';
import { postCompose, postOptimalWeights } from '../services/factorResearchService';
import type { FactorDataset, RangeParams } from '../services/factorResearchService';
import type { ComposeResponse, FactorMeta, OptimalResponse, OptimalWinner, ResearchMarket } from '../types/factorResearch';
import { Card, FACTOR_COLORS, fmtNum, fmtPct, KpiTiles, NavChart } from './common';
import { EChartsChart } from '../../../components/common/EChartsChart';

interface Props {
  factors: FactorMeta[];
  codes: string[];
  onChangeCodes: (codes: string[]) => void;
  range: RangeParams;
  dataset: FactorDataset;
  /** 本页当前市场（合成/寻优按市场） */
  market: ResearchMarket;
}

export const ComposeTab: React.FC<Props> = ({ factors, codes, onChangeCodes, range, dataset, market }) => {
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [thresholds, setThresholds] = useState<Record<string, string>>({});
  const [topN, setTopN] = useState(30);
  const [threshold, setThreshold] = useState<number | ''>('');
  const [result, setResult] = useState<ComposeResponse | null>(null);
  const [optimal, setOptimal] = useState<OptimalResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState('');

  const nameOf = useMemo(() => {
    const m = new Map(factors.map((f) => [f.code, f.name_cn]));
    return (c: string) => m.get(c) || c;
  }, [factors]);

  const addable = useMemo(
    () => factors.filter((f) => f.available && !codes.includes(f.code)),
    [factors, codes],
  );

  const updateWeight = (code: string, value: number) => setWeights({ ...weights, [code]: value });
  const updateThreshold = (code: string, value: string) =>
    setThresholds({ ...thresholds, [code]: value });
  const remove = (code: string) => onChangeCodes(codes.filter((c) => c !== code));

  const buildRequest = () => {
    const w: Record<string, number> = {};
    for (const c of codes) {
      const v = weights[c] ?? 1;
      if (v !== 0) w[c] = v;
    }
    const th: Record<string, number> = {};
    for (const c of codes) {
      const raw = (thresholds[c] ?? '').trim();
      if (raw !== '') th[c] = Number(raw);
    }
    return { weights: w, top_n: topN, thresholds: Object.keys(th).length ? th : null,
             threshold: threshold === '' ? null : Number(threshold),
             start: range.start ?? null, end: range.end ?? null, dataset, market };
  };

  const run = async () => {
    const req = buildRequest();
    if (!Object.keys(req.weights).length) {
      setError('请至少添加一个因子并设置非零权重');
      return;
    }
    setRunning(true);
    setError(null);
    try {
      setResult(await postCompose(req));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const runOptimal = async () => {
    if (!codes.length) {
      setError('请先添加因子');
      return;
    }
    setOptimizing(true);
    setError(null);
    try {
      setOptimal(await postOptimalWeights(codes, topN, range, dataset, market));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOptimizing(false);
    }
  };

  const applyWinner = (w: OptimalWinner) => {
    setWeights({ ...w.weights });
    run();
  };

  const turnoverOption = useMemo(() => {
    if (!result?.turnover?.length) return null;
    return {
      grid: { left: 40, right: 10, top: 10, bottom: 20 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: result.turnover.map((p) => p.date), axisLabel: { fontSize: 9, color: '#94a3b8' }, axisTick: { show: false } },
      yAxis: { type: 'value', max: 1, axisLabel: { fontSize: 9, color: '#94a3b8' }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      series: [{ type: 'line', data: result.turnover.map((p) => p.value), showSymbol: false, areaStyle: { opacity: 0.15 }, lineStyle: { width: 1.5, color: '#d97706' }, itemStyle: { color: '#d97706' } }],
    };
  }, [result]);

  // 超额目标键按市场分派：非 CN 只有 excess_primary（键缺席=该市场无基准）；
  // CN 两个键都在（后端同时下发），优先读新键。
  const optimalObjKeys = useMemo(() => {
    const o = optimal?.objectives;
    type ObjKey = 'sharpe' | 'annual_return' | 'excess_primary' | 'excess_300';
    if (!o) return [] as ObjKey[];
    const keys: ObjKey[] = ['sharpe', 'annual_return'];
    if (o.excess_primary) keys.push('excess_primary');
    else if (o.excess_300) keys.push('excess_300');
    return keys;
  }, [optimal]);
  const objLabel = (key: string): string => {
    if (key === 'sharpe') return '目标：夏普最大';
    if (key === 'annual_return') return '目标：年化最大';
    return `目标：超额最大（vs ${optimal?.bench_primary?.name ?? '主基准'}）`;
  };
  // 净值叠加的基准 = 合成超额映射（后端按 bench_order 生成）的首键；无基准市场 → null
  const composeBench = useMemo(() => {
    const code = result ? Object.keys(result.excess)[0] : undefined;
    if (!code || !result) return null;
    return result.benchmarks.find((b) => b.code === code) ?? { code, name: code };
  }, [result]);

  return (
    <div className="flex-1 min-h-0 flex gap-2">
      {/* 左：权重编辑器 */}
      <div className="w-[320px] shrink-0 flex flex-col bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between">
          <span className="text-xs font-extrabold text-slate-800">因子 · 权重 · 过滤阈值</span>
          <button
            onClick={() => { setWeights({}); setThresholds({}); }}
            className="flex items-center gap-1 text-[10px] font-bold text-blue-600 hover:text-blue-700"
            title="全部设为等权 1、清空阈值"
          >
            <Wand2 className="w-3 h-3" /> 重置
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-2 space-y-1.5">
          {codes.map((c, i) => (
            <div key={c} className="rounded-xl border border-slate-100 bg-slate-50/50 px-2 py-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-slate-700 truncate">
                  <span className="inline-block w-1.5 h-1.5 rounded-full mr-1" style={{ backgroundColor: FACTOR_COLORS[i % FACTOR_COLORS.length] }} />
                  {nameOf(c)}
                </span>
                <button onClick={() => remove(c)} className="text-slate-300 hover:text-rose-500">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="font-mono text-[9px] text-slate-400 w-14 truncate">{c}</span>
                <input
                  type="range" min={-3} max={3} step={0.25}
                  value={weights[c] ?? 1}
                  onChange={(e) => updateWeight(c, Number(e.target.value))}
                  className="flex-1"
                />
                <input
                  type="number" step={0.25}
                  value={weights[c] ?? 1}
                  onChange={(e) => updateWeight(c, Number(e.target.value))}
                  className="w-12 rounded-md border border-slate-200 bg-white px-1 py-0.5 text-[11px] font-mono text-right"
                />
                <input
                  type="number" step={0.25} placeholder="阈值"
                  value={thresholds[c] ?? ''}
                  onChange={(e) => updateThreshold(c, e.target.value)}
                  title="过滤阈值（z 刻度）：该因子得分低于此值的股票先被剔除；留空=不过滤"
                  className="w-12 rounded-md border border-slate-200 bg-white px-1 py-0.5 text-[11px] font-mono text-right placeholder:text-slate-300"
                />
              </div>
            </div>
          ))}
          {codes.length === 0 && (
            <div className="text-[11px] text-slate-400 px-2 py-4 text-center">
              从左侧目录勾选因子，或在下方下拉添加
            </div>
          )}
        </div>
        <div className="p-2 border-t border-slate-100 space-y-2">
          <select
            value={pick}
            onChange={(e) => {
              const c = e.target.value;
              setPick('');
              if (c && !codes.includes(c)) onChangeCodes([...codes, c]);
            }}
            className="w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px]"
          >
            <option value="">添加因子…</option>
            {addable.map((f) => (
              <option key={f.code} value={f.code}>{f.l1} / {f.l2} · {f.name_cn}</option>
            ))}
          </select>
          <div className="flex items-center gap-2 text-[11px]">
            <label className="text-slate-500 shrink-0">选股数</label>
            <input type="number" min={1} max={200} value={topN} onChange={(e) => setTopN(Number(e.target.value))}
              className="w-14 rounded-md border border-slate-200 px-1.5 py-0.5 font-mono text-right" />
            <label className="text-slate-500 shrink-0 ml-1" title="合成打分的下限（全局，z 刻度）：0 ≈ 只买高于截面均值的股票">合成阈值</label>
            <input
              type="number" step={0.25} placeholder="不过滤" value={threshold}
              onChange={(e) => setThreshold(e.target.value === '' ? '' : Number(e.target.value))}
              className="w-14 rounded-md border border-slate-200 px-1.5 py-0.5 font-mono text-right placeholder:text-slate-300"
            />
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={run}
              disabled={running}
              className="flex items-center gap-1 rounded-full bg-blue-600 text-white px-3 py-1 text-[11px] font-bold hover:bg-blue-700 disabled:opacity-50"
            >
              <Play className="w-3 h-3" /> {running ? '回测中…' : '运行回测'}
            </button>
            <button
              onClick={runOptimal}
              disabled={optimizing || !codes.length}
              className="flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-[11px] font-bold text-violet-600 hover:bg-violet-100 disabled:opacity-40"
              title="在所选因子上网格搜索非负、和为 1 的最优权重（忽略阈值）"
            >
              ⚙ {optimizing ? '搜索中…' : '求最优权重'}
            </button>
          </div>
          {error && <div className="text-[10px] text-rose-500">{error}</div>}
        </div>
      </div>

      {/* 右：结果 */}
      <div className="flex-1 min-w-0 min-h-0 overflow-y-auto custom-scrollbar flex flex-col gap-2 pr-0.5">
        {optimal && (
          <div className="shrink-0 grid grid-cols-1 lg:grid-cols-3 gap-2">
            {optimalObjKeys.map((key) => {
              const w = optimal.objectives[key];
              if (!w) return null;
              return (
                <div key={key} className="rounded-2xl border border-violet-100 bg-violet-50/40 p-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-extrabold text-violet-600">{objLabel(key)}</span>
                    <button
                      onClick={() => applyWinner(w)}
                      className="rounded-full bg-violet-600 text-white px-2 py-0.5 text-[10px] font-bold hover:bg-violet-700"
                    >
                      应用并回测
                    </button>
                  </div>
                  <div className="mt-1 space-y-0.5">
                    {Object.entries(w.weights).filter(([, v]) => v > 0).map(([c, v]) => (
                      <div key={c} className="flex items-center justify-between text-[10px]">
                        <span className="text-slate-600 truncate">{nameOf(c)}</span>
                        <span className="font-mono font-bold text-slate-700">{v.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-1 pt-1 border-t border-violet-100 flex items-center gap-2 text-[10px] font-mono text-slate-500">
                    <span>夏普 {fmtNum(w.sharpe)}</span>
                    <span>年化 {fmtPct(w.annual_return)}</span>
                    <span>回撤 {fmtPct(w.max_drawdown)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {optimal && (
          <div className="shrink-0 text-[10px] text-slate-400">
            网格搜索 {optimal.n_combos} 组权重 · {optimal.n_months} 个月 · 用时 {optimal.elapsed_ms}ms ·
            搜索在候选池（各因子月末前 150 名的并集）上评估，应用后以全样本精确回测为准
          </div>
        )}

        {result ? (
          <>
            <div className="shrink-0"><KpiTiles kpi={{ ...result.kpi, ...result.excess }} /></div>
            <div className="shrink-0 grid grid-cols-1 xl:grid-cols-2 gap-2">
              <Card
                title="合成组合净值"
                extra={<span className="text-[10px] text-slate-400">区间 {result.range.start} ~ {result.range.end} · 双边成本 0.2%</span>}
              >
                <NavChart
                  series={[
                    { name: '合成组合', data: result.nav },
                    ...(composeBench
                      ? result.benchmarks
                          .filter((b) => b.code === composeBench.code)
                          .map((b) => ({ name: b.name, data: b.nav, color: '#94a3b8', dashed: true }))
                      : []),
                  ]}
                  height={200}
                />
              </Card>
              <Card title="换手率（月末）" extra={<span className="text-[10px] text-slate-400">新进持仓占比</span>}>
                {turnoverOption
                  ? <div style={{ height: 200 }}><EChartsChart option={turnoverOption} /></div>
                  : <div className="h-[200px] flex items-center justify-center text-xs text-slate-400">—</div>}
              </Card>
            </div>
            <Card
              className="shrink-0"
              title={`最新持仓（${result.holdings_date || '—'} · Top ${result.holdings.length}）`}
              extra={<span className="text-[10px] text-slate-400">合成打分降序 · 阈值 {result.threshold ?? '无'}</span>}
            >
              <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-x-4">
                {result.holdings.map((h, i) => (
                  <div key={h.symbol} className="flex items-center justify-between py-[3px] border-b border-slate-50">
                    <span className="text-[11px] text-slate-600 truncate">
                      <span className="font-mono text-[10px] text-slate-400 mr-1">{i + 1}</span>
                      {h.name || h.symbol}
                    </span>
                    <span className="text-[9px] text-slate-400 shrink-0 ml-1">{h.industry}</span>
                  </div>
                ))}
              </div>
            </Card>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-2 text-slate-400">
            <Play className="w-8 h-8 text-slate-200" />
            <span className="text-xs">配置权重/阈值后点击「运行回测」，或先「求最优权重」</span>
            <span className="text-[10px] text-slate-300">
              口径：月末调仓 · Top-N 等权 · 双边成本 0.2% · 打分 = Σ w×因子分 / Σ|w| · 阈值 z 刻度（≥1.0 约前 16%）
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
