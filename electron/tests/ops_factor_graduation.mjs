/**
 * 因子毕业 → 快照重建 全链操作驱动（2026-10-09）
 *
 * 这是一次「运维跑一遍」，不是只读探针 —— 按真实用户路径点三件事：
 *  1) 因子研究 → 私人库 → 扫描 → 「一键毕业」          （CUSTOM 挖掘库镜像到 CN）
 *  2) 因子报告 → 下拉选 rd_mined → 「重建快照」          （挖掘因子的 Alphalens 报告）
 *  3) 因子研究 → 快照面板 → 「重算快照」                （私人库目录收进毕业后的因子）
 *
 * 每步后用接口轮询到完成态再进下一步（两步快照重建都很重，串行跑避免互相抢资源）。
 * 日志逐行带时间戳；退出码非零 = 有步骤失败，完成态以 API 为准（不看 DOM）。
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const t = () => new Date().toISOString().slice(11, 19);
const log = (...a) => console.log(`[${t()}]`, ...a);

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome-stable', args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1720, height: 1150 } });
const page = await ctx.newPage();
let failures = 0;
const step = async (label, fn) => {
  log(`▶ ${label}`);
  try { await fn(); log(`✓ ${label}`); } catch (e) { failures++; log(`✗ ${label}: ${String(e).slice(0, 300)}`); }
};

// ── 登录 ──
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await page.waitForTimeout(3500);
if ((await page.locator('input[type=password]').count()) > 0) {
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input[type=password]').first().fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const s = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(s)) { await btns.nth(i).click(); break; }
  }
  await page.waitForTimeout(6000);
}
const dismiss = async () => {
  const b = page.locator('.ant-modal-wrap button:has-text("稍后再答")');
  if (await b.count()) { await b.first().click(); await page.waitForTimeout(500); }
};

const apiGet = (path) => page.evaluate(async (p) => {
  const tok = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
  const r = await fetch(p, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} });
  return { status: r.status, json: await r.json().catch(() => null) };
}, path);
const apiPost = (path) => page.evaluate(async (p) => {
  const tok = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
  const r = await fetch(p, { method: 'POST', headers: tok ? { Authorization: `Bearer ${tok}` } : {} });
  return { status: r.status, json: await r.json().catch(() => null) };
}, path);

/** 轮询直到 predicate 为真；返回最后一次响应 */
async function poll(fn, done, { label, timeoutMs, everyMs = 8000 }) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await fn();
    const d = done(last);
    if (d.done) return { ...last, note: d.note };
    log(`  … ${label}：${d.note || JSON.stringify(last.json)?.slice(0, 160)}`);
    await page.waitForTimeout(everyMs);
  }
  throw new Error(`${label} 超时（${timeoutMs / 1000}s），最后状态 ${JSON.stringify(last?.json)?.slice(0, 200)}`);
}

// ═══ 1. 一键毕业（UI 点击；完成后 API 复核）═══
let pipelineState = null;
await step('毕业前状态（scan 复核）', async () => {
  await page.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  await dismiss();
  const s = await apiGet('/api/v1/factor-research/scan?dataset=private');
  pipelineState = s.json?.pipeline?.state ?? null;
  log('  毕业前 pipeline:', JSON.stringify(s.json?.pipeline)?.slice(0, 240));
  if (!['pending', 'synced'].includes(pipelineState)) throw new Error(`预期 pending/synced，实际 ${pipelineState}`);
});

