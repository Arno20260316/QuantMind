/**
 * 变体台账 tab（T-FE-09）—— 六条不能坏的行为线：
 *
 * 1. **缺失一律「—」**：无观测行的 ICIR/有效日/ρ/状态列全部 `—`，绝不渲染 0；
 *    台账文件缺席 = 显式空态文案（不是错误、不是空白表）。
 * 2. **不可验证永不判失败**：insufficient_days → 「有效日不足」，且不出现
 *    「冷却中」徽章（判定只认 ok）。
 * 3. **冷却按 (变体, 池) 键控**：cooling_active 行显「冷却中」+ 冷却截止。
 * 4. **过滤只管可见行**：勾「仅显示有观测」后空骨架行收起，有观测行仍在。
 * 5. **作用域随页**：universe 变化 → 重拉且带新池参数；非 A 股显链路说明。
 * 6. **失败如实上屏**：接口 success=false 显错误，不静默空态。
 */
import React from 'react';
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { VariantLedgerTab } from '../VariantLedgerTab';
import type {
  VariantLedgerRow,
  VariantLedgerStat,
  VariantLedgerView,
} from '../../services-v2/api';

const { getVariantLedgerMock } = vi.hoisted(() => ({ getVariantLedgerMock: vi.fn() }));

vi.mock('../../services-v2/api', () => ({ getVariantLedger: getVariantLedgerMock }));

const ok = <T,>(data: T) => ({ success: true as const, data });

function mkRow(
  over: Partial<VariantLedgerRow> & Pick<VariantLedgerRow, 'key' | 'name'>,
): VariantLedgerRow {
  return {
    family: '反转',
    formulation: 'expr(x)',
    baselineKey: null,
    editType: null,
    coolable: false,
    observationsCount: 0,
    latest: null,
    latestStat: null,
    cooling: [],
    coolingActive: null,
    ...over,
  };
}

function mkView(over: Partial<VariantLedgerView> = {}): VariantLedgerView {
  return {
    available: true,
    schemaVersion: 1,
    updatedAt: '2026-10-10T12:00:00Z',
    universe: 'all_a',
    constants: { cooldownDays: 90, highConfDays: 40, coolIcirFloor: 0.3, maxObservations: 24 },
    cooling: [],
    coolingActive: [],
    variants: [],
    ...over,
  };
}

const STAT_OK: VariantLedgerStat = {
  horizon: 5,
  status: 'ok',
  icIr: 0.1,
  icMean: 0.01,
  nDays: 60,
  basis: ['reversal_20d'],
  nBasis: 1,
  maxBasisAbsCorr: 0.42,
};

const COOLING_ENTRY = {
  variantKey: 'reversal_60d',
  universe: 'all_a',
  baselineKey: 'reversal_20d',
  since: '2026-01-01T00:00:00Z',
  until: '2026-04-01T00:00:00Z',
  evidence: { residualIcIr: 0.1, nDays: 60, horizon: 5, windowEnd: '2024-12-31' },
};

/** 有观测且冷却中的变体 + 父本行（供名字解析）。 */
function seededRows(): VariantLedgerRow[] {
  return [
    mkRow({
      key: 'reversal_60d',
      name: '反转60日',
      family: '反转',
      formulation: 'ts_rank(-ret, 60)',
      baselineKey: 'reversal_20d',
      editType: '调窗',
      coolable: true,
      observationsCount: 1,
      latest: {
        windowStart: '2024-01-01',
        windowEnd: '2024-12-31',
        universe: 'all_a',
        bestHorizon: 5,
        at: '2026-01-01T00:00:00Z',
        horizons: [STAT_OK],
      },
      latestStat: STAT_OK,
      cooling: [COOLING_ENTRY],
      coolingActive: true,
    }),
    mkRow({
      key: 'reversal_20d',
      name: '反转20日',
      baselineKey: null,
    }),
  ];
}

function renderTab(market = 'a_share', universe = 'all_a') {
  return render(<VariantLedgerTab market={market} universe={universe} />);
}

