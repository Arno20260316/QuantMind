import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  algorithmSupportsFreq,
  algorithmSupportsMarket,
  getMiningAlgorithms,
  resolveAlgorithm,
  selectableAlgorithms,
  type MiningAlgorithm,
} from '../miningAlgorithmApi';

/**
 * 算法目录（移植 P2）契约：
 *
 * - **只做归一，不发明能力面**：字段照抄后端 manifest；缺声明一律按最保守
 *   解释（uses_* / requires_gpu = false）——「没声明支持」不是支持；
 * - **坏 manifest 与合法目录分开**：valid=false 的条目没有可信能力面，
 *   进 invalid 报告，不混进选择器；
 * - **默认 key 来自后端**：`default` 缺席就是空串，前端不写死 'rdagent'；
 * - **未知能力面不拦**：目录未加载/未声明市场或频率时谓词放行（拿空表禁用
 *   所有市场会把人锁死，后端闸门仍在）。
 */

const mocks = vi.hoisted(() => ({ apiGet: vi.fn() }));

vi.mock('../../../../services/aiStrategyClients', () => ({
  apiClient: { get: mocks.apiGet, post: vi.fn(), delete: vi.fn() },
}));

const RD = {
  key: 'rdagent',
  name: 'RD-Agent 因子演化',
  version: '1.0.0',
  desc: 'LLM 多轮演化',
  vendor: '微软 RD-Agent',
  enabled: true,
  valid: true,
  tags: ['llm'],
  markets: ['a_share', 'hong_kong'],
  freqs: ['day', '1min'],
  uses_datasets: true,
  uses_llm: true,
  requires_gpu: true,
  progress_kind: 'workdir',
  stages: [{ key: 'hypothesis', label: '假设' }],
  runner: 'scripts/alpha_agent/run_rd_agent.py',
};

const STAT = {
  key: 'statistical',
  name: '统计量价挖掘',
  enabled: true,
  valid: true,
  markets: ['a_share'],
  freqs: ['day'],
  uses_datasets: false,
  uses_llm: false,
  requires_gpu: false,
  progress_kind: 'jsonl',
  stages: [
    { key: 'data', label: '取数' },
    { key: 'evaluate', label: '截面检验' },
  ],
  runner: 'scripts/alpha_agent/run_statistical_mining.py',
};

const catalogBody = (algorithms: unknown[] = [RD, STAT], def = 'rdagent') => ({
  data: { code: 200, data: { default: def, algorithms } },
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.apiGet.mockResolvedValue(catalogBody());
});

describe('getMiningAlgorithms', () => {
  it('归一 manifest 摘要：能力面照抄，snake→camel', async () => {
    const res = await getMiningAlgorithms();
    expect(mocks.apiGet).toHaveBeenCalledWith('/alpha-agent/algorithms');
    expect(res.success).toBe(true);
    expect(res.data?.defaultKey).toBe('rdagent');

    const [rd, stat] = res.data!.algorithms;
    expect(rd).toEqual({
      key: 'rdagent',
      name: 'RD-Agent 因子演化',
      version: '1.0.0',
      desc: 'LLM 多轮演化',
      vendor: '微软 RD-Agent',
      enabled: true,
      tags: ['llm'],
      markets: ['a_share', 'hong_kong'],
      freqs: ['day', '1min'],
      usesDatasets: true,
      usesLlm: true,
      requiresGpu: true,
      progressKind: 'workdir',
      stages: [{ key: 'hypothesis', label: '假设' }],
      runner: 'scripts/alpha_agent/run_rd_agent.py',
    });
    expect(stat.usesLlm).toBe(false);
    expect(stat.usesDatasets).toBe(false);
    expect(stat.requiresGpu).toBe(false);
    expect(stat.progressKind).toBe('jsonl');
    expect(stat.stages.map((s) => s.key)).toEqual(['data', 'evaluate']);
  });

  it('坏 manifest 进 invalid，不混进可选目录；缺字段按最保守解释', async () => {
    mocks.apiGet.mockResolvedValue(
      catalogBody([
        { key: 'broken', valid: false, error: 'runner 不存在: scripts/x.py' },
        { key: 'bare', valid: true, enabled: true },
      ]),
    );
    const res = await getMiningAlgorithms();
    expect(res.data?.invalid).toEqual([
      { key: 'broken', error: 'runner 不存在: scripts/x.py' },
    ]);
    expect(res.data?.algorithms).toHaveLength(1);
    const bare = res.data!.algorithms[0];
    // 没声明的能力面一律 false/空：不把「没说」读成「支持」
    expect(bare.usesLlm).toBe(false);
    expect(bare.usesDatasets).toBe(false);
    expect(bare.markets).toEqual([]);
    expect(bare.freqs).toEqual([]);
    expect(bare.progressKind).toBe('');
    expect(bare.name).toBe('bare'); // 无名回落到 key，不编造名字
  });

  it('后端未给 default → defaultKey 空串（前端不写死 rdagent）', async () => {
    mocks.apiGet.mockResolvedValue({
      data: { code: 200, data: { algorithms: [STAT] } },
    });
    const res = await getMiningAlgorithms();
    expect(res.data?.defaultKey).toBe('');
  });

  it('请求失败 → success=false 且带 detail 原文', async () => {
    mocks.apiGet.mockRejectedValue({
      response: { data: { detail: '认证失败' } },
    });
    const res = await getMiningAlgorithms();
    expect(res.success).toBe(false);
    expect(res.error).toBe('认证失败');
  });
});

