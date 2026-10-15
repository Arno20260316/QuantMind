/**
 * 机会雷达面板 —— 六柱因子复合评分三榜（机会个股 / 机会行业 / 风险警示）
 *
 * 口径纪律（详见 docs/港股市场分析_专业化升级_细案_20261015.md 第四·五节）：
 * - 本面板是**研究评分卡（展示面）**：只说位置（靠前/居中），不出现买入/卖出语
 * - 综合分 = 六柱（动量/趋势/南向/筹码/流动性/风险）全市场横截面分位的加权合成，
 *   再在候选池内取截面分位（0–100）；**柱缺失显示 —，绝不显示成 0**
 * - 因子选择经 IC + 五分位尾部差双过闸（脚本 hk_opportunity_ic_check.py），
 *   口径元信息（门槛/权重/剔除因子/数据日期）由 /opportunity/summary 如实披露
 * - 行业榜 = 成分机会分中位数（成分 ≥8 只）；点行业行 → 右侧个股榜按行业过滤
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Target, Building2, AlertTriangle, Info, X, Loader2, Crosshair } from 'lucide-react';
import { Tooltip } from 'antd';
import {
  getOpportunityStocks,
  getOpportunitySectors,
  getOpportunitySummary,
  getRiskWatch,
} from '../services/api';
import type {
  HkOpportunitySector,
  HkOpportunityStock,
  HkOpportunitySummary,
  HkPillarId,
  HkPillarScores,
  HkRiskWatchItem,
} from '../types';
import { EmptyHint, PctText, SectionCard, fmtInt } from './shared/ui';

const PILLARS: { id: HkPillarId; label: string; color: string }[] = [
  { id: 'mom', label: '动量', color: '#f59e0b' },
  { id: 'trend', label: '趋势位置', color: '#8b5cf6' },
  { id: 'south', label: '南向', color: '#ec4899' },
  { id: 'ccass', label: '筹码', color: '#06b6d4' },
  { id: 'liq', label: '流动性', color: '#3b82f6' },
  { id: 'risk', label: '风险结构', color: '#10b981' },
];

const RISK_REASON_STYLE: Record<string, string> = {
  近5日弱势: 'bg-green-50 text-green-700 border-green-100',
  波动抬升: 'bg-amber-50 text-amber-700 border-amber-100',
  南向减持: 'bg-pink-50 text-pink-700 border-pink-100',
  筹码面弱: 'bg-cyan-50 text-cyan-700 border-cyan-100',
  成交额居前: 'bg-slate-50 text-slate-600 border-slate-200',
};

/** 六柱 mini 条（缺失柱画灰色空槽 + title 显示 —，不冒充 0） */
const PillarBars: React.FC<{ pillars: HkPillarScores }> = ({ pillars }) => (
  <div className="flex items-end gap-[3px] h-6 w-[92px]" aria-label="六柱因子分">
    {PILLARS.map((p) => {
      const v = pillars[p.id];
      const missing = v === null || v === undefined;
      return (
        <div
          key={p.id}
          className="flex-1 flex flex-col items-center justify-end h-full"
          title={`${p.label}: ${missing ? '—（无数据）' : v.toFixed(0)}`}
        >
          <div
            className="w-full rounded-t-[2px]"
            style={{
              height: missing ? '12%' : `${Math.max(12, v)}%`,
              backgroundColor: missing ? '#cbd5e1' : p.color,
            }}
          />
        </div>
      );
    })}
  </div>
);

interface HkOpportunityPanelProps {
  /** 点击个股（行 / 行业榜龙头 / 风险榜）→ 打开个股终端浮窗 */
  onPick?: (symbol: string) => void;
}

