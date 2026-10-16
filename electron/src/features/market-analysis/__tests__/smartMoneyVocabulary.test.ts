/**
 * 聪明钱词表口径（展示纪律的机器检查）。
 *
 * 为什么值得钉住：
 * - 「缺失 ≠ 0」是聪明钱全链的展示契约——缺失一律「—」，任何 NaN/null 沉成
 *   0.00 都会把「引擎明确不给分」错误地表达成「分位为 0」；
 * - 分数是研究评分（中性紫系色阶），金额正负才用 A 股红涨绿跌；0 走中性，
 *   不能被涂成「绿」或「红」；
 * - 方向词（正向/负向）+ 提示必须说明负向柱已取反（100−分位）。
 */

import { describe, expect, it } from 'vitest';
import {
  BUCKET_TONES,
  CAR_STRONG_COLOR,
  CAR_WEAK_COLOR,
  MISSING,
  SEAT_CLASS_ORDER,
  directionHint,
  directionLabel,
  eventTypeLabel,
  fmtBp,
  fmtNum,
  fmtPct,
  fmtScore,
  fmtSigned,
  fmtYiFromWan,
  fmtYiFromYuan,
  pillarLabel,
  pillarShort,
  scoreCls,
  seatClassLabel,
  signCls,
} from '../smartMoneyVocabulary';

describe('缺失语义（缺失 ≠ 0）', () => {
  it('null/undefined/NaN 一律「—」，绝不显示 0', () => {
    expect(fmtNum(null)).toBe(MISSING);
    expect(fmtNum(undefined)).toBe(MISSING);
    expect(fmtNum(NaN)).toBe(MISSING);
    expect(fmtScore(null)).toBe(MISSING);
    expect(fmtYiFromYuan(null)).toBe(MISSING);
    expect(fmtYiFromWan(undefined)).toBe(MISSING);
  });

  it('真实的 0 是有效值：显示 0.00 而非「—」', () => {
    expect(fmtNum(0)).toBe('0.00');
    expect(fmtScore(0)).toBe('0.0');
  });
});

describe('金额单位换算', () => {
  it('元 → 亿（2 位，带符号）', () => {
    expect(fmtYiFromYuan(2e8)).toBe('2.00');
    expect(fmtYiFromYuan(-1.5e8)).toBe('-1.50');
  });

  it('万元 → 亿（两融原始单位是万元，除 1e4）', () => {
    expect(fmtYiFromWan(15000)).toBe('1.50');
    expect(fmtYiFromWan(-4200)).toBe('-0.42');
  });
});

describe('着色纪律（红涨绿跌，仅数值正负；分数中性）', () => {
  it('金额：正红 / 负绿 / 0 与缺失中性', () => {
    expect(signCls(1)).toContain('red');
    expect(signCls(-1)).toContain('green');
    expect(signCls(0)).toContain('slate');
    expect(signCls(null)).toContain('slate');
  });

  it('分数走中性紫/灰阶，绝不套红绿', () => {
    expect(scoreCls(82)).toContain('purple');
    expect(scoreCls(82)).not.toContain('red');
    expect(scoreCls(5)).not.toContain('green');
    expect(scoreCls(null)).toContain('slate');
  });
});

describe('方向与事件词', () => {
  it('事件类型枚举 → 中文标签；未知类型兜底不冒英文', () => {
    expect(eventTypeLabel('lhb')).toBe('龙虎榜');
    expect(eventTypeLabel('survey')).toBe('机构调研');
    expect(eventTypeLabel('unknown_type')).toBe('事件');
    expect(eventTypeLabel(null)).toBe(MISSING);
  });

  it('方向词：+1 正向 / −1 负向 / 缺 MISSING', () => {
    expect(directionLabel(1)).toBe('正向');
    expect(directionLabel(-1)).toBe('负向');
    expect(directionLabel(null)).toBe(MISSING);
  });

  it('负向方向提示明确披露「分位取反」', () => {
    expect(directionHint(-1)).toContain('分位取反');
    expect(directionHint(1)).toContain('原值');
  });

  it('柱名映射：label 与 short 均可回落原名（后端新增柱不至于显示空白）', () => {
    expect(pillarLabel('lhb_net_ratio_5d')).toBe('龙虎榜净买占比·5日');
    expect(pillarShort('lhb_net_ratio_5d')).toBe('龙虎榜');
    expect(pillarLabel('brand_new_pillar')).toBe('brand_new_pillar');
    expect(pillarShort('brand_new_pillar')).toBe('brand_new_pillar');
  });
});

describe('v2 格式器（B7 带符号统一 / 占比 / bp）', () => {
  it('fmtSigned：正数带「+」，负数原样，0 不带号；缺失「—」', () => {
    expect(fmtSigned(1.5)).toBe('+1.50');
    expect(fmtSigned(-1.5)).toBe('-1.50');
    expect(fmtSigned(0)).toBe('0.00');
    expect(fmtSigned(null)).toBe(MISSING);
  });

  it('fmtBp 与 fmtSigned 同口径（bp 只是展示单位语境）', () => {
    expect(fmtBp(37)).toBe('+37.0');
    expect(fmtBp(-14)).toBe('-14.0');
    expect(fmtBp(undefined)).toBe(MISSING);
  });

  it('fmtPct：0–1 分数 → 百分数带 %；缺失「—」', () => {
    expect(fmtPct(0.4712)).toBe('47.1%');
    expect(fmtPct(0.4712, 0)).toBe('47%');
    expect(fmtPct(null)).toBe(MISSING);
  });
});

describe('v2 常量纪律（席位类/评分桶/CAR 组色）', () => {
  it('席位类型顺序与兜底标签齐全', () => {
    expect(SEAT_CLASS_ORDER).toEqual(['inst', 'north', 'branch', 'other']);
    expect(seatClassLabel('inst')).toBe('机构专用');
    expect(seatClassLabel('north', '陆股通')).toBe('陆股通');
    expect(seatClassLabel('unknown_cls')).toBe('unknown_cls');
  });

  it('评分桶四档色阶（紫阶强度递进，不占红绿）', () => {
    expect(BUCKET_TONES).toHaveLength(4);
    BUCKET_TONES.forEach((tone) => {
      expect(tone).not.toContain('red');
      expect(tone).not.toContain('green');
    });
  });

  it('CAR 强/弱组=身份色（紫/蓝），绝不套红绿涨跌语义', () => {
    expect(CAR_STRONG_COLOR).toBe('#7c3aed');
    expect(CAR_WEAK_COLOR).toBe('#2563eb');
  });
});
