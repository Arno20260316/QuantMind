/**
 * 方向配额（T-FE-10）—— 设置页选方向时的「代理模型打分 + 三档名额预演」读数。
 *
 * 与 /evolve 的 quota 抽样**同源**（后端 direction_quota 的同一 preview/
 * allocate 实现）：代理模型 v1 = 经验贝叶斯平滑成功率（Jeffreys 先验，
 * 无史 = 0.5 中性）× 饱和惩罚（近 90 日值级查重命中）；三档名额 =
 * 探索 30% / 利用 50% / 变异 20%。这里展示的是事实统计与预演，不是强制；
 * quota 模式下真正的抽样决策与复现凭证在派发时落进任务行 direction_meta。
 *
 * 展示纪律与全站一致：缺失一律「—」（0 与「没有统计」是两回事）；
 * 饱和方向在三档一律剔除——若回退（剔尽），档位行如实标注。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Gauge, Loader2, RefreshCw } from 'lucide-react';
import {
  DIRECTION_QUOTA_DEFAULT_MARKET,
  DIRECTION_QUOTA_DEFAULT_N,
  getDirectionQuotaPreview,
} from '../services-v2/directionQuotaApi';
import type { QuotaPreviewView, QuotaTier } from '../services-v2/directionQuotaApi';

/** 默认口径 = 主挖掘范围；多市场配额预览留给后续任务按市场切换 */
const DEFAULT_MARKET = 'a_share';

const MARKET_LABELS: Record<string, string> = {
  a_share: 'A股',
  hong_kong: '港股',
  us_stock: '美股',
  crypto: '加密货币',
  futures: '期货',
};

const MISSING = '—';

const TIER_LABELS: Record<QuotaTier, string> = {
  explore: '探索',
  exploit: '利用',
  mutate: '变异',
};

const TIER_DESC: Record<QuotaTier, string> = {
  explore: '空白度加权（挖得越少权重越高）',
  exploit: '打分 top-K 归一（历史成功率高的方向优先）',
  mutate: '最近成功任务的方向集，均匀抽取',
};

const TIER_BADGE: Record<QuotaTier, string> = {
  explore: 'bg-sky-100 text-sky-700 border-sky-200',
  exploit: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  mutate: 'bg-violet-100 text-violet-700 border-violet-200',
};

const TIER_BAR: Record<QuotaTier, string> = {
  explore: 'bg-sky-400/80',
  exploit: 'bg-emerald-400/80',
  mutate: 'bg-violet-400/80',
};

/** 档位加权口径文案（与后端 WEIGHTING_* 常量同词表；未知值原样呈现）。 */
const WEIGHTING_LABELS: Record<string, string> = {
  blankness: '空白度加权',
  proxy_score: '打分归一',
  uniform: '均匀抽取',
};

/** 档位回退标注（saturated=剔尽回退同档全集；mutate_empty=无成功史退利用档）。 */
const FALLBACK_LABELS: Record<string, string> = {
  saturated: '饱和剔尽 · 已回退同档全集',
  mutate_empty: '无近期成功方向 · 已退利用档',
};

/** 预演名额可选值（1..10 与 /evolve num_directions 同闸；面板给常用档）。 */
const N_CHOICES = [1, 2, 3, 5, 8];

const SHOW_POOL_CHIPS = 6;

function fmtPercent(v: number | null): string {
  return v == null ? MISSING : `${Math.round(v * 100)}%`;
}

function fmtNum(v: number | null): string {
  return v == null ? MISSING : String(v);
}

/** score ∈ (0,1]：条形宽度直接用百分比；0/缺失显「—」条（不画假条）。 */
function barWidth(score: number | null): string {
  if (score == null || !(score > 0)) return '0%';
  return `${Math.min(100, Math.round(score * 100))}%`;
}

interface DirectionQuotaPanelProps {
  market?: string;
  /** 候选方向词表（设置页选中列表）；留空 = 只出全史打分表，无档位预演。 */
  directions?: string[];
  /** 预演名额数（受控初值；面板内可切换） */
  defaultN?: number;
}

