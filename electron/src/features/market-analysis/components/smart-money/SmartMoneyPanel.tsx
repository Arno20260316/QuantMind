/** 聪明钱 · 面板容器（A 股市场分析「聪明钱」Tab）。
 *
 * 取数：overview/stocks/branches/evidence 四面独立结算——主面（overview/stocks）
 * 404=未构建空态；branches/evidence 单独失败只空该卡，不拖垮整页。
 * 布局：概览带（6 卡）→ 选股榜 2/3 + 右栏（席位榜+口径卡）1/3 → 证据台通栏。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Info, RefreshCw, ThermometerSun } from 'lucide-react';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import {
  SmartMoneyError,
  getSmartMoneyBranches,
  getSmartMoneyEvidence,
  getSmartMoneyOverview,
  getSmartMoneyStocks,
  type SmartBranchRow,
  type SmartEvidenceResponse,
  type SmartOverviewResponse,
  type SmartStocksResponse,
} from '../../services/smartMoneyService';
import { SMART_MONEY_DISCLAIMER, SMART_MONEY_TITLE } from '../../smartMoneyVocabulary';
import { SmartMoneyOverviewBand } from './SmartMoneyOverviewBand';
import { SmartMoneyStockBoard } from './SmartMoneyStockBoard';
import { SmartMoneyBranchBoard } from './SmartMoneyBranchBoard';
import { SmartMoneyEvidenceCard } from './SmartMoneyEvidenceCard';

interface SmartMoneyPanelProps {
  /** 行点击 → 个股终端浮窗（由页面持有 windowSymbol） */
  onPick?: (symbol: string) => void;
}

export const SmartMoneyPanel: React.FC<SmartMoneyPanelProps> = ({ onPick }) => {
  const [overview, setOverview] = useState<SmartOverviewResponse | null>(null);
  const [stocks, setStocks] = useState<SmartStocksResponse | null>(null);
  const [branches, setBranches] = useState<SmartBranchRow[] | null>(null);
  const [evidence, setEvidence] = useState<SmartEvidenceResponse | null>(null);
  const [branchWindow, setBranchWindow] = useState<5 | 20>(5);
  const [loading, setLoading] = useState(true);
  const [notBuilt, setNotBuilt] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async (win: 5 | 20) => {
    setLoading(true);
    setError(null);
    const [ov, st, br, ev] = await Promise.allSettled([
      getSmartMoneyOverview(),
      getSmartMoneyStocks(300),
      getSmartMoneyBranches(win),
      getSmartMoneyEvidence(),
    ]);

    // 主面错误裁决：404 → 未构建空态（不白屏）；其他 → 错误条
    const mainMissing = [ov, st].find((r) => r.status === 'rejected');
    if (mainMissing && mainMissing.status === 'rejected') {
      const reason: unknown = mainMissing.reason;
      if (reason instanceof SmartMoneyError && reason.notBuilt) {
        setNotBuilt(true);
      } else {
        setNotBuilt(false);
        setError(reason instanceof Error ? reason.message : '聪明钱数据加载失败');
      }
    } else {
      setNotBuilt(false);
      if (ov.status === 'fulfilled') setOverview(ov.value);
      if (st.status === 'fulfilled') setStocks(st.value);
    }
    setBranches(br.status === 'fulfilled' ? br.value.branches : null);
    setEvidence(ev.status === 'fulfilled' ? ev.value : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll(5);
  }, [loadAll]);

  const handleWindowChange = useCallback(async (w: 5 | 20) => {
    setBranchWindow(w);
    setBranches(null);
    try {
      const r = await getSmartMoneyBranches(w);
      setBranches(r.branches);
    } catch {
      setBranches(null);
    }
  }, []);

  const asOf = stocks?.as_of ?? overview?.as_of ?? null;
  const partial = Boolean(stocks?.partial ?? overview?.partial);
  const freshness = overview?.freshness ?? null;

  if (notBuilt) {
    return (
      <SectionCard icon={<ThermometerSun className="w-3.5 h-3.5 text-purple-600" />} title={SMART_MONEY_TITLE}>
        <EmptyHint text="聪明钱统计尚未生成（首次构建完成后展示；每日调度会滚动更新）" />
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void loadAll(branchWindow)}
            className="flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-extrabold text-purple-700 bg-purple-50 border border-purple-100 hover:bg-purple-100 transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            重新加载
          </button>
        </div>
      </SectionCard>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <header className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h2 className="text-sm font-extrabold text-slate-800">{SMART_MONEY_TITLE}</h2>
          <span className="text-[10px] text-slate-400">{SMART_MONEY_DISCLAIMER}</span>
          {asOf && <span className="text-[9px] font-mono text-slate-400">数据截至 {asOf}</span>}
        </div>
        <button
          type="button"
          onClick={() => void loadAll(branchWindow)}
          className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </header>

      {partial && (
        <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
          部分数据源未就绪：当日产物以现有数据构建（缺口在各卡片日期与覆盖列如实标注，「—」为缺失非 0）
        </div>
      )}
      {error && (
        <div className="rounded-xl bg-red-50/80 border border-red-200 px-3 py-1.5 text-[10px] text-red-700 font-bold">
          {error}
        </div>
      )}

      <SmartMoneyOverviewBand overview={overview?.overview ?? null} loading={loading} />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3 items-start">
        <SmartMoneyStockBoard
          items={stocks?.items ?? null}
          pillars={stocks?.pillars ?? overview?.pillars ?? null}
          total={stocks?.total ?? null}
          poolSize={stocks?.summary?.pool_size ?? overview?.summary?.pool_size ?? null}
          loading={loading}
          onPick={onPick}
        />
        <div className="flex flex-col gap-3 min-w-0">
          <SmartMoneyBranchBoard
            rows={branches}
            window={branchWindow}
            onWindowChange={(w) => void handleWindowChange(w)}
            loading={loading}
          />
          <SectionCard icon={<Info className="w-3.5 h-3.5 text-purple-600" />} title="口径与新鲜度">
            <ul className="flex flex-col gap-1 text-[10px] text-slate-500 leading-relaxed">
              <li>
                · 事件池 = 近 5 交易日龙虎榜/机构席位/大宗/调研 ∪ 两融异动（|净买 5 日
                z20|≥2）；柱分位 = 池内截面 0–100 分位。
              </li>
              <li>
                · 入柱 4 项经双过闸裁定（h10 前瞻 IC 与尾部同号），方向 = 实测 IC
                符号；负向柱分位已取反（100−分位），柱分高 = 历史有利方向。
              </li>
              <li>· 复合分 = 4 柱等权均值；缺失柱按 50 计入并随行披露实际覆盖（n/4），缺失「—」绝不为 0。</li>
              <li>
                · 新鲜度：行情截至{' '}
                <span className="font-mono">{freshness?.kline_last ?? '—'}</span>；两融披露截至{' '}
                <span className="font-mono">{freshness?.margin_last ?? '—'}</span>（两融官方滞后行情约 1 交易日，余额类柱按最近披露值口径）。
              </li>
              <li>· 事件原始表附带的「上榜后表现」（fwd_ret_*）仅展示用途，已封存、绝不入信号。</li>
            </ul>
          </SectionCard>
        </div>
      </div>

      <SmartMoneyEvidenceCard evidence={evidence} loading={loading} />
    </section>
  );
};
