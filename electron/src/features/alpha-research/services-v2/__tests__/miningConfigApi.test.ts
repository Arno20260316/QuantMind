/**
 * 挖掘配置前端契约（移植 P1/P1.5）——最容易悄悄回归的四段：
 *
 * 1. **未自定义 = 零参数**：探测/预览/提交共用的映射不许发明键
 *    （多一个键，老路径的 h5 缓存身份与任务行就变了）；频率同理——
 *    日线是默认语义（''/null/'day' 都不发键）；
 * 2. **成对日期**：半截对显式抛错（提交路径上屏），绝不静默丢一半——
 *    「填了起点没填终点」静默丢掉会让用户以为区间生效了；
 * 3. **snake→camel 归一**：缺失一律 null / undefined（界面显「—」），
 *    服务端 `code!=200` 与网络错误都归一到 `success:false + error`；
 * 4. **分钟档**：freq=1min 进查询串；`minute` 块归一（ready/usable/gaps），
 *    未就绪不伪造可用区间。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: apiGetMock, post: vi.fn(), delete: vi.fn() },
}));

import {
  appendMiningConfigQuery,
  EMPTY_MINING_SELECTION,
  getMiningConfigOptions,
  hasCustomMiningConfig,
  isNonDefaultFreq,
  miningConfigWire,
} from '../miningConfigApi';

const fullOptionsBody = {
  code: 200,
  data: {
    datasets: [
      { key: 'features_daily', label: '日线因子宽表' },
      { key: 'l1_factors', label: 'L1 因子库' },
      { key: 'l2_factors', label: 'L2 微观结构因子' },
    ],
    freqs: [
      { value: 'day', label: '日线（默认）' },
      { value: '1min', label: '分钟 1min（仅 A 股）' },
    ],
    freq: 'day',
    minute: null,
    coverage: { start: '2016-01-04', end: '2026-10-09' },
    backtest_end_guard: '2026-10-08',
    defaults: {
      mining: { start: '2021-10-08', end: '2025-10-08' },
      validation: { start: '2025-10-08', end: '2026-10-08' },
    },
    windows: {
      mining: { start: '2021-10-08', end: '2025-10-08' },
      validation: null,
      is_default: false,
    },
    segments: {
      train: { start: '2021-10-08', end: '2024-06-24' },
      valid: { start: '2024-06-24', end: '2025-10-08' },
      test: { start: '2025-10-08', end: '2025-10-08' },
    },
    scopes: ['a_share'],
  },
};

beforeEach(() => {
  apiGetMock.mockReset();
  apiGetMock.mockResolvedValue({ data: fullOptionsBody });
});

describe('miningConfigWire：选择 → wire 字段', () => {
  test('全空/缺席 → 空对象（不发任何键）', () => {
    expect(miningConfigWire(null)).toEqual({});
    expect(miningConfigWire(undefined)).toEqual({});
    expect(miningConfigWire(EMPTY_MINING_SELECTION)).toEqual({});
    // 纯空白日期 = 没填（表单清空后可能留 ''）
    expect(
      miningConfigWire({
        miningStart: '  ',
        miningEnd: '',
        validStart: null,
        validEnd: null,
        datasets: [],
      }),
    ).toEqual({});
  });

  test('两对日期成对下发；只填一端显式抛错（不静默丢一半）', () => {
    expect(
      miningConfigWire({
        miningStart: '2021-01-04',
        miningEnd: '2023-12-31',
        validStart: '2024-01-01',
        validEnd: '2025-12-31',
        datasets: null,
      }),
    ).toEqual({
      mining_start: '2021-01-04',
      mining_end: '2023-12-31',
      valid_start: '2024-01-01',
      valid_end: '2025-12-31',
    });

    expect(() =>
      miningConfigWire({ ...EMPTY_MINING_SELECTION, miningStart: '2021-01-04' }),
    ).toThrow(/挖掘区间须成对/);
    expect(() =>
      miningConfigWire({ ...EMPTY_MINING_SELECTION, validEnd: '2025-12-31' }),
    ).toThrow(/检验区间须成对/);
  });

  test('数据集：空列表=全选不发键；非空按登记序原样（trim + 去空）', () => {
    expect(
      miningConfigWire({ ...EMPTY_MINING_SELECTION, datasets: [] }).datasets,
    ).toBeUndefined();
    expect(
      miningConfigWire({
        ...EMPTY_MINING_SELECTION,
        datasets: [' l1_factors ', '', 'features_daily'],
      }).datasets,
    ).toEqual(['l1_factors', 'features_daily']);
  });

  test('频率：日线默认语义（null/空串/day）不发键；1min 才发', () => {
    expect(miningConfigWire(EMPTY_MINING_SELECTION).freq).toBeUndefined();
    expect(
      miningConfigWire({ ...EMPTY_MINING_SELECTION, freq: '' }).freq,
    ).toBeUndefined();
    expect(
      miningConfigWire({ ...EMPTY_MINING_SELECTION, freq: 'day' }).freq,
    ).toBeUndefined();
    expect(
      miningConfigWire({ ...EMPTY_MINING_SELECTION, freq: ' 1min ' }).freq,
    ).toBe('1min');

    expect(isNonDefaultFreq(null)).toBe(false);
    expect(isNonDefaultFreq('day')).toBe(false);
    expect(isNonDefaultFreq('1min')).toBe(true);
  });
});

describe('hasCustomMiningConfig：谓词不抛（半截配置也判「动过」）', () => {
  test('全空 = false；任何一段在场 = true', () => {
    expect(hasCustomMiningConfig(null)).toBe(false);
    expect(hasCustomMiningConfig(EMPTY_MINING_SELECTION)).toBe(false);
    expect(hasCustomMiningConfig({ ...EMPTY_MINING_SELECTION, datasets: [] })).toBe(
      false,
    );
    expect(
      hasCustomMiningConfig({ ...EMPTY_MINING_SELECTION, miningStart: '2021-01-04' }),
    ).toBe(true);
    expect(
      hasCustomMiningConfig({ ...EMPTY_MINING_SELECTION, datasets: ['l1_factors'] }),
    ).toBe(true);
    // 频率离开日线默认 = 动过（分钟任务必须提交，否则等于没选）
    expect(hasCustomMiningConfig({ ...EMPTY_MINING_SELECTION, freq: '1min' })).toBe(
      true,
    );
    expect(hasCustomMiningConfig({ ...EMPTY_MINING_SELECTION, freq: 'day' })).toBe(
      false,
    );
  });
});

describe('appendMiningConfigQuery：追加语义（不覆盖已有键，数据集可重复）', () => {
  test('未自定义不产生键；数据集逐条 append', () => {
    const empty = appendMiningConfigQuery(new URLSearchParams({ loop_n: '3' }));
    expect([...empty.entries()]).toEqual([['loop_n', '3']]);

    const qs = appendMiningConfigQuery(new URLSearchParams({ loop_n: '3' }), {
      miningStart: '2021-01-04',
      miningEnd: '2023-12-31',
      validStart: null,
      validEnd: null,
      datasets: ['features_daily', 'l1_factors'],
    });
    expect([...qs.entries()]).toEqual([
      ['loop_n', '3'],
      ['mining_start', '2021-01-04'],
      ['mining_end', '2023-12-31'],
      ['datasets', 'features_daily'],
      ['datasets', 'l1_factors'],
    ]);
  });
});

describe('getMiningConfigOptions：预览请求与响应归一', () => {
  test('不传预览 = 裸路径；传了 = 频率 + 四端点 + 重复 datasets', async () => {
    await getMiningConfigOptions();
    expect(apiGetMock.mock.calls[0][0]).toBe('/alpha-agent/mining/config-options');

    await getMiningConfigOptions({
      miningStart: '2021-01-04',
      miningEnd: '2023-12-31',
      validStart: '2024-01-01',
      validEnd: '2025-12-31',
      datasets: ['l1_factors'],
      freq: '1min',
    });
    expect(apiGetMock.mock.calls[1][0]).toBe(
      '/alpha-agent/mining/config-options?freq=1min' +
        '&mining_start=2021-01-04&mining_end=2023-12-31' +
        '&valid_start=2024-01-01&valid_end=2025-12-31&datasets=l1_factors',
    );
  });

  test('分钟块归一：ready/usable/交易日数/gaps；未就绪不伪造可用区间', async () => {
    const minuteBlock = {
      ready: true,
      provider_dir: '/data/qlib/cn_data_1min',
      error: null,
      coverage: { start: '2026-01-05', end: '2026-08-25' },
      usable: { start: '2026-03-04', end: '2026-08-25' },
      usable_days: 118,
      gaps: [{ start: '2026-01-06', end: '2026-03-04' }, { start: 'x', bad: true }],
    };
    apiGetMock.mockResolvedValue({
      data: { code: 200, data: { ...fullOptionsBody.data, freq: '1min', minute: minuteBlock } },
    });
    const res = await getMiningConfigOptions({ ...EMPTY_MINING_SELECTION, freq: '1min' });
    expect(res.data?.minute).toEqual({
      ready: true,
      providerDir: '/data/qlib/cn_data_1min',
      error: null,
      coverage: { start: '2026-01-05', end: '2026-08-25' },
      usable: { start: '2026-03-04', end: '2026-08-25' },
      usableDays: 118,
      // 形状不对的段直接丢（宁缺勿错），不发明 start/end
      gaps: [{ start: '2026-01-06', end: '2026-03-04' }],
    });

    apiGetMock.mockResolvedValue({
      data: {
        code: 200,
        data: {
          freq: '1min',
          minute: { ready: false, error: '分钟数据面未构建', usable: null, usable_days: null },
        },
      },
    });
    const missing = await getMiningConfigOptions({ ...EMPTY_MINING_SELECTION, freq: '1min' });
    expect(missing.data?.minute).toMatchObject({
      ready: false,
      error: '分钟数据面未构建',
      usable: null,
      usableDays: 0,
      gaps: [],
    });
  });

  test('snake→camel 归一：guard / coverage / 分段 / scopes；validation=null 保留', async () => {
    const res = await getMiningConfigOptions();
    expect(res.success).toBe(true);
    expect(res.data).toEqual({
      datasets: [
        { key: 'features_daily', label: '日线因子宽表' },
        { key: 'l1_factors', label: 'L1 因子库' },
        { key: 'l2_factors', label: 'L2 微观结构因子' },
      ],
      coverage: { start: '2016-01-04', end: '2026-10-09' },
      freqs: [
        { value: 'day', label: '日线（默认）' },
        { value: '1min', label: '分钟 1min（仅 A 股）' },
      ],
      freq: 'day',
      minute: null,
      backtestEndGuard: '2026-10-08',
      defaults: {
        mining: { start: '2021-10-08', end: '2025-10-08' },
        validation: { start: '2025-10-08', end: '2026-10-08' },
      },
      windows: {
        mining: { start: '2021-10-08', end: '2025-10-08' },
        validation: null,
        isDefault: false,
      },
      segments: {
        train: { start: '2021-10-08', end: '2024-06-24' },
        valid: { start: '2024-06-24', end: '2025-10-08' },
        test: { start: '2025-10-08', end: '2025-10-08' },
      },
      scopes: ['a_share'],
    });
  });

  test('字段缺失/类型不符 → null / 空数组（不伪造默认值）', async () => {
    apiGetMock.mockResolvedValue({ data: { code: 200, data: {} } });
    const res = await getMiningConfigOptions();
    expect(res.success).toBe(true);
    expect(res.data).toEqual({
      datasets: [],
      freqs: [],
      freq: '',
      minute: null,
      coverage: null,
      backtestEndGuard: null,
      defaults: { mining: null, validation: null },
      windows: { mining: null, validation: null, isDefault: true },
      segments: { train: undefined, valid: undefined, test: undefined },
      scopes: [],
    });
  });

  test('400（非法组合）：detail 原文上屏，不返回半截数据', async () => {
    apiGetMock.mockRejectedValue({
      response: { data: { detail: '因子检验区间起点须晚于挖掘区间终点' } },
    });
    const res = await getMiningConfigOptions({
      miningStart: '2021-01-04',
      miningEnd: '2023-12-31',
      validStart: '2022-01-01',
      validEnd: '2025-12-31',
      datasets: null,
    });
    expect(res.success).toBe(false);
    expect(res.error).toBe('因子检验区间起点须晚于挖掘区间终点');
    expect(res.data).toBeUndefined();
  });
});