export const HkOpportunityPanel: React.FC<HkOpportunityPanelProps> = ({ onPick }) => {
  const [stocks, setStocks] = useState<HkOpportunityStock[]>([]);
  const [sectors, setSectors] = useState<HkOpportunitySector[]>([]);
  const [risks, setRisks] = useState<HkRiskWatchItem[]>([]);
  const [summary, setSummary] = useState<HkOpportunitySummary | null>(null);
  const [universe, setUniverse] = useState<{ eligible: number; scored: number } | null>(null);
  const [tradeDate, setTradeDate] = useState('');
  const [sectorFilter, setSectorFilter] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([
      getOpportunityStocks(30),
      getOpportunitySectors(12, 8),
      getRiskWatch(20),
      getOpportunitySummary(),
    ])
      .then(([st, se, rk, sm]) => {
        if (!alive) return;
        setStocks(st.items);
        setUniverse(st.universe);
        setTradeDate(st.trade_date || '');
        setSectors(se.items);
        setRisks(rk.items);
        setSummary(sm);
      })
      .catch(() => undefined)
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  // 行业过滤：点行业行 → 个股榜收窄到该行业（后端 sector 参数）
  useEffect(() => {
    if (!sectorFilter) return;
    let alive = true;
    setListLoading(true);
    getOpportunityStocks(30, sectorFilter)
      .then((st) => {
        if (!alive) return;
        setStocks(st.items);
        setUniverse(st.universe);
      })
      .catch(() => undefined)
      .finally(() => alive && setListLoading(false));
    return () => {
      alive = false;
    };
  }, [sectorFilter]);

  const clearFilter = () => {
    setSectorFilter(null);
    setListLoading(true);
    getOpportunityStocks(30)
      .then((st) => {
        setStocks(st.items);
        setUniverse(st.universe);
      })
      .catch(() => undefined)
      .finally(() => setListLoading(false));
  };

  const weightTooltip = useMemo(() => {
    if (!summary) return null;
    const w = summary.pillar_weights;
    return (
      <div className="text-[10px] leading-relaxed">
        <div className="font-bold">六柱权重（IC+尾部双过闸定稿）</div>
        {PILLARS.map((p) => (
          <div key={p.id} className="flex justify-between gap-3">
            <span>{p.label}</span>
            <span className="font-mono">{((w[p.id] ?? 0) * 100).toFixed(0)}%</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t border-slate-200">
          剔除：{Object.keys(summary.excluded_factors).join('、')}（尾部与 IC 反向）
        </div>
        <div>数据：{summary.data_as_of.l1_factors}（T-1 日频）</div>
      </div>
    );
  }, [summary]);

  return (
    <div className="flex flex-col gap-2.5">
      {/* 口径条：非模型预测 + 分母透明度（细案纪律：展示面 / 诚实口径） */}
      <div className="rounded-2xl bg-gradient-to-r from-violet-50/90 via-white to-purple-50/70 border border-purple-100/80 px-4 py-2 flex items-center gap-3 flex-wrap">
        <span className="flex items-center gap-1.5 text-[11px] font-extrabold text-purple-800">
          <Target className="w-3.5 h-3.5" />
          机会雷达 · 六柱因子复合评分
        </span>
        <span className="text-[10px] text-slate-500">
          研究评分卡（非模型预测、非交易指令）· 全市场横截面分位合成 0–100
        </span>
        {summary && (
          <span className="text-[10px] font-mono text-slate-400">
            候选池 {summary.eligible_total}/{summary.listed_total} 只（门槛：价格≥
            {summary.thresholds.min_price_hkd} 港元 · 成交额≥
            {summary.thresholds.min_amount_hkd / 1e8} 亿 · 上市≥120 交易日）
          </span>
        )}
        {summary && (
          <Tooltip title={weightTooltip} placement="bottomRight">
            <span className="flex items-center gap-1 text-[10px] font-bold text-purple-600 cursor-help">
              <Info className="w-3 h-3" />
              评分口径
            </span>
          </Tooltip>
        )}
        {tradeDate && (
          <span className="ml-auto text-[10px] font-mono text-slate-400">数据 {tradeDate}</span>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-2.5 items-start">
        {/* 机会个股榜（占 2/3） */}
        <SectionCard
          className="xl:col-span-2"
          title={
            <span className="flex items-center gap-1.5">
              <Crosshair className="w-3.5 h-3.5 text-purple-600" />
              机会个股榜（综合分降序）
              <span className="text-[10px] font-normal text-slate-400">
                柱条高度 = 该柱全市场分位；灰槽 = 无数据（—）
              </span>
            </span>
          }
          extra={
            <div className="flex items-center gap-2">
              {sectorFilter && (
                <button
                  type="button"
                  onClick={clearFilter}
                  className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 text-[10px] font-extrabold hover:bg-purple-200 transition-colors"
                  title="清除行业过滤"
                >
                  {sectorFilter}
                  <X className="w-3 h-3" />
                </button>
              )}
              {universe && (
                <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
                  已评分 {universe.scored} 只
                </span>
              )}
            </div>
          }
        >
          {loading && !stocks.length ? (
            <EmptyHint loading />
          ) : stocks.length ? (
            <div className="relative flex flex-col max-h-[640px] overflow-y-auto pr-0.5">
              {listLoading && (
                <div className="absolute inset-0 z-10 bg-white/60 flex items-center justify-center">
                  <Loader2 className="w-4 h-4 text-purple-500 animate-spin" />
                </div>
              )}
              <div className="grid grid-cols-[22px_minmax(0,1fr)_96px_56px_60px_46px] gap-2 px-1 pb-1.5 text-[9px] font-extrabold text-slate-400 border-b border-slate-100 sticky top-0 bg-white/95 backdrop-blur z-[5]">
                <span>#</span>
                <span>标的 / 触发标签</span>
                <span>六柱分位</span>
                <span className="text-right">涨跌</span>
                <span className="text-right">成交额</span>
                <span className="text-right">综合分</span>
              </div>
              {stocks.map((it, i) => (
                <div
                  key={it.symbol}
                  onClick={() => onPick?.(it.symbol)}
                  title={`打开 ${it.symbol} 个股终端`}
                  className="grid grid-cols-[22px_minmax(0,1fr)_96px_56px_60px_46px] gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 items-center cursor-pointer hover:bg-purple-50/60 transition-colors"
                >
                  <span
                    className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold ${
                      i < 3 ? 'bg-purple-600 text-white' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-bold text-slate-800 truncate">{it.name}</span>
                    <span className="text-[9px] font-mono text-slate-400">{it.symbol}</span>
                    {it.sector && (
                      <span className="text-[9px] text-slate-400 bg-slate-50 border border-slate-100 rounded px-1">
                        {it.sector}
                        {it.rank_in_sector && it.rank_in_sector <= 3
                          ? ` #${it.rank_in_sector}`
                          : ''}
                      </span>
                    )}
                    {it.tags.slice(0, 3).map((t) => (
                      <span
                        key={t}
                        className="text-[9px] font-bold text-purple-600 bg-purple-50 border border-purple-100 rounded px-1 whitespace-nowrap"
                      >
                        {t}
                      </span>
                    ))}
                    {it.sb_chg_d5_pp !== null && it.sb_chg_d5_pp !== undefined && it.sb_chg_d5_pp !== 0 && (
                      <span
                        className={`text-[9px] font-mono ${
                          it.sb_chg_d5_pp > 0 ? 'text-red-500' : 'text-green-600'
                        }`}
                        title="南向 5 日持股占比变化（百分点）"
                      >
                        南向{it.sb_chg_d5_pp > 0 ? '+' : ''}
                        {it.sb_chg_d5_pp.toFixed(2)}pp
                      </span>
                    )}
                  </div>
                  <PillarBars pillars={it.pillars} />
                  <span className="text-right">
                    <PctText value={it.pct_change} className="text-[10px]" />
                  </span>
                  <span className="text-right text-[10px] font-mono text-slate-500">
                    {it.amount_yi >= 1 ? `${it.amount_yi.toFixed(1)}亿` : `${fmtInt(it.amount_yi)}亿`}
                  </span>
                  <span className="text-right text-xs font-extrabold font-mono text-purple-700">
                    {it.opp_score.toFixed(1)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyHint text={sectorFilter ? '该行业暂无达标标的' : '机会榜暂无可评分标的'} />
          )}
        </SectionCard>

        {/* 机会行业榜 + 风险警示榜 */}
        <div className="xl:col-span-1 flex flex-col gap-2.5">
          <SectionCard
            title={
              <span className="flex items-center gap-1.5">
                <Building2 className="w-3.5 h-3.5 text-indigo-600" />
                机会行业榜（成分中位分）
              </span>
            }
            extra={<span className="text-[9px] text-slate-400">点行过滤个股榜</span>}
          >
            {loading && !sectors.length ? (
              <EmptyHint loading />
            ) : sectors.length ? (
              <div className="flex flex-col gap-1.5 max-h-[360px] overflow-y-auto">
                {sectors.map((s) => (
                  <div
                    key={s.name}
                    onClick={() => setSectorFilter(s.name)}
                    title={`只看「${s.name}」的个股机会榜`}
                    className={`rounded-xl border px-2.5 py-1.5 cursor-pointer transition-colors ${
                      sectorFilter === s.name
                        ? 'border-purple-300 bg-purple-50/70'
                        : 'border-slate-100 hover:border-purple-200 hover:bg-purple-50/40'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="w-16 text-[11px] font-extrabold text-slate-800 truncate flex-shrink-0">
                        {s.name}
                      </span>
                      <span className="text-sm font-extrabold font-mono text-purple-700 w-11 text-right flex-shrink-0">
                        {s.opp_score_median.toFixed(1)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden" title={`机会宽度：成分分≥70 占 ${(s.opportunity_breadth * 100).toFixed(0)}%`}>
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-violet-400 to-purple-600"
                            style={{ width: `${Math.max(3, s.opportunity_breadth * 100)}%` }}
                          />
                        </div>
                      </div>
                      <span className="text-[9px] font-mono text-slate-400 w-14 text-right flex-shrink-0">
                        宽 {(s.opportunity_breadth * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 mt-1 min-w-0">
                      <span className="text-[9px] text-slate-400 flex-shrink-0">
                        {s.stock_count} 只 · 南向5日中位{' '}
                        {s.south_chg_d5_median_pp === null
                          ? '—'
                          : `${s.south_chg_d5_median_pp >= 0 ? '+' : ''}${s.south_chg_d5_median_pp.toFixed(2)}pp`}
                      </span>
                      <span className="flex items-center gap-1 min-w-0 overflow-hidden">
                        {s.top_stocks.map((ts) => (
                          <button
                            key={ts.symbol}
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onPick?.(ts.symbol);
                            }}
                            className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-white border border-purple-100 text-[9px] font-bold text-slate-600 hover:border-purple-300 hover:text-purple-700 transition-colors whitespace-nowrap"
                            title={`${ts.symbol} · 综合分 ${ts.opp_score.toFixed(1)} · 打开个股终端`}
                          >
                            {ts.name}
                            <span className="font-mono text-purple-600">{ts.opp_score.toFixed(0)}</span>
                          </button>
                        ))}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyHint text="暂无达标行业（成分 ≥8 只）" />
            )}
          </SectionCard>

          <SectionCard
            title={
              <span className="flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                风险警示榜（弱势 + 波动 + 撤离线索）
              </span>
            }
            extra={<span className="text-[9px] text-slate-400">对称口径 100−综合分</span>}
          >
            {loading && !risks.length ? (
              <EmptyHint loading />
            ) : risks.length ? (
              <div className="flex flex-col max-h-[320px] overflow-y-auto">
                {risks.map((it, i) => (
                  <div
                    key={it.symbol}
                    onClick={() => onPick?.(it.symbol)}
                    title={`打开 ${it.symbol} 个股终端`}
                    className="flex items-center gap-2 px-1 py-1.5 border-b border-slate-50 last:border-0 cursor-pointer hover:bg-amber-50/50 transition-colors"
                  >
                    <span className="w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold bg-slate-100 text-slate-500 flex-shrink-0">
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-bold text-slate-800 truncate">{it.name}</span>
                        <span className="text-[9px] font-mono text-slate-400">{it.symbol}</span>
                        {it.sector && (
                          <span className="text-[9px] text-slate-400">{it.sector}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                        {it.reasons.map((r) => (
                          <span
                            key={r}
                            className={`text-[9px] font-bold border rounded px-1 ${
                              RISK_REASON_STYLE[r] || 'bg-slate-50 text-slate-600 border-slate-200'
                            }`}
                          >
                            {r}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="flex flex-col items-end flex-shrink-0">
                      <span className="text-xs font-extrabold font-mono text-amber-600">
                        {it.risk_score.toFixed(1)}
                      </span>
                      <PctText value={it.pct_change} className="text-[9px]" />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyHint text="暂无风险警示标的" />
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
};
