/**
 * 因子排行榜「去重 |ρ|≥0.9」实机验收探针（2026-10-15）。
 *
 * 覆盖用户原话的三件事：加去重按钮（与筛选页同口径）、去掉重复的、重新排名。
 * JSON 与 DOM 两条路都走（JSON 判数值口径，DOM 判「界面上真的换了视图」）：
 *   1. 初始 = 全量榜单（无 dedup 参数；chip 关态；计数 = 全量行数）；
 *   2. 点击 chip → 请求带 dedup=0.9；meta.dedup 报账（n_before/dropped/covered）；
 *      行数 = n_before − dropped；rank 连续 1..N；榜首保留且 composite 原值；
 *      DOM 行数与「命中 X / 全部 Y」跟随；chip 开态且显示「剔除 N」；
 *   3. 再点一次 → 回到无 dedup 请求；全量行回来；chip 关态。
 *
 * 用法：node electron/tests/probe_factor_leaderboard_dedup.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_factor_leaderboard_dedup.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';

let pass = 0;
let fail = 0;
const ok = (cond, label, extra = '') => {
  if (cond) { pass++; console.log(`  ✓ ${label}${extra ? ` — ${extra}` : ''}`); }
  else { fail++; console.log(`  ✗ ${label}${extra ? ` — ${extra}` : ''}`); }
};

const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome-stable',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

// 收集每次 leaderboard 响应（含 URL 参数），判定用「最近一次」避免首屏双请求竞态
const lbResps = [];
page.on('response', (r) => {
  if (!r.url().includes('/factor-research/leaderboard')) return;
  r.json().then((j) => lbResps.push({ url: r.url(), j })).catch(() => {});
});
const lastUnfiltered = (needle) => {
  const hits = lbResps.filter((x) => needle(x.url));
  return hits.length ? hits[hits.length - 1] : null;
};

/** DOM：表格数据行数 + 「命中 X / 全部 Y」计数 */
const domState = () => page.evaluate(() => {
  let rows = document.querySelectorAll('tbody tr').length;
  if (rows === 0) {
    rows = document.querySelectorAll('table tr').length
      - document.querySelectorAll('thead tr').length;
  }
  const m = document.body.innerText.match(/命中\s*([\d,]+)\s*\/\s*([\d,]+)/);
  return {
    rows,
    hit: m ? parseInt(m[1].replaceAll(',', ''), 10) : null,
    total: m ? parseInt(m[2].replaceAll(',', ''), 10) : null,
  };
});

const waitDomRows = async (n, timeout = 120000) => {
  await page.waitForFunction(
    (expect) => {
      let rows = document.querySelectorAll('tbody tr').length;
      if (rows === 0) {
        rows = document.querySelectorAll('table tr').length
          - document.querySelectorAll('thead tr').length;
      }
      return rows === expect;
    },
    n,
    { timeout },
  );
};

const chip = () => page.locator('[data-testid="lb-dedup"]');

/**
 * 清扫合规弹窗（首启风险问卷 / 「稍后再答」）。它由 App 层挂载、**登录后会再次弹出**，
 * 盖在页面上时 Playwright 点击报「subtree intercepts pointer events」——每次交互前都扫一遍。
 */
const dismissModals = async () => {
  for (let i = 0; i < 8; i++) {
    const btn = page.locator('button', { hasText: /稍后再答/ });
    if ((await btn.count()) === 0) break;
    await btn.first().click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
};

// ── 登录 ────────────────────────────────────────────────────────────────
// ⚠️ 不能 goto 完立刻查 password 输入框：bundle 未挂载时计数为 0，会**静默跳过登录**，
// 随后深链被 ProtectedRoute 顶回 /auth/login（症状 = 等 leaderboard 响应超时 240s，
// 与后端无关）。必须轮询等挂载，且登录失败要硬闸报错。
await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
for (let attempt = 0; attempt < 15; attempt++) {
  if ((await page.locator('input[type=password]').count()) > 0) {
    await page.locator('input').nth(0).fill('admin');
    await page.locator('input[type=password]').first().fill('admin123');
    const btns = page.locator('button');
    for (let i = 0; i < (await btns.count()); i++) {
      const t = (await btns.nth(i).innerText()).trim();
      if (/登录/.test(t)) { await btns.nth(i).click(); break; }
    }
    await page.locator('input[type=password]').first()
      .waitFor({ state: 'detached', timeout: 30000 }).catch(() => {});
    break;
  }
  if (!page.url().includes('auth/login')) break; // 已登录，直接进应用
  await page.waitForTimeout(1000);
}
const later = page.locator('button', { hasText: '稍后再答' });
if ((await later.count()) > 0) { await later.first().click().catch(() => {}); }
await dismissModals();
// 登录 POST 成功后 URL 还会在 /auth/login 停留数秒（「正在验证身份…」过场，实测 ~5s），
// 这里必须等它自己跳走，别在过场期间判失败。
for (let w = 0; w < 60 && page.url().includes('auth/login'); w++) {
  await page.waitForTimeout(1000);
}
if (page.url().includes('auth/login')) {
  console.log('\n[FATAL] 登录失败，仍停在 /auth/login —— 后续断言无意义，直接退出');
  await page.screenshot({ path: '/tmp/qm_lb_dedup_login_fail.png' });
  await browser.close();
  process.exit(2);
}

// ── ① 全量榜单（默认页签即排行榜；HashRouter 深链必须带 #/） ────────────
const [resp1] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/factor-research/leaderboard'),
    { timeout: 240000 }),
  page.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' }),
]);
const first = await resp1.json();
await chip().waitFor({ state: 'visible', timeout: 30000 });
await page.waitForTimeout(1500); // 双请求竞态窗口，随后取「最近一次」

