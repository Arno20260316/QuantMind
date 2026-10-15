# 期货市场分析模块 (Market Analysis Futures)

## 模块说明

期货多维市场分析：**行情总览、板块热力、期限结构、持仓分析、仓单库存、内外盘与期现价差**。
与「港股市场分析」「美股市场分析」平级，同属**独立市场目录 + 跨市场共享层**架构。

数据全部来自本地 QuantFutures parquet（`QM_QUANTFUTURES_DATA_DIR`，默认 `data/quantfutures/`），
**无外部实时行情依赖**。入口：顶部市场切换器选「期货」→ 侧边栏「市场分析」。

## 品种宇宙与 symbol 三层命名（改代码前必读）

统一出处 `backend/shared/futures_universe.py`（122 个品种，元数据含中文名/板块/交易所/合约乘数）：

| 层 | 命名 | 数量 | 量仓数据 |
|----|------|------|---------|
| 国内主力连续 | `RB0.CN` / `AU0.CN` | 84 | ✅ 成交量、持仓量（2026-10-15 起 position/settlement 落盘） |
| 国际 | `CL.FUT` / `GC.FUT` | 30 | ❌ 源不提供（futures_foreign_hist 的 volume/position/settlement 全 0） |
| 上金所现货 | `Au99.99` / `Ag(T+D)` | 8 | ❌ spot_hist_sge 仅 OHLC |

**缺数据一律 NULL → 前端显示 `—`，绝不落 0**（0 会被误读为「持仓归零」）。
9 大板块：贵金属/有色金属/黑色建材/能源/化工/农产品/金融与指数/新能源/其他。

## 核心口径（页面必须如实呈现）

| 口径 | 事实 | 对模块的影响 |
|------|------|-------------|
| **主力连续含换月跳空** | `RB0.CN` 在移仓日从旧主力跳到新主力，价格缺口计入当日涨跌幅 | 榜单涨跌幅可能出现 ±10% 级「假涨跌」（实测：BU0.CN 换月日 -11.91%）。`/status` notes 与页面标注 |
| **成交额/持仓额为名义口径** | `parquet.amount = close × volume` **无量纲意义**（未乘合约乘数），不可当成交额 | 统一现算 `close × volume × multiplier`（元），称「名义成交额/持仓额」 |
| **完整交易日** | 当日凌晨 03:00 同步先落国际夜盘品种，当天分区午后才是全量 | 截面/榜单以「国内品种 ≥ 60 只」的分区为准（`_full_trade_days`），否则会把 2 个国际品种当全市场 |
| **持仓额跨品种可比** | 手数不可跨品种比较（AU 一手 1000 克 vs RB 一手 10 吨） | 增减仓榜按 `|Δ持仓 × 价 × 乘数|`（元）排序 |
| **CFTC 周度** | 每周六发布上一周二数据；`c`=非商业（投机）、`merchant_goods`=商业（套保） | 面板标注报告日；净仓位 = 多头 − 空头（张） |
| **仓单覆盖所不定** | 上期所接口失效时最新日期可能仅 CZCE | 以最新日期实际收录的交易所为准渲染，缺所显示缺失而非 0 |
| **内外盘税制** | 黄金为投资净价直接可比（投资金免税）；白银与有色按进口到岸口径加 13% 增值税（沪银盘面含税，免税只覆盖黄金）；SC 含消费税未调 | 每组价差自带 `formula`/`tax` 说明字段，前端原样展示 |
| **国际品种源侧单位各自为政** | GC/SI 美元/盎司、CL 美元/桶、LME 系美元/吨、**HG 美分/磅**（新浪源侧惯例） | `feed/spread.py` 的 `_CROSS_PAIRS` 是唯一口径注册表，新增消费方先查它 |

## 数据链修复记录（2026-10-15）

| 问题 | 根因 | 处置 |
|------|------|------|
| `daily_forward` 持仓量/结算价从未落盘 | `akshare_futures_sync._normalise_daily` 未保留 position/settlement | 修复列保留 + `quantfutures_backfill_position.py` 回填 7771 分区（position 非空 69.3%） |
| `contracts_daily` 持仓量/结算价从未落盘 | `akshare_futures_extra._norm_contract_kline` colmap 缺 akshare 英文列 `hold`/`settle` 映射 | 补映射 + 全量重跑（84 品种 × 15 个月合约） |
| CFTC 停更（止于 2026-09-01）、FX 停更（止于 2026-09-10）、仓单停更（止于 2026-09-10） | `task_cftc`/`task_fx`/`task_receipts` 等额外任务**不在任何调度里**，只有手动跑 | 已手动重跑（仓单 24 日档/0 空，至 2026-10-15）；**P1：接入调度常态化**（见下） |
| 铜内外盘价差恒 ≈ −99%（截图复核发现） | `HG.FUT` 源侧（新浪）按**美分/磅**落盘（close≈661），`cu_hg` 按美元/磅换算放大 100× | `_cent_lb_to_cny_t` ÷100 修正 + 回归测试 + `\|spread_pct\|<50` 健康带断言；修复后 +0.26% |
| 白银「价差」+18.5% 实为税制差 | 沪银盘面含 13% 增值税（免税只覆盖黄金；黄金对 GC 平价 ±0.7% 即反证），pair 曾自标「投资净价直接可比」 | 改到岸含税口径（×1.13）+ 回归测试；修复后 +4.89% 为对进口平价真实溢价 |

