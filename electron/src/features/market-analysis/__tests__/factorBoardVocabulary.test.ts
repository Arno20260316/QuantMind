/**
 * 因子概率板 · 判定词汇与产物契约（与后端金样共读防漂移）。
 *
 * 金样与后端单测共用同一个 JSON，存在**后端包内**
 * （`backend/tests/fixtures/factorBoardGolden.json`）：后端测试跑在容器里，
 * 只挂了 `./backend`，金样放在后端侧两边才都读得到。任一侧改了判定枚举或
 * 顶层契约，另一侧的测试立刻红。
 *
 * 展示词纪律（CLAUDE.md「展示面/执行面用词边界」）：判定词只说位置
 * （靠前/靠后），绝不出现买卖指令。
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
    DECAY_META,
    REGIME_NOTE_META,
    VERDICT_META,
    decayMeta,
    regimeNoteMeta,
    verdictMeta,
} from '../factorBoardVocabulary';
import { REQUIRED_TOP_KEYS } from '../services/factorBoardService';

/**
 * 直接读文件而不是 `import`（与 researchScore.test.ts 同款）：金样在
 * `electron/` 之外，走模块解析会碰到 vite 的 root 限制；`__dirname` 定位与 cwd 无关。
 */
const GOLDEN_PATH = path.resolve(
    __dirname,
    '../../../../..',
    'backend/tests/fixtures/factorBoardGolden.json',
);
const golden: {
    verdictTruthTable: Array<{ case: string; expect: string }>;
    boardRequiredTop: string[];
    boardSchemaVersion: string;
    boardOptionalTop: string[];
    decayEnum: string[];
    regimeNoteEnum: string[];
} = JSON.parse(readFileSync(GOLDEN_PATH, 'utf-8'));

const BANNED_ADVICE_WORDS = ['买入', '卖出', '加仓', '减仓', '建仓', '清仓', '建议'];

describe('因子概率板词汇与契约', () => {
    it('真值表里的每个判定枚举都有展示映射', () => {
        // Arrange
        const enums = [...new Set(golden.verdictTruthTable.map((c) => c.expect))];

        // Assert
        expect(enums.length).toBeGreaterThan(0);
        for (const e of enums) {
            expect(VERDICT_META[e], `缺映射: ${e}`).toBeDefined();
        }
    });

    it('展示词不含买卖指令词（展示面纪律）', () => {
        const maps: Array<[string, Record<string, { label: string; hint: string }>]> = [
            ['VERDICT', VERDICT_META],
            ['DECAY', DECAY_META],
            ['REGIME_NOTE', REGIME_NOTE_META],
        ];
        for (const [mapName, map] of maps) {
            for (const [key, meta] of Object.entries(map)) {
                for (const word of BANNED_ADVICE_WORDS) {
                    expect(`${meta.label}|${meta.hint}`, `${mapName}.${key} 含禁用词 ${word}`).not.toContain(word);
                }
            }
        }
    });

    it('未知枚举不硬套位置词，原样显形', () => {
        const meta = verdictMeta('brand_new_verdict');
        expect(meta.label).toBe('未知判定');
        expect(meta.hint).toContain('brand_new_verdict');
    });

    it('产物必需顶层键与后端金样一致', () => {
        expect([...REQUIRED_TOP_KEYS].sort()).toEqual([...golden.boardRequiredTop].sort());
    });

    it('1.1 可选顶层块（regime/dedup）与必需键不交', () => {
        // 展示纪律：可选键缺省即隐藏（历史 1.0 产物仍按 8 核心键渲染）
        expect([...golden.boardOptionalTop].sort()).toEqual(['dedup', 'regime']);
        const required = new Set<string>(golden.boardRequiredTop);
        for (const k of golden.boardOptionalTop) {
            expect(required.has(k), `可选键 ${k} 与必需键重叠`).toBe(false);
        }
    });

    it('衰减四态枚举与 DECAY_META 一一对应', () => {
        expect(Object.keys(DECAY_META).sort()).toEqual([...golden.decayEnum].sort());
    });

    it('制度注记枚举与 REGIME_NOTE_META 一一对应', () => {
        expect(Object.keys(REGIME_NOTE_META).sort()).toEqual([...golden.regimeNoteEnum].sort());
    });

    it('衰减/制度注记缺省与未知出口不猜语义', () => {
        // 缺省（1.0 产物或降级）→ —；未知枚举 → 「未知」+ 原文显形
        expect(decayMeta(null).label).toBe('—');
        expect(decayMeta(undefined).label).toBe('—');
        const unknownDecay = decayMeta('brand_new_decay');
        expect(unknownDecay.label).toBe('未知');
        expect(unknownDecay.hint).toContain('brand_new_decay');
        expect(regimeNoteMeta(null).label).toBe('—');
        const unknownNote = regimeNoteMeta('brand_new_note');
        expect(unknownNote.label).toBe('未知');
        expect(unknownNote.hint).toContain('brand_new_note');
    });
});
