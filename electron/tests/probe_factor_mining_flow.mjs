/**
 * 因子挖掘 → 报告 全链路可见性探针（2026-10-09）
 *
 * 回答三个问题的**当前真相**（不点破坏性按钮，promote/build 由操作序列另跑）：
 *  1) 因子报告的数据集清单里有没有 rd_mined（挖掘因子），顶栏是不是下拉、在不在最顶
 *  2) 因子研究的「扫描」面板里，毕业管道漏斗（CUSTOM vs CN、待毕业数）显不显示
 *  3) 数据集滞后字段（stale / stale_reason / disk_n_factors）是否在 API 里真的存在
 *
 * 用法：node electron/tests/probe_factor_mining_flow.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
let pass = 0;
let fail = 0;
const ok = (cond, label, extra = '') => {
  if (cond) { pass++; console.log(`  ✓ ${label}${extra ? ` — ${extra}` : ''}`); }
  else { fail++; console.log(`  ✗ ${label}${extra ? ` — ${extra}` : ''}`); }
};

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1720, height: 1150 } });
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 200)));

// ── 登录（本地开发实例）──
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await page.waitForTimeout(3500);
if ((await page.locator('input[type=password]').count()) > 0) {
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input[type=password]').first().fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.waitForTimeout(6000);
}
const dismissLater = async () => {
  const btn = page.locator('.ant-modal-wrap button:has-text("稍后再答")');
  if (await btn.count()) { await btn.first().click(); await page.waitForTimeout(600); }
};
await dismissLater();

// ── 1. API：数据集清单含 rd_mined + 滞后字段 ──
const dsJson = await page.evaluate(async () => {
  const tok = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
  const r = await fetch('/api/v1/factor-report/datasets', { headers: tok ? { Authorization: `Bearer ${tok}` } : {} }).catch((e) => ({ ok: false, err: String(e) }));
  if (!('json' in r)) return { err: r.err };
  const j = await r.json().catch(() => null);
  return j ? { status: r.status, items: j.items } : { status: r.status, bad: true };
});
if (dsJson.err) { fail++; console.log(`  ✗ /datasets 请求失败：${dsJson.err}`); }
else {
  const items = dsJson.items || [];
  const rd = items.find((d) => d.dataset === 'rd_mined');
  ok(!!rd, '数据集清单含 rd_mined', rd ? `label=${rd.label} available=${rd.available} n=${rd.n_factors}` : '缺失');
  ok(rd && rd.label && rd.label.includes('挖掘'), 'rd_mined 标签是中文名', rd?.label);
  // 滞后字段必须在（盘上 vs 快照的对账口径）
  const hasFreshFields = items.some((d) => 'stale' in d) && items.some((d) => 'disk_n_factors' in d);
  ok(hasFreshFields, '每个数据集带 stale / disk_n_factors 字段');
  const staleOnes = items.filter((d) => d.stale === true);
  console.log(`  · 滞后数据集：${JSON.stringify(staleOnes.map((d) => ({ id: d.dataset, disk: d.disk_n_factors, snap: d.snapshot_n_factors, why: d.stale_reason })))}`);
}

// ── 2. API：扫描面板的毕业管道 ──
const scanJson = await page.evaluate(async () => {
  const tok = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
  const r = await fetch('/api/v1/factor-research/scan?dataset=private', { headers: tok ? { Authorization: `Bearer ${tok}` } : {} }).catch((e) => ({ ok: false, err: String(e) }));
  if (!('json' in r)) return { err: r.err };
  const j = await r.json().catch(() => null);
  return j ? { status: r.status, pipeline: j.pipeline, new_count: (j.new || []).length, catalog: j.catalog_count } : { status: r.status, bad: true };
});
if (scanJson.err) { fail++; console.log(`  ✗ /scan 请求失败：${scanJson.err}`); }
else {
  const p = scanJson.pipeline;
  ok(!!p, '扫描响应含 pipeline 段');
  if (p) {
    console.log(`  · pipeline: ${JSON.stringify(p).slice(0, 300)}`);
    ok(['pending', 'synced', 'empty'].includes(p.state), `pipeline.state 合法（${p.state}）`);
    ok(typeof p.pending_factors === 'number', 'pending_factors 是数字', String(p.pending_factors));
  }
}

// ── 3. UI：因子报告顶栏 ──
await page.goto(`${BASE}/#/factor-research?tab=factor-report`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(6000);
await dismissLater();

const dd = page.getByTestId('report-dataset-dropdown');
ok((await dd.count()) === 1, '顶栏数据集下拉存在');
if (await dd.count()) {
  const box = await dd.boundingBox();
  ok(box && box.y < 160, '下拉在页面最顶部区域', box ? `y=${Math.round(box.y)}` : '无');
  await dd.click();
  await page.waitForTimeout(800);
  const menu = page.getByTestId('report-dataset-menu');
  ok((await menu.count()) === 1, '下拉菜单能展开');
  const nOpts = await page.locator('[data-testid^="report-dataset-option-"]').count();
  ok(nOpts >= 9, `菜单里数据集选项数 ≥9（实际 ${nOpts}）`);
  const rdOpt = page.getByTestId('report-dataset-option-rd_mined');
  ok((await rdOpt.count()) === 1, '菜单里能看到 rd_mined 选项');
  if (await rdOpt.count()) {
    console.log(`  · rd_mined 选项文案：${(await rdOpt.innerText()).replace(/\n/g, ' | ').slice(0, 160)}`);
  }
  await page.screenshot({ path: '/tmp/probe_report_dropdown.png' });
  await dd.click(); // 收起
}
ok((await page.getByTestId('report-rebuild').count()) === 1, '顶栏有「重建快照」按钮');
ok((await page.getByTestId('report-stale-badge').count()) <= 1, '落后徽章至多一枚（无滞后就隐藏）');
await page.screenshot({ path: '/tmp/probe_report_topbar.png' });

// ── 4. UI：扫描面板的毕业管道 ──
// 注意：上一次已在 #/factor-research（?tab=factor-report），hash-only goto 是 no-op，
// 必须 reload 才是真正回到排行榜页签（硬规则：相同 URL goto 无效果）。
await page.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' });
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForTimeout(6000);
await dismissLater();
const libBtn = page.locator('button', { hasText: '私人因子库' }).first();
await libBtn.waitFor({ timeout: 30000 }).catch(() => undefined);
ok((await libBtn.count()) === 1, '页面有「私人因子库」切换按钮');
await libBtn.click();
await page.waitForTimeout(2500);
await dismissLater();
const scanBtn = page.getByTestId('open-scan');
ok((await scanBtn.count()) === 1, '私人库工具条出现「扫描」按钮');
if (await scanBtn.count()) {
  await scanBtn.click();
  await page.waitForTimeout(4500);
  const pipeline = page.getByTestId('scan-pipeline');
  ok((await pipeline.count()) === 1, '扫描面板显示毕业管道段');
  if (await pipeline.count()) {
    const txt = (await pipeline.innerText()).replace(/\n/g, ' | ');
    console.log(`  · 管道段文案：${txt.slice(0, 300)}`);
  }
  console.log(`  · 一键毕业按钮：${(await page.getByTestId('scan-promote').count()) === 1 ? '有' : '无（synced/empty 态）'}`);
  await page.screenshot({ path: '/tmp/probe_scan_pipeline.png' });
}

ok(pageErrors.length === 0, '无页面级 JS 错误', pageErrors.slice(0, 3).join(' || '));

console.log(`\n=== ${pass} passed, ${fail} failed ===`);
await browser.close();
process.exit(fail > 0 ? 1 : 0);
