/**
 * 板块标签融合条（资金流全景工具栏与桑基之间；「标签双向查询」并入资金流全景）。
 *
 * 统一搜索框的解析优先级（Enter 一键提交，候选 chips 可逐个点开）：
 * 1. 个股代码（纯 6 位 / SH|SZ|BJ 前缀式 / 后缀式）→ normalizeStockCode → 打开个股卡；
 * 2. 当前体系板块名精确命中 → 直接联动（保持当前体系）；
 * 3. 否则走 /tags/matches 出候选 chips：受支持类型（行业一级/二级/概念）一键切换
 *    联动；地区/风格等类型**如实列出但不可联动**（资金流体系仅此三种，不假装能切）。
 *
 * 热门 chips 只列受支持三类型、可按类型过滤；统计四数压成一行 meta。
 * 标签统计/候选取数失败只隐去对应块（搜索代码与完整板块名仍可用），
 * 绝不拦资金流主路径。
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Flame, Search, X } from 'lucide-react';
import { normalizeStockCode } from '../../../utils/portfolioUtils';
import {
  getTagMatches,
  getTagStats,
  type FlowSectorSet,
  type FlowTagMatch,
  type FlowTagStats,
} from '../services/moneyFlowService';

/** 板块类型字符串（后端 tags 口径）→ 资金流体系键；仅这三种可联动。 */
const SET_OF_TYPE: Record<string, FlowSectorSet> = {
  '行业板块(一级)': 'industry1',
  '行业板块(二级)': 'industry2',
  '概念板块': 'concept',
};
const SET_LABEL: Record<FlowSectorSet, string> = {
  industry1: '行业一级',
  industry2: '行业二级',
  concept: '概念',
};
const HOT_FILTERS: Array<{ id: FlowSectorSet | 'all'; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'industry1', label: '行业一级' },
  { id: 'industry2', label: '行业二级' },
  { id: 'concept', label: '概念' },
];
const HOT_MAX = 10;
const MATCH_MAX = 10;
const SEARCH_DEBOUNCE_MS = 200;

const CODE_RE = /^(SH|SZ|BJ)\d{6}$/;

export interface TagBrowserStripProps {
  /** 当前体系板块名（全景数据；为空时精确命中路径自然失效，不报错）。 */
  currentSectors: string[];
  /** 板块联动（set 为 null 时保持当前体系）。 */
  onNavigateSector: (name: string, set: string | null) => void;
  /** 代码命中 → 打开个股卡。 */
  onSelectStock: (symbol: string, name?: string) => void;
}

