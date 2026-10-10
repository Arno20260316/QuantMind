import React, { useEffect, useMemo, useState } from 'react';
import { CalendarRange, Loader2, RotateCcw } from 'lucide-react';
import type { MiningConfigSelection } from '../types-v2';
import {
  EMPTY_MINING_SELECTION,
  getMiningConfigOptions,
  hasCustomMiningConfig,
  type MiningConfigOptions,
  type MiningWindowSpan,
} from '../services-v2/miningConfigApi';
import {
  algorithmSupportsFreq,
  type MiningAlgorithm,
} from '../services-v2/miningAlgorithmApi';

/**
 * 挖掘配置面板（移植 P1/P1.5，仅 A 股）：频率 / 挖掘区间 /
 * 检验区间 / 数据集多选。
 *
 * 口径纪律（与后端 mining_config 的分工）：
 * - **默认模式一个参数都不下发**——默认窗口（日线：覆盖末端前推 4 年挖掘 +
 *   1 年检验；分钟：数据面可用连续块）由后端算，面板只展示服务端返回的值，
 *   前端不复算日期；
 * - **自定义模式：显示什么就下发什么**——预览与提交走同一份 payload，
 *   面板里看到的生效窗口/分段就是任务实际会用的那一份；
 * - 日期上限 = `backtest_end_guard`；晚于它的终点会被服务端**退让**到该日
 *   （不是拒绝），面板把这件事显式写出来，不装作没发生；
 * - **分钟频率下**：窗口限位改用 `minute.usable`（跨数据断层的窗口会被
 *   服务端 400），断层表如实上屏；数据集行整块隐藏（1min 面不叠日线富化列，
 *   带去会被后端拒）——不是「选择器坏了」而是口径如此；
 * - 分段（train/valid/test）来自服务端 `qlib_segments`（与实跑同一组纯函数），
 *   本地不做任何日期算术。
 */

const PREVIEW_DEBOUNCE_MS = 350;

interface MiningConfigPanelProps {
  value: MiningConfigSelection;
  onChange: (next: MiningConfigSelection) => void;
  disabled?: boolean;
  /**
   * 生效算法（移植 P2）：能力面收窄面板——只渲染该算法支持的频率档位、
   * 不消费数据集的算法收起数据集行。null = 能力面未知（目录未加载），
   * 按全量渲染，不拿猜测收起控件。
   */
  algorithm?: MiningAlgorithm | null;
}

function spanText(raw: MiningWindowSpan | null | undefined): string {
  return raw ? `${raw.start} ~ ${raw.end}` : '—';
}

/** 断层表压缩：最多 3 段，多出的折成「共 N 段」（与后端 gap_text 同口径） */
function gapsText(gaps: MiningWindowSpan[]): string {
  const head = gaps.slice(0, 3).map((g) => `${g.start} ~ ${g.end}`).join('、');
  return gaps.length <= 3 ? head : `${head}、…（共 ${gaps.length} 段）`;
}

