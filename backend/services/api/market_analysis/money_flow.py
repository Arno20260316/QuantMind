"""资金流向数据模块

提供板块/个股维度的主力资金流入/流出/净流向数据记录与查询。

数据存储在 ``qm_sector_daily_metrics.details`` JSON 字段中，
以 ``money_flow`` 键保存，避免新增数据库表。

主要数据结构:
    - MoneyFlowRecord: 单个板块某交易日的资金流记录
    - MoneyFlowService: 资金流记录、查询、排名服务
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SectorDailyMetricsRecord
from .repository import MarketAnalysisRepository


@dataclass
class MoneyFlowRecord:
    """资金流向记录 (单个板块某交易日)

    Attributes:
        sector_id: 板块 ID
        trade_date: 交易日期
        main_inflow: 主力资金流入额 (元)
        main_outflow: 主力资金流出额 (元)
        net_flow: 净流向 = main_inflow - main_outflow
        super_large_inflow: 超大单流入额 (元)
        super_large_outflow: 超大单流出额 (元)
        large_inflow: 大单流入额 (元)
        large_outflow: 大单流出额 (元)
        medium_inflow: 中单流入额 (元)
        medium_outflow: 中单流出额 (元)
        small_inflow: 小单流入额 (元)
        small_outflow: 小单流出额 (元)
    """

    sector_id: str
    trade_date: date
    main_inflow: float = 0.0
    main_outflow: float = 0.0
    net_flow: float = 0.0
    super_large_inflow: float = 0.0
    super_large_outflow: float = 0.0
    large_inflow: float = 0.0
    large_outflow: float = 0.0
    medium_inflow: float = 0.0
    medium_outflow: float = 0.0
    small_inflow: float = 0.0
    small_outflow: float = 0.0
    # 扩展字段：附加自定义属性（随记录存储，不参与查询）
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (不含 extra 嵌套展开)"""
        d = asdict(self)
        d.pop("extra", None)
        d["trade_date"] = str(self.trade_date)
        return d


