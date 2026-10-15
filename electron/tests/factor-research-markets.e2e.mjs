/**
 * 因子研究四市场 E2E：A股 / 港股 / 美股 / 期货（2026-10-16 市场分层）。
 *
 * 用法：QM_BASE=http://localhost:3099 node tests/factor-research-markets.e2e.mjs
 *      QM_MARKETS=HK 只跑部分市场
 *
 * 走真实用户路径：登录 → 切市场 → 进「因子研究」→ 等排行榜出数 → 点开第一行进
 * 「单因子分析」看基准口径。守的是「换市场」这条缝上的四类静默错——坏了都不报错，
 * 只是**看着像对的**：
 *
 * 1. 请求必须带新市场：少带一处（目录/榜单/详情）那块就还是 A 股口径，
 *    用户切到港股看到的却是 A 股因子，界面没有任何异常；
 * 2. 数据集胶囊按市场收敛：经典库只有 A 股供给（catalog.py 内置清单），
 *    非 CN 面渲染了它，点进去得到的是一块永远空的榜；
 * 3. 基准口径按市场：港股页面出现「沪深300」= 拿 A 股指数算港股超额（数据污染）；
 *    期货无指数基准 → 诚实降级成「超额 vs 基准」（列全「—」），不拿 A 股充数；
 * 4. 应用内换市场清旧：研究页自身没有切换器（顶栏 MarketSelector 只在 ModuleGrid/
 *    回测中心等切面页挂载，研究页 button[role=radio] 实测=0）；真实路径=离开研究页
 *    → 到有切换器的页切市场 → SPA 导航回来。末段专抓「只有整页刷新才对」的假绿。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3099';

const MARKETS = [
  { key: 'CN', radio: 'A股', tag: 'A股', classic: true, bench: '沪深300' },
  { key: 'HK', radio: '港股', tag: '港股', classic: false, bench: '恒生指数' },
  { key: 'US', radio: '美股', tag: '美股', classic: false, bench: '标普500' },
  // 期货无指数基准：单因子表头退「超额 vs 基准」，不给候选也不叠线
  { key: 'FUTURES', radio: '期货', tag: '期货', classic: false, bench: null },
];

const only = (process.env.QM_MARKETS || '').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
const TO_RUN = only.length ? MARKETS.filter((m) => only.includes(m.key)) : MARKETS;

const apiCalls = []; // "status path?query"（保留 query：市场参数就在里面）
const errors = [];
const flat = (s) => s.replace(/\s+/g, ' ');

const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const p = await (await b.newContext({ viewport: { width: 1720, height: 1150 } })).newPage();
p.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 200)); });
p.on('pageerror', (e) => errors.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
p.on('response', (r) => {
  const u = r.url();
  if (/\/api\/v1\/(factor-research|factor-report)\//.test(u)) {
    const q = u.replace(BASE, '').replace(/^https?:\/\/[^/]+/, '');
    apiCalls.push(r.status() + ' ' + q);
    if (r.status() >= 500) errors.push('[API ' + r.status() + '] ' + q.slice(0, 160));
  }
});

// ---- 登录 ----
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await p.waitForTimeout(3500);
if ((await p.locator('input[type=password]').count()) > 0) {
  await p.locator('input').nth(0).fill('admin');
  await p.locator('input[type=password]').first().fill('admin123');
  const btns = p.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await p.waitForTimeout(7000);
}
console.log('登录后:', p.url());

/** 首启「投资适当性评估」弹窗会遮住整页：走「稍后再答」（localStorage 留痕） */
async function dismissRiskModal(page) {
  const later = page.locator('.ant-modal button', { hasText: '稍后再答' }).first();
  const shown = await later.waitFor({ state: 'visible', timeout: 4000 }).then(() => true).catch(() => false);
  if (shown) { await later.click().catch(() => {}); await page.waitForTimeout(800); }
}
await dismissRiskModal(p);

let pass = 0, total = 0;
const check = (ok, label) => { total++; if (ok) pass++; console.log(`${ok ? '✓' : '✗'} ${label}`); };

