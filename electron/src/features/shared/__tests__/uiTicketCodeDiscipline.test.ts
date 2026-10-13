/**
 * 界面文案不得出现内部工单号（`T-P3-01`、`T-FE-12`、`P6` 这类开发批次编号）。
 *
 * 口径（记忆 `ui-no-internal-ticket-codes`）：编号只对开发期有意义，摆到标题/标签/
 * 表头/按钮/tooltip 上就是噪音——用户看到的是一串读不懂的东西，还挤掉了真信息。
 * 代码注释里保留编号没问题，所以本守卫生成的是**剥掉注释之后**的源码文本。
 *
 * 为什么不逐个断言字符串：`PromotionGateCard` / `StartGateFailedModal` 各自的单测
 * 只能钉住自己那一块，下一处新写的 tooltip 照样能溜过去。这里做全树扫描，
 * 任何文件里把编号写进字符串都会当场红。
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

/** 工单/阶段编号：`T-P3-01`、`T-FE-12`、`T-P6-08`、`P6` 字面量。 */
const TICKET_CODE = /T-[A-Z]{1,3}\d[\d-]*/g;

/**
 * 去掉注释、**保留字符串内容**——字符串里出现编号才正是要抓的。
 *
 * 朴素实现（先整段正则删 `/*…*\/`）会在字符串里含 `/*` 时吃掉后面的真实代码，
 * 所以这里按字符走一个最小状态机。模板串里的 `${}` 不单独解析：模板串内的编号
 * 一样是面向用户的文案，整段保留是对的。
 */
export function stripComments(src: string): string {
    const NONE = '';
    let out = '';
    let i = 0;
    let state: 'code' | 'line' | 'block' | 'sq' | 'dq' | 'tpl' = 'code';
    while (i < src.length) {
        const c = src[i];
        const n = src[i + 1];
        if (state === 'code') {
            if (c === '/' && n === '/') { state = 'line'; i += 2; continue; }
            if (c === '/' && n === '*') { state = 'block'; i += 2; continue; }
            if (c === "'") { state = 'sq'; out += c; i += 1; continue; }
            if (c === '"') { state = 'dq'; out += c; i += 1; continue; }
            if (c === '`') { state = 'tpl'; out += c; i += 1; continue; }
            out += c; i += 1; continue;
        }
        if (state === 'line') { if (c === '\n') { state = 'code'; out += c; } i += 1; continue; }
        if (state === 'block') {
            if (c === '*' && n === '/') { state = 'code'; i += 2; continue; }
            // 注释吃掉换行会让报错行号整体上移——保留 \n，行号才是真行号。
            if (c === '\n') out += c;
            i += 1; continue;
        }
        if (state === 'sq') {
            if (c === '\\') { out += c + (n ?? NONE); i += 2; continue; }
            if (c === "'") state = 'code';
            out += c; i += 1; continue;
        }
        if (state === 'dq') {
            if (c === '\\') { out += c + (n ?? NONE); i += 2; continue; }
            if (c === '"') state = 'code';
            out += c; i += 1; continue;
        }
        // tpl
        if (c === '\\') { out += c + (n ?? NONE); i += 2; continue; }
        if (c === '`') state = 'code';
        out += c; i += 1; continue;
    }
    return out;
}

const SRC_ROOT = path.resolve(__dirname, '..', '..', '..');
const SKIP_DIR = /(^|\/)(node_modules|__mocks__|__tests__)(\/|$)/;
const SKIP_FILE = /(\.test|\.spec)\.tsx?$/;

function collectSourceFiles(dir: string, acc: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
        const full = path.join(dir, entry);
        const rel = path.relative(SRC_ROOT, full);
        if (SKIP_DIR.test('/' + rel)) continue;
        if (statSync(full).isDirectory()) {
            collectSourceFiles(full, acc);
            continue;
        }
        if (!/\.tsx?$/.test(entry) || SKIP_FILE.test(entry)) continue;
        acc.push(full);
    }
    return acc;
}

describe('stripComments', () => {
    it('删注释但保留字符串里的编号', () => {
        const src = 'const a = 1; // T-P1-01\nconst b = "T-P2-02";';
        const stripped = stripComments(src);
        expect(stripped).not.toContain('T-P1-01');
        expect(stripped).toContain('T-P2-02');
    });

    it('字符串里含 /* 不吞掉后面的代码', () => {
        // 朴素正则实现会在这里把 `const live = 1` 一起吃掉，从而让真泄漏隐形。
        const src = 'const s = "a /* b";\nconst live = "T-P9-99";';
        expect(stripComments(src)).toContain('T-P9-99');
    });

    it('块注释整段删除，行号不变（按 \\n 计数）', () => {
        const src = 'a\n/* T-P1-01\nT-P2-02 */\nb';
        const stripped = stripComments(src);
        expect(stripped).not.toMatch(TICKET_CODE);
        expect(stripped.split('\n').length).toBe(src.split('\n').length);
    });
});

describe('界面文案工单号纪律', () => {
    const files = collectSourceFiles(SRC_ROOT);

    it('扫描范围不是空的（否则本守卫是假通过）', () => {
        // 先证明真的扫到了东西：路径写错 / glob 失效时这里当场红。
        expect(files.length).toBeGreaterThan(500);
        expect(files.some((f) => f.endsWith('deskModel.ts'))).toBe(true);
    });

    it('剥掉注释后，任何源文件里都不再有 T-Pnn / T-FEnn 这类编号', () => {
        const offenders: string[] = [];
        for (const file of files) {
            const stripped = stripComments(readFileSync(file, 'utf-8'));
            stripped.split('\n').forEach((line, idx) => {
                const hits = line.match(TICKET_CODE);
                if (hits) {
                    offenders.push(
                        `${path.relative(SRC_ROOT, file)}:${idx + 1}: ${hits.join(',')} → ${line.trim().slice(0, 120)}`,
                    );
                }
            });
        }
        // 报全部命中点，别只报第一个——一次修完比来回跑强。
        expect(offenders.join('\n')).toBe('');
    });
});
