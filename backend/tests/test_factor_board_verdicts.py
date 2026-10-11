"""因子概率板 · 判定层单测：三层真值表（金样）/ 聚合 / 四象限 / 名单 / 合成门槛。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import composite as CP
from backend.shared.factor_board import sectors as S

GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "factorBoardGolden.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    assert GOLDEN_PATH.exists(), "金样缺失：factorBoardGolden.json"
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert data["verdictTruthTable"], "金样不可为空"
    return data


def _sector_rows() -> pd.DataFrame:
    """一行命中一个判定类别（quantile 由表自身决定，值按构造可预期）。"""
    return pd.DataFrame(
        [
            # 6 个板块：均分 -0.6/-0.4/-0.5 低侧 与 0.4/0.6/0.5 高侧
            {"SectorName": "多A", "fac": 0.6, "fbreadth": 0.9, "mbreadth": 0.7},
            {"SectorName": "多弱B", "fac": 0.5, "fbreadth": 0.8, "mbreadth": 0.2},
            {"SectorName": "中C", "fac": 0.0, "fbreadth": 0.5, "mbreadth": 0.5},
            {"SectorName": "空D", "fac": -0.6, "fbreadth": 0.1, "mbreadth": 0.3},
            {"SectorName": "空抗E", "fac": -0.5, "fbreadth": 0.2, "mbreadth": 0.9},
            {"SectorName": "缺模F", "fac": 0.4, "fbreadth": 0.9, "mbreadth": np.nan},
        ]
    ).set_index("SectorName")


@pytest.mark.unit
@pytest.mark.parametrize(
    "row",
    json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["verdictTruthTable"],
    ids=lambda r: r["case"],
)
def test_三层判定真值表_金样逐条(row: dict):
    # Act
    got = S.classify_sector(
        row["fac"], row["fbreadth"], row["mbreadth"], row["q67"], row["q33"]
    )
    # Assert
    assert got == row["expect"]


@pytest.mark.unit
def test_apply_verdicts_与逐行函数一致(golden):
    # Arrange
    g = _sector_rows()

    # Act
    out, q67, q33 = S.apply_verdicts(g)

    # Assert：逐行复算应与向量化入口一致
    for name, r in out.iterrows():
        expect = S.classify_sector(
            S._f(r["fac"]), S._f(r["fbreadth"]), S._f(r["mbreadth"]), q67, q33
        )
        assert r["verdict"] == expect, name
    verdicts = dict(out["verdict"])
    assert verdicts["多A"] == S.VERDICT_LONG
    assert verdicts["多弱B"] == S.VERDICT_WEAK
    assert verdicts["空D"] == S.VERDICT_SHORT
    assert verdicts["空抗E"] == S.VERDICT_RESILIENT
    assert verdicts["缺模F"] == S.VERDICT_NEUTRAL  # 模型广度缺失 → 不判定


@pytest.mark.unit
def test_板块聚合_广度与最小成分数():
    # Arrange：板块 X 4 只（3 只 composite>0，模型覆盖 2 只其中 1 只≥0.5）
    idx = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"]
    base = pd.DataFrame(
        {
            "composite": [0.2, 0.1, -0.1, 0.3, np.nan],
            "mdl_rank": [0.8, 0.2, np.nan, np.nan, 0.9],
            "SectorName": ["X", "X", "X", "X", "Y"],
        },
        index=idx,
    )

    # Act：min_n=4 → 只剩 X
    g = S.sector_table(base, min_n=4)

    # Assert
    assert list(g.index) == ["X"]
    row = g.loc["X"]
    assert row["n"] == 4
    assert row["fbreadth"] == pytest.approx(0.75)  # 0.2/0.1/0.3 为正
    assert row["mbreadth"] == pytest.approx(0.5)  # 覆盖 2 只，1 只 ≥0.5
    assert row["model_cov"] == pytest.approx(0.5)


@pytest.mark.unit
def test_四象限计数与名单_剔ST_缺失不落0():
    # Arrange
    idx = [f"60{i:04d}.SH" for i in range(10)]
    stock = pd.DataFrame(
        {
            "composite": [0.9, 0.8, 0.7, -0.9, -0.8, -0.7, 0.1, 0.0, 0.05, 0.6],
            "mdl_rank": [0.9, 0.8, 0.75, 0.1, 0.2, 0.25, 0.5, 0.5, 0.5, 0.65],
            "st": [False, True, False, False, False, False, False, False, False, False],
            "name": [f"票{i}" for i in range(10)],
            "sector_l2": [None] * 10,
        },
        index=idx,
    )

    # Act
    q = S.quadrant_frame(stock)
    counts = S.quadrant_counts(q)
    lists = S.stock_lists(q, n=5)

    # Assert：HH = 3 只（其中一只 ST）→ 名单剔 ST 后 2 只
    assert counts["HH"] == 3
    assert counts["LL"] == 3
    assert sum(counts.values()) == 10
    long_syms = [x["symbol"] for x in lists["resonant_long"]]
    assert idx[1] not in long_syms  # ST 剔除
    assert long_syms == [idx[0], idx[2]]  # 按 composite 降序
    short_syms = [x["symbol"] for x in lists["resonant_short"]]
    assert short_syms == [idx[3], idx[4], idx[5]]
    # 缺失语义：sector_l2 是 None 就落 null，不落 0/空串
    assert all(x["sector_l2"] is None for x in lists["resonant_long"])


@pytest.mark.unit
def test_合成_反向因子低值加分_覆盖门槛剔除():
    # Arrange：单库两个因子；B 在覆盖门槛内，C 只缺 1 个因子 → 该股被剔除
    selected = pd.DataFrame(
        [
            {"lib": "a", "name": "fwd", "sgn": 1, "w": 0.1},
            {"lib": "a", "name": "rev", "sgn": -1, "w": 0.1},
            {"lib": "a", "name": "aux", "sgn": 1, "w": 0.1},
        ]
    )
    syms = ["S1", "S2", "S3"]
    lib_frames = {
        "a": pd.DataFrame(
            {
                "fwd": [0.9, 0.1, np.nan],  # 高值
                "rev": [0.1, 0.9, 0.5],  # 反向：低值才是利好
                "aux": [0.9, 0.9, 0.5],
            },
            index=syms,
        )
    }

    # Act
    comp = CP.composite_day(selected, lib_frames).dropna()

    # Assert：S1 正向因子高+反向因子低 → 分应高于 S2（仅 aux 平）
    assert comp["S1"] > comp["S2"]
    # S3 缺 fwd（1/3 权重 → 覆盖 2/3 < 70%）→ 被覆盖门槛剔除
    assert "S3" not in comp.index
