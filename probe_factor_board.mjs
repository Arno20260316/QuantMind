// 因子概率板 Tab 实机探针（P1 验收）：登录 → #/market-analysis → 点「因子概率板」
// → 等 API 200 → 断言关键文案与数据 → 截图。
// 确定性等待：以 /factor-board 响应与目标文案可见为准，不用裸 timeout 判成功。
import { chromium } from 'playwright';
const BASE = 'http://localhost:3080';
const OUT = '/tmp/factor_board.png';
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const logs = [];
const fails = [];
page.on('console', m => { if (m.type() === 'error') logs.push('[console.error] ' + m.text().slice(0, 200)); });
page.on('pageerror', e => logs.push('[PAGEERROR] ' + (e.stack || e.message).slice(0, 300)));
page.on('response', r => { if (/\/api\/v1\/market-analysis\/factor-board/.test(r.url())) logs.push('[API] ' + r.status() + ' ' + r.url().replace(BASE, '')); });

// ── 登录 ──
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(3500);
const inputs = page.locator('input');
if (await inputs.count() >= 2) {
  await inputs.nth(0).fill('admin');
  await inputs.nth(1).fill('admin123');
  const btns = page.locator('button');
  const n = await btns.count();
  for (let i = 0; i < n; i++) {
    const t = ((await btns.nth(i).innerText().catch(() => '')) || '').replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.waitForTimeout(5000);
}
logs.push('[URL after login] ' + page.url());

// ── 进入市场分析 ──
await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(4500);

// 登录后可能弹风险测评问卷（「你计划这笔资金投入多长时间？」）遮挡点击：先尝试关闭
for (const sel of ['.ant-modal-close', 'button:has-text("稍后")', 'button:has-text("跳过")', 'button:has-text("取消")', 'button:has-text("关闭")']) {
  const el = page.locator(sel).first();
  if ((await el.count()) > 0 && (await el.isVisible().catch(() => false))) {
    await el.click().catch(() => {});
    logs.push('[UI] 关闭弹窗 via ' + sel);
    await page.waitForTimeout(900);
  }
}

// ── 点「因子概率板」Tab，等 API 200 ──
const tab = page.locator('button', { hasText: '因子概率板' }).first();
if (!(await tab.count())) { fails.push('找不到「因子概率板」Tab 按钮'); }
else {
  const respP = page.waitForResponse(
    r => r.url().includes('/api/v1/market-analysis/factor-board') && r.status() === 200,
    { timeout: 40000 },
  ).catch(() => null);
  await tab.click({ force: true }).catch(async () => { await tab.click(); });
  const resp = await respP;
  if (!resp) fails.push('点击 Tab 后 40s 内未见 factor-board 200 响应');
  else logs.push('[UI] factor-board 200 已确认');
  await page.waitForTimeout(2500);
}

// ── 断言关键文案 ──
const expectTexts = [
  '质检与口径', '决策日', '数据截面', '入选', '合成覆盖',
  '板块共振', '个股四象限', '个股名单', '入选因子（28）', '持有期画像',
  '共振 · 靠前', '共振 · 靠后',
];
const bodyText = ((await page.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
for (const t of expectTexts) {
  if (!bodyText.includes(t)) fails.push(`缺文案: ${t}`);
}
// 展示词纪律：面板内不得出现买卖指令措辞。
// 注：因子特征名（如「资金流 买入 成交额」）是特征本身的名字，不在此列；
// 禁的是信号/建议形态的措辞（判定词映射由 factorBoardVocabulary 单测另行锁定）。
for (const w of ['建议买入', '建议卖出', '买入信号', '卖出信号', '加仓', '减仓']) {
  if (bodyText.includes(w)) fails.push(`展示面出现禁用词: ${w}`);
}
// 数据面抽查
for (const t of ['2026-10-12', 'ρ=', '28']) {
  if (!bodyText.includes(t)) logs.push(`[warn] 未在页面文本中找到: ${t}`);
}
logs.push('[TEXT] ' + bodyText.slice(0, 700));

await page.screenshot({ path: OUT, fullPage: false });
logs.push('[SHOT] ' + OUT);
console.log(logs.join('\n'));
if (fails.length) { console.log('\n=== FAIL ===\n' + fails.join('\n')); process.exitCode = 1; }
else console.log('\n=== PASS ===');
await browser.close();