const full = lastUnfiltered((u) => !u.includes('dedup='));
const nFull = full.j.leaderboard.length;
console.log(`\n[全量] 行数=${nFull}（首个响应 ${first.leaderboard?.length ?? '?'}）`);
ok(!(full.j.meta?.dedup?.applied), '全量响应无 dedup 生效标记', JSON.stringify(full.j.meta?.dedup ?? null));
ok(nFull > 1000, `全量行数合理（>1000）`, `${nFull}`);
await waitDomRows(nFull);
const domFull = await domState();
ok(domFull.total === nFull && domFull.hit === nFull, '「命中 X / 全部 Y」= 全量行数',
  `命中 ${domFull.hit} / 全部 ${domFull.total}`);
ok((await chip().getAttribute('aria-pressed')) === 'false', 'chip 初始为关态');
ok((await chip().textContent()).includes('|ρ|≥0.9'), 'chip 文案含口径阈值 |ρ|≥0.9');
ok(!(await chip().textContent()).includes('剔除'), '关态 chip 不显示剔除数');
await page.screenshot({ path: '/tmp/qm_lb_dedup_full.png' });

// ── ② 点开去重 ──────────────────────────────────────────────────────────
await dismissModals(); // 首启风险问卷可能此时才弹（实测它会盖住 chip）
const [resp2] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/factor-research/leaderboard')
    && r.url().includes('dedup=0.9'), { timeout: 240000 }),
  chip().click(),
]);
const ded = await resp2.json();
const meta = ded.meta?.dedup ?? {};
const nDed = ded.leaderboard.length;
console.log(`\n[去重] 行数=${nDed} dropped=${meta.dropped} covered=${meta.covered}`);
ok(meta.applied === true, 'meta.dedup.applied = true');
ok(meta.threshold === 0.9, 'meta.dedup.threshold = 0.9', String(meta.threshold));
ok(meta.n_before === nFull, 'meta.dedup.n_before = 全量行数', `${meta.n_before}`);
ok(meta.dropped > 0, 'dropped > 0（确有重复被剔）', `${meta.dropped}`);
ok(meta.covered > 0 && meta.covered <= nFull, 'covered 在 (0, n_before]', `${meta.covered}`);
ok(nDed === nFull - meta.dropped, '去重行数 = n_before − dropped', `${nDed}`);
ok(nDed < nFull, '去重后行数下降', `${nFull} → ${nDed}`);
ok(ded.leaderboard.every((r, i) => r.rank === i + 1), 'rank 连续重编号 1..N');
ok(new Set(ded.leaderboard.map((r) => r.code)).size === nDed, '无重复 code');
ok(ded.leaderboard[0].code === full.j.leaderboard[0].code, '榜首（rank 1）保留未被剔');
ok(ded.leaderboard[0].composite === full.j.leaderboard[0].composite,
  '榜首 composite 原值不动', `${ded.leaderboard[0].composite}`);

await waitDomRows(nDed);
const domDed = await domState();
ok(domDed.total === nDed && domDed.hit === nDed, '「命中 X / 全部 Y」= 去重行数',
  `命中 ${domDed.hit} / 全部 ${domDed.total}`);
ok((await chip().getAttribute('aria-pressed')) === 'true', 'chip 切到开态');
ok((await chip().textContent()).includes(`剔除 ${meta.dropped}`), '开态 chip 显示剔除数',
  `剔除 ${meta.dropped}`);
ok((await chip().getAttribute('title')).includes('取消去重'), '开态 title 提示「取消去重」');
await page.screenshot({ path: '/tmp/qm_lb_dedup_on.png' });

// ── ③ 再点取消：恢复全榜 ────────────────────────────────────────────────
await dismissModals();
const beforeCancel = lbResps.length;
const [resp3] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/factor-research/leaderboard'),
    { timeout: 240000 }),
  chip().click(),
]);
const back = await resp3.json();
// 竞态兜底：取的最近一次不带 dedup 的响应
const backU = lastUnfiltered((u) => !u.includes('dedup=')) ?? { j: back };
const nBack = backU.j.leaderboard.length;
console.log(`\n[取消] 行数=${nBack}`);
ok(!resp3.url().includes('dedup=') || lbResps.slice(beforeCancel).some((x) => !x.url.includes('dedup=')),
  '取消后请求不再带 dedup 参数');
ok(!backU.j.meta?.dedup?.applied, '恢复后无 dedup 生效标记');
ok(nBack === nFull, '行数恢复为全量', `${nBack}`);
await waitDomRows(nFull);
ok((await chip().getAttribute('aria-pressed')) === 'false', 'chip 回到关态');
await page.screenshot({ path: '/tmp/qm_lb_dedup_restored.png' });

// ── 报告 ────────────────────────────────────────────────────────────────
if (pageErrors.length) {
  fail++;
  console.log(`  ✗ 页面 JS 错误：${[...new Set(pageErrors)].slice(0, 5).join(' | ')}`);
} else {
  pass++;
  console.log('  ✓ 全程无页面 JS 错误');
}

console.log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
