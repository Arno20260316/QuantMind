/**
 * 因子概率板 · 入选因子清单默认折叠探针（v6 反馈 9）。
 *
 * 覆盖：
 *   - 「入选因子（N）」卡片首屏仅渲染 4 行预览（N>4 时），折叠态按钮
 *     「展开全部 N 条」在屏且 aria-expanded=false；
 *   - 点展开 → 行数 = N、按钮变「收起」、aria-expanded=true；
 *   - 点收起 → 回到 4 行预览；
 *   - N≤4（极端产物）→ 不出现折叠按钮且行数 = N；
 *   - 无页面 JS 错误。
 *
 * 用法：node electron/tests/probe_factor_board_fold.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_factor_board_fold.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_factor_board_fold.png';
const PREVIEW_ROWS = 4;

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
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

console.log(`\n===== 因子概率板 · 入选因子折叠探针 @ ${BASE} =====`);

// ── 登录（样板同 probe_flow_tags_fusion.mjs）──────────────────────────
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

await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);

const tab = page.getByRole('button', { name: '因子概率板' }).first();
ok((await tab.count()) > 0, '「因子概率板」tab 在场');
if (!(await tab.count())) { await browser.close(); process.exit(1); }

const boardP = page.waitForResponse(
  (r) => r.url().includes('/market-analysis/factor-board') && !r.url().includes('members') && r.status() === 200,
  { timeout: 90000 },
);
await tab.click();
const boardResp = await boardP.catch(() => null);
ok(boardResp !== null, '因子板取数返回 200（/factor-board）');

const boardJson = boardResp ? await boardResp.json().catch(() => null) : null;
const nFactors = boardJson?.factors?.length ?? -1;
ok(nFactors > 0, '因子板 JSON 含入选因子清单', `N=${nFactors}`);

const card = page
  .locator('div.rounded-2xl')
  .filter({ has: page.locator('h3', { hasText: '入选因子' }) })
  .last();
await card.waitFor({ timeout: 30000 });
const headerText = (await card.locator('h3').innerText()).replace(/\s/g, '');
ok(headerText.includes(`入选因子（${nFactors}）`), '卡片标题与 JSON 条数一致', headerText);

const rows = card.locator('tbody tr');
const countRows = () => rows.count();
await page.waitForTimeout(500); // 折叠态稳定

if (nFactors > PREVIEW_ROWS) {
  const collapsed = await countRows();
  ok(collapsed === PREVIEW_ROWS, `默认折叠：首屏仅 ${PREVIEW_ROWS} 行预览`, `DOM=${collapsed}`);

  const expandBtn = card.getByRole('button', { name: `展开全部 ${nFactors} 条` });
  ok((await expandBtn.count()) === 1 && (await expandBtn.isVisible()), '「展开全部 N 条」按钮在屏');
  ok((await expandBtn.getAttribute('aria-expanded')) === 'false', '折叠态 aria-expanded=false');

  await expandBtn.click();
  await page.waitForTimeout(400);
  const expanded = await countRows();
  ok(expanded === nFactors, '点展开：全量行渲染', `DOM=${expanded}`);
  const collapseBtn = card.getByRole('button', { name: '收起' });
  ok((await collapseBtn.count()) === 1, '展开态按钮变「收起」');
  ok((await collapseBtn.getAttribute('aria-expanded')) === 'true', '展开态 aria-expanded=true');

  await collapseBtn.click();
  await page.waitForTimeout(400);
  ok((await countRows()) === PREVIEW_ROWS, `点收起：回到 ${PREVIEW_ROWS} 行预览`);
} else {
  ok((await countRows()) === nFactors, 'N≤4：全量行渲染（无折叠）', `DOM=${nFactors}`);
  const foldBtn = card.getByRole('button', { name: /展开全部|^收起$/ });
  ok((await foldBtn.count()) === 0, 'N≤4：不出现折叠按钮');
}

await page.waitForTimeout(300);
await page.screenshot({ path: SHOT, fullPage: false });
ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.length ? pageErrors.join(' | ') : '');

console.log(`\n截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
