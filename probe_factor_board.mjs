// 因子概率板 Tab 实机探针（P1+P2 验收）：登录 → #/market-analysis → 点「因子概率板」
// → 等 API 200 → 断言关键文案与数据 → 截图。
// P2 增量：近 20 日 / 净 IC 两列、去重 / 制度 / 掩码三 chip、滚动 OOS 行。
// 确定性等待：以 /factor-board 响应与目标文案可见为准，不用裸 timeout 判成功。
import { chromium } from 'playwright';
const BASE = 'http://localhost:3080';
const OUT = '/tmp/factor_board.png';
const OUT_FULL = '/tmp/factor_board_full.png';
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

// ── 断言关键文案（结构面；具体数值只记录不钉死，换日重跑不脆） ──
const expectTexts = [
  '质检与口径', '决策日', '数据截面', '入选', '合成覆盖',
  '板块共振', '个股四象限', '个股名单', '持有期画像',
  '共振 · 靠前', '共振 · 靠后',
  '近 20 日', '净 IC', // P2 新列（1.1 产物才有；1.0 整列隐藏）
];
const bodyText = ((await page.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
for (const t of expectTexts) {
  if (!bodyText.includes(t)) fails.push(`缺文案: ${t}`);
}

// ── P2 元素断言：三 chip + 滚动 OOS 行（regex 锚结构，数值进日志供目检） ──
const p2Patterns = [
  ['入选因子计数', /入选因子（(\d+)）/],
  ['去重 chip', /去重：池 ([\d,]+) → 入选 ([\d,]+)（剔 ([\d,]+)）/],
  ['制度 chip', /制度：(\S+?)（弱桶 (\d+) \/ 调整 (\d+)）/],
  ['掩码 chip', /掩码[：: ](\S*)/],
  ['滚动 OOS 行', /上月滚动验证（(\d{4}-\d{2})）：OOS RankIC ([\d.—-]+) \/ 胜率 ([\d.—%]+)/],
];
for (const [name, re] of p2Patterns) {
  const m = bodyText.match(re);
  if (!m) fails.push(`缺 P2 元素: ${name}（${re}）`);
  else logs.push(`[P2] ${name}: ${m[0].slice(0, 90)}`);
}

// 展示词纪律：面板内不得出现买卖指令措辞。
// 注：因子特征名（如「资金流 买入 成交额」）是特征本身的名字，不在此列；
// 禁的是信号/建议形态的措辞（判定词映射由 factorBoardVocabulary 单测另行锁定）。
for (const w of ['建议买入', '建议卖出', '买入信号', '卖出信号', '加仓', '减仓']) {
  if (bodyText.includes(w)) fails.push(`展示面出现禁用词: ${w}`);
}
logs.push('[TEXT] ' + bodyText.slice(0, 700));

await page.screenshot({ path: OUT, fullPage: false });
await page.screenshot({ path: OUT_FULL, fullPage: true });
logs.push('[SHOT] ' + OUT + ' / ' + OUT_FULL);
console.log(logs.join('\n'));
if (fails.length) { console.log('\n=== FAIL ===\n' + fails.join('\n')); process.exitCode = 1; }
else console.log('\n=== PASS ===');
await browser.close();
