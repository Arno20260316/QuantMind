/**
 * 模型管理 / 模型训练 四市场分层 E2E：A 股 / 港股 / 美股 / 期货。
 *
 * 用法：QM_BASE=http://localhost:3090 node tests/model-registry-training.e2e.mjs
 *      QM_MARKETS=US 只跑部分市场
 *
 * 走真实用户路径：登录 → 切全局市场 → 「模型管理」→「模型训练」，逐市场断言：
 *  模型管理：
 *    · 头部「模型资产库 (市场)」随全局市场变；
 *    · 列表请求带 market=KEY 且 200（跨市场隔离走网络证据，不看文案）；
 *    · CN/HK/US 有模型 → 详情自动选中 →「推理管理」页签 → 口径说明文案
 *      「（日历 日历）」且 /market-calendar/batch-check 请求体 market=日历
 *      （SSE/HKEX/NYSE —— 覆盖日历不得再硬编码 SSE）；
 *    · 期货 0 模型 → 诚实空态「暂无模型」+「请选择模型」，无推理管理页签。
 *  模型训练（目录加载是异步的，判据一律先等该市场 feature-catalog 200 落地——
 *  非 CN 市场挂载瞬间带初始化预设，「特征数非 0」会误报 ready）：
 *    · 顶部「训练市场」选择器显示当前市场；市场规模卡「市场」=市场；
 *    · training-sources / feature-catalog 请求带 market=KEY 且 200；
 *    · CN/HK/US 目录已发布：数据覆盖非 —、特征数>0（目录默认勾选落地，
 *      修复前 StrictMode 开发态会停在初始化预设甚至 0——CN=42/HK=19/US=19 是回归信号）；
 *    · 期货诚实阻断：「数据源」行的目录 Tag 显「尚未发布因子目录」+ 特征数 0；
 *      数据覆盖有值不算失败——后端如实给出该市场数据覆盖（数据在、目录未发布），
 *      页面照实展示，阻断由 Tag + 特征数 0 承担；点 Tag 跳后台「训练数据集」。
 *    · 目录发布态判据必须限定在「数据源」行内的 Tag：附加因子库行的阻断 Tag
 *      会用同一句文案（HK 的 ccass/south 未发布时实测），整页文本包含检查会被污染。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3090';

const MARKETS = [
  { key: 'CN', radio: 'A股', regLabel: '模型资产库 (A股)', trainLabel: 'A股', calendar: 'SSE', expectModels: true },
  { key: 'HK', radio: '港股', regLabel: '模型资产库 (港股)', trainLabel: '港股', calendar: 'HKEX', expectModels: true },
  { key: 'US', radio: '美股', regLabel: '模型资产库 (美股)', trainLabel: '美股', calendar: 'NYSE', expectModels: true },
  { key: 'FUTURES', radio: '期货', regLabel: '模型资产库 (期货)', trainLabel: '期货', expectModels: false },
];

const only = (process.env.QM_MARKETS || '').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
const TO_RUN = only.length ? MARKETS.filter((m) => only.includes(m.key)) : MARKETS;

const apiCalls = [];
const calCalls = [];
const errors = [];
const flat = (s) => s.replace(/\s+/g, ' ');

const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const p = await (await b.newContext({ viewport: { width: 1720, height: 1150 } })).newPage();
p.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 200)); });
p.on('pageerror', (e) => errors.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
p.on('response', (r) => {
  const u = r.url();
  if (!/\/api\/v1\/(models|market-calendar)/.test(u)) return;
  const pathWithQuery = u.replace(BASE, '');
  apiCalls.push(r.status() + ' ' + pathWithQuery);
  if (r.status() >= 500) errors.push('[API ' + r.status() + '] ' + u.slice(0, 160));
});
// 覆盖日历判日的真实入参：硬编码 SSE 的回归会在请求体里现形
p.on('request', (req) => {
  if (req.method() === 'POST' && req.url().includes('/market-calendar/batch-check')) {
    try {
      const body = JSON.parse(req.postData() || '{}');
      if (body.market) calCalls.push(String(body.market));
    } catch { /* 无法解析的请求体忽略 */ }
  }
});

// ---- 登录 ----
// 表单水合前 fill/click 会静默丢输入（实测偶发：填了但点击没派发，7s 后仍在 login 页）。
// 等表单可见 → 填 → 点 → 轮询离开 login 页，整段最多重试 3 轮。
async function ensureLoggedIn(page) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!page.url().includes('/auth/login') && (await page.locator('input[type=password]').count()) === 0) return true;
    await page.locator('input[type=password]').first().waitFor({ state: 'visible', timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1500);
    await page.locator('input').nth(0).fill('admin').catch(() => {});
    await page.locator('input[type=password]').first().fill('admin123').catch(() => {});
    const btns = page.locator('button');
    for (let i = 0; i < (await btns.count()); i++) {
      const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
      if (/登录/.test(t)) { await btns.nth(i).click({ timeout: 8000 }).catch(() => {}); break; }
    }
    for (let i = 0; i < 25; i++) {
      await page.waitForTimeout(1000);
      if (!page.url().includes('/auth/login')) return true;
    }
  }
  return false;
}
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await p.waitForTimeout(3500);
const loggedIn = await ensureLoggedIn(p);
console.log('登录后:', p.url(), loggedIn ? '' : '（登录未完成！）');