export const MiningConfigPanel: React.FC<MiningConfigPanelProps> = ({
  value,
  onChange,
  disabled = false,
  algorithm = null,
}) => {
  // 窗口模式只认窗口/数据集是否动过——频率是正交的一行（选分钟但窗口全空
  // 仍是「默认窗」：由服务端物化成可用连续块）
  const [mode, setMode] = useState<'default' | 'custom'>(() =>
    value.miningStart ||
    value.miningEnd ||
    value.validStart ||
    value.validEnd ||
    (value.datasets && value.datasets.length > 0)
      ? 'custom'
      : 'default',
  );
  const [options, setOptions] = useState<MiningConfigOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(true);

  // 目录 + 分段预览一次拉齐（预览参数 = 当前选择 = 提交形态）。
  // 防抖只压预览请求，弹错/换值立刻反映在输入框上。
  useEffect(() => {
    let cancelled = false;
    setPreviewing(true);
    const timer = setTimeout(() => {
      getMiningConfigOptions(value).then((res) => {
        if (cancelled) return;
        if (res.success && res.data) {
          setOptions(res.data);
          setOptionsError(null);
        } else {
          setOptionsError(res.error || '挖掘配置获取失败');
        }
        setPreviewing(false);
      });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [value]);

  const datasets = useMemo(() => options?.datasets ?? [], [options]);
  // 频率档位 × 算法能力面：算法不支持的档位不渲染（选了提交必被后端 400）
  const freqs = useMemo(
    () =>
      (options?.freqs ?? []).filter((f) => algorithmSupportsFreq(algorithm, f.value)),
    [options, algorithm],
  );
  const usesDatasets = algorithm?.usesDatasets !== false;
  const allKeys = useMemo(() => datasets.map((d) => d.key), [datasets]);
  const selectedKeys = useMemo(
    () => new Set(value.datasets ?? allKeys),
    [value.datasets, allKeys],
  );

  const patch = (part: Partial<MiningConfigSelection>) =>
    onChange({ ...value, ...part });

  const isMinute = value.freq === '1min';
  const minute = options?.minute ?? null;

  /**
   * 换挡（日线 ⇄ 分钟）：两套数据面的日期上下限不同（日线 4+1 年 vs 分钟
   * 3–4 个月的可用块），旧档的窗口带过去必然越界/跨断层 → 一律清零，
   * 由新档的服务端默认窗接管。数据集在分钟档没有语义（后端正则拒），
   * 一并清空；换回日线即恢复「全选」。
   */
  const switchFreq = (next: string) => {
    if (next === value.freq) return;
    setMode('default');
    onChange({ ...EMPTY_MINING_SELECTION, freq: next === '1min' ? '1min' : null });
  };

  const switchToDefault = () => {
    setMode('default');
    // 频率不是「窗口默认」的一部分：清窗口保留当前档位
    onChange({ ...EMPTY_MINING_SELECTION, freq: value.freq });
  };

  const switchToCustom = () => {
    setMode('custom');
    // 以服务端默认窗口预填（用户改哪端都行；不改就等价于默认，只是显式下发）
    patch({
      miningStart: options?.defaults.mining?.start ?? null,
      miningEnd: options?.defaults.mining?.end ?? null,
      validStart: options?.defaults.validation?.start ?? null,
      validEnd: options?.defaults.validation?.end ?? null,
    });
  };

  const toggleDataset = (key: string) => {
    const current = new Set(selectedKeys);
    if (current.has(key)) {
      // 全不选没有语义（后端空列表=全选，会静默反转成全部数据集）
      if (current.size <= 1) return;
      current.delete(key);
    } else {
      current.add(key);
    }
    const next = allKeys.filter((k) => current.has(k));
    patch({ datasets: next.length === allKeys.length ? null : next });
  };

  const guard = options?.backtestEndGuard ?? null;
  const coverage = options?.coverage ?? null;
  const windows = options?.windows;
  // 分钟档日期下界 = 可用连续块起点（coverage.start 含断层前的碎片，
  // 用它当 min 会让用户选出必然被 400 的窗口）
  const minBound = isMinute
    ? (minute?.usable?.start ?? coverage?.start)
    : coverage?.start;
  // 服务端退让检测：显示的终点 < 请求的终点（写超可回测末端被原地收口）
  const clampNotes: string[] = [];
  if (windows?.mining && value.miningEnd && windows.mining.end !== value.miningEnd) {
    clampNotes.push(`挖掘终点 ${value.miningEnd} → ${windows.mining.end}`);
  }
  if (
    windows?.validation &&
    value.validEnd &&
    windows.validation.end !== value.validEnd
  ) {
    clampNotes.push(`检验终点 ${value.validEnd} → ${windows.validation.end}`);
  }

  const dateInput = (
    label: string,
    ariaLabel: string,
    valueIso: string | null,
    onEdit: (next: string) => void,
    extra?: { min?: string | null; max?: string | null },
  ) => (
    <label className="flex flex-col gap-0.5">
      <span className="text-[10px] font-bold text-slate-400">{label}</span>
      <input
        type="date"
        aria-label={ariaLabel}
        value={valueIso ?? ''}
        min={extra?.min ?? minBound ?? undefined}
        max={extra?.max ?? guard ?? undefined}
        disabled={disabled}
        onChange={(e) => onEdit(e.target.value)}
        className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-mono text-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-300 disabled:opacity-40"
      />
    </label>
  );

  return (
    <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 px-3 py-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <CalendarRange className="h-3.5 w-3.5 text-indigo-500" />
        <span className="flex-1 min-w-0 text-[11px] font-bold text-slate-600">
          挖掘配置（仅 A 股）：挖掘频率 / 自定义挖掘区间 / 样本外检验区间
          {usesDatasets ? ' / 挖掘数据集' : ''}
        </span>
        <button
          type="button"
          onClick={switchToDefault}
          disabled={disabled || mode === 'default'}
          title="恢复默认窗口（覆盖末端前推 4 年挖掘 + 1 年检验）与全部数据集"
          className="shrink-0 flex items-center gap-1 text-[10px] font-bold text-slate-400 hover:text-indigo-600 disabled:opacity-40 disabled:hover:text-slate-400 transition-colors cursor-pointer"
        >
          <RotateCcw className="h-3 w-3" />
          恢复默认
        </button>
      </div>

      {/* 频率档位：目录来自服务端（未给出不渲染——前端不发明档位）。
          换挡会清空窗口/数据集：两套数据面的日期上下限不同，带过去必然越界 */}
      {freqs.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap mb-2">
          <span className="text-[10px] font-bold text-slate-400">频率</span>
          {freqs.map((f) => {
            const active =
              f.value === 'day' ? !value.freq || value.freq === 'day' : value.freq === f.value;
            return (
              <button
                key={f.value}
                type="button"
                disabled={disabled}
                onClick={() => switchFreq(f.value)}
                title={
                  f.value === '1min'
                    ? '分钟原生挖掘：1min 数据面（OHLCV/amount/vwap），窗口须落在可用连续块内；换挡会重置窗口'
                    : '日线挖掘（默认）：覆盖末端前推 4 年挖掘 + 1 年检验'
                }
                className={`rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 disabled:opacity-40 ${
                  active
                    ? 'bg-violet-50 text-violet-700 ring-1 ring-violet-200 shadow-xs'
                    : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
                }`}
              >
                {f.label}
              </button>
            );
          })}
          <span className="text-[10px] text-slate-400">
            {isMinute
              ? '分钟口径独立成任务：不叠加日线富化列（数据集档位自动收起）'
              : usesDatasets
                ? '日线口径：可叠加数据集富化列'
                : `日线口径：${algorithm?.name || '当前算法'}不消费数据集选择`}
          </span>
        </div>
      )}

      {/* 窗口模式：默认 = 零参数下发（后端算）；自定义 = 下面填什么就下发什么 */}
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-[10px] font-bold text-slate-400">窗口</span>
        {(
          [
            {
              id: 'default',
              label: isMinute
                ? '默认（分钟可用连续块）'
                : '默认（近 4 年挖掘 + 1 年检验）',
            },
            { id: 'custom', label: '自定义区间' },
          ] as const
        ).map((m) => (
          <button
            key={m.id}
            type="button"
            disabled={
              disabled || (m.id === 'custom' && (!options || (isMinute && !minute?.ready)))
            }
            onClick={() => (m.id === 'default' ? switchToDefault() : switchToCustom())}
            className={`rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 disabled:opacity-40 ${
              mode === m.id
                ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-200 shadow-xs'
                : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === 'custom' && (
        <div className="flex flex-col gap-2 mb-2">
          <div className="flex items-end gap-2">
            <span className="w-14 shrink-0 pb-1 text-[10px] font-bold text-slate-400">
              挖掘区间
            </span>
            {dateInput(
              '起点',
              '挖掘区间起点',
              value.miningStart,
              (v) => patch({ miningStart: v || null }),
            )}
            {dateInput(
              '终点',
              '挖掘区间终点',
              value.miningEnd,
              (v) => patch({ miningEnd: v || null }),
              { max: guard },
            )}
          </div>
          <div className="flex items-end gap-2">
            <span className="w-14 shrink-0 pb-1 text-[10px] font-bold text-slate-400">
              检验区间
            </span>
            {dateInput(
              '起点（留空=不启用）',
              '检验区间起点',
              value.validStart,
              (v) => patch({ validStart: v || null }),
            )}
            {dateInput(
              '终点',
              '检验区间终点',
              value.validEnd,
              (v) => patch({ validEnd: v || null }),
              { max: guard },
            )}
            {(value.validStart || value.validEnd) && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => patch({ validStart: null, validEnd: null })}
                title="不启用样本外检验：分段退化为挖掘区间内 70/15/15 切分"
                className="pb-1 text-[10px] font-bold text-slate-400 hover:text-rose-500 disabled:opacity-40 transition-colors cursor-pointer"
              >
                不启用
              </button>
            )}
          </div>
        </div>
      )}

      {/* 数据集多选：全选 = 不下发参数（任务行零痕迹，h5 缓存沿用旧文件名）。
          分钟档整块不渲染：1min 数据面只有 OHLCV/amount/vwap，带数据集会被
          后端 400——收起是口径，不是加载失败。算法声明 uses_datasets=false
          时同理（能力面不消费数据集选择） */}
      {isMinute ? (
        <div className="mb-2 text-[10px] text-slate-400">
          数据集：分钟口径不适用（1min 数据面仅 OHLCV/amount/vwap，不叠加日线富化列）
        </div>
      ) : !usesDatasets ? (
        <div className="mb-2 text-[10px] text-slate-400">
          数据集：{algorithm?.name || '当前算法'}不消费数据集选择（能力面 uses_datasets=false）
        </div>
      ) : (
      <div className="flex items-center gap-1.5 flex-wrap mb-2">
        <span className="text-[10px] font-bold text-slate-400">数据集</span>
        {datasets.length === 0 ? (
          <span className="text-[10px] text-slate-400">目录加载中…</span>
        ) : (
          <>
            {datasets.map((d) => {
              const selected = selectedKeys.has(d.key);
              const isLast = selected && selectedKeys.size <= 1;
              return (
                <button
                  key={d.key}
                  type="button"
                  disabled={disabled || isLast}
                  onClick={() => toggleDataset(d.key)}
                  title={
                    isLast
                      ? '至少保留一个数据集（全不选等价于全选，没有意义）'
                      : `${d.label}（${d.key}）：选中即喂给挖掘的特征列包含该数据集`
                  }
                  className={`rounded-full px-2.5 py-[3px] text-[11px] font-semibold transition-all duration-200 disabled:cursor-not-allowed ${
                    selected
                      ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 shadow-xs'
                      : 'text-slate-400 ring-1 ring-slate-200 hover:text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  {d.label}
                </button>
              );
            })}
            <span className="text-[10px] text-slate-400">
              {value.datasets === null
                ? '全选（不写参数）'
                : `已选 ${selectedKeys.size}/${allKeys.length}`}
            </span>
          </>
        )}
      </div>
      )}

      {/* 服务端预览：生效窗口 + 分段（与实跑同源）；错误原文上屏，不吞 */}
      <div className="rounded-lg bg-white/70 px-2.5 py-1.5 text-[10px] leading-relaxed">
        {previewing && !options ? (
          <span className="flex items-center gap-1.5 font-bold text-slate-400">
            <Loader2 className="h-3 w-3 animate-spin" />
            正在读取挖掘配置…
          </span>
        ) : optionsError ? (
          <span className="font-bold text-rose-500 break-all">{optionsError}</span>
        ) : (
          <>
            {/* 分钟数据面事实：可用连续块 + 断层表 + 未就绪原因（一律如实上屏） */}
            {isMinute && minute && (
              <div className="mb-1 flex flex-col gap-0.5">
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-600">
                  <span>
                    分钟可用连续块{' '}
                    <span className="font-mono">{spanText(minute.usable)}</span>
                    <span className="text-slate-400">
                      （{minute.usableDays} 个交易日）
                    </span>
                  </span>
                </div>
                {minute.gaps.length > 0 && (
                  <div className="text-amber-600">
                    数据断层 <span className="font-mono">{gapsText(minute.gaps)}</span>
                    <span className="text-slate-400">
                      （跨断层窗口会被拒，请收进可用连续块）
                    </span>
                  </div>
                )}
                {!minute.ready && (
                  <div className="font-bold text-rose-500 break-all">
                    分钟数据面不可用：{minute.error || minute.providerDir || '未构建'}
                  </div>
                )}
              </div>
            )}
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-500">
              <span>
                数据覆盖 <span className="font-mono">{spanText(coverage)}</span>
              </span>
              <span>
                可回测末端{' '}
                <span className="font-mono">{guard ?? '—'}</span>
                <span className="text-slate-400">（qlib 按 t+1 成交，终点须留次日行情）</span>
              </span>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-600">
              <span>
                挖掘 <span className="font-mono">{spanText(windows?.mining)}</span>
              </span>
              <span>
                {windows?.validation ? (
                  <>
                    检验{' '}
                    <span className="font-mono">{spanText(windows.validation)}</span>
                    <span className="text-slate-400">（样本外）</span>
                  </>
                ) : (
                  <>
                    检验{' '}
                    <span className="text-slate-400">
                      不启用（分段 = 挖掘区间内 70/15/15 切分）
                    </span>
                  </>
                )}
              </span>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-slate-500">
              <span>
                train <span className="font-mono">{spanText(options?.segments.train)}</span>
              </span>
              <span>
                valid <span className="font-mono">{spanText(options?.segments.valid)}</span>
              </span>
              <span>
                test <span className="font-mono">{spanText(options?.segments.test)}</span>
              </span>
            </div>
            {clampNotes.length > 0 && (
              <div className="mt-0.5 font-bold text-amber-600">
                终点已退让到可回测末端：{clampNotes.join('；')}
              </div>
            )}
            {previewing && (
              <div className="mt-0.5 text-slate-400">预览更新中…</div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
