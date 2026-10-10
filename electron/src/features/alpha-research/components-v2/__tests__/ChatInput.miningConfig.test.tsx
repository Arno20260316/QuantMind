import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatInput } from '../ChatInput';

/**
 * 挖掘配置面板契约（移植 P1/P1.5，仅 A 股）：
 *
 * - 打开面板即拉 `/mining/config-options`；**默认态不下发任何参数**
 *   （提交不带 miningConfig 键——老路径零回归）；
 * - 切「自定义区间」以服务端默认窗口预填，预览/提交同一份 payload；
 * - 数据集多选控制喂给 LLM 的特征列：全选=不下发（None=全选），
 *   至少保留一个（全不选等价于全选，是静默反转不是「清空」）；
 * - 服务端 400 原文上屏（非法组合不静默落回默认）；服务端**退让**过的
 *   终点显式说明（qlib t+1 成交，终点须留次日行情）；
 * - 切到非 A 股市场：面板收起、配置清空（A 股窗口不能带去挖港美股）；
 * - **分钟档（P1.5）**：切「分钟 1min」→ 预览带 freq=1min、数据集档位收起、
 *   断层与可用连续块如实上屏；换挡清空旧档窗口；数据面未就绪显式报错；
 *   提交只带 freq（日线档不带该键）。
 */

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  listMarkets: vi.fn(),
  getUniverses: vi.fn(),
  getPoolFactors: vi.fn(),
}));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: mocks.apiGet, post: vi.fn(), delete: vi.fn() },
}));

vi.mock('../../services/alphaAgentService', () => ({
  alphaAgentService: { listMarkets: mocks.listMarkets },
}));

vi.mock('../../services-v2/api', () => ({
  getUniverses: mocks.getUniverses,
  getPoolFactors: mocks.getPoolFactors,
}));

/** /mining/config-options 响应（后端字段原样，snake_case） */
const optionsBody = (
  over: {
    mining?: { start: string; end: string };
    minute?: Record<string, unknown> | null;
  } = {},
) => ({
  data: {
    code: 200,
    data: {
      datasets: [
        { key: 'features_daily', label: '日线因子宽表' },
        { key: 'l1_factors', label: 'L1 因子库' },
      ],
      freqs: [
        { value: 'day', label: '日线（默认）' },
        { value: '1min', label: '分钟 1min（仅 A 股）' },
      ],
      freq: 'day',
      minute: over.minute ?? null,
      coverage: { start: '2016-01-04', end: '2026-10-09' },
      backtest_end_guard: '2026-10-08',
      defaults: {
        mining: { start: '2021-10-08', end: '2025-10-08' },
        validation: { start: '2025-10-08', end: '2026-10-08' },
      },
      windows: {
        mining: over.mining ?? { start: '2021-10-08', end: '2025-10-08' },
        validation: { start: '2025-10-08', end: '2026-10-08' },
        is_default: false,
      },
      segments: {
        train: { start: '2021-10-08', end: '2024-06-24' },
        valid: { start: '2024-06-24', end: '2025-10-08' },
        test: { start: '2025-10-08', end: '2026-10-08' },
      },
      scopes: ['a_share'],
    },
  },
});

/** 分钟档响应：可用连续块 2026-03-04 ~ 2026-08-25 + 一段断层 */
const minuteOptionsBody = (
  over: { ready?: boolean; error?: string | null; gaps?: Array<{ start: string; end: string }> } = {},
) => {
  const body = optionsBody();
  body.data.data.freq = '1min';
  body.data.data.coverage = { start: '2026-01-05', end: '2026-08-25' };
  body.data.data.backtest_end_guard = '2026-08-25';
  body.data.data.defaults = {
    mining: { start: '2026-03-04', end: '2026-08-25' },
    validation: null as unknown as { start: string; end: string },
  };
  body.data.data.windows = {
    mining: { start: '2026-03-04', end: '2026-08-25' },
    validation: null as unknown as { start: string; end: string },
    is_default: true,
  };
  body.data.data.segments = {};
  body.data.data.minute = {
    ready: over.ready ?? true,
    provider_dir: '/data/qlib/cn_data_1min',
    error: over.error ?? null,
    coverage: { start: '2026-01-05', end: '2026-08-25' },
    usable: over.ready === false ? null : { start: '2026-03-04', end: '2026-08-25' },
    usable_days: over.ready === false ? null : 118,
    gaps: over.gaps ?? [{ start: '2026-01-06', end: '2026-03-04' }],
  };
  return body;
};

/**
 * 只认挖掘配置请求：组件挂载时还会拉 `/alpha-agent/algorithms`（算法目录），
 * 取「最后一次 GET」会把那个请求误当成配置预览——按 URL 前缀过滤。
 */
const optionsUrls = () =>
  mocks.apiGet.mock.calls
    .map((call) => String(call?.[0] ?? ''))
    .filter((url) => url.startsWith('/alpha-agent/mining/config-options'));

