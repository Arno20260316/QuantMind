/**
 * 智能体对话摘要直出机器码修复 + RSS 资讯桌面排版批 验收探针（2026-10-15）。
 *
 * 断的是两件事（对应细案 《智能体对话与RSS资讯_桌面排版优化细案_20261015.md》）：
 *
 *   A. 模型对话（实盘栏 → 盘中实况，默认页签）：
 *      ① agent 现为纯结构化 JSON 输出，总结条第 3 级回退「决策扼要行」必须生效
 *         —— 卡片里不得出现 `{` / `"decisions"` 这类机器码残串；
 *      ② 卡头日期是「MM-DD HH:mm」口径（aware 走北京时间），不得出现裸 `T`。
 *      反假通过：先从 arena API 交叉核对今日日志**确实**含纯 JSON 回复
 *      （jsonRounds > 0）——否则「无机器码」可能只是因为没有机器码可漏。
 *
 *   B. RSS 资讯桌面（#/rss-news，1600×1000）：
 *      ③ 分类行折叠：热词 chip 默认只出 4 个，隐藏数 = API 热词总数 − 4，
 *         「更多分类 (N)」可展开/收起且 chip 数守恒；
 *      ④ 占位摘要滤除：逐条与同一 API 数据对账（每条应显/应隐一致 + 内容前缀一致），
 *         不做「页面上没看到就算过」的真空断言；并兜底断言全页无占位串。
 *      ⑤ 右栏自动开第一篇正文（未点任何列表项），标题与列表首条一致。
 *      ⑥ 全程零 pageerror。
 *
 * 前置：quantmind-web（3080）在跑且后端可达；admin/admin123 可登录。
 * 用法：node electron/tests/probe_chat_digest_news_desktop.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.PROBE_BASE || 'http://localhost:3080';
const USER = process.env.PROBE_USER || 'admin';
const PASS = process.env.PROBE_PASS || 'admin123';

const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome-stable',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const errs = [];
page.on('pageerror', (e) => errs.push('[PAGEERROR] ' + (e.message || '').slice(0, 220)));

const text = async (loc) => ((await loc.innerText().catch(() => '')) || '').replace(/\s/g, '');
const clickByText = async (re, scope = 'button') => {
  const items = page.locator(scope);
  for (let i = 0, n = await items.count(); i < n; i++) {
    if (re.test(await text(items.nth(i)))) { await items.nth(i).click({ timeout: 4000 }); return true; }
  }
  return false;
};

/** 关掉所有盖全屏的弹窗（合规/风险评测；不点掉后续点击全被遮罩吃掉） */
const dismissModals = async () => {
  for (let round = 0; round < 8; round++) {
    const blocking = await page.evaluate(
      () => Array.from(document.querySelectorAll('.ant-modal-wrap'))
        .filter((m) => getComputedStyle(m).display !== 'none').length,
    );
    if (!blocking) return true;
    if (await clickByText(/稍后再答|同意|确认|我知道了|已阅读|知道了|跳过/, '.ant-modal:visible button')) {
      await page.waitForTimeout(800);
      continue;
    }
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(600);
  }
  return false;
};

