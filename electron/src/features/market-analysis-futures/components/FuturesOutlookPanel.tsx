/** 期货市场分析 · Tab3 前瞻视角
 *
 * 五因子截面分位等权合成（carry/基差/库存/动量/季节性）+ 位置词三档
 * （靠前/居中/靠后）+ 口径卡；行点击右侧出品种画像（默认选中第一行）。
 *
 * 展示纪律：只说位置与分位数值，不给买卖词汇；缺失一律「—」；
 * member_net 席位净额为纯事实列（样本短、不入合成）。
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Telescope, ChevronDown, ChevronUp, RotateCcw } from 'lucide-react';
import { SectionCard, EmptyHint, DateBadge } from '../../market-analysis-shared/ui';
import { getOutlook } from '../services/api';
import type { FuturesOutlook, OutlookFactorKey, OutlookRow } from '../types';
import { Badge, PctBar, TIER_META, fmt, num } from '../format';
import { FuturesVarietyProfilePanel } from './FuturesVarietyProfilePanel';

/** 入合成因子展示顺序（与后端 COMPOSITE_FACTORS 一致） */
const COMPOSITE_KEYS: OutlookFactorKey[] = ['carry', 'basis', 'inv_chg', 'mom_20d', 'seasonality'];
const FACT_KEYS: OutlookFactorKey[] = [...COMPOSITE_KEYS, 'member_net'];

const REF_LABEL: Record<string, string> = {
  cs: '当日截面分位',
  ts250: '自身 250 日时序分位',
  ts_inv: '自身全史分位（方向已取反）',
  fact: '纯事实展示（不参与合成）',
};
const DIR_LABEL: Record<string, string> = {
  positive: '数值越高 → 位置越靠前',
  negative: '数值越低 → 位置越靠前',
  none: '不入合成',
};

