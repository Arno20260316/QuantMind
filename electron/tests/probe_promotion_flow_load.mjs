/**
 * 晋升流程工作区加载探针（2026-10-09）
 *
 * 背景：`GET /api/v1/models/rollouts` 被 model_training 的 `GET /{model_id}`
 * 参数兜底抢走（model_id="rollouts"）→ 404 "Model not found" → 推理中心
 * 「晋升流程」面板报错。修复（include 顺序：具体前缀先于兜底）后验证：
 * 台账能加载（存在 ro_cn_ 行 / 阶段徽标），页面无 "Model not found"。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1720, height: 1150 } });
const p = await ctx.newPage();
const errors = [];
const rolloutReqs = [];
p.on('pageerror', (e) => errors.push((e.message || '').slice(0, 160)));
p.on('response', (r) => {
  if (r.url().includes('/models/rollouts')) {
    rolloutReqs.push(`${r.status()} ${r.request().method()} ${r.url().split('/api/v1')[1].slice(0, 100)}`);
  }
});

// ── 登录 ──
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
const later = p.locator('.ant-modal-wrap button:has-text("稍后再答")');
if (await later.count()) { await later.first().click(); await p.waitForTimeout(600); }

// ── 推理中心 → 晋升流程 ──
await p.goto(`${BASE}/#/inference-center`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(4500);
const later2 = p.locator('.ant-modal-wrap button:has-text("稍后再答")');
if (await later2.count()) { await later2.first().click(); await p.waitForTimeout(600); }

await p.locator('button, [role=tab], a', { hasText: '晋升流程' }).first().click();
await p.waitForTimeout(4500);

const flat = (await p.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ');
console.log('含「晋升流程」:', flat.includes('晋升流程'));
console.log('含 "Model not found":', flat.includes('Model not found'));
const roIds = flat.match(/ro_cn_[0-9_a-z]+/g) || [];
console.log('台账行 ro_cn_ id:', [...new Set(roIds)]);
console.log('阶段徽标（回放评估/观察期/待批准）:', ['回放评估', '观察期', '待批准'].filter((s) => flat.includes(s)));
console.log('挑战者名出现:', flat.includes('T+10 LightGBM 选股'));
console.log('ROLLOUT 请求:', JSON.stringify(rolloutReqs.slice(0, 6), null, 1));
console.log('ERRORS:', errors.length ? errors.slice(0, 4) : 'none');
await p.screenshot({ path: '/tmp/promotion_flow.png' });
await b.close();
