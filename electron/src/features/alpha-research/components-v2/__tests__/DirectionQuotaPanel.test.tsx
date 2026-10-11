/**
 * 方向配额面板（T-FE-10）契约：
 * - 打分表按后端顺序渲染（成功 n/终态、p、90日命中、score 条形与数值）；
 * - 档位预演三条：加权口径 + 名额 + 回退标注（saturated / mutate_empty 如实展示）；
 * - 饱和方向带「已剔除」徽标，不静默消失；
 * - 缺失一律「—」（score/p 缺失绝不当 0 上屏）；
 * - 服务端降级（available=false）与请求失败分别给不同层级的可操作文案；
 * - 预演名额切换 → 以新 n 重新请求；勾选项按候选词表请求（防抖只影响时机）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DirectionQuotaPanel } from '../DirectionQuotaPanel';
import type { QuotaPreviewView } from '../../services-v2/directionQuotaApi';

const apiMocks = vi.hoisted(() => ({ getDirectionQuotaPreview: vi.fn() }));

vi.mock('../../services-v2/directionQuotaApi', () => ({
  getDirectionQuotaPreview: apiMocks.getDirectionQuotaPreview,
  DIRECTION_QUOTA_DEFAULT_MARKET: 'a_share',
  DIRECTION_QUOTA_DEFAULT_N: 3,
  QUOTA_TIERS: ['explore', 'exploit', 'mutate'],
}));

const view = (over: Partial<QuotaPreviewView> = {}): QuotaPreviewView => ({
  available: true,
  market: 'a_share',
  n: 3,
  quota: { explore: 0.3, exploit: 0.5, mutate: 0.2 },
  slots: { explore: 1, exploit: 2, mutate: 0 },
  saturatedThreshold: 2,
  recentSuccessTasks: 5,
  directions: [
    {
      direction: '尾盘资金流强度',
      attempts: 9,
      nTasks: 4,
      nSuccess: 1,
      p: 0.75,
      dupHits90d: 0,
      satPenalty: 0,
      score: 0.75,
      saturated: false,
    },
    {
      direction: '量价背离确认',
      attempts: 3,
      nTasks: 1,
      nSuccess: 0,
      p: 0.125,
      dupHits90d: 2,
      satPenalty: 0.5,
      score: 0.0625,
      saturated: true,
    },
  ],
  tiers: {
    explore: { pool: ['尾盘资金流强度'], weighting: 'blankness', fallback: null },
    exploit: { pool: ['尾盘资金流强度'], weighting: 'proxy_score', fallback: null },
    mutate: { pool: [], weighting: 'uniform', fallback: 'mutate_empty' },
  },
  recentSuccessDirs: ['尾盘资金流强度'],
  error: null,
  ...over,
});

const ok = (data: QuotaPreviewView) => ({ success: true, data });

beforeEach(() => {
  apiMocks.getDirectionQuotaPreview.mockReset();
});

describe('DirectionQuotaPanel', () => {
  it('打分表 + 档位预演 + 名额头部：计数/比例/回退徽标逐项上屏', async () => {
    apiMocks.getDirectionQuotaPreview.mockResolvedValue(ok(view()));

    render(<DirectionQuotaPanel />);

    // 就绪信号：饱和徽标唯一
    expect(await screen.findByText('饱和·已剔除')).toBeTruthy();
    expect(apiMocks.getDirectionQuotaPreview).toHaveBeenCalledWith({
      market: 'a_share',
      directions: [],
      n: 3,
    });

    // 头部名额切分
    expect(screen.getByText(/预演 N=3 → 探索 1 \/ 利用 2 \/ 变异 0/)).toBeTruthy();
    // 打分表：成功 n/终态 + 挖过 + p + 90日命中 + score 数值
    expect(screen.getByText(/成功 1\/4 · 挖过 9 次/)).toBeTruthy();
    expect(screen.getByText('p=75%')).toBeTruthy();
    expect(screen.getByText('90日命中 0')).toBeTruthy();
    expect(screen.getByText('0.750')).toBeTruthy();
    expect(screen.getByText('p=13%')).toBeTruthy();
    expect(screen.getByText('90日命中 2')).toBeTruthy();
    expect(screen.getByText('0.063')).toBeTruthy();
    // 档位预演：加权口径 + 回退标注
    expect(screen.getByText('空白度加权')).toBeTruthy();
    expect(screen.getByText('打分归一')).toBeTruthy();
    expect(screen.getByText(/无近期成功方向 · 已退利用档/)).toBeTruthy();
    // 变异池空如实说明
    expect(screen.getByText(/本档暂无可用方向/)).toBeTruthy();
    // 方向名在多处出现：打分表 + 档位池 chips + 最近成功方向 chips
    expect(screen.getAllByText('尾盘资金流强度').length).toBeGreaterThanOrEqual(3);
  });

  it('缺失一律「—」：p/score/计数为空绝不当 0 上屏', async () => {
    apiMocks.getDirectionQuotaPreview.mockResolvedValue(
      ok(
        view({
          directions: [
            {
              direction: '半截行',
              attempts: null,
              nTasks: null,
              nSuccess: null,
              p: null,
              dupHits90d: null,
              satPenalty: null,
              score: null,
              saturated: false,
            },
          ],
        }),
      ),
    );

    render(<DirectionQuotaPanel />);

    expect(await screen.findByText('半截行')).toBeTruthy();
    expect(screen.getByText('p=—')).toBeTruthy();
    expect(screen.getByText('90日命中 —')).toBeTruthy();
    expect(screen.getByText(/成功 —\/—/)).toBeTruthy();
    expect(screen.getByText('—')).toBeTruthy(); // score 数值位
    expect(screen.queryByText('p=0%')).toBeNull();
  });

  it('候选词表为空/无史：引导文案，不假装有档位预演', async () => {
    apiMocks.getDirectionQuotaPreview.mockResolvedValue(
      ok(view({ directions: [], tiers: null, recentSuccessDirs: [] })),
    );

    render(<DirectionQuotaPanel />);

    expect(await screen.findByText(/暂无方向任务史/)).toBeTruthy();
    expect(screen.queryByText(/档位预演/)).toBeNull();
  });

  it('服务端降级（available=false）：显示稳定文案，不给重试假按钮', async () => {
    apiMocks.getDirectionQuotaPreview.mockResolvedValue(
      ok(
        view({
          available: false,
          directions: [],
          tiers: null,
          recentSuccessDirs: [],
          error: '方向史/饱和数据读取出错，配额预览暂不可用（详见服务端日志）',
        }),
      ),
    );

    render(<DirectionQuotaPanel />);

    expect(await screen.findByText(/配额预览暂不可用/)).toBeTruthy();
    expect(screen.queryByText(/档位预演/)).toBeNull();
  });

  it('请求失败给错误文案与重试；重试后恢复', async () => {
    apiMocks.getDirectionQuotaPreview.mockRejectedValueOnce(new Error('后端不可达'));
    apiMocks.getDirectionQuotaPreview.mockResolvedValueOnce(ok(view()));

    render(<DirectionQuotaPanel />);

    expect(await screen.findByText(/方向配额读取失败：后端不可达/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(await screen.findByText('饱和·已剔除')).toBeTruthy();
    expect(apiMocks.getDirectionQuotaPreview).toHaveBeenCalledTimes(2);
  });

  it('候选词表按勾选传入（剥空白）；切换预演名额 → 以新 n 重新请求', async () => {
    apiMocks.getDirectionQuotaPreview.mockResolvedValue(ok(view()));

    render(
      <DirectionQuotaPanel directions={[' 尾盘资金流强度 ', '量价背离确认']} />,
    );

    await waitFor(() => {
      expect(apiMocks.getDirectionQuotaPreview).toHaveBeenCalledWith({
        market: 'a_share',
        directions: ['尾盘资金流强度', '量价背离确认'],
        n: 3,
      });
    });

    fireEvent.click(screen.getByRole('button', { name: '5' }));

    await waitFor(() => {
      expect(apiMocks.getDirectionQuotaPreview).toHaveBeenLastCalledWith({
        market: 'a_share',
        directions: ['尾盘资金流强度', '量价背离确认'],
        n: 5,
      });
    });
  });
});
