/**
 * 恒生行业热力图下钻卡（HkHeatmapDrillCard）状态机单测。
 *
 * 钉住的行为与口径契约：
 * - L1（行业）↔ L2（成分股）两层切换 + 面包屑返回；下钻失败回退 L1 不卡死
 * - 面积口径如实披露：L1「行业成交额」/ L2「当日成交额」（港股无日频市值列）
 * - 南向缺失（null）显示「南向 —」，绝不冒充 0；重名标的用「名称·代码」消歧
 * - renderKey 随层/着色口径变化（触发图表重绘的既有契约）
 * - L2 色块点击 → onStockClick(symbol, 图名)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { HkHeatmapDrillCard } from '../components/HkHeatmapDrillCard';
import { getHeatmapDrill, getOpportunitySectors } from '../services/api';
import type { HkHeatmapDrill, HkSectorHeatItem } from '../types';

vi.mock('../services/api', () => ({
  getHeatmapDrill: vi.fn(),
  getOpportunitySectors: vi.fn(),
}));

// 轻量假图表：把 name/回调/口径参数/取色与标签结果全部落到 DOM 属性，便于断言
vi.mock('../../market-analysis-shared/SectorHeatmapChart', () => {
  interface FakeItem {
    name: string;
    value: number;
    pct_change: number;
    extra?: Record<string, unknown>;
  }
  interface FakeProps {
    data?: FakeItem[];
    valueLabel?: string;
    pctLabel?: string;
    renderKey?: string | number;
    onItemClick?: (name: string) => void;
    colorOf?: (item: FakeItem) => string | undefined;
    labelValueOf?: (item: FakeItem) => string;
    tooltipExtraOf?: (item: FakeItem) => string;
  }
  return {
    SectorHeatmapChart: ({
      data = [],
      valueLabel,
      pctLabel,
      renderKey,
      onItemClick,
      colorOf,
      labelValueOf,
      tooltipExtraOf,
    }: FakeProps) => (
      <div
        data-testid="fake-chart"
        data-value-label={valueLabel ?? ''}
        data-pct-label={pctLabel ?? ''}
        data-render-key={String(renderKey)}
      >
        {data.map((it, i) => (
          <button
            key={`${it.name}-${i}`}
            type="button"
            data-testid={`cell-${it.name}`}
            data-color={colorOf ? (colorOf(it) ?? '') : ''}
            data-label={labelValueOf ? labelValueOf(it) : ''}
            data-tip={tooltipExtraOf ? tooltipExtraOf(it) : ''}
            onClick={() => onItemClick?.(it.name)}
          >
            {it.name}
          </button>
        ))}
      </div>
    ),
  };
});

const L1_SECTORS: HkSectorHeatItem[] = [
  { name: '资讯科技业', value: 320.5, pct_change: 1.23, leader: '腾讯控股', leader_pct: 3.1, stock_count: 42 },
  { name: '金融业', value: 210.0, pct_change: -0.45, leader: '汇丰控股', leader_pct: -1.2, stock_count: 30 },
];

function makeDrill(overrides?: Partial<HkHeatmapDrill>): HkHeatmapDrill {
  return {
    name: '资讯科技业',
    trade_date: '2026-10-14',
    items: [
      { symbol: '0700.HK', name: '腾讯控股', pct_change: 1.1, amount_yi: 88.2, sb_chg_d5_pp: 0.12 },
      { symbol: '0005.HK', name: '汇丰控股', pct_change: -0.3, amount_yi: 12.1, sb_chg_d5_pp: null },
    ],
    summary: { stock_count: 2, total_amount_yi: 100.3, truncated: false },
    ...overrides,
  };
}

const mockedDrill = vi.mocked(getHeatmapDrill);
const mockedSectors = vi.mocked(getOpportunitySectors);

beforeEach(() => {
  vi.clearAllMocks();
  mockedDrill.mockResolvedValue(makeDrill());
  mockedSectors.mockResolvedValue({
    trade_date: '2026-10-14',
    items: [
      {
        name: '资讯科技业',
        opp_score_median: 82.4,
        opportunity_breadth: 0.4,
        stock_count: 12,
        south_chg_d5_median_pp: 0.1,
        top_stocks: [],
      },
    ],
  });
});

describe('HkHeatmapDrillCard · L1 行业层', () => {
  it('渲染行业块与面积口径（行业成交额，不写「市值」）', () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);

    // Arrange 已在 render 之上完成；Assert
    expect(screen.getByTestId('cell-资讯科技业')).toBeInTheDocument();
    expect(screen.getByText('2 个行业')).toBeInTheDocument();
    const chart = screen.getByTestId('fake-chart');
    expect(chart.getAttribute('data-value-label')).toBe('行业成交额');
    expect(chart.getAttribute('data-pct-label')).toBe('平均涨跌幅');
    expect(chart.getAttribute('data-render-key')).toBe('L1-pct');
    expect(screen.getByText(/面积=当日成交额 · 颜色=涨跌幅/)).toBeInTheDocument();
  });

  it('「涨跌幅」默认口径下不覆盖取色（colorOf 返回 undefined 落回内置档位色）', () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);

    expect(screen.getByTestId('cell-资讯科技业').getAttribute('data-color')).toBe('');
    expect(screen.getByTestId('cell-资讯科技业').getAttribute('data-label')).toBe('+1.23%');
  });

  it('切换「机会分」着色 → 按需拉取行业机会榜并按分位着色，未评分行业灰显 —', async () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);

    // Act
    fireEvent.click(screen.getByRole('button', { name: '机会分' }));

    // Assert：按需请求（limit=31 全量行业、min_stocks=3）
    await waitFor(() => expect(mockedSectors).toHaveBeenCalledWith(31, 3));
    await waitFor(() =>
      expect(screen.getByTestId('cell-资讯科技业').getAttribute('data-label')).toBe('机会分 82'),
    );
    // 82.4 分 → 深紫档；金融业不在机会榜 → 灰 + 「机会分 —」
    expect(screen.getByTestId('cell-资讯科技业').getAttribute('data-color')).toBe('#7c3aed');
    expect(screen.getByTestId('cell-金融业').getAttribute('data-color')).toBe('#cbd5e1');
    expect(screen.getByTestId('cell-金融业').getAttribute('data-label')).toBe('机会分 —');
    expect(screen.getByTestId('fake-chart').getAttribute('data-render-key')).toBe('L1-score');
  });
});

describe('HkHeatmapDrillCard · L1 → L2 下钻与返回', () => {
  it('点击行业块下钻成分股：面包屑 / 摘要 / 面积与标签口径切换', async () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);

    // Act
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));

    // Assert
    await waitFor(() => expect(mockedDrill).toHaveBeenCalledWith('资讯科技业'));
    expect(await screen.findByTestId('cell-腾讯控股')).toBeInTheDocument();
    expect(screen.getByText('恒生行业')).toBeInTheDocument(); // 返回按钮（面包屑）
    expect(screen.getByText(/（成分股 · 面积=成交额）/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-14 · 2 只 · 合计 100\.3 亿/)).toBeInTheDocument();
    const chart = screen.getByTestId('fake-chart');
    expect(chart.getAttribute('data-value-label')).toBe('当日成交额');
    expect(chart.getAttribute('data-pct-label')).toBe('当日涨跌幅');
    expect(chart.getAttribute('data-render-key')).toBe('资讯科技业-pct');
  });

  it('点击成分股色块 → onStockClick(symbol, 图名)', async () => {
    const onStockClick = vi.fn();
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} onStockClick={onStockClick} />);
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));
    await screen.findByTestId('cell-腾讯控股');

    // Act
    fireEvent.click(screen.getByTestId('cell-腾讯控股'));

    // Assert
    expect(onStockClick).toHaveBeenCalledWith('0700.HK', '腾讯控股');
  });

  it('重名标的用「名称·代码」消歧，点击回传正确代码', async () => {
    const onStockClick = vi.fn();
    mockedDrill.mockResolvedValue(
      makeDrill({
        items: [
          { symbol: '0019.HK', name: '太古股份', pct_change: 0.5, amount_yi: 3.2, sb_chg_d5_pp: 0.01 },
          { symbol: '0087.HK', name: '太古股份', pct_change: -0.2, amount_yi: 1.1, sb_chg_d5_pp: null },
        ],
      }),
    );
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} onStockClick={onStockClick} />);
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));
    await screen.findByTestId('cell-太古股份·0087.HK');

    // Act
    fireEvent.click(screen.getByTestId('cell-太古股份·0019.HK'));

    // Assert
    expect(onStockClick).toHaveBeenCalledWith('0019.HK', '太古股份·0019.HK');
  });

  it('面包屑「恒生行业」返回 L1（清空下钻）', async () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));
    await screen.findByTestId('cell-腾讯控股');

    // Act
    fireEvent.click(screen.getByText('恒生行业'));

    // Assert
    expect(await screen.findByTestId('cell-资讯科技业')).toBeInTheDocument();
    expect(screen.queryByText('恒生行业')).toBeNull();
    expect(screen.getByTestId('fake-chart').getAttribute('data-render-key')).toBe('L1-pct');
  });

  it('下钻失败（如 404）回退 L1，不卡在空 L2', async () => {
    mockedDrill.mockRejectedValue(new Error('资讯科技业 不存在'));
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);

    // Act
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));

    // Assert
    await waitFor(() => expect(mockedDrill).toHaveBeenCalledWith('资讯科技业'));
    await waitFor(() => expect(screen.queryByText('恒生行业')).toBeNull());
    expect(screen.getByTestId('cell-资讯科技业')).toBeInTheDocument();
    expect(screen.getByTestId('fake-chart').getAttribute('data-render-key')).toBe('L1-pct');
  });

  it('截断时摘要如实标注「（按成交额截断）」', async () => {
    mockedDrill.mockResolvedValue(
      makeDrill({ summary: { stock_count: 300, total_amount_yi: 512.8, truncated: true } }),
    );
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));

    expect(await screen.findByText(/（按成交额截断）/)).toBeInTheDocument();
  });
});

describe('HkHeatmapDrillCard · L2 南向口径', () => {
  it('南向 5 日模式：有值显示 pp、缺失显示 —（灰），tooltip 标注非港股通标的', async () => {
    render(<HkHeatmapDrillCard sectors={L1_SECTORS} />);
    fireEvent.click(screen.getByTestId('cell-资讯科技业'));
    await screen.findByTestId('cell-腾讯控股');

    // Act
    fireEvent.click(screen.getByRole('button', { name: '南向5日变化' }));

    // Assert
    const tencent = screen.getByTestId('cell-腾讯控股');
    expect(tencent.getAttribute('data-label')).toBe('南向 +0.12pp');
    expect(tencent.getAttribute('data-color')).toBe('#ef4444'); // 增持红
    const hsbc = screen.getByTestId('cell-汇丰控股');
    expect(hsbc.getAttribute('data-label')).toBe('南向 —'); // null 不冒充 0
    expect(hsbc.getAttribute('data-color')).toBe('#cbd5e1');
    expect(hsbc.getAttribute('data-tip')).toContain('非港股通标的');
    expect(screen.getByTestId('fake-chart').getAttribute('data-render-key')).toBe('资讯科技业-south');
  });
});
