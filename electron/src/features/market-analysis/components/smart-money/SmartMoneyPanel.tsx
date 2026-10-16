/** 聪明钱 · 面板容器 v2.1（A 股市场分析「聪明钱」Tab；一框六面重排）。
 *
 * 布局（2026-10-16 重排，自上而下同一张卡）：
 *   头部（标题/口径/数据截至/日期/刷新）→ 概览带（6 卡常驻）→ 分面标签
 *   （选股榜/战绩追踪/席位面/事件研究/生态趋势/验证口径）→ 激活面内容。
 * 仅激活面挂载渲染（ECharts 不进 display:none 空转），长表限高滚动——
 * 竖向占用从约 9 屏收敛到 1–2 屏；口径说明随面归位（验证口径面尾）。
 *
 * 取数分工：overview 随挂载取（概览带 + 生态/验证面共用，404=未构建空态、
 * 失败=错误条）；选股榜/席位榜自持取数（服务端筛选分页，内部错误条）；
 * 战绩/事件研究/证据台首次切到该面才取（懒加载，useFaceData）。
 * 容错纪律（B2/B3/B4）：各面独立结算、失败显式呈现，绝不互相拖垮或伪装空数据。
 * 快照日期由 date prop（页面 DatePicker，B6）驱动，并可用面板下拉细化到历史计分日。
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  CalendarDays,
  Crosshair,
  FlaskConical,
  FlaskRound,
  History,
  Info,
  RefreshCw,
  Store,
  ThermometerSun,
} from 'lucide-react';
import { EmptyHint, FaceCard, SectionCard } from '../../../market-analysis-shared/ui';
import {
  SmartMoneyError,
  getSmartMoneyDates,
  getSmartMoneyEventStudy,
  getSmartMoneyEvidence,
  getSmartMoneyOverview,
  getSmartMoneyBranches,
  getSmartMoneyTrackRecord,
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
import { useFaceData } from './useFaceData';

type FaceId = 'stocks' | 'track' | 'seats' | 'event' | 'eco' | 'evidence';

interface FaceDef {
  id: FaceId;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

/** 分面定义（顺序 = 使用频度：找票 → 验战绩 → 看席位 → 查研究 → 读生态 → 审计口径） */
const FACES: FaceDef[] = [
  { id: 'stocks', label: '选股榜', icon: Crosshair },
  { id: 'track', label: '战绩追踪', icon: History },
  { id: 'seats', label: '席位面', icon: Store },
  { id: 'event', label: '事件研究', icon: FlaskRound },
  { id: 'eco', label: '生态趋势', icon: Activity },
  { id: 'evidence', label: '验证口径', icon: FlaskConical },
];

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

/** 面内取数失败条（懒面专用：失败显式 + 手动重试，绝不清空伪装空态） */
const FaceErrorStrip: React.FC<{ message: string; onRetry: () => void }> = ({
  message,
  onRetry,
}) => (
  <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
    <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
    <span className="text-[10px] text-amber-800 min-w-0 truncate">{message}</span>
    <button
      type="button"
      onClick={onRetry}
      className="ml-auto shrink-0 text-[10px] font-bold text-amber-700 hover:text-amber-900"
    >
      重试
    </button>
  </div>
);

