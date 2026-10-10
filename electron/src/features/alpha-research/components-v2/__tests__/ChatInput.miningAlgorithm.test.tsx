import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatInput } from '../ChatInput';

/**
 * 挖掘算法选择（移植 P2）——能力面驱动的控件联动与派发参数：
 *
 * - **目录是唯一数据源**：选择器选项/默认项名称来自 `GET /alpha-agent/algorithms`，
 *   前端不硬编码算法名；未选算法 = 不下发 `algorithm` 键（老路径零回归）；
 * - **能力面收窄控件**：算法不支持的市场禁用（不隐藏——隐藏会让人以为市场丢了）、
 *   不消费方向文本的算法（uses_llm=false）禁用方向输入并收起方向切换/拆解/父本；
 * - **换算法当场清不兼容配置**：目标算法吃不下频率/数据集 → 清空（留着提交必被
 *   后端 400，不如在这里收回）；
 * - **能力面未知（目录拉不到）不拦任何控件**：不拿猜测禁用用户（后端闸门仍在）。
 *
 * 提交契约：`algorithm`/`algorithmName`/`carriesDirection` 三个字段决定
 * TaskContext 是否代填默认方向（不消费方向的算法收到方向是后端 400）。
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

const RDAGENT = {
  key: 'rdagent',
  name: 'RD-Agent 因子演化',
  desc: 'LLM 多轮演化',
  enabled: true,
  valid: true,
  markets: ['a_share', 'hong_kong', 'crypto'],
  freqs: ['day', '1min'],
  uses_datasets: true,
  uses_llm: true,
  requires_gpu: true,
  progress_kind: 'workdir',
  stages: [],
  runner: 'scripts/alpha_agent/run_rd_agent.py',
};

const STATISTICAL = {
  key: 'statistical',
  name: '统计量价挖掘',
  desc: '不调用 LLM 的价量候选截面检验',
  enabled: true,
  valid: true,
  markets: ['a_share'],
  freqs: ['day'],
  uses_datasets: false,
  uses_llm: false,
  requires_gpu: false,
  progress_kind: 'jsonl',
  stages: [{ key: 'data', label: '取数' }],
  runner: 'scripts/alpha_agent/run_statistical_mining.py',
};

const catalogBody = () => ({
  data: {
    code: 200,
    data: { default: 'rdagent', algorithms: [RDAGENT, STATISTICAL] },
  },
});

const optionsBody = () => ({
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
      minute: null,
      coverage: { start: '2016-01-04', end: '2026-10-09' },
      backtest_end_guard: '2026-10-08',
      defaults: {
        mining: { start: '2021-10-08', end: '2025-10-08' },
        validation: { start: '2025-10-08', end: '2026-10-08' },
      },
      windows: {
        mining: { start: '2021-10-08', end: '2025-10-08' },
        validation: { start: '2025-10-08', end: '2026-10-08' },
        is_default: true,
      },
      segments: {},
      scopes: ['a_share'],
    },
  },
});

const renderInput = (onSubmit = vi.fn(), extra: Record<string, unknown> = {}) => {
  render(<ChatInput inline onSubmit={onSubmit} {...extra} />);
  return onSubmit;
};

// 标题随所选算法的 desc 变（信息量更大），所以按 aria-label 定位
const algSelect = () =>
  screen.getByLabelText('挖掘算法') as HTMLSelectElement;

const chooseAlgorithm = (key: string) => {
  fireEvent.change(algSelect(), { target: { value: key } });
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listMarkets.mockResolvedValue([
    { market_id: 'a_share', market_name: 'A股', description: '', data_ready: true },
    { market_id: 'hong_kong', market_name: '港股', description: '', data_ready: true },
    { market_id: 'crypto', market_name: '加密货币', description: '', data_ready: true },
  ]);
  mocks.getUniverses.mockResolvedValue({ data: { universes: [] } });
  mocks.apiGet.mockImplementation((url: string) =>
    Promise.resolve(
      String(url).startsWith('/alpha-agent/algorithms')
        ? catalogBody()
        : optionsBody(),
    ),
  );
});

describe('ChatInput 挖掘算法选择', () => {
  it('默认态：选项来自目录，未选算法 = 不发 algorithm 键（老路径零回归）', async () => {
    const onSubmit = renderInput();

    await waitFor(() => expect(algSelect().options.length).toBe(3));
    expect(algSelect().value).toBe('');
    expect(algSelect().options[0].text).toBe('默认（RD-Agent 因子演化）');
    expect(Array.from(algSelect().options).map((o) => o.value)).toEqual([
      '',
      'rdagent',
      'statistical',
    ]);

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    const payload = onSubmit.mock.calls[0][0];
    expect('algorithm' in payload).toBe(false);
    expect(payload.carriesDirection).toBe(true);
  });

  it('切统计派：市场收窄 + 方向控件收起 + 提交带能力面快照', async () => {
    const onSubmit = renderInput(vi.fn(), { onDecomposeRequest: vi.fn() });
    await waitFor(() => expect(algSelect().options.length).toBe(3));

    chooseAlgorithm('statistical');

    // 港股/加密货币：算法不支持 → 禁用并在 title 里点名原因
    const hk = screen.getByTitle('统计量价挖掘不支持港股市场');
    expect(hk).toBeDisabled();
    expect(screen.getByTitle('统计量价挖掘不支持加密货币市场')).toBeDisabled();
    expect(screen.getByTitle('A股因子挖掘')).toBeEnabled();

    // 方向文本/自选方向/拆解/父本整块不适用（uses_llm=false）
    expect(screen.getByRole('textbox')).toBeDisabled();
    expect(screen.queryByTitle(/使用设置中的挖掘方向/)).toBeNull();
    expect(screen.queryByTitle(/智能拆解|先输入要拆解/)).toBeNull();
    expect(screen.queryByTitle(/父本定向演化/)).toBeNull();

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.algorithm).toBe('statistical');
    expect(payload.algorithmName).toBe('统计量价挖掘');
    expect(payload.carriesDirection).toBe(false);
    // 不消费方向的算法：提交不带任何方向文本（后端收到即 400）
    expect(payload.userInput).toBe('');
    expect(payload.useCustomMiningDirection).toBe(false);
  });

  it('切回默认：方向控件恢复（能力面随选择双向联动，不是单向阉割）', async () => {
    renderInput(vi.fn(), { onDecomposeRequest: vi.fn() });
    await waitFor(() => expect(algSelect().options.length).toBe(3));

    chooseAlgorithm('statistical');
    expect(screen.getByRole('textbox')).toBeDisabled();
    chooseAlgorithm('');

    expect(screen.getByRole('textbox')).toBeEnabled();
    expect(screen.getByTitle(/使用设置中的挖掘方向/)).toBeTruthy();
    expect(screen.getByTitle(/智能拆解|先输入要拆解/)).toBeTruthy();
  });

  it('换算法清掉吃不下的配置：1min 档被收回，面板只剩日线', async () => {
    const onSubmit = renderInput();
    await waitFor(() => expect(algSelect().options.length).toBe(3));

    fireEvent.click(screen.getByTitle(/挖掘配置/));
    fireEvent.click(await screen.findByText('分钟 1min（仅 A 股）'));
    await waitFor(() =>
      expect(
        String(mocks.apiGet.mock.calls.at(-1)?.[0] ?? ''),
      ).toContain('freq=1min'),
    );
    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    expect(onSubmit.mock.calls[0][0].miningConfig.freq).toBe('1min');

    // 统计派只有日线：换算法即收回分钟档与旧窗口（提交必被 400 的配置不留）
    chooseAlgorithm('statistical');
    await waitFor(() =>
      expect(screen.queryByText('分钟 1min（仅 A 股）')).toBeNull(),
    );
    expect(screen.getByText(/数据集：统计量价挖掘不消费数据集选择/)).toBeTruthy();

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    const payload = onSubmit.mock.calls[1][0];
    expect('miningConfig' in payload).toBe(false);
  });

  it('目录拉不到：能力面未知 → 不禁用任何控件、不发 algorithm 键', async () => {
    mocks.apiGet.mockRejectedValue(new Error('network down'));
    const onSubmit = renderInput();

    await waitFor(() => expect(mocks.getUniverses).toHaveBeenCalled());
    // 选择器只有目录才能渲染——目录缺席就不渲染，不造一个假的默认项
    expect(screen.queryByLabelText('挖掘算法')).toBeNull();
    // 市场/方向一个都不禁用（未知能力面 ≠ 都不支持）
    expect(screen.getByTitle('港股因子挖掘')).toBeEnabled();
    expect(screen.getByRole('textbox')).toBeEnabled();

    fireEvent.click(screen.getByTitle('发送 (Enter)'));
    const payload = onSubmit.mock.calls[0][0];
    expect('algorithm' in payload).toBe(false);
    expect(payload.carriesDirection).toBe(true);
  });
});
