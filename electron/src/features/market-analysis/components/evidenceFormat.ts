/** 证据台展示格式（命中榜 / 信号档案 / 全景面板共用；缺失一律 —，绝不显 0）。 */

export function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US');
}

/** 产物裸日期（20261009）→ 展示用 ISO；已是 ISO 则原样。 */
export function fmtRawDate(s: string | null | undefined): string {
  if (!s) return '—';
  return s.length === 8 && /^\d{8}$/.test(s)
    ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}`
    : s;
}
