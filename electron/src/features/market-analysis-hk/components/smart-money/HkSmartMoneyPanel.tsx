/** 港股聪明钱 · 面板容器 v2.1（细案 §4.1：统一大框 + 概览带常显 + 五标签面）。
 *
 * 布局：统一大框（bg-white/90 + rounded-2xl + 细边）内——
 * header（标题/免责/截至/个股档案输入/刷新）→ 概览带（常显，不随 tab）→
 * tablist 五面：机构优势（默认）/ 类型效力 / 生态趋势 / 共识榜 / 稳健性与口径；
 * 仅激活面挂载取数（useFaceData 懒取数；overview 除外，常显面共用）；
 * 双抽屉：席位档案（榜单行/散点进入，可再下钻个股档案）+ 个股档案（输入框/共识榜/事件表进入）。
 * evidence-only 时：概览带与五面照常（零入柱是数据结论）——不是链路故障。
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Activity, Medal, RefreshCw, Search, ShieldCheck, Table2, Users } from 'lucide-react';
import { Input } from 'antd';
import {
  HkSmartMoneyError,
  getHkSmConsensus,
  getHkSmEvidence,
  getHkSmOverview,
  getHkSmPersistence,
  getHkSmTypes,
  type HkSmOverviewResponse,
} from '../../services/api';
import { EmptyHint } from '../../../market-analysis-shared/ui';
import { HK_SM_DISCLAIMER, HK_SM_TITLE, MISSING } from '../../hkSmartMoneyVocabulary';
import { HkSmOverviewBand } from './HkSmOverviewBand';
import { HkSmAdvantageFace } from './HkSmAdvantageFace';
import { HkSmTypeBoard } from './HkSmTypeBoard';
import { HkSmEcoFace } from './HkSmEcoFace';
import { HkSmConsensusBoard } from './HkSmConsensusBoard';
import { HkSmPersistenceFace } from './HkSmPersistenceFace';
import { HkSmStockDrawer } from './HkSmStockDrawer';
import { HkSmSeatDrawer } from './HkSmSeatDrawer';
import { useFaceData, type FaceData } from './useFaceData';

interface HkSmartMoneyPanelProps {
  /** 抽屉内「打开个股终端」入口 */
  onPick?: (symbol: string) => void;
}

type FaceId = 'advantage' | 'types' | 'eco' | 'consensus' | 'persistence';

const FACES: Array<{ id: FaceId; label: string; icon: React.ReactNode }> = [
  { id: 'advantage', label: '机构优势', icon: <Medal className="w-3 h-3" /> },
  { id: 'types', label: '类型效力', icon: <Table2 className="w-3 h-3" /> },
  { id: 'eco', label: '生态趋势', icon: <Activity className="w-3 h-3" /> },
  { id: 'consensus', label: '共识榜', icon: <Users className="w-3 h-3" /> },
  { id: 'persistence', label: '稳健性与口径', icon: <ShieldCheck className="w-3 h-3" /> },
];

/** 面加载态：错误优先（错误条自渲染）；首取未归为 loading */
function faceLoading(fd: FaceData<unknown>): boolean {
  return fd.error ? false : fd.loading || !fd.loaded;
}