export const TagBrowserStrip: React.FC<TagBrowserStripProps> = ({
  currentSectors,
  onNavigateSector,
  onSelectStock,
}) => {
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<FlowTagMatch[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState(false);
  const [stats, setStats] = useState<FlowTagStats | null>(null);
  const [statsErr, setStatsErr] = useState(false);
  const [hotFilter, setHotFilter] = useState<FlowSectorSet | 'all'>('all');
  const matchSeq = useRef(0);

  // ── 标签统计（一次；失败只隐去热门区） ──
  useEffect(() => {
    let alive = true;
    getTagStats()
      .then((s) => {
        if (alive) setStats(s);
      })
      .catch(() => {
        if (alive) setStatsErr(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const code = useMemo(() => {
    const n = normalizeStockCode(query);
    return CODE_RE.test(n) ? n : null;
  }, [query]);

  // ── 候选查询（防抖；代码形态不发请求——板块名不含 6 位数字段） ──
  useEffect(() => {
    const q = query.trim();
    if (!q || code) {
      setMatches(null);
      setSearching(false);
      setSearchErr(false);
      return;
    }
    const seq = ++matchSeq.current;
    setSearching(true);
    setSearchErr(false);
    const timer = setTimeout(() => {
      getTagMatches(q)
        .then((m) => {
          if (seq === matchSeq.current) setMatches(m);
        })
        .catch(() => {
          if (seq === matchSeq.current) {
            setMatches(null);
            setSearchErr(true);
          }
        })
        .finally(() => {
          if (seq === matchSeq.current) setSearching(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, code]);

  const exactCurrent = useMemo(() => {
    const q = query.trim();
    return q ? (currentSectors.find((s) => s === q) ?? null) : null;
  }, [query, currentSectors]);

  const supportedMatches = useMemo(
    () => (matches ?? []).filter((m) => SET_OF_TYPE[m.sector_type]),
    [matches],
  );

  /** Enter 一键提交：代码 → 精确当前体系 → 首个受支持候选。 */
  const submit = () => {
    if (code) {
      onSelectStock(code);
      setQuery('');
      return;
    }
    if (exactCurrent) {
      onNavigateSector(exactCurrent, null);
      return;
    }
    const first = supportedMatches[0];
    if (first) onNavigateSector(first.name, SET_OF_TYPE[first.sector_type]);
  };

  const hotList = useMemo(() => {
    const tags = (stats?.hot_tags ?? []).filter((t) => SET_OF_TYPE[t.type]);
    const filtered = hotFilter === 'all' ? tags : tags.filter((t) => SET_OF_TYPE[t.type] === hotFilter);
    return filtered.slice(0, HOT_MAX);
  }, [stats, hotFilter]);

  const searchActive = query.trim().length > 0;

  return (
    <div className="bg-white/95 backdrop-blur-md rounded-2xl px-3.5 py-2.5 border border-purple-100/80 shadow-xs flex flex-col gap-2 min-w-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {/* 统一搜索框 */}
        <div className="flex items-center gap-1.5 bg-slate-100/90 border border-slate-200/70 rounded-full px-3 py-1.5 w-full sm:w-80 shrink-0">
          <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <input
            aria-label="统一搜索：板块、标签或股票代码"
            className="flex-1 min-w-0 bg-transparent outline-none text-xs font-bold text-slate-700 placeholder:text-slate-400 placeholder:font-normal"
            placeholder="搜索板块 / 概念 / 或输入 6 位代码"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
            }}
          />
          {searchActive && (
            <button
              aria-label="清空搜索"
              className="text-slate-300 hover:text-slate-600 shrink-0 cursor-pointer"
              onClick={() => setQuery('')}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* 热门板块（只列受支持三类型，按成分数降序，可类型过滤） */}
        <div className="flex items-center gap-2 flex-wrap min-w-0 flex-1">
          <span className="flex items-center gap-1 text-[10px] font-extrabold text-slate-400 whitespace-nowrap">
            <Flame className="w-3 h-3 text-orange-400" />
            热门
          </span>
          <div className="flex items-center bg-slate-100/90 border border-slate-200/60 p-0.5 rounded-full gap-0.5">
            {HOT_FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setHotFilter(f.id)}
                className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold transition-colors cursor-pointer whitespace-nowrap ${
                  hotFilter === f.id ? 'bg-purple-600 text-white' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 flex-wrap min-w-0">
            {hotList.map((t) => (
              <button
                key={`${t.type}-${t.name}`}
                onClick={() => onNavigateSector(t.name, SET_OF_TYPE[t.type])}
                title={`${t.type} · 成分 ${t.count} 只：点击联动资金流工作台`}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-50 text-slate-600 border border-slate-200/70 hover:bg-purple-50 hover:text-purple-700 hover:border-purple-200 transition-colors cursor-pointer whitespace-nowrap"
              >
                {t.name}
                <span className="font-mono text-slate-400">{t.count}</span>
              </button>
            ))}
            {!statsErr && hotList.length === 0 && (
              <span className="text-[10px] text-slate-400">暂无热门板块</span>
            )}
            {statsErr && <span className="text-[10px] text-slate-400">标签统计暂不可用（搜索不受影响）</span>}
          </div>
        </div>
      </div>

      {/* 统计一行 meta */}
      {stats && (
        <span className="text-[10px] font-mono text-slate-400">
          {stats.trade_date ? `基准日 ${stats.trade_date} · ` : ''}板块 {stats.total_sectors.toLocaleString()} · 标的{' '}
          {stats.total_stocks.toLocaleString()} · 人均标签 {stats.avg_tags_per_stock} · 关系{' '}
          {stats.total_relations.toLocaleString()}
        </span>
      )}

      {/* 搜索候选行 */}
      {searchActive && (
        <div className="flex items-start gap-1.5 flex-wrap pt-0.5 border-t border-slate-100">
          {code && (
            <button
              onClick={() => {
                onSelectStock(code);
                setQuery('');
              }}
              className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold bg-purple-600 text-white hover:bg-purple-500 transition-colors cursor-pointer"
              title="按 Enter 或点击打开个股资金流详情卡"
            >
              打开个股卡 <span className="font-mono">{code}</span>
            </button>
          )}
          {!code && searching && <span className="text-[10px] text-slate-400 py-0.5">搜索中…</span>}
          {!code && !searching && searchErr && (
            <span className="text-[10px] text-slate-400 py-0.5">候选查询失败——可直接输入完整板块名后回车联动</span>
          )}
          {!code &&
            !searching &&
            !searchErr &&
            matches !== null &&
            matches.length === 0 && <span className="text-[10px] text-slate-400 py-0.5">无匹配板块</span>}
          {!code &&
            !searching &&
            !searchErr &&
            matches !== null &&
            matches.length > 0 && (
              <>
                {exactCurrent && (
                  <span
                    className="text-[10px] text-emerald-700 bg-emerald-50 border border-emerald-200/70 rounded-full px-2 py-0.5 font-bold whitespace-nowrap"
                    title="与当前体系板块名精确一致，按 Enter 直接联动"
                  >
                    精准命中：{exactCurrent}
                  </span>
                )}
                {matches.slice(0, MATCH_MAX).map((m) => {
                  const set = SET_OF_TYPE[m.sector_type];
                  return set ? (
                    <button
                      key={`${m.sector_type}-${m.name}`}
                      onClick={() => onNavigateSector(m.name, set)}
                      title={`${m.sector_type}（${SET_LABEL[set]}）· 成分 ${m.count} 只：点击切换体系并联动`}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-50/70 text-purple-700 border border-purple-200/70 hover:bg-purple-100 transition-colors cursor-pointer whitespace-nowrap"
                    >
                      <span className="text-purple-400">{SET_LABEL[set]}</span>
                      {m.name}
                      <span className="font-mono text-purple-400">{m.count}</span>
                    </button>
                  ) : (
                    <span
                      key={`${m.sector_type}-${m.name}`}
                      title={`${m.sector_type}不在资金流体系（行业一级/二级/概念），不可联动`}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-50 text-slate-400 border border-slate-200/60 whitespace-nowrap cursor-help"
                    >
                      {m.name}
                      <span className="font-mono">{m.count}</span>
                    </span>
                  );
                })}
              </>
            )}
        </div>
      )}
    </div>
  );
};
