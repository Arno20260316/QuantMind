/**
 * 港股聪明钱 v2.1（统一大框五标签面）实机探针 —— 硬 0 软 0。
 *
 * 覆盖全链：
 *  1) 登录 → 切港股 → 深链 #/market-analysis → 第 8 Tab「聪明钱」
 *  2) 五标签面就位（机构优势默认选中）；概览带常显（evidence-only 徽标/类型净流概览）
 *  3) 懒取数纪律：未切面时不请求 types/consensus/persistence/evidence
 *  4) 面 1 机构优势：类别优势图 + 席位优势榜（共 N 席位）+ 技能散点（canvas ≥2）
 *  5) 面 2 类型效力：公示条 + IC 热力（canvas ≥2）+ 信号行展开 ICIR
 *  6) 面 3 生态趋势：净流生态/参与广度/持仓存量格局（canvas ≥3）
 *  7) 面 4 共识榜：evidence-only 不发布空态 或 列表态
 *  8) 面 5 稳健性与口径：持续性审计/四分位 OOS/口径脚注全集/证据台（canvas ≥3）
 *  9) 席位档案抽屉：榜单行 → 月度战绩 + 增/减持命中率对比 → 活跃标的下钻个股抽屉
 * 10) 个股档案输入框 0700.HK → 时间线 + 权益结构 → 打开个股终端浮窗
 * 11) 八接口（overview/types/seats/seat/persistence/consensus/evidence/stock）全 200
 * 12) 无 console error / pageerror（favicon 与 ResizeObserver 噪音除外）
 *
 * 依赖：本地部署（deploy_frontend.sh → quantmind-web:3080）+ 后端
 * /api/v1/market-analysis-hk/smart-money/* 可用（v2 已交付）。
 * 用法：node electron/tests/probe_hk_smart_money.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const OUT = '/tmp';

const results = [];
const check = (label, pass, detail = '') => {
  results.push({ label, pass });
  console.log(`${pass ? '✓' : '✗'} ${label}${detail ? ` — ${detail}` : ''}`);
};

const apiHits = [];
const errs = [];
const b = await chromium.launch({
  executablePath: '/usr/bin/google-chrome-stable',
  args: ['--no-sandbox'],
});
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();
p.on('console', (m) => {
  if (m.type() === 'error') errs.push('[console] ' + m.text().slice(0, 200));
});
p.on('pageerror', (e) => errs.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
p.on('response', (r) => {
  const u = r.url();
  if (/market-analysis-hk\/smart-money/.test(u)) {
    apiHits.push({ status: r.status(), path: u.split('/smart-money')[1].split('?')[0] });
  }
});

const flat = (s) => s.replace(/\s+/g, ' ');
const bodyText = async () => flat(await p.locator('body').innerText().catch(() => ''));

async function waitForText(pred, timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (pred(await bodyText())) return true;
    await p.waitForTimeout(500);
  }
  return false;
}

async function dismissModal() {
  const wrap = p.locator('.ant-modal-wrap').first();
  if (await wrap.isVisible().catch(() => false)) {
    const close = p.locator('.ant-modal-close').first();
    if (await close.count()) await close.click({ timeout: 3000 }).catch(() => undefined);
    else await p.keyboard.press('Escape').catch(() => undefined);
    await p.waitForTimeout(800);
  }
}

async function terminalOpen() {
  return (await p.locator('span', { hasText: '个股终端' }).count()) > 0;
}

/** 点击聪明钱面板内的标签面（role=tab 按钮） */
async function openFace(name) {
  const btn = p.locator('button[role=tab]', { hasText: name }).first();
  const ok = await btn.click({ timeout: 6000 }).then(() => true).catch(() => false);
  await p.waitForTimeout(900);
  return ok;
}

const canvasCount = () => p.locator('canvas').count();

/** 等 canvas 数达到 n（ECharts 在数据 commit 后一帧内初始化，一次性计数会撞竞态）。 */
async function waitForCanvasAtLeast(n, timeoutMs = 8000) {
  const t0 = Date.now();
  let c = 0;
  while (Date.now() - t0 < timeoutMs) {
    c = await canvasCount();
    if (c >= n) return c;
    await p.waitForTimeout(300);
  }
  return c;
}

// ── 1) 登录 + 切港股 + 深链 ────────────────────────────────────────────
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await p.waitForTimeout(3500);
if ((await p.locator('input[type=password]').count()) > 0) {
  await p.locator('input').nth(0).fill('admin');
  await p.locator('input[type=password]').first().fill('admin123');
  const btns = p.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录|登\s*录/.test(t)) {
      await btns.nth(i).click();
      break;
    }
  }
  await p.waitForTimeout(7000);
}
check('登录成功（URL 不含 login）', !/login/i.test(p.url()), p.url());

await dismissModal();
const hkBtn = p.locator('button[role=radio]', { hasText: '港股' }).first();
const hkClicked = await hkBtn.click({ timeout: 8000 }).then(() => true).catch(() => false);
if (!hkClicked) {
  await p.evaluate(() => localStorage.setItem('qm:current_market', 'HK'));
  await p.reload({ waitUntil: 'domcontentloaded' });
}
await p.waitForTimeout(2500);

