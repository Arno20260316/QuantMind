"""因子概率板 · 持有期画像单测：方向对齐（sgn×RankIC）口径（合成数据，不落盘）。

背景（2026-10-11 目检抓获）：horizon_table 曾直接存原始符号 RankIC，反向因子
（sgn=−1）画像为负、全局均值正负相消≈0（实机 −0.019…−0.030），与细案 §2.3
「近 45 日 |IC|」（原型 0.124–0.178 全正）不符。修复=与合成口径一致乘 sgn。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import composite as CP

N_SYMBOLS = 900  # ≥ MIN_STOCK_SAMPLE(800)，单日 IC 才计数
DATES = ["2026-09-01", "2026-09-02", "2026-09-03"]


def _selected() -> pd.DataFrame:
    """(lib, name, sgn)：一正向、一反向、一「首日同向随后反转」的翻转因子。"""
    return pd.DataFrame(
        [
            {"lib": "libA", "name": "f_pos", "sgn": 1, "group": "libA/s"},
            {"lib": "libA", "name": "f_rev", "sgn": -1, "group": "libA/s"},
            {"lib": "libA", "name": "f_flip", "sgn": 1, "group": "libA/s"},
        ]
    )


def _x() -> pd.Series:
    return pd.Series(
        np.arange(N_SYMBOLS, dtype=float), index=[f"S{i:04d}" for i in range(N_SYMBOLS)]
    )


def _patch_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """f_pos 与 fwd 正相关、f_rev 负相关、f_flip 首日正随后负（跨日翻转）。"""

    def fake_frames(quantdb_root: Path, selected: pd.DataFrame, dt: str) -> dict:
        x = _x()
        flip = x if dt == DATES[0] else -x
        return {"libA": pd.DataFrame({"f_pos": x, "f_rev": -x, "f_flip": flip})}

    monkeypatch.setattr(CP, "read_selected_frames", fake_frames)


def test_反向因子画像方向对齐为正(monkeypatch: pytest.MonkeyPatch):
    # Arrange：f_rev 原始 RankIC=−1，sgn=−1 → 对齐后 +1
    _patch_frames(monkeypatch)
    selected = _selected()
    x = _x()
    fwd = {h: {d: x.rank() for d in DATES} for h in (1, 2, 5, 10, 20)}

    # Act
    hz = CP.horizon_table(Path("/nonexistent"), selected, DATES, 0, fwd)

    # Assert：正反两因子对齐后都为正强度（修复前 f_rev 为 −1，本断言红）
    assert hz.loc[("libA", "f_pos"), 1] == pytest.approx(1.0, abs=1e-9)
    assert hz.loc[("libA", "f_rev"), 1] == pytest.approx(1.0, abs=1e-9)
    assert hz.loc[("libA", "f_rev"), 5] == pytest.approx(1.0, abs=1e-9)


def test_对齐后跨日翻转仍可为负_翻转显形不被抹平(monkeypatch: pytest.MonkeyPatch):
    # Arrange：f_flip 三日原始 RankIC = +1, −1, −1，sgn=+1 → 均值 −1/3
    _patch_frames(monkeypatch)
    selected = _selected()
    x = _x()
    fwd = {h: {d: x.rank() for d in DATES} for h in (1, 2, 5, 10, 20)}

    # Act
    hz = CP.horizon_table(Path("/nonexistent"), selected, DATES, 0, fwd)

    # Assert：对齐不是取绝对值——跨日翻转占优时保留负号（有效信息）
    assert hz.loc[("libA", "f_flip"), 1] == pytest.approx(-1.0 / 3.0, abs=1e-9)


def test_无前向数据与样本不足的持有期为None(monkeypatch: pytest.MonkeyPatch):
    # Arrange：h=20 无前向；另设一因子仅 100 只覆盖（<800 闸）
    selected = _selected()
    x = _x()
    x_thin = x.copy()
    x_thin.iloc[100:] = np.nan
    fwd = {1: {d: x.rank() for d in DATES}}
    fwd.update({h: {} for h in (2, 5, 10, 20)})

    def fake_frames(quantdb_root: Path, sel: pd.DataFrame, dt: str) -> dict:
        return {"libA": pd.DataFrame({"f_pos": x, "f_rev": -x, "f_flip": x_thin})}

    monkeypatch.setattr(CP, "read_selected_frames", fake_frames)

    # Act
    hz = CP.horizon_table(Path("/nonexistent"), selected, DATES, 0, fwd)

    # Assert
    assert hz.loc[("libA", "f_pos"), 1] == pytest.approx(1.0, abs=1e-9)
    assert hz.loc[("libA", "f_pos"), 20] is None or np.isnan(
        hz.loc[("libA", "f_pos"), 20]
    )
    assert hz.loc[("libA", "f_flip"), 1] is None or np.isnan(
        hz.loc[("libA", "f_flip"), 1]
    )
