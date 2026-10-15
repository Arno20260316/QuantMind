"""单因子详情携带来源库 —— ``factor_detail.source_library``（「因子报告」跳转的定位契约）。

前端单因子页（因子研究·单因子分析）的「因子报告」按钮按 ``detail.source_library``
去因子报告里定位**对应数据集**的同名因子。该字段的取值只有一条正路：
``discovery.source_of(wind_source)`` 现算——与扫描侧（scan_sources 的
catalog_library）共用同一个反查实现，前端不解析显示串、后端不重复一遍前缀规则。

三条行为线（每条都对应一种会被静默吞掉的错法）：
1. QuantDB 前缀来源 → 目录名**逐字**下发（多一个斜杠/少一段都不行，
   前端拿它做 getFactorDatasets 的键匹配，错一字符入口就消失且无人报错）；
2. 非前缀来源（经典 demo 的 Wind 字段名）→ **空串**，不是 None、更不是整个
   wind_source 原样下发（原样下发会让前端拿着「AShareEODPrices…」去匹配数据集）；
3. 前缀不完整（只剩目录名）→ 空串——绝不猜。
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services.engine.factor_report import datasets as report_datasets
from backend.services.engine.factor_research import discovery, scorecard, service

_SYMS = ["000001.SZ", "600000.SH"]
_MONTHS = ["2026-09-30", "2026-10-08"]

_PREFIXED = "PREFIXED"  # wind_source = QuantDB 6_ml_datasets/l1_factors
_WIND = "WIND"  # wind_source = Wind 字段名（经典 demo 口径）
_BARE = "BARE"  # wind_source = l1_factors（目录名但没有前缀）


def _panel() -> scorecard.Panel:
    rows = []
    for code in (_PREFIXED, _WIND, _BARE):
        for mi, month in enumerate(_MONTHS):
            for rk, sym in enumerate(_SYMS, 1):
                rows.append(
                    {
                        "factor_code": code,
                        "trade_date": pd.Timestamp(month),
                        "rank": rk,
                        "symbol": sym,
                        "score": float(10 - rk - mi),
                        "raw": float(rk) * 0.5,
                        "fwd_ret": 0.01 * rk,
                    }
                )
    return scorecard.Panel(pd.DataFrame(rows))


def _factor_meta(code: str, wind_source: str) -> dict:
    return {
        "code": code,
        "name_cn": f"因子{code}",
        "display_name": "",
        "l1": "L1",
        "l2": "L2",
        "direction": 1,
        "description": "",
        "formula": "",
        "wind_source": wind_source,
        "available": True,
    }


@pytest.fixture()
def env(monkeypatch):
    """内存 store：面板/快照/元数据全部注入，绕开磁盘产物。"""
    service._ctx_cache.clear()
    service._derived_cache.clear()
    p = _panel()
    monkeypatch.setattr(service.store, "panel", lambda dataset="classic": p)
    monkeypatch.setattr(
        service.store, "panel_stamp", lambda dataset="classic": "snap-1"
    )
    monkeypatch.setattr(service.store, "benchmark_table", lambda *a, **k: None)
    monkeypatch.setattr(service.store, "ic_table", lambda *a, **k: None)
    monkeypatch.setattr(
        service.store,
        "factors_meta",
        lambda dataset="private": {
            "factors": [
                _factor_meta(_PREFIXED, "QuantDB 6_ml_datasets/l1_factors"),
                _factor_meta(_WIND, "AShareEODPrices.S_DQ_ADJCLOSE"),
                _factor_meta(_BARE, "l1_factors"),
            ]
        },
    )
    snap = pd.DataFrame(
        [
            {
                "symbol": sym,
                "name": f"名称{idx}",
                "industry": "银行",
                "total_mv_yi": 500.0 + idx,
                "pe_ttm": 10.0,
                "pb": 1.0,
                "avg_amount_yi": 3.0,
            }
            for idx, sym in enumerate(_SYMS)
        ]
    )
    monkeypatch.setattr(service.store, "stock_snapshot", lambda: snap)
    yield
    service._ctx_cache.clear()
    service._derived_cache.clear()


def test_prefixed_source_yields_dataset_dir_verbatim(env):
    d = service.factor_detail(_PREFIXED, ns=[1], dataset="private")

    assert d is not None
    assert d["source_library"] == "l1_factors"


def test_wind_source_without_prefix_yields_empty(env):
    """经典 demo：来源是 Wind 字段名，没有 QuantDB 库可跳 —— 空串，不原样下发。"""
    d = service.factor_detail(_WIND, ns=[1], dataset="private")

    assert d is not None
    assert d["source_library"] == ""


def test_bare_dir_name_without_prefix_yields_empty(env):
    """前缀残缺：只有一个像目录名的串 —— 不许猜，空串。"""
    d = service.factor_detail(_BARE, ns=[1], dataset="private")

    assert d is not None
    assert d["source_library"] == ""


def test_every_panel_source_library_has_a_report_dataset():
    """跨模块契约：面板能给出的来源库目录名，因子报告必须都有同名数据集。

    两份清单分居两个模块（discovery 的来源枚举 / factor_report 的 DATASETS），
    之间没有编译期联系：只往一边加库，前端「因子报告」入口会在整库因子上
    静默消失 —— 不报错、不告警。这条守卫让分叉在测试里当场红，而不是等
    用户发现按钮不见了。

    范围只取**静态清单**（L1L2/KEPT_LIB/AUTO_PRIORITY）：auto 扫描出的目录
    是数据态、随盘而变，不该进单元测试；静态清单覆盖了入仓的全部已登记库。
    """
    panel_sources = (
        set(discovery.L1L2_SOURCES)
        | set(discovery.KEPT_LIB_SOURCES)
        | set(discovery.AUTO_PRIORITY)
    )
    missing = sorted(panel_sources - set(report_datasets.DATASETS))

    assert missing == []
