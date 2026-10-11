/**
 * 资金流全景 · 延续证据台（v2 基线 + v3 重排 + v5 工作台重构）实机验收探针。
 *
 * 覆盖：
 *   - 大盘全景看板：主力资金净流入 Top 50（请求 limit=50 / 行数=JSON / 列表可滚动 /
 *     滚动后表头吸顶）；
 *   - JSON 双路：/money-flow/panorama（四层结构 + 链接契约）与 /money-flow/evidence
 *     （产物在、命中榜新鲜 hits_state=ok、档案三档 gate、否决清单 8 项、披露非空）；
 *   - v3 默认态：进入资金流全景即自动选中排名第一板块（无点击出明细 + 成分贡献榜在屏）；
 *   - v5 工作台：左列一窗两签（延续命中榜 ⇄ 板块榜，同卡 tab 切换、互斥显隐、切签
 *     即换列表；默认签契约 = 证据可用〔meta.evidence.available〕? 命中榜 : 板块榜）
 *     + 右列板块明细（左卡右缘 ≤ 明细左缘，左右栏同排）；
 *   - v5 底部压缩：否决清单 = 标题 + 8 项 chips（悬停 title 见统计与结论）+「展开
 *     审计详情」显露 8 行逐条明细（可收起）；口径与披露压缩一行（truncate + title 全文）；
 *   - DOM：桑基卡 / 证据台 / 信号档案 / 命中榜竖向列表（行数=JSON）；披露一行制
 *     （truncate + title 全文，全页恰 2 处）；
 *   - 档位标签：≥2/≥3/≥5 在场且计数与 JSON 一致，切换即过滤（空档显式空态）；
 *   - 成分贡献榜：成员行 recent（近 ≤5 交易日逐日涨跌幅）契约与单元格渲染；
 *   - v6 成分贡献榜：成员行 pct_20d（近 20 交易日累计涨幅，复利）契约；「20日涨幅」列
 *     DOM 渲染；逐日与 20 日涨幅一律带 % 书写（缺失显 —）；
 *   - 交互闭环：命中榜行 → 工作台板块明细（容忍自动选中同名不重取）→ 成分下钻个股卡
 *     → 归因 chip 回板块 → 命中 #2 联动切板块 → 关闭个股卡；
 *   - 徽章证据卡弹层：hover「连N」徽章 → 探针口径 / 可执行口径 / 分年 / 口径披露；
 *   - 展示面纪律：作用域内禁词（买入/卖出/前 20%）零命中、内部工单号零命中、
 *     「已实测不具预测力」文案在屏。
 *
 * 前置：证据产物已构建且跟到最新 L2 分区（hits_state=ok），否则先跑
 *   docker exec -w /app quantmind python backend/scripts/market_analysis/build_flow_evidence.py
 * 前置（Top 50）：快照已按 limit=50 重算（compute.py 构建或页面「市场分析」触发一次）。
 *
 * 用法：node electron/tests/probe_flow_evidence.mjs
 *      QM_BASE=http://localhost:3080 node electron/tests/probe_flow_evidence.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
const SHOT = process.env.QM_SHOT || '/tmp/probe_flow_evidence.png';

let pass = 0;
let fail = 0;
const ok = (cond, label, extra = '') => {
  if (cond) { pass++; console.log(`  ✓ ${label}${extra ? ` — ${extra}` : ''}`); }
  else { fail++; console.log(`  ✗ ${label}${extra ? ` — ${extra}` : ''}`); }
};

const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome-stable',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

// /money-flow/sector 响应跟踪：v3 起进入页面会「自动选中第一名」先发一次请求，
// 点击联动可能命中同一板块（无新请求）——用累计计数 + 最近载荷判定，二者皆真。
let sectorRespCount = 0;
let lastSectorJson = null;
page.on('response', (r) => {
  if (r.url().includes('/money-flow/sector')) {
    sectorRespCount++;
    r.json().then((j) => { lastSectorJson = j; }).catch(() => {});
  }
});

/** 点击板块入口并等待「该板块明细」生效（新响应或已因同名自动选中就位均算通过）。 */
const clickAndExpectSector = async (locator, name, timeoutMs = 15000) => {
  const before = sectorRespCount;
  await locator.click();
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (sectorRespCount > before && lastSectorJson?.name === name) break;
    if (lastSectorJson?.name === name) break; // 自动选中已就位（点击同名不触发重取）
    await page.waitForTimeout(200);
  }
  return lastSectorJson;
};

