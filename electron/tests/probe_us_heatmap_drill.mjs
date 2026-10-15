/**
 * 美股市场分析 · GICS 板块热力图「下钻个股」实机验收探针（2026-10-15）。
 *
 * 覆盖用户原话：「热力板块图写进到个股、可以点击」。断言链（缺一环都可能是假的）：
 *   1. 板块层真的画出来了：卡片头出现「N 个板块」（N ≥ 5，修复前事故态是 1）；
 *   2. 悬停出现 tooltip（含「领涨龙头」行）——证明 canvas 上有可交互色块；
 *   3. 点击色块 → 发 `/heatmap/drill?sector=<英文类名>` 且 200 —— 下钻契约成立；
 *   4. 钻取层渲染：面包屑「板块热力图 › x」出现 + 摘要行含「只 · 中位」；
 *   5. 个股层 tooltip 比板块层多出「量比」行（下钻附加字段真的透传了）；
 *   6. 点个股方块的色块 → 个股终端浮窗打开，标题栏 symbol 与下钻响应里
 *      该名称对应的 symbol 一致（点对了票，不是随便开了个窗）；
 *   7. 关闭终端 → 面包屑返回板块层，卡片头恢复「N 个板块」；全程零 pageerror。
 *
 * 用法：node electron/tests/probe_us_heatmap_drill.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_us_heatmap_drill.mjs
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

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

/** 清扫合规弹窗（非风险问卷的其它 ant-modal）。 */
const dismissModals = async () => {
  for (let i = 0; i < 8; i++) {
    const btn = page.locator('button', { hasText: /稍后再答/ });
    if ((await btn.count()) === 0) break;
    await btn.first().click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
};

/**
 * 完成首启投资适当性评估（5 题全选第一个选项 → 提交评估）。
 * 不能只点「稍后再答」：跳过只写 7 天 snooze，探测用**全新 context**（空 localStorage），
 * 每次都会重弹 —— 而且弹层是 ant-modal-wrap，会盖住页面吃掉鼠标悬停
 * （实探：热力图 tooltip 探不到的真因就是弹层挡着）。
 * 走完整提交会写 qm:risk_profile_v1 留痕，后续 reload 不再弹。
 */
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

// ── 登录（同 probe_factor_report_jump.mjs：轮询等挂载，失败硬闸） ──
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

// ── 切到美股市场 ──────────────────────────────────────────────────────
// 市场偏好（qm:current_market）只在**文档加载时**读入 Redux store；而改 hash 是同文档导航
// （不重建 store，探测会停在 A 股页）。必须先写偏好、再整页 reload 到目标路由。
await completeRiskQuiz(); // 先答完问卷，reload 后不再弹层挡鼠标
await page.evaluate(() => {
  localStorage.setItem('qm:current_market', 'US');
  location.hash = '#/market-analysis';
});
await page.reload({ waitUntil: 'domcontentloaded' });
await completeRiskQuiz();
await dismissModals();

// 先确认真的落在美股页（否则下面的等待会报误导性的超时）
const usTitle = page.locator('h1', { hasText: '美股市场多维分析' });
const landed = await usTitle.waitFor({ state: 'visible', timeout: 60000 })
  .then(() => true).catch(() => false);
if (!landed) {
  const market = await page.evaluate(() => localStorage.getItem('qm:current_market'));
  console.log(`\n[FATAL] 未落在美股市场分析页（qm:current_market=${market}）`);
  await page.screenshot({ path: '/tmp/qm_us_drill_landing_fail.png' });
  await browser.close();
  process.exit(2);
}

// ── ① 板块层渲染 ──────────────────────────────────────────────────────
const header = page.locator('h3', { hasText: 'GICS 板块热力图' });
await header.waitFor({ state: 'visible', timeout: 120000 });

// 卡片头「N 个板块 · 点击方块下钻成分股」——计数行在数据加载前就渲染（显示 0），
// 必须轮询等到 > 0，否则会读到「0 个板块」的加载态。
const sectorCountLine = page.locator('span', { hasText: /个板块 · 点击方块下钻/ }).first();
await sectorCountLine.waitFor({ state: 'visible', timeout: 60000 });
let countText = '';
let sectorN = 0;
for (let w = 0; w < 60; w++) {
  countText = (await sectorCountLine.innerText()).trim();
  const m = countText.match(/(\d+)\s*个板块/);
  sectorN = m ? Number(m[1]) : 0;
  if (sectorN > 0) break;
  await page.waitForTimeout(500);
}
ok(sectorN >= 5, '板块层板块数 ≥ 5（修复前事故态为 1）', countText);

// 卡片内 canvas（沿 canvas 反向找 rounded-2xl 卡片容器——**不能**用 h3 反找：
// 下钻后面包屑替换了「GICS 板块热力图」文本，h3 选择器会失配，之后每次
// card.innerText() 都要等 30s 默认超时，轮询循环直接拖死探针（实测挂 10 分钟）。
// canvas 在两层都在，是稳定锚点）
const canvas = page.locator('canvas').first();
const card = canvas.locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
// 单步定位上限收紧：即使选择器失配也最多等 15s，轮询循环保持有界
page.setDefaultTimeout(15000);
await canvas.waitFor({ state: 'visible', timeout: 60000 });
// 热力图在首屏之外：必须先滚进视口，否则 mouse.move 的坐标落在视口外，悬停永远不触发
await card.scrollIntoViewIfNeeded();
await page.waitForTimeout(600);
let box = await canvas.boundingBox();
ok(!!box && box.width > 200 && box.height > 200, '热力图 canvas 尺寸正常',
  box ? `${Math.round(box.width)}x${Math.round(box.height)}` : '(no box)');

/** 在热力图上按候选点悬停，直到 tooltip 出现含 `probe` 文本；返回 {x,y,text} 或 null。
 *  tooltip 定位用 echarts 的容器特征（div 带 inline `position: absolute`）——
 *  用 hasText 找 div 会命中卡片标题/最深子行（`.last()` 只拿到「领涨龙头: …」单行，
 *  `.first()` 又会拿到卡片根），都不是整张 tooltip。 */
const hoverUntilTooltip = async (probe, points) => {
  for (const [fx, fy] of points) {
    const x = box.x + box.width * fx;
    const y = box.y + box.height * fy;
    await page.mouse.move(x - 30, y - 30);
    await page.mouse.move(x, y, { steps: 3 });
    await page.waitForTimeout(500);
    const tip = card.locator('div[style*="position: absolute"]').filter({ hasText: probe }).last();
    const visible = await tip.isVisible().catch(() => false);
    if (visible) return { x, y, text: (await tip.innerText()).trim() };
  }
  return null;
};

const POINTS = [
  [0.2, 0.3], [0.5, 0.5], [0.35, 0.7], [0.7, 0.3], [0.6, 0.65], [0.85, 0.5],
];

// ── ② 板块层 tooltip（「领涨龙头:」带冒号 = tooltip 独有；标题里的无冒号） ──
const l1Hover = await hoverUntilTooltip('领涨龙头:', POINTS);
ok(!!l1Hover, '板块层悬停出现 tooltip（含领涨龙头行）', l1Hover ? l1Hover.text.split('\n')[0] : '(无)');
if (!l1Hover) {
  await page.screenshot({ path: '/tmp/qm_us_drill_l1_fail.png' });
  console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
  await browser.close();
  process.exit(1);
}
const l1Name = l1Hover.text.split('\n')[0].trim();
ok(/总市值/.test(l1Hover.text), '板块 tooltip 面积口径=总市值', l1Hover.text.replace(/\n/g, ' | '));
ok(/中位涨跌幅/.test(l1Hover.text), '板块 tooltip 涨跌幅口径=中位（非「平均」）');
await page.screenshot({ path: '/tmp/qm_us_drill_l1_tooltip.png' });

// ── ③ 点击板块 → 下钻请求（sector=<英文类名>，200） ────────────────────
const [drillResp] = await Promise.all([
  page.waitForResponse(
    (r) => r.url().includes('/market-analysis-us/heatmap/drill'),
    { timeout: 60000 },
  ),
  page.mouse.click(l1Hover.x, l1Hover.y),
]);
ok(drillResp.status() === 200, '下钻响应 200', `status=${drillResp.status()}`);
const drillUrl = drillResp.url();
ok(/sector=/.test(drillUrl), '下钻请求带 sector 参数',
  decodeURIComponent((drillUrl.match(/sector=[^&]+/) || ['?'])[0]));
const drillJson = await drillResp.json().catch(() => null);
ok(!!drillJson && Array.isArray(drillJson.items) && drillJson.items.length > 0,
  '下钻响应含成分股', drillJson ? `${drillJson.items?.length} 只` : '(no json)');

// ── ④ 钻取层渲染：面包屑 + 摘要行 ──────────────────────────────────────
const backBtn = page.locator('button', { hasText: '板块热力图' });
await backBtn.waitFor({ state: 'visible', timeout: 30000 });
ok(true, '面包屑「板块热力图」返回入口出现');
// 摘要行（只数 + 中位涨幅）：轮询卡片文本，失败时 dump 卡片全文便于定位
let summaryText = '';
for (let w = 0; w < 40; w++) {
  const txt = await card.innerText().catch(() => '');
  const m = txt.match(/\d{4}-\d{2}-\d{2}[^\n]*?只[^\n]*?中位[^\n]*/);
  if (m) { summaryText = m[0].trim(); break; }
  await page.waitForTimeout(500);
}
ok(!!summaryText, '钻取层摘要行（只数 + 中位涨幅）', summaryText || (await card.innerText()).slice(0, 200).replace(/\n/g, ' | '));
await page.screenshot({ path: '/tmp/qm_us_drill_l2_tiles.png' });

// ── ⑤ 个股层 tooltip：比板块层多「量比」行（extra 透传验证） ───────────
await page.waitForTimeout(600); // 等钻取层重绘完成
await card.scrollIntoViewIfNeeded();
await page.waitForTimeout(300);
box = await canvas.boundingBox(); // 钻取层文案行变化可能微移 canvas，重取坐标
const l2Hover = await hoverUntilTooltip('量比', POINTS);
ok(!!l2Hover, '个股层悬停 tooltip 含「量比」行（下钻附加字段透传）',
  l2Hover ? l2Hover.text.replace(/\n/g, ' | ') : '(无)');
if (!l2Hover) {
  await page.screenshot({ path: '/tmp/qm_us_drill_l2_fail.png' });
  console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
  await browser.close();
  process.exit(1);
}
const l2Name = l2Hover.text.split('\n')[0].trim();
// 该名称在钻取响应中的 symbol（点对了票的判据）
const expectSymbol = (drillJson.items.find((it) => it.name === l2Name) || {}).symbol || '';
ok(!!expectSymbol, `tooltip 名称在钻取响应中有对应 symbol`, `${l2Name} → ${expectSymbol || '?'}`);

// ── ⑥ 点击个股 → 个股终端浮窗打开 ─────────────────────────────────────
await page.mouse.click(l2Hover.x, l2Hover.y);
const termTitle = page.locator('span', { hasText: /^个股终端$/ }).first();
await termTitle.waitFor({ state: 'visible', timeout: 60000 });
ok(true, '个股终端浮窗打开');
// 标题栏 symbol chip（个股终端 标题旁的 mono chip）
const chip = page.locator('span.font-mono.font-bold.text-slate-500', { hasText: /^[A-Z.]{1,6}$/ }).first();
const chipText = (await chip.innerText().catch(() => '')).trim();
ok(expectSymbol !== '' && chipText === expectSymbol,
  '终端打开的是点中的那只票（标题栏 symbol 一致）', `期望 ${expectSymbol} / 实际 ${chipText || '(无)'}`);
await page.screenshot({ path: '/tmp/qm_us_drill_terminal.png' });

// ── ⑦ 关闭终端 → 面包屑返回 → 板块层恢复 ──────────────────────────────
await page.locator('button[title="关闭"]').first().click({ timeout: 10000 }).catch(() => {});
await page.waitForTimeout(400);
ok((await termTitle.count()) === 0 || !(await termTitle.isVisible().catch(() => false)),
  '终端已关闭');

await backBtn.click();
const restored = page.locator('span', { hasText: /个板块 · 点击方块下钻/ }).first();
await restored.waitFor({ state: 'visible', timeout: 30000 });
ok(true, '面包屑返回板块层，卡片头恢复「N 个板块」');
await page.screenshot({ path: '/tmp/qm_us_drill_back.png' });

// ── 收尾 ────────────────────────────────────────────────────────────────
ok(pageErrors.length === 0, '全程零 pageerror', pageErrors.slice(0, 2).join(' | '));
console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
console.log('截图: /tmp/qm_us_drill_l1_tooltip.png（板块 tooltip）/ /tmp/qm_us_drill_l2_tiles.png（个股层）/');
console.log('      /tmp/qm_us_drill_terminal.png（终端浮窗）/ /tmp/qm_us_drill_back.png（返回板块层）');
await browser.close();
process.exit(fail === 0 ? 0 : 1);
