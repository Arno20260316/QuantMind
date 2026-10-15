/** 期货市场分析页 E2E：走真实用户路径（点「期货」→ 进「市场分析」→ 逐 Tab 断言 DOM）
 *
 * 依赖：本地部署（deploy_frontend.sh 到 quantmind-web:3080）+ 后端 /market-analysis-futures 可用。
 * 断言取向：内容关键字命中（确定性等待，不用固定 sleep 兜底断言）。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const EXPECT = {
  行情总览: ['领涨榜', '领跌榜', '主力榜单', '板块温度', '大盘基准', '交易所官方成交额', '数据质检'],
  板块热力: ['品种热力图', '板块统计', '入图', '点击板块看前瞻因子'],
  前瞻视角: ['前瞻榜', '有效覆盖', '合成权重', '口径说明', '品种画像', '靠前'],
  期限结构: ['近远月曲线', '主力合约', '近远月价差', '期限结构榜', '年化 carry'],
  持仓分析: ['CFTC', '投机仓（非商业）', '商业仓（套保）', '增减仓四象限', '席位净多榜', '席位明细'],
  仓单库存: ['库存主榜', '库存历史（东财口径）', '注册仓单对照', '仓单历史'],
  内外盘基差: ['价差列表', 'USD/CNY', '内外盘价差', '贵金属期现', '现货基差'],
};

const apiCalls = [];
const errors = [];
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();
p.on('console', (m) => { if (m.type() === 'error') errors.push('[console] ' + m.text().slice(0, 200)); });
p.on('pageerror', (e) => errors.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
p.on('response', (r) => {
  const u = r.url();
  if (/market-analysis-futures/.test(u)) {
    apiCalls.push(r.status() + ' ' + u.split('market-analysis-futures')[1].slice(0, 55));
    if (r.status() >= 400) errors.push('[API ' + r.status() + '] ' + u.slice(0, 140));
  }
});

const flat = (s) => s.replace(/\s+/g, ' ');
const bodyText = async () => flat(await p.locator('body').innerText().catch(() => ''));

/** 确定性等待：轮询 body 文本直到命中关键字（无固定 sleep 兜底断言） */
async function waitForText(substr, maxMs = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    if ((await bodyText()).includes(substr)) return true;
    await p.waitForTimeout(700);
  }
  return false;
}

/** 等待面板加载完成（页面无「加载中」空态） */
async function waitUntilLoaded(maxMs = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    const txt = await bodyText();
    if (txt.length > 0 && !txt.includes('加载中')) return true;
    await p.waitForTimeout(700);
  }
  return false;
}

/** 登录后可能弹公告/风险弹窗（ant-modal）拦截点击：先关掉 */
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

// ---- 点顶部市场切换器的「期货」 ----
await dismissModal();
const futBtn = p.locator('button[role=radio]', { hasText: '期货' }).first();
console.log('找到「期货」切换按钮:', (await futBtn.count()) > 0);
const switchClicked = await futBtn.click({ timeout: 8000 }).then(() => true).catch(() => false);
console.log('点击「期货」切换:', switchClicked);
if (!switchClicked) {
  // 兜底：弹窗无法关闭时直接写入市场持久化键并刷新（与切换器等价）
  await p.evaluate(() => localStorage.setItem('qm:current_market', 'FUTURES'));
  await p.reload({ waitUntil: 'domcontentloaded' });
}
await p.waitForTimeout(2500);
console.log('切换后 localStorage 市场:', await p.evaluate(() => localStorage.getItem('qm:current_market')));

// ---- 深链进「市场分析」（hash 路由） ----
await p.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await waitForText('期货市场全景', 40000);
await waitForText('上金所现货', 30000); // 统计卡已出（/overview 完成）
await dismissModal();
await p.waitForTimeout(500);

let f = await bodyText();
console.log('\nURL:', p.url());
console.log('✓ 渲染期货页标题:', f.includes('期货市场全景'));
console.log('✓ QuantFutures 引擎标识:', f.includes('QuantFutures'));
console.log('✓ 市场标识 pill:', f.includes('市场: 期货'));
console.log('✓ 未误渲染 A 股/港股页:', !f.includes('上证指数') && !f.includes('恒生指数'));
console.log('✓ 三层市场统计卡:', ['全市场', '国内品种', '国际品种', '上金所现货'].every((k) => f.includes(k)));
const countMatch = f.match(/(\d+) 品种/);
console.log('✓ 全市场品种数:', countMatch ? countMatch[1] : '(未解析到)');
console.log('✓ 名义额口径说明可见:', f.includes('名义'));

