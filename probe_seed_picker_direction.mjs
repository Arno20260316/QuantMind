// 父本选择器正/反方向分组探针（T-MV-01 方向切分）
// 断言：展开面板后并发两请求 direction=pos / direction=neg 均 200；
// 分组头「正向（n）」「反向（n）」都出现且计数 > 0；两方向各有可见行。
import { chromium } from 'playwright';
const BASE = 'http://localhost:3080';
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1680, height: 980 } });
const errs = [];
const poolReqs = [];
page.on('pageerror', e => errs.push('[PAGEERROR] ' + (e.message || '').slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push('[console.error] ' + m.text().slice(0, 200)); });
page.on('response', async (res) => {
  const u = res.url();
  if (u.includes('/pool/factors') && u.includes('direction=')) {
    poolReqs.push({ url: u.replace(/^.*pool\/factors/, 'pool/factors'), status: res.status() });
  }
});

await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3500);
const inputs = page.locator('input');
if (await inputs.count() >= 2) {
  await inputs.nth(0).fill('admin'); await inputs.nth(1).fill('admin123');
  const btns = page.locator('button');
  for (let i = 0, n = await btns.count(); i < n; i++) {
    const t = ((await btns.nth(i).innerText().catch(() => '')) || '').replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.waitForTimeout(5000);
}
await page.goto(`${BASE}/#/alpha-research`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(13000);
for (const label of ['稍后再答', '稍后', '取消']) {
  const b = page.locator(`button:has-text("${label}")`).first();
  if (await b.count()) { await b.click().catch(() => {}); break; }
}
await page.waitForTimeout(2000);

const seedBtn = page.locator('button[title*="父本定向演化"]').first();
console.log('[probe] 父本按钮存在:', await seedBtn.count());
await seedBtn.click();
try {
  await page.waitForFunction(() => /正向（\d+）/.test(document.body.innerText), null, { timeout: 15000 });
  console.log('[probe] 分组头出现: ok');
} catch {
  console.log('[probe] 分组头等待超时');
}
await page.waitForTimeout(1200);

const info = await page.evaluate(() => {
  const txt = document.body.innerText;
  const pick = (re) => { const m = txt.match(re); return m ? m[0] : null; };
  const panel = [...document.querySelectorAll('div')].find(el =>
    typeof el.className === 'string' && /父本因子（可选/.test(el.textContent || '') && el.querySelector('button'));
  const rows = panel ? [...panel.querySelectorAll('button')].filter(b => b.title && !/最多选|清空/.test(b.title)) : [];
  return {
    posHead: pick(/正向（\d+）/), negHead: pick(/反向（\d+）/),
    rowCount: rows.length,
    firstNames: rows.slice(0, 8).map(b => (b.querySelector('span span') || {}).textContent || '').filter(Boolean),
  };
});
console.log('[probe] 请求:', JSON.stringify(poolReqs, null, 1));
console.log('[probe] 面板:', JSON.stringify(info, null, 1));
await page.screenshot({ path: 'shot_seed_picker_direction.png', fullPage: false });
console.log('[probe] console errors:', errs.length ? errs.slice(0, 5) : '无');
await browser.close();
