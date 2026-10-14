/**
 * 交易台「调仓计划（预演）」区块（2026-09-17 自今日交易台拆出；2026-10-14 清理收口）。
 *
 * 现存仅 `PlanSection`（调仓计划预览 + 一键执行）→ 归位到「手动任务」页。
 * 原先同文件里的 `SignalsSection`（候选信号）/ `ExecutionSection`（今日执行）已下线：
 * 候选信号由模拟交易「候选信号」页签承担，今日执行由持仓监控页的 `ExecutionStrip` 承担，
 * 两者都直接吃 `/api/v1/desk/today`，不必再绕这一层。
 *
 * 本 Section 自取数（/api/v1/desk/today）并自带下钻弹窗，宿主页零侵入。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { getDeskToday } from '../services/deskService';
import type { DeskToday } from '../types';
import { DrillDownDrawer, type DrillEntry } from '../../shared/DrillDownDrawer';
import { PlanCard } from './PlanCard';
import { planDrillEntries, planOrderDrillEntries, symbolLabel } from '../deskModel';

interface DrawerState {
  title: string;
  subtitle?: string;
  entries: DrillEntry[];
  raw: unknown;
}

function useDesk(plan: boolean) {
  const [desk, setDesk] = useState<DeskToday | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const resp = await getDeskToday({ health: false, plan });
      setDesk(resp?.data || null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '请求失败');
    } finally {
      setLoading(false);
    }
  }, [plan]);
  useEffect(() => {
    void load();
  }, [load]);
  return { desk, loading, error, reload: load };
}

const SectionShell: React.FC<{
  loading: boolean;
  error: string;
  empty: boolean;
  children: React.ReactNode;
}> = ({ loading, error, empty, children }) => {
  if (loading) {
    return <div className="flex items-center justify-center h-40 text-xs text-slate-400">加载中…</div>;
  }
  if (error) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3 text-xs text-amber-800">{error}</div>
    );
  }
  if (empty) {
    return (
      <div className="bg-gray-50 rounded-2xl border border-gray-200 p-8 text-center text-sm text-gray-500">
        暂无数据
      </div>
    );
  }
  return <>{children}</>;
};

/** 调仓计划（预演）→ 手动任务页：预览 + 一键执行调仓（dry-run 与执行共用同一计算器） */
export const PlanSection: React.FC = () => {
  const { desk, loading, error, reload } = useDesk(true);
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  return (
    <>
      <SectionShell loading={loading} error={error} empty={!desk}>
        {desk && (
          <PlanCard
            plan={desk.plan}
            onExecuted={() => void reload()}
            onOrderDrill={(order) =>
              setDrawer({
                title: `计划单 · ${symbolLabel(order.symbol, order.name)}`,
                subtitle: '单字段 → 触发类别/当日信号 → 原始条目（dry-run 输出）',
                entries: planOrderDrillEntries(order, desk.plan, desk.signals) as DrillEntry[],
                raw: order,
              })
            }
            onDrillDown={() =>
              setDrawer({
                title: '调仓计划 · 来源链',
                subtitle: '预演与执行共用同一 RebalanceCalculator（dry-run，未执行）',
                entries: planDrillEntries(desk.plan) as DrillEntry[],
                raw: desk.plan,
              })
            }
          />
        )}
      </SectionShell>
      <DrillDownDrawer
        open={!!drawer}
        presentation="modal"
        title={drawer?.title || ''}
        subtitle={drawer?.subtitle}
        entries={drawer?.entries || []}
        raw={drawer?.raw}
        onClose={() => setDrawer(null)}
      />
    </>
  );
};
