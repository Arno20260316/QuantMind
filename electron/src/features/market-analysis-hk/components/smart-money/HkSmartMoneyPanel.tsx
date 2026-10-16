/** 港股聪明钱 · 面板容器（细案 §6：四取数独立结算 + 七组件布局）。
 *
 * 取数分工：overview/types/consensus/evidence 由本容器 allSettled 独立结算
 * （overview 404 → 全局 notBuilt 空态；其余面单面失败只出错误条，不拖垮整页）；
 * 席位榜自持取数（服务端筛选）。抽屉由面板头部「个股档案」输入框或榜单行点击打开；
 * 头部输入框是对细案的一处增补（保证 evidence-only 无榜单时抽屉仍可达，P7 记录）。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Info, Radar, RefreshCw, Search } from 'lucide-react';
import { Input } from 'antd';
import {
  HkSmartMoneyError,
  getHkSmConsensus,
  getHkSmEvidence,
  getHkSmOverview,
  getHkSmTypes,
  type HkSmConsensusResponse,
  type HkSmEvidenceResponse,
  type HkSmOverviewResponse,
  type HkSmTypesResponse,
} from '../../services/api';
import { EmptyHint, SectionCard } from '../shared/ui';
import { HK_SM_DISCLAIMER, HK_SM_TITLE, MISSING } from '../../hkSmartMoneyVocabulary';
import { HkSmOverviewBand } from './HkSmOverviewBand';
import { HkSmTypeBoard } from './HkSmTypeBoard';
import { HkSmSeatBoard } from './HkSmSeatBoard';
import { HkSmConsensusBoard } from './HkSmConsensusBoard';
import { HkSmEvidenceCard } from './HkSmEvidenceCard';
import { HkSmStockDrawer } from './HkSmStockDrawer';

interface HkSmartMoneyPanelProps {
  /** 抽屉内「打开个股终端」入口 */
  onPick?: (symbol: string) => void;
}

function faceError(reason: unknown): string {
  return reason instanceof Error ? reason.message : '数据加载失败';
}

