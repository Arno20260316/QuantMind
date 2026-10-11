/**
 * 延续证据台 · 档位词汇（v3 命中榜「连N」标签切换的唯一实现）。
 *
 * 纪律：
 * - 档位一律来自产物 `badge_levels`（形如 [5, 3, 2]），升序去重后展示；
 *   产物缺档/坏值 → 回落金样三档 [2, 3, 5]，绝不显 0 档；
 * - 命中行按 `streak >= 档位` 过滤（≥2 含 ≥3 的行——这是「至少连续 N 日」的语义，
 *   不是「恰为 N 日」）；streak 缺失/坏值行不参与任何档位，绝不按 0 处理放行。
 */

const FALLBACK_LEVELS = [2, 3, 5];

/** 产物档位 → 升序去重的正整数档位；空/坏输入回落 [2, 3, 5]。 */
export function streakLevels(badgeLevels?: number[] | null): number[] {
  const src = Array.isArray(badgeLevels) ? badgeLevels : [];
  const clean = [...new Set(src.filter((v) => Number.isFinite(v) && v >= 1 && Number.isInteger(v)))].sort(
    (a, b) => a - b,
  );
  return clean.length > 0 ? clean : [...FALLBACK_LEVELS];
}

/** 默认档位 = 最低档（即「≥2 日」的完整命中榜）。 */
export function defaultLevel(levels: number[]): number {
  return levels.length > 0 ? levels[0] : FALLBACK_LEVELS[0];
}

/** 命中行（至少含 streak）按档位过滤；坏 streak 行剔除，level 非数→原样返回。 */
export function filterHits<T extends { streak: number }>(
  hits: T[] | null | undefined,
  level: number,
): T[] {
  if (!Array.isArray(hits)) return [];
  if (!Number.isFinite(level)) return hits;
  return hits.filter((h) => Number.isFinite(h?.streak) && h.streak >= level);
}

/** 各档命中计数，与 levels 一一对齐（供标签角标）。 */
export function hitCounts<T extends { streak: number }>(
  hits: T[] | null | undefined,
  levels: number[],
): number[] {
  return levels.map((lv) => filterHits(hits, lv).length);
}
