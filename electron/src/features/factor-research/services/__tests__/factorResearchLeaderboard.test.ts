/**
 * 排行榜「去重参数」的 HTTP 层契约（2026-10-15）。
 *
 * 去重是**服务端行为**（`dedup` 查询参数），前端唯一能错的接缝就是 URL：
 * 参数名写错/忘了带 → 后端照旧返回全量榜单，页面上开关亮着但行数没变，
 * 而且没有任何报错。所以这里钉死：开去重才带 `dedup`，关去重一个多余字符都不发。
 */
import { describe, test, expect, vi, afterEach } from 'vitest';
import { getLeaderboard } from '../factorResearchService';
import { SERVICE_ENDPOINTS } from '../../../../config/services';

function stubFetch(payload: unknown) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(String(url));
      return {
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const EMPTY = { leaderboard: [], meta: {} };

describe('getLeaderboard：dedup 参数', () => {
  test('去重开启：URL 带 dedup=0.9（与筛选页同口径的阈值），market 一并带上', async () => {
    const calls = stubFetch(EMPTY);

    await getLeaderboard(undefined, 30, 'private', 0.9, 'HK');

    expect(calls).toHaveLength(1);
    expect(calls[0]).toBe(
      `${SERVICE_ENDPOINTS.USER_SERVICE}/factor-research/leaderboard?dataset=private&market=HK&dedup=0.9`,
    );
  });

  test('去重关闭（0）：URL 不含 dedup —— 默认路径字节级不变', async () => {
    const calls = stubFetch(EMPTY);

    await getLeaderboard(undefined, 30, 'private', 0);

    expect(calls[0]).not.toContain('dedup');
  });
});
