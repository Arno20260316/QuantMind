/** A 股市场分析「聪明钱」Tab E2E —— 实机验证（2026-10-16）
 *
 * 覆盖：
 * 1) 深链 #/market-analysis（A 股）→「聪明钱」Tab 切换
 * 2) 面板四面渲染：总览带（六数据源卡）/ 聪明钱选股榜 / 活跃席位 / 验证证据台 + 口径卡
 * 3) 选股榜行点击 → 个股终端浮窗（关闭后消失）
 * 4) 事件筛选 chip（龙虎榜）过滤与还原
 * 5) 席位榜窗口 5 日 → 20 日重取（branches?window=20）
 * 6) 证据台审计面：复合分验证条 + 入柱（4）+ 否决清单 + 仅展示证据
 * 7) 接口面：/smart-money/{overview,stocks,branches,evidence} 全部 200
 *
 * 依赖：本地部署（deploy_frontend.sh → quantmind-web:3080）+ 后端 /api/v1/market-analysis/smart-money/* 可用。
 * 断言取向：内容关键字命中（确定性等待 + 轮询，不用固定 sleep 兜底硬断言）。
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
    apiCalls.push(r.status() + ' ' + u.split('/smart-money')[1].slice(0, 60));
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

/** 轮询等待 body 文本满足条件（确定性等待，避免固定 sleep 断言） */
async function waitForText(pred, timeoutMs = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (pred(await bodyText())) return true;
    await p.waitForTimeout(500);
  }
  return false;
}

/** 轮询等待条件为真（如行数 > 0） */
async function waitFor(pred, timeoutMs = 12000, step = 400) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await p.waitForTimeout(step);
  }
  return false;
}

/** 「个股终端」浮窗是否打开（标题栏/悬浮条文本） */
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
console.log('找到「A股」切换按钮:', (await cnBtn.count()) > 0);
const cnClicked = await cnBtn.click({ timeout: 8000 }).then(() => true).catch(() => false);
if (!cnClicked) {
  await p.evaluate(() => localStorage.setItem('qm:current_market', 'CN'));
  await p.reload({ waitUntil: 'domcontentloaded' });
}
await p.waitForTimeout(2500);
console.log('切换后 localStorage 市场:', await p.evaluate(() => localStorage.getItem('qm:current_market')));

// ---- 深链进「市场分析」→「聪明钱」Tab ----
await p.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(9000);
await dismissModal();
await p.waitForTimeout(500);

let f = await bodyText();
hard(!f.includes('港股市场多维分析与资金穿透'), '未误渲染港股页');
hard(f.includes('大盘全景看板') && f.includes('资金流全景'), 'A 股市场分析页就绪（大盘全景看板/资金流全景 Tab 可见）');

console.log('\n---- 聪明钱 Tab 切换 ----');
const smTab = p.locator('button', { hasText: '聪明钱' }).first();
hard((await smTab.count()) > 0, '「聪明钱」Tab 按钮存在');
const smClicked = await smTab.click({ timeout: 6000 }).then(() => true).catch(() => false);
hard(smClicked, '「聪明钱」Tab 点击');

const PANEL_KEYS = [
  '聪明钱 · 席位与资金行为统计评分',
  '研究评分卡（非模型预测、非交易指令）',
  '数据截至',
  '龙虎榜生态',
  '机构席位',
  '两融',
  '大宗交易',
  '机构调研',
  '北向持仓（季度）',
  '聪明钱选股榜（池内复合分降序）',
  '活跃席位（龙虎榜营业部）',
  '验证证据台（双过闸审计）',
  '复合分（入柱等权·方向对齐后）h10 验证',
  '口径与新鲜度',
];
const panelOk = await waitForText((t) => PANEL_KEYS.every((k) => t.includes(k)), 25000);
f = await bodyText();
const missing = PANEL_KEYS.filter((k) => !f.includes(k));
hard(panelOk, `面板骨架齐（${PANEL_KEYS.length} 项关键字${missing.length ? `，缺: ${missing.join(' / ')}` : ''}）`);

// 选股榜数据就位（行 = title 以「个股终端」结尾的可点击行）
const rowsReady = await waitFor(async () => (await p.locator('[title$="个股终端"]').count()) > 0, 25000);
f = await bodyText();
if (f.includes('聪明钱统计尚未生成')) {
  hard(false, '产物未构建（面板显示 404 notBuilt 空态）——请先跑 build_smart_money.py');
} else {
  hard(rowsReady, '选股榜行数据渲染（可点击行出现）');
}
await p.screenshot({ path: `${OUT}/sm-panel.png` });

