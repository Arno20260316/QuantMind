/** 聪明钱 · 面板容器 v2（A 股市场分析「聪明钱」Tab；细案 §6.2 八区信息架构）。
 *
 * 取数分工：overview/evidence/branches 由本容器取（404=未构建空态、失败=错误条）；
 * 选股榜/席位榜自持取数（服务端筛选分页，内部错误条）；快照日期由 date prop（页面
 * DatePicker，B6）驱动，并可用面板下拉细化到历史计分日。
 * 容错纪律（B2/B3/B4）：各面独立结算、失败显式呈现，绝不互相拖垮或伪装空数据。
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarDays, Info, RefreshCw, ThermometerSun } from 'lucide-react';
import { EmptyHint, SectionCard } from '../../../market-analysis-shared/ui';
import {
  SmartMoneyError,
  getSmartMoneyBranches,
  getSmartMoneyDates,
  getSmartMoneyEventStudy,
  getSmartMoneyEvidence,
  getSmartMoneyOverview,
  getSmartMoneyTrackRecord,
  type SmartBranchRow,
  type SmartEventStudyResponse,
  type SmartEvidenceResponse,
  type SmartOverviewResponse,
  type SmartTrackRecordResponse,
} from '../../services/smartMoneyService';
import { SMART_MONEY_DISCLAIMER, SMART_MONEY_TITLE } from '../../smartMoneyVocabulary';
import { SmartMoneyOverviewBand } from './SmartMoneyOverviewBand';
import { SmartMoneyEcoTrendSection } from './SmartMoneyEcoTrendSection';
import { SmartMoneyTrackRecordSection } from './SmartMoneyTrackRecordSection';
import { SmartMoneyEventStudySection } from './SmartMoneyEventStudySection';
import { SmartMoneyStockBoard } from './SmartMoneyStockBoard';
import { SmartMoneySeatBoard } from './SmartMoneySeatBoard';
import { SmartMoneyIndustryFlowCard } from './SmartMoneyIndustryFlowCard';
import { SmartMoneyBranchBoard } from './SmartMoneyBranchBoard';
import { SmartMoneyEvidenceCard } from './SmartMoneyEvidenceCard';
import { SmartMoneyStockDrawer } from './SmartMoneyStockDrawer';

interface SmartMoneyPanelProps {
  /** 页面级快照日期（DatePicker；B6）；null/缺省=最新 */
  date?: string | null;
  /** 抽屉内「打开个股终端」入口 */
  onOpenTerminal?: (symbol: string) => void;
}

interface StocksMeta {
  as_of: string | null;
  partial: boolean | null;
  total: number;
}

