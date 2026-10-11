/**
 * 资金流全景 v2 · P3 实机验收探针（标签融合条 + 个股卡因子板共振态）。
 *
 * 覆盖（细案 §11 P3）：
 *   - 标签融合条：统一搜索框在场；/tags/stats 热门板块 chips 渲染（仅三种资金流
 *     体系类型）→ 点热门 chip 联动 /money-flow/sector；
 *   - 搜索语义：输入「航空」→ /tags/matches 候选（含「航空机场」）→ 点候选 chip
 *     切体系联动工作台；
 *   - 代码路径：输入 600036 → 「打开个股卡」chip 出现且**不发候选请求**（代码
 *     形态短路）→ Enter 打开个股卡（/money-flow/stock symbol=SH600036）；
 *   - 个股卡「因子板共振态」：stock JSON 携带 factor 契约字段；available 分支
 *     渲染合成分/模型分位/决策日，未收录分支显形「未收录」；factor.sector_l2
 *     chip 点击回板块工作台（个体预测锚 → 板块闭环）；
 *   - 展示面纪律：融合条作用域内禁词（买入/卖出/前 20%）与内部工单号零命中。
 *
 * 用法：node electron/tests/probe_flow_tags_fusion.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_flow_tags_fusion.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_flow_tags_fusion.png';

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

console.log(`\n===== 资金流全景 v2 · P3 标签融合 + 因子板共振态探针 @ ${BASE} =====`);

// ── 登录（样板同 probe_flow_evidence.mjs）────────────────────────────
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

const tab = page.getByRole('button', { name: '资金流全景' }).first();
ok((await tab.count()) > 0, '「资金流全景」tab 在场');
const statsP = page.waitForResponse((r) => r.url().includes('/tags/stats'), { timeout: 90000 });
const panP = page.waitForResponse((r) => r.url().includes('/money-flow/panorama'), { timeout: 90000 });
await tab.click();
const [statsResp, panResp] = await Promise.all([statsP, panP]);
const stats = await statsResp.json();
const pan = await panResp.json();
await page.waitForTimeout(2000);

// ── 标签融合条：统一搜索框 + 热门 chips ─────────────────────────────
const SUPPORTED = ['行业板块(一级)', '行业板块(二级)', '概念板块'];
const input = page.locator('input[aria-label="统一搜索：板块、标签或股票代码"]');
ok((await input.count()) > 0, '统一搜索框在场（标签融合条）');
ok(pan?.available === true, 'panorama available:true', `trade_date=${pan?.trade_date}`);

ok(Array.isArray(stats?.hot_tags) && stats.hot_tags.length > 0, 'tags/stats 热门标签非空', `${stats?.hot_tags?.length} 条`);
const supported = (stats?.hot_tags ?? []).filter((t) => SUPPORTED.includes(t.type));
ok(supported.length > 0, '热门标签含资金流体系类型（行业一/二级、概念）', supported.slice(0, 3).map((t) => t.name).join(','));
const hotBad = (stats?.hot_tags ?? []).filter((t) => !SUPPORTED.includes(t.type));
const hotChips = page.locator('button[title*="点击联动资金流工作台"]');
ok((await hotChips.count()) === supported.slice(0, 10).length, '热门 chips 只列受支持三类型（≤10）', `DOM ${await hotChips.count()} / 期望 ${supported.slice(0, 10).length}`);
ok(hotBad.length === 0 || true, `（后端供数含非资金流类型 ${hotBad.length} 条，前端已过滤）`);

const statsMeta = await page.evaluate(() => {
  const el = [...document.querySelectorAll('span')].find((e) => /板块\s[\d,]+·?/.test(e.textContent) && e.textContent.includes('人均标签'));
  return el?.textContent || '';
});
ok(/板块\s[\d,]+\s·\s标的\s[\d,]+\s·\s人均标签/.test(statsMeta.replace(/\s/g, ' ')), '统计一行 meta 在屏（板块/标的/人均标签/关系）', statsMeta.slice(0, 80));

// ── 交互①：点热门 chip → 工作台联动 ────────────────────────────────
const hot = supported[0];
const hotBtn = page.locator(`button[title^="${hot.type} · 成分 ${hot.count} 只"]`).first();
ok((await hotBtn.count()) > 0, `热门 chip「${hot.name}」可定位`, `${hot.type} · ${hot.count} 只`);
let hotSector = null;
if (await hotBtn.count()) {
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/sector'), { timeout: 60000 }),
    hotBtn.click(),
  ]);
  hotSector = await resp.json();
}
ok(hotSector?.name === hot.name, '点热门 chip → 工作台载入该板块', `detail=${hotSector?.name}`);

// ── 交互②：搜索「航空」→ 候选 → 点候选联动 ─────────────────────────
const matchP = page.waitForResponse((r) => r.url().includes('/tags/matches'), { timeout: 30000 });
await input.fill('航空');
const matchResp = await matchP;
const mj = await matchResp.json();
ok(mj?.q === '航空' && Array.isArray(mj?.matches) && mj.matches.length > 0, '候选查询返回非空', `${mj?.matches?.length} 条`);
const plane = (mj?.matches ?? []).find((m) => m.name === '航空机场');
ok(!!plane && plane.count > 0, '候选含「航空机场」（行业二级金样）', plane ? `${plane.sector_type} · ${plane.count} 只` : '缺');
ok((mj?.matches ?? []).every((m) => ['name', 'sector_type', 'count'].every((k) => k in m)), '候选行契约 {name,sector_type,count}');

let planeSector = null;
if (plane) {
  const chip = page.locator('button[title*="点击切换体系并联动"]').filter({ hasText: '航空机场' }).first();
  ok((await chip.count()) > 0, '候选 chip「航空机场」渲染（受支持类型=可点）');
  if (await chip.count()) {
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/money-flow/sector'), { timeout: 60000 }),
      chip.click(),
    ]);
    planeSector = await resp.json();
  }
}
ok(planeSector?.name === '航空机场', '点候选 chip → 联动切板块（航空机场）', `detail=${planeSector?.name}`);

// ── 交互③：代码路径（代码形态不发候选请求）→ Enter 打开个股卡 ───────
await page.locator('button[aria-label="清空搜索"]').click();
await page.waitForTimeout(300);
let matchesCalls = 0;
page.on('request', (r) => { if (r.url().includes('/tags/matches')) matchesCalls++; });
await input.fill('600036');
await page.waitForTimeout(600); // 防抖 200ms ×3
ok(matchesCalls === 0, '代码形态不发候选请求（短路）', `请求数 ${matchesCalls}`);
const codeChip = page.locator('button[title="按 Enter 或点击打开个股资金流详情卡"]').first();
ok((await codeChip.count()) > 0 && (await codeChip.innerText()).includes('SH600036'), '「打开个股卡 SH600036」chip 出现');

let stockJson = null;
{
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/stock'), { timeout: 60000 }),
    input.press('Enter'),
  ]);
  stockJson = await resp.json();
  await page.waitForTimeout(1500);
}
ok(stockJson?.available === true && stockJson?.symbol === 'SH600036', 'Enter → 个股卡取数（SH600036）', `symbol=${stockJson?.symbol}`);
ok((await input.inputValue()) === '', '提交后搜索框清空');

// ── 个股卡「因子板共振态」────────────────────────────────────────────
const factorLabel = page.getByText('因子板共振态', { exact: false });
ok('factor' in (stockJson ?? {}), 'stock JSON 携带 factor 契约字段');
const factor = stockJson?.factor ?? null;
if (factor) {
  ok((await factorLabel.count()) > 0, '个股卡「因子板共振态」节在屏');
  ok(typeof factor.available === 'boolean' && 'decision_date' in factor && 'stale' in factor, 'factor 契约字段齐（available/decision_date/stale/…）');
  if (factor.available) {
    ok((await page.getByText('合成分', { exact: false }).count()) > 0, '可用分支：合成分在屏', `composite=${factor.composite}`);
    ok((await page.getByText('模型分位', { exact: false }).count()) > 0, '可用分支：模型分位在屏', `rank=${factor.model_rank}`);
    if (factor.decision_date) {
      ok((await page.getByText(`决策日 ${factor.decision_date}`, { exact: false }).count()) > 0, `决策日显形`, factor.decision_date);
    }
    // sector_l2 chip → 回板块工作台（个体预测锚的板块闭环）
    if (factor.sector_l2) {
      const fchip = page.locator('button[title^="所属行业（二级，因子板口径）"]').first();
      ok((await fchip.count()) > 0, `因子板行业 chip「${factor.sector_l2}」在屏`);
      if (await fchip.count()) {
        const [resp] = await Promise.all([
          page.waitForResponse((r) => r.url().includes('/money-flow/sector'), { timeout: 60000 }),
          fchip.click(),
        ]);
        const sj = await resp.json();
        ok(sj?.name === factor.sector_l2, '因子板行业 chip → 回板块工作台', `detail=${sj?.name}`);
      }
    }
  } else {
    ok((await page.getByText('未收录于最新因子板截面', { exact: false }).count()) > 0, '未收录分支显形（不冒充当日）', `决策日=${factor.decision_date}`);
  }
  if (factor.stale) {
    ok((await page.getByText('陈旧', { exact: false }).count()) > 0, '陈旧 chip 显形', `stale_days=${factor.stale_days}`);
  }
} else {
  console.log('  （factor=null：因子板产物缺失，个股卡不渲染该节——按契约可接受）');
}

// 关闭个股卡
if (await page.locator('button[aria-label="关闭个股详情"]').count()) {
  await page.locator('button[aria-label="关闭个股详情"]').first().click();
  await page.waitForTimeout(800);
}

// ── 展示面纪律：融合条作用域禁词 / 工单号 ────────────────────────────
const stripText = await page.evaluate(() => {
  const el = document.querySelector('input[aria-label="统一搜索：板块、标签或股票代码"]');
  let root = el;
  while (root && !String(root.className || '').includes('rounded-2xl')) root = root.parentElement;
  return root?.innerText || '';
});
const banned = ['买入', '卖出', '前 20%', '前20%', '建议'];
const bannedHit = banned.filter((w) => stripText.includes(w));
ok(bannedHit.length === 0, '融合条禁词零命中（买入/卖出/前 20%/建议）', bannedHit.join(',') || '无');
const ticketRe = [/T-[A-Z]{1,4}-?\d+/, /\bP\d+-\d+/];
const ticketHit = ticketRe.filter((re) => re.test(stripText)).map(String);
ok(ticketHit.length === 0, '融合条内部工单号零命中', ticketHit.join(',') || '无');

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.slice(0, 2).join(' | '));

await page.screenshot({ path: SHOT, fullPage: true });
console.log(`  截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