export const HkSmartMoneyPanel: React.FC<HkSmartMoneyPanelProps> = ({ onPick }) => {
  const [face, setFace] = useState<FaceId>('advantage');
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);
  const [drawerSeat, setDrawerSeat] = useState<string | null>(null);
  const [jump, setJump] = useState('');
  const [refreshToken, setRefreshToken] = useState(0);

  // overview 常显：概览带 + 面 1（类别优势/散点语境）+ 面 3（趋势）共用
  const [overview, setOverview] = useState<HkSmOverviewResponse | null>(null);
  const [ovLoading, setOvLoading] = useState(true);
  const [ovError, setOvError] = useState<string | null>(null);
  const [notBuilt, setNotBuilt] = useState(false);

  const loadOverview = useCallback(async () => {
    setOvLoading(true);
    setOvError(null);
    try {
      const d = await getHkSmOverview();
      setOverview(d);
      setNotBuilt(false);
    } catch (e) {
      if (e instanceof HkSmartMoneyError && e.notBuilt) {
        setNotBuilt(true);
        setOverview(null);
      } else {
        setOvError(e instanceof Error ? e.message : '概览加载失败');
      }
    } finally {
      setOvLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // 懒面取数（首次切入才请求；刷新 = reset 复位闸 → 激活面自动重取）
  const fetchTypes = useCallback(() => getHkSmTypes('10'), []);
  const fetchConsensus = useCallback(() => getHkSmConsensus(50), []);
  const fetchEvidence = useCallback(() => getHkSmEvidence(), []);
  const fetchPersistence = useCallback(() => getHkSmPersistence(), []);

  const types = useFaceData(fetchTypes, '类型效力加载失败');
  const consensus = useFaceData(fetchConsensus, '共识榜加载失败');
  const evidence = useFaceData(fetchEvidence, '证据面加载失败');
  const persistence = useFaceData(fetchPersistence, '持续性审计加载失败');

  // 激活面自动首取（守卫：已加载/加载中/已报错都不自动重发——重试走刷新）
  useEffect(() => {
    if (face !== 'types') return;
    if (!types.loaded && !types.loading && !types.error) void types.load();
  }, [face, types.loaded, types.loading, types.error, types.load]);
  useEffect(() => {
    if (face !== 'consensus') return;
    if (!consensus.loaded && !consensus.loading && !consensus.error) void consensus.load();
  }, [face, consensus.loaded, consensus.loading, consensus.error, consensus.load]);
  useEffect(() => {
    if (face !== 'persistence') return;
    if (!persistence.loaded && !persistence.loading && !persistence.error) void persistence.load();
  }, [face, persistence.loaded, persistence.loading, persistence.error, persistence.load]);
  useEffect(() => {
    if (face !== 'persistence') return;
    if (!evidence.loaded && !evidence.loading && !evidence.error) void evidence.load();
  }, [face, evidence.loaded, evidence.loading, evidence.error, evidence.load]);

  const handleRefresh = useCallback(() => {
    void loadOverview();
    types.reset();
    consensus.reset();
    evidence.reset();
    persistence.reset();
    setRefreshToken((t) => t + 1); // 席位榜自持取数的重取信号
  }, [loadOverview, types, consensus, evidence, persistence]);

  const handleJump = useCallback(() => {
    const code = jump.trim().toUpperCase();
    if (!code) return;
    setDrawerSymbol(code);
  }, [jump]);

  const fresh = overview?.freshness ?? null;

  return (
    <section className="bg-white/90 backdrop-blur-md rounded-2xl border border-slate-200/80 shadow-sm p-4 flex flex-col gap-3 min-w-0">
      {/* 头部 */}
      <header className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h2 className="text-sm font-extrabold text-slate-800">{HK_SM_TITLE}</h2>
          <span className="text-[10px] text-slate-400">{HK_SM_DISCLAIMER}</span>
          {overview?.as_of && (
            <span className="text-[9px] font-mono text-slate-400">
              截至 <span className="font-bold text-slate-600">{overview.as_of}</span>
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
            onClick={handleRefresh}
            className="flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-extrabold text-slate-500 bg-white border border-slate-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${ovLoading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>
      </header>

      {notBuilt ? (
        <div className="flex flex-col gap-2 py-4">
          <EmptyHint text="港股聪明钱统计尚未生成（首次构建完成后展示；每日 19:40 / 21:40 两档调度滚动更新）" />
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => void loadOverview()}
              className="flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-extrabold text-purple-700 bg-purple-50 border border-purple-100 hover:bg-purple-100 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${ovLoading ? 'animate-spin' : ''}`} />
              重新加载
            </button>
          </div>
        </div>
      ) : (
        <>
          {overview?.partial && (
            <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
              部分数据源未就绪：当日产物以现有数据构建（缺口在各卡片日期与覆盖列如实标注，「—」为缺失非 0）
            </div>
          )}
          {ovError && (
            <div className="flex items-center gap-2 rounded-xl bg-red-50/80 border border-red-200 px-3 py-1.5">
              <span className="text-[10px] text-red-700 font-bold min-w-0 truncate">{ovError}</span>
              <button
                type="button"
                onClick={() => void loadOverview()}
                className="ml-auto shrink-0 text-[10px] font-bold text-red-700 hover:text-red-900"
              >
                重试
              </button>
            </div>
          )}

          {/* 概览带（常显，不随 tab） */}
          <HkSmOverviewBand data={overview} loading={ovLoading} />

          {/* 五面 tablist */}
          <div
            role="tablist"
            aria-label="聪明钱视图"
            className="flex gap-0.5 rounded-full bg-slate-100/90 p-0.5 self-start max-w-full overflow-x-auto"
          >
            {FACES.map((f) => {
              const active = face === f.id;
              return (
                <button
                  key={f.id}
                  role="tab"
                  aria-selected={active}
                  type="button"
                  onClick={() => setFace(f.id)}
                  className={`flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-extrabold whitespace-nowrap transition-all ${
                    active
                      ? 'bg-white text-purple-700 shadow-2xs border border-purple-200'
                      : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {f.icon}
                  {f.label}
                </button>
              );
            })}
          </div>

          {/* 面内容（仅激活面挂载） */}
          <div role="tabpanel" aria-label={FACES.find((f) => f.id === face)?.label} className="min-w-0">
            {face === 'advantage' && (
              <HkSmAdvantageFace
                overview={overview}
                overviewLoading={ovLoading}
                refreshToken={refreshToken}
                onPickSeat={(pid) => setDrawerSeat(pid)}
              />
            )}
            {face === 'types' && (
              <HkSmTypeBoard
                data={types.data}
                loading={faceLoading(types)}
                error={types.error}
                bare
              />
            )}
            {face === 'eco' && <HkSmEcoFace overview={overview} loading={ovLoading} />}
            {face === 'consensus' && (
              <HkSmConsensusBoard
                data={consensus.data}
                loading={faceLoading(consensus)}
                error={consensus.error}
                onPick={(s) => setDrawerSymbol(s)}
                bare
              />
            )}
            {face === 'persistence' && (
              <HkSmPersistenceFace
                data={persistence.data}
                loading={faceLoading(persistence)}
                error={persistence.error}
                onRetry={() => void persistence.load()}
                evidence={evidence.data}
                evidenceLoading={faceLoading(evidence)}
              />
            )}
          </div>

          {/* 新鲜度脚注（常显） */}
          <p className="text-[9px] font-mono text-slate-400 border-t border-slate-100 pt-2">
            行情 {fresh?.kline_date ?? MISSING} · CCASS {fresh?.ccass_date ?? MISSING}
            {fresh?.ccass_lag_days != null ? `（滞后 ${fresh.ccass_lag_days} 交易日）` : ''} · 南向{' '}
            {fresh?.south_date ?? MISSING}
            {overview?.summary
              ? ` · 复合分快照 ${overview.summary.scores_date ?? MISSING}`
              : ''}
          </p>
        </>
      )}

      {/* 双抽屉 */}
      <HkSmSeatDrawer
        participantId={drawerSeat}
        open={!!drawerSeat}
        onClose={() => setDrawerSeat(null)}
        onPickSymbol={(s) => setDrawerSymbol(s)}
      />
      <HkSmStockDrawer
        symbol={drawerSymbol}
        open={!!drawerSymbol}
        onClose={() => setDrawerSymbol(null)}
        onOpenTerminal={onPick}
      />
    </section>
  );
};