console.log(`\n===== 资金流全景 · 延续证据台实机探针（v3） @ ${BASE} =====`);

// ── 登录（样板同 probe_flow_tags_fusion.mjs）─────────────────────────
await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3000);
if ((await page.locator('input[type=password]').count()) > 0) {
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input[type=password]').first().fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) { await btns.nth(i).click(); break; }
  }
  await page.locator('input[type=password]').first().waitFor({ state: 'detached', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(2500);
}
const later = page.locator('button', { hasText: '稍后再答' });
if (await later.count()) { await later.first().click().catch(() => {}); await page.waitForTimeout(800); }

// ── 进入市场分析（默认落「大盘全景看板」；先验 Top 50 卡）──────────────
const stocksP = page.waitForResponse((r) => r.url().includes('/money-flow/stocks'), { timeout: 90000 });
await page.goto(`${BASE}/#/market-analysis`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);

const stocksResp = await stocksP.catch(() => null);
let stocksJson = null;
if (stocksResp) {
  const reqLimit = new URL(stocksResp.url()).searchParams.get('limit');
  ok(reqLimit === '50', '/money-flow/stocks 请求 limit=50', `limit=${reqLimit}`);
  stocksJson = await stocksResp.json().catch(() => null);
  ok(Array.isArray(stocksJson) && stocksJson.length === 50, '个股榜 50 行（快照已按 limit=50 重算）', `${stocksJson?.length} 行`);
}

await page.getByText('主力资金净流入 Top', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
const topCard = await page.evaluate(() => {
  const h = [...document.querySelectorAll('h3')].find((e) => e.textContent?.includes('主力资金净流入 Top'));
  let card = h;
  while (card && !String(card.className || '').includes('rounded-2xl')) card = card.parentElement;
  if (!card) return null;
  const sc = card.querySelector('div.overflow-y-auto');
  const rows = card.querySelectorAll('.ant-table-tbody > tr').length;
  return { title: h?.textContent || '', rows, hasScroll: !!sc };
});
ok(/主力资金净流入 Top 50 股票/.test(topCard?.title || ''), '看板卡标题 Top 50', (topCard?.title || '').slice(0, 40));
ok(stocksJson ? topCard?.rows === Math.min(50, stocksJson.length) : (topCard?.rows || 0) >= 5, 'DOM 表格行数 = JSON 行数（≤50）', `DOM ${topCard?.rows} / JSON ${stocksJson?.length}`);

// 滚动到底 → 可滚动 + 表头吸顶
const stickInfo = await page.evaluate(() => {
  const h = [...document.querySelectorAll('h3')].find((e) => e.textContent?.includes('主力资金净流入 Top'));
  let card = h;
  while (card && !String(card.className || '').includes('rounded-2xl')) card = card.parentElement;
  const sc = card?.querySelector('div.overflow-y-auto');
  if (!sc) return null;
  sc.scrollTop = sc.scrollHeight;
  const th = sc.querySelector('.ant-table-thead th');
  const r1 = th?.getBoundingClientRect();
  const r2 = sc.getBoundingClientRect();
  return { scrolled: sc.scrollTop > 0, thTop: r1?.top ?? null, scTop: r2.top };
});
ok(stickInfo?.scrolled === true, 'Top 50 列表可滚动到底（滚动容器生效）', '');
ok(
  stickInfo && Math.abs((stickInfo.thTop ?? -999) - stickInfo.scTop) < 4,
  '滚动后表头吸顶（贴合容器顶部）',
  `thTop=${stickInfo?.thTop?.toFixed(0)} scTop=${stickInfo?.scTop?.toFixed(0)}`,
);
await page.screenshot({ path: '/tmp/probe_flow_evidence_top50.png', fullPage: false });

// ── 切「资金流全景」tab（HashRouter 带 #/）─────────────────────────────
const tab = page.getByRole('button', { name: '资金流全景' }).first();
ok((await tab.count()) > 0, '「资金流全景」tab 在场');
const panP = page.waitForResponse((r) => r.url().includes('/money-flow/panorama'), { timeout: 90000 });
const evP = page.waitForResponse((r) => r.url().includes('/money-flow/evidence'), { timeout: 90000 });
await tab.click();
const [panResp, evResp] = await Promise.all([panP, evP]);
const pan = await panResp.json();
const ev = await evResp.json();
await page.waitForTimeout(2500);

// ── v3 默认态：自动选中排名第一板块（无点击出明细）────────────────────
const expectFirst = pan?.sectors?.[0]?.name ?? null;
{
  const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    if (expectFirst && lastSectorJson?.name === expectFirst) break;
    await page.waitForTimeout(250);
  }
}
ok(
  expectFirst ? lastSectorJson?.name === expectFirst : true,
  '进入即自动选中排名第一板块（无点击出明细）',
  `detail=${lastSectorJson?.name ?? '—'} / 期望 ${expectFirst ?? '—'}`,
);
await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
ok((await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).count()) > 0, '成分贡献榜在屏（自动选中即渲染）');

