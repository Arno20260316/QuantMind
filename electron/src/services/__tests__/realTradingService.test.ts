import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
  request: vi.fn(),
}));

vi.mock('axios', () => {
  class MockAxiosHeaders {
    set() {}
    has() { return false; }
  }

  return {
    AxiosHeaders: MockAxiosHeaders,
    default: {
      create: () => ({
        get: mocks.get,
        post: mocks.post,
        put: mocks.put,
        delete: mocks.delete,
        patch: mocks.patch,
        request: mocks.request,
        interceptors: {
          request: { use: vi.fn() },
          response: { use: vi.fn() },
        },
      }),
    },
    create: () => ({
      get: mocks.get,
      post: mocks.post,
      put: mocks.put,
      delete: mocks.delete,
      patch: mocks.patch,
      request: mocks.request,
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
    __esModule: true,
  };
});

vi.mock('../../features/auth/services/authService', () => ({
  authService: {
    getAccessToken: vi.fn(() => 'token'),
    handle401Error: vi.fn((error: unknown) => Promise.reject(error)),
    getStoredUser: vi.fn(() => ({ user_id: '79311845' })),
  },
}));

import { realTradingService } from '../realTradingService';

describe('realTradingService manual execution APIs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('previewManualExecution should call preview endpoint', async () => {
    mocks.request.mockResolvedValueOnce({ data: { preview_hash: 'abc', sell_orders: [], buy_orders: [], skipped_items: [], summary: {}, account_snapshot: {}, strategy_context: {} } });

    const result = await realTradingService.previewManualExecution({
      model_id: 'model_qlib',
      run_id: 'run-1',
      strategy_id: '101',
      trading_mode: 'REAL',
    });

    expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({
      method: 'post',
      url: '/manual-executions/preview',
      data: {
        model_id: 'model_qlib',
        run_id: 'run-1',
        strategy_id: '101',
        trading_mode: 'REAL',
      },
    }));
    expect(result.preview_hash).toBe('abc');
  });

  it('createManualExecution should send preview_hash to submit endpoint', async () => {
    mocks.request.mockResolvedValueOnce({ data: { status: 'success', task_id: 'manual_1' } });

    const result = await realTradingService.createManualExecution({
      model_id: 'model_qlib',
      run_id: 'run-1',
      strategy_id: '101',
      trading_mode: 'REAL',
      preview_hash: 'preview-123',
    });

    expect(mocks.request).toHaveBeenCalledWith(expect.objectContaining({
      method: 'post',
      url: '/manual-executions',
      data: {
        model_id: 'model_qlib',
        run_id: 'run-1',
        strategy_id: '101',
        trading_mode: 'REAL',
        preview_hash: 'preview-123',
      },
    }));
    expect(result.task_id).toBe('manual_1');
  });

  it('start should fall back to direct real trading endpoint when gateway request fails', async () => {
    mocks.request
      .mockRejectedValueOnce({ response: { status: 503 }, config: { url: '/start' } })
      .mockResolvedValueOnce({ data: { status: 'success', mode: 'REAL' } });

    const result = await realTradingService.start(
      '79311845',
      '101',
      'REAL',
      'default',
      { max_buy_drop: 0.05 },
      { order_type: 'LIMIT' } as any,
    );

    expect(mocks.request).toHaveBeenCalledTimes(2);
    expect(result.status).toBe('success');
  });
});

describe('realTradingService.extractStartGateFailure', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const gateEnvelope = (overrides: Record<string, unknown> = {}) => ({
    response: {
      status: 400,
      data: {
        detail: {
          message: '策略尚未模拟验证（VERIFIED）：REAL 须先经模拟盘（SIM）验证',
          gate: 'lifecycle',
          mode: 'REAL',
          next_step: '先切模拟盘启动一次，通过后即可启动实盘',
          gate_items: [
            {
              key: 'lifecycle',
              label: '生命周期状态',
              value: 'VERIFIED',
              passed: false,
              reason: 'REAL 须先经模拟盘（SIM）验证',
              next_step: '先切模拟盘启动一次',
            },
            {
              key: 'health',
              label: '回测体检',
              value: 'A · 可信度 82/100',
              passed: true,
              reason: '',
              next_step: '',
            },
          ],
          ...overrides,
        },
      },
    },
  });

  it('parses the lifecycle gate envelope into per-item rows', () => {
    const failure = realTradingService.extractStartGateFailure(gateEnvelope());

    expect(failure).not.toBeNull();
    expect(failure!.gate).toBe('lifecycle');
    expect(failure!.mode).toBe('REAL');
    expect(failure!.next_step).toBe('先切模拟盘启动一次，通过后即可启动实盘');
    expect(failure!.items).toHaveLength(2);
    expect(failure!.items[0]).toEqual({
      key: 'lifecycle',
      label: '生命周期状态',
      value: 'VERIFIED',
      passed: false,
      reason: 'REAL 须先经模拟盘（SIM）验证',
      next_step: '先切模拟盘启动一次',
    });
    expect(failure!.items[1].passed).toBe(true);
  });

  it('returns null for a precheck envelope so the two gates do not swallow each other', () => {
    const precheck = {
      response: {
        status: 409,
        data: {
          detail: {
            precheck_failed: true,
            message: '次日预测排名准备度未通过',
            items: [{ key: 'model', detail: '模型未就绪' }],
          },
        },
      },
    };

    expect(realTradingService.extractStartGateFailure(precheck)).toBeNull();
    expect(realTradingService.extractTradingPrecheckFailure(gateEnvelope())).toBeNull();
  });

  it('returns null when there is no structured detail at all', () => {
    expect(realTradingService.extractStartGateFailure(new Error('boom'))).toBeNull();
    expect(realTradingService.extractStartGateFailure(undefined)).toBeNull();
    expect(
      realTradingService.extractStartGateFailure({ response: { data: { detail: 'plain string' } } }),
    ).toBeNull();
    // `gate` 短串出现在别的接口里不算命中——形状特征是 `gate_items` 数组。
    expect(
      realTradingService.extractStartGateFailure({
        response: { data: { detail: { gate: 'lifecycle' } } },
      }),
    ).toBeNull();
  });

  it('normalizes malformed item fields instead of throwing', () => {
    const failure = realTradingService.extractStartGateFailure({
      response: {
        data: {
          detail: {
            gate_items: [{ key: 'sim_days', label: '模拟交易日', value: '无记录（只提示不拦）' }, null],
          },
        },
      },
    });

    expect(failure).not.toBeNull();
    expect(failure!.message).toBe('当前策略不满足启动条件');
    expect(failure!.gate).toBe('');
    expect(failure!.mode).toBe('');
    expect(failure!.next_step).toBe('');
    expect(failure!.items[0]).toEqual({
      key: 'sim_days',
      label: '模拟交易日',
      value: '无记录（只提示不拦）',
      passed: false,
      reason: '',
      next_step: '',
    });
    expect(failure!.items[1].key).toBe('');
  });
});
