/**
 * 晋级门槛卡（T-FE-12 / T-P3-05）：策略详情内展示"距晋级还差什么"。
 *
 * 数据源：/api/v1/eval/health/{strategy_id}（体检留档 + **两档门禁预演——与启动端点
 * 拦截逻辑同源**，展示即真实口径）。
 *
 * 两档门禁**必须分开列**：「進模拟盘」与「进实盘」的判据不同（实盘另需 SIM ≥ 20 交易日
 * + 跟踪误差 ≤ 15%），合成一行会让用户以为修好体检就能直接上实盘。逐项判据全部来自
 * 后端 `shared/start_eligibility.py`——本组件只展示，不新增阈值、不重算通过与否；
 * 缺记录时后端给「无记录」，绝不渲染成 0。
 */

import React, { useEffect, useState } from 'react';
import { Check, DoorOpen, RefreshCw, ShieldAlert, ShieldCheck, X } from 'lucide-react';
import { getStrategyHealth } from '../../features/eval-center/services/evalCenterService';
import type {
  StartEligibility,
  StrategyHealthArchive,
} from '../../features/eval-center/types/evalCenter';
import { gradeMeta } from '../../features/eval-center/components/evalCenterModel';

interface PromotionGateCardProps {
  strategyId: string;
}

const GATE_ROWS: Array<{ key: 'SIMULATION' | 'REAL'; title: string }> = [
  { key: 'SIMULATION', title: '晋级门禁（进模拟盘）' },
  { key: 'REAL', title: '晋级门禁（进实盘）' },
];

/** 逐项清单（生命周期 / 体检结论 / 模拟交易日 / 跟踪误差）——✓ 绿的、✗ 告警。 */
const EligibilityItems: React.FC<{ data: StartEligibility }> = ({ data }) => (
  <div className="mt-1.5 flex flex-col gap-1" data-testid="promotion-gate-items">
    {data.items.map((item) => (
      <div key={item.key} className="flex items-start gap-1.5 leading-5">
        <span className="mt-0.5 shrink-0">
          {item.passed ? (
            <Check className="w-3.5 h-3.5 text-emerald-600" />
          ) : (
            <X className="w-3.5 h-3.5 text-amber-600" />
          )}
        </span>
        <span className="shrink-0 font-semibold text-slate-700">{item.label}</span>
        <span className={item.passed ? 'text-slate-500' : 'font-semibold text-amber-800'}>
          {item.value}
        </span>
        {!item.passed && item.reason && <span className="text-amber-700">— {item.reason}</span>}
        {!item.passed && item.next_step && (
          <span className="text-slate-500">（下一步：{item.next_step}）</span>
        )}
      </div>
    ))}
  </div>
);

export const PromotionGateCard: React.FC<PromotionGateCardProps> = ({ strategyId }) => {
  const [archive, setArchive] = useState<StrategyHealthArchive | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    getStrategyHealth(strategyId)
      .then((resp) => {
        if (!cancelled) setArchive(resp?.data || null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '门禁信息读取失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [strategyId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-400 py-3">
        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
        正在读取晋级门槛...
      </div>
    );
  }
  if (error || !archive) {
    return <div className="text-xs text-rose-600 py-2">{error || '门禁信息不可用'}</div>;
  }

  const latest = archive.latest;
  const meta = gradeMeta(latest?.verdict ?? null, false);
  const eligibility = archive.eligibility;
  const rows = GATE_ROWS.map((row) => ({ ...row, data: eligibility?.[row.key] })).filter(
    (row): row is typeof row & { data: StartEligibility } => !!row.data,
  );
  // 老后端 / 非 user_strategy：没有逐项清单时退回既有的单行 `gate` 预演，不留空白。
  const legacyAllowed = archive.gate.allowed;

  return (
    <div className="space-y-2 text-xs">
      {rows.length > 0
        ? rows.map((row) => {
            const allowed = row.data.allowed;
            return (
              <div
                key={row.key}
                data-testid={`promotion-gate-${row.key}`}
                data-allowed={allowed}
                className={`flex items-start gap-2 rounded-xl border p-3 ${
                  allowed
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                    : 'bg-amber-50 border-amber-200 text-amber-900'
                }`}
              >
                {allowed ? (
                  <ShieldCheck className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" />
                ) : (
                  <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">
                    {row.title}：{allowed ? '放行' : '拦截'}
                    {row.key === 'SIMULATION' && latest ? (
                      <span className={`ml-2 px-1.5 py-0.5 rounded-full border ${meta.className}`}>
                        体检 {latest.verdict}
                        {meta.isLowConfidence ? ' †' : ''}
                      </span>
                    ) : null}
                    {row.key === 'SIMULATION' && !latest ? (
                      <span className="ml-2 text-slate-400">尚无体检留档</span>
                    ) : null}
                  </div>
                  <EligibilityItems data={row.data} />
                  {!allowed && row.data.next_step && (
                    <div className="mt-1 leading-5">下一步：{row.data.next_step}</div>
                  )}
                </div>
              </div>
            );
          })
        : (
          <div
            className={`flex items-start gap-2 rounded-xl border p-3 ${
              legacyAllowed
                ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                : 'bg-amber-50 border-amber-200 text-amber-900'
            }`}
          >
            {legacyAllowed ? (
              <ShieldCheck className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" />
            ) : (
              <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
            )}
            <div>
              <div className="font-semibold">
                晋级门禁（进模拟盘）：{legacyAllowed ? '放行' : '拦截'}
                {latest ? (
                  <span className={`ml-2 px-1.5 py-0.5 rounded-full border ${meta.className}`}>
                    体检 {latest.verdict}
                    {meta.isLowConfidence ? ' †' : ''}
                  </span>
                ) : (
                  <span className="ml-2 text-slate-400">尚无体检留档</span>
                )}
              </div>
              <div className="mt-1 leading-5">{archive.gate.note}</div>
            </div>
          </div>
        )}

      {latest && (
        <div className="rounded-xl border border-gray-100 p-3 space-y-1">
          <div className="text-slate-500">
            可信度 {Math.round(latest.confidence ?? 0)}/100 · 证据源 {latest.evidence_source || '—'} ·{' '}
            {latest.snapshot_date || '—'}
          </div>
          {(latest.reasons || []).slice(0, 2).map((r, i) => (
            <div key={`r-${i}`} className="text-slate-600">
              · {r}
            </div>
          ))}
          {(latest.suggestions || []).slice(0, 2).map((s, i) => (
            <div key={`s-${i}`} className="text-slate-500">
              建议 {i + 1}: {s}
            </div>
          ))}
        </div>
      )}

      {archive.history.length > 1 && (
        <div className="rounded-xl border border-gray-100 p-3">
          <div className="text-slate-500 mb-1">结论历史</div>
          {archive.history.slice(0, 5).map((point, i) => {
            const pm = gradeMeta(point.verdict, false);
            return (
              <div key={i} className="flex items-center gap-2 text-[11px]">
                <span className="text-slate-400 w-20">{point.snapshot_date || '—'}</span>
                <span className={`px-1.5 rounded border ${pm.className}`}>{point.verdict || '—'}</span>
                <span className="text-slate-500">可信度 {Math.round(point.confidence ?? 0)}</span>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-center gap-1 text-[11px] text-slate-400">
        <DoorOpen className="w-3 h-3" />
        门槛口径：A/B 放行（B 标注收益来源）、L/E/未体检拦截（与启动端点同一判定）
      </div>
    </div>
  );
};