// ── 成分贡献榜:recent（近 ≤5 交易日逐日涨跌幅）契约与单元格 ───────────
const sectorJsonAuto = lastSectorJson;
const mAuto = (sectorJsonAuto?.members ?? []).find((m) => m.name);
ok(Array.isArray(mAuto?.recent) && mAuto.recent.length >= 2 && mAuto.recent.length <= 5, '成分行 recent 契约（近 2~5 日）', `${mAuto?.recent?.length} 日`);
ok(
  (mAuto?.recent ?? []).every((p) => typeof p.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(p.date) && (p.pct === null || typeof p.pct === 'number')),
  'recent 行 = {date: ISO, pct: number|null}',
  JSON.stringify((mAuto?.recent ?? []).slice(0, 2)),
);
const recentCells = await page.locator('span[title*="涨跌幅"]').count();
ok(recentCells >= 10, 'recent 单元格渲染（近5日涨跌幅 span）', `${recentCells} 个`);

// ── v6：成分贡献榜 20 日涨幅（pct_20d 契约 + 列渲染 + % 书写） ────────
ok(
  mAuto?.pct_20d === null || typeof mAuto?.pct_20d === 'number',
  '成分行 pct_20d 契约（number|null；缺失不落 0）',
  `pct_20d=${mAuto?.pct_20d}`,
);
const pct20Vals = (sectorJsonAuto?.members ?? []).map((m) => m.pct_20d).filter((v) => typeof v === 'number');
ok(pct20Vals.length >= 1, '成员 pct_20d 有实值（20 日窗复利）', `${pct20Vals.length}/${sectorJsonAuto?.members?.length} 行有值`);
const pct20Cells = page.locator('span[title*="20 交易日累计涨幅"]');
ok((await pct20Cells.count()) >= 1, '「20日涨幅」单元格渲染', `${await pct20Cells.count()} 个`);
const pct20Text = (await pct20Cells.first().innerText().catch(() => '')).trim();
ok(/%$/.test(pct20Text) || pct20Text === '—', '20日涨幅按百分比书写（带 % 或缺失显 —）', `样例「${pct20Text}」`);
const dayCellText = (await page.locator('span[title*="涨跌幅"]:not([title*="累计涨幅"])').first().innerText().catch(() => '')).trim();
ok(/%$/.test(dayCellText) || dayCellText === '—', '逐日涨幅按百分比书写（带 %）', `样例「${dayCellText}」`);

