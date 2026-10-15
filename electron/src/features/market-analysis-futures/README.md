# 期货市场分析（Futures Market Analysis）

期货市场分析前端模块 —— 「大盘分析」页顶部市场切换器选「期货」后渲染。
后端：`backend/services/api/market_analysis_futures/`（`/api/v1/market-analysis-futures`）。

## 结构

```
market-analysis-futures/
├── pages/MarketAnalysisFuturesPage.tsx   # 入口：Banner + 统计卡 + 6 Tab（App.tsx 懒加载）
├── components/
│   ├── FuturesStatCards.tsx        # 三层市场统计卡（全市场/国内/国际/上金所）
│   ├── FuturesOverviewPanel.tsx    # Tab1 行情总览：涨跌双榜 + 主力榜单 + 板块温度条
│   ├── FuturesHeatmapPanel.tsx     # Tab2 板块热力：品种 treemap + 9 板块统计
│   ├── FuturesTermPanel.tsx        # Tab3 期限结构：近远月曲线 + 期限结构榜
│   ├── FuturesPositionsPanel.tsx   # Tab4 持仓分析：CFTC 双榜 + 增减仓四象限
│   ├── FuturesInventoryPanel.tsx   # Tab5 仓单库存：注册仓单榜 + 单品种历史
│   └── FuturesSpreadPanel.tsx      # Tab6 内外盘基差：跨市场价差 9 组 + 贵金属期现
├── services/api.ts                 # 12 个只读端点 + POST /refresh
├── types.ts                        # 与后端 JSON 形状一一对应
└── format.tsx                      # 本地格式化（「—」口径）+ 结构/象限徽标
```

## 口径纪律（改动必读）

1. **后端金额字段已预换算**：`*_yi`=亿、`*_wan`=万手，前端只展示不再缩放。
   注意共享层 `fmtYi` 的输入是「元」，**不可**对 `*_yi` 字段使用（会再除 1e8）。
2. **缺失一律 null → 渲染「—」**：国际品种与上金所无量仓数据，缺数据显示 0 是缺陷。
3. **主力连续含换月跳空**：单日涨跌幅可能混入移仓缺口，榜单/统计卡均有标注。
4. **名义额 = 价 × 量仓 × 合约乘数**：不是真实资金流水，tooltip 均已注明。
5. **板块统计（热力 Tab 右栏）只含国内品种**（国际量仓口径不可比，不聚合）；
   热力图面积只覆盖有量仓数据的品种，未入图数量如实标注。
6. **四象限为教材口径**：涨+增仓=多头增仓 / 跌+增仓=空头增仓 /
   涨+减仓=空头回补 / 跌+减仓=多头离场（与后端 `positions._quadrant` 同源）。
7. **CFTC 为周度数据**（每周六发布上一周二数据），报告期另行标注，勿与日频混读。

## 交互

- 热力图点击品种色块 → 跳「期限结构」Tab 并载入该品种曲线。
- 期限结构榜行点击 → 切换曲线品种；选择器按期限榜（有合约数据的品种）列出。
- Banner「刷新分析」= POST /refresh（清后端缓存重算）+ 递增 `reloadKey` 令各 Tab 重取。
- 各 Tab 面板首次挂载自取数据；切 Tab 会卸载重取（与港股模块同构）。

## 展示面用词

展示面只说「位置/方向」：「Back 结构 / Contango」为期限结构术语，页面同时给出
「近高远低 / 近低远高」白话注解；不存在股票语境下「买入/卖出」执行词。
