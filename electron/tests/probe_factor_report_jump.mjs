/**
 * 单因子详情 ⇄ 因子报告 双向跳转 实机验收探针（2026-10-15）。
 *
 * 覆盖用户原话的两件事：① 单因子详情里有个按钮跳进因子报告的这个因子；
 * ② 因子报告里也有按钮跳回单因子分析，**来回切换**。断言链（缺一环都可能是假的）：
 *   1. 正向入口出现且**带对库**：私人库 amt_log 的来源库是 l1_factors（wind_source
 *      反查），按钮 title 必须显示 l1_factors 的报告快照标签「L1 因子…」——
 *      拿别的库去问，报告页只会显示「该因子不在快照里」，不会说库选错了；
 *   2. 点击后落到**因子报告页签**且深链生效：单因子页卸载（按钮消失），
 *      /factor-report/detail 请求带 factor=amt_log **且 dataset=l1_factors**
 *      （来源库直定位 = 核心契约）；
 *   3. 报告面板真的把 amt_log 展示出来（DOM 层）；
 *   4. **反跳**：报告页出现「单因子分析」入口（title 指 amt_log），点击回单因子页，
 *      且数据集回到私人库（扫描按钮出现）——反跳资格由目录索引判，回错库会是一屏
 *      「该因子不可用」；
 *   5. **第二程**：再点正向入口，深链仍带 dataset=l1_factors、反跳入口又出现
 *      （来回切换闭环成立），全程零 pageerror。
 *
 * 用法：node electron/tests/probe_factor_report_jump.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_factor_report_jump.mjs
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

/** 清扫合规弹窗（首启风险问卷 / 「稍后再答」，登录后会再弹）。 */
const dismissModals = async () => {
  for (let i = 0; i < 8; i++) {
    const btn = page.locator('button', { hasText: /稍后再答/ });
    if ((await btn.count()) === 0) break;
    await btn.first().click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
};

// ── 登录（同 probe_factor_leaderboard_dedup.mjs：必须轮询等挂载，失败硬闸） ──
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
  await page.screenshot({ path: '/tmp/qm_factor_report_login_fail.png' });
  await browser.close();
  process.exit(2);
}

// ── ① 因子研究页：默认私人库、目录默认展开 ─────────────────────────────
await page.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' });
const searchBox = page.locator('input[placeholder="搜索因子…"]');
await searchBox.waitFor({ state: 'visible', timeout: 120000 });
await dismissModals();
const scanBtn = page.locator('[data-testid="open-scan"]');
const hasScan = await scanBtn.count() > 0;
ok(hasScan, '默认数据集 = 私人因子库（扫描按钮只在私人库出现）');

// ── ② 搜 amt_log → 点开单因子分析 ──────────────────────────────────────
await searchBox.fill('amt_log');
const entry = page.locator('div.cursor-pointer', { hasText: 'amt_log' }).first();
await entry.waitFor({ state: 'visible', timeout: 90000 });
await dismissModals();
await entry.click();

const btn = page.locator('[data-testid="open-factor-report"]');
await btn.waitFor({ state: 'visible', timeout: 180000 });
const title = await btn.getAttribute('title');
ok(/L1 因子/.test(title || ''), '入口 title 带来源库报告快照标签', title || '(no title)');
ok((title || '').includes('amt_log'), '入口 title 指向当前因子 amt_log');
await page.screenshot({ path: '/tmp/qm_factor_report_btn.png' });

// ── ③ 点击 → 因子报告页签 + 深链（dataset=l1_factors & factor=amt_log） ──
const [resp] = await Promise.all([
  page.waitForResponse(
    (r) => r.url().includes('/factor-report/detail') && r.url().includes('factor=amt_log'),
    { timeout: 180000 },
  ),
  btn.click(),
]);
ok(resp.status() === 200, '报告 detail 响应 200', `status=${resp.status()}`);
ok(resp.url().includes('dataset=l1_factors'),
  '深链落到来源库数据集 l1_factors（不是默认 alpha_library）',
  (resp.url().match(/dataset=[^&]+/) || ['?'])[0]);

await page.waitForTimeout(1200);
ok((await btn.count()) === 0, '单因子页已切走（入口按钮消失）');
const body = await page.innerText('body');
ok(body.includes('amt_log'), '报告面板 DOM 里出现 amt_log');
ok(body.includes('L1 因子'), '报告面板数据集标签含「L1 因子」');
await page.screenshot({ path: '/tmp/qm_factor_report_jumped.png' });

// ── ④ 反跳：回单因子分析看同一个因子（数据集须回到私人库） ──────────────
const back = page.locator('[data-testid="open-single-factor"]');
await back.waitFor({ state: 'visible', timeout: 180000 });
const backTitle = await back.getAttribute('title');
ok((backTitle || '').includes('amt_log'), '反跳入口 title 指向当前因子 amt_log', backTitle || '(no title)');
await page.screenshot({ path: '/tmp/qm_factor_single_back_btn.png' });

await back.click();
const fwd = page.locator('[data-testid="open-factor-report"]');
await fwd.waitFor({ state: 'visible', timeout: 180000 });
ok((await back.count()) === 0, '已回单因子页（反跳入口消失）');
const fwdTitle = await fwd.getAttribute('title');
ok(/L1 因子/.test(fwdTitle || '') && (fwdTitle || '').includes('amt_log'),
  '回到的是同一个因子（正向入口 title 仍指 amt_log / L1 因子）', fwdTitle || '(no title)');
ok((await page.locator('[data-testid="open-scan"]').count()) > 0,
  '数据集回到私人库（扫描按钮只在私人库出现）');
await page.screenshot({ path: '/tmp/qm_factor_back_to_single.png' });

// ── ⑤ 第二程：再进报告，深链仍带来源库，反跳入口又出现 ─────────────────
const [resp2] = await Promise.all([
  page.waitForResponse(
    (r) => r.url().includes('/factor-report/detail') && r.url().includes('factor=amt_log'),
    { timeout: 180000 },
  ),
  fwd.click(),
]);
ok(resp2.status() === 200, '第二程前进：detail 响应 200', `status=${resp2.status()}`);
ok(resp2.url().includes('dataset=l1_factors'), '第二程仍落来源库 l1_factors（不是默认 alpha_library）');
await page.waitForTimeout(1200);
ok((await page.locator('[data-testid="open-single-factor"]').count()) > 0, '又回到报告页（反跳入口再次出现）');
await page.screenshot({ path: '/tmp/qm_factor_roundtrip_report2.png' });

// ── 收尾 ────────────────────────────────────────────────────────────────
ok(pageErrors.length === 0, '全程零 pageerror', pageErrors.slice(0, 2).join(' | '));
console.log(`\n===== 结果：${pass} passed / ${fail} failed =====`);
console.log('截图: /tmp/qm_factor_report_btn.png（正向入口）/ /tmp/qm_factor_report_jumped.png（报告落点）/');
console.log('      /tmp/qm_factor_single_back_btn.png（反跳入口）/ /tmp/qm_factor_back_to_single.png（回到单因子）/');
console.log('      /tmp/qm_factor_roundtrip_report2.png（第二程再进报告）');
await browser.close();
process.exit(fail === 0 ? 0 : 1);
