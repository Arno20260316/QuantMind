"""因子概率板 · 产物 IO 单测：schema 自校验 / 原子写 / 读取语义 / 日期清单。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import board_io as BIO
from backend.shared.factor_board import config as C


def _valid_board(n_factors: int | None = None) -> dict:
    """最小可过校验的产物骨架。"""
    n = C.MIN_SELECTED if n_factors is None else n_factors
    return {
        "schema_version": C.SCHEMA_VERSION,
        "meta": {
            "decision_date": "2026-10-12",
            "trade_date": "2026-10-09",
            "warnings": [],
        },
        "factors": [
            {"name": f"f{i}", "lib": "a", "sign": 1, "weight": 0.05} for i in range(n)
        ],
        "horizon": {"global": {}, "by_group": {}},
        "validation": {},
        "resonance": {"spearman": None},
        "sectors": {"industry_l1": None, "industry_l2": None, "concept": None},
        "stocks": {"top": [], "bottom": [], "resonant_long": [], "resonant_short": []},
    }


@pytest.mark.unit
def test_clean_缺失落null_有限值收位():
    assert BIO._clean(float("nan")) is None
    assert BIO._clean(np.inf) is None
    assert BIO._clean(None) is None
    assert BIO._clean(3) == 3
    assert BIO._clean(0.123456) == pytest.approx(0.1235)
    # 0 是真实值，绝不能被当缺失吞掉
    assert BIO._clean(0.0) == 0.0


@pytest.mark.unit
def test_validate_通过骨架():
    assert BIO.validate_board(_valid_board()) == []


@pytest.mark.unit
def test_validate_缺字段与硬门各自报错():
    # Arrange
    bad = _valid_board()
    del bad["horizon"]
    bad["schema_version"] = "factor_board/0.0"
    bad["factors"] = bad["factors"][: C.MIN_SELECTED - 1]
    del bad["sectors"]["concept"]

    # Act
    errors = BIO.validate_board(bad)

    # Assert
    joined = "\n".join(errors)
    assert "缺顶层字段: horizon" in joined
    assert "schema_version" in joined
    assert f"硬门 {C.MIN_SELECTED}" in joined
    assert "sectors.concept" in joined


@pytest.mark.unit
def test_write_board_原子落盘_不留tmp_两文件一致(tmp_path: Path):
    # Act
    dated, latest = BIO.write_board(_valid_board(), tmp_path)

    # Assert
    assert dated == tmp_path / "2026-10-12.json"
    assert latest == tmp_path / "latest.json"
    assert dated.read_text(encoding="utf-8") == latest.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
def test_write_board_校验不过不落盘(tmp_path: Path):
    # Arrange
    bad = _valid_board()
    bad["meta"]["decision_date"] = None

    # Act & Assert
    with pytest.raises(ValueError, match="自校验失败"):
        BIO.write_board(bad, tmp_path)
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.unit
def test_write_board_NaN_直接炸_绝不出非标JSON(tmp_path: Path):
    # Arrange：NaN 混入任一角落 → json.dumps(allow_nan=False) 必须拒绝
    bad = _valid_board()
    bad["resonance"]["spearman"] = float("nan")

    # Act & Assert
    with pytest.raises(ValueError):
        BIO.write_board(bad, tmp_path)


@pytest.mark.unit
def test_load_board_缺文件坏JSON均null(tmp_path: Path):
    # 缺文件
    assert BIO.load_board(tmp_path) is None
    assert BIO.load_board(tmp_path, date="2026-10-12") is None
    # 坏 JSON
    (tmp_path / "latest.json").write_text("{broken", encoding="utf-8")
    assert BIO.load_board(tmp_path) is None


@pytest.mark.unit
def test_load_board_按日与latest各自取文件(tmp_path: Path):
    # Arrange
    a = _valid_board()
    BIO.write_board(a, tmp_path)
    b = _valid_board()
    b["meta"]["decision_date"] = "2026-10-13"
    BIO.write_board(b, tmp_path)

    # Act & Assert：latest 停在最新写入；按日取历史仍可读
    assert BIO.load_board(tmp_path)["meta"]["decision_date"] == "2026-10-13"
    assert (
        BIO.load_board(tmp_path, date="2026-10-12")["meta"]["decision_date"]
        == "2026-10-12"
    )


@pytest.mark.unit
def test_available_dates_降序且只认日期文件名(tmp_path: Path):
    for d in ("2026-10-09", "2026-10-12", "2026-10-10"):
        (tmp_path / f"{d}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "latest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")

    assert BIO.available_dates(tmp_path) == ["2026-10-12", "2026-10-10", "2026-10-09"]
    assert BIO.available_dates(tmp_path / "nope") == []


@pytest.mark.unit
def test_sector_payload_空成员返回None():
    q = pd.DataFrame({"composite": [0.1], "mdl_rank": [0.6]}, index=["600001.SH"])
    members = pd.DataFrame(columns=["SectorType", "SectorName", "Symbol"])
    assert BIO.sector_payload(q, members, "concept") is None


@pytest.mark.unit
def test_sector_payload_端到端json化_缺失落null():
    # Arrange：一个概念 8 只（满足 SECTOR_MIN_N），模型只覆盖 4 只
    syms = [f"60{i:04d}.SH" for i in range(8)]
    q = pd.DataFrame(
        {
            "composite": [0.5, 0.4, 0.3, 0.2, -0.1, -0.2, -0.3, -0.4],
            "mdl_rank": [0.9, 0.8, 0.7, 0.6, np.nan, np.nan, np.nan, np.nan],
        },
        index=syms,
    )
    members = pd.DataFrame(
        {"SectorType": "concept", "SectorName": "测试概念", "Symbol": syms}
    )

    # Act
    payload = BIO.sector_payload(q, members, "concept")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)

    # Assert
    assert payload is not None
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["name"] == "测试概念"
    assert item["n"] == 8
    assert item["fbreadth"] == pytest.approx(0.5)  # 4/8 为正
    assert "NaN" not in encoded and "Infinity" not in encoded


@pytest.mark.unit
def test_factor_payload_无画像时horizon为空对象不落0():
    sel = pd.DataFrame(
        [
            {
                "name": "f",
                "lib": "a",
                "group": "a/s",
                "display": "F",
                "sgn": -1,
                "ic60": -0.12,
                "win60": 0.7,
                "icir60": -1.5,
                "kept_ic": -0.05,
                "w": 0.12,
            }
        ]
    )
    out = BIO.factor_payload(sel, pd.DataFrame())
    assert out[0]["sign"] == -1
    assert out[0]["horizon"] == {}
