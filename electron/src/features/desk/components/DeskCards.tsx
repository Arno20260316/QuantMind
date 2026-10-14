/** 交易台健康卡：体检断言清单 + 系统运行状态条（浅色专业金融风；带 source 下钻）
 *
 * 2026-10-14 清理：本文件原有 候选信号 / 执行 / 盈亏 三张卡，自 2026-09-17 起已无活引用
 * （候选信号改由模拟交易页签承担、今日执行改由持仓监控页 `ExecutionStrip` 承担，
 * 账户盈亏由持仓总览/顶栏/个人中心等多面承担），连同其配套 helper 一并删除，只留健康卡。
 *
 * 排版口径：数字一律 font-mono tabular-nums、卡片头部=图标色块+标题+微统计。
 */

import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock, Database, HeartPulse, HelpCircle, Server, XCircle } from 'lucide-react';
import type { HealthBlock } from '../types';
import { useUiMode } from '../../shared/useUiMode';
import { CARD, CardHeader } from './cardKit';
import { SERVICE_URLS } from '../../../config/services';
import { fmtBeijingClock } from '../../../utils/timeBeijing';
import { healthItemViews, statusStyle } from '../deskModel';

function SourceFooter({ source, onDrillDown }: { source: string; onDrillDown?: () => void }) {
  const { isSimple } = useUiMode();
  if (isSimple && !onDrillDown) return null; // 简单模式收起技术来源（专业模式/下钻保留）
  return (
    <footer className="text-[10px] text-slate-400 mt-2.5">
      {onDrillDown ? (
        <button
          type="button"
          onClick={onDrillDown}
          className="hover:text-blue-600 underline decoration-dotted underline-offset-2"
          title="下钻：来源链与原始载荷"
        >
          来源：{source}（点击下钻）
        </button>
      ) : (
        <>来源：{source}</>
      )}
    </footer>
  );
}

const formatUptime = (startedAt?: string | null): string => {
  if (!startedAt) return '--';
  const start = new Date(startedAt).getTime();
  if (Number.isNaN(start)) return '--';
  const diffSec = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const h = Math.floor(diffSec / 3600);
  const m = Math.floor((diffSec % 3600) / 60);
  const sec = diffSec % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
};

export const HealthCard: React.FC<{ health: HealthBlock | null | undefined; tradingRunning?: boolean }> = ({ health, tradingRunning }) => {
  const items = healthItemViews(health);
  // 系统运行状态条（与个人中心同源：/api/v1/tdx/l2/status，20s 轮询）
  const [l2Status, setL2Status] = useState<{ capture?: Record<string, unknown>; realtime?: Record<string, unknown> } | null>(null);
  useEffect(() => {
    let alive = true;
    const base = SERVICE_URLS.API_GATEWAY.replace(/\/+$/, '');
    const fetchL2 = async () => {
      try {
        const res = await fetch(`${base}/api/v1/tdx/l2/status`, {
          headers: { Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` },
        });
        if (!alive) return;
        if (res.ok) setL2Status(await res.json());
      } catch {
        /* 桥/服务不可达时保持上一状态 */
      }
    };
    void fetchL2();
    const timer = window.setInterval(() => void fetchL2(), 20000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  const captureLast = l2Status?.capture?.last_cycle_at as string | undefined;
  const captureStale = (() => {
    if (!captureLast) return true;
    const ageSec = (Date.now() - new Date(captureLast).getTime()) / 1000;
    return !Number.isFinite(ageSec) || ageSec > 300;
  })();
  const nodeRunning = l2Status?.realtime?.running === true || l2Status?.capture?.running === true;
  const running = Boolean(tradingRunning) || nodeRunning;
  const levelIcon = (level: string) => {
    if (level === 'ok') return <CheckCircle2 className="h-3.5 w-3.5" />;
    if (level === 'warn') return <AlertTriangle className="h-3.5 w-3.5" />;
    if (level === 'fail') return <XCircle className="h-3.5 w-3.5" />;
    return <HelpCircle className="h-3.5 w-3.5" />;
  };
  return (
    <section className={CARD} data-testid="health-card">
      <CardHeader
        icon={<HeartPulse className="h-4 w-4" />}
        title="系统健康"
        extra={
          <span className="inline-flex items-center gap-2 text-[11px] font-medium text-slate-500">
            {([['ok', health?.ok], ['warn', health?.warn], ['fail', health?.fail]] as const).map(
              ([level, count]) => {
                const style = statusStyle(level);
                return (
                  <span key={level} className="inline-flex items-center gap-1">
                    <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                    <span className={style.text}>{count ?? 0}</span> {style.label}
                  </span>
                );
              }
            )}
          </span>
        }
      />
      {/* 系统运行状态条（2026-09-17 自个人中心并入）：交易节点 / 数据同步 / 运行时长 */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] font-bold">
        <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-1 text-slate-600">
          <Server className="h-3 w-3 text-slate-400" />
          交易节点
          <span className={`rounded-full px-1.5 py-px text-[10px] font-bold ${running ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
            {running ? 'Running' : 'Stopped'}
          </span>
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-1 text-slate-600">
          <Database className="h-3 w-3 text-slate-400" />
          数据同步
          {captureLast ? (
            <span className={`rounded-full border px-1.5 py-px text-[10px] font-bold ${captureStale ? 'bg-amber-50 text-amber-600 border-amber-200' : 'bg-emerald-50 text-emerald-600 border-emerald-200'}`}>
              {fmtBeijingClock(captureLast, { withSeconds: false })}
              {captureStale ? ' · 陈旧' : ' · 新鲜'}
            </span>
          ) : (
            <span className="font-mono text-[10px] text-slate-400">--</span>
          )}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-1 text-slate-600">
          <Clock className="h-3 w-3 text-slate-400" />
          运行时长
          <span className="font-mono text-[10px] text-slate-700">
            {running ? formatUptime(l2Status?.realtime?.started_at as string | undefined) : '--'}
          </span>
        </span>
      </div>

      {/* 体检项卡片网格：状态图标 + 名称 + 结论 + 明细两行截断（悬停看全文/建议） */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 flex-1 content-start auto-rows-min">
        {items.map((item) => {
          const style = statusStyle(item.level);
          return (
            <div
              key={item.id}
              title={`${item.detail}${item.suggestion ? `\n建议：${item.suggestion}` : ''}`}
              className={`rounded-xl border p-2.5 min-w-0 flex flex-col gap-1 transition-all hover:shadow-sm ${style.card}`}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={`shrink-0 ${style.text}`}>{levelIcon(item.level)}</span>
                <span className="text-[12px] font-bold text-slate-800 truncate">{item.name}</span>
                <span className={`ml-auto shrink-0 text-[10px] font-bold ${style.text}`}>
                  {style.label}
                </span>
              </div>
              <div className="text-[10px] leading-4 text-slate-500 line-clamp-2">
                {item.detail || '—'}
              </div>
            </div>
          );
        })}
        {items.length === 0 && <p className="text-xs text-slate-400">体检未运行（?health=false）</p>}
      </div>
      <SourceFooter source={health?.source || '—'} />
    </section>
  );
};
