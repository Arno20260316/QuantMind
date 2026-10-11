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
- 资金流全景 v3（2026-10-11）：延续证据台重排——命中榜改左侧竖向列表 + 「连N」档位标签（≥2/≥3/≥5 可切换、带各档计数），信号档案居右；底部「否决清单｜口径与披露」两列并列，披露收敛为一行（truncate + 悬停全文）。看板「主力资金净流入 Top 50」列表内滚动、表头吸顶。成分贡献榜新增近 5 日逐日涨跌幅列（统一日期组，缺价显 —）。进入资金流全景默认选中排名第一板块（延续状态优先），进入即有明细。
- 资金流全景 v4（2026-10-11，用户反馈四项）：①「板块榜 · 延续状态优先」与「延续命中榜」合并同框（工作台左栏同一卡片，榜内先按延续状态再按净额）；②桑基板块→个股层改**双侧排名**：direction=all 时每板块同时给出流入前 6 与流出最深 4（K=10；其余档位按 6:4 折算，短板块自动收敛去重），inflow/outflow 仍为单向 TopK——「到个股的流入/流出排名都要」；③非「行业二级」体系（行业一级/概念）显式披露「连N 证据仅按行业二级测量」（证据台横幅 + 板块榜标题/提示动态化，不伪造 streak）；④同名板块二次点击重取明细（detailTick 计数，明细卡闪光反馈）；另修个股卡 20 日 tooltip 双重缩放（后端序列已折算「亿」，新增 `fmtSeriesYi` 专收亿元值与 `fmtYi`〔收元〕严格分工，见 `__tests__/moneyFlowFormat.test.ts` 回归）。
- 资金流全景 v5（2026-10-12，用户反馈两项）：①「板块榜」并入「延续命中榜」为一窗两签（同卡 tab 切换、两签常挂载 hidden 互斥显隐，切签不重取证据；默认签契约 = 证据可用〔`meta.evidence.available=true`，正常态 `reason=null`〕? 命中榜 : 板块榜——后端从不下发 `'ok'`，初版误比 `reason==='ok'` 致默认签恒落板块榜，探针新增契约断言兜底）；板块明细（SectorDetailPanel）从底部全宽移到右侧列，与榜单窗形成左右栏（左卡 440px + 右列明细/个股卡自适应）；②证据台底部压缩为 2~3 行：「否决清单」= 标题 + 8 项 chips（悬停 title 见统计与结论）+「展开审计详情」按需展开 8 行逐条明细，「口径与披露」收敛一行（truncate + 悬停全文），释放纵向空间；榜单行改两行制（首行 名 + 连N 徽章 + 净额，次行 净额条 + 比/成分/涨幅），窄栏下板块名不再截断。
- 资金流全景 v6（2026-10-12，用户反馈两项）：①热门区左端新增**三体系选择器**（行业二级/行业一级/概念 tabs，与工具栏「体系」组同源同步，点 tab 即切页面体系）＋每体系 top-18 热门 chips 可直接点选（后端 `/tags/stats?hot_type=` **单体系过滤**——全局混合 top-N 会被成分数大的体系挤空，行业一级在全局 top-100 里为 0 条；每体系进程内缓存回切不重取，失败只隐去热门区给重试）；统一搜索改**两路并行**（`/tags/matches` 板块候选 + `/stock-terminal/list` 个股联想〔名称/代码/拼音〕，错误态独立互不拦、渲染按路就绪不互相门控——初版统一 pending 门控被探针抓到时序缺陷），文字搜股（「茅台」→ 贵州茅台）与代码形态短路并存；②成分贡献榜行尾新增「**20 日涨幅**」列（官方逐日涨跌幅复利连乘，带 % 书写；有效日 <10〔停牌/次新〕显 —），既有逐日涨幅列同步补 % 尾缀。
- 因子概率板（2026-10-12，用户反馈）：「入选因子（N）」清单默认**折叠为 4 行预览** ＋ 居中「展开全部 N 条 ⇄ 收起」按钮（`aria-expanded` 契约；N≤4 不出现按钮），清单长时卡片不再拉高整页。改动仅限 `FactorBoardPanel.tsx`（`SectionCard` 为跨三市场共享件，不动共享面）。