export const SmartMoneyPanel: React.FC<SmartMoneyPanelProps> = ({ date, onOpenTerminal }) => {
  const [tab, setTab] = useState<FaceId>('stocks');

  const [overview, setOverview] = useState<SmartOverviewResponse | null>(null);
  const [stocksMeta, setStocksMeta] = useState<StocksMeta | null>(null);
  const [dateOptions, setDateOptions] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(date ?? null);
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [notBuilt, setNotBuilt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // 营业部榜窗口（ref 供懒取数闭包读最新值；state 供展示与切档）
  const branchWindowRef = useRef<5 | 20>(5);
  const [branchWindow, setBranchWindow] = useState<5 | 20>(5);

  // 懒取数面：战绩 / 事件研究 / 证据台 / 营业部榜（首次切入才取；404 交给空态文案）
  const trackFace = useFaceData<SmartTrackRecordResponse>(getSmartMoneyTrackRecord, '战绩数据加载失败');
  const eventFace = useFaceData<SmartEventStudyResponse>(getSmartMoneyEventStudy, '事件研究数据加载失败');
  const evidenceFace = useFaceData<SmartEvidenceResponse>(getSmartMoneyEvidence, '验证证据数据加载失败');
  const branchesFace = useFaceData(
    useCallback(() => getSmartMoneyBranches(branchWindowRef.current), []),
    '席位数据加载失败',
  );

  // 页面日期（DatePicker）→ 面板选中日期（B6 单向同步）
  useEffect(() => {
    setSelectedDate(date ?? null);
  }, [date]);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ov = await getSmartMoneyOverview();
      setOverview(ov);
      setNotBuilt(false);
    } catch (e) {
      // 主面错误裁决：404 → 未构建空态；其他 → 错误条（B3：不影响其余面）
      if (e instanceof SmartMoneyError && e.notBuilt) {
        setNotBuilt(true);
        setError(null);
      } else {
        setError(e instanceof Error ? e.message : '聪明钱数据加载失败');
      }
    } finally {
      setLoading(false);
    }
    void getSmartMoneyDates()
      .then((d) => setDateOptions(d.dates ?? []))
      .catch(() => setDateOptions([]));
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // 面激活即按需取数（loaded 闸挡重复；失败置 error 后只走显式重试，不自动风暴）
  useEffect(() => {
    if (tab === 'track' && !trackFace.loaded && !trackFace.loading && !trackFace.error) {
      void trackFace.load();
    }
    if (tab === 'event' && !eventFace.loaded && !eventFace.loading && !eventFace.error) {
      void eventFace.load();
    }
    if (tab === 'evidence' && !evidenceFace.loaded && !evidenceFace.loading && !evidenceFace.error) {
      void evidenceFace.load();
    }
    if (tab === 'seats' && !branchesFace.loaded && !branchesFace.loading && !branchesFace.error) {
      void branchesFace.load();
    }
  }, [tab, trackFace, eventFace, evidenceFace, branchesFace]);

  const handleRefresh = useCallback(() => {
    setRefreshToken((n) => n + 1); // 选股榜/席位榜（自持取数）经 refreshToken 重拉
    trackFace.reset();
    eventFace.reset();
    evidenceFace.reset();
    branchesFace.reset();
    void loadOverview();
  }, [loadOverview, trackFace, eventFace, evidenceFace, branchesFace]);

  const handleBranchWindowChange = useCallback(
    (w: 5 | 20) => {
      branchWindowRef.current = w;
      setBranchWindow(w);
      void branchesFace.load();
    },
    [branchesFace],
  );

  /** 懒面在首次结算前一律按 loading 呈现（否则切面瞬间会闪现「尚未生成」空态） */
  const faceLoading = useCallback(
    (f: { loading: boolean; loaded: boolean; error: string | null }) =>
      f.error ? false : f.loading || !f.loaded,
    [],
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
      {/* ─── 统一大框：头部 + 概览带 + 分面标签 + 激活面 ─── */}
      <div className="bg-white/90 backdrop-blur-md rounded-2xl border border-slate-200/80 shadow-sm flex flex-col">
        {/* 头部 */}
        <header className="flex items-center justify-between gap-2 flex-wrap px-4 pt-3.5 pb-3">
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

        {(partial || error) && (
          <div className="px-4 pb-3 flex flex-col gap-1.5">
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
          </div>
        )}

        {/* 概览带（6 卡常驻，浅底带衬托） */}
        <div className="px-4 pb-3.5">
          <div className="rounded-2xl bg-slate-50/70 border border-slate-100 p-2.5">
            <SmartMoneyOverviewBand
              overview={overview?.overview ?? null}
              trends={overview?.trends ?? null}
              northNote={overview?.north_note ?? null}
              loading={loading}
            />
          </div>
        </div>

        {/* 分面标签（与 PeriodChips 同一视觉语言；仅激活面挂载） */}
        <div className="px-4 py-3 border-t border-slate-100">
          <div
            role="tablist"
            aria-label="聪明钱分面"
            className="flex items-center gap-0.5 rounded-full bg-slate-100/90 p-0.5 max-w-full overflow-x-auto w-fit"
          >
            {FACES.map((f) => {
              const Icon = f.icon;
              const active = tab === f.id;
              return (
                <button
                  key={f.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTab(f.id)}
                  className={`flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-extrabold whitespace-nowrap transition-all ${
                    active
                      ? 'bg-white text-purple-700 shadow-2xs border border-purple-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  <Icon className="w-3 h-3" />
                  {f.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 激活面内容 */}
        <div role="tabpanel" className="px-4 pb-4 min-w-0">
          {tab === 'stocks' && (
            <SmartMoneyStockBoard
              bare
              date={selectedDate}
              refreshToken={refreshToken}
              industryOptions={industryOptions}
              onPick={(s) => setDrawerSymbol(s)}
              onMeta={setStocksMeta}
            />
          )}

          {tab === 'track' &&
            (trackFace.error ? (
              <FaceErrorStrip
                message={trackFace.error}
                onRetry={() => void trackFace.load()}
              />
            ) : (
              <SmartMoneyTrackRecordSection
                bare
                data={trackFace.data}
                loading={faceLoading(trackFace)}
                onPick={(s) => setDrawerSymbol(s)}
              />
            ))}

          {tab === 'seats' && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start min-w-0">
              <SmartMoneySeatBoard bare refreshToken={refreshToken} />
              <SmartMoneyBranchBoard
                bare
                rows={branchesFace.data?.branches ?? null}
                window={branchWindow}
                onWindowChange={handleBranchWindowChange}
                loading={faceLoading(branchesFace)}
                error={branchesFace.error}
                onRetry={() => void branchesFace.load()}
              />
            </div>
          )}

          {tab === 'event' &&
            (eventFace.error ? (
              <FaceErrorStrip
                message={eventFace.error}
                onRetry={() => void eventFace.load()}
              />
            ) : (
              <SmartMoneyEventStudySection
                bare
                data={eventFace.data}
                loading={faceLoading(eventFace)}
              />
            ))}

          {tab === 'eco' && (
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 items-start min-w-0">
              <div className="xl:col-span-2 min-w-0">
                <SmartMoneyEcoTrendSection
                  bare
                  trends={overview?.trends ?? null}
                  loading={loading}
                />
              </div>
              <SmartMoneyIndustryFlowCard
                bare
                rows={overview?.overview?.industry ?? null}
                loading={loading}
              />
            </div>
          )}

          {tab === 'evidence' && (
            <div className="flex flex-col gap-4 min-w-0">
              {evidenceFace.error ? (
                <FaceErrorStrip
                  message={evidenceFace.error}
                  onRetry={() => void evidenceFace.load()}
                />
              ) : (
                <SmartMoneyEvidenceCard
                  bare
                  evidence={evidenceFace.data}
                  loading={faceLoading(evidenceFace)}
                />
              )}
              <FaceCard bare icon={<Info className="w-3.5 h-3.5 text-purple-600" />} title="口径与新鲜度">
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
              </FaceCard>
            </div>
          )}
        </div>
      </div>

      {/* 个股档案抽屉（行点击打开；跨面共用一例） */}
      <SmartMoneyStockDrawer
        symbol={drawerSymbol}
        open={!!drawerSymbol}
        onClose={() => setDrawerSymbol(null)}
        onOpenTerminal={onOpenTerminal}
      />
    </section>
  );
};
