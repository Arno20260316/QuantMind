"""市场根解析（backend/shared/feature_source.py）单元测试。

因子研究四市场分层的地基：``validate_market`` 严格校验（非法值**绝不静默落 CN**，
与训练侧宽松版 ``normalize_market`` 的分工锁死）、``resolve_market_root`` 的市场→
根矩阵（CN 走存量候选表、其余走 ``market_data_dir``，两套解析语义不得互相覆盖）。
"""
from __future__ import annotations

import pytest

from backend.shared import feature_source


@pytest.mark.unit
def test_validate_market_normalizes_aliases():
    assert feature_source.validate_market("A") == "CN"
    assert feature_source.validate_market("a_share") == "CN"
    assert feature_source.validate_market("sse") == "CN"
    assert feature_source.validate_market(" hk ") == "HK"
    assert feature_source.validate_market("futures") == "FUTURES"
    assert feature_source.validate_market(None) == "CN"


@pytest.mark.unit
def test_validate_market_rejects_unknown_instead_of_falling_back():
    """非法市场必须显式失败 —— 与 normalize_market 的宽松回退（落 CN）刻意不同。

    这条分歧是设计：要查 HK 却因拼错市场读 A 股数据，是以「港股」名义展示 A 股
    口径的数据污染；宽松回退留给训练页这类回落无害的场景。
    """
    from backend.services.engine.data_platform.quantdb_factor_reader import (
        normalize_market,
    )

    with pytest.raises(ValueError, match="未知市场"):
        feature_source.validate_market("XX")
    with pytest.raises(ValueError, match="未知市场"):
        feature_source.validate_market("港股")
    # 对照组：宽松版同一输入静默落 CN（分歧锁定，改一侧必须同步另一侧的注释）
    assert normalize_market("XX") == "CN"


@pytest.mark.unit
def test_resolve_market_root_cn_uses_legacy_candidate_table(tmp_path, monkeypatch):
    """CN 走 ``resolve_quantdb_dir``（带非空探测），不走 market_data_dir 的 env 直读。"""
    root = tmp_path / "quantdb"
    (root / "2_base_sector").mkdir(parents=True)
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(root))

    assert feature_source.resolve_market_root("CN") == root
    assert feature_source.resolve_market_root("A") == root  # 别名同根


@pytest.mark.unit
def test_resolve_market_root_non_cn_honors_market_env(tmp_path, monkeypatch):
    hk = tmp_path / "quanthk"
    us = tmp_path / "quantus"
    futures = tmp_path / "quantfutures"
    bc = tmp_path / "quantbc"
    monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(hk))
    monkeypatch.setenv("QM_QUANTUS_DATA_DIR", str(us))
    monkeypatch.setenv("QM_QUANTFUTURES_DATA_DIR", str(futures))
    monkeypatch.setenv("QM_QUANTBC_DATA_DIR", str(bc))

    assert feature_source.resolve_market_root("HK") == hk
    assert feature_source.resolve_market_root("US") == us
    assert feature_source.resolve_market_root("FUTURES") == futures
    assert feature_source.resolve_market_root("CRYPTO") == bc


@pytest.mark.unit
def test_resolve_market_root_non_cn_defaults_without_env(monkeypatch):
    """env 未设 → 各市场默认容器路径（不探测盘符，与 market_data_dir 语义一致）。"""
    from backend.services.engine.data_platform.quantdb_factor_reader import (
        MARKET_DATA_DIR_DEFAULT,
    )

    for env in (
        "QM_QUANTHK_DATA_DIR",
        "QM_QUANTUS_DATA_DIR",
        "QM_QUANTFUTURES_DATA_DIR",
        "QM_QUANTBC_DATA_DIR",
    ):
        monkeypatch.delenv(env, raising=False)

    assert str(feature_source.resolve_market_root("HK")) == MARKET_DATA_DIR_DEFAULT["HK"]
    assert str(feature_source.resolve_market_root("US")) == MARKET_DATA_DIR_DEFAULT["US"]
    assert (
        str(feature_source.resolve_market_root("FUTURES"))
        == MARKET_DATA_DIR_DEFAULT["FUTURES"]
    )


@pytest.mark.unit
def test_resolve_market_root_rejects_unknown(monkeypatch):
    with pytest.raises(ValueError, match="未知市场"):
        feature_source.resolve_market_root("XX")