export const SmartMoneyPanel: React.FC<SmartMoneyPanelProps> = ({ date, onOpenTerminal }) => {
  const [overview, setOverview] = useState<SmartOverviewResponse | null>(null);
  const [evidence, setEvidence] = useState<SmartEvidenceResponse | null>(null);
  const [trackRecord, setTrackRecord] = useState<SmartTrackRecordResponse | null>(null);
  const [eventStudy, setEventStudy] = useState<SmartEventStudyResponse | null>(null);
  const [branches, setBranches] = useState<SmartBranchRow[] | null>(null);
  const [branchWindow, setBranchWindow] = useState<5 | 20>(5);
  const [branchError, setBranchError] = useState<string | null>(null);
  const [branchLoading, setBranchLoading] = useState(false);
  const [stocksMeta, setStocksMeta] = useState<StocksMeta | null>(null);

  const [dateOptions, setDateOptions] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(date ?? null);
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [notBuilt, setNotBuilt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // 页面日期（DatePicker）→ 面板选中日期（B6 单向同步）
  useEffect(() => {
    setSelectedDate(date ?? null);
  }, [date]);

  const fetchBranches = useCallback(async (win: 5 | 20) => {
    setBranchLoading(true);
    setBranchError(null);
    try {
      const r = await getSmartMoneyBranches(win);
      setBranches(r.branches);
    } catch (e) {
      // B4：失败进错误条，绝不清空后伪装「无记录」
      setBranchError(e instanceof Error ? e.message : '席位数据加载失败');
    } finally {
      setBranchLoading(false);
    }
  }, []);

  const loadCore = useCallback(
    async (win: 5 | 20) => {
      setLoading(true);
      setError(null);
      const [ov, ev, tr, es] = await Promise.allSettled([
        getSmartMoneyOverview(),
        getSmartMoneyEvidence(),
        getSmartMoneyTrackRecord(),
        getSmartMoneyEventStudy(),
      ]);

      // 主面（overview）错误裁决：404 → 未构建空态；其他 → 错误条（B3：不影响其余面）
      if (ov.status === 'fulfilled') {
        setOverview(ov.value);
        setNotBuilt(false);
      } else {
        const reason: unknown = ov.reason;
        if (reason instanceof SmartMoneyError && reason.notBuilt) {
          setNotBuilt(true);
          setError(null);
        } else {
          setError(reason instanceof Error ? reason.message : '聪明钱数据加载失败');
        }
      }
      if (ev.status === 'fulfilled') setEvidence(ev.value);
      if (tr.status === 'fulfilled') setTrackRecord(tr.value);
      if (es.status === 'fulfilled') setEventStudy(es.value);

      void fetchBranches(win);
      void getSmartMoneyDates()
        .then((d) => setDateOptions(d.dates ?? []))
        .catch(() => setDateOptions([]));
      setLoading(false);
    },
    [fetchBranches],
  );

  useEffect(() => {
    void loadCore(5);
    // 仅在挂载时首载；刷新走 handleRefresh
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefresh = useCallback(() => {
    setRefreshToken((n) => n + 1);
    void loadCore(branchWindow);
  }, [loadCore, branchWindow]);

  const handleBranchWindowChange = useCallback(
    (w: 5 | 20) => {
      setBranchWindow(w);
      void fetchBranches(w);
    },
    [fetchBranches],
  );

  const asOf = stocksMeta?.as_of ?? overview?.as_of ?? null;
  // B2：partial 用逻辑或（任一面声明 partial 即披露）
  const partial = Boolean(overview?.partial) || Boolean(stocksMeta?.partial);
  const freshness = overview?.freshness ?? null;
  const industryOptions = useMemo(
    () => (overview?.overview?.industry ?? []).map((r) => r.industry),
    [overview],
  );
  const dateSelectOptions = useMemo(() => [...dateOptions].reverse(), [dateOptions]);

  if (notBuilt) {
    return (
      <SectionCard icon={<ThermometerSun className="w-3.5 h-3.5 text-purple-600" />} title={SMART_MONEY_TITLE}>
        <EmptyHint text="聪明钱统计尚未生成（首次构建完成后展示；每日调度会滚动更新）" />
        <div className="flex justify-center">
          <button
            type="button"
            onClick={handleRefresh}
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
          {asOf && (
            <span className="text-[9px] font-mono text-slate-400">
              数据截至 <span className="font-bold text-slate-600">{asOf}</span>
              {selectedDate && selectedDate !== asOf ? `（请求 ${selectedDate}，回退最近快照）` : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="flex items-center gap-1 text-[10px] text-slate-500">
            <CalendarDays className="w-3 h-3 text-slate-400" />
            <select
              value={selectedDate ?? ''}
              onChange={(e) => setSelectedDate(e.target.value || null)}
              className="px-1.5 py-0.5 rounded-full text-[10px] font-bold border border-slate-200 bg-white text-slate-600"
            >
              <option value="">最新快照</option>
              {dateSelectOptions.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </span>
          <button
            type="button"
            onClick={handleRefresh}
            className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
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

      {/* 区 2：概览带（mini 走势消费 trends） */}
      <SmartMoneyOverviewBand
        overview={overview?.overview ?? null}
        trends={overview?.trends ?? null}
        northNote={overview?.north_note ?? null}
        loading={loading}
      />

      {/* 区 3：生态趋势 */}
      <SmartMoneyEcoTrendSection trends={overview?.trends ?? null} loading={loading} />

      {/* 区 4：战绩追踪（历史回溯统计） */}
      <SmartMoneyTrackRecordSection
        data={trackRecord}
        loading={loading}
        onPick={(s) => setDrawerSymbol(s)}
      />

      {/* 区 5：事件研究 */}
      <SmartMoneyEventStudySection data={eventStudy} loading={loading} />

      {/* 区 6：选股榜 + 席位榜 双栏 */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 items-start min-w-0">
        <SmartMoneyStockBoard
          date={selectedDate}
          refreshToken={refreshToken}
          industryOptions={industryOptions}
          onPick={(s) => setDrawerSymbol(s)}
          onMeta={setStocksMeta}
        />
        <SmartMoneySeatBoard refreshToken={refreshToken} />
      </div>

      {/* 区 7：行业净买 + 营业部榜，证据台收尾 */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3 items-start min-w-0">
        <SmartMoneyIndustryFlowCard
          rows={overview?.overview?.industry ?? null}
          loading={loading}
        />
        <SmartMoneyBranchBoard
          rows={branches}
          window={branchWindow}
          onWindowChange={handleBranchWindowChange}
          loading={branchLoading}
          error={branchError}
          onRetry={() => void fetchBranches(branchWindow)}
        />
        <SectionCard icon={<Info className="w-3.5 h-3.5 text-purple-600" />} title="口径与新鲜度">
          <ul className="flex flex-col gap-1 text-[10px] text-slate-500 leading-relaxed">
            <li>
              · 事件池 = 近 5 交易日龙虎榜/机构席位/大宗/调研 ∪ 两融异动（|净买 5 日
              z20|≥2）；柱分位 = 池内截面 0–100 分位。
            </li>
            <li>
              · 入柱 6 项经双过闸裁定（h10 前瞻 IC 与尾部同号），方向 = 实测 IC
              符号；负向柱分位已取反（100−分位），柱分高 = 历史有利方向。
            </li>
            <li>· 复合分 = 入柱等权均值；缺失柱按 50 计入并随行披露实际覆盖（n/6），缺失「—」绝不为 0。</li>
            <li>
              · 新鲜度：行情截至{' '}
              <span className="font-mono">{freshness?.kline_last ?? '—'}</span>；两融披露截至{' '}
              <span className="font-mono">{freshness?.margin_last ?? '—'}</span>（两融官方滞后行情约 1 交易日，余额类柱按最近披露值口径）。
            </li>
            <li>· 事件原始表附带的「上榜后表现」（fwd_ret_*）仅展示用途，已封存、绝不入信号。</li>
            <li>· 战绩/事件研究/滚动 IC 均为历史回溯统计（非预测）；样本小的窗口请连同 n 阅读。</li>
          </ul>
        </SectionCard>
      </div>

      <SmartMoneyEvidenceCard evidence={evidence} loading={loading} />

      {/* 区 8：个股档案抽屉 */}
      <SmartMoneyStockDrawer
        symbol={drawerSymbol}
        open={!!drawerSymbol}
        onClose={() => setDrawerSymbol(null)}
        onOpenTerminal={onOpenTerminal}
      />
    </section>
  );
};
