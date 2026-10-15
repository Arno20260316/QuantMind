/**
 * 美股市场分析 · 估值主题「市值分层下钻个股」实机验收探针（2026-10-15）。
 *
 * 覆盖用户原话：「市值分层这个也是只统计，不能点进去显示个股呢」。
 * 断言链：
 *   1. 估值主题 tab 打开，市值分层卡出现（概览层 4 行分层，行可点击）；
 *   2. 点「超大盘」行 → 发 `/valuation/size-tiers/drill?tier=mega` 且 200；
 *   3. 下钻层渲染：面包屑「市值分层」返回入口 + 摘要条（成分 / 涨跌 / 中位涨跌）
 *      + 成分表首行为已知超大市值标的（NVDA/英伟达 或 AAPL/苹果）；
 *   4. 摘要「成分 N 只」的 N 与响应 summary.members 一致（整层口径，非当前页）；
 *   5. 点成分行 → 个股终端浮窗打开，标题栏 symbol == 该行 symbol（点对了票）；
 *   6. 关闭终端 → 面包屑返回 → 概览层 4 行分层恢复；全程零 pageerror。
 *
 * 用法：node electron/tests/probe_us_tier_drill.mjs
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
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
// 单步定位上限收紧：轮询循环里的 innerText 即使失配也最多 15s，不会拖死探针
page.setDefaultTimeout(15000);

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

const dismissModals = async () => {
  for (let i = 0; i < 8; i++) {
    const btn = page.locator('button', { hasText: /稍后再答/ });
    if ((await btn.count()) === 0) break;
    await btn.first().click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
};

/** 完成首启投资适当性评估（弹层是 ant-modal-wrap，会吃掉鼠标事件）。 */
const completeRiskQuiz = async () => {
  const modal = page.locator('.ant-modal');
  if (!(await modal.isVisible().catch(() => false))) return;
  const groups = modal.locator('.ant-radio-group');
  const n = await groups.count();
  for (let i = 0; i < n; i++) {
    await groups.nth(i).locator('.ant-radio-wrapper').first().click({ timeout: 5000 }).catch(() => {});
  }
  await modal.locator('button', { hasText: '提交评估' }).click({ timeout: 5000 }).catch(() => {});
  await modal.waitFor({ state: 'hidden', timeout: 10000 }).catch(() => {});
};

// ── 登录 ──
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
  if (!page.url().includes('auth/login')) break;
  await page.waitForTimeout(1000);
}
await dismissModals();
for (let w = 0; w < 60 && page.url().includes('auth/login'); w++) {
  await page.waitForTimeout(1000);
}
if (page.url().includes('auth/login')) {
  console.log('\n[FATAL] 登录失败，仍停在 /auth/login');
  await browser.close();
  process.exit(2);
}

// ── 切到美股 + 估值主题 tab ──
await completeRiskQuiz();
await page.evaluate(() => {
  localStorage.setItem('qm:current_market', 'US');
  location.hash = '#/market-analysis';
});
await page.reload({ waitUntil: 'domcontentloaded' });
await completeRiskQuiz();
await dismissModals();

const usTitle = page.locator('h1', { hasText: '美股市场多维分析' });
const landed = await usTitle.waitFor({ state: 'visible', timeout: 60000 })
  .then(() => true).catch(() => false);
if (!landed) {
  console.log('\n[FATAL] 未落在美股市场分析页');
  await page.screenshot({ path: '/tmp/qm_us_tier_landing_fail.png' });
  await browser.close();
  process.exit(2);
}

// 点「估值主题」tab（NAV_TABS 按钮）
const tabBtn = page.locator('button', { hasText: '估值主题' }).first();
await tabBtn.waitFor({ state: 'visible', timeout: 30000 });
await tabBtn.click();

// ── ① 概览层：市值分层卡 4 行可点击 ──
const tierHeader = page.locator('h3', { hasText: '市值分层' }).first();
await tierHeader.waitFor({ state: 'visible', timeout: 60000 });
const tierRowBtns = page.locator('button[title^="查看"][title$="成分股"]');
let tierN = 0;
for (let w = 0; w < 60; w++) {
  tierN = await tierRowBtns.count();
  if (tierN > 0) break;
  await page.waitForTimeout(500);
}
ok(tierN === 4, '概览层 4 行分层且行可点击', `实际 ${tierN} 行`);
await page.screenshot({ path: '/tmp/qm_us_tier_overview.png' });
if (tierN === 0) {
  console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
  await browser.close();
  process.exit(1);
}

