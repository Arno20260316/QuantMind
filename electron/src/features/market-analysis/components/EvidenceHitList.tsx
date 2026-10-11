/**
 * 延续命中榜（连续净流入 ≥ N 日）· 竖向列表 + 档位标签切换。
 *
 * v5 起并入左列「一窗两签」的命中榜签（与板块榜签互斥展示）：本组件按填充式
 * 渲染——无自有卡壳与主标题（窗口标题、切换器由 FlowPanoramaPanel 提供），
 * 头部仅保留「当前档位 + 产物日」一行；两签常挂载、以 hidden 切换，来回切签
 * 不重取证据。行点击共享选中态：联动上方桑基高亮与右列板块明细；档位由父级
 * 持有（与信号档案行高亮联动）。
 * 数据面独立取 /money-flow/evidence；产物缺失/陈旧一律显式披露，绝不回落 0。
 */

import React, { useEffect, useRef, useState } from 'react';
import { History, Info } from 'lucide-react';
import { EvidenceBadge } from './EvidenceBadge';
import { fmtCount, fmtRawDate } from './evidenceFormat';
import {
  defaultLevel,
  filterHits,
  hitCounts,
  streakLevels,
} from '../flowEvidenceVocabulary';
import {
  MoneyFlowError,
  flowTextClass,
  fmtRatio,
  fmtYi,
  getFlowEvidence,
  type FlowEvidenceHit,
  type FlowEvidenceResponse,
  type FlowSectorSet,
} from '../services/moneyFlowService';

/** 列表滚动区高度（v5：与右列明细卡列高相当；超出滚动，卡高不随命中数膨胀）。 */
const LIST_MAX_HEIGHT = 560;

/** 当前体系标签（仅用于「证据只覆盖行业二级」披露横幅的文案）。 */
const SET_LABEL: Record<FlowSectorSet, string> = {
  industry2: '行业二级',
  industry1: '行业一级',
  concept: '概念',
};

/** 命中榜行（两行制：窄栏下保住板块名；title 契约与旧卡片一致，探针与联动依赖此前缀）。 */
const HitRow: React.FC<{ hit: FlowEvidenceHit; rank: number; onClick: () => void }> = ({
  hit,
  rank,
  onClick,
}) => (
  <button
    onClick={onClick}
    className="group w-full flex flex-col gap-0.5 px-2.5 py-1.5 rounded-xl border border-slate-200/70 bg-white text-left hover:border-purple-300 hover:shadow-sm hover:shadow-purple-500/10 transition-all cursor-pointer min-w-0"
    title={`${hit.name} · 连续净流入 ${hit.streak} 日 · 点击联动桑基与工作台`}
  >
    <span className="w-full flex items-center gap-2 min-w-0">
      <span
        className={`w-6 text-[10px] font-mono font-bold shrink-0 ${rank <= 3 ? 'text-purple-600' : 'text-slate-400'}`}
      >
        {rank}
      </span>
      <span className="flex-1 min-w-0 text-[11px] font-extrabold text-slate-800 group-hover:text-purple-700 truncate">
        {hit.name}
      </span>
      <EvidenceBadge streak={hit.streak} badge={hit.evidence} size="sm" />
      <span className={`w-[74px] text-right text-[11px] font-mono font-extrabold shrink-0 ${flowTextClass(hit.net_main)}`}>
        {fmtYi(hit.net_main)}
      </span>
    </span>
    <span className="w-full flex items-center gap-2 pl-8 text-[10px] font-mono text-slate-400 min-w-0">
      <span>净额比 {fmtRatio(hit.net_ratio)}</span>
      <span>广度 {hit.breadth === null || hit.breadth === undefined ? '—' : fmtRatio(hit.breadth, 0)}</span>
      <span>{fmtCount(hit.members)} 只</span>
    </span>
  </button>
);

export interface EvidenceHitListProps {
  /** 页面快照日期（ISO yyyy-mm-dd）；undefined=最新。命中榜随快照日回溯。 */
  date?: string;
  /** 当前板块体系：≠行业二级时显式披露（证据只在行业二级测量，命中榜永远是二级口径）。 */
  sectorSet: FlowSectorSet;
  /** 命中行点击 → 联动桑基与板块明细（父级负责切到行业二级体系）。 */
  onSelectSector: (name: string) => void;
  /** 档位（父级持有，与信号档案行高亮联动）；null=按产物默认档。 */
  level: number | null;
  onLevelChange: (lv: number) => void;
}

