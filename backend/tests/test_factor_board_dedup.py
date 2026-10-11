"""因子概率板 · 入选内二次去重单测（P2-e）：族头池 / 恒等退化 / 席位置换 / 族修剪。

面板用**旋转置换**合成：符号 S_i 取值 ``(i+k) mod 10``，任意两个旋转的逐日
Spearman 可手算（n=10 时 Σd²=(10−r)r²+r(10−r)² → ρ=1−6Σd²/990）：
r=1 → 0.4545、r=2 → 0.0303、r=3 → −0.2727、r=4 → −0.4545、r=5 → −0.5152，
r=0 → 1.0（全同）。全部 <0.8 的既非重复也非警区；「同置换」= ρ=1 = 重复。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backend.shared.factor_board import config as C
from backend.shared.factor_board import dedup as DD

DAYS = ("d1", "d2", "d3", "d4")


def _gated(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """(lib, name, ic60) → 过闸全表（gated_table 形态）。"""
    return pd.DataFrame(
        [
            {"lib": lib, "name": n, "group": f"{lib}/s", "ic60": ic}
            for lib, n, ic in rows
        ]
    )


def _sel(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """(lib, name, ic60) → 入选表（sgn/w 同 select_factors 出口式）。"""
    df = _gated(rows)
    df["sgn"] = np.sign(df["ic60"]).astype(int)
    df["w"] = df["ic60"].abs().clip(C.W_CLIP_LO, C.W_CLIP_HI)
    return df


def _key(lib: str, name: str) -> str:
    return DD.key_of(lib, name)


def _panel(pool: pd.DataFrame, patterns: dict[str, list[int]]) -> dict:
    """池 → {key: 长序列}；patterns[key][i] = 符号 S_i 的取值（逐日同模式）。"""
    syms = [f"S{i}" for i in range(10)]
    idx = pd.MultiIndex.from_product([list(DAYS), syms], names=["trade_date", "symbol"])
    out = {}
    for key in pool["key"]:
        assert key in patterns, f"测试自身的 pattern 表缺 {key}"
        vals = [float(patterns[key][i % 10]) for _d in DAYS for i in range(10)]
        out[key] = pd.Series(vals, index=idx)
    return out


def _fake_panel(monkeypatch, patterns: dict[str, list[int]], expect_calls=None):
    def fake(quantdb_root, pool, dates):
        if expect_calls is not None:
            expect_calls.append(len(pool))
        return _panel(pool, patterns)

    monkeypatch.setattr(DD, "build_panel", fake)


def _rot(k: int) -> list[int]:
    return [(i + k) % 10 for i in range(10)]


# ═══════════════════ 族头池 ═══════════════════


@pytest.mark.unit
def test_build_pool_入选在前_替补头CAP_替补sgn_w非NaN():
    # Arrange：a/s 族 5 个过闸（入选 1）+ rd_mined 族 6 个（入选 1）
    gated = _gated(
        [
            ("a", "A1", 0.09),
            ("a", "A2", 0.07),
            ("a", "A3", -0.05),  # 反向替补：sgn 必须为 −1
            ("a", "A4", 0.04),
            ("a", "A5", 0.02),
            ("rd_mined", "R1", 0.08),
            ("rd_mined", "R2", 0.065),
            ("rd_mined", "R3", 0.06),
            ("rd_mined", "R4", 0.055),
            ("rd_mined", "R5", 0.05),
            ("rd_mined", "R6", 0.045),
        ]
    )
    sel = _sel([("a", "A1", 0.09), ("rd_mined", "R1", 0.08)])

    # Act
    pool = DD.build_pool(gated, sel)

    # Assert：前排 = 入选（|ic60| 降序），后排 = 各族头 CAP 名（rd 族 CAP=4）
    assert pool["is_sel"].tolist() == [True, True] + [False] * 7
    assert pool["key"].iloc[:2].tolist() == [_key("a", "A1"), _key("rd_mined", "R1")]
    reserves = pool[~pool["is_sel"]]
    # a/s 替补取 |ic60| 头 FAMILY_CAP=3：A2/A3/A4（A5 被截）；rd 头 4：R2~R5（R6 被截）
    assert set(reserves["key"]) == {
        _key("a", "A2"),
        _key("a", "A3"),
        _key("a", "A4"),
        _key("rd_mined", "R2"),
        _key("rd_mined", "R3"),
        _key("rd_mined", "R4"),
        _key("rd_mined", "R5"),
    }
    # 替补全局按 |ic60| 降序
    assert reserves["ic60"].abs().tolist() == sorted(
        reserves["ic60"].abs().tolist(), reverse=True
    )
    # 回归钉子：替补行的 sgn/w 由 build_pool 同式补齐（曾为 NaN——NaN 权重
    # 会静默吃掉合成贡献）
    assert not reserves[["sgn", "w"]].isna().any().any()
    a3 = pool[pool["key"] == _key("a", "A3")].iloc[0]
    assert a3["sgn"] == -1 and a3["w"] == pytest.approx(0.05)
    a4 = pool[pool["key"] == _key("a", "A4")].iloc[0]
    assert a4["sgn"] == 1 and a4["w"] == pytest.approx(0.04)


# ═══════════════════ 恒等退化 / 置换 / 修剪 ═══════════════════


@pytest.mark.unit
def test_恒等退化_kept前topk等于入选原样_警区保留记警(monkeypatch):
    # Arrange：B1 与 A1 近同（仅 S0/S4 互换 → ρ≈0.806 ∈ [0.8,0.9) 警区）
    sel = _sel([("a", "A1", 0.09), ("b", "B1", 0.07)])
    gated = _gated([("a", "A1", 0.09), ("a", "A2", 0.06), ("b", "B1", 0.07)])
    swapped = list(range(10))
    swapped[0], swapped[4] = swapped[4], swapped[0]
    _fake_panel(
        monkeypatch,
        {_key("a", "A1"): _rot(0), _key("b", "B1"): swapped, _key("a", "A2"): _rot(1)},
    )

    # Act
    out = DD.dedup_selection("/qdb", gated, sel, list(DAYS))

    # Assert：无 ≥reject 的对 → kept 前 top_k（=len(sel)）就是入选原样
    assert out.kept_keys == (_key("a", "A1"), _key("b", "B1"))
    assert out.dropped == () and out.pruned_keys == ()
    assert out.n_reserves == 1
    # 警区对保留但记警（ρ 手算 1−6·32/990 ≈ 0.8061）
    assert len(out.warnings) == 1
    key_a, key_b, rho = out.warnings[0]
    assert key_b == _key("a", "A1") and rho == pytest.approx(0.8061, abs=1e-3)


@pytest.mark.unit
def test_席位置换_高相关被拒_不占席_替补递补(monkeypatch):
    # Arrange：B1 ≡ A1（ρ=1）→ 被拒；A2（独立）递补进席
    sel = _sel([("a", "A1", 0.09), ("b", "B1", 0.08)])
    gated = _gated(
        [("a", "A1", 0.09), ("a", "A2", 0.07), ("b", "B1", 0.08), ("b", "B2", 0.06)]
    )
    _fake_panel(
        monkeypatch,
        {
            _key("a", "A1"): _rot(0),
            _key("b", "B1"): _rot(0),  # 与 A1 全同 → ρ=1 → 拒
            _key("a", "A2"): _rot(1),
            _key("b", "B2"): _rot(1),
        },
    )

    # Act
    out = DD.dedup_selection("/qdb", gated, sel, list(DAYS))

    # Assert：kept = 前排 A1 + 递补的 A2；被拒者不占席
    assert out.kept_keys == (_key("a", "A1"), _key("a", "A2"))
    assert out.n_reserves == 2
    assert len(out.dropped) == 1  # B1（B2 在凑满 K 后不再评估）
    k, matched, rho = out.dropped[0]
    assert k == _key("b", "B1") and matched == _key("a", "A1")
    assert rho == pytest.approx(1.0, abs=1e-9)
    assert out.pruned_keys == ()


@pytest.mark.unit
def test_族cap后置修剪_递补顶过CAP_失去席位不回收(monkeypatch):
    # Arrange：a/s 族入选已顶 CAP=3；b 族入选被拒 → a 族替补递补 → 4 个 a → 修剪
    sel = _sel(
        [("a", "A1", 0.09), ("a", "A2", 0.08), ("a", "A3", 0.07), ("b", "B1", 0.06)]
    )
    gated = _gated(
        [
            ("a", "A1", 0.09),
            ("a", "A2", 0.08),
            ("a", "A3", 0.07),
            ("a", "A4", 0.05),
            ("b", "B1", 0.06),
            ("b", "B2", 0.055),
        ]
    )
    _fake_panel(
        monkeypatch,
        {
            _key("a", "A1"): _rot(0),
            _key("a", "A2"): _rot(1),
            _key("a", "A3"): _rot(3),
            _key("b", "B1"): _rot(0),  # ≡A1 → 拒
            _key("b", "B2"): _rot(0),  # ≡A1 → 拒
            _key("a", "A4"): _rot(5),
        },
    )

    # Act
    out = DD.dedup_selection("/qdb", gated, sel, list(DAYS))

    # Assert：greedy 收 [A1,A2,A3,A4] → 族内第 4 个被修剪，席位不回收
    assert out.kept_keys == (_key("a", "A1"), _key("a", "A2"), _key("a", "A3"))
    assert out.pruned_keys == (_key("a", "A4"),)
    assert {d[0] for d in out.dropped} == {_key("b", "B1"), _key("b", "B2")}


@pytest.mark.unit
def test_无替补_恒等退化_不建面板(monkeypatch):
    # Arrange：gated == sel（无替补）→ 绝不触面板（省掉百万行读取）
    sel = _sel([("a", "A1", 0.09), ("a", "A2", 0.08)])
    gated = _gated([("a", "A1", 0.09), ("a", "A2", 0.08)])

    def boom(*a, **kw):  # pragma: no cover — 触发即测试失败
        raise AssertionError("无替补时不应建面板")

    monkeypatch.setattr(DD, "build_panel", boom)

    # Act
    out = DD.dedup_selection("/qdb", gated, sel, list(DAYS))

    # Assert
    assert out.kept_keys == (_key("a", "A1"), _key("a", "A2"))
    assert out.n_reserves == 0 and out.dropped == () and out.pruned_keys == ()


# ═══════════════════ dedup 块（JSON 安全） ═══════════════════


@pytest.mark.unit
def test_dedup_block_json安全_rho净化(monkeypatch):
    sel = _sel([("a", "A1", 0.09), ("b", "B1", 0.08)])
    gated = _gated([("a", "A1", 0.09), ("b", "B1", 0.08), ("a", "A2", 0.07)])
    _fake_panel(
        monkeypatch,
        {_key("a", "A1"): _rot(0), _key("b", "B1"): _rot(0), _key("a", "A2"): _rot(1)},
    )
    out = DD.dedup_selection("/qdb", gated, sel, list(DAYS))

    block = DD.dedup_block(out)
    text = json.dumps(block, ensure_ascii=False, allow_nan=False)

    assert block["n_pool"] == 3 and block["n_kept"] == 2
    assert block["reject"] == C.DEDUP_REJECT and block["warn"] == C.DEDUP_WARN
    assert block["dropped"][0]["rho"] == pytest.approx(1.0)
    assert isinstance(text, str)


@pytest.mark.unit
def test_key_of_split_key_往返():
    assert DD.split_key(DD.key_of("l1_factors", "turn_1")) == ("l1_factors", "turn_1")