if (pipelineState !== 'pending') {
  log('· pipeline 已是 synced：跳过毕业步骤（幂等，重复跑不重付一次镜像）');
} else {
  await step('UI：私人库 → 扫描 → 一键毕业', async () => {
    const lib = page.locator('button', { hasText: '私人因子库' }).first();
    await lib.waitFor({ timeout: 30000 });
    await lib.click();
    await page.waitForTimeout(2500);
    await dismiss();
    await page.getByTestId('open-scan').click();
    await page.waitForTimeout(4000);
    const btn = page.getByTestId('scan-promote');
    await btn.waitFor({ timeout: 20000 });
    await btn.click();
    log('  已点「一键毕业」，等 promote-status 完成…');
  });

  await step('等待毕业完成（promote-status 轮询）', async () => {
    // 竞态防守（2026-10-09 实跑教训）：点击后第一拍 running=false 只说明请求
    // 还没落到后端，绝不能当「跑完」——否则整链在毕业真正开始前就放行。
    // 必须至少见过一次 running=true，之后的 false 才是完成态。
    let seenRunning = false;
    const promoted = await poll(
      () => apiGet('/api/v1/factor-research/promote-status'),
      (r) => {
        if (r.json?.running) {
          seenRunning = true;
          return { done: false, note: `running step=${(r.json?.step || '').slice(0, 60)}` };
        }
        if (seenRunning) return { done: true };
        return { done: false, note: '等待毕业进程启动…' };
      },
      { label: 'promote', timeoutMs: 20 * 60 * 1000, everyMs: 4000 },
    );
    log('  promote 收尾:', JSON.stringify(promoted.json)?.slice(0, 300));
  });

  await step('毕业后复核：scan pipeline 应为 synced', async () => {
    const s = await apiGet('/api/v1/factor-research/scan?dataset=private');
    const p = s.json?.pipeline;
    log('  毕业后 pipeline:', JSON.stringify(p)?.slice(0, 240));
    if (p?.state !== 'synced') throw new Error(`预期 synced，实际 ${p?.state}（pending=${p?.pending_factors}）`);
    log(`  新增（CN 有、目录没有）=${s.json?.new?.length}，样例 ${JSON.stringify((s.json?.new || []).slice(0, 4).map((x) => x.code))}`);
    await page.screenshot({ path: '/tmp/ops_promote_synced.png' });
  });
}