export const EvidenceHitList: React.FC<EvidenceHitListProps> = ({
  date,
  sectorSet,
  onSelectSector,
  level,
  onLevelChange,
}) => {
  const [doc, setDoc] = useState<FlowEvidenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const s = ++seq.current;
    setError(null);
    getFlowEvidence({ date })
      .then((d) => {
        if (s === seq.current) setDoc(d);
      })
      .catch((e: unknown) => {
        if (s !== seq.current) return;
        setDoc(null);
        setError(e instanceof MoneyFlowError ? `命中榜取数失败：${e.message}` : '命中榜取数失败，请稍后重试');
      });
  }, [date]);

  const levels = streakLevels(doc?.badge_levels);
  const activeLevel = level !== null && levels.includes(level) ? level : defaultLevel(levels);
  const hits = doc?.hits ?? [];
  const shownHits = filterHits(hits, activeLevel);
  const counts = hitCounts(hits, levels);
  const hitsRetro = !!doc?.requested_date && doc.requested_date !== doc.hits_trade_date;

  return (
    <div className="flex flex-col gap-2 min-w-0">
      {/* ── 头部一行：当前档位口径 + 产物日（窗口主标题与切换器由父级提供） ── */}
      <div className="flex flex-wrap items-center justify-between gap-2 min-h-[18px]">
        <span className="text-[10px] font-bold text-slate-500 flex items-center gap-1.5">
          <History className="w-3 h-3 text-purple-500" />
          连续净流入 ≥ {activeLevel} 日 · 按（连续天数 → 净额）排序
        </span>
        {doc?.hits_trade_date && doc?.available && (
          <span className="text-[10px] font-mono text-slate-400">
            产物日 {fmtRawDate(doc.hits_trade_date)}
            {hitsRetro ? '（快照日回溯）' : ''}
          </span>
        )}
      </div>

      {/* ── 体系错位披露：证据只在行业二级测量，命中榜永远是二级口径 ── */}
      {sectorSet !== 'industry2' && (
        <div className="flex items-start gap-1.5 px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500 leading-snug">
          <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            连续净流入证据仅按「行业二级」测量——当前体系为「{SET_LABEL[sectorSet]}」：
            命中榜与「连N」均为行业二级口径，点击任意命中行将切回行业二级联动。
          </span>
        </div>
      )}

      {/* ── 档位标签（至少连续 N 日；计数随档位联动） ── */}
      {doc?.available && (
        <div className="flex items-center gap-1 bg-slate-100/90 border border-slate-200/60 p-0.5 rounded-full w-fit">
          {levels.map((lv, i) => {
            const active = lv === activeLevel;
            return (
              <button
                key={lv}
                onClick={() => onLevelChange(lv)}
                title={`连续净流入至少 ${lv} 日的板块（含更高档）`}
                className={`px-2.5 py-1 rounded-full text-[11px] font-extrabold transition-all duration-200 cursor-pointer whitespace-nowrap ${
                  active
                    ? 'bg-purple-600 text-white shadow-sm shadow-purple-600/20'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                }`}
              >
                ≥ {lv} 日
                <span className={`ml-1 font-mono text-[10px] ${active ? 'text-purple-100' : 'text-slate-400'}`}>
                  {counts[i]}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* ── 状态面：加载 / 失败 / 产物缺失 ── */}
      {!doc && !error && (
        <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-400">
          命中榜加载中…
        </div>
      )}
      {error && (
        <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
          {error}
        </div>
      )}
      {doc && !doc.available && (
        <div className="flex items-start gap-1.5 px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500 leading-snug">
          <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>延续证据产物尚未生成（离线构建任务未运行）——连续净流入与条件超额不可用；其余面板不受影响。</span>
        </div>
      )}

      {doc?.available && doc.hits_state === 'stale' && (
        <div className="flex items-start gap-1.5 px-3 py-1.5 rounded-xl border bg-amber-50/90 text-amber-700 border-amber-200/70 text-[11px] font-bold leading-snug">
          <Info className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            命中榜产物停留在 {fmtRawDate(doc.trade_date_raw ?? doc.trade_date)}，落后于最新行情——本次不展示命中榜（陈旧 streak 不参与展示）
          </span>
        </div>
      )}
      {doc?.available && doc.hits_state === 'no_rows' && (
        <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
          所选日期没有日面板数据（非交易日或超出样本窗），命中榜不可用。
        </div>
      )}

      {/* ── 列表 ── */}
      {doc?.available && doc.hits_state === 'ok' && hits.length === 0 && (
        <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
          榜单日无连续净流入达标的板块。
        </div>
      )}
      {doc?.available && doc.hits_state === 'ok' && hits.length > 0 && (
        <>
          {/* 列表列头（两行制行内的首行对齐：名 + 徽章 + 净额；净额比/广度/成分在行二行） */}
          <div className="flex items-center gap-2 px-2.5 text-[9px] font-bold text-slate-400 select-none">
            <span className="w-6">#</span>
            <span className="flex-1">板块 · 连N</span>
            <span className="w-[74px] text-right">净额</span>
          </div>
          <div className="flex flex-col gap-1 overflow-y-auto custom-scrollbar pr-0.5" style={{ maxHeight: LIST_MAX_HEIGHT }}>
            {shownHits.length === 0 && (
              <div className="px-3 py-2 rounded-xl bg-slate-50 border border-slate-200/70 text-[11px] text-slate-500">
                该档位当前无命中（切换更低档查看）。
              </div>
            )}
            {shownHits.map((h, i) => (
              <HitRow key={h.name} hit={h} rank={i + 1} onClick={() => onSelectSector(h.name)} />
            ))}
          </div>
        </>
      )}
    </div>
  );
};
