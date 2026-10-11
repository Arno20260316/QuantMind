# 市场分析模块 (Market Analysis)

## 模块说明
市场分析功能板块，提供全市场大盘全景、多周期资金流向、板块/个股资金链、申万一级行业热力矩形图谱等可视化与交互分析。

## 核心组件与页面
- `pages/MarketAnalysisPage.tsx`: 市场分析主页面组件，包含顶部 Banner 顶栏、核心指数快照、功能 Tab 导航以及资金流向下钻面板。
- `components/ShenwanHeatmapChart.tsx`: 申万一级分类矩形树图 (Treemap) 热力图组件。
- `components/MarketBreadthCard.tsx`: 市场情绪温度计与赚钱效应卡片。
- `components/StockMoneyFlowTable.tsx`: 个股资金流向排行榜表格。
- `components/FlowPanoramaPanel.tsx`: 资金流全景面板（细案 v2 状态中枢）：工具栏 + 证据 meta 披露 + 标签融合条 + 桑基 v2 + 板块⇄个股双向工作台 + 延续证据台。
- `components/FlowSankeyV2.tsx`: 四层桑基（主体 → 单型 → 板块 → 个股），点节点联动下方工作台。
- `components/SectorFlowWorkbench.tsx`: 板块榜 ⇄ 板块内个股榜双向工作台。
- `components/StockFlowDetailCard.tsx`: 个股资金流详情卡（20 日五档序列 / 板块归因回跳 / 因子板共振态）。
- `components/FlowEvidencePanel.tsx`: 延续证据台（全史条件统计命中榜 + 信号档案）。
- `components/TagBrowserStrip.tsx`: 板块标签融合条（统一搜索：板块 / 标签 / 股票代码；热门板块 chips）。
- `components/EvidenceBadge.tsx`: 延续证据徽章（连N + 条件超额悬停）。
- `components/FactorBoardPanel.tsx`: 因子概率板面板（合成分 × 模型分位象限）。

## 数据服务与词汇表
- `services/moneyFlowService.ts`: 资金流全景/板块/个股/标签/证据台 API 客户端与类型（`FlowPanoramaResponse`、`FlowStockFactor` 等契约字段）。
- `services/factorBoardService.ts`: 因子概率板 API 客户端（`REQUIRED_TOP_KEYS` 与后端金样共读）。
- `factorBoardVocabulary.ts`: 判定词/衰减/制度注记/个股象限（`quadChipCls` / `quadrantMeta`）唯一展示映射，禁用买卖指令词。

## 最近优化
- 规范展示文案，将“申万一级 31 个行业”统一简化命名为“申万一级分类”。
- 采用容器原生 CSS `mask-image` 线性渐隐方案，滚动时在顶底边缘产生自然平滑渐隐，保持精致视觉过渡与流畅交互。
- 资金流全景 v2（2026-10-11）：四层桑基 + 板块⇄个股双向工作台 + 个股卡归因回跳；「标签双向查询」并入统一搜索条（TagBrowserStrip）；个股卡新增「因子板共振态」（合成分 × 模型分位象限，陈旧显形不冒充当日）。个股层不设资金流预测徽章——实测否决（IC≤0.01），页内披露口径。