beforeEach(() => {
  getVariantLedgerMock.mockReset();
  getVariantLedgerMock.mockResolvedValue(ok(mkView()));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('变体台账：缺失纪律与空态', () => {
  test('无观测行指标全「—」；台账缺席显空态文案而不是错误', async () => {
    getVariantLedgerMock.mockResolvedValue(
      ok(
        mkView({
          available: false,
          updatedAt: null,
          variants: [
            mkRow({
              key: 'reversal_60d',
              name: '反转60日',
              baselineKey: 'reversal_20d',
              editType: '调窗',
              coolable: true,
            }),
            mkRow({ key: 'reversal_5d', name: '反转5日' }),
          ],
        }),
      ),
    );
    renderTab();
    await screen.findByText('反转60日');

    expect(screen.getByText(/尚无记录/)).toBeTruthy();
    const row = screen.getByTestId('ledger-row-reversal_60d');
    expect(within(row).getByText('调窗')).toBeTruthy();
    // 残差 ICIR / 有效日 / ρ / 观测窗 / 状态 / 冷却至 全「—」
    expect(within(row).getAllByText('—').length).toBeGreaterThanOrEqual(5);
    expect(within(row).queryByText('0')).toBeNull();
  });

  test('台账缺席时「冷却中」KPI 显共享 0 而不是伪造（池已限定）', async () => {
    getVariantLedgerMock.mockResolvedValue(
      ok(mkView({ available: false, coolingActive: [], variants: [] })),
    );
    renderTab();
    const kpi = await screen.findByTestId('ledger-kpi-active');
    expect(kpi.textContent).toContain('0');
  });
});

describe('变体台账：判定状态与冷却态', () => {
  test('ok+冷却中行显 ICIR/有效日/ρ/「冷却中」徽章与冷却截止；父本显名字', async () => {
    getVariantLedgerMock.mockResolvedValue(
      ok(
        mkView({
          cooling: [COOLING_ENTRY],
          coolingActive: ['reversal_60d'],
          variants: seededRows(),
        }),
      ),
    );
    renderTab();
    const row = await screen.findByTestId('ledger-row-reversal_60d');

    expect(within(row).getByText('0.100')).toBeTruthy(); // 残差 ICIR 3 位
    expect(within(row).getByText('60')).toBeTruthy(); // 有效日
    expect(within(row).getByText('0.420')).toBeTruthy(); // ρ(父本)
    expect(within(row).getByText('冷却中')).toBeTruthy();
    expect(within(row).getByText(/2026/)).toBeTruthy(); // 冷却至
    // 父本列显父本名（另有父本自己的行，故 ≥2 处）
    expect(screen.getAllByText('反转20日').length).toBeGreaterThanOrEqual(2);
    // KPI：已观测 1 / 2
    expect(screen.getByTestId('ledger-kpi-observed').textContent).toContain('1 / 2');
    expect(screen.getByTestId('ledger-kpi-active').textContent).toContain('1');
  });

  test('insufficient_days 显「有效日不足」且不出现冷却徽章（不可验证永不判）', async () => {
    const insuff: VariantLedgerStat = { ...STAT_OK, status: 'insufficient_days', nDays: 12 };
    getVariantLedgerMock.mockResolvedValue(
      ok(
        mkView({
          variants: [
            mkRow({
              key: 'reversal_60d',
              name: '反转60日',
              coolable: true,
              baselineKey: 'reversal_20d',
              observationsCount: 1,
              latest: {
                windowStart: '2024-06-01',
                windowEnd: '2024-12-31',
                universe: 'all_a',
                bestHorizon: 5,
                at: null,
                horizons: [insuff],
              },
              latestStat: insuff,
              coolingActive: false,
            }),
          ],
        }),
      ),
    );
    renderTab();
    const row = await screen.findByTestId('ledger-row-reversal_60d');

    expect(within(row).getByText('有效日不足')).toBeTruthy();
    expect(within(row).queryByText('冷却中')).toBeNull();
  });

  test('范围未限定池时「冷却中」不可判定显「—」（不臆造全池并集）', async () => {
    getVariantLedgerMock.mockResolvedValue(
      ok(
        mkView({
          universe: null,
          cooling: [COOLING_ENTRY],
          coolingActive: null,
          // 池未限定时后端逐行 cooling_active=null，界面不得凭条目自判「生效」
          variants: seededRows().map((r) => ({ ...r, coolingActive: null })),
        }),
      ),
    );
    renderTab('a_share', '');
    const row = await screen.findByTestId('ledger-row-reversal_60d');

    expect(within(row).queryByText('冷却中')).toBeNull();
    expect(screen.getByTestId('ledger-kpi-active').textContent).toContain('—');
  });
});

describe('变体台账：过滤与下钻', () => {
  test('勾「仅显示有观测」收起空骨架行，有观测行仍在', async () => {
    getVariantLedgerMock.mockResolvedValue(ok(mkView({ variants: seededRows() })));
    renderTab();
    await screen.findByText('反转60日');

    expect(screen.getByTestId('ledger-row-reversal_20d')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('仅显示有观测的行'));
    expect(screen.queryByTestId('ledger-row-reversal_20d')).toBeNull();
    expect(screen.getByTestId('ledger-row-reversal_60d')).toBeTruthy();
  });

  test('行展开显示逐周期残差表与冷却记录', async () => {
    getVariantLedgerMock.mockResolvedValue(
      ok(
        mkView({
          cooling: [COOLING_ENTRY],
          coolingActive: ['reversal_60d'],
          variants: seededRows(),
        }),
      ),
    );
    renderTab();
    await screen.findByText('反转60日');

    fireEvent.click(screen.getByTestId('ledger-toggle-reversal_60d'));
    const detail = screen.getByTestId('ledger-detail-reversal_60d');
    expect(within(detail).getByText('5 日')).toBeTruthy(); // 周期
    expect(within(detail).getByText(/冷却记录/)).toBeTruthy();
    expect(within(detail).getAllByText(/2024-12-31/).length).toBeGreaterThanOrEqual(1); // 证据窗
  });
});

describe('变体台账：作用域与失败面', () => {
  test('universe 变化重拉并带新池参数；非 A 股显链路说明', async () => {
    getVariantLedgerMock.mockResolvedValue(ok(mkView({ variants: seededRows() })));
    const { rerender } = renderTab('a_share', 'all_a');
    await screen.findByText('反转60日');
    expect(getVariantLedgerMock).toHaveBeenCalledWith({ universe: 'all_a' });

    getVariantLedgerMock.mockResolvedValue(ok(mkView({ universe: 'csi300', variants: [] })));
    rerender(<VariantLedgerTab market="a_share" universe="csi300" />);
    await waitFor(() =>
      expect(getVariantLedgerMock).toHaveBeenLastCalledWith({ universe: 'csi300' }),
    );

    rerender(<VariantLedgerTab market="us_stock" universe="csi300" />);
    expect(screen.getByText(/A 股统计派链路/)).toBeTruthy();
  });

  test('接口失败如实上屏错误', async () => {
    getVariantLedgerMock.mockResolvedValue({ success: false, error: '变体台账读取失败' });
    renderTab();
    await screen.findByText('变体台账读取失败');
  });
});
