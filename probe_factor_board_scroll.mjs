// 因子概率板下半页截图：登录 → 因子概率板 tab → 内层滚动容器逐屏截图（排版目检用）
import { chromium } from 'playwright';
const BASE = 'http://localhost:3080';
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(3500);
const inputs = page.locator('input');
if (await inputs.count() >= 2) {
  await inputs.nth(0).fill('admin');
  await inputs.nth(1).fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < await btns.count(); i++) {
    const t = ((await btns.nth(i).innerText().catch(() => '')) || '').replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.waitForTimeout(5000);
}
await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(4500);
for (const sel of ['.ant-modal-close', 'button:has-text("稍后")', 'button:has-text("跳过")', 'button:has-text("取消")']) {
  const el = page.locator(sel).first();
  if ((await el.count()) > 0 && (await el.isVisible().catch(() => false))) { await el.click().catch(() => {}); await page.waitForTimeout(800); }
}
const tab = page.locator('button', { hasText: '因子概率板' }).first();
await tab.click({ force: true }).catch(async () => { await tab.click(); });
await page.waitForTimeout(3500);

// 找「质检与口径」卡片最近的页面级滚动祖先（跳过卡片内部 max-h 列表）
const info = await page.evaluate(() => {
  const hit = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 && e.textContent === '质检与口径');
  let el = hit;
  while (el) {
    const cs = getComputedStyle(el);
    if (el.scrollHeight > el.clientHeight + 100 && ['auto', 'scroll'].includes(cs.overflowY) && !el.className.includes('max-h-')) break;
    el = el.parentElement;
  }
  if (!el) return null;
  window.__scroller = el;
  return { cls: String(el.className).slice(0, 120), scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
});
console.log('[scroller]', JSON.stringify(info));
if (info) {
  const steps = Math.ceil(info.scrollHeight / info.clientHeight);
  for (let i = 0; i < Math.min(steps, 6); i++) {
    await page.evaluate((i) => { const el = window.__scroller; el.scrollTop = i * el.clientHeight; }, i);
    await page.waitForTimeout(600);
    await page.screenshot({ path: `/tmp/fb_scroll_${i}.png` });
    console.log('[SHOT] /tmp/fb_scroll_' + i + '.png');
  }
} else {
  await page.screenshot({ path: '/tmp/fb_scroll_none.png' });
}
await browser.close();
