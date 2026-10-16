/** 跨市场共享 · 市场分析 UI 小组件（卡片 / 涨跌读数 / 排行行 / 空态）
 *
 * 原为 `features/market-analysis-hk/components/shared/ui.tsx`，三个市场
 * （A 股 / 港股 / 美股）的展示口径一致，故提升到共享目录；
 * 港股侧原路径改为 re-export，调用方零改动。
 */

import React from 'react';

/** 涨跌颜色文本（A 股/港股/美股统一：红涨绿跌） */
export function PctText({
  value,
  suffix = '%',
  className = '',
}: {
  value: number | null | undefined;
  suffix?: string;
  className?: string;
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={`text-slate-400 ${className}`}>--</span>;
  }
  const v = Number(value);
  const color = v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-slate-500';
  const sign = v > 0 ? '+' : '';
  return (
    <span className={`font-extrabold font-mono ${color} ${className}`}>
      {sign}
      {v.toFixed(2)}
      {suffix}
    </span>
  );
}

/** 带颜色的数值读数（非百分比场景，如金额/倍数） */
export function NumText({
  value,
  digits = 2,
  suffix = '',
  className = '',
}: {
  value: number | null | undefined;
  digits?: number;
  suffix?: string;
  className?: string;
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={`text-slate-400 ${className}`}>--</span>;
  }
  const v = Number(value);
  const color = v > 0 ? 'text-red-600' : v < 0 ? 'text-green-600' : 'text-slate-500';
  const sign = v > 0 ? '+' : '';
  return (
    <span className={`font-extrabold font-mono ${color} ${className}`}>
      {sign}
      {v.toLocaleString('zh-CN', { maximumFractionDigits: digits })}
      {suffix}
    </span>
  );
}

/** 白底卡片容器（对齐 A 股市场分析卡片视觉） */
export function SectionCard({
  title,
  icon,
  extra,
  children,
  className = '',
}: {
  title: React.ReactNode;
  icon?: React.ReactNode;
  extra?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`bg-white/90 backdrop-blur-md rounded-2xl p-4 border border-slate-200/80 shadow-sm flex flex-col gap-3 ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5">
          {icon}
          <span>{title}</span>
        </h3>
        {extra && <div className="flex items-center gap-2 flex-shrink-0">{extra}</div>}
      </div>
      {children}
    </div>
  );
}

/** 分面容器：framed（默认）= 独立白卡（与 SectionCard 同款）；
 *
 * `bare` = 无卡壳（标题行 + 内容直接铺开）——供「统一大框 + 页内标签面」使用，
 * 避免卡中卡；标签面标题保留（面内口径说明不随卡壳一起丢）。
 */
export function FaceCard({
  bare = false,
  title,
  icon,
  extra,
  children,
  className = '',
}: {
  /** true = 无卡壳（嵌在统一大框内的标签面用） */
  bare?: boolean;
  title: React.ReactNode;
  icon?: React.ReactNode;
  extra?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  if (!bare) {
    return (
      <SectionCard title={title} icon={icon} extra={extra} className={className}>
        {children}
      </SectionCard>
    );
  }
  return (
    <div className={`flex flex-col gap-2.5 min-w-0 ${className}`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-xs font-extrabold text-slate-800 flex items-center gap-1.5 min-w-0">
          {icon}
          <span className="min-w-0">{title}</span>
        </h3>
        {extra && <div className="flex items-center gap-2 flex-wrap">{extra}</div>}
      </div>
      {children}
    </div>
  );
}

/** 通用排行表行（表格行二三列对齐名/值）
 *
 * 传 `onClick` 时整行为可点击入口（如点击弹出个股终端浮窗）：
 * 指针 + hover 高亮；内部子元素（下钻按钮等）的点击用 stopPropagation 隔离。
 * `title` 供点击行为提示（如「查看个股终端」）。
 */
export function RankRow({
  rank,
  name,
  nameSub,
  main,
  right,
  onClick,
  title,
}: {
  rank: number;
  name: string;
  nameSub?: string;
  main: React.ReactNode;
  right?: React.ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  const clickable = Boolean(onClick);
  return (
    <div
      onClick={onClick}
      title={title}
      role={clickable ? 'button' : undefined}
      className={`flex items-center gap-2.5 px-1 py-1.5 border-b border-slate-50 last:border-0 transition-colors hover:bg-slate-50/70 ${
        clickable ? 'cursor-pointer hover:bg-purple-50/60' : ''
      }`}
    >
      <span
        className={`w-5 h-5 rounded-md grid place-items-center text-[10px] font-extrabold ${
          rank <= 3 ? 'bg-purple-600 text-white' : 'bg-slate-100 text-slate-500'
        }`}
      >
        {rank}
      </span>
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="text-xs font-bold text-slate-800 truncate">{name}</span>
        {nameSub && <span className="text-[10px] font-mono text-slate-400 truncate">{nameSub}</span>}
        {main}
      </div>
      {right}
    </div>
  );
}

/** 加载骨架/空态 */
export function EmptyHint({ text = '暂无数据', loading = false }: { text?: string; loading?: boolean }) {
  return (
    <div className="py-8 text-center text-xs text-slate-400 font-medium">
      {loading ? '加载中…' : text}
    </div>
  );
}

/** 周期/分类 chip 切换
 *
 * `accent` 控制选中态主色：港股与 A 股用默认的紫色，美股模块用蓝色
 * （各市场模块有自己的主题色，共享组件不写死单一市场配色）。
 */
export function PeriodChips({
  options,
  value,
  onChange,
  accent = 'purple',
}: {
  options: Array<{ id: string; label: string }>;
  value: string;
  onChange: (id: string) => void;
  accent?: 'purple' | 'blue';
}) {
  const activeCls =
    accent === 'blue'
      ? 'bg-white text-blue-700 shadow-2xs border border-blue-200'
      : 'bg-white text-purple-700 shadow-2xs border border-purple-200';
  return (
    <div className="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          onClick={() => onChange(opt.id)}
          className={`px-3 py-1 rounded-full text-[11px] font-extrabold transition-all ${
            value === opt.id ? activeCls : 'text-slate-500 hover:text-slate-800'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** 千分位数字 */
export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

/** 元/美元等原始金额 → 亿（保留两位），无效值返回 '--' */
export function fmtYi(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  return (Number(n) / 1e8).toFixed(2);
}

/** 数据日期标注条（面板统一显示「数据日期」，避免跨面板日期错位被误读） */
export function DateBadge({ label, date }: { label?: string; date?: string | null }) {
  if (!date) return null;
  return (
    <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-100 text-[10px] font-extrabold font-mono text-slate-500 whitespace-nowrap">
      {label && <span className="text-slate-400">{label}</span>}
      <span>{date}</span>
    </span>
  );
}