/** 首启「投资适当性评估」弹窗会遮住整页：走「稍后再答」 */
async function dismissRiskModal(page) {
  const later = page.locator('.ant-modal button', { hasText: '稍后再答' }).first();
  const shown = await later.waitFor({ state: 'visible', timeout: 4000 }).then(() => true).catch(() => false);
  if (shown) {
    await later.click().catch(() => {});
    await page.waitForTimeout(800);
  }
}
await dismissRiskModal(p);

let pass = 0, total = 0;
const check = (ok, label) => { total++; if (ok) pass++; console.log(`${ok ? '✓' : '✗'} ${label}`); };

/** 轮询直到条件成立（探针硬规则：确定性等待，不用碰运气的固定 sleep） */
async function waitFor(page, fn, timeoutMs = 30000, stepMs = 700) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await fn()) return true;
    await page.waitForTimeout(stepMs);
  }
  return false;
}

/** 切市场：先等应用水合到可交互（导航栏出现），再点轮询确认生效 */
async function switchMarket(page, radioLabel, wantKey) {
  for (let attempt = 0; attempt < 4; attempt++) {
    await page.locator('a,button', { hasText: '模型管理' }).first()
      .waitFor({ state: 'visible', timeout: 25000 }).catch(() => {});
    await page.waitForTimeout(2000);
    const radio = page.locator('button[role=radio]', { hasText: radioLabel }).first();
    if (await radio.count()) {
      await radio.click({ timeout: 10000 }).catch((e) => console.log('  · 点击市场按钮失败:', String(e).slice(0, 70)));
    }
    for (let i = 0; i < 8; i++) {
      await page.waitForTimeout(700);
      if ((await page.evaluate(() => localStorage.getItem('qm:current_market'))) === wantKey) return true;
    }
  }
  return false;
}

/** 读训练页顶部市场规模卡的数值（label div 的下一个兄弟 div） */
async function metricValue(page, label) {
  const el = page.locator(`div:text-is("${label}")`).first().locator('xpath=following-sibling::div[1]');
  return (await el.innerText().catch(() => '')).trim();
}

