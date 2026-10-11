/**
 * 变体台账 API 桥（T-FE-09）映射契约：
 * - snake→camel 归一（骨架行 / 观测 / 逐周期 stat / 冷却判据）；
 * - **缺失一律 null**（评审 code-review CR3）：`horizon`/`nBasis` 曾
 *   `Number(?? 0)` 伪造 0——缺失与真值 0 是两回事，界面按 null 落「—」；
 * - **run_id 不出网**（评审 security-F2）：后端投影层已脱敏，前端映射
 *   也不得重建该字段——整棵映射结果不得含任何原始 run_id 值。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: getMock },
}));

import { getVariantLedger } from '../api';

const okRes = (data: unknown) => ({ data: { code: 200, data } });

const RUN_ID = 'run-secret-hx9';

const RAW = {
  available: true,
  schema_version: 1,
  updated_at: '2026-10-10T12:00:00Z',
  universe: 'all_a',
  constants: {
    cooldown_days: 90,
    high_conf_days: 40,
    cool_icir_floor: 0.3,
    max_observations: 24,
  },
  cooling: [
    {
      variant_key: 'reversal_60d',
      universe: 'all_a',
      baseline_key: 'reversal_20d',
      since: '2026-10-01T00:00:00Z',
      until: '2026-12-30T00:00:00Z',
      // 即便后端脱敏被回归回退、run_id 又漏进响应，映射也不得让它落到类型上
      run_id: RUN_ID,
      evidence: {
        residual_ic_ir: -0.12,
        n_days: 44,
        horizon: 5,
        window_end: '2026-09-30',
        run_id: RUN_ID,
      },
    },
  ],
  cooling_active: ['reversal_60d'],
  variants: [
    {
      key: 'reversal_60d',
      name: '反转60日',
      family: '反转',
      formulation: 'ts_rank(-ret, 60)',
      baseline_key: 'reversal_20d',
      edit_type: '调窗',
      coolable: true,
      observations_count: 1,
      latest: {
        window_start: '2024-01-01',
        window_end: '2024-12-31',
        run_id: RUN_ID,
        universe: 'all_a',
        best_horizon: 5,
        at: '2026-10-10T12:00:00Z',
        horizons: [
          {
            horizon: 5,
            status: 'ok',
            ic_ir: 0.4,
            ic_mean: 0.012,
            n_days: 120,
            basis: ['reversal_20d'],
            n_basis: 1,
            max_basis_abs_corr: 0.42,
          },
          // 老后端/降级路径：horizon 与 n_basis 缺失 → null，不得伪造 0
          { status: 'unavailable' },
        ],
      },
      latest_stat: {
        horizon: 5,
        status: 'ok',
        ic_ir: 0.4,
        ic_mean: 0.012,
        n_days: 120,
        basis: ['reversal_20d'],
        n_basis: 1,
        max_basis_abs_corr: 0.42,
      },
      cooling: [],
      cooling_active: true,
    },
    // 无观测骨架行：latest/latest_stat null（界面显「—」）
    { key: 'reversal_20d', name: '反转20日', family: '反转', formulation: 'x' },
  ],
};

beforeEach(() => {
  getMock.mockReset();
});

describe('请求形态', () => {
  test('URL = /alpha-agent/variant-ledger（universe 给值才带查询串）', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    await getVariantLedger({ universe: 'all_a' });
    expect(getMock.mock.calls[0][0]).toBe('/alpha-agent/variant-ledger?universe=all_a');

    getMock.mockClear();
    getMock.mockResolvedValue(okRes(RAW));
    await getVariantLedger({});
    expect(getMock.mock.calls[0][0]).toBe('/alpha-agent/variant-ledger');
  });
});

describe('映射契约：缺失 null + run_id 不出网', () => {
  test('逐周期 stat：horizon/nBasis 缺失 → null；真值原样', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    const d = (await getVariantLedger({ universe: 'all_a' })).data!;

    const [okStat, degraded] = d.variants[0].latest!.horizons;
    expect(okStat.horizon).toBe(5);
    expect(okStat.nBasis).toBe(1);
    expect(okStat.status).toBe('ok');

    expect(degraded.horizon).toBeNull();
    expect(degraded.nBasis).toBeNull();
    expect(degraded.status).toBe('unavailable');
    expect(degraded.icIr).toBeNull();
  });

  test('观测与冷却判据均不含 run_id（原值不出现在整棵映射结果里）', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    const d = (await getVariantLedger({ universe: 'all_a' })).data!;

    const latest = d.variants[0].latest!;
    expect(Object.keys(latest)).not.toContain('runId');
    expect(Object.keys(d.cooling[0])).not.toContain('runId');
    expect(Object.keys(d.cooling[0].evidence!)).not.toContain('runId');
    expect(JSON.stringify(d)).not.toContain(RUN_ID);
  });

  test('观测窗口/池/入账时间归一；无观测骨架行 latest=null', async () => {
    getMock.mockResolvedValue(okRes(RAW));
    const d = (await getVariantLedger({ universe: 'all_a' })).data!;

    expect(d.variants[0].latest).toMatchObject({
      windowStart: '2024-01-01',
      windowEnd: '2024-12-31',
      universe: 'all_a',
      bestHorizon: 5,
      at: '2026-10-10T12:00:00Z',
    });
    expect(d.variants[1].latest).toBeNull();
    expect(d.variants[1].latestStat).toBeNull();

    expect(d.cooling[0].evidence).toEqual({
      residualIcIr: -0.12,
      nDays: 44,
      horizon: 5,
      windowEnd: '2026-09-30',
    });
    expect(d.coolingActive).toEqual(['reversal_60d']);
  });

  test('payload 缺席（data={}）不炸：空骨架 + available=false', async () => {
    getMock.mockResolvedValue(okRes({}));
    const res = await getVariantLedger({});
    expect(res.success).toBe(true);
    const d = res.data!;
    expect(d.available).toBe(false);
    expect(d.variants).toEqual([]);
    expect(d.cooling).toEqual([]);
  });
});
