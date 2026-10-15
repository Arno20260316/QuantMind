/** 聪明钱 · 席位榜（龙虎榜营业部净买额排序，5/20 日窗口切换）。
 *
 * 口径：净买额 = 买入额−卖出额（元→亿展示）；n_days=窗口内上榜天数；
 * 席位行为是事实统计，不含「跟随营业部」类暗示文案。
 */

import React from 'react';
import { Store } from 'lucide-react';
import { Tooltip } from 'antd';
import { EmptyHint, PeriodChips, RankRow, SectionCard } from '../../../market-analysis-shared/ui';
import type { SmartBranchRow } from '../../services/smartMoneyService';
import { fmtYiFromYuan, signCls } from '../../smartMoneyVocabulary';

const WINDOW_OPTIONS = [
  { id: '5', label: '5 日' },
  { id: '20', label: '20 日' },
];

interface SmartMoneyBranchBoardProps {
  rows: SmartBranchRow[] | null;
  window: 5 | 20;
  onWindowChange: (w: 5 | 20) => void;
  loading?: boolean;
}

export const SmartMoneyBranchBoard: React.FC<SmartMoneyBranchBoardProps> = ({
  rows,
  window,
  onWindowChange,
  loading,
}) => {
  const list = rows ?? [];

  return (
    <SectionCard
      icon={<Store className="w-3.5 h-3.5 text-purple-600" />}
      title="活跃席位（龙虎榜营业部）"
      extra={
        <PeriodChips
          options={WINDOW_OPTIONS}
          value={String(window)}
          onChange={(id) => onWindowChange(id === '20' ? 20 : 5)}
        />
      }
    >
      {loading && !list.length ? (
        <EmptyHint loading />
      ) : list.length ? (
        <div className="flex flex-col">
          {list.map((b, i) => (
            <RankRow
              key={b.branch_name}
              rank={i + 1}
              name={b.branch_name}
              nameSub={b.n_days > 0 ? `${b.n_days} 日` : undefined}
              main={
                b.stocks_bought ? (
                  <Tooltip title={`窗口内买入个股：${b.stocks_bought}`}>
                    <span className="text-[9px] text-slate-400 truncate">{b.stocks_bought}</span>
                  </Tooltip>
                ) : null
              }
              right={
                <Tooltip
                  title={`买 ${fmtYiFromYuan(b.buy_amount)} 亿 · 卖 ${fmtYiFromYuan(b.sell_amount)} 亿`}
                >
                  <span className={`text-[11px] font-mono font-extrabold ${signCls(b.net_amount)}`}>
                    {fmtYiFromYuan(b.net_amount)}
                    <span className="text-[9px] font-normal text-slate-400 ml-0.5">亿</span>
                  </span>
                </Tooltip>
              }
            />
          ))}
        </div>
      ) : (
        <EmptyHint text={`近 ${window} 日无席位记录`} />
      )}
    </SectionCard>
  );
};