for (const mk of TO_RUN) {
  console.log(`\n========== ${mk.key} 模型管理 / 模型训练 ==========`);
  await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await dismissRiskModal(p);
  const switched = await switchMarket(p, mk.radio, mk.key);
  const market = await p.evaluate(() => localStorage.getItem('qm:current_market'));
  check(switched, `市场已切到 ${mk.key}（localStorage=${market}）`);

  // ── 模型管理 ──
  const regNav = p.locator('a,button', { hasText: '模型管理' }).first();
  await regNav.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {});
  await p.waitForTimeout(800);
  const regNavOk = await regNav.click({ timeout: 8000 }).then(() => true).catch(() => false);
  if (!regNavOk) { console.log('✗ 未能点开模型管理导航'); continue; }
  const regShown = await waitFor(p, async () => flat(await p.locator('body').innerText().catch(() => '')).includes('模型资产库 ('));
  check(regShown, '模型管理页已挂载');
  let f = flat(await p.locator('body').innerText().catch(() => ''));
  check(f.includes(mk.regLabel), `头部市场标识「${mk.regLabel}」`);
  check(
    await waitFor(p, async () => apiCalls.some((c) => c.startsWith('200') && c.includes('/models?') && c.includes(`market=${mk.key}`))),
    `模型列表请求带 market=${mk.key} 且 200`,
  );

  if (mk.expectModels) {
    const detailReady = await waitFor(p, async () =>
      (await p.locator('.ant-tabs-tab', { hasText: '推理管理' }).count()) > 0, 40000);
    check(detailReady, '已有模型 → 详情自动选中（推理管理页签在屏）');
    f = flat(await p.locator('body').innerText().catch(() => ''));
    check(!f.includes('暂无模型') && !f.includes('请选择模型'), '非空市场不落空态（无「暂无模型」/「请选择模型」）');

    if (detailReady) {
      const tab = p.locator('.ant-tabs-tab', { hasText: '推理管理' }).first();
      await tab.click({ timeout: 8000 }).catch(() => {});
      const panelReady = await waitFor(p, async () => flat(await p.locator('body').innerText().catch(() => '')).includes('口径说明'));
      check(panelReady, '推理管理 → 覆盖面板渲染（口径说明在屏）');
      const calOk = await waitFor(p, async () => calCalls.includes(mk.calendar), 25000);
      check(calOk, `覆盖日历判日走 ${mk.calendar}（batch-check 请求体，实测=${[...new Set(calCalls)].join(',') || '无'}）`);
      const explain = p.locator('text=口径说明').first();
      await explain.click({ timeout: 6000 }).catch(() => {});
      const copyOk = await waitFor(p, async () => flat(await p.locator('body').innerText().catch(() => '')).includes(`（${mk.calendar} 日历）`));
      check(copyOk, `口径说明文案随市场「（${mk.calendar} 日历）」`);
    }
  } else {
    const emptyReady = await waitFor(p, async () => flat(await p.locator('body').innerText().catch(() => '')).includes('暂无模型'), 40000);
    check(emptyReady, '0 模型市场：左栏诚实空态「暂无模型」');
    f = flat(await p.locator('body').innerText().catch(() => ''));
    check(f.includes('请选择模型'), '右栏「请选择模型」提示（不装门面）');
    check((await p.locator('.ant-tabs-tab', { hasText: '推理管理' }).count()) === 0, '0 模型市场无推理管理页签');
  }

  // ── 模型训练 ──
  const trainNav = p.locator('a,button', { hasText: '模型训练' }).first();
  await trainNav.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {});
  await p.waitForTimeout(800);
  const trainNavOk = await trainNav.click({ timeout: 8000 }).then(() => true).catch(() => false);
  if (!trainNavOk) { console.log('✗ 未能点开模型训练导航'); continue; }
  const trainShown = await waitFor(p, async () => flat(await p.locator('body').innerText().catch(() => '')).includes('模型训练中心'));
  check(trainShown, '模型训练页已挂载');

  const marketSel = p.locator('div:has(> span:text-is("训练市场")) .ant-select').first();
  await marketSel.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
  const selText = flat(await marketSel.innerText().catch(() => ''));
  check(selText.includes(mk.trainLabel), `训练市场选择器显示「${mk.trainLabel}」（实测="${selText}"）`);

  // 目录加载是异步链（training-sources → feature-catalog，实测导航后 ~2s 落定），
  // 且非 CN 市场挂载瞬间的特征数来自内置预设（US=20，非 0）——拿「特征数非 0」
  // 当就绪判据会把「目录还没加载」误报成 ready。判据一律走网络证据。
  const sourceOk = await waitFor(p, async () =>
    apiCalls.some((c) => c.startsWith('200') && c.includes('/models/training-sources') && c.includes(`market=${mk.key}`)), 60000);
  check(sourceOk, `训练源请求带 market=${mk.key} 且 200`);
  const catalogOk = await waitFor(p, async () =>
    apiCalls.some((c) => c.startsWith('200') && c.includes('/models/feature-catalog') && c.includes(`market=${mk.key}`)), 60000);
  check(catalogOk, `特征目录请求带 market=${mk.key} 且 200`);

  // 目录落地后等页面状态落定：四个市场后端都会给数据覆盖（期货：数据在、目录未发布）。
  // 数据覆盖与目录版本在 catalog 响应落地同一批渲染，出值即可安全读全部指标。
  await waitFor(p, async () => (await metricValue(p, '数据覆盖')) !== '—', 25000);

  const catalogTag = p.locator('div:has(> span:text-is("数据源")) .ant-tag').first();
  await catalogTag.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
  const tagText = flat(await catalogTag.innerText().catch(() => ''));
  check(tagText.includes('尚未发布因子目录') === !mk.expectModels, `目录发布态与市场一致（实测 Tag="${tagText}"）`);
  const mMarket = await metricValue(p, '市场');
  const mFeat = await metricValue(p, '特征数');
  const mCoverage = await metricValue(p, '数据覆盖');
  check(mMarket === mk.trainLabel, `市场规模卡「${mk.trainLabel}」（实测="${mMarket}"）`);
  if (mk.expectModels) {
    check(Number(mFeat) > 0, `特征数 > 0（实测=${mFeat}）`);
    check(mCoverage !== '—', `数据覆盖有值（实测="${mCoverage}"）`);
  } else {
    check(mFeat === '0', `期货不凭空勾特征：特征数=0（实测=${mFeat}）`);
  }
  check(!apiCalls.some((c) => /^5/.test(c) && c.includes(`market=${mk.key}`)), `${mk.key} 全程无 5xx（该市场请求）`);

  if (!mk.expectModels) {
    const tag = catalogTag;
    const tagOk = await tag.click({ timeout: 6000 }).then(() => true).catch(() => false);
    const escOk = await waitFor(p, async () => p.url().includes('/admin/training-datasets'), 10000);
    check(tagOk && escOk, '诚实阻断可操作：Tag 跳后台「训练服务 → 训练数据集」');
  }
}

console.log('\n---- 接口调用 ----');
// 逐日 prev-trading-day 会淹没模型面请求（实测 50 条截断里挤掉 training-sources），展示时剔除
console.log([...new Set(apiCalls)].filter((c) => !c.includes('/market-calendar/prev-trading-day')).slice(0, 60).join('\n') || '(无)');
console.log('\n---- 覆盖日历判日请求体 ----');
console.log([...new Set(calCalls)].join(', ') || '(无)');
console.log('\n---- 错误 ----');
console.log(errors.length ? [...new Set(errors)].slice(0, 12).join('\n') : '(无)');
console.log(`\n断言通过 ${pass}/${total}`);

await b.close();
process.exit(0);
