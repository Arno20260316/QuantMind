"""通达信桥数据面采集包（批次 1，2026-10-15）。

模块分工见工程文档《通达信桥数据面全量接入_实施细案_20261015.md》§2.1：

* :mod:`datasets`     声明式数据集注册表（采集什么/何时/到哪张表）
* :mod:`parsers`      桥 JSON → 行记录（纯函数，金样测试面）
* :mod:`fetchers`     每数据集取数编排（宇宙/分批/预算闸）
* :mod:`sinks`        类型化 upsert + parquet 归档 + 台账/水位
* :mod:`task`         档位主循环（trade 常驻）
* :mod:`manual`       手跑 CLI（python -m …manual --once --dataset X）
* :mod:`bridge_client` / :mod:`rate_budget`  桥客户端（_nc 破缓存）与令牌桶

建表唯一出处 = ``backend/shared/tdx_ingest_contract.py``（本包不做 DDL）。
"""

from backend.shared.tdx_ingest_contract import (
    ensure_tdx_ingest_tables,
    ensure_tdx_ingest_tables_async,
)

__all__ = ["ensure_tdx_ingest_tables", "ensure_tdx_ingest_tables_async"]
