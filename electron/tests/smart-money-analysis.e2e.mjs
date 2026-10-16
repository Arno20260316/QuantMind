/** A 股市场分析 · 聪明钱 v2（机构版）E2E —— 实机验证
 *
 * 覆盖：
 * 1) 「聪明钱」Tab 九区内容断言（概览带六卡 / 生态趋势 / 战绩追踪 / 事件研究 CAR /
 *    选股榜 / 席位榜 / 行业净买 / 营业部榜 / 验证证据台（双过闸审计））
 * 2) 交互：战绩 h10 切换、选股榜分页（下一页/上一页）、个股档案抽屉 → 个股终端浮窗、
 *    席位榜三筛（窗口/排序/类型）、营业部榜 20 日窗口、面板头历史快照日期切换
 * 3) 图表面：canvas 数量（ECharts 实渲染）
 * 4) 接口面：/smart-money/* 八端点全部 200（overview/stocks/dates/evidence/
 *    track-record/event-study/seats/branches）
 *
 * 依赖：本地部署（deploy_frontend.sh → quantmind-web:3080）+ 后端产物已构建。
 * 断言取向：内容关键字命中（轮询等待，不用固定 sleep 兜底硬断言）；
 * 数据规模相关（分页是否 >1 页、历史快照是否可选）走软断言并打印实况。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const OUT = '/tmp';

const apiCalls = [];
const errors = [];
const hardFails = [];
const softFails = [];
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();
p.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 200)); });
p.on('pageerror', (e) => errors.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
p.on('response', (r) => {
  const u = r.url();
  if (/smart-money/.test(u)) {
    apiCalls.push(r.status() + ' ' + u.split('smart-money')[1].slice(0, 70));
    if (r.status() >= 400) errors.push('[API ' + r.status() + '] ' + u.slice(0, 140));
  } else if (r.status() >= 400 && !/favicon/.test(u)) {
    errors.push('[API ' + r.status() + '] ' + u.slice(0, 160));
  }
});

const flat = (s) => s.replace(/\s+/g, ' ');
const bodyText = async () => flat(await p.locator('body').innerText().catch(() => ''));

function hard(ok, label) {
  console.log(`${ok ? '✓' : '✗'} ${label}`);
  if (!ok) hardFails.push(label);
}
function soft(ok, label) {
  console.log(`${ok ? '✓' : '✗(软)'} ${label}`);
  if (!ok) softFails.push(label);
}

async function dismissModal() {
  const wrap = p.locator('.ant-modal-wrap').first();
  if (await wrap.isVisible().catch(() => false)) {
    const close = p.locator('.ant-modal-close').first();
    if (await close.count()) {
      await close.click({ timeout: 3000 }).catch(() => undefined);
    } else {
      await p.keyboard.press('Escape').catch(() => undefined);
    }
    await p.waitForTimeout(800);
  }
}

async function waitForText(pred, timeoutMs = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (pred(await bodyText())) return true;
    await p.waitForTimeout(500);
  }
  return false;
}

async function waitApi(pat, timeoutMs = 10000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (apiCalls.some((c) => c.startsWith('200') && c.includes(pat))) return true;
    await p.waitForTimeout(400);
  }
  return false;
}

async function terminalOpen() {
  return await p.locator('span', { hasText: '个股终端' }).count() > 0;
}
async function closeTerminal() {
  const btn = p.locator('button[title="关闭"]').last();
  if (await btn.count()) {
    await btn.click({ timeout: 3000 }).catch(() => undefined);
    await p.waitForTimeout(600);
  }
}

/** 主内容区滚动（长面板；分页按钮/席位榜在折叠下方） */
async function scrollMain(fraction) {
  await p.evaluate((f) => {
    const els = [...document.querySelectorAll('.custom-scrollbar, .overflow-y-auto')]
      .sort((a, el2) => el2.scrollHeight - a.scrollHeight);
    const el = els[0];
    if (el && el.scrollHeight > el.clientHeight) el.scrollTop = (el.scrollHeight - el.clientHeight) * f;
  }, fraction).catch(() => undefined);
  await p.waitForTimeout(700);
}