/** 页内取数（与前端同令牌；纯读取，不写任何东西） */
const apiJson = (path) =>
  page.evaluate(async (p) => {
    const token = localStorage.getItem('access_token') || '';
    const res = await fetch(`/api/v1${p}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) return { __status: res.status };
    return res.json();
  }, path);

const results = [];
const check = (name, pass, detail = '') => {
  results.push({ name, pass, detail });
  console.log(`${pass ? '✅' : '❌'} ${name}${detail ? `  — ${detail}` : ''}`);
};

// ── 登录（样板） ────────────────────────────────────────────────────────────
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(4000);
const inputs = page.locator('input:visible');
if ((await inputs.count()) >= 2) {
  await inputs.nth(0).fill(USER);
  await inputs.nth(1).fill(PASS);
  await clickByText(/登录/);
  await page.waitForTimeout(6000);
}
await dismissModals();

console.log(`\n=== 对话摘要 + RSS 桌面排版验收 @ ${BASE} ===\n`);

// ═══════════════════════════════════════════════════════════════════════════
// A. 模型对话（盘中实况，默认页签）
// ═══════════════════════════════════════════════════════════════════════════
console.log('— A. 盘中实况 → 模型对话 —');
await page.goto(`${BASE}/#/live`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(8000);
await dismissModals();

const liveBtn = page.locator('button').filter({ hasText: /^盘中实况$/ }).first();
await liveBtn.click({ timeout: 8000 }).catch(async () => {
  await dismissModals();
  await liveBtn.click({ force: true });
});
await page.locator('.qm-arena-root').first().waitFor({ timeout: 20000 }).catch(() => {});
await page.locator('.qm-arena-root .mc-card').first().waitFor({ timeout: 30000 }).catch(() => {});
await page.waitForTimeout(2500);

const cardInfo = await page.evaluate(() => ({
  rootOk: !!document.querySelector('.qm-arena-root'),
  cards: document.querySelectorAll('.qm-arena-root .mc-card').length,
  summaries: Array.from(document.querySelectorAll('.qm-arena-root .mc-sum-text')).map((e) => (e.textContent || '').trim()),
  dates: Array.from(document.querySelectorAll('.qm-arena-root .mc-date')).map((e) => (e.textContent || '').trim()),
}));

// 反假通过：交叉核对原始日志里确实有纯 JSON 回复（有机器码可漏才算验到修复）
const agentsRaw = await apiJson('/agent-arena/agents?market=cn');
const agents = agentsRaw?.data ?? agentsRaw;
const agent = Array.isArray(agents) ? (agents.find((a) => a.has_log) ?? agents[0]) : null;
let jsonRounds = 0;
let totalRounds = 0;
let logsStatus = null;
if (agent) {
  const logsRaw = await apiJson(`/agent-arena/agents/${encodeURIComponent(agent.name)}/logs?market=cn&limit=400`);
  logsStatus = logsRaw?.__status ?? null;
  const logs = logsRaw?.data ?? logsRaw;
  if (Array.isArray(logs)) {
    totalRounds = logs.length;
    for (const l of logs) {
      for (const m of (l.new_messages || l.messages || [])) {
        if (m.role === 'assistant' && typeof m.content === 'string' && m.content.trim().startsWith('{')) jsonRounds++;
      }
    }
  }
}

check('「盘中实况」点开后 arena 作用域在', cardInfo.rootOk);
if (cardInfo.cards === 0) {
  check('模型对话有卡片可验', false,
    `无 .mc-card（agent=${agent?.name ?? '?'} 日志回合 ${totalRounds}，API 状态 ${logsStatus ?? 'ok'}）——无法验收摘要口径`);
} else {
  check(`模型对话渲染 ${cardInfo.cards} 张卡、${cardInfo.summaries.length} 条总结`, cardInfo.summaries.length > 0);
  const bad = cardInfo.summaries.filter((s) => s.includes('{') || s.includes('"decisions"'));
  check('总结条无原始 JSON 残串', cardInfo.summaries.length > 0 && bad.length === 0, bad[0]?.slice(0, 70));
  check(`原始日志含纯 JSON 回复（有机器码可修）`, jsonRounds > 0,
    `agent=${agent?.name ?? '?'} 共 ${totalRounds} 回合，纯 JSON 助手回复 ${jsonRounds} 条`);
  const badDates = cardInfo.dates.filter((d) => d !== '—' && !/^\d{2}-\d{2}( \d{2}:\d{2})?$/.test(d));
  check('卡头日期全为「MM-DD (HH:mm)」口径、无裸 T', cardInfo.dates.length > 0 && badDates.length === 0,
    badDates[0] ?? cardInfo.dates[0]);
}
await page.screenshot({ path: '/tmp/qm_chat_digest_20261015.png' });

// ═══════════════════════════════════════════════════════════════════════════
// B. RSS 资讯桌面（#/rss-news）
// ═══════════════════════════════════════════════════════════════════════════
console.log('\n— B. RSS 资讯桌面 —');
// 清掉本地筛选残留：探针要在默认口径（全部/时间倒序/无筛选）下对账
await page.evaluate(() => localStorage.removeItem('news_panel_filters')).catch(() => {});
await page.goto(`${BASE}/#/rss-news`, { waitUntil: 'domcontentloaded' });
// 不 catch 会让列表渲染失败以裸 TimeoutError 崩掉整个探针，后面的具名断言一个都跑不到
await page.locator('.news-article-item').first().waitFor({ timeout: 30000 }).catch(() => {});
// 热词 chip 依赖 stats 接口（晚于列表）；等它到位（或 15s 超时后按折叠 0 热词处理）
await page.waitForFunction(
  () => document.querySelectorAll('.news-category-row .ant-tag').length > 10 || !!document.querySelector('.news-category-more'),
  { timeout: 15000 },
).catch(() => {});
await page.waitForTimeout(1500);

// —— 对账数据（与页面同口径：默认首页 50 条 / 时间倒序；stats 无筛选参数） ——
// 列表每 10s 轮询刷新：API 取数与 DOM 快照之间可能插进新文章 → 条数对不上时重取一轮再判
// （只容忍「读取窗口内的插入」，最多 3 轮；三轮仍不一致 = 真偏差）。
const fetchArticles = async () => {
  const raw = await apiJson('/news/articles?page=1&page_size=50&sort=time_desc');
  const body = raw && Array.isArray(raw.articles) ? raw : (raw?.data ?? {});
  return Array.isArray(body.articles) ? body.articles : [];
};
const readDom = () => page.evaluate(() =>
  Array.from(document.querySelectorAll('.news-article-item')).map((el) => ({
    title: (el.querySelector('.news-article-title')?.textContent || '').trim(),
    summary: (el.querySelector('.news-article-summary')?.textContent || '').trim() || null,
  })),
);
let apiArticles = [];
let domItems = [];
let countTrials = 0;
for (; countTrials < 3; countTrials++) {
  apiArticles = await fetchArticles();
  domItems = await readDom();
  if (domItems.length === apiArticles.length && domItems.length > 0) break;
  await page.waitForTimeout(1200);
}
const statsRaw = await apiJson('/news/enrichment/stats');
const statsBody = statsRaw && Array.isArray(statsRaw.top_events) ? statsRaw : (statsRaw?.data ?? {});
const topEvents = Array.isArray(statsBody.top_events) ? statsBody.top_events : [];

check('资讯列表条数与 API 首页一致', domItems.length === apiArticles.length && domItems.length > 0,
  `DOM ${domItems.length} / API ${apiArticles.length}（第 ${Math.min(countTrials, 3) + 1} 轮读取）`);

// —— ④ 占位摘要滤除：逐条与 API 对账（防真空通过） ——
const PH = /^(?:\[[^\]]{1,20}\]\s*)?(?:点击标题查看详细内容|点击查看详细内容|查看全文|点击查看全文|阅读全文|阅读更多)$/;
const isPlaceholder = (s, t) => {
  const x = (s || '').trim();
  if (x === '') return true;
  if (t != null && x === (t || '').trim()) return true;
  return PH.test(x);
};
const norm = (s) => (s || '').replace(/\s+/g, '');
const mismatches = [];
let apiPlaceholder = 0;
let expectedShown = 0;
const titleCount = new Map();
for (const a of apiArticles) titleCount.set((a.title || '').trim(), (titleCount.get((a.title || '').trim()) || 0) + 1);
for (const a of apiArticles) {
  const t = (a.title || '').trim();
  const show = !isPlaceholder(a.summary, a.title);
  if (show) expectedShown++; else apiPlaceholder++;
  const dom = domItems.find((d) => d.title === t);
  if (!dom) { mismatches.push(`DOM 缺「${t.slice(0, 16)}」`); continue; }
  if (show !== (dom.summary != null)) {
    mismatches.push(`「${t.slice(0, 16)}…」应${show ? '显' : '隐'}摘要，实${dom.summary != null ? '显' : '隐'}`);
    continue;
  }
  if (show && dom.summary != null && titleCount.get(t) === 1) {
    const domBody = norm(dom.summary.endsWith('…') ? dom.summary.slice(0, -1) : dom.summary);
    if (!norm(a.summary).startsWith(domBody)) mismatches.push(`「${t.slice(0, 16)}…」摘要内容不符`);
  }
}
const domWithSummary = domItems.filter((d) => d.summary != null).length;
check('逐条摘要显隐与 API 判据一致（含内容前缀，防真空通过）',
  mismatches.length === 0 && domItems.length > 0,
  mismatches.slice(0, 3).join('；') || `样本 ${domItems.length} 条，其中占位摘要 ${apiPlaceholder} 条`);
check('摘要行数与 API 期望一致', domWithSummary === expectedShown,
  `DOM ${domWithSummary} / 期望 ${expectedShown}`);
check('全页无占位摘要漏网', domItems.every((d) => d.summary == null || !PH.test(d.summary)));

// —— ⑤ 右栏自动开第一篇（此前未点过任何列表项） ——
// 不跟「列表首条」比（10s 轮询可能刚好插进新文章导致假红）；比同一瞬间的右栏标题与
// 选中项（.news-article-active）标题——这正是自动打开的稳定契约。
await page.locator('.news-right-panel h4').first().waitFor({ timeout: 15000 }).catch(() => {});
const panelState = await page.evaluate(() => ({
  detailTitle: (document.querySelector('.news-right-panel h4')?.textContent || '').trim(),
  activeTitle: (document.querySelector('.news-article-active .news-article-title')?.textContent || '').trim(),
  emptyState: !!Array.from(document.querySelectorAll('.news-right-panel'))
    .some((el) => (el.textContent || '').includes('选择文章查看正文')),
}));
const listTitles = new Set(domItems.map((d) => d.title));
check('右栏自动展示第一篇正文（标题=当前选中列表项、零点击）',
  panelState.detailTitle.length > 0 && panelState.detailTitle === panelState.activeTitle
    && listTitles.has(panelState.detailTitle),
  `右栏「${panelState.detailTitle.slice(0, 24)}」 / 选中「${panelState.activeTitle.slice(0, 24)}」`);
check('右栏不再是「选择文章查看正文」空态', !panelState.emptyState);
// 右栏正文区按实现同语义验收（2026-10-15 复盘：财联社源 content_html 与 content 同为占位串，
// 两条渲染路都要滤）。不能拿「占位字面量出现在面板文本里」当判据——真正文里也可能带这类字眼；
// 与实现同分支：content_html 剥标签→非占位则显示它；否则 content||summary→非占位则显示它；
// 都占位则必须是提示句。再验 UI 实际文本与判定一致（防真空）。
const htmlToPlain = (h) => (h || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
const activeArticle = apiArticles.find((a) => (a.title || '').trim() === panelState.activeTitle);
let paneBodyCheck = { pass: false, detail: '未定位到当前选中文章（API 无此标题）' };
if (activeArticle?.id != null) {
  const detRaw = await apiJson(`/news/articles/${activeArticle.id}`);
  const det = detRaw?.data ?? detRaw ?? {};
  const htmlTxt = htmlToPlain(String(det.content_html || ''));
  const bodyTxt = String(det.content || det.summary || '').trim();
  const shown = htmlTxt !== '' && !isPlaceholder(htmlTxt, det.title) ? htmlTxt
    : (!isPlaceholder(bodyTxt, det.title) ? bodyTxt : null);
  const paneText = await page.evaluate(() =>
    (document.querySelector('.news-right-panel')?.textContent || '').replace(/\s+/g, ' ').trim());
  const corpus = norm(paneText);
  if (shown == null) {
    const expectHint = htmlTxt !== '' || bodyTxt !== '';
    const expect = expectHint ? '该来源未提供正文' : '(正文为空)';
    paneBodyCheck = {
      pass: corpus.includes(expect),
      detail: corpus.includes(expect)
        ? `witness：正文位源侧为占位（content_html=${JSON.stringify(htmlTxt.slice(0, 24))}），已替换为提示`
        : '占位正文未替换，面板仍显示源侧占位',
    };
  } else {
    const head = norm(shown).slice(0, 24);
    paneBodyCheck = {
      pass: head.length > 0 && corpus.includes(head),
      detail: head.length > 0 && corpus.includes(head)
        ? `witness：正文直出「${shown.slice(0, 30)}…」`
        : `正文未渲染（期望头部「${shown.slice(0, 30)}」）`,
    };
  }
}
check('右栏正文区与详情 API 一致（占位→提示 / 真正文→直出）', paneBodyCheck.pass, paneBodyCheck.detail);

// —— ③ 分类行折叠（放在最后：唯一会点击的断言，不污染前两条的「未交互」前提） ——
const QUICK_LABELS = ['政策', '宏观', '产业', '财报', '市场', '监管', '地缘', '加密', '期货'];
const hotApi = topEvents.filter((e) => !QUICK_LABELS.includes(e.name)).length;
const chipCount = () => page.locator('.news-category-row .ant-tag').count();
const moreBtn = page.locator('.news-category-more');
const chipsBefore = await chipCount();
const moreExists = (await moreBtn.count()) > 0;
if (topEvents.length === 0) {
  // stats 挂掉/无数据时 hotApi=0，「热词 ≤ 4 → 无按钮」会假绿——显式判不通过（2026-10-15 评审）
  check('分类行折叠可验收（stats 有数据）', false, '「/news/enrichment/stats」无 top_events，折叠断言无从对账');
} else if (hotApi > 4) {
  check('热词 > 4：出现「更多分类」按钮', moreExists, `API 热词 ${hotApi} 个`);
  if (moreExists) {
    const label = await text(moreBtn);
    check(`按钮标注隐藏数 = 热词总数 − 4`, label.includes(`更多分类(${hotApi - 4})`), label);
    check('折叠态 chip 数 = 快捷 10 + 前 4', chipsBefore === 14, `实得 ${chipsBefore}`);
    await moreBtn.click();
    await page.waitForTimeout(500);
    const chipsAfter = await chipCount();
    check('展开后 chip 数 = 快捷 10 + 全部热词', chipsAfter === 10 + hotApi, `实得 ${chipsAfter}，期望 ${10 + hotApi}`);
    await page.locator('.news-category-more').click();
    await page.waitForTimeout(500);
    const chipsBack = await chipCount();
    check('收起后回到折叠态', chipsBack === chipsBefore, `${chipsBack} vs ${chipsBefore}`);
  }
} else {
  check('热词 ≤ 4：不出现「更多分类」按钮（无隐藏项）', !moreExists, `API 热词 ${hotApi} 个`);
}
await page.screenshot({ path: '/tmp/qm_rss_desktop_20261015.png' });

// ═══════════════════════════════════════════════════════════════════════════
// ⑥ 全程零 pageerror
// ═══════════════════════════════════════════════════════════════════════════
check('全程无 pageerror', errs.length === 0, errs.slice(0, 3).join(' ; '));

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通过`);
console.log('截图：/tmp/qm_chat_digest_20261015.png（模型对话）、/tmp/qm_rss_desktop_20261015.png（RSS）');
await browser.close();
process.exit(failed.length ? 1 : 0);
