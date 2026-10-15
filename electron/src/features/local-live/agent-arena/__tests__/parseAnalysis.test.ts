/**
 * 分析文本解析「无标记回退」口径（2026-10-15 模型对话排版批）。
 *
 * 背景：agent 改纯结构化 JSON 输出（无【】/①②标记）后，旧回退把原始 JSON 当
 * 「首段摘要」印进总结条、推理段再回抄一遍——给人看的界面出一屏机器码。
 * 现口径四级递降：JSON 内 summary 字段 → 剥离 JSON 后的散文首段 → 决策扼要行
 * → 说明句；推理不再回抄 JSON（纯 JSON 轮为空串 → 该节自动隐藏）。
 * 传统【】/①标记路径不受影响，这里同时钉住防回归。
 */

import { describe, expect, it } from 'vitest';

import { parseAnalysis } from '../arena/utils/parseAnalysis';

/** 真实形态（决策轮输出）：纯 JSON，无任何【】/①②标记 */
const pureJson = JSON.stringify({
  decisions: [
    { action: 'sell', code: '600338.SH', name: '西藏珠峰', pct: 1, reason: '技术面破位' },
    { action: 'buy', code: '601985.SH', name: '中国核电', pct: 0.1, reason: '量价共振' },
  ],
});

describe('无标记回退：纯 JSON 轮', () => {
  it('总结=决策扼要行（本轮 N 项决策…），不再直出原始 JSON', () => {
    const pa = parseAnalysis(pureJson);

    expect(pa.summary).toContain('本轮 2 项决策');
    expect(pa.summary).toContain('卖出 西藏珠峰 100%');
    expect(pa.summary).toContain('买入 中国核电 10%');
    expect(pa.summary).not.toContain('{');
    expect(pa.decisions).toHaveLength(2);
  });

  it('推理论证置空（不再回抄 JSON，该节自动隐藏）', () => {
    const pa = parseAnalysis(pureJson);

    expect(pa.reasoning).toBe('');
  });

  it('决策超 4 项：扼要行只出前 4 项 + 省略号', () => {
    const many = JSON.stringify({
      decisions: Array.from({ length: 6 }, (_, i) => ({
        action: 'buy',
        code: `60000${i}.SH`,
        pct: 0.1,
      })),
    });
    const pa = parseAnalysis(many);

    expect(pa.summary).toContain('本轮 6 项决策');
    expect(pa.summary.split(' · ')).toHaveLength(4);
    expect(pa.summary.endsWith('…')).toBe(true);
  });

  it('未知动作原样保留（不吞字），无 name 退用 code', () => {
    const pa = parseAnalysis(
      JSON.stringify({ decisions: [{ action: 'hedge', code: 'IF2410' }] }),
    );

    expect(pa.summary).toContain('hedge IF2410');
  });

  it('JSON 内 summary 字段优先于决策扼要行', () => {
    const pa = parseAnalysis(
      JSON.stringify({ summary: '盘面偏震荡，观望为主。', decisions: [{ action: 'hold', code: '600000.SH' }] }),
    );

    expect(pa.summary).toBe('盘面偏震荡，观望为主。');
    expect(pa.decisions).toHaveLength(1);
  });

  it('JSON 内 analysis 字段进推理论证', () => {
    const pa = parseAnalysis(
      JSON.stringify({ analysis: '北向净流入，量能温和放大。', decisions: [{ action: 'hold', code: '600000.SH' }] }),
    );

    expect(pa.reasoning).toBe('北向净流入，量能温和放大。');
    expect(pa.summary).toContain('本轮 1 项决策');
  });
});

describe('无标记回退：散文与混排', () => {
  it('散文 + JSON 混排：摘要取散文首段，推理剥掉 JSON 区段，两段不重叠', () => {
    const text = `今日盘面缩量整理，观望情绪浓。\n\n第二段补充说明。\n\n${pureJson}`;
    const pa = parseAnalysis(text);

    expect(pa.summary).toBe('今日盘面缩量整理，观望情绪浓。');
    expect(pa.reasoning).toBe('第二段补充说明。');
    expect(pa.reasoning).not.toContain('{');
    expect(pa.decisions).toHaveLength(2);
  });

  it('散文里的「已调用」行被剥离', () => {
    const text = `分析结论如下。\n已调用 get_news\n\n${pureJson}`;
    const pa = parseAnalysis(text);

    expect(pa.summary).toBe('分析结论如下。');
    expect(pa.reasoning).toBe('');
  });

  it('短散文（无 JSON）：原「摘要=首段」行为保留', () => {
    const pa = parseAnalysis('暂不调整，等待信号。');

    expect(pa.summary).toBe('暂不调整，等待信号。');
    expect(pa.reasoning).toBe('');
  });

  it('markdown 报告（真实分析轮形态）：摘要跳过标题行取正文，不再只剩标题', () => {
    const text =
      '# 盘前持仓分析（2026-10-14）\n\n## 一句话结论\n账户 96.8% 现金、四笔持仓全部浮盈。\n\n## 逐一点评\n- 002074 逢高兑现。';
    const pa = parseAnalysis(text);

    expect(pa.summary).toBe('账户 96.8% 现金、四笔持仓全部浮盈。');
    expect(pa.summary).not.toContain('#');
    expect(pa.reasoning).toContain('## 逐一点评'); // 正文各段仍进推理论证
    expect(pa.reasoning).not.toContain('# 盘前持仓分析'); // 纯标题段不进
    expect(pa.reasoning).not.toContain('96.8%'); // 摘要取用的段不进推理（2026-10-15 评审修复：互不重叠）
  });

  it('短段散文 + 决策 JSON：摘要=决策扼要行，短段全文进推理（不吞首段）', () => {
    const text = '好的，执行\n\n完毕\n\n{"decisions":[{"action":"buy","code":"600000.SH"}]}';
    const pa = parseAnalysis(text);

    expect(pa.summary).toBe('本轮 1 项决策：买入 600000.SH');
    expect(pa.reasoning).toBe('好的，执行\n\n完毕'); // 摘要未取段落 → 全文保留（评审修复：旧实现吞掉首段）
  });

  it('坏 JSON（截断）：总结=说明句，推理保留原文（不丢料）', () => {
    const bad = '{"decisions": [{"action": "buy", "code": "600000.SH"';
    const pa = parseAnalysis(bad);

    expect(pa.summary).toBe('结构化输出未能解析（展开查看原始记录）');
    expect(pa.summary).not.toContain('{');
    expect(pa.reasoning).toBe(bad);
  });
});

describe('传统标记路径不回归', () => {
  it('【】段标（无【总体总结】）：正文引子作摘要、推理/决策照旧', () => {
    const pa = parseAnalysis(
      '正文引子。\n【推理论证】资金面宽松。\n【决策】{"decisions":[{"action":"buy","code":"600036.SH","pct":0.2}]}',
    );

    expect(pa.summary).toBe('正文引子。');
    expect(pa.reasoning).toBe('资金面宽松。');
    expect(pa.decisions).toHaveLength(1);
  });

  it('①②③④ 编号式：各段互不重叠', () => {
    const pa = parseAnalysis(
      '① 缩量整理。② 已调用 get_kline ③ 北向净流入 ④ {"decisions":[{"action":"buy","code":"600519.SH"}]}',
    );

    expect(pa.summary).toBe('缩量整理。');
    expect(pa.chain).toContain('已调用 get_kline');
    expect(pa.reasoning).toBe('北向净流入');
    expect(pa.decisions).toHaveLength(1);
  });
});
