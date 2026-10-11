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
 *   - v6 热门体系选择器：热门区三 tab（行业二级/行业一级/概念）与页面体系同源同步；
 *     点 tab 即切体系（panorama sectors= 联动重取）+ 按体系取热门
 *     （/tags/stats?hot_type= 单体系 top-18 chips）；回切走缓存不重复取数；
 *   - v6 文字搜股：输入「茅台」并行触发 /stock-terminal/list 个股联想 → 个股候选
 *     chip「贵州茅台」→ 点击打开个股卡（SH600519）；
 *   - v7 双向定位：热门 chip / 候选 chip 跳转自动下钻首位成分（个股卡取数 =
 *     members[0] + 该行 aria-current 选中）；检索个股（茅台）→ 成分贡献榜自动
 *     定位其归因 industry2 板块并选中该股行（不在 top30 则按契约不显选中行）；
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

// v6：行业二级热门取数计数——回切体系必须走进程内缓存（不重复请求）
let statsL2Calls = 0;
page.on('request', (r) => {
  const u = r.url();
  if (u.includes('/tags/stats') && decodeURIComponent(u).includes('hot_type=行业板块(二级)')) statsL2Calls++;
});

// /money-flow/sector 响应累计：全景载入后前端会「默认选中排名第一板块」先行取数一次；
// 若随后点击的板块恰与已选中同名，React 状态不变 → 不重发请求。这类场景应命中
// 「已有响应/已就位」而非干等新响应（否则 60s 超时假红）。
let sectorRespCount = 0;
let lastSectorJson = null;
page.on('response', (r) => {
  if (!r.url().includes('/money-flow/sector')) return;
  sectorRespCount += 1;
  r.json().then((j) => { lastSectorJson = j; }).catch(() => {});
});

const clickAndExpectSector = async (locator, name, timeoutMs = 15000) => {
  const before = sectorRespCount;
  await locator.click();
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (sectorRespCount > before && lastSectorJson?.name === name) break;
    if (lastSectorJson?.name === name) break; // 自动选中已就位（点击同名不触发重取）
    await page.waitForTimeout(200);
  }
  return lastSectorJson;
};

/** 等待「指定名称」板块明细到位（v7：检索个股自动定位板块会异步触发取数）。 */
const waitSectorNamed = async (name, timeoutMs = 20000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (lastSectorJson?.name === name) break;
    await page.waitForTimeout(200);
  }
  return lastSectorJson;
};

// /money-flow/stock 响应跟踪（v7：板块跳转自动下钻首位成分 / 检索个股定位均触发取数）
let lastStockJson = null;
page.on('response', (r) => {
  if (!r.url().includes('/money-flow/stock')) return;
  r.json().then((j) => { lastStockJson = j; }).catch(() => {});
});

/** 等待个股卡取数到位（载荷 symbol 匹配；容忍「已是目标」的零等待路径）。 */
const waitStockJson = async (symbol, timeoutMs = 15000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (lastStockJson?.symbol === symbol) break;
    await page.waitForTimeout(200);
  }
  return lastStockJson;
};

/** 若个股卡在屏则关闭（v7：板块跳转会自动开卡，避免干扰后续「同码不重取」断言）。 */
const closeStockIfAny = async () => {
  if (await page.locator('button[aria-label="关闭个股详情"]').count()) {
    await page.locator('button[aria-label="关闭个股详情"]').first().click();
    await page.waitForTimeout(500);
  }
};

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

ok(Array.isArray(stats?.hot_tags) && stats.hot_tags.length > 0, 'tags/stats 热门非空', `${stats?.hot_tags?.length} 条`);
ok(
  statsResp.url().includes('hot_type=') && decodeURIComponent(statsResp.url()).includes('hot_type=行业板块(二级)'),
  '热门按当前体系取数（hot_type 单体系过滤）',
  decodeURIComponent(statsResp.url().split('?')[1] || ''),
);
ok(
  (stats?.hot_tags ?? []).every((t) => SUPPORTED.includes(t.type) && t.type === '行业板块(二级)'),
  '单体系过滤生效（全部为行业板块(二级)）',
  [...new Set((stats?.hot_tags ?? []).map((t) => t.type))].join(','),
);
const hotChips = page.locator('button[title*="点击联动资金流工作台"]');
ok(
  (await hotChips.count()) === stats.hot_tags.length && stats.hot_tags.length === 18,
  '热门 chips = 单体系 top-18（可选 10-20 个）',
  `DOM ${await hotChips.count()} / JSON ${stats?.hot_tags?.length}`,
);

const statsMeta = await page.evaluate(() => {
  const el = [...document.querySelectorAll('span')].find((e) => /板块\s[\d,]+·?/.test(e.textContent) && e.textContent.includes('人均标签'));
  return el?.textContent || '';
});
ok(/板块\s[\d,]+\s·\s标的\s[\d,]+\s·\s人均标签/.test(statsMeta.replace(/\s/g, ' ')), '统计一行 meta 在屏（板块/标的/人均标签/关系）', statsMeta.slice(0, 80));