await p.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(9000);
await dismissModal();

let f = await bodyText();
check('港股市场分析页就位', f.includes('港股市场多维分析与资金穿透'), p.url());

// ── 2) 第 8 Tab「聪明钱」+ 五标签面就位 ────────────────────────────────
const tab = p.locator('button', { hasText: '聪明钱' }).first();
const tabOk = await tab.click({ timeout: 6000 }).then(() => true).catch(() => false);
check('「聪明钱」Tab 可点击', tabOk);

const mainOk = await waitForText((t) => t.includes('聪明钱 · 席位行为统计与类型效力'), 15000);
f = await bodyText();
check('栏目标题呈现', mainOk);
check('免责口径（研究统计，非模型预测）', f.includes('研究统计（非模型预测、非交易指令）'));
check('概览带常显（类型净流概览）', f.includes('类型净流概览'));

const faceTabs = p.locator('button[role=tab]', { hasText: /机构优势|类型效力|生态趋势|共识榜|稳健性与口径/ });
const faceTabCount = await faceTabs.count();
check('五标签面就位', faceTabCount === 5, `tabs=${faceTabCount}`);
const defaultSel = await p
  .locator('button[role=tab][aria-selected=true]')
  .first()
  .innerText()
  .catch(() => '');
check('默认面 = 机构优势', defaultSel.includes('机构优势'), defaultSel.trim());

// 懒取数纪律：切面前不请求非默认面接口
const lazyOk =
  !apiHits.some((h) => h.path.startsWith('/types')) &&
  !apiHits.some((h) => h.path.startsWith('/consensus')) &&
  !apiHits.some((h) => h.path.startsWith('/persistence')) &&
  !apiHits.some((h) => h.path.startsWith('/evidence'));
check('懒取数纪律（未切面 = 零请求）', lazyOk, apiHits.map((h) => h.path).join(','));

// ── 3) 面 1 机构优势（默认面） ─────────────────────────────────────────
check('面 1：类别优势图卡', f.includes('类别优势'));
check('面 1：席位优势榜', f.includes('席位优势榜'));
check('面 1：怎么读这个面（90% 下界口径）', f.includes('90% 下界') && f.includes('描述性盘点'));
check('面 1：散点卡（样本量 vs 优势）', f.includes('技能散点 · 样本量 vs 优势'));
const seatRowsOk = await waitForText((t) => /共 [\d,]+ 席位达/.test(t), 12000);
check('席位榜取数结算（共 N 席位脚注）', seatRowsOk);
const c1 = await waitForCanvasAtLeast(2);
check('面 1 双图渲染（canvas ≥2）', c1 >= 2, `canvas=${c1}`);
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_face1.png`, fullPage: false });

// ── 4) 面 2 类型效力 ─────────────────────────────────────────────────
check('切「类型效力」', await openFace('类型效力'));
const typesOk = await waitForText((t) => t.includes('本期裁定') || t.includes('本期入柱'), 15000);
check('面 2：公示条（过闸裁定）', typesOk);
f = await bodyText();
check('面 2：IC 热力卡', f.includes('前瞻 IC 热力'));
const sigRow = p.locator('[title*="::"]').first();
const sigClicked = await sigRow.click({ timeout: 5000 }).then(() => true).catch(() => false);
check('面 2：信号行可点击（展开裁定明细）', sigClicked);
const expandOk = await waitForText((t) => t.includes('ICIR'), 8000);
check('面 2：行展开出裁定明细（ICIR/尾部同号）', expandOk);
const c2 = await waitForCanvasAtLeast(2);
check('面 2 双图渲染（canvas ≥2）', c2 >= 2, `canvas=${c2}`);
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_face2.png`, fullPage: false });

// ── 5) 面 3 生态趋势 ─────────────────────────────────────────────────
check('切「生态趋势」', await openFace('生态趋势'));
const ecoOk = await waitForText((t) => t.includes('类型净流生态'), 12000);
f = await bodyText();
check('面 3：净流生态卡', ecoOk);
check('面 3：参与广度卡', f.includes('参与广度'));
check('面 3：持仓存量格局卡', f.includes('持仓存量格局'));
const c3 = await waitForCanvasAtLeast(3);
check('面 3 三图渲染（canvas ≥3）', c3 >= 3, `canvas=${c3}`);
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_face3.png`, fullPage: false });

// ── 6) 面 4 共识榜（evidence-only 双态） ─────────────────────────────
check('切「共识榜」', await openFace('共识榜'));
await p.waitForTimeout(1500);
f = await bodyText();
const evidenceOnly = f.includes('零入柱（evidence-only）') || f.includes('共识榜不发布');
if (evidenceOnly) {
  check('面 4：evidence-only 诚实空态（不发布=数据结论）', f.includes('共识榜不发布'));
} else {
  const consensusRows = await p.locator('.cursor-pointer', { hasText: '覆盖' }).count();
  check('面 4：列表态有行（覆盖 n/N 披露）', consensusRows > 0, `rows=${consensusRows}`);
}

// ── 7) 面 5 稳健性与口径 ─────────────────────────────────────────────
check('切「稳健性与口径」', await openFace('稳健性与口径'));
const persOk = await waitForText((t) => t.includes('持续性审计 · 前半强'), 15000);
f = await bodyText();
check('面 5：持续性审计卡', persOk);
check('面 5：四分位 OOS 检验卡', f.includes('四分位 OOS 检验'));
check('面 5：口径脚注全集', f.includes('口径脚注全集（各面用词唯一出处）'));
check('面 5：证据台并入', f.includes('证据台 · 裁定与标定'));
const c5 = await waitForCanvasAtLeast(3);
check('面 5 三图渲染（canvas ≥3）', c5 >= 3, `canvas=${c5}`);
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_face5.png`, fullPage: false });

