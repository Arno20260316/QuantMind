/**
 * 训练页四市场分层的两条「静默错配」防线 + 结果卡词条全覆盖。
 *
 * 为什么值得钉住：
 * 1) `resolveTrainingPoolMarket` 是「池 × 市场」唯一的前端口径转换点。后端
 *    `pool_binding` 对池与市场**不做任何校验**——传错池不报错，A 股成分直接
 *    混进港股/美股/期货训练（静默错配，事后从日志里看不出来）。历史缺陷是把
 *    HK/US/FUTURES 一律映射成 `undefined`（弹窗默认成 CN）。唯一合法的映射是
 *    CUSTOM→CN（自传数据走 A 股供给），其余市场必须恒等透传。
 * 2) 空目录（目录未发布，`catalog_status='unpublished'`）曾经回落内置预设：
 *    US/HK/CRYPTO/FUTURES 的预设各 20 个键，会给一个「还没有数据契约的市场」
 *    凭空勾上 20 个特征。空目录是显式答案（没有可勾的特征），不是「落兜底」。
 *    对照组证明预设兜底本身还活着——不变量只在空目录生效，没有误杀老路径。
 * 3) `MARKET_LABELS` 是训练结果卡的展示词条：缺哪个市场，结果卡就退化成显示
 *    原始市场码（如 `CUSTOM`）。以 `TRAINING_MARKET_OPTIONS` 为全集断言（防再漏），
 *    同时反向断言没有词表外的多余词条。
 */

import { describe, expect, it } from 'vitest';

import {
  getDefaultFeaturesForMarket,
  resolveDefaultSelectedFeatures,
  resolveTrainingPoolMarket,
  TRAINING_MARKET_OPTIONS,
  type FeatureCategory,
  type TrainingMarket,
} from '../trainingUtils';
import { MARKET_LABELS } from '../TrainingResultView';

// ─── 股票池市场口径 ──────────────────────────────────────────────────────────

describe('resolveTrainingPoolMarket', () => {
  it('CUSTOM 自传数据走 A 股供给 → CN', () => {
    expect(resolveTrainingPoolMarket('CUSTOM')).toBe('CN');
  });

  it('HK/US/FUTURES/CRYPTO 恒等透传，绝不回落 CN', () => {
    expect(resolveTrainingPoolMarket('HK')).toBe('HK');
    expect(resolveTrainingPoolMarket('US')).toBe('US');
    expect(resolveTrainingPoolMarket('FUTURES')).toBe('FUTURES');
    expect(resolveTrainingPoolMarket('CRYPTO')).toBe('CRYPTO');
    expect(resolveTrainingPoolMarket('CN')).toBe('CN');
  });

  it('唯一映射是 CUSTOM→CN：词表全集里其余市场必须恒等', () => {
    for (const option of TRAINING_MARKET_OPTIONS) {
      if (option.value === 'CUSTOM') {
        expect(resolveTrainingPoolMarket(option.value)).toBe('CN');
        continue;
      }
      expect(resolveTrainingPoolMarket(option.value)).toBe(option.value);
    }
  });

  it('词表全集都返回非空字符串（undefined 会被弹窗默认成 CN——静默错配的旧形态）', () => {
    for (const option of TRAINING_MARKET_OPTIONS) {
      const market = resolveTrainingPoolMarket(option.value);
      expect(typeof market).toBe('string');
      expect(market.length).toBeGreaterThan(0);
      expect(market).not.toBe('undefined');
    }
  });
});

// ─── 空目录不变量 ────────────────────────────────────────────────────────────

/** 靶场夹具：目录里有一个真实存在的 US 预设键（无 flag），用于证明预设兜底仍活 */
const US_CATALOG_WITHOUT_FLAGS: FeatureCategory[] = [
  {
    id: 'fundamental',
    name: '基本面',
    icon: null,
    features: [{ key: 'pe_ttm', label: 'PE(TTM)' }],
  },
];

describe('空目录不落预设（resolveDefaultSelectedFeatures）', () => {
  const ALL_TRAINING_MARKETS = TRAINING_MARKET_OPTIONS.map((option) => option.value);

  // 前提对照组：这些市场的内置预设都非空。没有这个前提，下面的「返回 []」
  // 可能只是「兜底恰好也空」，什么也没证明。
  it.each(['HK', 'US', 'CRYPTO', 'FUTURES'] as TrainingMarket[])(
    '对照前提：%s 的内置预设非空（证明空返回不是恰好兜底也空）',
    (market) => {
      expect(getDefaultFeaturesForMarket(market).length).toBeGreaterThan(0);
    },
  );

  it.each(ALL_TRAINING_MARKETS)('空目录对 %s 返回 []，不凭空勾内置预设', (market) => {
    expect(resolveDefaultSelectedFeatures([], market)).toEqual([]);
  });

  it('目录非空但无 flag 时，非 CN/CUSTOM 市场仍走预设兜底（不变量只在空目录生效）', () => {
    expect(resolveDefaultSelectedFeatures(US_CATALOG_WITHOUT_FLAGS, 'US')).toEqual(['pe_ttm']);
  });

  it('对照：CN/CUSTOM 即使目录非空也无 flag 也返回空（名单只认 catalog flag）', () => {
    const cnKeyCatalog: FeatureCategory[] = [
      {
        id: 'fundamental',
        name: '基本面',
        icon: null,
        features: [{ key: getDefaultFeaturesForMarket('CN')[0], label: 'x' }],
      },
    ];
    expect(resolveDefaultSelectedFeatures(cnKeyCatalog, 'CN')).toEqual([]);
    expect(resolveDefaultSelectedFeatures(cnKeyCatalog, 'CUSTOM')).toEqual([]);
  });
});

// ─── 结果卡市场词条 ──────────────────────────────────────────────────────────

describe('MARKET_LABELS', () => {
  it('训练词表每个市场都有中文词条（缺 CUSTOM 时结果卡显示原始码）', () => {
    for (const option of TRAINING_MARKET_OPTIONS) {
      expect(MARKET_LABELS[option.value]).toBeTruthy();
    }
  });

  it('反向：没有词表外的多余词条（词条随词表收敛）', () => {
    const vocabulary = new Set<string>(TRAINING_MARKET_OPTIONS.map((option) => option.value));
    for (const key of Object.keys(MARKET_LABELS)) {
      expect(vocabulary.has(key)).toBe(true);
    }
  });
});
