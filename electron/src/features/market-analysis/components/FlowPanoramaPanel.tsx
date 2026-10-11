/**
 * 资金流全景面板（细案 v2 §5 状态中枢）。
 *
 * 布局：工具栏（周期/体系/方向/个股 TopK/数据日期）→ 证据 meta 披露条 →
 * 板块标签融合条（统一搜索 + 热门板块）→ 4 层桑基 v2（全宽）→
 * 板块⇄个股 双向工作台（选中个股时右侧加挂个股详情卡）。
 *
 * 状态纪律：
 * - 切体系 → 清板块选中（不同体系下名称不可混用）；切周期/方向/日期只重取；
 * - 外部 focusSymbol（顶栏搜索/其他面板）→ 打开个股卡；关闭时回报 onClearFocus
 *   以便同一代码可再次触发；
 * - 证据 meta 缺失/陈旧/不适用一律显式披露，绝不假装有 streak 数据。
 */

import React, { useEffect, useRef, useState } from 'react';
import { Spin } from 'antd';
import { Clock, Info, AlertCircle, Network, RefreshCw, Waves } from 'lucide-react';
import { FlowSankeyV2 } from './FlowSankeyV2';
import { SectorFlowWorkbench } from './SectorFlowWorkbench';
import { StockFlowDetailCard } from './StockFlowDetailCard';
import { FlowEvidencePanel } from './FlowEvidencePanel';
import { TagBrowserStrip } from './TagBrowserStrip';
import {
  getFlowPanorama,
  getFlowSector,
  MoneyFlowError,
  panoramaEmptyHint,
  type FlowDirection,
  type FlowEvidenceMeta,
  type FlowPanoramaNode,
  type FlowPanoramaResponse,
  type FlowPeriod,
  type FlowSectorDetailResponse,
  type FlowSectorSet,
} from '../services/moneyFlowService';

const PERIODS: Array<{ id: FlowPeriod; label: string }> = [
  { id: '1d', label: '1日' },
  { id: '3d', label: '3日' },
  { id: '5d', label: '5日' },
  { id: '10d', label: '10日' },
  { id: '20d', label: '20日' },
];
const SECTOR_SETS: Array<{ id: FlowSectorSet; label: string }> = [
  { id: 'industry2', label: '行业二级' },
  { id: 'industry1', label: '行业一级' },
  { id: 'concept', label: '概念' },
];
const DIRECTIONS: Array<{ id: FlowDirection; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'inflow', label: '净流入' },
  { id: 'outflow', label: '净流出' },
];
const TOP_KS = [3, 5, 10] as const;
const TOP_SECTORS = 10; // 6 入 + 4 出（direction=all 时后端 6:4 拆分）