export const FuturesOutlookPanel: React.FC<{
  sector: string | null;
  onSectorChange: (sector: string | null) => void;
  reloadKey: number;
}> = ({ sector, onSectorChange, reloadKey }) => {
  const [data, setData] = useState<FuturesOutlook | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [showMeta, setShowMeta] = useState(false);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getOutlook(undefined, 120)
      .then((d) => {
        if (alive) {
          setData(d);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey, retryTick]);

  const allRows = data?.rows ?? [];
  const rows = useMemo(
    () => (sector ? allRows.filter((r) => r.sector === sector) : allRows),
    [allRows, sector],
  );

  // 板块清单（含中文名）：从全量行去重
  const sectorOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const r of allRows) if (!seen.has(r.sector)) seen.set(r.sector, r.sector_name);
    return Array.from(seen, ([id, name]) => ({ id, name })).sort((a, b) => a.id.localeCompare(b.id));
  }, [allRows]);

  // 默认选中第一行（用户口径：右侧画像不空置等待点击）
  useEffect(() => {
    if (rows.length === 0) {
      setSelected(null);
      return;
    }
    if (!selected || !rows.some((r) => r.symbol === selected)) {
      setSelected(rows[0].symbol);
    }
  }, [rows, selected]);

  const sectorName = sector ? (sectorOptions.find((s) => s.id === sector)?.name ?? sector) : '全市场';

  return (
    <div className="flex flex-col gap-2.5">
      {/* 摘要 + 口径卡 */}
      <SectionCard
        className="!gap-2"
        title={
          <span className="flex items-center gap-1.5">
            <Telescope className="w-3.5 h-3.5 text-amber-600" />
            前瞻视角 · 五因子截面合成
            <span className="text-[9px] font-normal text-slate-400">
              分位视角，非收益承诺（口径卡可展开）
            </span>
          </span>
        }
        extra={
          <div className="flex items-center gap-2">
            <DateBadge label="数据日期" date={data?.as_of} />
            <button
              onClick={() => setShowMeta((v) => !v)}
              className="flex items-center gap-1 px-2.5 py-1 rounded-full border border-amber-200 bg-white text-[11px] font-extrabold text-amber-700 hover:border-amber-300 transition-colors cursor-pointer"
            >
              口径说明
              {showMeta ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
          </div>
        }
      >
        {data && (
          <div className="rounded-xl border border-amber-100/80 bg-gradient-to-b from-amber-50/50 to-white px-2.5 py-1.5 flex items-center gap-x-4 gap-y-1 flex-wrap text-[10px]">
            <span className="flex items-baseline gap-1">
              <span className="font-bold text-slate-400">入板品种</span>
              <span className="font-extrabold font-mono text-slate-800">{data.universe_size}</span>
              <span className="text-slate-400">/ 有效覆盖</span>
              <span className="font-extrabold font-mono text-slate-700">{data.covered_size}</span>
            </span>
            <span className="flex items-baseline gap-1" title="三档阈值：≥q67 靠前，≤q33 靠后">
              <span className="font-bold text-slate-400">三档阈值</span>
              <span className="font-mono text-slate-600">
                q33 {fmt(data.composite_quantiles.q33, 1)} · q67 {fmt(data.composite_quantiles.q67, 1)}
              </span>
            </span>
            <span className="flex items-center gap-1.5 flex-wrap">
              <span className="font-bold text-slate-400">合成权重</span>
              {COMPOSITE_KEYS.map((k) => (
                <span key={k} className="font-mono text-slate-600">
                  {data.factor_meta[k]?.name ?? k} {data.weights[k] ?? 0}%
                </span>
              ))}
            </span>
            {data.excluded_note && (
              <span className="text-amber-600 font-bold">{data.excluded_note}</span>
            )}
          </div>
        )}

        {showMeta && data && (
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/60 p-2.5 flex flex-col gap-1.5">
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-1.5">
              {FACT_KEYS.map((k) => {
                const m = data.factor_meta[k];
                if (!m) return null;
                return (
                  <div key={k} className="rounded-lg bg-white border border-slate-100 px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] font-extrabold text-slate-800">{m.name}</span>
                      <span className="text-[9px] font-mono text-slate-400">{k}</span>
                      <span className="ml-auto text-[9px] font-bold text-slate-400">
                        {REF_LABEL[m.ref] ?? m.ref}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-500 leading-snug mt-0.5">{m.definition}</div>
                    <div className="text-[9px] text-slate-400 leading-snug mt-0.5">
                      方向：{DIR_LABEL[m.direction] ?? m.direction} · 单位：{m.unit}
                    </div>
                    <div className="text-[9px] text-slate-400 leading-snug">源：{m.source}</div>
                    {m.note && <div className="text-[9px] text-amber-600 leading-snug">{m.note}</div>}
                  </div>
                );
              })}
            </div>
            <div className="text-[9px] text-slate-400 leading-snug">
              carry 与基差溢价是商品期货文献广泛验证的截面收益相关因子；本板为历史统计口径的分位视角，
              不构成收益承诺与投资建议。分位参照系各不相同（见上），跨因子数值不直接可比。
            </div>
          </div>
        )}

        {!data && <EmptyHint loading={loading} text="暂无数据" />}
      </SectionCard>

      {/* 板块过滤 chips */}
      {sectorOptions.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => onSectorChange(null)}
            className={`px-2.5 py-1 rounded-full text-[11px] font-extrabold border transition-colors cursor-pointer ${
              sector === null
                ? 'bg-amber-600 text-white border-amber-600'
                : 'bg-white text-slate-500 border-slate-200 hover:border-amber-300'
            }`}
          >
            全市场
          </button>
          {sectorOptions.map((s) => (
            <button
              key={s.id}
              onClick={() => onSectorChange(sector === s.id ? null : s.id)}
              className={`px-2.5 py-1 rounded-full text-[11px] font-extrabold border transition-colors cursor-pointer ${
                sector === s.id
                  ? 'bg-amber-600 text-white border-amber-600'
                  : 'bg-white text-slate-500 border-slate-200 hover:border-amber-300'
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      {failed && !data ? (
        <SectionCard title="前瞻榜" icon={<Telescope className="w-3.5 h-3.5 text-amber-600" />}>
          <div className="flex flex-col items-center gap-2 py-8">
            <span className="text-xs text-slate-400">加载失败</span>
            <button
              onClick={() => setRetryTick((v) => v + 1)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-amber-600 text-white text-[11px] font-extrabold hover:bg-amber-700 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3 h-3" /> 重试
            </button>
          </div>
        </SectionCard>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-2.5 items-start">
          <SectionCard
            className="xl:col-span-2 !gap-2"
            title={
              <span className="flex items-center gap-1.5">
                前瞻榜
                <span className="text-[9px] font-normal text-slate-400">
                  {sectorName} · 按合成分降序 · 点击看画像
                </span>
              </span>
            }
            extra={<span className="text-[10px] font-bold text-slate-400">{rows.length} 品种</span>}
          >
            {rows.length === 0 ? (
              <EmptyHint loading={loading} text="该板块暂无入板品种" />
            ) : (
              <div className="flex flex-col max-h-[720px] overflow-y-auto pr-0.5">
                {rows.map((r, i) => (
                  <OutlookRankRow
                    key={r.symbol}
                    rank={i + 1}
                    row={r}
                    factorNames={data?.factor_meta}
                    active={r.symbol === selected}
                    onClick={() => setSelected(r.symbol)}
                  />
                ))}
              </div>
            )}
          </SectionCard>

          <div className="xl:col-span-3">
            {selected ? (
              <FuturesVarietyProfilePanel symbol={selected} reloadKey={reloadKey} />
            ) : (
              <SectionCard title="品种画像">
                <EmptyHint loading={loading} text="点击左侧品种查看画像" />
              </SectionCard>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

function OutlookRankRow({
  rank,
  row,
  factorNames,
  active,
  onClick,
}: {
  rank: number;
  row: OutlookRow;
  factorNames: FuturesOutlook['factor_meta'] | undefined;
  active: boolean;
  onClick: () => void;
}) {
  const tier = row.tier ? TIER_META[row.tier] : null;
  return (
    <button
      onClick={onClick}
      title={`${row.sector_name} · 有效因子 ${row.coverage} 个 · 席位净额为短样本事实列`}
      className={`flex items-center gap-2 px-1.5 py-1.5 rounded-lg border-b border-slate-50 last:border-0 text-left transition-colors cursor-pointer ${
        active ? 'bg-amber-50/80' : 'hover:bg-slate-50/70'
      }`}
    >
      <span
        className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold flex-shrink-0 ${
          rank <= 3 ? 'bg-amber-600 text-white' : 'bg-slate-100 text-slate-500'
        }`}
      >
        {rank}
      </span>
      <span className="w-[72px] flex-shrink-0 flex flex-col leading-tight">
        <span className="text-xs font-bold text-slate-800 truncate">{row.name}</span>
        <span className="text-[9px] font-mono text-slate-400 truncate">
          {row.symbol} · {row.sector_name}
        </span>
      </span>
      {tier && <Badge text={tier.label} cls={tier.cls} />}
      <span className="flex items-center gap-[3px]" title="五因子分位（carry/基差/库存/动量/季节性）">
        {COMPOSITE_KEYS.map((k) => {
          const pct = num(row.factors[k]?.pct);
          return (
            <PctBar
              key={k}
              pct={pct}
              width={30}
              title={`${factorNames?.[k]?.name ?? k}：分位 ${
                pct === null ? '—' : pct.toFixed(0)
              }（${factorNames?.[k]?.ref ?? ''}）`}
            />
          );
        })}
      </span>
      <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
        <span
          className="text-[12px] font-extrabold font-mono text-slate-800 tabular-nums"
          title="可用因子分位等权均值（0-100）"
        >
          {row.composite === null ? '—' : row.composite.toFixed(1)}
        </span>
      </span>
    </button>
  );
}