// ── JSON 侧：panorama 四层与契约 ─────────────────────────────────────
ok(pan?.available === true, 'panorama available:true', `trade_date=${pan?.trade_date}`);
const kinds = new Set((pan?.nodes ?? []).map((n) => n.kind));
ok(['entity', 'size', 'sector', 'stock'].every((k) => kinds.has(k)), '四层节点齐（entity/size/sector/stock）', [...kinds].join(','));
const names = new Set((pan?.nodes ?? []).map((n) => n.name));
const linkBad = (pan?.links ?? []).filter(
  (l) => !names.has(l.source) || !names.has(l.target) || l.value < 0 || Math.abs(l.value - Math.abs(l.net)) > 1e-6,
);
ok(linkBad.length === 0, '链接契约（端点在场 / value=|net|≥0）', `违例 ${linkBad.length}`);

// ── v4：桑基个股层双侧（6:4）不变量——每板块 = 主力净额前 3 + 末 2 ────
// direction=all 时后端对每个展示板块取流入排名 head(3) 与流出排名 tail(2)（去重）；
// 断言按 inner_rank/inner_total 复核每板块挂载个股集（容忍短板块自动收敛）。
const stockBySector = new Map();
for (const n of pan?.nodes ?? []) {
  if (n.kind !== 'stock' || !n.sector) continue;
  if (!stockBySector.has(n.sector)) stockBySector.set(n.sector, []);
  stockBySector.get(n.sector).push(n);
}
const bilateralBad = [];
for (const [sec, list] of stockBySector) {
  const total = list[0]?.inner_total ?? 0;
  const ranks = list.map((n) => n.inner_rank).sort((a, b) => a - b);
  const expect = total >= 5 ? [1, 2, 3, total - 1, total] : [...Array(total)].map((_, i) => i + 1);
  if (ranks.join(',') !== expect.join(',')) bilateralBad.push(`${sec}: [${ranks.join('/')}] vs [${expect.join('/')}]`);
}
ok(
  stockBySector.size > 0 && bilateralBad.length === 0,
  'v4：个股层双侧不变量（前3 + 末2，含 Top1 与末位）',
  bilateralBad.slice(0, 3).join(' | ') || `${stockBySector.size} 板块`,
);
ok((pan?.sectors ?? []).length >= 20, '板块行 ≥ 20', `${pan?.sectors?.length}`);

// ── JSON 侧：evidence 产物与四节数据 ─────────────────────────────────
ok(ev?.available === true, 'evidence available:true', ev?.reason ? `reason=${ev.reason}` : '');
ok(ev?.hits_state === 'ok', '命中榜新鲜（hits_state=ok）', `state=${ev?.hits_state} 产物日=${ev?.hits_trade_date}；陈旧请先重建产物`);
ok(Array.isArray(ev?.hits) && ev.hits.length > 0, '命中榜行数 > 0', `${ev?.hits?.length} 行`);
const condKeys = Object.keys(ev?.conditions ?? {}).sort();
ok(condKeys.join(',') === 'streak_2,streak_3,streak_5', '条件三档齐', condKeys.join(','));
const gateBad = condKeys.filter((k) => typeof ev.conditions[k]?.gate?.pass !== 'boolean');
ok(gateBad.length === 0, '每档 gate.pass 为布尔', `违例 ${gateBad.join(',') || '无'}`);
ok((ev?.rejected ?? []).length === 8, '否决清单 8 项', `${ev?.rejected?.length}`);
ok(typeof ev?.conventions?.price_source === 'string' && ev.conventions.price_source.includes('daily_forward'), '口径 price_source 直传', ev?.conventions?.price_source);
ok(ev?.conventions?.sector_set === 'industry2', '口径测量体系 = industry2（分类审计）', ev?.conventions?.sector_set);
ok((ev?.meta?.disclosure ?? []).length > 0, '披露清单非空', `${ev?.meta?.disclosure?.length} 条`);
const badgeHits = (ev?.hits ?? []).filter((h) => h.evidence && h.evidence.fwd?.n > 0);
ok(badgeHits.length > 0, '至少一条命中带证据徽章', `${badgeHits.length}/${ev?.hits?.length}`);
const hitsSorted = (ev?.hits ?? []).every((h, i, arr) => i === 0 || arr[i - 1].streak > h.streak || (arr[i - 1].streak === h.streak && (arr[i - 1].net_main ?? 0) >= (h.net_main ?? 0)));
ok(hitsSorted, '命中榜排序 = (-streak, -净额)（审计）', '');