// ---- 逐 Tab 断言 ----
console.log('\n---- 各 Tab 内容断言 ----');
let pass = 0;
for (const [tab, keys] of Object.entries(EXPECT)) {
  const el = p.locator('button', { hasText: tab }).first();
  const clicked = await el.click({ timeout: 6000 }).then(() => true).catch(() => false);
  if (!clicked) { console.log(`✗ ${tab}: 按钮点击失败`); continue; }
  await waitUntilLoaded(20000);
  // 仓单库存：默认选中第一行（用户布局拍板），点击仓单对照第一行出「最新仓单」
  if (tab === '仓单库存') {
    const firstRow = p.locator('button[title*="点击查看仓单历史"]').first();
    if (await firstRow.count()) {
      await firstRow.click().catch(() => undefined);
      await waitForText('最新仓单', 15000); // 点击后才出现，动态补断言
      keys.push('最新仓单');
    } else {
      console.log('(仓单行未找到，历史断言跳过)');
    }
  }
  // 内外盘基差：默认选中第一组（黄金·内外盘价差率），右侧图表区出现
  if (tab === '内外盘基差') {
    await waitForText('个交易日', 20000);
    keys.push('价差率', '个交易日');
  }
  // 持仓分析：切换「商业仓（套保）」验证 CFTC 双面可点切
  // 判据 = 口径 caption 换成商业侧文案，而非按钮高亮
  if (tab === '持仓分析') {
    const swap = p.locator('button', { hasText: '商业仓（套保）' }).first();
    if (await swap.count()) {
      await swap.click().catch(() => undefined);
      await waitForText('产业套保盘', 10000);
      keys.push('上期报告期', '产业套保盘');
    } else {
      console.log('(商业仓切换按钮未找到)');
    }
  }
  f = await bodyText();
  const hit = keys.filter((k) => f.includes(k));
  const loading = f.includes('加载中');
  const ok = hit.length === keys.length && !loading;
  if (ok) pass++;
  console.log(`${ok ? '✓' : '✗'} ${tab}: ${hit.length}/${keys.length} [${hit.join(' / ')}]${loading ? ' 仍在加载' : ''}`);
  await p.screenshot({ path: `/tmp/futures-ma-${tab}.png` });
}
console.log(`\nTab 通过 ${pass}/${Object.keys(EXPECT).length}`);

// ---- 交叉钻取 ----
// a)（硬断言新增）板块热力统计行 → 跳「前瞻视角」并过滤该板块
const heatTab = p.locator('button', { hasText: '板块热力' }).first();
if (await heatTab.count()) {
  await heatTab.click().catch(() => undefined);
  await p.waitForTimeout(5000);
  const sectorRow = p.locator('button[title*="板块的前瞻因子板"]').first();
  if (await sectorRow.count()) {
    await sectorRow.click().catch(() => undefined);
    // 轮询等前瞻面板载完（新挂载 + 板块过滤），再断言落点
    let jumpedOutlook = false;
    const t0 = Date.now();
    while (Date.now() - t0 < 30000) {
      const txt = await bodyText();
      if (txt.includes('前瞻榜') && txt.includes('有效覆盖') && !txt.includes('加载中')) {
        jumpedOutlook = true;
        break;
      }
      await p.waitForTimeout(800);
    }
    f = await bodyText();
    console.log(`\n${jumpedOutlook ? '✓' : '✗'} 板块统计行点击 → 跳转前瞻视角（按板块过滤）`);
    await p.screenshot({ path: '/tmp/futures-ma-drill-outlook.png' });
  } else {
    console.log('\n✗(软) 板块统计行按钮未找到（title 文案变更？）');
  }
  // b)（软断言）热力图色块 → 跳期限结构并载入品种
  await heatTab.click().catch(() => undefined);
  await p.waitForTimeout(4000);
  const box = await p.locator('canvas').first().boundingBox().catch(() => null);
  if (box) {
    await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await p.waitForTimeout(4000);
    f = await bodyText();
    const jumped = f.includes('近远月曲线');
    console.log(`${jumped ? '✓' : '✗(软)'} 热力图点击色块 → 跳转期限结构并载入品种`);
    await p.screenshot({ path: '/tmp/futures-ma-drill.png' });
  }
}

console.log('\n---- market-analysis-futures 接口 ----');
const uniq = [...new Set(apiCalls)];
console.log(uniq.length ? uniq.join('\n') : '(无调用)');
console.log('\n---- 错误 ----');
console.log(errors.length ? [...new Set(errors)].slice(0, 10).join('\n') : '(无)');
await b.close();
