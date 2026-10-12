/**
 * 挖掘记忆三面验收探针（T-FM-04/07 + T-FE-09/10 前端落点，2026-10-12）：
 *  1) 因子池「变体台账」tab：判定口径卡 + KPI tiles + 表头（数据面 200 由
 *     response 捕获钉死；空/满态二选一都算通过，读取失败必红）；
 *  2) 设置「挖掘方向」tab：经验记忆（拒因聚类/饱和方向）+ 方向配额（三档预算）
 *     两个面板渲染且无「读取失败/出错」；
 *  3) 因子挖掘首页「进入实时演化台」→ 演化台「决策轨迹」toggle → TrajectoryView
 *     落到终态（轮卡或空态，绝不卡在加载）；
 *  4) 挖掘历史行内「查看轨迹」→ 抽屉「任务决策轨迹」内同样落终态。
 *
 * 依赖引擎侧新路由（variant-ledger / experience-memory / direction-quota /
 * tasks/{id}/trajectory）——engine 子进程不热载，跑本探针前须确认已
 * respawn（见 memory：oss-backend-hot-reload）。
 *
 * 用法：node electron/tests/probe_mining_memory_surfaces.mjs
 */
import { chromium } from 'playwright';

const BASE = process.env.QM_BASE || 'http://localhost:3080';
let pass = 0;
let fail = 0;
const ok = (cond, label, extra = '') => {
  if (cond) {
    pass++;
    console.log(`  ✓ ${label}${extra ? ` — ${extra}` : ''}`);
  } else {
    fail++;
    console.log(`  ✗ ${label}${extra ? ` — ${extra}` : ''}`);
  }
};

const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome-stable',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1720, height: 1150 } });
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 200)));

// 四个新端点的真实响应（前端发的请求，不是探针自己 curl）
const apiHits = [];
page.on('response', (r) => {
  const u = r.url();
  if (
    /\/api\/v1\/alpha-agent\/(variant-ledger|experience-memory|direction-quota)/.test(u) ||
    /\/api\/v1\/alpha-agent\/tasks\/[^/]+\/trajectory/.test(u)
  ) {
    apiHits.push({ url: u.replace(BASE, '').slice(0, 110), status: r.status() });
  }
});
const hit200 = (re) => apiHits.some((h) => re.test(h.url) && h.status === 200);
const anyHit = (re) => apiHits.filter((h) => re.test(h.url));

const waitForAny = async (locators, timeout = 15000) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const l of locators) {
      if ((await l.count()) > 0 && (await l.first().isVisible().catch(() => false))) return l;
    }
    await page.waitForTimeout(400);
  }
  return null;
};
const visible = async (loc) => (await loc.count()) > 0 && (await loc.first().isVisible().catch(() => false));