/** 后端产物里的裸日期（20261010）→ 展示用 ISO；已是 ISO 则原样。 */
function fmtRawDate(s: string | null | undefined): string {
  if (!s) return '—';
  return s.length === 8 && /^\d{8}$/.test(s)
    ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`
    : s;
}

interface EvidenceStrip {
  tone: 'ok' | 'warn' | 'muted';
  text: string;
}

function evidenceStrip(meta: FlowEvidenceMeta | undefined): EvidenceStrip | null {
  if (!meta) return null;
  switch (meta.reason) {
    case 'ok':
      return {
        tone: 'ok',
        text: `延续证据产物交易日 ${meta.trade_date ?? '—'}${
          meta.window ? `，样本窗 ${meta.window[0]}~${meta.window[1]}` : ''
        }；板块「连N」徽章悬停可查看条件超额（含分年样本）`,
      };
    case 'stale':
      return {
        tone: 'warn',
        text: `延续证据产物停留在 ${meta.trade_date ?? '—'}，落后于最新行情——本次不展示连续净流入与条件超额`,
      };
    case 'not_built':
      return { tone: 'muted', text: '延续证据产物尚未生成（离线任务未运行）——本次不展示连续净流入与条件超额' };
    case 'not_applicable':
      return { tone: 'muted', text: '连续净流入证据当前只覆盖「行业二级」分类，该体系下不展示条件超额徽章' };
    default:
      return null;
  }
}

const Chip: React.FC<{ active: boolean; onClick: () => void; children: React.ReactNode }> = ({
  active,
  onClick,
  children,
}) => (
  <button
    onClick={onClick}
    className={`px-3.5 py-1 rounded-full text-xs font-extrabold transition-all duration-200 cursor-pointer whitespace-nowrap ${
      active
        ? 'bg-purple-600 text-white shadow-md shadow-purple-600/20 scale-[1.02]'
        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
    }`}
  >
    {children}
  </button>
);

const Group: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center gap-2">
    <span className="text-xs font-extrabold text-slate-700 whitespace-nowrap">{label}</span>
    <div className="flex items-center bg-slate-100/90 border border-slate-200/60 p-1 rounded-full gap-1 shadow-2xs">
      {children}
    </div>
  </div>
);

export interface FlowPanoramaPanelProps {
  /** 页面快照日期（ISO yyyy-mm-dd）；undefined=最新 */
  date?: string;
  /** 外部聚焦个股（顶栏搜索/其他面板入口），非空时打开个股卡 */
  focusSymbol?: string | null;
  onClearFocus?: () => void;
}

export const FlowPanoramaPanel: React.FC<FlowPanoramaPanelProps> = ({ date, focusSymbol, onClearFocus }) => {
  const [period, setPeriod] = useState<FlowPeriod>('1d');
  const [sectorSet, setSectorSet] = useState<FlowSectorSet>('industry2');
  const [direction, setDirection] = useState<FlowDirection>('all');
  const [stocksPerSector, setStocksPerSector] = useState<number>(5);
  const [refreshTick, setRefreshTick] = useState(0);

  const [data, setData] = useState<FlowPanoramaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedSector, setSelectedSector] = useState<string | null>(null);
  const [detail, setDetail] = useState<FlowSectorDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [selectedStock, setSelectedStock] = useState<{ symbol: string; name?: string } | null>(null);

  const panoSeq = useRef(0);
  const detailSeq = useRef(0);

  // ── 全景取数（周期/体系/方向/TopK/快照日期/手动刷新 全量重取） ──
  useEffect(() => {
    const seq = ++panoSeq.current;
    setLoading(true);
    setError(null);
    getFlowPanorama({
      period,
      sectorSet,
      topSectors: TOP_SECTORS,
      stocksPerSector,
      direction,
      date,
    })
      .then((d) => {
        if (seq !== panoSeq.current) return;
        setData(d);
      })
      .catch((e: unknown) => {
        if (seq !== panoSeq.current) return;
        setData(null);
        setError(e instanceof MoneyFlowError ? `资金流全景取数失败：${e.message}` : '资金流全景取数失败，请稍后重试');
      })
      .finally(() => {
        if (seq === panoSeq.current) setLoading(false);
      });
  }, [period, sectorSet, direction, stocksPerSector, date, refreshTick]);

  // ── 板块明细取数 ──
  useEffect(() => {
    if (!selectedSector) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    setDetailError(null);
    getFlowSector(selectedSector, { sectorSet, period, top: 30, date })
      .then((d) => {
        if (seq !== detailSeq.current) return;
        setDetail(d);
      })
      .catch((e: unknown) => {
        if (seq !== detailSeq.current) return;
        setDetail(null);
        setDetailError(
          e instanceof MoneyFlowError && e.status === 404
            ? '该板块在当前体系与日期下查无数据'
            : '板块明细取数失败，请稍后重试',
        );
      })
      .finally(() => {
        if (seq === detailSeq.current) setDetailLoading(false);
      });
  }, [selectedSector, sectorSet, period, date]);

  // ── 外部聚焦个股 → 打开个股卡 ──
  useEffect(() => {
    if (focusSymbol) setSelectedStock({ symbol: focusSymbol });
  }, [focusSymbol]);

  const handleSectorSetChange = (s: FlowSectorSet) => {
    if (s === sectorSet) return;
    setSectorSet(s);
    setSelectedSector(null); // 不同体系的板块名称不可跨用
  };

  const handleSelectStock = (symbol: string, name?: string) => {
    setSelectedStock({ symbol, name });
  };

  const handleCloseStock = () => {
    setSelectedStock(null);
    onClearFocus?.();
  };

  const handleNavigateSector = (name: string, set: string | null) => {
    if (set && set !== sectorSet) setSectorSet(set as FlowSectorSet);
    setSelectedSector(name);
  };

  const strip = evidenceStrip(data?.meta.evidence);
  const stripClass =
    strip?.tone === 'ok'
      ? 'bg-emerald-50/90 text-emerald-700 border-emerald-200/70'
      : strip?.tone === 'warn'
        ? 'bg-amber-50/90 text-amber-700 border-amber-200/70'
        : 'bg-slate-50 text-slate-500 border-slate-200/70';

  return (
    <div className="flex flex-col gap-3.5 min-w-0">
      {/* ── 工具栏 ── */}
      <div className="bg-white/95 backdrop-blur-md rounded-2xl p-3.5 border border-purple-100/80 shadow-xs flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4">
            <Group label="窗口">
              {PERIODS.map((p) => (
                <Chip key={p.id} active={period === p.id} onClick={() => setPeriod(p.id)}>
                  {p.label}
                </Chip>
              ))}
            </Group>
            <Group label="体系">
              {SECTOR_SETS.map((s) => (
                <Chip key={s.id} active={sectorSet === s.id} onClick={() => handleSectorSetChange(s.id)}>
                  {s.label}
                </Chip>
              ))}
            </Group>
            <Group label="方向">
              {DIRECTIONS.map((d) => (
                <Chip key={d.id} active={direction === d.id} onClick={() => setDirection(d.id)}>
                  {d.label}
                </Chip>
              ))}
            </Group>
            <Group label="个股数">
              {TOP_KS.map((k) => (
                <Chip key={k} active={stocksPerSector === k} onClick={() => setStocksPerSector(k)}>
                  Top {k}
                </Chip>
              ))}
            </Group>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {data?.trade_date && (
              <span className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-50 text-slate-600 border border-slate-200/70 text-[11px] font-bold font-mono whitespace-nowrap">
                <Clock className="w-3 h-3 text-purple-500" />
                <span>数据日期</span>
                <span className="text-purple-700 font-extrabold">{data.trade_date}</span>
                {data.meta.window_days && (
                  <span className="text-slate-400">
                    （窗口 {fmtRawDate(data.meta.window_days[0])}~{fmtRawDate(data.meta.window_days[1])}）
                  </span>
                )}
              </span>
            )}
            {data?.meta.coverage && (
              <span
                title="窗口内有 L2 资金流数据、且能映射到当前板块体系的个股覆盖率"
                className="px-3 py-1 rounded-full bg-purple-50 text-purple-700 border border-purple-200/70 text-[11px] font-bold font-mono whitespace-nowrap"
              >
                覆盖 {data.meta.coverage.covered_symbols}/{data.meta.coverage.flow_symbols}
                {data.meta.coverage.covered_pct !== null ? `（${data.meta.coverage.covered_pct}%）` : ''}
              </span>
            )}
            <button
              onClick={() => setRefreshTick((t) => t + 1)}
              disabled={loading}
              className="flex items-center gap-1 px-3 py-1 rounded-full bg-white text-slate-600 border border-slate-200/80 hover:bg-slate-50 text-[11px] font-extrabold transition-colors cursor-pointer disabled:opacity-50"
              title="重新取数（服务端缓存 5 分钟）"
            >
              <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>
        </div>

        {strip && (
          <div className={`flex items-start gap-1.5 px-3 py-1.5 rounded-xl border text-[11px] font-bold leading-snug ${stripClass}`}>
            <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
            <span>{strip.text}</span>
          </div>
        )}
      </div>

      {/* ── 板块标签融合条（统一搜索 + 热门板块；数据面 /tags/*，不依赖全景取数） ── */}
      <TagBrowserStrip
        currentSectors={data?.sectors?.map((s) => s.name) ?? []}
        onNavigateSector={handleNavigateSector}
        onSelectStock={handleSelectStock}
      />

      {/* ── 取数失败 ── */}
      {!loading && error && (
        <div className="bg-white/95 rounded-2xl border border-rose-100 p-6 flex flex-col items-center gap-2 text-center">
          <AlertCircle className="w-5 h-5 text-rose-400" />
          <span className="text-xs text-slate-600 font-bold">{error}</span>
          <button
            onClick={() => setRefreshTick((t) => t + 1)}
            className="mt-1 px-4 py-1.5 rounded-full bg-purple-600 text-white text-[11px] font-extrabold hover:bg-purple-500 cursor-pointer"
          >
            重试
          </button>
        </div>
      )}

      {/* ── 数据不可用（available=false，含原因） ── */}
      {!loading && !error && data && !data.available && (
        <div className="bg-white/95 rounded-2xl border border-purple-100/80 p-8 flex flex-col items-center gap-2.5 text-center">
          <Waves className="w-6 h-6 text-slate-300" />
          <span className="text-sm font-extrabold text-slate-700">资金流全景暂不可用</span>
          <span className="text-xs text-slate-400 max-w-md leading-relaxed">
            {panoramaEmptyHint(data.meta.reason)}
          </span>
        </div>
      )}

      {/* ── 桑基 v2 ── */}
      {!error && (!data || data.available) && (
        <div className="bg-white/95 backdrop-blur-md rounded-2xl p-3.5 border border-purple-100/80 shadow-md shadow-purple-500/5 flex flex-col gap-2 min-w-0">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
              <Network className="w-3.5 h-3.5 text-purple-600" />
              <span>主力与散户资金流全景 · 四层桑基（主体 → 单型 → 板块 → 个股）</span>
            </h3>
            <span className="text-[10px] text-slate-400 font-mono">
              红=净流入 · 绿=净流出 · 边宽=|净额| · 点板块/个股节点联动下方工作台
            </span>
          </div>
          <FlowSankeyV2
            nodes={data?.nodes ?? []}
            links={data?.links ?? []}
            loading={loading}
            height={560}
            selectedSector={selectedSector}
            onSelectSector={setSelectedSector}
            onSelectStock={(n: FlowPanoramaNode) => handleSelectStock(n.key, n.name)}
          />
        </div>
      )}

      {/* ── 工作台 + 个股卡 ── */}
      {!error && (!data || data.available) && (
        <div className="flex flex-col xl:flex-row gap-3.5 min-w-0 items-stretch">
          <div className="flex-1 min-w-0">
            <SectorFlowWorkbench
              rows={data?.sectors ?? []}
              selectedSector={selectedSector}
              onSelectSector={setSelectedSector}
              detail={selectedSector ? detail : null}
              detailLoading={detailLoading}
              detailError={detailError}
              onSelectStock={handleSelectStock}
              compact={!!selectedStock}
            />
          </div>
          {selectedStock && (
            <div className="w-full xl:w-[380px] shrink-0">
              <StockFlowDetailCard
                symbol={selectedStock.symbol}
                date={date}
                onClose={handleCloseStock}
                onNavigateSector={handleNavigateSector}
              />
            </div>
          )}
        </div>
      )}

      {/* ── 延续证据台（命中榜 + 信号档案；点击命中榜联动桑基/工作台） ── */}
      <FlowEvidencePanel date={date} onSelectSector={(name) => handleNavigateSector(name, 'industry2')} />

      {/* ── 常驻披露 ── */}
      {!error && data?.meta.disclosure && data.meta.disclosure.length > 0 && (
        <div className="px-1 flex flex-col gap-0.5">
          {data.meta.disclosure.map((line, i) => (
            <p key={i} className="text-[10px] text-slate-400 leading-snug">
              {line}
            </p>
          ))}
        </div>
      )}
    </div>
  );
};