// ---- 登录 ----
await p.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await p.waitForTimeout(3500);
if ((await p.locator('input[type=password]').count()) > 0) {
  await p.locator('input').nth(0).fill('admin');
  await p.locator('input[type=password]').first().fill('admin123');
  const btns = p.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录|登\s*录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await p.waitForTimeout(7000);
}
console.log('登录后 URL:', p.url());

// ---- 切换市场为 A 股 ----
await dismissModal();
const cnBtn = p.locator('button[role=radio]', { hasText: 'A股' }).first();
const cnClicked = await cnBtn.click({ timeout: 8000 }).then(() => true).catch(() => false);
if (!cnClicked) {
  await p.evaluate(() => localStorage.setItem('qm:current_market', 'CN'));
  await p.reload({ waitUntil: 'domcontentloaded' });
}
await p.waitForTimeout(2500);
console.log('切换后 localStorage 市场:', await p.evaluate(() => localStorage.getItem('qm:current_market')));

// ---- 深链进「市场分析」→ 聪明钱 ----
await p.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(9000);
await dismissModal();
await p.waitForTimeout(500);

let f = await bodyText();
console.log('\nURL:', p.url());
hard(f.includes('市场分析'), '页面标题：市场分析');
hard(f.includes('QuantDB 2.0 数据引擎'), '数据引擎标识：QuantDB 2.0');
hard(!f.includes('港股市场多维分析与资金穿透'), '未误渲染港股页');
const NAV = ['大盘全景看板', '资金流全景', '因子概率板', '聪明钱'];
hard(NAV.every((t) => f.includes(t)), `四 Tab 导航齐全：${NAV.join(' / ')}`);

const tabOk = await p
  .locator('button', { hasText: '聪明钱' })
  .first()
  .click({ timeout: 6000 })
  .then(() => true)
  .catch(() => false);
hard(tabOk, '聪明钱 Tab 点击');

// ---- 九区内容断言（数据就位判据：证据台 + 最近案例，两处皆在长面板尾部） ----
const ready = await waitForText(
  (t) => t.includes('验证证据台（双过闸审计）') && t.includes('最近案例（近 10 计分日'),
  35000,
);
hard(ready, '聪明钱面板数据渲染（证据台 + 战绩最近案例）');
await p.waitForTimeout(1500); // ECharts 渲染余量（canvas 无文本信号可等）

const KEYS = [
  '聪明钱 · 席位与资金行为统计评分',
  '研究评分卡（非模型预测、非交易指令）',
  '数据截至',
  '龙虎榜生态', '机构席位', '两融', '大宗交易', '机构调研', '北向持仓（季度）',
  '生态趋势',
  '战绩追踪 · 历史回溯统计（非预测）',
  '全史（h5）', '平均超额', '计分日',
  '最近案例（近 10 计分日 × Top-5，含进行中）',
  '事件研究 · CAR（历史回溯统计，非预测）',
  '聪明钱选股榜（池内复合分降序）',
  '席位榜',
  '行业净买（20 日）',
  '活跃席位（龙虎榜营业部）',
  '验证证据台（双过闸审计）',
  '复合分（入柱等权·方向对齐后）',
  'IC 衰减',
  '口径与新鲜度',
];
f = await bodyText();
const miss = KEYS.filter((k) => !f.includes(k));
hard(miss.length === 0, `九区关键字 ${KEYS.length - miss.length}/${KEYS.length}${miss.length ? ' 缺：' + miss.join('｜') : ''}`);
await p.screenshot({ path: `${OUT}/sm-panel-full.png`, fullPage: true });

// ---- 图表面：canvas 实渲染（滚动全页触发懒渲染后计数） ----
for (const fr of [0, 0.35, 0.7, 1]) await scrollMain(fr);
const canvasN = await p.locator('canvas').count();
hard(canvasN >= 4, `ECharts canvas 实渲染数 ${canvasN}（≥4；预期≈8：战绩3 + CAR4 + 衰减1）`);
await scrollMain(0);

// ---- 战绩区 h10 切换（区 4 的 h10 按钮是页内第一枚） ----
const h10btns = p.locator('button', { hasText: /^h10$/ });
const h10n = await h10btns.count();
if (h10n > 0) {
  await h10btns.first().click({ timeout: 3000 }).catch(() => undefined);
  await p.waitForTimeout(1200);
  soft(true, `战绩 h10 切换点击（页内 h10 按钮 ${h10n} 枚，点第一枚=战绩区）`);
} else {
  soft(false, '战绩 h10 按钮未找到');
}

