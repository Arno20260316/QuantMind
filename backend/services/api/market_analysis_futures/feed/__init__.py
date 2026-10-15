"""期货市场分析数据面 —— 基座 + 九域。

| 模块 | 域 | 端点前缀 |
|------|-----|----------|
| base | parquet 读取/缓存/完整交易日/品种元数据/数据质检 | /status /refresh |
| overview | 行情总览 + 榜单 + 大盘基准/官方成交额 | /overview /ranking |
| sectors | 板块热力 + 聚合 | /heatmap /sector-stats |
| term | 期限结构（含年化 carry / 距到期） | /term /term-ranking |
| outlook | 前瞻视角（五因子分位合成 + 品种画像） | /outlook /variety |
| positions | CFTC + 增减仓四象限 | /cftc /oi-changes |
| inventory | 仓单库存 + 东财库存 | /receipts /inventory |
| basis | 现货基差全景 | /basis |
| members | 席位排名 | /members |
| spread | 内外盘价差 + 贵金属期现基差 | /cross-market /precious-basis |
"""

from backend.services.api.market_analysis_futures.feed.base import (
    clear_cache_futures,
    feed_status,
)
from backend.services.api.market_analysis_futures.feed.basis import get_basis
from backend.services.api.market_analysis_futures.feed.inventory import (
    get_inventory_em,
    get_receipts,
)
from backend.services.api.market_analysis_futures.feed.members import get_members
from backend.services.api.market_analysis_futures.feed.outlook import (
    get_outlook,
    get_variety,
)
from backend.services.api.market_analysis_futures.feed.overview import (
    get_overview,
    get_ranking,
)
from backend.services.api.market_analysis_futures.feed.positions import (
    get_cftc,
    get_oi_changes,
)
from backend.services.api.market_analysis_futures.feed.sectors import (
    get_heatmap,
    get_sector_stats,
)
from backend.services.api.market_analysis_futures.feed.spread import (
    get_cross_market,
    get_precious_basis,
)
from backend.services.api.market_analysis_futures.feed.term import (
    get_term_ranking,
    get_term_structure,
)

__all__ = [
    "clear_cache_futures",
    "feed_status",
    "get_overview",
    "get_ranking",
    "get_heatmap",
    "get_sector_stats",
    "get_term_structure",
    "get_term_ranking",
    "get_outlook",
    "get_variety",
    "get_cftc",
    "get_oi_changes",
    "get_receipts",
    "get_inventory_em",
    "get_basis",
    "get_members",
    "get_cross_market",
    "get_precious_basis",
]