## 目录结构

```
backend/services/api/market_analysis_futures/   # 后端：独立市场目录
├── __init__.py
├── router.py            # 14 个端点（REST + SSE 刷新），只做转发
├── feed/                # 数据层按域拆分（每个 Tab ↔ 一个模块）
│   ├── base.py          # 基座：显式分区读取 / 完整交易日 / 截面 / 分合约 / 仓单 / CFTC / FX
│   ├── overview.py      # Tab1 统计卡 + 四类榜单
│   ├── sectors.py       # Tab2 品种热力图 + 板块聚合
│   ├── term.py          # Tab3 期限结构（单品种曲线 + 全市场 contango/backwardation 榜）
│   ├── positions.py     # Tab4 CFTC 双榜 + 增减仓四象限
│   ├── inventory.py     # Tab5 交易所仓单
│   └── spread.py        # Tab6 内外盘价差（9 组）+ 贵金属期现基差
└── README.md

electron/src/features/market-analysis-futures/  # 前端：独立 feature 目录
├── pages/MarketAnalysisFuturesPage.tsx         # 主页面（6 Tab）
├── components/Futures*.tsx                     # 各面板
├── services/api.ts                             # /api/v1/market-analysis-futures 封装
└── types.ts                                    # 响应类型（与后端字段严格对齐）

backend/shared/futures_universe.py              # 品种宇宙唯一出处（名称/板块/乘数/单位）
backend/services/api/market_analysis_shared/    # 跨市场共享层（caching/market_days/display）
```

## 数据源要点

- **`1_kline_data/daily_forward`**：主力连续日K（三层 symbol），本模块截面/榜单主数据源。
- **`2_base_sector/contracts_daily`**：分合约日K（每个合约一个文件，存其存续期全量数据）——
  期限结构唯一来源。曲线取每个合约**最后一根K线**，并用 7 自然日新鲜度闸剔除
  已到期合约（其留下的「最后一根」在到期日）。持仓量缺失时主力判定退回成交量并
  以 `main_by="volume"` 如实标注。
- **`2_base_sector/warehouse_receipts`**：按 dt 分区的交易所仓单（CZCE/SHFE/DCE/GFEX）。
- **`2_base_sector/cftc`**：宽表（日期 + 12 品种 × 多头/空头/净仓位），周度。
- **`2_base_sector/fx_daily`**：中行牌价（quote_unit=100 外币，rate = mid/100）；
  内外盘价差按**逐日汇率**折算（ffill，不外推），缺失日跳过该点。
- **DuckDB 陷阱**：文件内部自带 `dt` 列会遮蔽 hive 分区列，按日期取数一律走
  显式分区文件列表（`base._read_partitioned`）；回填过渡期新旧分区列集不一，
  必须 `union_by_name=true`。

## API 端点（前缀 `/api/v1/market-analysis-futures`）

| 分组 | 端点 |
|------|------|
| 诊断 | `GET /status` |
| Tab1 行情总览 | `/overview`、`/ranking?kind=change\|turnover\|oi_amount\|oi_change&sector=&limit=` |
| Tab2 板块热力 | `/heatmap`、`/sector-stats` |
| Tab3 期限结构 | `/term?symbol=AU0`、`/term-ranking` |
| Tab4 持仓分析 | `/cftc`、`/oi-changes?days=1&limit=` |
| Tab5 仓单库存 | `/receipts?variety=CU&days=` |
| Tab6 内外盘价差 | `/cross-market?days=`、`/precious-basis?days=` |
| 刷新 | `POST /refresh`、`POST /refresh/stream`（SSE，11 步） |

端点模式与港美股一致：`Depends(get_current_user)` + `asyncio.to_thread(feed.xxx)` + `except → 500`。

## P1 待办（数据面常态化）

`akshare_futures_extra.py` 的 `contracts_daily` / `cftc` / `fx` / `receipts` / `member_positions`
目前**不在 `scheduler_registry` 调度内**，只有手动执行。CFTC 停更两周（止于 09-01）、
FX 与仓单停更（止于 09-10）即由此而来——停更无声，靠页面日期标注才会暴露。
需按市场同步的既有惯例接入调度（或并入 03:00 夜链），并在前端「数据管理」暴露状态。

上游失效已知项：SHFE/DCE/GFEX 仓单接口（akshare 1.18.88）实测全部
`JSONDecodeError`（源站非 JSON 响应），仓单覆盖所因此仅 CZCE——页面按
「以最新日期实际收录的交易所为准」如实渲染，勿当数据缺失补 0。

## 测试

```bash
# 在容器内（后端依赖在容器里）
docker exec -w /app/backend quantmind python -m pytest tests/test_quantfutures_market_analysis.py -q --no-cov
```

`backend/tests/test_quantfutures_market_analysis.py`：**零 mock** 直连本地 parquet，
断言取向为量级与健康度（防停更 / 防口径失真 / 防 0 冒充缺失），而非精确数值。

## 相关

- 细案：`docs/期货市场分析_分层对接与前端专业化_细案_20261015.md`
- 数据平台：`backend/services/engine/data_platform/quantfutures_hub.py`
- 同步：`backend/scripts/akshare_futures_sync.py`（日频）、`backend/scripts/akshare_futures_extra.py`（合约/CFTC/FX/仓单）
- 港股对标：`backend/services/api/market_analysis_hk/`；美股对标：`backend/services/api/market_analysis_us/`
