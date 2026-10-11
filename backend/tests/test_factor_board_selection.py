"""因子概率板 · 精选链单测：闸门 / 族封顶 / 降级 / 汇总口径（纯函数，合成数据）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import selection as SEL


def _cand(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """(lib, name, kept_ic) → 最小候选表。"""
    return pd.DataFrame(
        [
            {
                "name": n,
                "lib": lib,
                "sublibrary": "s",
                "display_name": n,
                "kept_ic": ic,
                "kept_icir": 1.0,
                "turnover": None,
                "src": "kept",
                "group": f"{lib}/s",
            }
            for lib, n, ic in rows
        ]
    )


def _stats(rows: list[tuple[str, str, float, float, float]]) -> pd.DataFrame:
    """(lib, name, ic60, win60, kept_ic) → summarize_ic 形态表。"""
    return pd.DataFrame(
        [
            {
                "lib": lib,
                "name": n,
                "group": f"{lib}/s",
                "display": n,
                "kept_ic": kept,
                "kept_icir": 1.0,
                "turnover": None,
                "src": "kept",
                "ic60": ic60,
                "win60": win,
                "n_days": 60,
                "icir60": ic60 * 10,
            }
            for lib, n, ic60, win, kept in rows
        ]
    )


@pytest.mark.unit
def test_闸门三条件必须同时成立():
    # Arrange：分别只违反一个条件
    stats = _stats(
        [
            ("a", "pass", 0.05, 0.6, 0.04),  # 全过
            ("a", "small_ic", 0.01, 0.9, 0.04),  # |IC60| 不足
            ("a", "flip", -0.05, 0.9, 0.04),  # 方向与联合闸不一致
            ("a", "low_win", 0.05, 0.4, 0.04),  # 胜率不足
        ]
    )

    # Act
    gated = SEL.gate(stats, min_win=0.5)

    # Assert
    assert gated["name"].tolist() == ["pass"]


@pytest.mark.unit
def test_族封顶每族只留_IC60_绝对值前3():
    # Arrange：同一 (lib, group) 5 个 + 另一族 1 个 + 第三族 11 个
    # （第三族是陪跑：把过闸数抬过 MIN_GATED=15，隔离本用例要测的封顶逻辑，
    #  否则会顺带触发 win 降级路径——降级本身另有专测）
    rows = [("a", f"f{i}", 0.2 - i * 0.01, 0.8, 0.05) for i in range(5)]
    rows += [("b", "x", 0.1, 0.8, 0.05)]
    rows += [("c", f"c{i}", 0.09 - i * 0.004, 0.8, 0.05) for i in range(11)]
    stats = _stats(rows)

    # Act
    sel, info = SEL.select_factors(stats)

    # Assert
    fam_a = sel[sel["lib"] == "a"]
    assert len(fam_a) == 3
    assert set(fam_a["name"]) == {"f0", "f1", "f2"}  # 按 |IC60| 降序前3
    assert len(sel[sel["lib"] == "c"]) == 3  # 封顶对每族都生效
    assert info["families"] == 3
    assert info["gated"] == 17
    assert info["degraded"] is False


@pytest.mark.unit
def test_rd_mined_族上限为4():
    rows = [("rd_mined", f"r{i}", 0.2 - i * 0.01, 0.8, 0.05) for i in range(6)]
    stats = _stats(rows)
    sel, _ = SEL.select_factors(stats)
    assert len(sel) == 4


@pytest.mark.unit
def test_过闸不足时降级胜率至_0_45():
    # Arrange：全部胜率 0.47 → 0.5 门槛全灭 → 降级后全过
    stats = _stats([("a", f"f{i}", 0.1, 0.47, 0.05) for i in range(3)])

    # Act
    sel, info = SEL.select_factors(stats)

    # Assert
    assert info["degraded"] is True
    assert len(sel) == 3


@pytest.mark.unit
def test_反向因子保留负号_by_sgn_而权重取正():
    stats = _stats(
        [
            ("a", "rev", -0.3, 0.8, -0.05),  # 反向因子：低值利好
            ("a", "fwd", 0.005, 0.8, 0.05),  # |IC60| 不足，落选
        ]
    )
    sel, _ = SEL.select_factors(stats)
    row = sel.iloc[0]
    assert row["name"] == "rev"
    assert row["sgn"] == -1
    assert row["w"] == pytest.approx(0.15)  # |IC60|=0.3 截断到上限
    assert row["w"] > 0  # 权重恒正，方向由 sgn 携带


@pytest.mark.unit
def test_汇总口径_ic60_与方向胜率():
    # Arrange：一个因子 60 天里 58 天正 IC、2 天负
    ics = [0.1] * 58 + [-0.05, -0.05]
    ic_table = {("a", "f"): ics}
    cand = _cand([("a", "f", 0.08)])

    # Act
    stats = SEL.summarize_ic(ic_table, cand)

    # Assert
    row = stats.iloc[0]
    assert row["ic60"] == pytest.approx(np.mean(ics))
    assert row["win60"] == pytest.approx(58 / 60)
    assert row["n_days"] == 60


@pytest.mark.unit
def test_有效天数不足40天不可评估():
    ic_table = {("a", "f"): [0.1] * 39}
    stats = SEL.summarize_ic(ic_table, _cand([("a", "f", 0.08)]))
    assert stats.empty
