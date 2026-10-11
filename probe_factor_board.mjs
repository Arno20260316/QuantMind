// 因子概率板 Tab 实机探针（P1+P2 验收）：登录 → #/market-analysis → 点「因子概率板」
// → 等 API 200 → 断言关键文案与数据 → 截图。
// P2 增量：近 20 日 / 净 IC 两列、去重 / 制度 / 掩码三 chip、滚动 OOS 行。
// 确定性等待：以 /factor-board 响应与目标文案可见为准，不用裸 timeout 判成功。
import { chromium } from 'playwright';
const BASE = 'http://localhost:3080';
const OUT = '/tmp/factor_board.png';
const OUT_FULL = '/tmp/factor_board_full.png';
const OUT_DRILL = '/tmp/factor_board_drill.png';
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
  ['去重 chip', /去重：池 ([\d,]+) → 入选 ([\d,]+)（剔 ([\d,]+)(?: · 警 ([\d,]+) 对)?）/],
  ['制度 chip', /制度：(\S+?)（弱桶 (\d+) \/ 调整 (\d+)）/],
  ['掩码 chip', /掩码[：: ](\S*)/],
  ['滚动 OOS 行', /上月滚动验证（(\d{4}-\d{2})）：OOS RankIC ([\d.—-]+) \/ 胜率 ([\d.—%]+)/],
];
for (const [name, re] of p2Patterns) {
  const m = bodyText.match(re);
  if (!m) fails.push(`缺 P2 元素: ${name}（${re}）`);
  else logs.push(`[P2] ${name}: ${m[0].slice(0, 90)}`);
}

// ── 布局断言：板块|名单 并排一行；持有期|四象限 并排于末行（入选因子居中） ──
async function h3Box(text) {
  const el = page.locator(`h3:has-text("${text}")`).first();
  if (!(await el.count()) || !(await el.isVisible().catch(() => false))) return null;
  return el.boundingBox();
}
const bxSector = await h3Box('板块共振');
const bxList = await h3Box('个股名单');
const bxFactors = await h3Box('入选因子');
const bxHold = await h3Box('持有期画像');
const bxQuad = await h3Box('个股四象限');
if (!bxSector || !bxList || !bxFactors || !bxHold || !bxQuad) {
  fails.push('布局断言：五张卡标题不全');
} else {
  const sameRow = (a, b) => Math.abs(a.y - b.y) < 30 && b.x > a.x + 300;
  if (!sameRow(bxSector, bxList)) fails.push(`「板块共振」与「个股名单」未并排：y=${bxSector.y}/${bxList.y}`);
  if (!sameRow(bxHold, bxQuad)) fails.push(`「持有期画像」与「个股四象限」未并排：y=${bxHold.y}/${bxQuad.y}`);
  if (!(bxSector.y < bxFactors.y && bxFactors.y < bxHold.y)) {
    fails.push(`卡序错位（应为 板块/名单 → 入选因子 → 持有期/四象限）：y=${bxSector.y}/${bxFactors.y}/${bxHold.y}`);
  }
  logs.push(`[LAYOUT] 板块|名单 y=${bxSector.y.toFixed(0)}/${bxList.y.toFixed(0)}；入选因子 y=${bxFactors.y.toFixed(0)}；持有期|四象限 y=${bxHold.y.toFixed(0)}/${bxQuad.y.toFixed(0)}`);
}

// ── 板块钻取断言：点「航空机场」行 → members 200 → 右侧卡切成分视图 → 返回名单可退出 ──
const drillRow = page.locator('div[role="button"][title*="点击查看成分股"]', { hasText: '航空机场' }).first();
if (!(await drillRow.count())) fails.push('找不到「航空机场」板块行（role=button）');
else {
  const mRespP = page.waitForResponse(
    r => r.url().includes('/factor-board/members') && r.status() === 200,
    { timeout: 20000 },
  ).catch(() => null);
  await drillRow.click();
  const mResp = await mRespP;
  if (!mResp) fails.push('点击「航空机场」后 20s 内未见 members 200 响应');
  else {
    const mBody = await mResp.json().catch(() => null);
    const mn = mBody?.n ?? 0;
    if (!Array.isArray(mBody?.items) || mn < 1) fails.push(`members 响应异常：n=${mn}`);
    else {
      const row0 = mBody.items[0];
      for (const k of ['symbol', 'name', 'composite', 'model_rank', 'quadrant']) {
        if (!(k in row0)) fails.push(`members 行缺列: ${k}`);
      }
      logs.push(`[DRILL] members 200 n=${mn} 首行=${row0.symbol} ${row0.name} 象限=${row0.quadrant}`);
    }
  }
  await page.waitForTimeout(900);
  const drillText = ((await page.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
  const dr = page.locator('button:has-text("返回名单")');
  if (!(await dr.count())) fails.push('钻取后未见「返回名单」按钮');
  if (!drillText.includes('板块成分 · 航空机场')) fails.push('钻取后标题未切换为「板块成分 · 航空机场」');
  if (!/海航控股|南方航空|中国东航/.test(drillText)) fails.push('钻取后成分股名未出现');
  await page.screenshot({ path: OUT_DRILL, fullPage: false });
  logs.push('[SHOT] ' + OUT_DRILL);
  if (await dr.count()) {
    await dr.first().click();
    await page.waitForTimeout(500);
    const back = ((await page.locator('body').innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
    if (!back.includes('个股名单')) fails.push('点「返回名单」后未恢复名单视图');
    if (await page.locator('button:has-text("返回名单")').count()) fails.push('返回后「返回名单」按钮仍在');
    logs.push('[DRILL] 返回名单恢复 ✓');
  }
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