// ---- 席位榜三筛：类型=机构专用 / 窗口 20 日 / 排序 净买 ----
console.log('\n---- 席位榜三筛 ----');
const scrollSeat = await waitForText((t) => t.includes('席位榜'), 8000);
soft(scrollSeat, '席位榜区存在（筛选前）');
const instChip = p.locator('button', { hasText: '机构专用' }).first();
if (await instChip.count()) {
  await instChip.click({ timeout: 3000 }).catch(() => undefined);
  const ok = await waitApi('seats?window=all&sort=win_rate&class=inst', 10000);
  hard(ok, '席位筛选：类型=机构专用 → /seats class=inst 200');
} else {
  hard(false, '席位类型 chip「机构专用」未找到');
}
const w20 = p.locator('button', { hasText: /^20 日$/ });
const w20n = await w20.count();
if (w20n >= 1) {
  await w20.nth(0).click({ timeout: 3000 }).catch(() => undefined); // [0]=席位榜（营业部榜在区 7）
  const ok = await waitApi('seats?window=20', 10000);
  hard(ok, '席位筛选：窗口 20 日 → /seats window=20 200');
} else {
  hard(false, '席位窗口 chip「20 日」未找到');
}
const byNet = p.locator('button', { hasText: '按净买' }).first();
if (await byNet.count()) {
  await byNet.click({ timeout: 3000 }).catch(() => undefined);
  soft(await waitApi('seats?window=20&sort=net', 10000), '席位筛选：排序 按净买 → sort=net 200');
} else {
  soft(false, '席位排序 chip「按净买」未找到');
}
// 复位（供后续截图/营业部榜判据干净）
const resetAll = p.locator('button', { hasText: '全部类型' }).first();
if (await resetAll.count()) await resetAll.click({ timeout: 3000 }).catch(() => undefined);
await p.waitForTimeout(800);
f = await bodyText();
soft(f.includes('对照池内基线'), '席位榜基线对照文案在（h5 超额>0 占比 · 对照池内基线）');

// ---- 营业部榜 20 日窗口（区 7 的第二枚「20 日」） ----
if (w20n >= 2) {
  await w20.nth(w20n - 1).click({ timeout: 3000 }).catch(() => undefined);
  const ok = await waitApi('branches?window=20', 10000);
  hard(ok, '营业部榜：窗口 20 日 → /branches window=20 200');
} else {
  soft(false, `营业部榜「20 日」按钮共 ${w20n} 枚（预期 ≥2：席位榜 + 营业部榜）`);
}

// ---- 选股榜分页 ----
console.log('\n---- 选股榜分页 ----');
const hasPager = await waitForText((t) => /第 \d+\/\d+ 页 · 每页 50/.test(t), 8000);
hard(hasPager, '分页器渲染（第 x/n 页 · 每页 50）');
const nextBtn = p.locator('button', { hasText: '下一页' }).first();
if ((await nextBtn.count()) && (await nextBtn.isEnabled().catch(() => false))) {
  await nextBtn.click({ timeout: 3000 }).catch(() => undefined);
  const p2 = await waitForText((t) => t.includes('第 2/'), 10000);
  if (p2) {
    hard(await waitApi('stocks?limit=50&offset=50', 10000), '下一页 → /stocks offset=50 200 且文案到第 2 页');
    const prevBtn = p.locator('button', { hasText: '上一页' }).first();
    await prevBtn.click({ timeout: 3000 }).catch(() => undefined);
    soft(await waitForText((t) => t.includes('第 1/'), 8000), '上一页 → 回到第 1 页');
  } else {
    hard(false, '下一页点击后文案未到第 2 页');
  }
} else {
  soft(true, `下一页不可用（池内 ≤50 只，数据实况）`);
}
await p.screenshot({ path: `${OUT}/sm-stockboard.png` });

