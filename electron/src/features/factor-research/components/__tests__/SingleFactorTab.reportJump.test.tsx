/**
 * 单因子页「因子报告」跳转入口的行为线（2026-10-15）。
 *
 * 单因子页看的因子来自**私人库/经典库快照目录**，而因子报告是一组**另一个脚本
 * 按自己的清单生成的数据集快照**——两层命名空间之间只靠两个字段对得上：
 * 后端读时现算的 `source_library`（= 报告数据集名）与报告快照里的同名因子。
 * 这里守的是几件**点错了不会报错、只会静默指错路**的性质：
 *
 * 1. 入口必须带**这个因子自己的**来源库去报告页。拿别的数据集去问，报告页只会
 *    显示「该因子不在快照里」，不会告诉你库选错了；
 * 2. 只在来源库**真有报告快照**时渲染。没快照的库点进去是一屏重建横幅——
 *    入口本身就是个谎；
 * 3. 经典库（无来源库）既不渲染入口，也**不发**数据集清单请求（省一次无谓往返，
 *    且保证入口的出现不依赖请求时序）；
 * 4. 清单取不到 → 没有入口（不是错误面：单因子页本身完好）。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SingleFactorTab } from '../SingleFactorTab';
import type { FactorDetail } from '../../types/factorResearch';

const { detailMock, datasetsMock } = vi.hoisted(() => ({
  detailMock: vi.fn(),
  datasetsMock: vi.fn(),
}));

vi.mock('../../services/factorResearchService', () => ({ getFactorDetail: detailMock }));
vi.mock('../../services/factorReportService', () => ({ getFactorDatasets: datasetsMock }));

// jsdom 无 canvas：图表整体换成占位（与 SingleFactorTab.test.tsx 同口径）。
vi.mock('../../../../components/common/EChartsChart', () => ({
  EChartsChart: () => <div data-testid="chart" />,
}));

const RANGE = { start: '2026-01-01', end: '2026-10-08' };

function mkDetail(over: Partial<FactorDetail> = {}): FactorDetail {
  return {
    code: 'turn_1',
    name_cn: '换手率',
    display_name: '',
    l1: 'L1 因子',
    l2: 'l1_factors',
    direction: 1,
    description: '',
    formula: '',
    wind_source: 'QuantDB 6_ml_datasets/l1_factors',
    source_library: 'l1_factors',
    available: true,
    env_tag: '',
    time_tag: '',
    range: { start: '2026-01-01', end: '2026-10-08', n_months: 10 },
    variants: [],
    benchmarks: [],
    nscan: [],
    ic: [],
    ic_kpi: {},
    stocks: [],
    stocks_date: null,
    stocks_stale: false,
    industry_dist: [],
    cap_dist: [],
    ...over,
  };
}

function renderTab(onOpenReport = vi.fn()) {
  render(
    <SingleFactorTab
      code="turn_1"
      range={RANGE}
      dataset="private"
      onOpenReport={onOpenReport}
    />,
  );
  return onOpenReport;
}

beforeEach(() => {
  detailMock.mockReset();
  datasetsMock.mockReset();
  datasetsMock.mockResolvedValue({
    default: 'alpha_library',
    items: [
      { dataset: 'l1_factors', label: 'L1 因子（量价/换手/波动等基础因子）', available: true },
      { dataset: 'alpha_library', label: 'Alpha 库', available: true },
      { dataset: 'features_daily', label: '每日特征（技术+估值）', available: false },
    ],
  });
});

describe('SingleFactorTab：「因子报告」跳转入口', () => {
  test('来源库有快照：入口渲染，点击回传 (来源库, 因子名)', async () => {
    detailMock.mockResolvedValue(mkDetail());
    const onOpenReport = renderTab();

    const btn = await screen.findByTestId('open-factor-report');
    expect(btn.getAttribute('title')).toContain('L1 因子');
    fireEvent.click(btn);

    expect(onOpenReport).toHaveBeenCalledWith('l1_factors', 'turn_1');
  });

  test('来源库没有报告快照：不渲染入口，也不误跳到别的数据集', async () => {
    detailMock.mockResolvedValue(
      mkDetail({ source_library: 'features_daily', wind_source: 'QuantDB 6_ml_datasets/features_daily' }),
    );
    renderTab();

    // 先等详情主体上屏（入口的出现与否都发生在它之后），再断言没有入口。
    await screen.findByText('因子定义');
    await waitFor(() => expect(datasetsMock).toHaveBeenCalled());
    expect(screen.queryByTestId('open-factor-report')).toBeNull();
  });

  test('经典库因子（无来源库）：不发清单请求、不渲染入口', async () => {
    detailMock.mockResolvedValue(
      mkDetail({ source_library: '', wind_source: 'AShareEODPrices.S_DQ_ADJCLOSE' }),
    );
    renderTab();

    await screen.findByText('因子定义');
    expect(screen.queryByTestId('open-factor-report')).toBeNull();
    expect(datasetsMock).not.toHaveBeenCalled();
  });

  test('清单请求失败：没有入口（单因子页本身不受影响）', async () => {
    datasetsMock.mockRejectedValue(new Error('接口失败 500'));
    detailMock.mockResolvedValue(mkDetail());
    renderTab();

    await screen.findByText('因子定义');
    await waitFor(() => expect(datasetsMock).toHaveBeenCalled());
    expect(screen.queryByTestId('open-factor-report')).toBeNull();
  });

  test('清单首次失败不落状态：换因子会重试，恢复后入口照常出现', async () => {
    // 一次失败（引擎重启/超时）不该让这个库在整个会话里都没有入口。
    datasetsMock.mockRejectedValueOnce(new Error('引擎重启中'));
    detailMock.mockImplementation((c: string) => Promise.resolve(mkDetail({ code: c })));
    const onOpenReport = vi.fn();
    const view = (c: string) => (
      <SingleFactorTab code={c} range={RANGE} dataset="private" onOpenReport={onOpenReport} />
    );
    const { rerender } = render(view('turn_1'));

    await screen.findByText('因子定义');
    await waitFor(() => expect(datasetsMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('open-factor-report')).toBeNull();

    rerender(view('amt_log'));
    const btn = await screen.findByTestId('open-factor-report');
    fireEvent.click(btn);

    expect(datasetsMock).toHaveBeenCalledTimes(2);
    expect(onOpenReport).toHaveBeenCalledWith('l1_factors', 'amt_log');
  });

  test('跨库换因子：入口改指新库，不停留在旧库', async () => {
    detailMock.mockImplementation((c: string) =>
      Promise.resolve(
        c === 'turn_1'
          ? mkDetail({ code: c })
          : mkDetail({
              code: c,
              wind_source: 'QuantDB 6_ml_datasets/alpha_library',
              source_library: 'alpha_library',
            }),
      ),
    );
    const onOpenReport = vi.fn();
    const view = (c: string) => (
      <SingleFactorTab code={c} range={RANGE} dataset="private" onOpenReport={onOpenReport} />
    );
    const { rerender } = render(view('turn_1'));

    fireEvent.click(await screen.findByTestId('open-factor-report'));
    expect(onOpenReport).toHaveBeenLastCalledWith('l1_factors', 'turn_1');

    rerender(view('a101_001'));
    // 旧卡面残留期间按钮是被藏起来的（不许对着上一个因子点跳转）；
    // 等新因子的按钮出现（title 带新因子名）再点。
    await waitFor(() =>
      expect(screen.getByTestId('open-factor-report').getAttribute('title')).toContain('a101_001'),
    );
    fireEvent.click(screen.getByTestId('open-factor-report'));

    expect(onOpenReport).toHaveBeenLastCalledWith('alpha_library', 'a101_001');
    // 清单只问一次：一次成功即覆盖所有库（该端点逐数据集扫盘，不便宜）。
    expect(datasetsMock).toHaveBeenCalledTimes(1);
  });
});
