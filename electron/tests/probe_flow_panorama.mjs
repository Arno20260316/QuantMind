/**
 * 资金流全景 v2（桑基四层 + 板块⇄个股双向工作台）实机验收探针。
 *
 * 双路核对（样板同 probe_factor_portfolio_bestsubset.mjs）：
 *   - JSON：/money-flow/panorama | /sector | /stock 三端点契约（节点分层/链接非负/
 *     方向过滤符号/成员与归因/披露与证据 meta）
 *   - DOM ：工具栏与披露条、桑基 canvas、板块榜、板块明细、个股卡、归因回跳，
 *     以及**内部工单号零命中**（界面上不准出现 T-xx-00 / P0-0 之类开发代号）
 *
 * 用法：node electron/tests/probe_flow_panorama.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_flow_panorama.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_flow_panorama.png';

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

console.log(`\n===== 资金流全景 v2 实机探针 @ ${BASE} =====`);

// ── 登录（样板同 probe_factor_portfolio_bestsubset.mjs）────────────────
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

// ── 进入市场分析 → 切「资金流全景」（HashRouter 必须带 #/）─────────────
await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500);

const tab = page.getByRole('button', { name: '资金流全景', exact: true }).first();
ok((await tab.count()) > 0, '「资金流全景」tab 在场');
const [panoResp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/panorama'), { timeout: 60000 }),
  tab.click(),
]);
const pano = await panoResp.json();

// ── JSON 侧：panorama 契约 ────────────────────────────────────────────
ok(pano?.available === true, 'panorama available:true', `trade_date=${pano?.trade_date} reason=${pano?.meta?.reason ?? ''}`);
const kinds = new Set((pano?.nodes || []).map((n) => n.kind));
ok(['entity', 'size', 'sector', 'stock'].every((k) => kinds.has(k)), '四层节点齐备', [...kinds].join(','));
const layers = new Set((pano?.nodes || []).map((n) => n.layer));
ok([1, 2, 3, 4].every((l) => layers.has(l)), 'layer 1..4 齐备', [...layers].sort().join(','));
const names = new Set((pano?.nodes || []).map((n) => n.name));
ok((pano?.links || []).length > 0 && (pano?.links || []).every((l) => l.value >= 0), '链接非负', `${pano?.links?.length} 条`);
ok((pano?.links || []).every((l) => names.has(l.source) && names.has(l.target)), '链接端点全部存在');
const sectorNodes = (pano?.nodes || []).filter((n) => n.kind === 'sector');
ok(sectorNodes.every((n) => 'streak' in n), '板块节点携带 streak 字段（可为 null）');
ok(sectorNodes.every((n) => !('evidence' in n) || n.evidence === null || typeof n.evidence === 'object'), '板块 evidence 结构合法');
const stockNodes = (pano?.nodes || []).filter((n) => n.kind === 'stock');
ok(stockNodes.every((n) => !n.evidence), '个股节点无预测徽章（实测否决）');
ok((pano?.sectors || []).length > 0, '板块 master 榜非空', `${pano?.sectors?.length} 行`);
ok(pano?.meta?.unit === 'yuan', 'unit=yuan', pano?.meta?.unit);
ok(Array.isArray(pano?.meta?.disclosure) && pano.meta.disclosure.length > 0, '常驻披露非空');
ok(pano?.meta?.evidence && typeof pano.meta.evidence.reason === 'string', '证据 meta.reason 在场', pano?.meta?.evidence?.reason);
ok(
  pano?.meta?.coverage?.covered_symbols > 0 && pano?.meta?.coverage?.flow_symbols >= pano.meta.coverage.covered_symbols,
  '覆盖率数字自洽',
  `${pano?.meta?.coverage?.covered_symbols}/${pano?.meta?.coverage?.flow_symbols}`,
);

// ── DOM 侧：工具栏 + 桑基 + 板块榜 ─────────────────────────────────────
ok(await page.getByText('主力与散户资金流全景 · 四层桑基').first().isVisible().catch(() => false), '桑基标题渲染');
ok((await page.locator('canvas').count()) >= 1, 'canvas 渲染（桑基）', `${await page.locator('canvas').count()} 个`);
ok(await page.getByText('板块榜 · 延续状态优先').first().isVisible().catch(() => false), '板块榜标题渲染');
for (const label of ['窗口', '体系', '方向', '个股数']) {
  ok((await page.getByText(label, { exact: true }).count()) > 0, `工具栏「${label}」组在场`);
}
const firstSectorName = pano?.sectors?.[0]?.name;
ok((await page.getByText(firstSectorName, { exact: false }).count()) > 0, `榜首板块「${firstSectorName}」可见`);

// ── 方向过滤：净流出 → 板块符号全负 ───────────────────────────────────
const [panoOutResp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/panorama'), { timeout: 60000 }),
  page.getByRole('button', { name: '净流出', exact: true }).click(),
]);
const panoOut = await panoOutResp.json();
ok(panoOut?.direction === 'outflow', 'direction=outflow 生效');
const outSecNodes = (panoOut?.nodes || []).filter((n) => n.kind === 'sector');
ok(
  outSecNodes.length > 0 && outSecNodes.every((n) => n.sign <= 0),
  '净流出：桑基板块节点全为非正',
  `${outSecNodes.length} 个`,
);
ok(
  (panoOut?.sectors || []).length === (pano?.sectors || []).length,
  'master 榜不随方向过滤（完整榜单）',
  `${panoOut?.sectors?.length} 行`,
);
const [panoAllResp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/panorama'), { timeout: 60000 }),
  page.getByRole('button', { name: '全部', exact: true }).click(),
]);
await panoAllResp.json();
await page.waitForTimeout(600);

// ── 交互：点板块榜首行 → 板块明细 ────────────────────────────────────
const [secResp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/sector?'), { timeout: 60000 }),
  page.getByText(firstSectorName, { exact: false }).first().click(),
]);
const sec = await secResp.json();
ok(sec?.available === true && sec?.name === firstSectorName, `板块明细 available`, `${sec?.name} members=${sec?.members?.length}`);
ok((sec?.members || []).length > 0, '成分贡献榜非空');
ok((sec?.trend || []).length > 0, '20 日趋势非空', `${sec?.trend?.length} 点`);
ok(
  (sec?.members || []).every((m, i, arr) => i === 0 || Math.abs(arr[i - 1].net_main) >= Math.abs(m.net_main) - 1e-6),
  '成分按 |净额| 降序',
);
ok(await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）').first().isVisible().catch(() => false), '明细区渲染');
ok((await page.locator('canvas').count()) >= 2, '趋势图 canvas 追加', `${await page.locator('canvas').count()} 个`);

// ── 交互：点首个成分 → 个股卡（含板块归因）────────────────────────────
const firstMember = sec.members[0];
const [stkResp] = await Promise.all([
  page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/stock?'), { timeout: 60000 }),
  page.getByText(firstMember.symbol, { exact: true }).first().click(),
]);
const stk = await stkResp.json();
ok(stk?.available === true && stk?.symbol === firstMember.symbol, '个股详情 available', `symbol=${stk?.symbol}`);
ok((stk?.series || []).length > 0 && stk.series.length <= 20, '20 日序列长度合法', `${stk?.series?.length} 点`);
ok(stk?.windows?.['1d'] && stk?.windows?.['5d'] && stk?.windows?.['20d'], '1/5/20 日窗口齐备');
ok((stk?.attribution || []).length > 0, '板块归因非空', `${stk?.attribution?.length} 条`);
ok(stk?.factor === null || stk?.factor === undefined, 'factor 槽位为 null（P3 对接位）');
ok(await page.getByText('所属板块归因（点击回板块视角）').first().isVisible().catch(() => false), '个股卡归因区渲染');
for (const lbl of ['当日', '5日', '20日']) {
  ok((await page.getByText(lbl, { exact: true }).count()) > 0, `窗口卡「${lbl}」在场`);
}
const stockBody = await page.locator('body').innerText();
const discloseHead = (stk?.meta?.disclosure?.[0] || '').slice(0, 12);
ok(discloseHead.length > 0 && stockBody.includes(discloseHead), '个股不具预测力披露可见', discloseHead);

// ── 交互：归因 chip 回跳板块（个股 → 板块 反向联动）───────────────────
const jump = (stk?.attribution || []).find((a) => a.name !== firstSectorName);
if (jump) {
  const chip = page
    .locator('button')
    .filter({ hasText: new RegExp(`${jump.inner_rank}/${jump.inner_total}`) })
    .filter({ hasText: jump.name })
    .first();
  const chipFound = (await chip.count()) > 0;
  ok(chipFound, `归因 chip「${jump.name}」可定位`);
  if (chipFound) {
    const [jumpResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/market-analysis/money-flow/sector?'), { timeout: 60000 }),
      chip.click(),
    ]);
    ok(jumpResp.ok(), '回跳板块取数成功', `HTTP ${jumpResp.status()}`);
    await page.waitForTimeout(600);
    ok(
      await page.getByText(jump.name, { exact: false }).first().isVisible().catch(() => false),
      `明细切到「${jump.name}」`,
    );
  }
} else {
  ok(false, '归因里存在其他板块可回跳', '所有归因均指向当前板块');
}

// ── 关闭个股卡 ───────────────────────────────────────────────────────
const closeBtn = page.getByLabel('关闭个股详情');
if (await closeBtn.count()) {
  await closeBtn.first().click();
  await page.waitForTimeout(500);
  ok((await page.getByText('所属板块归因（点击回板块视角）').count()) === 0, '个股卡关闭');
} else {
  ok(false, '个股卡关闭按钮在场');
}

// ── 界面禁内部工单号 ─────────────────────────────────────────────────
const bodyText = await page.locator('body').innerText();
const bannedPatterns = [
  [/\bT-[A-Z]{1,3}-\d{1,2}\b/, 'T-xx-00 工单号'],
  [/\bP\d-\d{1,2}\b/, 'P0-0 阶段号'],
  [/开发代号|内部工单|ticket/i, '内部开发用语'],
];
for (const [re, label] of bannedPatterns) {
  const hit = bodyText.match(re);
  ok(!hit, `界面零命中：${label}`, hit ? `命中「${hit[0]}」` : '');
}

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.slice(0, 2).join(' | '));

await page.screenshot({ path: SHOT, fullPage: false });
console.log(`  截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
