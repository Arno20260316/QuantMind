"""因子概率板 signals.py：模型信号装载（CSV 分支的 symbol 口径）。

PG 分支需真库（集成范畴）不在本文件；此处专测 CSV 复现路径——它是验收
对照的工具，口径必须与 PG 逐位一致（裸 6 位码含前导零）。
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from backend.shared.factor_board import signals as SIG


def _write_csv(path, symbols: list[str]) -> None:
    df = pd.DataFrame(
        {
            "symbol": symbols,
            "fusion_score": [0.01 * i for i in range(len(symbols))],
            "rank_pct": [0.1 * (i + 1) for i in range(len(symbols))],
            "signal_side": ["BUY" if i % 2 else "SELL" for i in range(len(symbols))],
        }
    )
    df.to_csv(path, index=False)


def test_csv_symbols_preserve_leading_zeros(tmp_path):
    """裸 6 位码以文本保留：002062 不得被 read_csv 推断成 2062。"""
    # Arrange
    csv = tmp_path / "signals.csv"
    _write_csv(csv, ["000001", "002062", "300662", "600176"])

    # Act
    mdl, meta = SIG.load_model_signals(csv_path=csv, model_date="2026-10-12")

    # Assert
    assert set(mdl.index) == {"000001.SZ", "002062.SZ", "300662.SZ", "600176.SH"}
    assert meta["n"] == 4
    assert meta["source"] == "csv:signals.csv"


def test_csv_truncated_code_is_visible_not_silent(tmp_path, caplog):
    """已被截断的裸码（2062）不报错但要告警——静默会拉低模型覆盖。"""
    # Arrange
    csv = tmp_path / "bad.csv"
    _write_csv(csv, ["2062", "600176"])

    # Act
    with caplog.at_level(logging.WARNING, logger="factor_board.signals"):
        mdl, meta = SIG.load_model_signals(csv_path=csv, model_date="2026-10-12")

    # Assert
    assert meta["n"] == 2  # 行不丢，但归一失败行必须可见
    assert "2062" in mdl.index  # 认不出原样保留（后续对不上号自然剔出覆盖）
    assert "symbol 归一失败 1/2 行" in caplog.text


def test_csv_duplicate_symbol_keeps_last(tmp_path):
    """同码多行（重复导出）→ 去重保留最后一行。"""
    # Arrange
    csv = tmp_path / "dup.csv"
    _write_csv(csv, ["600176", "600176"])
    df = pd.read_csv(csv, dtype={"symbol": str})
    df.loc[1, "rank_pct"] = 0.99
    df.to_csv(csv, index=False)

    # Act
    mdl, _ = SIG.load_model_signals(csv_path=csv, model_date="2026-10-12")

    # Assert
    assert len(mdl) == 1
    assert mdl.loc["600176.SH", "rank_pct"] == pytest.approx(0.99)


def test_csv_missing_model_date_rejected(tmp_path):
    """CSV 模式必须显式给决策日（PG 才允许自动取最新批）。"""
    csv = tmp_path / "signals.csv"
    _write_csv(csv, ["600176"])
    with pytest.raises(ValueError, match="必须用 --model-date"):
        SIG.load_model_signals(csv_path=csv, model_date=None)