// ── 交互①：点热门 chip → 工作台联动 ────────────────────────────────
const hot = stats.hot_tags[0];
const hotBtn = page.locator(`button[title^="${hot.type} · 成分 ${hot.count} 只"]`).first();
ok((await hotBtn.count()) > 0, `热门 chip「${hot.name}」可定位`, `${hot.type} · ${hot.count} 只`);
let hotSector = null;
if (await hotBtn.count()) {
  hotSector = await clickAndExpectSector(hotBtn, hot.name);
}
ok(hotSector?.name === hot.name, '点热门 chip → 工作台载入该板块', `detail=${hotSector?.name}`);

// v7：热门 chip 跳转自动下钻首位成分（开个股卡 + 选中该行）→ 随后关闭，
// 避免与交互③ Enter 同码时不重取（个股卡 symbol 未变不发新请求）
const hotFirst = (hotSector?.members ?? [])[0];
if (hotFirst) {
  const autoHot = await waitStockJson(hotFirst.symbol);
  ok(autoHot?.symbol === hotFirst.symbol, 'v7：热门 chip 跳转自动下钻首位成分', `symbol=${autoHot?.symbol} vs ${hotFirst.symbol}`);
  const actH = page.locator('button[aria-current="true"]');
  const nH = await actH.count();
  const txtH = nH ? await actH.first().innerText().catch(() => '') : '';
  ok(nH === 1 && txtH.includes(hotFirst.symbol), 'v7：成分贡献榜选中首位成分行（aria-current）', `count=${nH}`);
}
await closeStockIfAny();

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
    planeSector = await clickAndExpectSector(chip, '航空机场');
  }
}
ok(planeSector?.name === '航空机场', '点候选 chip → 联动切板块（航空机场）', `detail=${planeSector?.name}`);

// v7：候选 chip 跳转同样自动下钻首位成分 → 校验后关闭（同交互①）
const planeFirst = (planeSector?.members ?? [])[0];
if (planeFirst) {
  const autoPlane = await waitStockJson(planeFirst.symbol);
  ok(autoPlane?.symbol === planeFirst.symbol, 'v7：候选 chip 跳转自动下钻首位成分', `symbol=${autoPlane?.symbol} vs ${planeFirst.symbol}`);
}
await closeStockIfAny();

// ── 交互③：代码路径（代码形态不发候选请求）→ Enter 打开个股卡 ───────
await page.locator('button[aria-label="清空搜索"]').click();
await page.waitForTimeout(300);
let matchesCalls = 0;
let stockListCalls = 0;
page.on('request', (r) => {
  if (r.url().includes('/tags/matches')) matchesCalls++;
  if (r.url().includes('/stock-terminal/list')) stockListCalls++;
});
await input.fill('600036');
await page.waitForTimeout(600); // 防抖 200ms ×3
ok(matchesCalls === 0 && stockListCalls === 0, '代码形态不发候选/联想请求（短路）', `calls=${matchesCalls}/${stockListCalls}`);
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
        const sj = await clickAndExpectSector(fchip, factor.sector_l2);
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

// ── v6-①：热门体系选择器（同源同步 + 单体系取数 + 回切缓存） ─────────
const stripTabState = () =>
  page.evaluate(() => {
    const el = document.querySelector('input[aria-label="统一搜索：板块、标签或股票代码"]');
    let root = el;
    while (root && !String(root.className || '').includes('rounded-2xl')) root = root.parentElement;
    if (!root) return [];
    return [...root.querySelectorAll('button')]
      .filter((b) => ['行业二级', '行业一级', '概念'].includes(b.textContent.trim()))
      .map((b) => ({ label: b.textContent.trim(), active: String(b.className).includes('bg-purple-600') }));
  });
const clickStripTab = (label) =>
  page.evaluate((lb) => {
    const el = document.querySelector('input[aria-label="统一搜索：板块、标签或股票代码"]');
    let root = el;
    while (root && !String(root.className || '').includes('rounded-2xl')) root = root.parentElement;
    const b = root && [...root.querySelectorAll('button')].find((x) => x.textContent.trim() === lb);
    if (b) { b.click(); return true; }
    return false;
  }, label);

const tabState0 = await stripTabState();
ok(tabState0.length === 3, '热门区三体系选择器在屏（行业二级/行业一级/概念）', tabState0.map((t) => t.label).join(' / '));
ok(tabState0.find((t) => t.label === '行业二级')?.active === true, '选择器高亮与页面体系同步（当前=行业二级）');