export const DirectionQuotaPanel: React.FC<DirectionQuotaPanelProps> = ({
  market = DEFAULT_MARKET,
  directions,
  defaultN = DIRECTION_QUOTA_DEFAULT_N,
}) => {
  const [view, setView] = useState<QuotaPreviewView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [n, setN] = useState(defaultN);

  const directionsKey = useMemo(
    () =>
      (directions ?? [])
        .map((d) => (typeof d === 'string' ? d.trim() : ''))
        .filter(Boolean)
        .join('\n'),
    [directions],
  );

  useEffect(() => {
    let cancelled = false;
    // 勾选方向时逐次防抖：候选项连点只发最后一笔
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      const dirs = directionsKey ? directionsKey.split('\n') : [];
      getDirectionQuotaPreview({ market, directions: dirs, n })
        .then((res) => {
          if (cancelled) return;
          if (!res.success || !res.data) throw new Error(res.error || '加载失败');
          setView(res.data);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          const msg = err instanceof Error ? err.message : String(err);
          setError(`方向配额读取失败：${msg}`);
          setView(null);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [market, directionsKey, n, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  const scope = MARKET_LABELS[market] ?? market;
  const rows = view?.directions ?? [];
  const tiers = view?.tiers ?? null;
  const slots = view?.slots ?? null;

  return (
    <div className="pt-4 border-t border-border/50">
      <div className="flex items-center justify-between mb-3">
        <label className="text-sm font-medium inline-flex items-center gap-1.5">
          <Gauge className="h-4 w-4" />
          方向配额（探索 / 利用 / 变异）
        </label>
        <span className="text-xs text-muted-foreground">
          {scope}
          {slots && view?.n != null
            ? ` · 预演 N=${view.n} → 探索 ${fmtNum(slots.explore)} / 利用 ${fmtNum(
                slots.exploit,
              )} / 变异 ${fmtNum(slots.mutate)}`
            : ''}
        </span>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        启动模式选「方向配额」时，后端按三档预算抽样：成功率 = 经验贝叶斯平滑（无史 = 50%
        中性），饱和惩罚 = 近 90 日值级查重命中（命中 ≥{' '}
        {fmtNum(view?.saturatedThreshold ?? null)} 次的方向三档一律剔除）。此处只做预演，
        决策与复现凭证在派发时落进任务证据。
      </p>

      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs text-muted-foreground">预演名额</span>
        <div className="inline-flex overflow-hidden rounded-full border border-border/60">
          {N_CHOICES.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setN(v)}
              className={`px-2.5 py-0.5 text-[11px] font-medium transition-colors cursor-pointer ${
                n === v
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-transparent text-muted-foreground hover:bg-secondary/40'
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 rounded-lg border border-border/50 bg-secondary/10 p-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在读取方向配额预演…
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

      {!loading && !error && view && !view.available && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-700">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1 min-w-0">
            {view.error ?? `${MISSING} 方向史/饱和数据读取出错，配额预览暂不可用。`}
          </span>
        </div>
      )}

      {!loading && !error && view?.available && (
        <div className="space-y-3">
          {/* 配额条：三档预算比例（额数随 N 在头部展示） */}
          <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium">三档预算</span>
              <span className="text-[11px] text-muted-foreground">
                探索 {fmtPercent(view.quota.explore)} / 利用 {fmtPercent(view.quota.exploit)}{' '}
                / 变异 {fmtPercent(view.quota.mutate)}
                {view.recentSuccessTasks != null
                  ? ` · 变异池源 = 最近 ${view.recentSuccessTasks} 个成功任务`
                  : ''}
              </span>
            </div>
            <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-secondary/40">
              {(Object.keys(TIER_LABELS) as QuotaTier[]).map((t) => (
                <div
                  key={t}
                  className={TIER_BAR[t]}
                  style={{ width: `${(view.quota[t] ?? 0) * 100}%` }}
                  title={`${TIER_LABELS[t]} ${fmtPercent(view.quota[t])}`}
                />
              ))}
            </div>
          </div>

          {rows.length === 0 ? (
            <p className="rounded-lg border border-border/50 bg-secondary/10 p-3 text-xs text-muted-foreground">
              暂无方向任务史——跑几轮挖掘后，成功率与饱和评分会出现在这里。
            </p>
          ) : (
            <>
              {/* 档位预演（候选词表为空时为 null：只出全史打分表） */}
              {tiers && (
                <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
                  <div className="mb-2">
                    <span className="text-xs font-medium">档位预演（本次名额如何分配）</span>
                  </div>
                  <div className="space-y-2">
                    {(Object.keys(TIER_LABELS) as QuotaTier[]).map((t) => {
                      const plan = tiers[t];
                      return (
                        <div key={t} className="flex items-start gap-3 py-0.5">
                          <span
                            className={`mt-0.5 w-12 shrink-0 rounded-full border px-2 py-0.5 text-center text-[11px] font-medium ${TIER_BADGE[t]}`}
                          >
                            {TIER_LABELS[t]}
                          </span>
                          <span className="mt-0.5 w-14 shrink-0 font-mono text-[11px] text-muted-foreground">
                            {fmtNum(slots?.[t] ?? null)} 席
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] text-muted-foreground">
                                {plan.weighting
                                  ? (WEIGHTING_LABELS[plan.weighting] ?? plan.weighting)
                                  : TIER_DESC[t]}
                              </span>
                              {plan.fallback && (
                                <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">
                                  {FALLBACK_LABELS[plan.fallback] ?? plan.fallback}
                                </span>
                              )}
                            </div>
                            {plan.pool.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {plan.pool.slice(0, SHOW_POOL_CHIPS).map((d) => (
                                  <span
                                    key={d}
                                    className="max-w-56 truncate rounded-full border border-border/50 bg-white/60 px-2 py-0.5 text-[11px] text-muted-foreground"
                                    title={d}
                                  >
                                    {d}
                                  </span>
                                ))}
                                {plan.pool.length > SHOW_POOL_CHIPS && (
                                  <span className="px-1 py-0.5 text-[11px] text-muted-foreground">
                                    +{plan.pool.length - SHOW_POOL_CHIPS}
                                  </span>
                                )}
                              </div>
                            )}
                            {plan.pool.length === 0 && (
                              <span className="text-[11px] text-muted-foreground">
                                本档暂无可用方向（剔尽回退链已尽力）
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* 打分表 */}
              <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium">
                    代理模型打分（成功率 × 饱和惩罚）
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    口径 = 本用户全史（终态任务为分母）
                  </span>
                </div>
                <div className="space-y-1.5">
                  {rows.map((r) => (
                    <div key={r.direction} className="flex items-center gap-3 py-0.5">
                      <span
                        className="w-48 shrink-0 truncate text-xs font-medium"
                        title={r.direction}
                      >
                        {r.direction}
                      </span>
                      <span
                        className="w-40 shrink-0 font-mono text-[11px] text-muted-foreground"
                        title="成功 = 已完成且产出 ≥1 因子；分母只算终态任务"
                      >
                        成功 {fmtNum(r.nSuccess)}/{fmtNum(r.nTasks)}
                        {r.attempts != null ? ` · 挖过 ${r.attempts} 次` : ''}
                      </span>
                      <span className="w-12 shrink-0 font-mono text-[11px] text-muted-foreground">
                        p={fmtPercent(r.p)}
                      </span>
                      <span
                        className="w-28 shrink-0 font-mono text-[11px] text-muted-foreground"
                        title="近 90 日值级查重命中数（饱和惩罚依据）"
                      >
                        90日命中 {fmtNum(r.dupHits90d)}
                      </span>
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-secondary/40">
                          <span
                            className={`block h-full rounded-full ${
                              r.saturated ? 'bg-rose-400/80' : 'bg-primary/70'
                            }`}
                            style={{ width: barWidth(r.score) }}
                          />
                        </span>
                        <span className="w-10 shrink-0 text-right font-mono text-[11px]">
                          {r.score == null ? MISSING : r.score.toFixed(3)}
                        </span>
                      </span>
                      {r.saturated && (
                        <span
                          className="shrink-0 rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-600"
                          title="三档一律剔除；回退时才会出现在档位池"
                        >
                          饱和·已剔除
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* 最近成功方向（变异档池源） */}
              {view.recentSuccessDirs.length > 0 && (
                <div className="rounded-lg border border-border/50 bg-secondary/10 p-3">
                  <div className="mb-1.5 text-xs font-medium">
                    最近成功方向（变异档池源）
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {view.recentSuccessDirs.map((d) => (
                      <span
                        key={d}
                        className="max-w-56 truncate rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] text-violet-700"
                        title={d}
                      >
                        {d}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default DirectionQuotaPanel;