/** 切市场：点顶栏 radio，轮询 localStorage 确认生效（同推理中心探针的手法） */
async function switchMarket(page, radioLabel, wantKey) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const radio = page.locator('button[role=radio]', { hasText: radioLabel }).first();
    if (await radio.count()) await radio.click({ timeout: 10000 }).catch(() => {});
    for (let i = 0; i < 8; i++) {
      await page.waitForTimeout(700);
      if ((await page.evaluate(() => localStorage.getItem('qm:current_market'))) === wantKey) return true;
    }
  }
  return false;
}

/** 进「因子研究」页（点侧栏导航，真实用户路径） */
async function openResearch(page) {
  const nav = page.locator('a,button', { hasText: '因子研究' }).first();
  await nav.waitFor({ state: 'visible', timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(800);
  await nav.click({ timeout: 8000 }).catch(() => {});
  return page.locator('[data-testid=research-market-tag]').first()
    .waitFor({ state: 'visible', timeout: 30000 }).then(() => true).catch(() => false);
}

/** 等排行榜出数（行数 > 0；CN 私人库 4000+ 因子冷启动可能要一分钟） */
async function waitRows(page, timeout = 120000) {
  return page.waitForFunction(
    () => document.querySelectorAll('[data-testid^="lb-fav-"]').length > 0,
    null, { timeout },
  ).then(() => true).catch(() => false);
}

const requestsWith = (re, since = 0) => apiCalls.slice(since).filter((c) => re.test(c));

for (const mk of TO_RUN) {
  console.log(`\n========== ${mk.key} 因子研究 ==========`);
  await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await dismissRiskModal(p);
  const switched = await switchMarket(p, mk.radio, mk.key);
  check(switched, `市场已切到 ${mk.key}`);

  const since = apiCalls.length;
  const opened = await openResearch(p);
  check(opened, '「因子研究」页面已挂载（市场标签可见）');
  if (!opened) continue;

  const tag = (await p.locator('[data-testid=research-market-tag]').first().innerText().catch(() => '')).trim();
  check(tag === mk.tag, `市场标签「${mk.tag}」（实测「${tag}」）`);

  const rows = await waitRows(p);
  check(rows, '排行榜已出数（行数 > 0，本市场快照真实出数）');
  let f = flat(await p.locator('body').innerText().catch(() => ''));
  check(f.includes('私人因子库'), '私人库胶囊在场');
  check(mk.classic ? f.includes('经典因子') : !f.includes('经典因子'),
    mk.classic ? 'CN：经典库胶囊在场' : '非 CN：经典库胶囊不渲染（它只有 A 股供给）');

  // 请求全部带本市场：目录/榜单少带一处不会报错，只会静默给旧口径
  const mkRe = new RegExp(`[?&]market=${mk.key}(&|$)`);
  check(requestsWith(/\/factor-research\/leaderboard/, since).some((c) => mkRe.test(c)),
    `排行榜请求带 market=${mk.key}`);
  check(requestsWith(/\/factor-research\/catalog/, since).some((c) => mkRe.test(c)),
    `目录请求带 market=${mk.key}`);
  check(!requestsWith(/\/factor-research\//, since).some((c) => c.includes('market=CN') && mk.key !== 'CN'),
    '非 CN 市场：无一条 factor-research 请求落 CN 兜底');

  if (rows) {
    // 点行进「单因子分析」：落点=名字格 lb-name-（冒泡到 tr 的 onOpenSingle）。
    // 不可点 lb-fav-（收藏星）——星/勾选/标签格都 e.stopPropagation()，点了只切收藏，
    // 详情永不打开（首跑 CN 在此静默等满 90s 超时，页面无任何报错）。
    await p.locator('[data-testid^="lb-name-"]').first().click();
    await p.waitForTimeout(500);
    // 点行进「单因子分析」：等基准口径表头出现
    const want = mk.bench ? `超额 vs ${mk.bench}` : '超额 vs 基准';
    const got = await p.locator(`text=${want}`).first()
      .waitFor({ state: 'visible', timeout: 90000 }).then(() => true).catch(() => false);
    f = flat(await p.locator('body').innerText().catch(() => ''));
    check(got, `单因子基准口径「${want}」`);
    if (!mk.classic) check(!f.includes('沪深300'), '非 CN 页面不出现「沪深300」（绝不拿 A 股指数算别市场超额）');
    if (mk.key === 'FUTURES') {
      check(requestsWith(/\/factor-research\/factor\//, since).some((c) => c.includes('market=FUTURES')),
        '详情请求带 market=FUTURES');
    }
  }
}

// ---- 应用内换市场（不整页刷新）：研究页 → 回测中心切港股 → 回研究页 ----
// ⚠️ 因子研究页**自身没有市场切换器**（顶栏 MarketSelector 只在 ModuleGrid/回测中心等
// 切面页挂载，实测研究页 button[role=radio] 数=0）。真实用户的换市场路径=离开页面→
// 在有切换器的地方切→再回来。本段走这条路径（SPA 内导航、不 reload），专门抓
// 「整页刷新才能对」的假绿：redux 市场变了但重挂载后仍读旧市场的串市场残留。
console.log('\n========== 应用内换市场（CN → HK，无整页刷新） ==========');
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await dismissRiskModal(p);
await switchMarket(p, 'A股', 'CN');
let inAppSince = apiCalls.length;
if (await openResearch(p) && await waitRows(p)) {
  let f = flat(await p.locator('body').innerText().catch(() => ''));
  check(f.includes('超额300'), 'CN：榜单出现存量超额列「超额300」');

  inAppSince = apiCalls.length;
  // 换市场必须去有切换器的切面页：回测中心顶栏自带 MarketSelector（App.tsx 路由实测）
  await p.locator('a,button', { hasText: '回测中心' }).first().click({ timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(2500);
  const sw = await switchMarket(p, '港股', 'HK');
  check(sw, '回测中心内切换市场生效（顶栏 radio → qm:current_market=HK）');
  const back = await openResearch(p);
  check(back, 'SPA 导航回「因子研究」页（未整页刷新）');
  const tagOk = await p.waitForFunction(
    () => document.querySelector('[data-testid=research-market-tag]')?.textContent?.trim() === '港股',
    null, { timeout: 15000 },
  ).then(() => true).catch(() => false);
  check(tagOk, '返回后市场标签即为「港股」');
  check(await waitRows(p), '港股榜单重新出数');
  check(requestsWith(/\/factor-research\/(leaderboard|catalog)/, inAppSince).some((c) => /[?&]market=HK(&|$)/.test(c)),
    '重进出数的请求带 market=HK');
  f = flat(await p.locator('body').innerText().catch(() => ''));
  check(f.includes('超额恒生指数'), '榜单超额列已是「超额恒生指数」（本市场基准）');
  check(!f.includes('超额300'), '旧市场列「超额300」已清（无 A 股口径残留）');
}

// ---- CRYPTO（若启用）：诚实阻断面，一个请求都不发 ----
// 同样先回到有切换器的切面页再判断区块链 radio 是否启用（研究页上它恒为 0 → 会永远假跳过）
console.log('\n========== CRYPTO（若启用） ==========');
await p.locator('a,button', { hasText: '回测中心' }).first().click({ timeout: 8000 }).catch(() => {});
await p.waitForTimeout(2500);
const cryptoRadio = p.locator('button[role=radio]', { hasText: '区块链' }).first();
if ((await cryptoRadio.count()) > 0) {
  await switchMarket(p, '区块链', 'CRYPTO');
  const before = apiCalls.length;
  const nav = p.locator('a,button', { hasText: '因子研究' }).first();
  await nav.click({ timeout: 8000 }).catch(() => {});
  const blocked = await p.locator('[data-testid=research-market-blocked]').first()
    .waitFor({ state: 'visible', timeout: 20000 }).then(() => true).catch(() => false);
  check(blocked, 'CRYPTO 未接入：渲染诚实阻断面');
  await p.waitForTimeout(3000);
  check(apiCalls.length === before, 'CRYPTO 阻断面：一个 factor-research 请求都不发（不拿别市场充数）');
} else {
  console.log('· 区块链市场未启用（marketFlags），跳过阻断面试探');
}

console.log('\n---- 接口调用（抽样） ----');
console.log([...new Set(apiCalls)].slice(0, 40).join('\n') || '(无)');
console.log('\n---- 错误 ----');
console.log(errors.length ? [...new Set(errors)].slice(0, 12).join('\n') : '(无)');
console.log(`\n断言通过 ${pass}/${total}`);

await b.close();
process.exit(0);
