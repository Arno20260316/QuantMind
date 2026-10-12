/**
 * 组合构建弹窗「精选子集（T-FE-02）」实机验收探针。
 *
 * 双路核对（与 probe_factor_report.mjs 同套路）：
 *   - JSON：`/factor-report/portfolio` 响应里 best_subset 块的值对不对
 *     （available/k 范围/因子个数=K/三行基准在场/数字非空）
 *   - DOM ：节真渲染且自洽 —— 标题、K* chip、三行对照表、因子 chip 个数
 *
 * 用法：node electron/tests/probe_factor_portfolio_bestsubset.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_factor_portfolio_bestsubset.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_bestsubset.png';

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
const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await ctx.newPage();

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

console.log(`\n===== 精选子集（T-FE-02）实机探针 @ ${BASE} =====`);

// ── 登录（样板同 probe_factor_report.mjs）──────────────────────────────
await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3000);
if ((await page.locator('input[type=password]').count()) > 0) {
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input[type=password]').first().fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.locator('input[type=password]').first().waitFor({ state: 'detached', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(2500);
}
const later = page.locator('button', { hasText: '稍后再答' });
if (await later.count()) { await later.first().click().catch(() => {}); await page.waitForTimeout(800); }

// ── 进入因子报告（HashRouter 必须带 #/）───────────────────────────────
await page.goto(`${BASE}/#/factor-research?tab=factor-report`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);

// ── 打开组合构建：JSON + DOM 双路 ─────────────────────────────────────
const btn = page.getByRole('button', { name: /组合构建/ }).first();
ok((await btn.count()) > 0, '「组合构建」按钮在场');
let portfolio = null;
if (await btn.count()) {
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/factor-report/portfolio'), { timeout: 120000 }),
    btn.click(),
  ]);
  portfolio = await resp.json();
  await page.waitForTimeout(2500);
}

// ── JSON 侧 ──────────────────────────────────────────────────────────
const bs = portfolio?.best_subset;
ok(portfolio?.available === true, `portfolio JSON available`, `dataset=${portfolio?.dataset}`);
ok(bs && bs.available === true, 'best_subset 块 available:true', bs?.available === false ? `reason=${bs.reason}` : '');
if (bs?.available === true) {
  ok(Number.isInteger(bs.k) && bs.k >= bs.k_range[0] && bs.k <= bs.k_range[1],
    `K* 在区间内`, `k=${bs.k} 区间 ${bs.k_range?.join('–')}`);
  ok(Array.isArray(bs.factors) && bs.factors.length === bs.k,
    `factors 个数 = K`, `${bs.factors?.length}`);
  const ch = bs.chosen || {};
  ok(ch.train_sharpe != null && ch.holdout_sharpe != null,
    'chosen train/holdout Sharpe 非空', `train=${ch.train_sharpe} holdout=${ch.holdout_sharpe}`);
  ok(Math.abs(ch.train_sharpe - bs.curve?.[0]?.train_sharpe) < 1e-9 || bs.curve?.some((c) => c.k === bs.k),
    'K 扫描曲线含 K* 点', `curve k=${bs.curve?.map((c) => c.k).join(',')}`);
  ok(bs.benchmarks?.equal_weight_all?.n_factors > 0, '等权 1/N 基准在场', `n=${bs.benchmarks?.equal_weight_all?.n_factors}`);
  ok(bs.n_days?.train > 0 && bs.n_days?.holdout > 0, 'n_days 分窗非空', JSON.stringify(bs.n_days));
}

// ── DOM 侧 ───────────────────────────────────────────────────────────
const header = page.getByText('精选子集 · 组合 Sharpe 口径').first();
ok((await header.count()) > 0, '「精选子集 · 组合 Sharpe 口径」节渲染');
if (bs?.available === true) {
  const kChip = page.getByText(new RegExp(`K\\* = ${bs.k}（区间`)).first();
  ok((await kChip.count()) > 0, `K* chip 文案`, `K* = ${bs.k}`);
  ok((await page.getByText(new RegExp(`精选子集 K\\*=${bs.k}`)).count()) > 0, '对照表首行');
  ok((await page.getByText(/等权 1\/N（\d+ 个）/).count()) > 0, '对照表 1/N 行');
  // 因子 chip 个数（按钮名=因子名）
  let chipHit = 0;
  for (const f of bs.factors.slice(0, 5)) {
    if ((await page.getByRole('button', { name: f.name, exact: true }).count()) > 0) chipHit++;
  }
  ok(chipHit === Math.min(5, bs.factors.length), `前 ${Math.min(5, bs.factors.length)} 个因子 chip 可点`, `命中 ${chipHit}`);
}

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.slice(0, 2).join(' | '));

await page.screenshot({ path: SHOT, fullPage: false });
console.log(`  截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
