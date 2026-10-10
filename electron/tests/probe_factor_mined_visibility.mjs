/**
 * 因子研究/因子报告 挖掘因子可见性 + 版面现状探针（2026-10-09）
 *
 * 用户诉求现场勘查：
 *  1) 因子研究（私人因子库）里能不能看到挖掘因子（rd_mined_*）
 *  2) 因子报告里有没有挖掘因子的数据集
 *  3) 「顶部的右侧的因子分类」当前长什么样（截图）
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();
const errors = [];
p.on('pageerror', (e) => errors.push((e.message || '').slice(0, 200)));

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

// ── 因子研究（默认排行榜）──
await p.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(9000);
await dismissLater();
await p.screenshot({ path: '/tmp/factor_research_main.png', fullPage: false });

// 私人库在不在、挖掘因子在不在：抓目录请求响应
const cat = await p.evaluate(async () => {
  const tok = localStorage.getItem('token') || localStorage.getItem('access_token') || '';
  const r = await fetch('/api/v1/factor-research/catalog?dataset=private', {
    headers: tok ? { Authorization: `Bearer ${tok}` } : {},
  }).catch((e) => ({ ok: false, err: String(e) }));
  if (!('ok' in r)) return { err: r.err };
  const j = await r.json().catch(() => null);
  if (!j) return { status: r.status, ok: r.ok };
  const codes = (j.factors || []).map((f) => f.code);
  const mined = codes.filter((c) => String(c).startsWith('rd_mined'));
  return {
    status: r.status,
    total: codes.length,
    minedCount: mined.length,
    minedSample: mined.slice(0, 6),
  };
});
console.log('CATALOG(private):', JSON.stringify(cat));

// ── 因子报告页签 ──
await p.goto(`${BASE}/#/factor-research?tab=factor-report`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(8000);
await dismissLater();
await p.screenshot({ path: '/tmp/factor_report_page.png', fullPage: false });

const ds = await p.evaluate(async () => {
  const r = await fetch('/api/v1/factor-report/datasets').catch((e) => ({ ok: false, err: String(e) }));
  if (!('json' in r)) return { err: r.err };
  const j = await r.json().catch(() => null);
  return j ? { items: (j.items || []).map((d) => ({ id: d.dataset, label: d.label, available: d.available, n: d.n_factors })) } : { status: r.status };
});
console.log('REPORT DATASETS:', JSON.stringify(ds, null, 1));

// 页面上「分类」相关文字（截取顶栏区域 innerText 观察）
const topText = await p.evaluate(() => {
  const root = document.querySelector('#root') || document.body;
  return (root.innerText || '').slice(0, 1200);
});
console.log('PAGE TEXT HEAD:\n', topText.slice(0, 900));

console.log('PAGE ERRORS:', JSON.stringify(errors.slice(0, 5)));
await b.close();
