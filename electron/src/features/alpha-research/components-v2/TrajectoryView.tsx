/**
 * TrajectoryView —— 任务决策轨迹（T-FM-07，只读）。
 *
 * 数据源 = `GET /alpha-agent/tasks/{id}/trajectory`（后端 trajectory_store
 * 目录扫描 + 结局 join 的读时组装，零持久化）。两处复用：挖掘演化台新
 * tab「决策轨迹」与挖掘历史行操作「查看轨迹」抽屉，都按 task_id 拉取。
 *
 * 展示纪律（全站一致）：
 * - 只标「位置 + 事实」——哪个环节耗时多久、哪轮产出没进实现、因子结局
 *   是什么；绝不写「因为假设错了」这类因果推断，旗标只做定位。
 * - 缺失一律「—」：时长不可解、结局未知、归因降级都不伪装成 0。
 * - 任务 id 只显前 8 位；Phase 词汇与进度侧栏（ProgressSidebar）同词表。
 * - partial（任务未终态）如实标注：这是当前落盘快照，不是最终结论。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, GitBranch, Loader2, RefreshCw } from 'lucide-react';
import { getTaskTrajectory } from '../services-v2/api';
import type {
  TaskTrajectory,
  TrajectoryFactor,
  TrajectoryLoop,
  TrajectoryNode,
} from '../services-v2/api';

/** 五环节中文标签（与 launcher `_PHASE_DIR_MAP` 同词表）。 */
const PHASE_LABELS: Record<string, string> = {
  hypothesis: '生成假设',
  experiment: '设计实验',
  coding: '编写因子代码',
  running: '回测运行',
  feedback: '总结反馈',
};

const PHASE_ORDER = ['hypothesis', 'experiment', 'coding', 'running', 'feedback'];

/** 节点证据状态：ok=载荷+时长都可解 / partial=证据不全 / absent=无任何证据。 */
const NODE_STATUS_LABELS: Record<string, string> = {
  ok: '完成',
  partial: '部分',
  absent: '无记录',
};

const NODE_STATUS_DOT: Record<string, string> = {
  ok: 'bg-emerald-500',
  partial: 'bg-amber-400',
  absent: 'bg-slate-300',
};

const NODE_STATUS_TITLE: Record<string, string> = {
  ok: '该环节已扫到载荷且耗时可解',
  partial: '该环节载荷或计时证据不完整',
  absent: '该环节既无载荷也无计时证据',
};

const OUTCOME_LABELS: Record<string, string> = {
  materialized: '已物化',
  rejected_duplicate: '重复被拒',
  rejected_gate: '门禁被拒',
  superseded: '已被顶替',
  pending: '待定',
};

const OUTCOME_CLS: Record<string, string> = {
  materialized: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  rejected_duplicate: 'border-rose-200 bg-rose-50 text-rose-600',
  rejected_gate: 'border-rose-200 bg-rose-50 text-rose-600',
  superseded: 'border-slate-200 bg-slate-50 text-slate-500',
  pending: 'border-sky-200 bg-sky-50 text-sky-700',
};

/** 定位旗标短标签（tooltip 用后端 flag_reason 全文）。 */
const FLAG_LABELS: Record<string, string> = {
  no_selected: '本轮产出均未进入实现',
  task_failed_here: '任务止步于本轮',
  all_rejected: '本轮产出全部被拒',
};

const MISSING = '—';

const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

function fmtDuration(s: number | null): string {
  if (s == null || !Number.isFinite(s) || s < 0) return MISSING;
  if (s >= 3600) return `${(s / 3600).toFixed(1)}h`;
  if (s >= 60) return `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, '0')}s`;
  return `${Math.round(s)}s`;
}

function nodeTitle(node: TrajectoryNode): string {
  const parts = [PHASE_LABELS[node.phase] ?? node.phase];
  parts.push(NODE_STATUS_TITLE[node.status] ?? node.status);
  if (node.start || node.end) {
    parts.push(`${node.start ?? '?'} → ${node.end ?? '?'}`);
  }
  parts.push(`载荷文件 ${node.pklCount ?? MISSING} 个`);
  return parts.join(' · ');
}

