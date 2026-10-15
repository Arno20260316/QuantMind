/**
 * 明细图表数值净化契约（2026-10-15，f13_0020/f13_1040 事故）。
 *
 * 后端快照净化后 `quantile_mean` / `turnover_series` 可含 null（缺失，来自整列 NaN 的
 * 全退化因子）。图表必须**不画柱 + 显示「—」**，绝不折算成 0——站内铁律「缺失一律
 * —，绝不显示成 0」（JS 的 `null * 100` / `?? 0` 都会静默折成 0，这里逐条钉死）。
 */
import { describe, test, expect } from 'vitest';
import { quantileBarOption, quantileSpread, turnoverOption } from '../legacyCharts';
import type { FactorDetail } from '../../../../types/factorReport';

const mkDetail = (over: Partial<FactorDetail> = {}): FactorDetail => ({
  factor: 'f13_1040',
  horizon: '5',
  empty: false,
  dates: ['20260105', '20260106'],
  quantile_mean: [0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01],
  quantile_curves: [[1, 1.01], [1, 1.02]],
  ls_curve: [1, 1.01],
  ic_series: [0.01, null],
  ic_rolling: [null, null],
  ic_mean: 0.01,
  ic_std: 0.02,
  turnover_dates: ['20260105', '20260106'],
  turnover_series: [0.3, 0.4],
  turnover_mean: 0.35,
  coverage_mean: 0.9,
  n_dates: 2,
  start: '20260105',
  end: '20260106',
  ...over,
});

describe('quantileSpread —— Q10−Q1 价差', () => {
  test('正常因子返回 (Q10−Q1)×100', () => {
    // Arrange
    const d = mkDetail();
    // Act
    const s = quantileSpread(d);
    // Assert
    expect(s).toBe(0.9);
  });

  test('全退化因子（quantile_mean 全 null）返回 null，不折算成 0', () => {
    // Arrange：f13_1040 的形态——整列 NaN 经快照净化后逐位 null
    const d = mkDetail({ quantile_mean: Array.from({ length: 10 }, () => null) });
    // Act / Assert
    expect(quantileSpread(d)).toBeNull();
  });

  test('任一端缺失即 null（Q1 缺 / Q10 缺各验一次）', () => {
    const base = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01];
    const q1Null = [...base];
    q1Null[0] = null;
    const q10Null = [...base];
    q10Null[9] = null;
    expect(quantileSpread(mkDetail({ quantile_mean: q1Null }))).toBeNull();
    expect(quantileSpread(mkDetail({ quantile_mean: q10Null }))).toBeNull();
  });
});

describe('quantileBarOption —— 分位平均前瞻收益柱', () => {
  test('正常因子柱值 = 分位均值 ×100', () => {
    const opt = quantileBarOption(mkDetail());
    expect(opt.series[0].data.map((x: { value: number | null }) => x.value)).toEqual(
      [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1],
    );
  });

  test('全退化因子柱值全为 null（不画柱、不标数），绝不出现 0', () => {
    const opt = quantileBarOption(mkDetail({ quantile_mean: Array.from({ length: 10 }, () => null) }));
    const vals = opt.series[0].data.map((x: { value: number | null }) => x.value);
    expect(vals.every((v: number | null) => v === null)).toBe(true);
  });

  test('tooltip 对缺失值显示「—」', () => {
    const opt = quantileBarOption(mkDetail());
    expect(opt.tooltip.valueFormatter(null)).toBe('—');
    expect(opt.tooltip.valueFormatter(0.9)).toBe('+0.9%');
    expect(opt.tooltip.valueFormatter(-0.9)).toBe('-0.9%');
  });
});

describe('turnoverOption —— 换手序列', () => {
  test('缺失日给 null（此前 null.toFixed 会直接抛异常）', () => {
    const d = mkDetail({ turnover_series: [null, 0.4] });
    expect(() => turnoverOption(d)).not.toThrow();
    const opt = turnoverOption(d);
    expect(opt.series[0].data).toEqual([null, 0.4]);
  });

  test('tooltip 对缺失值显示「—」', () => {
    const opt = turnoverOption(mkDetail());
    expect(opt.tooltip.valueFormatter(null)).toBe('—');
    expect(opt.tooltip.valueFormatter(0.3)).toBe('30%');
  });
});