// ── 8) 席位档案抽屉（回面 1 → 榜单行 → 下钻个股） ────────────────────
check('切回「机构优势」', await openFace('机构优势'));
await waitForText((t) => t.includes('席位优势榜'), 12000);
const seatRow = p.locator('[title*="席位档案"]').first();
const seatRowOk = await seatRow.click({ timeout: 6000 }).then(() => true).catch(() => false);
check('席位榜行可点击', seatRowOk);
const seatDrawerOk = await waitForText((t) => t.includes('月度战绩'), 12000);
f = await bodyText();
check('席位抽屉：月度战绩图卡', seatDrawerOk);
check('席位抽屉：增/减持命中率对比条', f.includes('增 / 减持命中率对比'));
check('席位抽屉：活跃标的表', f.includes('活跃标的（事件最多前'));
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_seat_drawer.png`, fullPage: false });

const symRow = p.locator('[title*="个股档案"]').first();
const symOk = await symRow.click({ timeout: 6000 }).then(() => true).catch(() => false);
const stockFromSeat = symOk && (await waitForText((t) => t.includes('事件时间线'), 12000));
check('席位抽屉活跃标的下钻 → 个股抽屉', stockFromSeat);
await p.screenshot({ path: `${OUT}/qm_hk_sm_v21_stock_drawer.png`, fullPage: false });
await p.keyboard.press('Escape');
await p.waitForTimeout(700);
await p.keyboard.press('Escape');
await p.waitForTimeout(700);

// ── 9) 个股档案输入框 + 打开个股终端 ─────────────────────────────────
const jump = p.getByPlaceholder(/个股档案/);
check('头部「个股档案」输入框存在', (await jump.count()) === 1);
await jump.fill('0700.HK');
await jump.press('Enter');
const drawerOk = await waitForText(
  (t) => t.includes('事件时间线') && (t.includes('腾讯控股') || t.includes('0700.HK')),
  12000,
);
check('输入框抽屉打开（0700.HK 时间线 + 权益结构）', drawerOk);
f = await bodyText();
check('抽屉含权益结构区', f.includes('权益结构'));

const openTerm = p.locator('button', { hasText: '打开个股终端' }).first();
await openTerm.click({ timeout: 5000 }).catch(() => undefined);
let termOk = false;
for (let i = 0; i < 20; i++) {
  if (await terminalOpen()) {
    termOk = true;
    break;
  }
  await p.waitForTimeout(400);
}
check('「打开个股终端」→ 浮窗出现', termOk);
const closeBtn = p.locator('button[title="关闭"]').last();
if (await closeBtn.count()) {
  await closeBtn.click({ timeout: 3000 }).catch(() => undefined);
  await p.waitForTimeout(800);
}
check('个股终端浮窗可关闭', !(await terminalOpen()));
await p.keyboard.press('Escape');
await p.waitForTimeout(600);

// ── 10) 八接口全 200 ─────────────────────────────────────────────────
const seen = new Map();
for (const h of apiHits) {
  const key = h.path.startsWith('/stock/')
    ? '/stock'
    : h.path.startsWith('/seat/')
      ? '/seat'
      : h.path;
  seen.set(key, h.status);
}
const need = ['/overview', '/types', '/seats', '/seat', '/persistence', '/consensus', '/evidence', '/stock'];
const missing = need.filter((k) => !seen.has(k));
const badStatus = need.filter((k) => seen.has(k) && seen.get(k) !== 200);
check(
  '八接口全被真实调用且 200',
  missing.length === 0 && badStatus.length === 0,
  `missing=${missing.join(',') || '无'} / 非200=${badStatus.map((k) => `${k}:${seen.get(k)}`).join(',') || '无'}`,
);

// ── 11) 错误面 ───────────────────────────────────────────────────────
const pageErrs = errs.filter((e) => !/ResizeObserver|favicon/.test(e));
check('无 console error / pageerror', pageErrs.length === 0, pageErrs.slice(0, 3).join(' ; '));

await b.close();
const failed = results.filter((r) => !r.pass);
console.log(`\n${failed.length === 0 ? 'PASS' : `FAIL（${failed.length} 项）`}：${results.length} 项检查`);
process.exit(failed.length === 0 ? 0 : 1);