function factorTitle(f: TrajectoryFactor): string {
  const parts: string[] = [];
  parts.push(
    f.selected == null ? '是否进入实现：未知' : f.selected ? '已进入实现' : '未进入实现',
  );
  parts.push(`结局：${f.outcome ? (OUTCOME_LABELS[f.outcome] ?? f.outcome) : '未知'}`);
  if (f.reason) parts.push(f.reason);
  return parts.join(' · ');
}

const FactorChip: React.FC<{ factor: TrajectoryFactor }> = ({ factor }) => (
  <span
    className="inline-flex max-w-72 items-center gap-1 rounded-full border border-border/60 bg-white/70 px-2 py-0.5"
    title={factorTitle(factor)}
  >
    <span className="truncate font-mono text-[11px] text-slate-700">{factor.name || MISSING}</span>
    {factor.selected === true && (
      <span className="shrink-0 rounded-full bg-emerald-100 px-1 text-[9px] font-bold text-emerald-700">
        实现
      </span>
    )}
    {factor.selected === false && (
      <span className="shrink-0 rounded-full bg-slate-100 px-1 text-[9px] font-bold text-slate-400">
        未实现
      </span>
    )}
    {factor.outcome ? (
      <span
        className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-bold ${
          OUTCOME_CLS[factor.outcome] ?? 'border-border/60 bg-white text-muted-foreground'
        }`}
      >
        {OUTCOME_LABELS[factor.outcome] ?? factor.outcome}
      </span>
    ) : (
      <span className="shrink-0 text-[10px] text-muted-foreground" title="结局未知（无物化清单/因子行）">
        {MISSING}
      </span>
    )}
  </span>
);

const NodeCard: React.FC<{ node: TrajectoryNode }> = ({ node }) => (
  <div
    className="min-w-[7.5rem] flex-1 rounded-lg border border-border/50 bg-secondary/10 px-2.5 py-1.5"
    title={nodeTitle(node)}
  >
    <div className="flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${NODE_STATUS_DOT[node.status] ?? 'bg-slate-300'}`} />
      <span className="truncate text-[11px] font-bold text-slate-700">
        {PHASE_LABELS[node.phase] ?? node.phase}
      </span>
    </div>
    <div className="mt-0.5 flex items-center justify-between gap-2 font-mono text-[10px] text-muted-foreground">
      <span>{fmtDuration(node.durationS)}</span>
      <span>
        {NODE_STATUS_LABELS[node.status] ?? node.status}
        {node.pklCount != null ? ` · ${node.pklCount}件` : ''}
      </span>
    </div>
  </div>
);

const LoopCard: React.FC<{ loop: TrajectoryLoop }> = ({ loop }) => {
  // 节点按固定五环节顺序渲染（后端恒给五元；即便缺也给占位，绝不跳格）
  const byPhase = new Map(loop.nodes.map((n) => [n.phase, n]));
  const nodes = PHASE_ORDER.map(
    (p) =>
      byPhase.get(p) ?? {
        phase: p,
        status: 'absent',
        start: null,
        end: null,
        durationS: null,
        pklCount: null,
      },
  );
  const label = loop.loop === 0 ? '首轮' : `Loop ${loop.loop}`;
  return (
    <div
      className={`rounded-xl border p-3 ${
        loop.flagged ? 'border-amber-300/70 bg-amber-50/40' : 'border-border/50 bg-white/50'
      }`}
      data-testid={`trajectory-loop-${loop.loop}`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-xs font-black text-slate-700">{label}</span>
        {loop.flags.map((flag) => (
          <span
            key={flag}
            className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700"
            title={loop.flagReason ?? undefined}
          >
            {FLAG_LABELS[flag] ?? flag}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {nodes.map((n) => (
          <NodeCard key={n.phase} node={n} />
        ))}
      </div>
      <div className="mt-2">
        {loop.factors.length === 0 ? (
          <span className="text-[11px] text-muted-foreground">
            本轮未归因到产出因子（提案为空或载荷不可解析）
          </span>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {loop.factors.map((f) => (
              <FactorChip key={f.name} factor={f} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export interface TrajectoryViewProps {
  /** 目标任务 id（两处复用：演化台焦点任务 / 历史行） */
  taskId: string;
  className?: string;
}

export const TrajectoryView: React.FC<TrajectoryViewProps> = ({ taskId, className }) => {
  const [view, setView] = useState<TaskTrajectory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!taskId) {
      setView(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTaskTrajectory(taskId)
      .then((res) => {
        if (cancelled) return;
        if (!res.success || !res.data) throw new Error(res.error || '加载失败');
        setView(res.data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
        setView(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  if (!taskId) return null;

  return (
    <div className={`space-y-3 ${className ?? ''}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate text-xs font-bold text-slate-700">决策轨迹</span>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground" title={taskId}>
            {taskId.slice(0, 8)}
          </span>
          {view?.direction && (
            <span className="truncate text-[11px] text-muted-foreground" title={view.direction}>
              · {view.direction}
            </span>
          )}
          {view?.status && (
            <span className="shrink-0 rounded-full border border-border/60 bg-white/70 px-1.5 py-px text-[10px] font-bold text-slate-500">
              {STATUS_LABELS[view.status] ?? view.status}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={retry}
          disabled={loading}
          className="shrink-0 inline-flex items-center gap-1 rounded-full border border-border/60 bg-white px-2.5 py-1 text-[11px] font-bold text-slate-600 hover:border-blue-300 hover:text-blue-600 disabled:opacity-40 cursor-pointer"
          title="重新拉取轨迹"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {loading && !view && (
        <div className="flex items-center gap-2 rounded-lg border border-border/50 bg-secondary/10 p-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在扫描任务日志目录…
        </div>
      )}

      {!loading && error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50/70 p-3 text-xs font-bold text-rose-600">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 break-all">{error}</span>
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

      {view && (
        <div className="space-y-3">
          {/* 如实标注（partial / 降级 / 截断），不藏事实 */}
          {view.partial && (
            <p className="rounded-lg border border-sky-200 bg-sky-50/60 px-3 py-2 text-[11px] text-sky-700">
              任务尚未结束——以下为当前已落盘快照，终态后可能继续变化。
            </p>
          )}
          {view.attribution === 'task_level' && (
            <p className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-[11px] text-amber-700">
              某轮载荷解析失败，逐轮因子归因不可用；以下为任务级产出清单（不硬分配到具体轮次）。
            </p>
          )}
          {view.truncated && (
            <p className="rounded-lg border border-border/60 bg-secondary/10 px-3 py-2 text-[11px] text-muted-foreground">
              任务轮次过多，仅保留最近 {view.loops.length} 轮（已裁 {view.droppedLoops} 轮，深轮更接近失败点）。
            </p>
          )}

          {view.loops.length === 0 && view.attribution === 'loop' ? (
            <div className="rounded-lg border border-border/50 bg-secondary/10 p-4 text-center text-xs text-muted-foreground">
              没有扫到决策轨迹记录（日志目录为空）。
            </div>
          ) : (
            view.loops.map((loop) => <LoopCard key={loop.loop} loop={loop} />)
          )}

          {/* task_level 降级：任务级因子清单 */}
          {view.attribution === 'task_level' && (
            <div className="rounded-xl border border-border/50 bg-white/50 p-3">
              <div className="mb-2 text-xs font-black text-slate-700">任务级产出清单</div>
              {view.factors.length === 0 ? (
                <span className="text-[11px] text-muted-foreground">该任务没有可归因的因子记录。</span>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {view.factors.map((f) => (
                    <FactorChip key={f.name} factor={f} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default TrajectoryView;