export const HkSmartMoneyPanel: React.FC<HkSmartMoneyPanelProps> = ({ onPick }) => {
  const [overview, setOverview] = useState<HkSmOverviewResponse | null>(null);
  const [types, setTypes] = useState<HkSmTypesResponse | null>(null);
  const [consensus, setConsensus] = useState<HkSmConsensusResponse | null>(null);
  const [evidence, setEvidence] = useState<HkSmEvidenceResponse | null>(null);
  const [faceErrors, setFaceErrors] = useState<{ types: string | null; consensus: string | null }>(
    { types: null, consensus: null },
  );
  const [loading, setLoading] = useState(true);
  const [notBuilt, setNotBuilt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);
  const [jump, setJump] = useState('');

  const loadCore = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [ov, ty, cs, ev] = await Promise.allSettled([
      getHkSmOverview(),
      getHkSmTypes('10'),
      getHkSmConsensus(50),
      getHkSmEvidence(),
    ]);

    // 主面（overview）错误裁决：404 → 未构建空态；其他 → 错误条（不影响其余面）
    if (ov.status === 'fulfilled') {
      setOverview(ov.value);
      setNotBuilt(false);
    } else {
      const reason: unknown = ov.reason;
      if (reason instanceof HkSmartMoneyError && reason.notBuilt) {
        setNotBuilt(true);
        setError(null);
      } else {
        setError(faceError(ov.reason));
      }
    }
    if (ty.status === 'fulfilled') setTypes(ty.value);
    if (cs.status === 'fulfilled') setConsensus(cs.value);
    if (ev.status === 'fulfilled') setEvidence(ev.value);
    setFaceErrors({
      types: ty.status === 'rejected' ? faceError(ty.reason) : null,
      consensus: cs.status === 'rejected' ? faceError(cs.reason) : null,
    });
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadCore();
  }, [loadCore]);

  const handleJump = useCallback(() => {
    const code = jump.trim().toUpperCase();
    if (!code) return;
    setDrawerSymbol(code);
  }, [jump]);

  if (notBuilt) {
    return (
      <SectionCard
        icon={<Radar className="w-3.5 h-3.5 text-purple-600" />}
        title={HK_SM_TITLE}
      >
        <EmptyHint text="港股聪明钱统计尚未生成（首次构建完成后展示；每日 19:40 / 21:40 两档调度滚动更新）" />
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void loadCore()}
            className="flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-extrabold text-purple-700 bg-purple-50 border border-purple-100 hover:bg-purple-100 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            重新加载
          </button>
        </div>
      </SectionCard>
    );
  }

  const fresh = overview?.freshness ?? null;

  return (
    <section className="flex flex-col gap-3">
      <header className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h2 className="text-sm font-extrabold text-slate-800">{HK_SM_TITLE}</h2>
          <span className="text-[10px] text-slate-400">{HK_SM_DISCLAIMER}</span>
          {overview?.as_of && (
            <span className="text-[9px] font-mono text-slate-400">
              数据截至 <span className="font-bold text-slate-600">{overview.as_of}</span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <Input
            size="small"
            value={jump}
            onChange={(e) => setJump(e.target.value)}
            onPressEnter={handleJump}
            placeholder="个股档案：如 0700.HK 回车"
            prefix={<Search className="w-3 h-3 text-slate-400" />}
            allowClear
            className="w-44"
          />
          <button
            type="button"
            onClick={() => void loadCore()}
            className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </header>

      {overview?.partial && (
        <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
          部分数据源未就绪：当日产物以现有数据构建（缺口在各卡片日期与覆盖列如实标注，「—」为缺失非 0）
        </div>
      )}
      {error && (
        <div className="rounded-xl bg-red-50/80 border border-red-200 px-3 py-1.5 text-[10px] text-red-700 font-bold">
          {error}
        </div>
      )}

      <HkSmOverviewBand data={overview} loading={loading} />

      <HkSmTypeBoard data={types} loading={loading} error={faceErrors.types} />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 items-start min-w-0">
        <HkSmSeatBoard />
        <HkSmConsensusBoard
          data={consensus}
          loading={loading}
          error={faceErrors.consensus}
          onPick={(s) => setDrawerSymbol(s)}
        />
      </div>

      <SectionCard
        icon={<Info className="w-3.5 h-3.5 text-purple-600" />}
        title="口径与新鲜度"
      >
        <ul className="flex flex-col gap-1 text-[10px] text-slate-500 leading-relaxed">
          <li>
            · 事件仓 = CCASS 前 50 席位逐日持股差分；事件门槛 |Δ市值| ≥ 100 万 HKD ∧
            |Δ股数|/持股 ≥ 5% ∧ 收盘价 ≥ 0.5 HKD；过户嫌疑只标记、不计入统计。
          </li>
          <li>
            · 统计样本 = episode 首日 ∧ 非嫌疑事件；历史胜率 = 事件后 10 日超额与方向
            同号占比（事后统计，非预测）；席位胜率/超额为收缩估计（w=n/(n+20)，
            向中性 0bp/50% 收缩，n 随行披露）。
          </li>
          <li>
            · 南向（内资·港股通）为 hsgt_south 直读披露口径，不在 CCASS 事件仓内
            （因此席位榜无南向分类）；A 席对账见证据台。
          </li>
          <li>
            · 零类型通过 h10 双过闸时为 evidence-only：共识榜不发布、类型效力矩阵与
            席位榜照常交付——这是数据结论，不是链路故障。
          </li>
          <li>
            · 事后超额（fwd_exc_*）为封存展示列（T+1 收盘入场口径），不参与任何信号构造。
          </li>
          <li>
            · 新鲜度：行情截至 <span className="font-mono">{fresh?.kline_date ?? MISSING}</span>
            ；CCASS 截至 <span className="font-mono">{fresh?.ccass_date ?? MISSING}</span>
            （披露滞后 <span className="font-mono">{fresh?.ccass_lag_days ?? MISSING}</span> 交易日）；
            南向截至 <span className="font-mono">{fresh?.south_date ?? MISSING}</span>。
          </li>
        </ul>
      </SectionCard>

      <HkSmEvidenceCard evidence={evidence} loading={loading} />

      <HkSmStockDrawer
        symbol={drawerSymbol}
        open={!!drawerSymbol}
        onClose={() => setDrawerSymbol(null)}
        onOpenTerminal={onPick}
      />
    </section>
  );
};
