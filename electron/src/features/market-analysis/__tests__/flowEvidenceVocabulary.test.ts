/**
 * 延续证据台档位词汇（v3 命中榜标签切换）。
 *
 * 覆盖：产物档位升序去重 / 坏输入回落金样三档 / ≥N 的「至少」语义 /
 * 坏 streak 行剔除（绝不按 0 放行）/ 计数与档位对齐。
 */

import { describe, expect, it } from 'vitest';
import {
    defaultLevel,
    filterHits,
    hitCounts,
    streakLevels,
} from '../flowEvidenceVocabulary';

const HITS = [
    { name: 'A', streak: 6 },
    { name: 'B', streak: 4 },
    { name: 'C', streak: 3 },
    { name: 'D', streak: 2 },
    { name: 'E', streak: 2 },
];

describe('延续证据台档位词汇', () => {
    it('产物档位升序去重（[5,3,2] → [2,3,5]）', () => {
        expect(streakLevels([5, 3, 2])).toEqual([2, 3, 5]);
        expect(streakLevels([3, 3, 2])).toEqual([2, 3]);
        expect(streakLevels([1, 2, 10])).toEqual([1, 2, 10]);
    });

    it('缺档/坏值一律回落金样三档，绝不显 0 档', () => {
        for (const bad of [null, undefined, [], [NaN], [0], [-2], [1.5 as number]]) {
            expect(streakLevels(bad as number[] | null | undefined), `坏输入 ${String(bad)}`).toEqual([2, 3, 5]);
        }
        expect(defaultLevel(streakLevels(null))).toBe(2);
    });

    it('默认档位 = 最低档', () => {
        expect(defaultLevel([2, 3, 5])).toBe(2);
        expect(defaultLevel([])).toBe(2);
    });

    it('≥N 是「至少」语义：连6 同时出现在 ≥2/≥3/≥5 档', () => {
        expect(filterHits(HITS, 2).map((h) => h.name)).toEqual(['A', 'B', 'C', 'D', 'E']);
        expect(filterHits(HITS, 3).map((h) => h.name)).toEqual(['A', 'B', 'C']);
        expect(filterHits(HITS, 5).map((h) => h.name)).toEqual(['A']);
    });

    it('streak 缺失/坏值的行不参与任何档位（绝不按 0 放行）', () => {
        const dirty = [
            { name: 'ok', streak: 2 },
            { name: 'nan', streak: NaN },
            { name: 'str', streak: '3' as unknown as number },
            { name: 'undef', streak: undefined as unknown as number },
        ];
        expect(filterHits(dirty, 2).map((h) => h.name)).toEqual(['ok']);
    });

    it('空命中/null 输入 → 空数组；level 非数 → 原样返回', () => {
        expect(filterHits(null, 2)).toEqual([]);
        expect(filterHits(undefined, 2)).toEqual([]);
        expect(filterHits(HITS, NaN)).toBe(HITS);
    });

    it('计数与档位一一对齐', () => {
        expect(hitCounts(HITS, [2, 3, 5])).toEqual([5, 3, 1]);
        expect(hitCounts(null, [2, 3, 5])).toEqual([0, 0, 0]);
    });
});