// ── DOM 侧：各节与桑基渲染 ───────────────────────────────────────────
await page.getByText('主力与散户资金流全景 · 四层桑基', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
ok((await page.getByText('主力与散户资金流全景 · 四层桑基', { exact: false }).count()) > 0, '桑基卡标题在屏');
ok((await page.locator('div[_echarts_instance_]').count()) > 0, 'ECharts 实例挂载');
ok((await page.getByText('延续证据台 · 信号档案与口径', { exact: false }).count()) > 0, '延续证据台节在屏');

// ── v5：左列一窗两签（延续命中榜 ⇄ 板块榜）+ 右列板块明细 ─────────────
const tabStreakBtn = page.getByRole('button', { name: '延续命中榜' }).first();
const tabBoardBtn = page.getByRole('button', { name: '板块榜' }).first();
ok((await tabStreakBtn.count()) > 0 && (await tabBoardBtn.count()) > 0, 'v5：一窗两签（延续命中榜 / 板块榜）在场');
const tabsBox = await page.evaluate(() => {
  const bs = [...document.querySelectorAll('button')];
  const a = bs.find((e) => e.textContent?.trim() === '延续命中榜');
  const b = bs.find((e) => e.textContent?.trim() === '板块榜');
  if (!a || !b) return null;
  let ca = a;
  let cb = b;
  while (ca && !String(ca.className || '').includes('rounded-2xl')) ca = ca.parentElement;
  while (cb && !String(cb.className || '').includes('rounded-2xl')) cb = cb.parentElement;
  return { same: !!ca && ca === cb };
});
ok(tabsBox?.same === true, 'v5：两签同卡（单窗口 tab 切换）');

// 默认签契约（flow_panorama._evidence_context）：证据可用（meta.evidence.available=true，
// 正常态 reason=null）→ 默认「延续命中榜」；降级（not_applicable/not_built/stale）→ 默认「板块榜」。
// 先于任何点签断言，防「判据写成 reason==='ok'（后端从不发）」一类回归。
{
  const expectStreakDefault = pan?.meta?.evidence?.available === true;
  const defaultTab = await page.evaluate(() => {
    const bs = [...document.querySelectorAll('button')];
    const find = (t) => bs.find((e) => e.textContent?.trim() === t);
    const active = (el) => !!el && String(el.className || '').includes('bg-purple-600');
    return { streak: active(find('延续命中榜')), board: active(find('板块榜')) };
  });
  ok(
    defaultTab.streak === expectStreakDefault && defaultTab.board === !expectStreakDefault,
    'v5：默认签 = 证据可用 ? 命中榜 : 板块榜（契约）',
    `active: streak=${defaultTab.streak} board=${defaultTab.board} · evidence.available=${pan?.meta?.evidence?.available} reason=${pan?.meta?.evidence?.reason ?? 'null'}`,
  );
}

// 默认签：有证据 → 命中榜在前台（幂等点击确保激活）
await tabStreakBtn.click().catch(() => {});
await page.waitForTimeout(400);
ok((await page.getByText('连续净流入 ≥', { exact: false }).count()) > 0, 'v5：命中榜签前台（档位口径行在屏）');
const hitCards = page.locator('button[title*="点击联动桑基与工作台"]');
ok(
  (await page.locator('button[title*="点击联动桑基与工作台"]:visible').count()) === ev.hits.length,
  '命中列表行数 = JSON 命中行数（默认 ≥2 档，可见）',
  `DOM ${await page.locator('button[title*="点击联动桑基与工作台"]:visible').count()} / JSON ${ev.hits.length}`,
);

// 切「板块榜」签：板块行全部可见、命中榜让位；再切回恢复
await tabBoardBtn.click();
await page.waitForTimeout(400);
const boardVis = await page.locator('button[title^="板块榜 · "]:visible').count();
ok(boardVis === (pan?.sectors ?? []).length, 'v5：切「板块榜」签 → 板块行全部可见', `DOM ${boardVis} / JSON ${pan?.sectors?.length}`);
const hitVisHidden = await page.locator('button[title*="点击联动桑基与工作台"]:visible').count();
ok(hitVisHidden === 0, 'v5：板块榜签下命中榜隐藏（单窗两签互斥）', `visible ${hitVisHidden}`);
await tabStreakBtn.click();
await page.waitForTimeout(400);
ok(
  (await page.locator('button[title*="点击联动桑基与工作台"]:visible').count()) === ev.hits.length,
  'v5：切回命中榜签恢复列表',
);

// 右列：板块明细与左列榜单同排（左卡右缘 ≤ 明细左缘）
const layout = await page.evaluate(() => {
  const bs = [...document.querySelectorAll('button')];
  const b = bs.find((e) => e.textContent?.trim() === '板块榜');
  let card = b;
  while (card && !String(card.className || '').includes('rounded-2xl')) card = card.parentElement;
  const anchor = [...document.querySelectorAll('span')].find((e) => e.textContent?.includes('成分贡献榜'));
  let d = anchor;
  while (d && !String(d.className || '').includes('rounded-2xl')) d = d.parentElement;
  if (!card || !d) return null;
  const r1 = card.getBoundingClientRect();
  const r2 = d.getBoundingClientRect();
  return {
    listRight: Math.round(r1.right),
    listTop: Math.round(r1.top),
    detailLeft: Math.round(r2.left),
    detailTop: Math.round(r2.top),
  };
});
ok(
  layout && layout.listRight <= layout.detailLeft + 4 && Math.abs(layout.listTop - layout.detailTop) < 40,
  'v5：板块明细移到右列（左右栏同排）',
  JSON.stringify(layout),
);

// ── v5 底部压缩：否决 chips 一行（可展开审计明细）+ 口径一行（title 全文） ──
ok((await page.getByText('信号档案 · 板块「连N」历史条件统计', { exact: false }).count()) > 0, '信号档案节在屏');
ok((await page.getByText('否决清单 · 已实测、不具预测力、不参与展示', { exact: false }).count()) > 0, '「已实测、不具预测力」文案在屏（v5 压缩行）');
const rejectBox = await page.evaluate(() => {
  const h = [...document.querySelectorAll('h4')].find((e) => e.textContent?.includes('否决清单'));
  if (!h) return null;
  const row = h.closest('div');
  const chips = [...row.querySelectorAll('span')].filter((s) => s.getAttribute('title'));
  return {
    chips: chips.length,
    sample: (chips[0]?.getAttribute('title') || '').slice(0, 48),
    hasToggle: !!row.querySelector('button'),
  };
});
ok(rejectBox?.chips === 8, 'v5：否决清单 8 项 chips（悬停见统计与结论）', `${rejectBox?.chips} 条 · ${rejectBox?.sample}`);
ok(rejectBox?.hasToggle === true, 'v5：「展开审计详情」开关在场');
await page.getByRole('button', { name: '展开审计详情' }).first().click();
await page.waitForTimeout(300);
const auditBox = await page.evaluate(() => {
  const btn = [...document.querySelectorAll('button')].find((e) => e.textContent?.includes('收起审计详情'));
  if (!btn) return null;
  const box = btn.closest('div.flex.flex-col.gap-1');
  const badges = [...(box || document).querySelectorAll('span')].filter((s) =>
    ['否决', '背景', '对照'].includes(s.textContent?.trim() || ''),
  );
  return { badges: badges.length };
});
ok(auditBox?.badges === 8, 'v5：展开审计详情 → 8 行逐条明细', `${auditBox?.badges} 行`);
await page.getByRole('button', { name: '收起审计详情' }).first().click();
await page.waitForTimeout(300);
const convInfo = await page.evaluate(() => {
  const label = [...document.querySelectorAll('span')].find((s) => s.textContent?.trim() === '口径与披露');
  if (!label) return null;
  const val = label.parentElement?.querySelector('span.truncate');
  return { hasVal: !!val, titleLen: (val?.getAttribute('title') || '').length };
});
ok(convInfo?.hasVal === true && convInfo.titleLen > 80, 'v5：口径与披露压缩一行（truncate + title 全文）', `title ${convInfo?.titleLen} 字符`);

// ── v3 档位标签：≥2/≥3/≥5 在场，切换即过滤 ──────────────────────────
const expCounts = {
  2: ev.hits.filter((h) => h.streak >= 2).length,
  3: ev.hits.filter((h) => h.streak >= 3).length,
  5: ev.hits.filter((h) => h.streak >= 5).length,
};
for (const lv of [2, 3, 5]) {
  ok((await page.getByRole('button', { name: new RegExp(`≥ ${lv} 日`) }).count()) > 0, `档位标签「≥ ${lv} 日」在场`, `计数应=${expCounts[lv]}`);
}
await page.getByRole('button', { name: /≥ 3 日/ }).first().click();
await page.waitForTimeout(500);
ok((await hitCards.count()) === expCounts[3], '切「≥ 3 日」→ 列表过滤', `DOM ${await hitCards.count()} / 期望 ${expCounts[3]}`);
await page.getByRole('button', { name: /≥ 5 日/ }).first().click();
await page.waitForTimeout(500);
ok((await hitCards.count()) === expCounts[5], '切「≥ 5 日」→ 列表过滤', `DOM ${await hitCards.count()} / 期望 ${expCounts[5]}`);
if (expCounts[5] === 0) {
  ok((await page.getByText('该档位当前无命中', { exact: false }).count()) > 0, '≥5 空档显式空态（不假装有数据）');
}
await page.getByRole('button', { name: /≥ 2 日/ }).first().click();
await page.waitForTimeout(500);
ok((await hitCards.count()) === expCounts[2], '切回「≥ 2 日」→ 全量命中', `DOM ${await hitCards.count()} / 期望 ${expCounts[2]}`);

// ── v3 披露一行制（证据台 + 常驻底部；truncate + title 全文）──────────
const discInfo = await page.evaluate(() => {
  const ps = [...document.querySelectorAll('p')].filter((e) => e.textContent?.includes('幸存者偏差'));
  return ps.map((e) => ({
    textLen: e.textContent.length,
    titleLen: (e.getAttribute('title') || '').length,
    truncated: String(e.className || '').includes('truncate'),
  }));
});
ok(discInfo.length === 2, '披露一行制：全页恰 2 行（证据台底 + 常驻底）', `${discInfo.length} 处`);
ok(discInfo.every((d) => d.truncated && d.titleLen >= d.textLen), '每行披露 truncate + title 全文', JSON.stringify(discInfo));

// ── 展示面禁词与工单号零命中（作用域=资金流全景面板子树）──────────────
const scopeText = await page.evaluate(() => {
  const anchor = [...document.querySelectorAll('h3')].find((e) => e.textContent?.includes('主力与散户资金流全景'));
  let el = anchor;
  while (el && !String(el.className || '').includes('gap-3.5')) el = el.parentElement;
  return (el || anchor?.parentElement)?.innerText || '';
});
const banned = ['买入', '卖出', '前 20%', '前20%'];
const bannedHit = banned.filter((w) => scopeText.includes(w));
ok(bannedHit.length === 0, '展示面禁词零命中（买入/卖出/前 20%）', bannedHit.join(',') || '无');
const ticketRe = [/T-[A-Z]{1,4}-?\d+/, /\bP\d+-\d+/];
const ticketHit = ticketRe.filter((re) => re.test(scopeText)).map(String);
ok(ticketHit.length === 0, '内部工单号零命中', ticketHit.join(',') || '无');
ok(scopeText.includes('不具预测力'), '展示面含「不具预测力」披露', '');

// ── 交互①：命中榜行 → 工作台板块明细（容忍自动选中同名不重取）────────
const hit0 = ev.hits[0];
const escAttr = (s) => s.replace(/"/g, '\\"');
const card0 = page.locator(`button[title^="${escAttr(hit0.name)} ·"]`).first();
ok((await card0.count()) > 0, `命中 #1「${hit0.name}」行可定位`);
let sectorJson = null;
if (await card0.count()) {
  sectorJson = await clickAndExpectSector(card0, hit0.name);
}
ok(sectorJson?.available === true && sectorJson?.name === hit0.name, '点命中行 → 工作台载入该板块明细', `detail=${sectorJson?.name}`);
await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).first().waitFor({ timeout: 20000 }).catch(() => {});
ok((await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).count()) > 0, '成分贡献榜在屏');

