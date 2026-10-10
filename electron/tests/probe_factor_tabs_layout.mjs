/** 因子研究剩余页签版面勘察（单因子/对比/合成）——定位「顶部的右侧的因子分类」 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();

await p.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await p.waitForTimeout(3500);
if ((await p.locator('input[type=password]').count()) > 0) {
  await p.locator('input').nth(0).fill('admin');
  await p.locator('input[type=password]').first().fill('admin123');
  const btns = p.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await p.waitForTimeout(6000);
}
const dismissLater = async () => {
  const btn = p.locator('.ant-modal-wrap button:has-text("稍后再答")');
  if (await btn.count()) { await btn.first().click(); await p.waitForTimeout(600); }
};
await dismissLater();

for (const tab of ['single', 'compare', 'compose', 'screening']) {
  await p.goto(`${BASE}/#/factor-research?tab=${tab}`, { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(7000);
  await p.screenshot({ path: `/tmp/factor_tab_${tab}.png` });
  // 顶部 200px 区域的文字，帮助定位
  const top = await p.evaluate(() => {
    const el = document.elementFromPoint ? document.querySelector('#root') : null;
    return (el?.innerText || '').slice(0, 300).replace(/\n+/g, ' | ');
  });
  console.log(`TAB ${tab}:`, top.slice(0, 260));
}
await b.close();