describe('目录选择/解析', () => {
  const asAlgo = (over: Partial<MiningAlgorithm>): MiningAlgorithm => ({
    key: 'k',
    name: 'k',
    version: '',
    desc: '',
    vendor: '',
    enabled: true,
    tags: [],
    markets: [],
    freqs: [],
    usesDatasets: false,
    usesLlm: false,
    requiresGpu: false,
    progressKind: '',
    stages: [],
    runner: '',
    ...over,
  });

  it('selectableAlgorithms 只给启用的（下线算法留档但不给选）', () => {
    const list = [
      asAlgo({ key: 'on', enabled: true }),
      asAlgo({ key: 'off', enabled: false }),
    ];
    expect(
      selectableAlgorithms({ defaultKey: 'on', algorithms: list, invalid: [] }).map(
        (a) => a.key,
      ),
    ).toEqual(['on']);
  });

  it('resolveAlgorithm：空 key 落到注册表默认；找不到 = null（不猜）', () => {
    const catalog = {
      defaultKey: 'rdagent',
      algorithms: [asAlgo({ key: 'rdagent', usesLlm: true })],
      invalid: [],
    };
    expect(resolveAlgorithm(catalog, '')?.key).toBe('rdagent');
    expect(resolveAlgorithm(catalog, null)?.key).toBe('rdagent');
    expect(resolveAlgorithm(catalog, 'gone')).toBeNull();
    expect(resolveAlgorithm(null, 'rdagent')).toBeNull();
  });

  it('能力面谓词：未声明/未知不拦，声明了就按声明', () => {
    const stat = asAlgo({ markets: ['a_share'], freqs: ['day'] });
    expect(algorithmSupportsMarket(stat, 'a_share')).toBe(true);
    expect(algorithmSupportsMarket(stat, 'hong_kong')).toBe(false);
    expect(algorithmSupportsMarket(null, 'hong_kong')).toBe(true);
    expect(algorithmSupportsMarket(asAlgo({}), 'crypto')).toBe(true);

    // ''/null 是日线默认语义（不是一种独立频率）
    expect(algorithmSupportsFreq(stat, null)).toBe(true);
    expect(algorithmSupportsFreq(stat, '')).toBe(true);
    expect(algorithmSupportsFreq(stat, 'day')).toBe(true);
    expect(algorithmSupportsFreq(stat, '1min')).toBe(false);
    expect(algorithmSupportsFreq(null, '1min')).toBe(true);
    expect(algorithmSupportsFreq(asAlgo({}), '1min')).toBe(true);
  });
});
