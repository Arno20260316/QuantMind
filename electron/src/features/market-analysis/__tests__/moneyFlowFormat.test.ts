/**
 * 资金流金额格式化单位纪律（双重缩放回归）。
 *
 * 背景：flow 逐日序列（个股五档 / 板块 trend）后端已按 1e8 折算成「亿」，
 * 若再走 fmtYi（入参为「元」）会二次除 1e8，任何真实值都渲染成 +0.00 亿——
 * 个股卡 tooltip 曾因此「超大/大/中/小全是 0」。本测试锁定两个口径的分工：
 * fmtYi 只收「元」，fmtSeriesYi 只收「亿」，二者绝不可互换。
 */

import { describe, expect, it } from 'vitest';
import { fmtSeriesYi, fmtYi } from '../services/moneyFlowService';

describe('金额格式化的单位口径', () => {
    it('fmtYi 收「元」：6.63e7 元 → +0.66 亿', () => {
        expect(fmtYi(66297612.65)).toBe('+0.66 亿');
        expect(fmtYi(-305000000)).toBe('-3.05 亿');
    });

    it('fmtSeriesYi 收「亿」：0.2802（已折算）→ +0.28 亿，不被二次缩放', () => {
        // 回归：旧实现把这四个值又走了一遍 fmtYi → 全部渲染成 +0.00 亿
        expect(fmtSeriesYi(0.2802)).toBe('+0.28 亿');
        expect(fmtSeriesYi(0.3828)).toBe('+0.38 亿');
        expect(fmtSeriesYi(1.2308)).toBe('+1.23 亿');
        expect(fmtSeriesYi(-0.4031)).toBe('-0.40 亿');
    });

    it('若把「亿」误喂给 fmtYi，结果会被缩到 0.00——这正是历史 bug 的形态', () => {
        expect(fmtYi(0.2802)).toBe('+0.00 亿');
    });

    it('缺失一律 —，绝不显 0（两口径一致）', () => {
        for (const v of [null, undefined, NaN]) {
            expect(fmtYi(v)).toBe('—');
            expect(fmtSeriesYi(v)).toBe('—');
        }
    });

    it('零值显示 +0.00 亿（真实 0 与缺失区分：0 不是缺失）', () => {
        expect(fmtSeriesYi(0)).toBe('+0.00 亿');
        expect(fmtYi(0)).toBe('+0.00 亿');
    });
});