class MoneyFlowService:
    """资金流向服务

    负责将资金流快照记录到 ``qm_sector_daily_metrics.details['money_flow']``，
    并提供历史查询与排名功能。

    依赖注入 ``MarketAnalysisRepository`` 进行数据库操作。
    """

    # details JSON 中资金流的键名
    FLOW_KEY = "money_flow"

    def __init__(self, repository: MarketAnalysisRepository):
        self.repository = repository

    # ---- 记录 ----

    async def record_money_flow(
        self,
        sector_id: str,
        trade_date: date,
        snapshot: dict[str, Any],
    ) -> MoneyFlowRecord:
        """记录资金流数据到 qm_sector_daily_metrics.details

        如果该日已有 metrics 记录，则更新其 details 中的 money_flow 字段；
        否则新建一条 metrics 记录 (仅含资金流数据)。

        Args:
            sector_id: 板块 ID
            trade_date: 交易日期
            snapshot: 资金流快照字典，可包含以下键:
                - main_inflow / main_outflow: 主力流入/流出
                - super_large_inflow / super_large_outflow: 超大单
                - large_inflow / large_outflow: 大单
                - medium_inflow / medium_outflow: 中单
                - small_inflow / small_outflow: 小单
                - net_flow: 净流向 (可选，不提供则自动计算)

        Returns:
            构建好的 MoneyFlowRecord
        """
        # 从快照提取数据
        record = self._build_record(sector_id, trade_date, snapshot)

        # 查询是否已有当日 metrics
        existing = await self.repository.get_latest_metrics(sector_id)
        existing_details: dict[str, Any] = {}
        if existing is not None and existing.trade_date == trade_date:
            # 已有记录，更新 details 中的 money_flow
            if isinstance(existing.details, dict):
                existing_details = dict(existing.details)
            existing_details[self.FLOW_KEY] = record.to_dict()
            # 同时更新 net_inflow 列
            stmt = (
                update(SectorDailyMetricsRecord)
                .where(
                    SectorDailyMetricsRecord.id == existing.id,
                )
                .values(details=existing_details, net_inflow=record.net_flow)
            )
            await self.repository.session.execute(stmt)
        else:
            # 无当日记录，新建一条仅含资金流的 metrics
            new_record = SectorDailyMetricsRecord(
                trade_date=trade_date,
                sector_id=sector_id,
                net_inflow=record.net_flow,
                details={self.FLOW_KEY: record.to_dict()},
            )
            self.repository.add(new_record)

        await self.repository.flush()
        return record

    # ---- 查询 ----

    async def get_money_flow(
        self,
        sector_id: str,
        start_date: date,
        end_date: date,
    ) -> list[MoneyFlowRecord]:
        """查询历史资金流数据

        Args:
            sector_id: 板块 ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            按日期升序排列的 MoneyFlowRecord 列表；
            无资金流数据的日期会被跳过。
        """
        history = await self.repository.get_metrics_history(
            sector_id, start_date=start_date, end_date=end_date
        )
        records: list[MoneyFlowRecord] = []
        for m in history:
            flow_data = self._extract_flow(m)
            if flow_data is not None:
                records.append(flow_data)
        return records

    async def get_sector_flow_ranking(
        self,
        trade_date: date,
    ) -> list[dict[str, Any]]:
        """按净流入额排序返回当日板块资金流排名

        Args:
            trade_date: 交易日期

        Returns:
            排名列表 (净流入降序)，每项包含:
            - sector_id, net_flow, main_inflow, main_outflow, rank
        """
        all_metrics = await self.repository.get_all_latest_metrics(trade_date)
        items: list[dict[str, Any]] = []
        for m in all_metrics:
            flow_data: dict[str, Any] | None = None
            m_details = m.details if isinstance(m.details, dict) else {}
            if m_details:  # type: ignore[truthy-bool]
                flow_data = m_details.get(self.FLOW_KEY)
            if flow_data is None:
                # 如果没有 details 中的 money_flow，但有 net_inflow 列
                if m.net_inflow is not None:
                    flow_data = {"net_flow": float(m.net_inflow)}
                else:
                    continue
            items.append({
                "sector_id": str(m.sector_id),
                "trade_date": str(m.trade_date),
                "net_flow": float(flow_data.get("net_flow", 0)),
                "main_inflow": float(flow_data.get("main_inflow", 0)),
                "main_outflow": float(flow_data.get("main_outflow", 0)),
            })
        # 按净流入降序排序
        items.sort(key=lambda x: x["net_flow"], reverse=True)
        # 添加排名
        for i, item in enumerate(items, 1):
            item["rank"] = i
        return items

    # ---- 内部方法 ----

    @staticmethod
    def _build_record(
        sector_id: str,
        trade_date: date,
        snapshot: dict[str, Any],
    ) -> MoneyFlowRecord:
        """从快照字典构建 MoneyFlowRecord"""
        main_inflow = float(snapshot.get("main_inflow", 0) or 0)
        main_outflow = float(snapshot.get("main_outflow", 0) or 0)

        # 净流向：优先使用传入值，否则自动计算
        net_flow = snapshot.get("net_flow")
        if net_flow is None:
            net_flow = main_inflow - main_outflow
        else:
            net_flow = float(net_flow)

        return MoneyFlowRecord(
            sector_id=sector_id,
            trade_date=trade_date,
            main_inflow=main_inflow,
            main_outflow=main_outflow,
            net_flow=net_flow,
            super_large_inflow=float(snapshot.get("super_large_inflow", 0) or 0),
            super_large_outflow=float(snapshot.get("super_large_outflow", 0) or 0),
            large_inflow=float(snapshot.get("large_inflow", 0) or 0),
            large_outflow=float(snapshot.get("large_outflow", 0) or 0),
            medium_inflow=float(snapshot.get("medium_inflow", 0) or 0),
            medium_outflow=float(snapshot.get("medium_outflow", 0) or 0),
            small_inflow=float(snapshot.get("small_inflow", 0) or 0),
            small_outflow=float(snapshot.get("small_outflow", 0) or 0),
            extra=snapshot.get("extra", {}),
        )

    def _extract_flow(self, metrics: SectorDailyMetricsRecord) -> MoneyFlowRecord | None:
        """从 metrics 记录的 details 中提取资金流数据"""
        m_details = metrics.details if isinstance(metrics.details, dict) else {}
        if not m_details:  # type: ignore[truthy-bool]
            return None
        flow_data = m_details.get(self.FLOW_KEY)
        if flow_data is None:
            return None
        # 解析日期
        td = metrics.trade_date
        if isinstance(td, str):
            td = date.fromisoformat(td)
        elif td is None:
            return None
        return MoneyFlowRecord(
            sector_id=str(metrics.sector_id),
            trade_date=td,
            main_inflow=float(flow_data.get("main_inflow", 0) or 0),
            main_outflow=float(flow_data.get("main_outflow", 0) or 0),
            net_flow=float(flow_data.get("net_flow", 0) or 0),
            super_large_inflow=float(flow_data.get("super_large_inflow", 0) or 0),
            super_large_outflow=float(flow_data.get("super_large_outflow", 0) or 0),
            large_inflow=float(flow_data.get("large_inflow", 0) or 0),
            large_outflow=float(flow_data.get("large_outflow", 0) or 0),
            medium_inflow=float(flow_data.get("medium_inflow", 0) or 0),
            medium_outflow=float(flow_data.get("medium_outflow", 0) or 0),
            small_inflow=float(flow_data.get("small_inflow", 0) or 0),
            small_outflow=float(flow_data.get("small_outflow", 0) or 0),
        )
