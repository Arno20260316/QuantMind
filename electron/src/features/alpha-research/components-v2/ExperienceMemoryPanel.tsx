/**
 * 经验记忆/禁区（T-FM-04）—— 设置页选方向时的「历史拒因聚类 + 饱和方向」读数。
 *
 * 与下一轮 LLM 挖掘提示词**同源**（后端 experience_memory.build_memory 同一组装，
 * 注入段落任务日志 experience_context.md）：这里展示的是事实统计，不是评分；
 * 真正的方向选择权在挖掘算法手里。
 *
 * 展示纪律与全站一致：缺失一律「—」（计数 0 与「没有统计」是两回事）；
 * 对不上方向的历史查重命中如实显示为「(未归因)」，不硬编造映射。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, History, Loader2, RefreshCw } from 'lucide-react';
import { getExperienceMemory } from '../services-v2/experienceMemoryApi';
import type { ExperienceMemoryView } from '../services-v2/experienceMemoryApi';

/** 默认口径 = 主挖掘范围；多市场记忆留给后续任务按市场切换 */
const DEFAULT_MARKET = 'a_share';

const MARKET_LABELS: Record<string, string> = {
  a_share: 'A股',
  hong_kong: '港股',
  us_stock: '美股',
  crypto: '加密货币',
  futures: '期货',
};

const MISSING = '—';

/** 计数渲染：null（没有统计）与 0（真值 0）分开显（同全站缺失纪律）。 */
function fmtCount(value: number | null): string {
  return value == null ? MISSING : String(value);
}

/** ISO（aware UTC，带 Z）→ 本地「YYYY-MM-DD HH:mm」；解析失败原样返回。 */
function fmtTime(iso: string | null): string {
  if (!iso) return MISSING;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

interface ExperienceMemoryPanelProps {
  market?: string;
}

export const ExperienceMemoryPanel: React.FC<ExperienceMemoryPanelProps> = ({
  market = DEFAULT_MARKET,
}) => {
  const [view, setView] = useState<ExperienceMemoryView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getExperienceMemory({ market })
      .then((res) => {
        if (cancelled) return;
        if (!res.success || !res.data) throw new Error(res.error || '加载失败');
        setView(res.data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(`经验记忆读取失败：${msg}`);
        setView(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [market, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  const scope = MARKET_LABELS[market] ?? market;
  const buckets = view?.reasonBuckets ?? [];
  const saturated = view?.saturatedDirections ?? [];

  return (
    <div className="pt-4 border-t border-border/50">
      <div className="flex items-center justify-between mb-3">
        <label className="text-sm font-medium inline-flex items-center gap-1.5">
          <History className="h-4 w-4" />
          经验记忆 / 禁区（历史拒因与饱和方向）
        </label>
        <span className="text-xs text-muted-foreground">
          {scope}
          {view?.generatedAt ? ` · 生成于 ${fmtTime(view.generatedAt)}` : ''}
        </span>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        下一轮 LLM 挖掘会读到这些事实统计（与注入同源）：拒因聚类 = 历史被拒因子的归一原因；
        饱和方向 = 统计派值级查重命中聚集的方向——命中已知饱和方向的提案需要说明差异化理由。
      </p>

      {loading && (
        <div className="flex items-center gap-2 rounded-lg border border-border/50 bg-secondary/10 p-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在读取经验记忆…
        </div>
      )}

      {!loading && error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50/70 p-3 text-xs font-bold text-rose-600">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1 min-w-0 break-all">{error}</span>
          <button
            type="button"
            onClick={retry}
            className="shrink-0 inline-flex items-center gap-1 rounded-full border border-rose-200 bg-white px-2.5 py-1 text-[11px] font-bold text-rose-600 hover:bg-rose-50 cursor-pointer"
          >
            <RefreshCw className="h-3 w-3" />
            重试
          </button>
        </div>
      )}

      {!loading && !error && view && (
        <div className="space-y-3">
          {/* 拒因聚类 */}
          <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium">拒因聚类（归一原因 × 次数）</span>
              <span className="text-[11px] text-muted-foreground">
                任务史 {fmtCount(view.sources.tasks)} 条 · 物化清单{' '}
                {fmtCount(view.sources.manifestEntries)} 条 ·{' '}
                统计派报告 {view.sources.report ? `有（${fmtTime(view.sources.reportGeneratedAt)}）` : '无'}
              </span>
            </div>
            {buckets.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {view.available ? '暂无归一的拒因记录。' : `${MISSING} 暂无历史统计——跑几轮挖掘后，拒因会自动归一到这里。`}
              </p>
            ) : (
              <div className="space-y-1.5">
                {buckets.map((b) => (
                  <div key={b.bucket} className="flex items-center gap-3 py-0.5">
                    <span
                      className="w-56 shrink-0 truncate text-xs font-medium"
                      title={
                        b.sampleColumns.length
                          ? `样例列：${b.sampleColumns.join('、')}${b.sampleDirs.length ? `｜方向：${b.sampleDirs.join('、')}` : ''}`
                          : undefined
                      }
                    >
                      {b.label}
                    </span>
                    <span
                      className="w-36 shrink-0 truncate font-mono text-[11px] text-muted-foreground"
                      title={b.bucket}
                    >
                      {b.bucket}
                    </span>
                    <span className="w-12 shrink-0 font-mono text-[11px] text-muted-foreground">
                      ×{fmtCount(b.count)}
                    </span>
                    <span className="w-36 shrink-0 font-mono text-[11px] text-muted-foreground">
                      最近 {fmtTime(b.lastAt)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                      来源：{b.sources.length ? b.sources.join('+') : MISSING}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 饱和方向 */}
          <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium">饱和方向（值级查重命中聚集）</span>
              <span className="text-[11px] text-muted-foreground">近 90 日命中数是配额避让的硬依据</span>
            </div>
            {saturated.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {view.available ? '暂无值级查重命中的方向。' : `${MISSING} 暂无可用历史统计。`}
              </p>
            ) : (
              <div className="space-y-1.5">
                {saturated.map((s) => (
                  <div key={s.direction} className="flex items-center gap-3 py-0.5">
                    <span className="w-56 shrink-0 truncate text-xs font-medium" title={s.direction}>
                      {s.label}
                    </span>
                    <span className="w-32 shrink-0 font-mono text-[11px] text-muted-foreground">
                      命中 {fmtCount(s.dupHits)}（90日 {fmtCount(s.dupHits90d)}）
                    </span>
                    <span className="w-20 shrink-0 font-mono text-[11px] text-muted-foreground">
                      已物化 {fmtCount(s.materializedCount)}
                    </span>
                    <span
                      className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground"
                      title={s.topOpponent ?? undefined}
                    >
                      最强对手 {s.topOpponent ?? MISSING}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default ExperienceMemoryPanel;