// ── ② 点「超大盘」→ 下钻请求 ──
const firstTierBtn = tierRowBtns.first();
const firstTierTitle = await firstTierBtn.getAttribute('title');
await firstTierBtn.scrollIntoViewIfNeeded();
const [drillResp] = await Promise.all([
  page.waitForResponse(
    (r) => r.url().includes('/market-analysis-us/valuation/size-tiers/drill'),
    { timeout: 60000 },
  ),
  firstTierBtn.click(),
]);
ok(drillResp.status() === 200, '下钻响应 200', `status=${drillResp.status()}`);
const drillUrl = drillResp.url();
ok(/tier=mega/.test(drillUrl), '下钻请求带 tier 参数',
  decodeURIComponent((drillUrl.match(/tier=[^&]+/) || ['?'])[0]));
const drillJson = await drillResp.json().catch(() => null);
ok(!!drillJson && Array.isArray(drillJson.items) && drillJson.items.length > 0,
  '下钻响应含成分股', drillJson ? `${drillJson.items.length} 只 (${firstTierTitle || ''})` : '(no json)');

// ── ③ 下钻层渲染：面包屑 + 摘要条 ──
const backBtn = page.locator('button', { hasText: '市值分层' }).first();
await backBtn.waitFor({ state: 'visible', timeout: 30000 });
ok(true, '面包屑「市值分层」返回入口出现');

// 等成分行出现（下钻数据到位），并从**最内层**含行按钮的卡片读文本
const itemRow = page.locator('button[title="点击打开个股终端"]').first();
await itemRow.waitFor({ state: 'visible', timeout: 30000 });
const drillCard = page.locator('div.rounded-2xl', {
  has: page.locator('button[title="点击打开个股终端"]'),
}).last();
const cardText = await drillCard.innerText().catch(() => '');
const m = cardText.match(/成分\s*([\d,]+)\s*只/);
const sumText = m ? m[1] : '';
const expectMembers = String(drillJson?.summary?.members ?? '');
ok(sumText !== '' && sumText.replace(/,/g, '') === expectMembers,
  '摘要「成分 N 只」与响应 members 一致（整层口径）',
  `页面 ${sumText} / 响应 ${expectMembers}`);
ok(/中位涨跌/.test(cardText), '摘要含中位涨跌指标');
ok(/涨\/跌/.test(cardText), '摘要含涨跌家数指标');
await page.screenshot({ path: '/tmp/qm_us_tier_drill.png' });

// 首行应为超大市值标的（NVDA 或 AAPL，中文名 英伟达/苹果）
const firstItem = drillJson?.items?.[0];
ok(!!firstItem && ['英伟达', '苹果'].includes(firstItem.name),
  '成分表首行为超大盘头部标的', firstItem ? `${firstItem.name}(${firstItem.symbol})` : '(无)');

// ── ④ 点成分行 → 个股终端 ──
const rowText = (await itemRow.innerText()).trim();
// 行内 symbol（mono 小字）：行内 ASCII 大写 token 即 symbol（名称是中文）
const rowSymbol = (rowText.match(/[A-Z.]{1,6}/g) || [])[0] || '';
await itemRow.scrollIntoViewIfNeeded();
await itemRow.click();

const termTitle = page.locator('span', { hasText: /^个股终端$/ }).first();
await termTitle.waitFor({ state: 'visible', timeout: 60000 });
ok(true, '个股终端浮窗打开', `行=${rowText.split('\n')[0]}`);
const chip = page.locator('span.font-mono.font-bold.text-slate-500', { hasText: /^[A-Z.]{1,6}$/ }).first();
const chipText = (await chip.innerText().catch(() => '')).trim();
ok(rowSymbol !== '' && chipText === rowSymbol,
  '终端打开的是点中的那只票（标题栏 symbol 一致）', `期望 ${rowSymbol} / 实际 ${chipText || '(无)'}`);
await page.screenshot({ path: '/tmp/qm_us_tier_terminal.png' });

// ── ⑤ 关闭终端 → 返回概览层 ──
await page.locator('button[title="关闭"]').first().click({ timeout: 10000 }).catch(() => {});
await page.waitForTimeout(400);
await backBtn.click();
let restored = 0;
for (let w = 0; w < 30; w++) {
  restored = await tierRowBtns.count();
  if (restored === 4) break;
  await page.waitForTimeout(500);
}
ok(restored === 4, '面包屑返回概览层，4 行分层恢复', `实际 ${restored} 行`);
await page.screenshot({ path: '/tmp/qm_us_tier_back.png' });

// ── 收尾 ──
ok(pageErrors.length === 0, '全程零 pageerror', pageErrors.slice(0, 2).join(' | '));
console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
console.log('截图: /tmp/qm_us_tier_overview.png / qm_us_tier_drill.png / qm_us_tier_terminal.png / qm_us_tier_back.png');
await browser.close();
process.exit(fail === 0 ? 0 : 1);
