/**
 * 港股聪明钱词表纪律单测（细案 §6 用词纪律）。
 *
 * 钉住三件事：
 * - 缺失一律「—」绝不显示成 0（反之 0 是真实读数，不能被吞成缺失）；
 * - 方向/流向/分类展示词与色阶纪律（IC 与分数绝不套红绿——统计量不是涨跌）；
 * - 词表全量字符串禁词扫描：不出现「推荐/建议/买入信号」与「前 N%」话术。
 */
import { describe, it, expect } from 'vitest';
import * as vocab from '../hkSmartMoneyVocabulary';

describe('hkSmartMoneyVocabulary · 缺失安全与符号', () => {
  it('缺失一律「—」，绝不显示成 0', () => {
    expect(vocab.fmtNum(null)).toBe('—');
    expect(vocab.fmtNum(undefined)).toBe('—');
    expect(vocab.fmtNum(Number.NaN)).toBe('—');
    expect(vocab.fmtInt(null)).toBe('—');
    expect(vocab.fmtSigned(null)).toBe('—');
    expect(vocab.fmtPct(null)).toBe('—');
    expect(vocab.fmtBp(null)).toBe('—');
    expect(vocab.fmtScore(null)).toBe('—');
    expect(vocab.fmtShares(null)).toBe('—');
    expect(vocab.fmtHkdWan(null)).toBe('—');
  });

  it('0 是真实读数（显示 0 而不是缺失符）', () => {
    expect(vocab.fmtNum(0, 1)).toBe('0.0');
    expect(vocab.fmtInt(0)).toBe('0');
    expect(vocab.fmtSigned(0, 2)).toBe('0.00');
    expect(vocab.fmtPct(0)).toBe('0.0%');
  });

  it('正数统一带「+」、负数带「-」（与红绿符号一致）', () => {
    expect(vocab.fmtSigned(12.34, 2)).toBe('+12.34');
    expect(vocab.fmtSigned(-5.6, 1)).toBe('-5.6');
    expect(vocab.fmtBp(3.25)).toBe('+3.3');
  });

  it('股数自适应亿/万/股', () => {
    expect(vocab.fmtShares(5.83e11)).toBe('5,830.00亿股');
    expect(vocab.fmtShares(2.5e5)).toBe('25.0万股');
    expect(vocab.fmtShares(999)).toBe('999股');
  });
});

describe('hkSmartMoneyVocabulary · 方向与流向词', () => {
  it('席位方向：+1 增持 / −1 减持 / 其他缺失', () => {
    expect(vocab.directionWord(1)).toBe('增持');
    expect(vocab.directionWord(-1)).toBe('减持');
    expect(vocab.directionWord(null)).toBe('—');
    expect(vocab.directionWord(2 as never)).toBe('—');
  });

  it('类型净额：正净流入 / 负净流出 / 缺失「—」', () => {
    expect(vocab.flowWord(1.2)).toBe('净流入');
    expect(vocab.flowWord(-3)).toBe('净流出');
    expect(vocab.flowWord(null)).toBe('—');
  });

  it('分类与 kind 标签优先取接口回传、兜底内置表', () => {
    expect(vocab.categoryLabel('cn_broker')).toBe('内资·券商');
    expect(vocab.categoryLabel('cn_broker', '接口标签')).toBe('接口标签');
    expect(vocab.categoryLabel('unknown_cat')).toBe('unknown_cat');
    expect(vocab.kindLabel('custodian')).toBe('托管');
    expect(vocab.kindLabel('broker')).toBe('券商');
    expect(vocab.kindLabel(null)).toBe('—');
  });

  it('窗口 label 与 chips 集一致', () => {
    expect(vocab.windowLabel('60')).toBe('60日');
    expect(vocab.windowLabel('all')).toBe('全史');
    expect(vocab.SEAT_WINDOW_OPTIONS.map((o) => o.id)).toEqual(['20', '60', 'all']);
  });
});

describe('hkSmartMoneyVocabulary · 色阶纪律', () => {
  it('signCls 红涨绿跌；0 与缺失中性', () => {
    expect(vocab.signCls(1)).toContain('text-red-600');
    expect(vocab.signCls(-1)).toContain('text-green-600');
    expect(vocab.signCls(0)).toContain('text-slate-500');
    expect(vocab.signCls(null)).toContain('text-slate-500');
  });

  it('IC 是统计量：绝不套红绿', () => {
    for (const v of [0.02, -0.02, 0.001, -0.0001, 0, null, undefined]) {
      const cls = vocab.icCls(v);
      expect(cls).not.toContain('red');
      expect(cls).not.toContain('green');
      expect(cls).toContain('slate');
    }
  });

  it('scoreCls 紫阶（分不是涨跌）', () => {
    expect(vocab.scoreCls(85)).toBe('text-purple-700');
    expect(vocab.scoreCls(50)).toBe('text-slate-700');
    expect(vocab.scoreCls(10)).toBe('text-slate-500');
    expect(vocab.scoreCls(null)).toBe('text-slate-400');
  });
});

describe('港股聪明钱 · 展示词表禁词扫描（细案 §6 纪律）', () => {
  it('词表全部导出字符串与格式化输出不含推荐/建议/买入信号/前 N% 话术', () => {
    const banned = [
      '推荐',
      '建议',
      '买入信号',
      '卖出信号',
      '前 20%',
      '前20%',
      '后 20%',
      '后20%',
      '前 10%',
      '前10%',
    ];
    const strings: string[] = [];
    for (const v of Object.values(vocab)) {
      if (typeof v === 'string') strings.push(v);
      else if (v && typeof v === 'object') {
        for (const x of Object.values(v)) {
          if (typeof x === 'string') strings.push(x);
          else if (x && typeof x === 'object') {
            for (const y of Object.values(x)) {
              if (typeof y === 'string') strings.push(y);
            }
          }
        }
      }
    }
    // 动态输出也扫（含方向/流向/窗口/格式化）
    strings.push(
      vocab.directionWord(1),
      vocab.directionWord(-1),
      vocab.flowWord(2),
      vocab.flowWord(-2),
      vocab.windowLabel('all'),
      vocab.fmtSigned(1.5, 1),
      vocab.fmtPct(0.512),
      vocab.categoryLabel('hk'),
      vocab.kindLabel('settlement'),
    );
    expect(strings.length).toBeGreaterThan(20);
    for (const s of strings) {
      for (const w of banned) {
        expect(s, `禁词「${w}」出现在「${s}」`).not.toContain(w);
      }
    }
  });

  it('标题与免责声明符合细案文案', () => {
    expect(vocab.HK_SM_TITLE).toBe('聪明钱 · 席位行为统计与类型效力');
    expect(vocab.HK_SM_DISCLAIMER).toBe('研究统计（非模型预测、非交易指令）');
  });
});
