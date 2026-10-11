/**
 * 资金流全景 v2 · 延续证据台（P2）实机验收探针。
 *
 * 覆盖（细案 §11）：
 *   - JSON 双路：/money-flow/panorama（四层结构 + 链接契约）与 /money-flow/evidence
 *     （产物在、命中榜新鲜 hits_state=ok、档案三档 gate、否决清单 8 项、披露非空）；
 *   - DOM：桑基卡 / 延续证据台 / 信号档案 / 否决清单四节渲染，命中卡片数与 JSON 一致；
 *   - 交互闭环：命中榜行 → 工作台板块明细（/money-flow/sector）→ 成分下钻个股卡
 *     （/money-flow/stock）→ 归因 chip 回板块 → 命中 #2 联动切板块 → 关闭个股卡；
 *   - 徽章证据卡弹层：hover「连N」徽章 → 探针口径 / 可执行口径 / 分年 / 口径披露；
 *   - 展示面纪律：作用域内禁词（买入/卖出/前 20%）零命中、内部工单号零命中、
 *     「已实测不具预测力」文案在屏。
 *
 * 前置：证据产物已构建且跟到最新 L2 分区（hits_state=ok），否则先跑
 *   docker exec -w /app quantmind python backend/scripts/market_analysis/build_flow_evidence.py
 *
 * 用法：node electron/tests/probe_flow_evidence.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_flow_evidence.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_flow_evidence.png';

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

console.log(`\n===== 资金流全景 v2 · 延续证据台实机探针 @ ${BASE} =====`);

// ── 登录（样板同 probe_factor_portfolio_bestsubset.mjs）───────────────
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

// ── 进入市场分析 → 切「资金流全景」tab（HashRouter 带 #/）──────────────
await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);

const tab = page.getByRole('button', { name: '资金流全景' }).first();
ok((await tab.count()) > 0, '「资金流全景」tab 在场');
const panP = page.waitForResponse((r) => r.url().includes('/money-flow/panorama'), { timeout: 90000 });
const evP = page.waitForResponse((r) => r.url().includes('/money-flow/evidence'), { timeout: 90000 });
await tab.click();
const [panResp, evResp] = await Promise.all([panP, evP]);
const pan = await panResp.json();
const ev = await evResp.json();
await page.waitForTimeout(2500);

// ── JSON 侧：panorama 四层与契约 ─────────────────────────────────────
ok(pan?.available === true, 'panorama available:true', `trade_date=${pan?.trade_date}`);
const kinds = new Set((pan?.nodes ?? []).map((n) => n.kind));
ok(['entity', 'size', 'sector', 'stock'].every((k) => kinds.has(k)), '四层节点齐（entity/size/sector/stock）', [...kinds].join(','));
const names = new Set((pan?.nodes ?? []).map((n) => n.name));
const linkBad = (pan?.links ?? []).filter(
  (l) => !names.has(l.source) || !names.has(l.target) || l.value < 0 || Math.abs(l.value - Math.abs(l.net)) > 1e-6,
);
ok(linkBad.length === 0, '链接契约（端点在场 / value=|net|≥0）', `违例 ${linkBad.length}`);
ok((pan?.sectors ?? []).length >= 20, '板块行 ≥ 20', `${pan?.sectors?.length}`);

// ── JSON 侧：evidence 产物与四节数据 ─────────────────────────────────
ok(ev?.available === true, 'evidence available:true', ev?.reason ? `reason=${ev.reason}` : '');
ok(ev?.hits_state === 'ok', '命中榜新鲜（hits_state=ok）', `state=${ev?.hits_state} 产物日=${ev?.hits_trade_date}；陈旧请先重建产物`);
ok(Array.isArray(ev?.hits) && ev.hits.length > 0, '命中榜行数 > 0', `${ev?.hits?.length} 行`);
const condKeys = Object.keys(ev?.conditions ?? {}).sort();
ok(condKeys.join(',') === 'streak_2,streak_3,streak_5', '条件三档齐', condKeys.join(','));
const gateBad = condKeys.filter((k) => typeof ev.conditions[k]?.gate?.pass !== 'boolean');
ok(gateBad.length === 0, '每档 gate.pass 为布尔', `违例 ${gateBad.join(',') || '无'}`);
ok((ev?.rejected ?? []).length === 8, '否决清单 8 项', `${ev?.rejected?.length}`);
ok(typeof ev?.conventions?.price_source === 'string' && ev.conventions.price_source.includes('daily_forward'), '口径 price_source 直传', ev?.conventions?.price_source);
ok((ev?.meta?.disclosure ?? []).length > 0, '披露清单非空', `${ev?.meta?.disclosure?.length} 条`);
const badgeHits = (ev?.hits ?? []).filter((h) => h.evidence && h.evidence.fwd?.n > 0);
ok(badgeHits.length > 0, '至少一条命中带证据徽章', `${badgeHits.length}/${ev?.hits?.length}`);

// ── DOM 侧：四节与桑基渲染 ───────────────────────────────────────────
await page.getByText('主力与散户资金流全景 · 四层桑基', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
ok((await page.getByText('主力与散户资金流全景 · 四层桑基', { exact: false }).count()) > 0, '桑基卡标题在屏');
ok((await page.locator('div[_echarts_instance_]').count()) > 0, 'ECharts 实例挂载');
ok((await page.getByText('延续证据台 · 命中榜与信号档案', { exact: false }).count()) > 0, '延续证据台节在屏');
ok((await page.getByText('信号档案 · 板块「连N」历史条件统计', { exact: false }).count()) > 0, '信号档案节在屏');
ok((await page.getByText('否决清单 · 已实测、不具预测力、不参与展示', { exact: false }).count()) > 0, '「已实测、不具预测力」文案在屏');
const hitCards = page.locator('button[title*="点击联动桑基与工作台"]');
ok((await hitCards.count()) === ev.hits.length, '命中卡片数 = JSON 命中行数', `DOM ${await hitCards.count()} / JSON ${ev.hits.length}`);

// ── 展示面禁词与工单号零命中（作用域=资金流全景面板子树）──────────────
const scopeText = await page.evaluate(() => {
  const anchor = [...document.querySelectorAll('h3')].find((e) => e.textContent?.includes('主力与散户资金流全景'));
  let el = anchor;
  while (el && !String(el.className || '').includes('gap-3.5')) el = el.parentElement;
  return (el || anchor?.parentElement)?.innerText || '';
});
const banned = ['买入', '卖出', '前 20%', '前20%'];
const bannedHit = banned.filter((w) => scopeText.includes(w));
ok(bannedHit.length === 0, '展示面禁词零命中（买入/卖出/前 20%）', bannedHit.join(',') || '无');
const ticketRe = [/T-[A-Z]{1,4}-?\d+/, /\bP\d+-\d+/];
const ticketHit = ticketRe.filter((re) => re.test(scopeText)).map(String);
ok(ticketHit.length === 0, '内部工单号零命中', ticketHit.join(',') || '无');
ok(scopeText.includes('不具预测力'), '展示面含「不具预测力」披露', '');

// ── 交互①：命中榜行 → 工作台板块明细 ────────────────────────────────
const hit0 = ev.hits[0];
const escAttr = (s) => s.replace(/"/g, '\\"');
const card0 = page.locator(`button[title^="${escAttr(hit0.name)} ·"]`).first();
ok((await card0.count()) > 0, `命中 #1「${hit0.name}」卡片可定位`);
let sectorJson = null;
if (await card0.count()) {
  const [secResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/sector'), { timeout: 60000 }),
    card0.click(),
  ]);
  sectorJson = await secResp.json();
}
ok(sectorJson?.available === true && sectorJson?.name === hit0.name, '点命中行 → 工作台载入该板块明细', `detail=${sectorJson?.name}`);
await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
ok((await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).count()) > 0, '成分贡献榜在屏');

// ── 交互②：徽章证据卡弹层（hover）────────────────────────────────────
if (badgeHits.length) {
  const bh = badgeHits[0];
  const card = page.locator(`button[title^="${escAttr(bh.name)} ·"]`).first();
  await card.locator('span.cursor-help').first().hover().catch(() => {});
  const pop = page.locator('.ant-popover').filter({ hasText: '探针口径' }).first();
  await pop.waitFor({ state: 'visible', timeout: 6000 }).catch(() => {});
  const popText = (await pop.count()) ? await pop.innerText().catch(() => '') : '';
  ok(/连续净流入 ≥\d+ 日/.test(popText), `徽章卡 label`, popText.match(/连续净流入 ≥\d+ 日/)?.[0] || '');
  ok(popText.includes('探针口径 t→t+3'), '徽章卡含探针口径列');
  ok(popText.includes('可执行口径 T+1 收盘建仓'), '徽章卡含可执行主口径列');
  ok(popText.includes('命中') && popText.includes('基准'), '徽章卡含命中/基准');
  ok(popText.includes('历史超额') && popText.includes('未来收益'), '徽章卡常驻「历史超额≠未来收益」');
  await page.mouse.move(10, 10);
}

// ── 交互③：成分下钻个股卡 ────────────────────────────────────────────
const m0 = (sectorJson?.members ?? []).find((m) => m.name);
let stockJson = null;
if (m0) {
  const [stkResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/stock'), { timeout: 60000 }),
    page.getByRole('button', { name: m0.name }).first().click(),
  ]);
  stockJson = await stkResp.json();
  await page.waitForTimeout(1500);
}
ok(stockJson?.available === true && stockJson?.symbol === m0?.symbol, '点成分股 → 个股卡取数', `symbol=${stockJson?.symbol}`);
ok((await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) > 0, '个股卡「板块归因（回板块）」在屏');

// ── 交互④：归因 chip 回板块（同板块 = 幂等回视）───────────────────────
const chipName = (stockJson?.attribution ?? [])[0]?.name;
if (chipName) {
  // 归因 chip 为 rounded-full（工作台板块行是圆角矩形，避免误点）
  await page.locator('button.rounded-full').filter({ hasText: chipName }).first().click().catch(() => {});
  await page.waitForTimeout(1200);
  ok((await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).count()) > 0, `归因 chip「${chipName}」回板块后工作台在屏`);
}

// ── 交互⑤：命中 #2 联动切板块（个股卡保持）──────────────────────────
const hit1 = ev.hits[1];
if (hit1) {
  const card1 = page.locator(`button[title^="${escAttr(hit1.name)} ·"]`).first();
  const [secResp2] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/sector'), { timeout: 60000 }),
    card1.click(),
  ]);
  const sectorJson2 = await secResp2.json();
  ok(sectorJson2?.name === hit1.name, `命中 #2「${hit1.name}」联动切换工作台`, `detail=${sectorJson2?.name}`);
  ok(stockJson ? (await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) > 0 : true, '切板块后个股卡保持（个股↔板块双向不打断）');
}

// ── 交互⑥：关闭个股卡 ───────────────────────────────────────────────
if (await page.locator('button[aria-label="关闭个股详情"]').count()) {
  await page.locator('button[aria-label="关闭个股详情"]').first().click();
  await page.waitForTimeout(1000);
  ok((await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) === 0, '关闭个股卡后详情卡卸载');
}

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.slice(0, 2).join(' | '));

await page.screenshot({ path: SHOT, fullPage: true });
console.log(`  截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
