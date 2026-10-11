import React, { useCallback, useState } from 'react';
import { Activity, GitBranch, Square } from 'lucide-react';
import { ProgressSidebar } from '../components-v2/ProgressSidebar';
import { LiveCharts } from '../components-v2/LiveCharts';
import { FactorStatsRow } from '../components-v2/FactorStatsRow';
import { FactorList } from '../components-v2/FactorList';
import { TrajectoryView } from '../components-v2/TrajectoryView';
import { useTaskContext } from '../context-v2/TaskContext';
import { useBacktestQueue } from '../context-v2/RunQueueContext';
import { Layout } from '../components-v2/layout/Layout';
import type { PageId } from '../components-v2/layout/Layout';

interface MiningDashboardPageProps {
  onNavigate?: (page: PageId) => void;
}

/** 任务胶囊的状态点颜色（与运行中/已完成/失败三态对应） */
function statusDotClass(status: string): string {
  if (status === 'running') return 'bg-blue-500 animate-pulse';
  if (status === 'completed') return 'bg-emerald-500';
  return 'bg-rose-400';
}

function statusLabel(status: string): string {
  if (status === 'running') return '运行中';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  return '未知';
}

export const MiningDashboardPage: React.FC<MiningDashboardPageProps> = ({ onNavigate }) => {
  const {
    miningTask: task,
    miningTasks,
    focusedTaskId,
    focusMiningTask,
    miningEquityCurve: equityCurve,
    miningDrawdownCurve: drawdownCurve,
    stopMining,
    refreshMiningFactors,
  } = useTaskContext();
  const backtestQueue = useBacktestQueue();

  // 视图切换：「实时演化台」（进度侧栏+图表，默认）|「决策轨迹」（T-FM-07 只读扫描）
  const [viewMode, setViewMode] = useState<'live' | 'trajectory'>('live');
  // 轨迹按焦点任务拉取；无焦点回退当前任务（无任务时空串——下方有专属空态分支）
  const trajectoryTaskId = focusedTaskId || task?.taskId || '';

  // 一键回测：真入队（并发 2）；每行终结后拉一次权威清单刷新指标
  const handleQuickBacktest = useCallback(
    (factorIds: string[]) => {
      backtestQueue.enqueue(factorIds, {
        onSettled: () => {
          void refreshMiningFactors();
        },
      });
    },
    [backtestQueue, refreshMiningFactors],
  );

  // If no task, this page shouldn't be active (or show empty state)
  if (!task) {
    return (
      <Layout
        currentPage="home"
        onNavigate={onNavigate || (() => {})}
        showNavigation={!!onNavigate}
      >
        <div className="flex flex-col items-center justify-center min-h-[60vh] animate-fade-in-up">
          <p className="text-muted-foreground">当前无进行中的挖掘任务</p>
          <button 
            className="mt-4 text-primary hover:underline"
            onClick={() => onNavigate?.('home')}
          >
            返回主页
          </button>
        </div>
      </Layout>
    );
  }

  return (
    <Layout
      currentPage="home"
      onNavigate={onNavigate || (() => {})}
      showNavigation={!!onNavigate}
    >
      {/* 任务切换（多任务时出现）：点哪条就把演化台切到哪条，运行中/已完成都可回看 */}
      {miningTasks.length > 1 && (
        <div
          role="tablist"
          aria-label="任务切换"
          className="mb-3 flex flex-wrap items-center justify-center gap-1.5"
        >
          {miningTasks.map((t) => {
            const active = t.taskId === focusedTaskId;
            const label = (t.config?.userInput?.trim() || t.taskId).slice(0, 18);
            const pct = Math.min(100, Math.max(0, t.progress?.progress ?? 0));
            return (
              <button
                key={t.taskId}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => focusMiningTask(t.taskId)}
                title={`${t.config?.userInput?.trim() || t.taskId} · ${statusLabel(t.status)}`}
                className={`inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-bold transition-colors cursor-pointer ${
                  active
                    ? 'border-blue-300 bg-blue-50 text-blue-700 shadow-2xs'
                    : 'border-slate-200 bg-white/80 text-slate-500 hover:border-blue-200 hover:text-blue-600'
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${statusDotClass(t.status)}`} />
                <span className="truncate">{label}</span>
                {t.status === 'running' && (
                  <span className="font-mono text-[10px] text-blue-400 shrink-0">{pct}%</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* 任务状态栏：状态 + 进度 + 停止（替代原底部悬浮输入框） */}
      <div className="mb-4 flex items-center justify-center gap-3 rounded-2xl border border-border/60 bg-white/80 backdrop-blur-xl px-4 py-3 shadow-xs text-center">
        {task.status === 'running' ? (
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
          </span>
        ) : (
          <span className={`h-2.5 w-2.5 rounded-full shrink-0 ${task.status === 'completed' ? 'bg-emerald-500' : 'bg-red-500'}`} />
        )}
        <span className="text-sm font-bold text-slate-800 whitespace-nowrap">
          {task.status === 'running'
            ? '任务运行中'
            : task.status === 'completed'
              ? '任务已完成'
              : '任务已失败'}
        </span>
        <span className="text-xs text-muted-foreground truncate min-w-0 max-w-md" title={task.progress?.message}>
          {task.progress?.message || `Loop ${task.progress?.currentRound ?? 0}/${task.progress?.totalRounds ?? 0}`}
        </span>
        <div className="w-28 h-1.5 rounded-full bg-slate-100 overflow-hidden hidden sm:block">
          <div
            className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-500"
            style={{ width: `${Math.min(100, Math.max(0, task.progress?.progress ?? 0))}%` }}
          />
        </div>
        <span className="text-[11px] font-mono text-slate-400 hidden md:block w-9 text-right">
          {Math.min(100, Math.max(0, task.progress?.progress ?? 0))}%
        </span>
        {task.status === 'running' && (
          <button
            onClick={() => void stopMining(task.taskId)}
            className="flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold text-red-600 bg-red-50 hover:bg-red-100 border border-red-100 transition-colors cursor-pointer"
            title="只停止这个任务，其它任务不受影响"
          >
            <Square className="w-3 h-3" />
            停止任务
          </button>
        )}
      </div>

      {/* 视图切换（实时演化台 / 决策轨迹） */}
      <div
        role="tablist"
        aria-label="任务视图"
        className="mb-4 flex w-fit items-center gap-1 rounded-full border border-slate-200/80 bg-white/80 p-1 shadow-2xs"
      >
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'live'}
          onClick={() => setViewMode('live')}
          className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-black transition-colors cursor-pointer ${
            viewMode === 'live'
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-slate-500 hover:text-blue-600'
          }`}
        >
          <Activity className="h-3.5 w-3.5" />
          实时演化台
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'trajectory'}
          onClick={() => setViewMode('trajectory')}
          className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-black transition-colors cursor-pointer ${
            viewMode === 'trajectory'
              ? 'bg-blue-600 text-white shadow-sm'
              : 'text-slate-500 hover:text-blue-600'
          }`}
        >
          <GitBranch className="h-3.5 w-3.5" />
          决策轨迹
        </button>
      </div>

      {viewMode === 'trajectory' && (
        <div className="rounded-2xl border border-white/90 bg-white/80 backdrop-blur-xl shadow-xs p-4">
          <TrajectoryView taskId={trajectoryTaskId} />
        </div>
      )}

      {viewMode === 'live' && (
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1">
          <ProgressSidebar
            progress={task.progress}
            timeline={task.timeline}
            tokenUsage={task.tokenUsage}
            progressKind={task.progressKind}
          />
        </div>
        <div className="lg:col-span-3">
          <LiveCharts
            equityCurve={equityCurve}
            drawdownCurve={drawdownCurve}
            metrics={task.metrics || null}
            isRunning={task.status === 'running'}
            logs={task.logs}
          />
        </div>

        {/* New Rows - Full Width */}
        <div className="lg:col-span-4">
           <FactorStatsRow
             metrics={task.metrics || null}
             onQuickBacktest={handleQuickBacktest}
           />
        </div>
        <div className="lg:col-span-4">
           <FactorList metrics={task.metrics || null} onNavigate={onNavigate} />
        </div>
      </div>
      )}
    </Layout>
  );
};