// ── 交互②：徽章证据卡弹层（hover）────────────────────────────────────
if (badgeHits.length) {
  const bh = badgeHits[0];
  const card = page.locator(`button[title^="${escAttr(bh.name)} ·"]`).first();
  await card.locator('span.cursor-help').first().hover().catch(() => {});
  const pop = page.locator('.ant-popover').filter({ hasText: '探针口径' }).first();
  await pop.waitFor({ state: 'visible', timeout: 6000 }).catch(() => {});
  const popText = (await pop.count()) ? await pop.innerText().catch(() => '') : '';
  ok(/连续净流入 ≥\d+ 日/.test(popText), `徽章卡 label`, popText.match(/连续净流入 ≥\d+ 日/)?.[0] || '');
  ok(popText.includes('探针口径 t→t+3'), '徽章卡含探针口径列');
  ok(popText.includes('可执行口径 T+1 收盘建仓'), '徽章卡含可执行主口径列');
  ok(popText.includes('命中') && popText.includes('基准'), '徽章卡含命中/基准');
  ok(popText.includes('历史超额') && popText.includes('未来收益'), '徽章卡常驻「历史超额≠未来收益」');
  await page.mouse.move(10, 10);
}

// ── 交互③：成分下钻个股卡 ────────────────────────────────────────────
const m0 = (sectorJson?.members ?? []).find((m) => m.name);
let stockJson = null;
if (m0) {
  const [stkResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/money-flow/stock'), { timeout: 60000 }),
    page.getByRole('button', { name: m0.name }).first().click(),
  ]);
  stockJson = await stkResp.json();
  await page.waitForTimeout(1500);
}
ok(stockJson?.available === true && stockJson?.symbol === m0?.symbol, '点成分股 → 个股卡取数', `symbol=${stockJson?.symbol}`);
ok((await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) > 0, '个股卡「板块归因（回板块）」在屏');

