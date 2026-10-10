"""因子报告数据集注册表：rd_mined（RD-Agent 挖掘因子）接入契约（2026-10-09）。

背景：因子挖掘每天往 ``data/quantcustom/6_ml_datasets/rd_mined`` 出新因子，
经毕业桥（``backend/scripts/promote_rd_mined.py``）镜像到 CN 根后，因子报告页
却看不到 —— 因为注册表里没有这个数据集。本测试锁死三件事：

1. ``rd_mined`` 已注册且走 ``close_fwd_donor``（本表无 OHLCV，行情列由 l1_factors 供）；
2. donor 解析只对声明了 ``donor_parts`` 的数据集生效，其余数据集返回 None
   （防止「顺手给所有数据集都配 donor」把 close_fwd 的地基挖松）；
3. ``build_start`` 显式给定 —— 构建器默认 ``--years 5`` 会把 2020-2021 静默砍掉，
   报告页一键重建必须按注册表起点补齐全历史。
"""

from __future__ import annotations

from backend.services.engine.factor_report import datasets as ds


def test_rd_mined_is_registered_as_close_fwd_donor():
    cfg = ds.DATASETS["rd_mined"]

    assert cfg["label_mode"] == "close_fwd_donor"
    assert cfg["donor_parts"] == ("6_ml_datasets", "l1_factors")
    assert cfg["dir_parts"] == ("6_ml_datasets", "rd_mined")
    # rd_mined 落盘只有 symbol/date + rd_* 因子列 —— meta 白名单不得含 OHLCV
    assert tuple(cfg["meta_cols"]) == ("symbol", "date", "time", "dt")
    assert cfg["library_rule"] == "fixed:rd_mined"
    assert cfg.get("build_start") == "20200102"


def test_rd_mined_library_rule_maps_every_column_to_itself():
    assert ds.library_of("rd_mined", "rd_amihud_20d") == "rd_mined"
    assert ds.library_of("rd_mined", "rd_any_new_factor") == "rd_mined"


def test_donor_dir_resolves_only_for_datasets_declaring_it():
    p = ds.donor_dir("rd_mined")
    assert p is not None
    assert p.parts[-2:] == ("6_ml_datasets", "l1_factors")

    # 未声明 donor_parts 的数据集必须返回 None（构建器据此拒绝启动 donor 分支）
    assert ds.donor_dir("l1_factors") is None
    assert ds.donor_dir("alpha_library") is None


def test_dataset_names_include_rd_mined():
    assert "rd_mined" in ds.dataset_names()
