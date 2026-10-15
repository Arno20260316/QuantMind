/**
 * 因子研究 —— 快照计算面板：快照未生成 / 重建时的「一键计算」入口。
 *
 * 全部在本地 QuantDB 上计算（约 25~45 分钟），不上传任何数据；
 * 计算过程中轮询 /snapshot-status 展示进度与日志；完成后回调 onReady 刷新页面。
 */
import React, { useEffect, useRef, useState } from 'react';
import { Database, Play, RefreshCw } from 'lucide-react';
import { getSnapshotStatus, postBuildSnapshot } from '../services/factorResearchService';
import type { FactorDataset, SnapshotStatus } from '../services/factorResearchService';
import type { ResearchMarket } from '../types/factorResearch';
import { Card } from './common';

interface Props {
  dataset: FactorDataset;
  /** 本页当前市场（快照按市场独立构建/存放） */
  market: ResearchMarket;
  /** 快照就绪（存在且不再构建中）时回调（防重复触发） */
  onReady: () => void;
}

export const SnapshotPanel: React.FC<Props> = ({ dataset, market, onReady }) => {
  const [st, setSt] = useState<SnapshotStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const notified = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      getSnapshotStatus(dataset, market)
        .then((s) => {
          if (!alive) return;
          setSt(s);
          setErr(null);
          if (s.exists && !s.running && !notified.current) {
            notified.current = true;
            onReady();
          }

        })
        .catch((e: unknown) => {
          if (alive) setErr(e instanceof Error ? e.message : String(e));
        });
    };
    tick();
    const timer = window.setInterval(tick, 5000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [dataset, market, onReady]);

  const start = async () => {
    setStarting(true);
    setErr(null);
    try {
      await postBuildSnapshot(dataset, market);
      notified.current = false;
      const s = await getSnapshotStatus(dataset, market);
      setSt(s);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const running = !!st?.running;

  return (
    <div className="flex-1 flex items-center justify-center">
      <Card title={dataset === "private" ? "私人因子库快照" : "因子快照"} className="w-[560px]">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 border border-indigo-100 flex items-center justify-center shrink-0">
            <Database className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="min-w-0 flex-1">
            {!st ? (
              <div className="text-xs text-slate-400 py-2">正在读取快照状态…</div>
            ) : st.exists && !running ? (
              <>
                <div className="text-xs font-bold text-slate-700">快照已生成</div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  构建于 {st.built_at || '—'} · {st.n_factors ?? '—'} 因子 × {st.n_dates ?? '—'} 期
                  （{st.window?.[0]} ~ {st.window?.[1]}）
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={start}
                    disabled={starting}
                    className="flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-[11px] font-bold text-indigo-600 hover:bg-indigo-100 disabled:opacity-50"
                  >
                    <RefreshCw className="w-3 h-3" /> 重算快照
                  </button>
                  <span className="text-[10px] text-slate-400">自动扫描 6_ml_datasets 重建索引 · 全本地、不上传</span>
                </div>
              </>
            ) : (
              <>
                <div className="text-xs font-bold text-slate-700">
                  {running ? '快照计算中…' : '尚未生成因子快照'}
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500 leading-relaxed">
                  榜单/单因子/对比/合成依赖一份本地快照（月末打分索引）。
                  点击下方按钮将**自动扫描本机该市场的 6_ml_datasets**（L1/L2、Alpha 库、
                  量价/技术指标等已计算好的因子数据）并生成索引 —— 只读取、不重算因子本身，
                  全程本地运行、不上传任何数据。私人因子库约 5~15 分钟，经典因子集（仅 A 股）约 25 分钟。
                  若市场数据尚未同步，请先在「数据管理」里同步对应市场的数据。
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={start}
                    disabled={running || starting}
                    className="flex items-center gap-1 rounded-full bg-indigo-600 px-3.5 py-1 text-[11px] font-bold text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    <Play className="w-3 h-3" /> {running ? '计算中（可关闭页面，后台继续）' : starting ? '启动中…' : '开始计算'}
                  </button>
                </div>
                {running && st.step && (
                  <div className="mt-2 text-[11px] font-mono text-indigo-600">{st.step}</div>
                )}
                {running && st.log_tail.length > 0 && (
                  <pre className="mt-1.5 max-h-[130px] overflow-y-auto custom-scrollbar rounded-lg bg-slate-50 border border-slate-100 px-2 py-1.5 text-[9px] leading-relaxed text-slate-500 font-mono whitespace-pre-wrap">
                    {st.log_tail.join('\n')}
                  </pre>
                )}
              </>
            )}
            {err && <div className="mt-1.5 text-[10px] text-rose-500">{err}</div>}
          </div>
        </div>
      </Card>
    </div>
  );
};