const conceptStatsP = page.waitForResponse(
  (r) => r.url().includes('/tags/stats') && decodeURIComponent(r.url()).includes('hot_type=概念板块'),
  { timeout: 30000 },
);
const conceptPanP = page.waitForResponse(
  (r) => r.url().includes('/money-flow/panorama') && r.url().includes('sectors=concept'),
  { timeout: 90000 },
);
ok(await clickStripTab('概念'), '点热门区「概念」选择器');
const [conceptStatsResp, conceptPanResp] = await Promise.all([conceptStatsP, conceptPanP]);
const conceptStats = await conceptStatsResp.json();
ok(
  (conceptStats?.hot_tags ?? []).length > 0 && conceptStats.hot_tags.every((t) => t.type === '概念板块'),
  '概念热门按体系取数',
  `${conceptStats?.hot_tags?.length} 条`,
);
ok((await conceptPanResp.json())?.sector_set === 'concept', '切体系 → panorama 联动重取（sectors=concept）');
await page.waitForTimeout(800);
const conceptChips = page.locator('button[title^="概念板块 · 成分"]');
ok(
  (await conceptChips.count()) === conceptStats.hot_tags.length,
  '热门 chips 随体系切换（概念 top-18）',
  `DOM ${await conceptChips.count()} / JSON ${conceptStats.hot_tags.length}`,
);

const backPanP = page.waitForResponse(
  (r) => r.url().includes('/money-flow/panorama') && r.url().includes('sectors=industry2'),
  { timeout: 90000 },
);
ok(await clickStripTab('行业二级'), '点回「行业二级」选择器');
await backPanP;
await page.waitForTimeout(800);
const l2Chips = page.locator('button[title^="行业板块(二级) · 成分"]');
ok(
  (await l2Chips.count()) === stats.hot_tags.length,
  '回切行业二级：热门即刻恢复（缓存直显）',
  `DOM ${await l2Chips.count()} / JSON ${stats.hot_tags.length}`,
);
ok(statsL2Calls === 1, '行业二级热门仅首取 1 次（回切不重复取数）', `calls=${statsL2Calls}`);

// ── v6-②：文字搜股（名称）→ 个股候选 chip → 点击开个股卡 ─────────────
const stockListP = page.waitForResponse((r) => r.url().includes('/stock-terminal/list'), { timeout: 30000 });
await input.fill('茅台');
const stockListResp = await stockListP;
const slJson = await stockListResp.json();
const slItems = slJson?.data?.items ?? slJson?.items ?? [];
ok(Array.isArray(slItems) && slItems.length > 0, '文字搜索并行触发个股联想（/stock-terminal/list）', `${slItems.length} 条`);
await page.waitForTimeout(600);
const maotaiChip = page.locator('button[title^="个股 贵州茅台"]').first();
ok((await maotaiChip.count()) > 0, '个股候选 chip「贵州茅台」在屏（文字搜股）');
if (await maotaiChip.count()) {
  const [mkResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/stock'), { timeout: 60000 }),
    maotaiChip.click(),
  ]);
  const mkJson = await mkResp.json();
  ok(
    mkJson?.available === true && mkJson?.symbol === 'SH600519',
    '点个股候选 chip → 个股卡取数（SH600519）',
    `symbol=${mkJson?.symbol}`,
  );
  ok((await input.inputValue()) === '', '个股 chip 提交后搜索框清空');

  // v7：检索个股 → 成分贡献榜自动定位到该股所属板块并选中其行。
  // 重要事实：行业二级税目（80 板块）无酒类——茅台在上游成员表中**没有二级归属**，
  // 归因回退链 industry2 → industry1 落到「酿酒」，页面体系随之同步切换（诚实呈现）。
  const attsM = mkJson?.attribution ?? [];
  const expectPick =
    attsM.find((a) => a.sector_set === 'industry2') ??
    attsM.find((a) => a.sector_set === 'industry1') ??
    attsM.find((a) => a.sector_set != null);
  if (expectPick) {
    const sj = await waitSectorNamed(expectPick.name);
    await page.waitForTimeout(800); // 等响应落地后的 DOM 渲染（明细头部/榜单行）
    ok(
      sj?.name === expectPick.name && sj?.sector_set === expectPick.sector_set,
      `v7：检索个股自动定位所属板块「${expectPick.name}」（归因 ${expectPick.sector_set}）`,
      `detail=${sj?.name}@${sj?.sector_set}`,
    );
    ok((await page.getByText(expectPick.name, { exact: true }).count()) > 0, 'v7：明细头部显示归属板块名');
    const inTop = (sj?.members ?? []).some((m) => m.symbol === 'SH600519');
    const actM = page.locator('button[aria-current="true"]');
    const nM = await actM.count();
    const txtM = nM ? await actM.first().innerText().catch(() => '') : '';
    if (inTop) {
      ok(nM === 1 && txtM.includes('SH600519'), 'v7：成分贡献榜选中该股行（aria-current）', `count=${nM}`);
    } else {
      ok(nM === 0, 'v7：该股不在 top30 成分榜（按契约不显选中行）', `count=${nM}`);
    }
  } else {
    console.log('  （attribution 全部无体系项：跳过检索定位断言）');
  }
  await closeStockIfAny();
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
