/**
 * 市场开关（isMarketEnabled）在训练配置导入面与市场下拉面的统一口径。
 *
 * 为什么值得钉住：生产构建隐藏 CRYPTO 后（顶栏 MarketSelector 都选不到），
 * 一份 crafted 的配置文件如果把 market 写成 CRYPTO，不能靠文件绕过开关把
 * 训练带进一个 UI 上不存在的市场——**词表校验通过 ≠ 当前构建启用**，两道
 * 判据必须都在。同时市场下拉的选项收敛与导入面的拒绝必须走同一把开关，
 * 否则会出现「下拉选不到、配置进得来」的双口径。
 *
 * 本文件 vi.mock 掉 marketFlags 以显式控制开关状态；真实 marketFlags 的
 * 词表校验（MARS / 大小写漂移拒绝）已由 trainingMarketCustom.test.ts 覆盖。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../config/marketFlags', () => ({
  isMarketEnabled: vi.fn((market: string) => market !== 'CRYPTO'),
}));

import { isMarketEnabled } from '../../../config/marketFlags';
import {
  buildTrainingConfigFile,
  DEFAULT_CONTEXT,
  DEFAULT_PARAMS,
  DEFAULT_TARGET,
  getEnabledTrainingMarketOptions,
  parseTrainingConfig,
  serializeTrainingConfig,
  TRAINING_MARKET_OPTIONS,
  type TrainingConfigFile,
  type TrainingDraft,
} from '../trainingUtils';

const mockedIsMarketEnabled = vi.mocked(isMarketEnabled);

/** 生产形态：CRYPTO 关闭（mock 工厂默认实现），每个用例先复位 */
const AS_PRODUCTION = () => mockedIsMarketEnabled.mockImplementation((market: string) => market !== 'CRYPTO');
/** 开发形态：全部市场开启 */
const AS_DEV = () => mockedIsMarketEnabled.mockReturnValue(true);

beforeEach(() => {
  AS_PRODUCTION();
});

const CRYPTO_DRAFT: Omit<TrainingDraft, 'lastSavedAt'> = {
  displayName: 'CRYPTO 配置门禁',
  displayNameMode: 'manual',
  selectedFeatures: ['mom_ret_20d'],
  timePeriods: {
    train: ['2018-01-01T00:00:00.000Z', '2023-12-31T00:00:00.000Z'],
    val: ['2024-01-01T00:00:00.000Z', '2024-12-31T00:00:00.000Z'],
    test: ['2025-01-01T00:00:00.000Z', '2025-12-31T00:00:00.000Z'],
  },
  target: DEFAULT_TARGET,
  params: DEFAULT_PARAMS,
  context: { ...DEFAULT_CONTEXT, market: 'CRYPTO' },
  wfa: { enabled: true, strategy: 'rolling', nWindows: 6, trainYears: 4, valMonths: 6, stepMonths: 3 },
};

const configFor = (market: TrainingConfigFile['market']): string =>
  serializeTrainingConfig(
    buildTrainingConfigFile(
      market === 'CRYPTO' ? CRYPTO_DRAFT : { ...CRYPTO_DRAFT, context: { ...DEFAULT_CONTEXT, market: 'CN' } },
      { market },
    ),
  );

describe('parseTrainingConfig 的市场开关门禁', () => {
  it('生产形态：词表内但构建未启用的 CRYPTO 必须拒绝', () => {
    expect(() => parseTrainingConfig(configFor('CRYPTO'))).toThrow('配置中的市场标识无效或当前构建未启用该市场');
  });

  it('对照组：同一份文件在开关打开（开发形态）时正常导入，证明拒绝来自开关而非词表', () => {
    AS_DEV();
    const parsed = parseTrainingConfig(configFor('CRYPTO'));
    expect(parsed.market).toBe('CRYPTO');
    expect(parsed.draft.selectedFeatures).toEqual(['mom_ret_20d']);
  });

  it('词表校验在开关打开时依然独立生效（MARS 即使市场全开也拒绝）', () => {
    AS_DEV();
    expect(() => parseTrainingConfig(configFor('MARS' as unknown as TrainingConfigFile['market']))).toThrow(
      '配置中的市场标识无效或当前构建未启用该市场',
    );
  });

  it('启用市场（CN）在两种形态下都可导入', () => {
    expect(parseTrainingConfig(configFor('CN')).market).toBe('CN');
    AS_DEV();
    expect(parseTrainingConfig(configFor('CN')).market).toBe('CN');
  });
});

describe('getEnabledTrainingMarketOptions（下拉选项收敛）', () => {
  it('生产形态：CRYPTO 从下拉隐藏，其余市场按词表顺序保留', () => {
    const values = getEnabledTrainingMarketOptions().map((option) => option.value);
    expect(values).toEqual(TRAINING_MARKET_OPTIONS.map((o) => o.value).filter((v) => v !== 'CRYPTO'));
    expect(values).not.toContain('CRYPTO');
  });

  it('开发形态：与词表全集一致（含 CRYPTO 与 CUSTOM）', () => {
    AS_DEV();
    expect(getEnabledTrainingMarketOptions()).toEqual(TRAINING_MARKET_OPTIONS);
  });

  it('下拉与导入面同一把开关：开关关 CRYPTO 时导入面也拒绝', () => {
    expect(getEnabledTrainingMarketOptions().map((o) => o.value)).not.toContain('CRYPTO');
    expect(() => parseTrainingConfig(configFor('CRYPTO'))).toThrow('当前构建未启用');
  });
});