// ── 交互④：归因 chip 回板块（同板块 = 幂等回视）───────────────────────
const chipName = (stockJson?.attribution ?? [])[0]?.name;
if (chipName) {
  // 归因 chip 为 rounded-full（工作台板块行是圆角矩形，避免误点）
  await page.locator('button.rounded-full').filter({ hasText: chipName }).first().click().catch(() => {});
  await page.waitForTimeout(1200);
  ok((await page.getByText('成分贡献榜（窗口净额 · 点击下钻个股）', { exact: false }).count()) > 0, `归因 chip「${chipName}」回板块后工作台在屏`);
}

// ── 交互⑤：命中 #2 联动切板块（个股卡保持；容忍同名自动选中）──────────
const hit1 = ev.hits[1];
if (hit1) {
  const card1 = page.locator(`button[title^="${escAttr(hit1.name)} ·"]`).first();
  const sectorJson2 = await clickAndExpectSector(card1, hit1.name);
  ok(sectorJson2?.name === hit1.name, `命中 #2「${hit1.name}」联动切换工作台`, `detail=${sectorJson2?.name}`);
  ok(stockJson ? (await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) > 0 : true, '切板块后个股卡保持（个股↔板块双向不打断）');
}

// ── 交互⑥：关闭个股卡 ───────────────────────────────────────────────
if (await page.locator('button[aria-label="关闭个股详情"]').count()) {
  await page.locator('button[aria-label="关闭个股详情"]').first().click();
  await page.waitForTimeout(1000);
  ok((await page.getByText('所属板块归因（点击回板块视角）', { exact: false }).count()) === 0, '关闭个股卡后详情卡卸载');
}

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.slice(0, 2).join(' | '));

await page.screenshot({ path: SHOT, fullPage: true });
console.log(`  截图：${SHOT}`);
console.log(`===== ${fail === 0 ? 'PASS' : 'FAIL'} (${pass} ✓ / ${fail} ✗) =====`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
