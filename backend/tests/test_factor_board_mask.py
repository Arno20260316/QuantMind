"""因子概率板 · 可交易性掩码单测（P2-d）：读取 / 行序对齐 / 三态降级。

最坏的错法是**错位**：把 A 股的涨跌停状态扣到 B 股头上（行序对齐 bug），
或把「掩码没读到」静默当成「全都可交易」而不出 warning。本文件两条都钉死。

口径（mask.py docstring）：涨跌停/停牌是**当日**状态——无当日行一律不过滤、
绝不沿用旧日行；掩码最新 dt 距今超 MASK_STALE_DAYS 时 warning 升级为「陈旧」。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import config as C
from backend.shared.factor_board import mask as MK


def _write_mask(root: Path, rows: list[tuple[int, str, bool, bool]]) -> Path:
    """按 mask.py 契约写掩码 parquet（dt int32 / symbol str / 两个 bool）。"""
    p = root / C.TRADE_MASK_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "dt": np.array([r[0] for r in rows], dtype=np.int32),
            "symbol": [r[1] for r in rows],
            "blocked_long": [r[2] for r in rows],
            "blocked_short": [r[3] for r in rows],
        }
    ).to_parquet(p, index=False)
    return p


@pytest.mark.unit
def test_load_mask_缺文件坏文件均空dict():
    assert MK.load_mask(Path("/nonexistent/trade_mask.parquet")) == {}
    # 坏文件（不是 parquet）→ 空 dict，绝不抛（掩码坏了不该拖垮产板）
    bad = Path("/tmp/qm_mask_bad_test.parquet")
    bad.write_text("not-parquet", encoding="utf-8")
    try:
        assert MK.load_mask(bad) == {}
    finally:
        bad.unlink(missing_ok=True)


@pytest.mark.unit
def test_load_mask_按日分组两集合(tmp_path: Path):
    _write_mask(
        tmp_path,
        [
            (20261009, "600000.SH", True, False),
            (20261009, "600001.SH", False, True),
            (20261009, "600002.SH", False, False),
            (20261008, "600000.SH", False, False),
        ],
    )
    table = MK.load_mask(MK.resolve_mask_path(tmp_path))
    assert set(table) == {20261008, 20261009}
    long_set, short_set = table[20261009]
    assert long_set == frozenset({"600000.SH"})
    assert short_set == frozenset({"600001.SH"})
    # 未标记名不在任何集合
    assert "600002.SH" not in long_set and "600002.SH" not in short_set


@pytest.mark.unit
def test_align_blocked_按行序逐位对齐_不是集合顺序():
    # 行序故意和集合构造顺序相反——错位 bug 在这里必红
    index = pd.Index(["600001.SH", "600000.SH"])
    blk_l, blk_s = MK.align_blocked(
        index, (frozenset({"600000.SH"}), frozenset({"600001.SH"}))
    )
    assert blk_l.tolist() == [False, True]
    assert blk_s.tolist() == [True, False]
    # None/空 → 全 False（等价「不过滤」）
    for entry in (None, (frozenset(), frozenset())):
        blk_l, blk_s = MK.align_blocked(index, entry)
        assert blk_l.tolist() == [False, False]
        assert blk_s.tolist() == [False, False]


@pytest.mark.unit
def test_mask_columns_命中当日行_对齐且无warning(tmp_path: Path):
    # Arrange：索引含表外名；表含索引外名
    _write_mask(
        tmp_path,
        [
            (20261009, "600000.SH", True, False),
            (20261009, "600001.SH", False, True),
            (20261009, "999999.SH", True, True),  # 索引里没有 → 忽略
        ],
    )
    index = pd.Index(["600001.SH", "600000.SH", "600002.SH"])

    # Act
    blk_l, blk_s, info, warning = MK.mask_columns(tmp_path, "20261009", index)

    # Assert
    assert warning is None
    assert blk_l.tolist() == [False, True, False]
    assert blk_s.tolist() == [True, False, False]
    assert blk_l.index.equals(index)
    assert info == {
        "mask_date": 20261009,
        "mask_max_dt": 20261009,
        "stale_days": 0,
        "missing": False,
    }


@pytest.mark.unit
def test_mask_columns_缺文件_不过滤且warning显形(tmp_path: Path):
    index = pd.Index(["600000.SH"])
    blk_l, blk_s, info, warning = MK.mask_columns(tmp_path, "20261009", index)
    assert blk_l.tolist() == [False] and blk_s.tolist() == [False]
    assert info["missing"] is True and info["mask_date"] is None
    assert warning is not None and "缺失" in warning


@pytest.mark.unit
def test_mask_columns_无当日行_不沿用旧行_近期缺口只记无行(tmp_path: Path):
    # Arrange：掩码最新 20261002，trade_date 20261006（距今 4 天 ≤7）
    _write_mask(tmp_path, [(20261002, "600000.SH", True, True)])
    index = pd.Index(["600000.SH"])

    # Act
    blk_l, blk_s, info, warning = MK.mask_columns(tmp_path, "20261006", index)

    # Assert：绝不沿用旧日行（昨日涨停今日可能已开板，跨日沿用 = 错剔）
    assert blk_l.tolist() == [False] and blk_s.tolist() == [False]
    assert info == {
        "mask_date": None,
        "mask_max_dt": 20261002,
        "stale_days": 4,
        "missing": True,
    }
    assert warning is not None and "无 20261006 行" in warning and "陈旧" not in warning


@pytest.mark.unit
def test_mask_columns_陈旧超限_warning升级为陈旧幌(tmp_path: Path):
    # Arrange：掩码最新 20260920，trade_date 20261006（距今 16 天 >7）
    _write_mask(tmp_path, [(20260920, "600000.SH", True, True)])
    index = pd.Index(["600000.SH"])

    blk_l, _, info, warning = MK.mask_columns(tmp_path, "20261006", index)

    assert blk_l.tolist() == [False]  # 结果同为不过滤
    assert info["stale_days"] == 16 and info["missing"] is True
    assert warning is not None and "陈旧" in warning and ">7" in warning


@pytest.mark.unit
def test_mask模块不import_factor_report_防fastapi连坐():
    """宿主无 fastapi 的解释器也必须能 import mask——出现 import 即红。

    注意：**注释/docstring 里提到 factor_report 是允许的**（口径对照说明），
    守卫只看真正的 import 语句。
    """
    src = (
        Path(__file__).resolve().parents[1] / "shared" / "factor_board" / "mask.py"
    ).read_text(encoding="utf-8")
    imports = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not [ln for ln in imports if "factor_report" in ln], imports