const lastOptionsUrl = () => {
  const urls = optionsUrls();
  return urls[urls.length - 1] ?? '';
};

const openPanel = async () => {
  fireEvent.click(screen.getByTitle(/挖掘配置|已自定义挖掘频率/));
  // 面板挂载后有 350ms 预览防抖：等配置请求真的发出，而不是等任意 GET
  await waitFor(() => expect(optionsUrls().length).toBeGreaterThan(0));
};

const renderInput = (onSubmit = vi.fn()) => {
  render(<ChatInput inline onSubmit={onSubmit} />);
  return onSubmit;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listMarkets.mockResolvedValue([]);
  mocks.getUniverses.mockResolvedValue({ data: { universes: [] } });
  mocks.apiGet.mockResolvedValue(optionsBody());
});

describe('ChatInput 挖掘配置面板', () => {
  it('默认态：打开即预览（不带任何窗口参数），展示服务端默认窗口/覆盖/可回测末端', async () => {
    renderInput();
    await openPanel();

    expect(lastOptionsUrl()).toBe('/alpha-agent/mining/config-options');
    expect(await screen.findByText('2021-10-08 ~ 2025-10-08')).toBeTruthy();
    expect(screen.getByText('2016-01-04 ~ 2026-10-09')).toBeTruthy();
    expect(screen.getByText('2026-10-08')).toBeTruthy();
    expect(screen.getByText('2021-10-08 ~ 2024-06-24')).toBeTruthy();
    // 数据集：默认全选（不下发）
    expect(screen.getByText('全选（不写参数）')).toBeTruthy();
  });

  it('默认态提交：不带 miningConfig 键（老路径零回归）', async () => {
    const onSubmit = renderInput();
    await openPanel();

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect('miningConfig' in onSubmit.mock.calls[0][0]).toBe(false);
  });

  it('切自定义：以默认窗口预填、预览请求带四端点；提交原样带走', async () => {
    const onSubmit = renderInput();
    await openPanel();

    fireEvent.click(screen.getByText('自定义区间'));
    await waitFor(() =>
      expect(lastOptionsUrl()).toBe(
        '/alpha-agent/mining/config-options?mining_start=2021-10-08&mining_end=2025-10-08' +
          '&valid_start=2025-10-08&valid_end=2026-10-08',
      ),
    );
    // 预填值即服务端默认（输入框里能看到）
    expect(
      (screen.getByLabelText('挖掘区间起点') as HTMLInputElement).value,
    ).toBe('2021-10-08');

    // 改挖掘起点 → 预览跟随（防抖后）
    fireEvent.change(screen.getByLabelText('挖掘区间起点'), {
      target: { value: '2020-06-01' },
    });
    await waitFor(() => expect(lastOptionsUrl()).toContain('mining_start=2020-06-01'));

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect(onSubmit.mock.calls[0][0].miningConfig).toEqual({
      miningStart: '2020-06-01',
      miningEnd: '2025-10-08',
      validStart: '2025-10-08',
      validEnd: '2026-10-08',
      datasets: null,
      freq: null,
    });
  });

  it('数据集多选：取消一个 → 预览与提交带剩余列表；最后一个不可取消', async () => {
    const onSubmit = renderInput();
    await openPanel();
    fireEvent.click(screen.getByText('自定义区间'));

    fireEvent.click(screen.getByText('L1 因子库'));
    await waitFor(() => expect(lastOptionsUrl()).toContain('datasets=features_daily'));
    expect(lastOptionsUrl()).not.toContain('datasets=l1_factors');
    expect(screen.getByText('已选 1/2')).toBeTruthy();

    // 最后一个数据集不可取消（全不选等价于全选，禁止静默反转）
    const last = screen.getByText('日线因子宽表').closest('button') as HTMLButtonElement;
    expect(last).toBeDisabled();
    expect(last.getAttribute('title')).toBe(
      '至少保留一个数据集（全不选等价于全选，没有意义）',
    );

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect(onSubmit.mock.calls[0][0].miningConfig.datasets).toEqual(['features_daily']);

    // 全选回来 → 归一到 null（不写参数）
    fireEvent.click(screen.getByText('L1 因子库'));
    await waitFor(() => expect(screen.getByText('全选（不写参数）')).toBeTruthy());
  });

  it('服务端 400：detail 原文上屏，不显示任何分段（不静默落回默认窗口）', async () => {
    mocks.apiGet.mockResolvedValue({
      data: { code: 200, data: {} },
    });
    mocks.apiGet.mockRejectedValue({
      response: { data: { detail: '因子检验区间起点须晚于挖掘区间终点' } },
    });
    renderInput();
    await openPanel();

    expect(
      await screen.findByText('因子检验区间起点须晚于挖掘区间终点'),
    ).toBeTruthy();
    expect(screen.queryByText(/^train/)).toBeNull();
  });

  it('服务端退让：请求终点晚于可回测末端时显式说明（不是静默改数）', async () => {
    // 请求 2026-10-09（覆盖内），服务端按 t+1 退让到 2026-10-08
    mocks.apiGet.mockResolvedValue(
      optionsBody({ mining: { start: '2021-10-08', end: '2026-10-08' } }),
    );
    renderInput();
    await openPanel();
    fireEvent.click(screen.getByText('自定义区间'));
    fireEvent.change(screen.getByLabelText('挖掘区间终点'), {
      target: { value: '2026-10-09' },
    });

    expect(
      await screen.findByText(/终点已退让到可回测末端：挖掘终点 2026-10-09 → 2026-10-08/),
    ).toBeTruthy();
  });

  it('切到非 A 股：面板收起、配置清空；提交不带 miningConfig', async () => {
    const onSubmit = renderInput();
    await openPanel();
    fireEvent.click(screen.getByText('自定义区间'));
    fireEvent.change(screen.getByLabelText('挖掘区间起点'), {
      target: { value: '2020-06-01' },
    });

    fireEvent.click(screen.getByText('加密货币'));
    await waitFor(() => expect(screen.queryByText('自定义区间')).toBeNull());

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect('miningConfig' in onSubmit.mock.calls[0][0]).toBe(false);
  });

  it('分钟档：预览带 freq=1min；可用连续块/交易日数/断层如实上屏', async () => {
    mocks.apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        String(url).includes('freq=1min') ? minuteOptionsBody() : optionsBody(),
      ),
    );
    const onSubmit = renderInput();
    await openPanel();

    fireEvent.click(screen.getByText('分钟 1min（仅 A 股）'));
    await waitFor(() => expect(lastOptionsUrl()).toContain('freq=1min'));
    expect(lastOptionsUrl()).not.toContain('mining_start');

    // 可用块同时出现在分钟事实行与「挖掘」生效窗行——两处都该有
    expect((await screen.findAllByText('2026-03-04 ~ 2026-08-25')).length).toBe(2);
    expect(screen.getByText(/118 个交易日/)).toBeTruthy();
    expect(screen.getByText('2026-01-06 ~ 2026-03-04')).toBeTruthy();
    // 数据集档位整块收起（分钟面不叠日线富化列）
    expect(screen.queryByText('全选（不写参数）')).toBeNull();
    expect(screen.getByText(/数据集：分钟口径不适用/)).toBeTruthy();

    // 提交换挡后的形态：只带 freq（窗口默认由后端物化成可用连续块）
    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect(onSubmit.mock.calls[0][0].miningConfig).toEqual({
      miningStart: null,
      miningEnd: null,
      validStart: null,
      validEnd: null,
      datasets: null,
      freq: '1min',
    });
  });

  it('换挡清空旧档窗口：分钟档下自定义区间以可用块预填、日期下限=可用块起点', async () => {
    mocks.apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        String(url).includes('freq=1min') ? minuteOptionsBody() : optionsBody(),
      ),
    );
    renderInput();
    await openPanel();

    // 先设日线自定义窗口
    fireEvent.click(screen.getByText('自定义区间'));
    fireEvent.change(screen.getByLabelText('挖掘区间起点'), {
      target: { value: '2020-06-01' },
    });
    await waitFor(() => expect(lastOptionsUrl()).toContain('mining_start=2020-06-01'));

    // 换到分钟档 → 旧窗口清零（预览回到默认，不带窗口键）
    fireEvent.click(screen.getByText('分钟 1min（仅 A 股）'));
    await waitFor(() => expect(lastOptionsUrl()).toContain('freq=1min'));
    expect(lastOptionsUrl()).not.toContain('mining_start');

    // 再切自定义 → 以服务端分钟默认窗（可用连续块）预填
    fireEvent.click(screen.getByText('自定义区间'));
    await waitFor(() =>
      expect(
        (screen.getByLabelText('挖掘区间起点') as HTMLInputElement).value,
      ).toBe('2026-03-04'),
    );
    expect(
      (screen.getByLabelText('挖掘区间起点') as HTMLInputElement).getAttribute('min'),
    ).toBe('2026-03-04');
    expect(
      (screen.getByLabelText('挖掘区间终点') as HTMLInputElement).getAttribute('max'),
    ).toBe('2026-08-25');
  });

  it('分钟数据面未就绪：显式报错、自定义区间禁用（不显示假分段）', async () => {
    mocks.apiGet.mockImplementation((url: string) =>
      Promise.resolve(
        String(url).includes('freq=1min')
          ? minuteOptionsBody({ ready: false, error: '分钟数据面未构建或不可读' })
          : optionsBody(),
      ),
    );
    renderInput();
    await openPanel();

    fireEvent.click(screen.getByText('分钟 1min（仅 A 股）'));
    expect(
      await screen.findByText(/分钟数据面不可用：分钟数据面未构建或不可读/),
    ).toBeTruthy();
    expect((screen.getByText('自定义区间').closest('button') as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
