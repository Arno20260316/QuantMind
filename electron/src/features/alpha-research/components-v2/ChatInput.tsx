import React, { useState, useRef, useEffect } from 'react';
import { Send, Dna, Loader2, Sparkles } from 'lucide-react';
import { TaskConfig } from '../types-v2';
import { getPoolFactors } from '../services-v2/api';
import type { PoolFactorRow } from '../services-v2/api';
import { MiningConfigPanel } from './MiningConfigPanel';
import { MiningOptionsBar } from './MiningOptionsBar';
import { useMiningOptions } from './useMiningOptions';
import type { DecomposeRequestPayload, SeedFactorRef } from './DecomposePanel';

/** 父本选择上限（服务端 DECOMPOSE_SEED_MAX 是硬闸：更严时以它的 400 文案为准） */
const SEED_MAX = 3;

/** 输入框自动增高上下限（style 与增高效应同源一处，不再两处各写一份数字） */
const TEXTAREA_MIN_HEIGHT = 72;
const TEXTAREA_MAX_HEIGHT = 240;

interface ChatInputProps {
  onSubmit: (config: TaskConfig) => void;
  /**
   * 提交在途（POST /evolve 未返回）——只锁提交动作本身。
   * 多任务下「已有任务运行」不再是提交障碍（任务相互独立），运行数走 runningCount。
   */
  isSubmitting?: boolean;
  /** 运行中任务数：只影响占位提示文案，不禁用任何控件 */
  runningCount?: number;
  inline?: boolean;
  initialPrompt?: string;
  /** 「重跑」回填：市场/池/数据源。只在有值时覆盖，空值不动用户当前选择。 */
  initialConfig?: Partial<Pick<TaskConfig, 'miningMarket' | 'universe' | 'dataSource'>>;
  /** 回填代次（`MiningRetryDraft.key`）：同内容连点两次「重跑」也要重新应用 */
  initialConfigKey?: number;
  onSelectPrompt?: (prompt: string) => void;
  /**
   * 「智能拆解」：把当前输入的方向 + 当前市场/池交给拆解面板（HomePage 持状态）。
   * 缺省不渲染拆解按钮。
   */
  onDecomposeRequest?: (payload: DecomposeRequestPayload) => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSubmit,
  isSubmitting = false,
  runningCount = 0,
  inline = false,
  initialPrompt = '',
  initialConfig,
  initialConfigKey,
  onDecomposeRequest,
}) => {
  const [input, setInput] = useState(initialPrompt);
  const [useCustomMiningDirection, setUseCustomMiningDirection] = useState(false);
  const [config] = useState<Partial<TaskConfig>>({ librarySuffix: '' });
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 父本（种子）选择器：展开时拉本市场本池「正向 / 反向」各 pool_score 前 50；选择数 ≤3
  const [seedPanelOpen, setSeedPanelOpen] = useState(false);
  const [seedPos, setSeedPos] = useState<PoolFactorRow[]>([]);
  const [seedNeg, setSeedNeg] = useState<PoolFactorRow[]>([]);
  const [seedsLoading, setSeedsLoading] = useState(false);
  const [seedsError, setSeedsError] = useState<string | null>(null);
  const [seeds, setSeeds] = useState<SeedFactorRef[]>([]);

  // 挖掘选项（算法/市场/池/数据源/挖掘配置）：与文档挖掘确认步共用同一份状态机
  const mining = useMiningOptions();
  const {
    miningMarket,
    universe,
    dataSource,
    algorithm,
    algorithmLabel,
    carriesDirection,
    selectedNotReady,
    miningConfig,
    setMiningConfig,
    miningConfigPanelOpen,
    effectiveAlgorithm,
    miningConfigPayload,
    setMiningMarket,
    setUniverse,
    setDataSource,
  } = mining;

  useEffect(() => {
    // 空串不清空：legacy 历史行方向为空是常态，不能把用户打了一半的字擦掉；
    // initialConfigKey 一起看：key 前进（又点了一次「重跑」）时同文案也要重放。
    if (initialPrompt) {
      setInput(initialPrompt);
    }
  }, [initialPrompt, initialConfigKey]);

  useEffect(() => {
    if (!initialConfig) return;
    if (initialConfig.miningMarket) setMiningMarket(initialConfig.miningMarket);
    if (initialConfig.universe) setUniverse(initialConfig.universe);
    if (initialConfig.dataSource) setDataSource(initialConfig.dataSource);
    // 依赖只认代次 key，不认对象身份/内容——同内容连点两次「重跑」
    // 也必须把用户手改过的选项复原（对象比内容会被 React 判成无变化而跳过）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialConfigKey]);

  // 种子按「本市场 × 本池」隔离：切市场/池即清空（旧池的父本对新池无意义）
  useEffect(() => {
    setSeeds([]);
  }, [miningMarket, universe]);

  // 非 LLM 算法整块不适用：方向开关与父本都是提示词链的输入，当场收回
  // （换算法时的市场/频率/数据集收回在 useMiningOptions 内完成）
  useEffect(() => {
    if (!carriesDirection) {
      setUseCustomMiningDirection(false);
      setSeeds([]);
      setSeedPanelOpen(false);
    }
  }, [carriesDirection]);

  // 展开时并行拉「正向 / 反向」各 pool_score 前 50（方向口径 = ic_value 符号，
  // 与因子库「方向」列一致）；开着时切市场/池自动重拉。任一侧失败显式报错——
  // 只显示一半候选会让用户以为选择了完整的前 N 强，静默漏掉另一方向。
  useEffect(() => {
    if (!seedPanelOpen || !onDecomposeRequest) return;
    let cancelled = false;
    setSeedsLoading(true);
    setSeedsError(null);
    const base = {
      market: miningMarket,
      universe: String(universe),
      limit: 50,
      sort: 'pool_score' as const,
    };
    Promise.all([
      getPoolFactors({ ...base, direction: 'pos' }),
      getPoolFactors({ ...base, direction: 'neg' }),
    ])
      .then(([posRes, negRes]) => {
        if (cancelled) return;
        if (!posRes.success) throw new Error(posRes.error || '正向因子获取失败');
        if (!negRes.success) throw new Error(negRes.error || '反向因子获取失败');
        setSeedPos(posRes.data?.items ?? []);
        setSeedNeg(negRes.data?.items ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        setSeedsError(err instanceof Error ? err.message : '因子池列表获取失败');
      })
      .finally(() => {
        if (!cancelled) setSeedsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [seedPanelOpen, miningMarket, universe, onDecomposeRequest]);

  const toggleSeed = (row: PoolFactorRow) => {
    setSeeds((prev) => {
      if (prev.some((s) => s.id === row.factorId)) {
        return prev.filter((s) => s.id !== row.factorId);
      }
      if (prev.length >= SEED_MAX) return prev;
      return [...prev, { id: row.factorId, name: row.factorName }];
    });
  };

  // 方向分组渲染（空组整组不渲染——空标题会被误读成「本侧拉取失败」）
  const renderSeedGroup = (label: string, rows: PoolFactorRow[]) => {
    if (rows.length === 0) return null;
    return (
      <div key={label} className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5 px-0.5">
          <span className="text-[10px] font-bold text-slate-400">
            {label}（{rows.length}）
          </span>
          <span className="h-px flex-1 bg-slate-200/70" />
        </div>
        {rows.map((row) => {
          const selectedIdx = seeds.findIndex((s) => s.id === row.factorId);
          const isSelected = selectedIdx >= 0;
          const atLimit = !isSelected && seeds.length >= SEED_MAX;
          return (
            <button
              key={row.factorId}
              type="button"
              onClick={() => toggleSeed(row)}
              disabled={atLimit}
              title={atLimit ? `最多选 ${SEED_MAX} 个父本` : row.factorFormulation || row.factorId}
              className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                isSelected
                  ? 'bg-white ring-1 ring-indigo-300 shadow-xs cursor-pointer'
                  : atLimit
                    ? 'opacity-40 cursor-not-allowed'
                    : 'hover:bg-white/70 cursor-pointer'
              }`}
            >
              <span
                className={`shrink-0 inline-flex items-center justify-center h-4 w-4 rounded-full text-[9px] font-black ${
                  isSelected ? 'bg-indigo-600 text-white' : 'bg-slate-200 text-slate-500'
                }`}
              >
                {isSelected ? selectedIdx + 1 : ''}
              </span>
              <span className="flex-1 min-w-0 flex flex-col">
                <span className="text-[11px] font-bold text-slate-700 truncate">
                  {row.factorName}
                </span>
                <span className="text-[10px] font-mono text-slate-400 truncate">
                  {row.factorFormulation || row.factorId}
                </span>
              </span>
              <span className="shrink-0 text-[10px] font-mono text-slate-400">
                {row.icir != null
                  ? `ICIR ${row.icir.toFixed(2)}`
                  : row.ic != null
                    ? `IC ${row.ic.toFixed(3)}`
                    : '—'}
              </span>
            </button>
          );
        })}
      </div>
    );
  };

  const handleSubmit = () => {
    if (isSubmitting) return;
    const suffix = config.librarySuffix?.trim() || undefined;
    onSubmit({
      // 不消费方向文本的算法：从源头不产生方向（后端收到即 400）
      userInput: carriesDirection ? input.trim() : '',
      useCustomMiningDirection: carriesDirection && useCustomMiningDirection,
      miningMarket: miningMarket as TaskConfig['miningMarket'],
      universe,
      dataSource: dataSource as TaskConfig['dataSource'],
      // 挖掘配置仅在 A 股有效（面板也只在 A 股渲染；散件里再收一道，
      // 防止「切市场又切回来」残留的自定义窗口被带到别的市场）
      ...miningConfigPayload,
      // 算法（未选 = 不下发键，走后端注册表默认）
      ...(algorithm ? { algorithm, algorithmName: algorithmLabel } : {}),
      // 能力面快照：TaskContext 据此决定要不要代填默认方向
      carriesDirection,
      ...config,
      librarySuffix: suffix,
    } as TaskConfig);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height =
      Math.max(TEXTAREA_MIN_HEIGHT, Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)) + 'px';
  }, [input]);

  // 拆解按钮可用性：与发送同门槛，另要求有非空输入且未开「自选方向」
  // （自选模式下输入框内容被忽略，拆解它没有意义）
  const decomposeEnabled =
    !!onDecomposeRequest &&
    !isSubmitting &&
    !selectedNotReady &&
    !useCustomMiningDirection &&
    input.trim().length > 0;

  const handleDecompose = () => {
    if (!decomposeEnabled || !onDecomposeRequest) return;
    onDecomposeRequest({
      direction: input.trim(),
      market: miningMarket,
      universe: String(universe),
      ...(seeds.length ? { seeds } : {}),
      // 自定义挖掘配置随卡片批量派发一起带走（未自定义不携带键）
      ...miningConfigPayload,
    });
  };

  const content = (
    <div className={`relative w-full ${inline ? 'max-w-5xl mx-auto' : 'container mx-auto px-6 max-w-3xl'}`}>
      {/* Animated gradient border ring（聚焦时整圈加亮：输入中一眼可见） */}
      <div className="relative rounded-2xl bg-gradient-to-r from-blue-500/30 via-purple-500/30 to-pink-500/30 p-[1px] shadow-xl shadow-blue-500/5 transition-all duration-300 focus-within:from-blue-500/60 focus-within:via-purple-500/50 focus-within:to-pink-500/50 focus-within:shadow-blue-500/15">
        <div className="rounded-[15px] bg-white/95 backdrop-blur-xl overflow-hidden shadow-xs">
          {/* Options row — 单行芯片（窄窗横向滚动，不换行） */}
          <div className="px-4 pt-3.5 pb-1">
            <MiningOptionsBar
              options={mining}
              disabled={isSubmitting}
              direction={{
                active: useCustomMiningDirection,
                onToggle: () => setUseCustomMiningDirection(!useCustomMiningDirection),
              }}
              trailing={onDecomposeRequest && carriesDirection ? (
                /* 父本定向演化：拆解围绕选中的池内因子做受控变异。
                   父本是拆解（LLM）的输入——不消费方向的算法整块不适用 */
                <button
                  type="button"
                  onClick={() => setSeedPanelOpen((v) => !v)}
                  disabled={isSubmitting}
                  title={
                    seeds.length > 0
                      ? `已选 ${seeds.length} 个父本：拆解围绕其做受控变异（点开调整）`
                      : '父本定向演化：选 1–3 个池内因子，拆解围绕其做受控变异（滞后/窗长/标准化/差分…）'
                  }
                  className={`shrink-0 ml-1 pl-1 border-l border-slate-200 flex items-center gap-1 rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 disabled:opacity-40 ${
                    seedPanelOpen || seeds.length > 0
                      ? 'bg-indigo-50 text-indigo-600 ring-1 ring-indigo-200 shadow-xs'
                      : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  <Dna className="h-3 w-3" />
                  <span>父本</span>
                  {seeds.length > 0 && (
                    <span className="rounded-full bg-indigo-600 text-white px-1.5 text-[10px] font-bold leading-4">
                      {seeds.length}/{SEED_MAX}
                    </span>
                  )}
                </button>
              ) : undefined}
            />
          </div>

          {/* 父本选择面板（在流内展开：卡片 overflow-hidden 裁掉绝对定位下拉） */}
          {onDecomposeRequest && carriesDirection && seedPanelOpen && (
            <div className="px-4 pb-2.5">
              <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 px-3 py-2.5">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="flex-1 min-w-0 text-[11px] font-bold text-slate-600">
                    父本因子（可选，最多 {SEED_MAX} 个）：拆解将围绕父本做受控变异（滞后 / 窗长 / 标准化 / 差分 / 比值 / 非线性）
                  </span>
                  {seeds.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setSeeds([])}
                      className="shrink-0 text-[10px] font-bold text-slate-400 hover:text-rose-500 transition-colors cursor-pointer"
                    >
                      清空
                    </button>
                  )}
                </div>
                {seedsLoading ? (
                  <div className="flex items-center gap-2 py-1.5 text-[11px] font-bold text-slate-400">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    正在读取本池因子…
                  </div>
                ) : seedsError ? (
                  <div className="py-1.5 text-[11px] font-bold text-rose-500 break-all">
                    {seedsError}
                  </div>
                ) : seedPos.length === 0 && seedNeg.length === 0 ? (
                  <div className="py-1.5 text-[11px] text-slate-400">
                    本池暂无已完成回测的因子——先挖出因子、回测入池后即可选作父本
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5 max-h-44 overflow-y-auto pr-0.5">
                    {renderSeedGroup('正向', seedPos)}
                    {renderSeedGroup('反向', seedNeg)}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* 挖掘配置面板（在流内展开，同父本面板） */}
          {miningMarket === 'a_share' && miningConfigPanelOpen && (
            <div className="px-4 pb-2.5">
              <MiningConfigPanel
                value={miningConfig}
                onChange={setMiningConfig}
                disabled={isSubmitting}
                algorithm={effectiveAlgorithm}
              />
            </div>
          )}

          {/* Divider（渐变细线，与两侧留白对齐） */}
          <div className="mx-4 h-px bg-gradient-to-r from-transparent via-slate-200/90 to-transparent" />

          {/* Textarea + send row */}
          <div className="flex items-end gap-2.5 px-4 py-3.5">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isSubmitting
                  ? '任务提交中...'
                  : selectedNotReady
                    ? '该市场数据未就绪，请先在管理后台同步数据'
                    : !carriesDirection
                      ? `「${algorithmLabel || '当前算法'}」不消费方向文本：直接点发送即可（挖掘区间走「区间/频率/数据集」）`
                      : runningCount > 0
                        ? `已有 ${runningCount} 个任务运行中，可继续提交新想法（任务相互独立）`
                        : useCustomMiningDirection
                          ? '已开启自选挖掘方向，将使用「设置 → 挖掘方向」中的选项'
                          : miningMarket === 'crypto'
                            ? '描述加密货币因子需求，如：短期动量反转、量价背离...'
                            : '描述因子挖掘需求 (如：挖掘基于5日动量反转与成交量偏度组合的Alpha因子)，按 Enter 发送'
              }
              disabled={isSubmitting || !carriesDirection}
              className="flex-1 bg-transparent text-sm placeholder:text-slate-400 focus:outline-none focus:ring-0 resize-none leading-relaxed font-sans rounded-xl border border-transparent"
              rows={3}
              style={{ minHeight: TEXTAREA_MIN_HEIGHT, maxHeight: TEXTAREA_MAX_HEIGHT }}
            />

            {/* 智能拆解：粗方向 → 多张正交卡片 → 批量派发（满员自动排队）。
                仅在有输入、未开「自选方向」、市场就绪时可点 */}
            {onDecomposeRequest && carriesDirection && (
              <button
                onClick={handleDecompose}
                disabled={!decomposeEnabled}
                className="flex-shrink-0 p-2.5 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 text-white hover:from-indigo-600 hover:to-purple-700 disabled:from-slate-300 disabled:to-slate-400 disabled:cursor-not-allowed transition-all duration-200 hover:scale-105 active:scale-95 shadow-lg shadow-indigo-500/25 disabled:shadow-none cursor-pointer"
                title={
                  useCustomMiningDirection
                    ? '已开「自选方向」：拆解针对输入框方向，请先关闭'
                    : input.trim()
                      ? '智能拆解：把当前方向拆成多张正交卡片后批量派发'
                      : '先输入要拆解的挖掘方向'
                }
              >
                <Sparkles className="h-4 w-4" />
              </button>
            )}

            {/* Send button：多任务下「停止」不属于输入框（每行任务各有自己的停止），
                这里只负责提交；仅在提交在途或市场未就绪时禁用 */}
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || !!selectedNotReady}
              className="flex-shrink-0 p-2.5 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white hover:from-blue-600 hover:to-indigo-700 disabled:from-slate-300 disabled:to-slate-400 disabled:cursor-not-allowed transition-all duration-200 hover:scale-105 active:scale-95 shadow-lg shadow-blue-500/25 disabled:shadow-none cursor-pointer"
              title={selectedNotReady ? '市场数据未就绪' : '发送 (Enter)'}
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  if (inline) {
    return <div className="w-full relative">{content}</div>;
  }

  return (
    <div
      className="fixed left-0 right-0 z-40 flex flex-col items-center"
      style={{ bottom: 0, paddingBottom: '88px' }}
    >
      {/* Gradient scrim */}
      <div
        className="pointer-events-none absolute inset-x-0"
        style={{
          bottom: 0,
          height: '160px',
          background: 'linear-gradient(to bottom, hsl(var(--background) / 0) 0%, hsl(var(--background) / 0.6) 40%, hsl(var(--background) / 0.95) 70%, hsl(var(--background)) 100%)',
        }}
      />
      {content}
    </div>
  );
};
