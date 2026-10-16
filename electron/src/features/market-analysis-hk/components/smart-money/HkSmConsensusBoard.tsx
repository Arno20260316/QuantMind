/** 港股聪明钱 · 共识榜（细案 §6：入柱柱分合成，紫阶；柱分位 tooltip）。
 *
 * evidence-only（零入柱）时不发布——空态如实说明「这是数据结论」，不是链路故障。
 * 缺失柱按 50 计分并随行披露覆盖（covered/n_pillars）；缺失一律「—」绝不为 0。
 */

import React from 'react';
import { Users } from 'lucide-react';
import type { HkSmConsensusItem, HkSmConsensusResponse } from '../../services/api';
import { EmptyHint, FaceCard, RankRow } from '../../../market-analysis-shared/ui';
import { categoryLabel, fmtInt, fmtNum, fmtScore, scoreCls } from '../../hkSmartMoneyVocabulary';

interface HkSmConsensusBoardProps {
  data: HkSmConsensusResponse | null;
  loading: boolean;
  error: string | null;
  onPick: (symbol: string) => void;
  /** true = 无卡壳（统一大框内的标签面） */
  bare?: boolean;
}

/** 柱名（category::variant）→ 中文展示名 */
function pillarLabel(name: string): string {
  const [cat, variant] = name.split('::');
  return variant ? `${categoryLabel(cat)}·${variant}` : name;
}

function PillarPct({ item, directions }: {
  item: HkSmConsensusItem;
  directions: Record<string, number | null>;
}) {
  const names = Object.keys(item.pct ?? {});
  const tip = names
    .map((n) => {
      const d = directions?.[n];
      return `${pillarLabel(n)}：分位 ${fmtNum(item.pct[n], 0)}${d === -1 ? '（负向已取反 100−分位）' : ''} · 值 ${fmtNum(item.val?.[n], 4)}`;
    })
    .join('\n');
  return (
    <span className="flex items-center gap-1 flex-shrink-0" title={tip || '无柱分位明细'}>
      {names.slice(0, 4).map((n) => (
        <span
          key={n}
          className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-purple-50 text-purple-600 border border-purple-100"
        >
          {fmtNum(item.pct[n], 0)}
        </span>
      ))}
      {names.length > 4 && <span className="text-[8px] text-slate-400">+{names.length - 4}</span>}
    </span>
  );
}

export const HkSmConsensusBoard: React.FC<HkSmConsensusBoardProps> = ({
  data,
  loading,
  error,
  onPick,
  bare = false,
}) => (
  <FaceCard
    bare={bare}
    icon={<Users className="w-3.5 h-3.5 text-purple-600" />}
    title="共识榜"
    extra={
      data && !data.evidence_only ? (
        <span className="text-[9px] font-mono text-slate-400 whitespace-nowrap">
          共 {fmtInt(data.total)} 只 · 入柱 {fmtInt(data.adopted?.length ?? null)} 柱
        </span>
      ) : undefined
    }
  >
    {error && (
      <div className="rounded-xl bg-amber-50/80 border border-amber-200 px-3 py-1.5 text-[10px] text-amber-800 font-bold">
        共识榜数据不可用：{error}
      </div>
    )}
    {!data && !error && <EmptyHint loading={loading} text="共识榜不可用" />}
    {data?.evidence_only && (
      <div className="flex flex-col gap-1.5">
        <EmptyHint text="零类型通过 h10 双过闸（evidence-only）——共识榜不发布" />
        <p className="text-[10px] text-slate-500 leading-relaxed px-1">
          本次产物无信号通过入柱闸门，「多类型行为共识」不成立属数据结论（不是链路故障）；
          类型效力矩阵与席位榜照常交付。矩阵中任一变体日后过闸，本榜自动出现。
        </p>
      </div>
    )}
    {data && !data.evidence_only && (
      <div className="max-h-[430px] overflow-y-auto">
        {data.items.length === 0 && <EmptyHint text="当前无覆盖达标的标的" />}
        {data.items.map((it, i) => (
          <RankRow
            key={it.symbol}
            rank={i + 1}
            name={it.name || it.symbol}
            nameSub={it.symbol}
            onClick={() => onPick(it.symbol)}
            title="查看个股档案时间线"
            main={
              <span className="flex items-center gap-1.5 min-w-0">
                <span className={`text-sm font-extrabold font-mono ${scoreCls(it.score)}`}>
                  {fmtScore(it.score)}
                </span>
                <span
                  className="px-1.5 py-px rounded-full text-[8px] font-extrabold bg-slate-100 text-slate-500 whitespace-nowrap"
                  title="覆盖柱数 / 全部入柱柱数；缺失柱按中性 50 计分并随行披露"
                >
                  覆盖 {fmtInt(it.covered)}/{fmtInt(it.n_pillars)}
                </span>
              </span>
            }
            right={<PillarPct item={it} directions={data.directions} />}
          />
        ))}
        <p className="text-[9px] text-slate-400 leading-relaxed pt-1.5">
          {data.notes?.method ?? '柱分位 = 池内截面 0–100 分位；负向柱已取反（100−分位）计分。'}
        </p>
      </div>
    )}
  </FaceCard>
);