// ---- 事件筛选 chip ----
console.log('\n---- 事件筛选 chip ----');
const lhbChip = p.locator('button', { hasText: '龙虎榜' }).first();
if (await lhbChip.count()) {
  await lhbChip.click({ timeout: 3000 }).catch(() => undefined);
  await p.waitForTimeout(1500);
  f = await bodyText();
  const rowsAfter = await p.locator('[title$="个股终端"]').count();
  const emptyHint = f.includes('最新事件为「龙虎榜」的标的暂不在榜');
  soft(rowsAfter > 0 || emptyHint, `「龙虎榜」筛选生效（行 ${rowsAfter} 或空态提示）`);
  await p.screenshot({ path: `${OUT}/sm-filter-lhb.png` });
  // 还原「全部」
  const allChip = p.locator('button', { hasText: '全部' }).first();
  await allChip.click({ timeout: 3000 }).catch(() => undefined);
  await p.waitForTimeout(1200);
  const rowsAll = await p.locator('[title$="个股终端"]').count();
  soft(rowsAll > 0, '「全部」还原后行数据恢复');
} else {
  soft(false, '「龙虎榜」筛选 chip 未找到');
}

// ---- 选股榜行点击 → 个股终端浮窗 ----
console.log('\n---- 选股榜行点击弹窗 ----');
const firstRow = p.locator('[title$="个股终端"]').first();
if (await firstRow.count()) {
  const rowTitle = await firstRow.getAttribute('title').catch(() => '');
  await firstRow.click().catch(() => undefined);
  let opened = false;
  const t0 = Date.now();
  while (Date.now() - t0 < 5000) {
    if (await terminalOpen()) { opened = true; break; }
    await p.waitForTimeout(300);
  }
  hard(opened, `选股榜行「${rowTitle || '?'}」点击 → 个股终端弹出`);
  if (opened) {
    await p.screenshot({ path: `${OUT}/sm-terminal.png` });
    // 终端懒加载：等待主体渲染（出现 复权 控件或至少不再是空态欢迎页）
    const termReady = await waitForText(
      (t) => !t.includes('在上方搜索框输入代码') && /复权|K线|分时/.test(t),
      15000,
    );
    soft(termReady, '终端主体自动选中标的并渲染（非空态）');
    if (termReady) await p.screenshot({ path: `${OUT}/sm-terminal-loaded.png` });
  }
  await closeTerminal();
  await p.waitForTimeout(800);
  const closedOk = !(await terminalOpen());
  soft(closedOk, '关闭后浮窗消失');
} else {
  hard(false, '选股榜行（title=打开 … 个股终端）未找到');
}

// ---- 席位榜窗口 5 → 20 日 ----
console.log('\n---- 席位榜窗口切换 ----');
const w20 = p.locator('button', { hasText: '20 日' }).first();
if (await w20.count()) {
  await w20.click({ timeout: 3000 }).catch(() => undefined);
  const w20Hit = await waitFor(
    () => Promise.resolve(apiCalls.some((c) => c.startsWith('200') && c.includes('branches?window=20'))),
    10000,
  );
  hard(w20Hit, '窗口切 20 日 → branches?window=20 重取 200');
  await p.screenshot({ path: `${OUT}/sm-branch-20.png` });
} else {
  soft(false, '「20 日」窗口 chip 未找到');
}

// ---- 证据台审计面 ----
console.log('\n---- 证据台审计面 ----');
f = await bodyText();
hard(f.includes('入柱（'), '入柱表存在');
hard(f.includes('否决（'), '否决清单存在');
soft(f.includes('入柱（4）'), '入柱数=4（与 ADMITTED_PILLARS 一致）');
soft(f.includes('仅展示证据（不入柱、不进分）'), 'evidence_only 引述区可见');
soft(!f.includes('部分数据源未就绪'), '无 partial 横幅（当日产物无缺口）');

// ---- 刷新按钮 ----
const refresh = p.locator('button', { hasText: '刷新' }).first();
if (await refresh.count()) {
  await refresh.click({ timeout: 3000 }).catch(() => undefined);
  await p.waitForTimeout(2500);
  soft((await p.locator('[title$="个股终端"]').count()) > 0, '刷新后选股榜仍在（重取无炸）');
}

// ---- 接口面 ----
console.log('\n---- /smart-money 接口 ----');
const uniq = [...new Set(apiCalls)];
console.log(uniq.length ? uniq.join('\n') : '(无调用)');
for (const ep of ['/overview', '/stocks', '/branches', '/evidence']) {
  hard(uniq.some((c) => c.startsWith('200') && c.includes(ep)), `接口 200：smart-money${ep}`);
}

console.log('\n---- 错误 ----');
console.log(errors.length ? [...new Set(errors)].slice(0, 10).join('\n') : '(无)');

console.log(`\n==== 硬断言失败 ${hardFails.length} / 软断言失败 ${softFails.length} ====`);
if (hardFails.length) console.log('硬失败：\n- ' + hardFails.join('\n- '));
if (softFails.length) console.log('软失败：\n- ' + softFails.join('\n- '));
await b.close();
process.exitCode = hardFails.length ? 1 : 0;