// ── 登录（本地开发实例）──
await page.goto(`${BASE}/#/auth/login`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await page.waitForTimeout(3500);
if ((await page.locator('input[type=password]').count()) > 0) {
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input[type=password]').first().fill('admin123');
  const btns = page.locator('button');
  for (let i = 0; i < (await btns.count()); i++) {
    const t = (await btns.nth(i).innerText().catch(() => '')).replace(/\s/g, '');
    if (/登录/.test(t)) {
      await btns.nth(i).click();
      break;
    }
  }
  await page.waitForTimeout(6000);
}
const dismissLater = async () => {
  const btn = page.locator('.ant-modal-wrap button:has-text("稍后再答")');
  if (await btn.count()) {
    await btn.first().click();
    await page.waitForTimeout(600);
  }
};
await dismissLater();

// ════════════════ 1) 因子池 → 变体台账 ════════════════
console.log('\n[1] 因子池「变体台账」');
await page.goto(`${BASE}/#/alpha-research`, { waitUntil: 'domcontentloaded', timeout: 40000 });
await page.waitForTimeout(5000);
await dismissLater();
await page.locator('text=因子池').first().click();
await page.waitForTimeout(2500);
const ledgerTab = page.locator('button:has-text("变体台账")').first();
ok(await ledgerTab.count(), '存在「变体台账」tab');
await ledgerTab.click();

// 落终态：判定口径卡（成功渲染）或读取失败卡
const ledgerTerminal = await waitForAny(
  [
    page.locator('text=判定口径（非对称否决：宁可漏判，不误杀）'),
    page.locator('text=变体台账读取失败'),
  ],
  15000,
);
ok(
  await visible(page.locator('text=判定口径（非对称否决：宁可漏判，不误杀）')),
  '判定口径卡渲染（非读取失败态）',
  ledgerTerminal ? '' : '超时未落任何终态',
);
ok(
  (await page.locator('text=已观测变体').count()) > 0 &&
    (await page.locator('text=台账更新').count()) > 0,
  'KPI tiles 渲染（已观测变体 / 台账更新）',
);
ok(
  (await page.locator('th:has-text("残差 ICIR")').count()) > 0 &&
    (await page.locator('th:has-text("冷却至")').count()) > 0,
  '表头渲染（残差 ICIR / 冷却至）',
);
const ledgerRows = await page.locator('table tbody tr').count();
const ledgerBanner = await page.locator('text=台账尚无记录——跑过一轮统计派挖掘后').count();
ok(hit200(/variant-ledger/), 'GET /alpha-agent/variant-ledger 200', JSON.stringify(anyHit(/variant-ledger/)));
console.log(`    [记录] 变体骨架行数=${ledgerRows}，'尚无记录'横幅=${ledgerBanner > 0 ? '有' : '无'}`);

// ════════════════ 2) 设置 → 挖掘方向 ════════════════
console.log('\n[2] 设置「挖掘方向」');
await page.locator('span:text-is("设置")').first().click();
await page.waitForTimeout(2500);
const dirTab = page.locator('button:has-text("挖掘方向")').first();
ok(await dirTab.count(), '存在「挖掘方向」tab');
await dirTab.click();
await page.waitForTimeout(1500);

const quotaPanel = await waitForAny(
  [page.locator('text=方向配额（探索 / 利用 / 变异）'), page.locator('text=正在读取方向配额预演…')],
  20000,
);
ok(await visible(page.locator('text=方向配额（探索 / 利用 / 变异）')), '方向配额面板渲染');
ok(await visible(page.locator('text=经验记忆 / 禁区（历史拒因与饱和方向）')), '经验记忆/禁区面板渲染');
ok(await visible(page.locator('text=三档预算')), '配额「三档预算」块渲染（available=true 路径）');
ok(
  await visible(page.locator('text=拒因聚类（归一原因 × 次数）')),
  '「拒因聚类」块渲染',
);
ok(
  await visible(page.locator('text=饱和方向（值级查重命中聚集）')),
  '「饱和方向」块渲染',
);
const bucketRow = await page.locator('text=值级重复（库内已有高度相关列）').count();
ok(bucketRow > 0, '真实拒因桶数据上屏（值级重复 ≈ 统计派报告归因）');
ok(
  (await page.locator('text=读取出错').count()) === 0 &&
    (await page.locator('text=经验记忆读取失败').count()) === 0,
  '两面板无「读取失败/出错」态',
);
ok(hit200(/experience-memory/), 'GET /alpha-agent/experience-memory 200', JSON.stringify(anyHit(/experience-memory/)));
ok(hit200(/direction-quota/), 'GET /alpha-agent/direction-quota 200', JSON.stringify(anyHit(/direction-quota/)));
void quotaPanel;

// ════════════════ 3) 首页 → 演化台入口门控 ════════════════
// TaskContext 只接管 running/queued（本次会话在跑/排队）的任务；历史终态走
// 「挖掘历史」。本实例当前无 running/queued 任务 → 首页入口按设计「滚动到
// 输入框、不进空演化台」（HomePage.handleOpenDashboard gate）。
// 「决策轨迹」toggle 本体由组件测试 MiningDashboardPage.switcher 覆盖；
// TrajectoryView 真实数据渲染在 [4] 抽屉（同一组件同一端点）做 E2E。
console.log('\n[3] 演化台入口门控（无在跑任务）');
await page.locator('span:text-is("因子挖掘")').first().click();
await page.waitForTimeout(2000);
const dashToggle = page.locator('button:text-is("决策轨迹")');
ok((await dashToggle.count()) === 0, '前提成立：当前无在场任务（决策轨迹 toggle 不在场）');
await page.locator('text=进入实时演化台').first().click();
await page.waitForTimeout(2000);
ok(
  (await dashToggle.count()) === 0 &&
    (await page.locator('#mining-input').count()) > 0,
  '无在场任务时入口不进空演化台（留在首页输入区）',
);

// ════════════════ 4) 挖掘历史 → 查看轨迹抽屉 ════════════════
console.log('\n[4] 挖掘历史「查看轨迹」抽屉');
await page.locator('span:text-is("挖掘历史")').first().click();
await page.waitForTimeout(2500);
const trajBtn = page.locator('button:has-text("查看轨迹")').first();
ok((await trajBtn.count()) > 0, '历史行内存在「查看轨迹」按钮');
await trajBtn.click();
await page.waitForTimeout(1000);
const drawer = page.locator('[role=dialog][aria-label="任务决策轨迹"]');
ok(await visible(drawer), '抽屉「任务决策轨迹」打开');
ok(await visible(drawer.locator('h3:has-text("任务决策轨迹")')), '抽屉标题渲染');
ok((await drawer.locator('text=/^任务 [0-9a-f]{8}$/').count()) > 0, '抽屉展示任务短 ID');
const drawerTerminal = await waitForAny(
  [
    drawer.locator('text=首轮').first(),
    drawer.locator('text=没有扫到决策轨迹记录（日志目录为空）。'),
    drawer.locator('text=任务尚未结束——以下为当前已落盘快照'),
    drawer.locator('text=某轮载荷解析失败'),
  ],
  20000,
);
ok(drawerTerminal != null, '抽屉内 TrajectoryView 落终态');
const drawerLoops = await drawer.locator('text=首轮').count();
const drawerPhase = await drawer.locator('text=总结反馈').count();
ok(hit200(/tasks\/[^/]+\/trajectory/), 'GET /tasks/{id}/trajectory 200', JSON.stringify(anyHit(/tasks\/[^/]+\/trajectory/).slice(-2)));
console.log(`    [记录] 抽屉终态=${drawerLoops > 0 ? '轮卡' : '空态/快照'}，五环节节点卡=${drawerPhase > 0 ? '有' : '无'}`);
ok(
  (await drawer.locator('text=加载失败').count()) === 0,
  '抽屉内无加载失败',
);
await page.locator('[aria-label="关闭轨迹"]').first().click();
await page.waitForTimeout(800);
ok(!(await visible(drawer)), '抽屉可关闭');

ok(pageErrors.length === 0, '无页面 JS 错误', pageErrors.join(' | ').slice(0, 200));

await browser.close();
console.log(`\nRESULT ${fail === 0 ? 'ALL PASS' : `FAILURES: ${fail}`} (pass=${pass} fail=${fail})`);
process.exit(fail === 0 ? 0 : 1);