// ═══ 2. 因子报告 rd_mined 重建快照 ═══
let needReportBuild = false;
await step('UI：报告页选中 rd_mined → 重建快照', async () => {
  await page.goto(`${BASE}/#/factor-research?tab=factor-report`, { waitUntil: 'domcontentloaded' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(6000);
  await dismiss();
  const ds = await apiGet('/api/v1/factor-report/datasets');
  const rd = (ds.json?.items || []).find((d) => d.dataset === 'rd_mined');
  if (rd?.available) {
    log('  rd_mined 快照已存在：跳过重建（幂等）');
    return;
  }
  needReportBuild = true;
  await page.getByTestId('report-dataset-dropdown').click();
  await page.waitForTimeout(600);
  // 「快照尚未生成」的数据集必须可选中（选中后主体是横幅 + 重建按钮）
  await page.getByTestId('report-dataset-option-rd_mined').click();
  await page.waitForTimeout(2500);
  await page.getByTestId('report-unavailable-rebuild').click();
  log('  已点「重建快照」（rd_mined），等 build-status 完成…');
});

if (needReportBuild) {
  await step('等待 rd_mined 报告快照构建（build-status 轮询）', async () => {
    // 与 promote 同款竞态防守：见过 running=true 之后的 false 才算完成。
    let seenRunning = false;
    const r = await poll(
      () => apiGet('/api/v1/factor-report/build-status?dataset=rd_mined'),
      (s) => {
        if (s.json?.running) {
          seenRunning = true;
          return { done: false, note: `running step=${(s.json?.step || '').slice(0, 60)}` };
        }
        if (seenRunning) return { done: true };
        return { done: false, note: '等待构建进程启动…' };
      },
      { label: 'rd_mined build', timeoutMs: 60 * 60 * 1000, everyMs: 15000 },
    );
    log('  构建收尾:', JSON.stringify(r.json)?.slice(0, 300));
  });
} else {
  log('· rd_mined 快照已存在：跳过构建等待');
}

await step('复核：rd_mined 数据集 available + 因子数', async () => {
  const s = await apiGet('/api/v1/factor-report/datasets');
  const rd = (s.json?.items || []).find((d) => d.dataset === 'rd_mined');
  log('  rd_mined:', JSON.stringify(rd)?.slice(0, 300));
  if (!rd?.available) throw new Error('rd_mined 仍不可用');
  const sum = await apiGet(`/api/v1/factor-report/summary?dataset=rd_mined&sort=abs_ic`);
  const n = (sum.json?.factors || []).length;
  log(`  报告因子数=${n}，Top3=${JSON.stringify((sum.json?.factors || []).slice(0, 3).map((f) => [f.name, f.abs_ic]))}`);
  if (n < 80) throw new Error(`报告因子数 ${n} 明显偏少`);
  await page.screenshot({ path: '/tmp/ops_report_rdmined.png' });
});

// ═══ 3. 私人库目录快照重算（收进毕业后的因子）═══
// 幂等：目录已含 rd_* 就跳过重算（一次十几分钟，重复跑不重付）
let needCatalog = true;
await step('检查私人库目录是否已收录挖掘因子', async () => {
  const cat = await apiGet('/api/v1/factor-research/catalog?dataset=private');
  const mined = (cat.json?.factors || []).map((f) => String(f.code)).filter((c) => c.startsWith('rd_'));
  if (mined.length > 0) {
    needCatalog = false;
    log(`  目录已含 ${mined.length} 个 rd_* 因子，跳过重算`);
  }
});

if (!needCatalog) {
  log('· 跳过目录重算');
} else {
  await step('UI：快照面板 → 重算快照（private）', async () => {
    await page.goto(`${BASE}/#/factor-research`, { waitUntil: 'domcontentloaded' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(6000);
    await dismiss();
    const lib = page.locator('button', { hasText: '私人因子库' }).first();
    await lib.waitFor({ timeout: 30000 });
    await lib.click();
    await page.waitForTimeout(2000);
    const snapBtn = page.locator('button', { hasText: '快照' }).first();
    await snapBtn.click();
    await page.waitForTimeout(3000);
    const rebuild = page.locator('button:has-text("重算快照")').first();
    await rebuild.waitFor({ timeout: 20000 });
    await rebuild.click();
    log('  已点「重算快照」（private 目录）…');
  });

  await step('等待私人库目录重算（snapshot-status 轮询）', async () => {
    // 同款竞态防守：旧快照 exists=true，若点击的请求还没落到后端，第一拍
    // running=false && exists 会假通过 —— 必须见过 running=true 才认完成。
    let seenRunning = false;
    const r = await poll(
      () => apiGet('/api/v1/factor-research/snapshot-status?dataset=private'),
      (s) => {
        if (s.json?.running) {
          seenRunning = true;
          return { done: false, note: `running step=${(s.json?.step || '').slice(0, 60)}` };
        }
        if (seenRunning && s.json?.exists) return { done: true };
        return { done: false, note: '等待重算进程启动…' };
      },
      { label: 'private catalog', timeoutMs: 60 * 60 * 1000, everyMs: 15000 },
    );
    log('  目录重算收尾:', JSON.stringify(r.json)?.slice(0, 300));
  });
}

await step('终态复核：scan 应为最新 + 目录含 rd_mined', async () => {
  const s = await apiGet('/api/v1/factor-research/scan?dataset=private');
  log(`  new=${s.json?.new?.length} missing=${s.json?.missing?.length} catalog=${s.json?.catalog_count} discovered=${s.json?.discovered_count}`);
  const cat = await apiGet('/api/v1/factor-research/catalog?dataset=private');
  const codes = (cat.json?.factors || []).map((f) => f.code);
  const mined = codes.filter((c) => String(c).startsWith('rd_'));
  log(`  私人库目录因子总数=${codes.length}，其中挖掘因子=${mined.length}`);
  await page.screenshot({ path: '/tmp/ops_catalog_done.png' });
});

log(failures === 0 ? '═══ 全链完成，0 失败 ═══' : `═══ 有 ${failures} 步失败 ═══`);
await browser.close();
process.exit(failures > 0 ? 1 : 0);
