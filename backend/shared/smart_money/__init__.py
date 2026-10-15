"""聪明钱（CN 市场分析 + 选股）共享层。

- datasets：数据面读取（原始层 parquet / 两融 / 北向 / 行情 / 名称行业 / 事件时间线）
- signals：事件池 + 柱 + 复合分（纯函数）
- stats：市场级统计聚合（概览带 / 趋势 / 营业部榜 / 北向摘要）

设计细案：docs/市场分析_聪明钱追踪与选股_细案_20261015.md
"""

from __future__ import annotations

__all__ = ["datasets", "signals", "stats"]