// ---- 个股档案抽屉 → 个股终端 ----
console.log('\n---- 个股档案抽屉 ----');
await scrollMain(0.5);
const row = p.locator('div[title^="打开 "]').first();
if (await row.count()) {
  const rowTitle = await row.getAttribute('title').catch(() => '');
  await row.click({ timeout: 3000 }).catch(() => undefined);
  const drawerOk = await waitForText((t) => t.includes('事件时间线') && t.includes('最近龙虎榜席位构成'), 12000);
  hard(drawerOk, `选股榜行「${rowTitle || '?'}」→ 抽屉打开（事件时间线 + 席位构成）`);
  soft(await waitForText((t) => t.includes('大宗交易明细') || t.includes('北向持股占比'), 6000), '抽屉其余区（大宗明细/北向序列）');
  await p.screenshot({ path: `${OUT}/sm-drawer.png` });
  const toTerm = p.locator('button, span', { hasText: '打开个股终端' }).first();
  if (await toTerm.count()) {
    await toTerm.click({ timeout: 3000 }).catch(() => undefined);
    let opened = false;
    const t0 = Date.now();
    while (Date.now() - t0 < 5000) {
      if (await terminalOpen()) { opened = true; break; }
      await p.waitForTimeout(300);
    }
    hard(opened, '抽屉「打开个股终端 ➔」→ 个股终端浮窗弹出');
    if (opened) {
      await p.screenshot({ path: `${OUT}/sm-terminal.png` });
      const termReady = await waitForText((t) => t.includes('前复权') && !t.includes('在上方搜索框输入代码'), 15000);
      soft(termReady, '终端主体渲染 K 线区（非空态）');
    }
    await closeTerminal();
  } else {
    hard(false, '抽屉「打开个股终端 ➔」按钮未找到');
  }
  const dClose = p.locator('.ant-drawer-close').first();
  if (await dClose.count()) {
    await dClose.click({ timeout: 3000 }).catch(() => undefined);
    await p.waitForTimeout(800);
    soft(!(await bodyText()).includes('事件时间线'), '抽屉关闭');
  }
} else {
  hard(false, '选股榜可点击行未找到');
}

// ---- 历史快照日期切换（面板头 select；选项=最新快照 + 历史日期） ----
console.log('\n---- 历史快照日期切换 ----');
await scrollMain(0);
const dateSel = p.locator('select:has(option:text-is("最新快照"))').first();
if (await dateSel.count()) {
  const nOpt = await dateSel.locator('option').count();
  console.log(`· 日期选择器选项数：${nOpt}（1=仅最新快照）`);
  if (nOpt >= 2) {
    const optVal = await dateSel.locator('option').nth(1).getAttribute('value');
    await dateSel.selectOption({ index: 1 }).catch(() => undefined);
    const ok = await waitApi(`stocks?limit=50&date=${optVal}`, 12000);
    hard(ok, `切换历史快照 ${optVal} → /stocks date= 200`);
    f = await bodyText();
    hard(f.includes('数据截至') && f.includes('聪明钱 · 席位与资金行为统计评分'), '历史快照下头部（数据截至）与面板完好');
    if (f.includes('（请求 ')) console.log('· 头部含回退提示（请求日期非快照日，回退最近）');
    await p.screenshot({ path: `${OUT}/sm-date-history.png` });
    await dateSel.selectOption({ index: 0 }).catch(() => undefined);
    await p.waitForTimeout(2000);
    soft((await bodyText()).includes('数据截至'), '切回最新快照');
  } else {
    soft(false, '历史快照不可选（/dates 仅 1 项，数据实况）');
  }
} else {
  hard(false, '面板头日期选择器（option=最新快照）未找到');
}

// ---- 接口面 ----
console.log('\n---- /smart-money 接口 ----');
const uniq = [...new Set(apiCalls)];
console.log(uniq.slice(0, 40).join('\n'));
for (const ep of ['/overview', '/stocks', '/dates', '/evidence', '/track-record', '/event-study', '/seats', '/branches']) {
  hard(uniq.some((c) => c.startsWith('200') && c.includes(ep)), `接口 200：${ep}`);
}
hard(!uniq.some((c) => c.startsWith('404')), '无 404 接口（产物已构建）');

console.log('\n---- 错误 ----');
console.log(errors.length ? [...new Set(errors)].slice(0, 10).join('\n') : '(无)');

console.log(`\n==== 硬断言失败 ${hardFails.length} / 软断言失败 ${softFails.length} ====`);
if (hardFails.length) console.log('硬失败：\n- ' + hardFails.join('\n- '));
if (softFails.length) console.log('软失败：\n- ' + softFails.join('\n- '));
await b.close();
process.exitCode = hardFails.length ? 1 : 0;
