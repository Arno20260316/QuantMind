/** 期货市场分析 · 本地格式化与口径徽标
 *
 * 关键口径（与后端 feed 对齐）：
 * - 后端所有金额字段**已预换算**：`*_yi`=亿、`*_wan`=万手/万张，前端只展示不再缩放
 *   （注意：共享层 `fmtYi` 输入是「元」，直接对 `*_yi` 用会把数值再除 1e8，勿用）
 * - 「源不提供」一律 null → 渲染「—」，绝不落 0（国际品种/上金所无量仓数据）
 */

export const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

/** 普通数值：null → 「—」 */
export function fmt(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return '—';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

/** 带单位数值：null → 「—」（无单位尾巴，避免「— 亿」） */
export function fmtUnit(v: unknown, unit: string, digits = 2): string {
  const n = num(v);
  if (n === null) return '—';
  return `${n.toLocaleString('zh-CN', { maximumFractionDigits: digits })} ${unit}`;
}

/** 百分比字符串（tooltip / 图表标签用；颜色语义走共享 PctText） */
export function fmtPctS(v: unknown, digits = 2): string {
  const n = num(v);
  if (n === null) return '—';
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

/** 期限结构徽标（back=近高远低 / contango=近低远高） */
export const STRUCTURE_META: Record<string, { label: string; cls: string; hint: string }> = {
  backwardation: {
    label: 'Back 结构',
    cls: 'bg-amber-50 text-amber-700 border-amber-200',
    hint: '近月价格高于远月（近高远低），常见于现货偏紧格局',
  },
  contango: {
    label: 'Contango',
    cls: 'bg-sky-50 text-sky-700 border-sky-200',
    hint: '近月价格低于远月（近低远高），常见于库存充裕格局',
  },
  flat: {
    label: '平水结构',
    cls: 'bg-slate-100 text-slate-600 border-slate-200',
    hint: '近远月价差率在 ±0.25% 内，曲线近似水平',
  },
};

/** 增减仓四象限徽标（教材口径：涨+增仓=多头增仓 …） */
export const QUADRANT_META: Record<string, { label: string; cls: string; hint: string }> = {
  build_long: {
    label: '多头增仓',
    cls: 'bg-red-50 text-red-700 border-red-200',
    hint: '价格上行 + 持仓增加：多头新开仓推动',
  },
  build_short: {
    label: '空头增仓',
    cls: 'bg-green-50 text-green-700 border-green-200',
    hint: '价格下行 + 持仓增加：空头新开仓推动',
  },
  short_covering: {
    label: '空头回补',
    cls: 'bg-amber-50 text-amber-700 border-amber-200',
    hint: '价格上行 + 持仓减少：空头买平推涨',
  },
  long_unwind: {
    label: '多头离场',
    cls: 'bg-sky-50 text-sky-700 border-sky-200',
    hint: '价格下行 + 持仓减少：多头卖平砸跌',
  },
};

/** 期现基差状态徽标 */
export const BASIS_STATE_META: Record<string, { label: string; cls: string }> = {
  premium: { label: '现货升水', cls: 'bg-red-50 text-red-700 border-red-200' },
  discount: { label: '现货贴水', cls: 'bg-green-50 text-green-700 border-green-200' },
  flat: { label: '平水', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
};

/** 徽标小片 */
export function Badge({ text, cls, title }: { text: string; cls: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center px-1.5 py-[1px] rounded-md border text-[10px] font-extrabold whitespace-nowrap ${cls}`}
    >
      {text}
    </span>
  );
}
