"""因子语义分类（taxonomy）测试：层级优先级、降级、富化语义（全 hermetic）+ 真库不变量。

覆盖：
- 五级链条优先级：手工表 > name_map > formulas > 前缀 > 词根；
- 缺失/损坏降级：词表文件缺 → 该层跳过、catalog 面不抛异常；
- 中文名采纳口径：仅确含汉字（Alpha101_1 这类非中文名不采纳）；
- enrich：返回新 dict、不覆盖已有真名、category/category_name 两字段；
- 兜底：未知码/空码 → other（如实展示，不硬猜）。
"""

from __future__ import annotations

import json

import pytest

from backend.services.engine.factor_research import taxonomy as tx


def _write_vocab(root, *, name_map: dict | None, formulas: list | None) -> None:
    """在临时数据根下铺词表（None = 不写该文件）。"""
    (root / "quantdb").mkdir(parents=True, exist_ok=True)
    (root / "quantdb" / "keep").touch()  # resolve_quantdb_dir 要求目录非空
    if name_map is not None:
        d = root / "factor1300"
        d.mkdir(parents=True, exist_ok=True)
        (d / "name_map.json").write_text(
            json.dumps(name_map, ensure_ascii=False), encoding="utf-8"
        )
    if formulas is not None:
        d = root / "factor_defs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "formulas.json").write_text(
            json.dumps(formulas, ensure_ascii=False), encoding="utf-8"
        )


@pytest.fixture()
def vocab(tmp_path, monkeypatch):
    """标准词表：name_map 覆盖 f13_/Alpha101_，formulas 覆盖 feat_。

    ``amount_ma_5`` 故意在 name_map 里标成「形态因子」（pattern），用来证明
    手工表层先于词表层——真值应是 flow（成交额族）。
    """
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "quantdb"))
    _write_vocab(
        tmp_path,
        name_map={
            "f13_0001": {"seq": 1, "name_cn": "市盈率TTM", "category": "估值因子"},
            "Alpha101_001": {
                "seq": 711,
                "name_cn": "Alpha101_1",
                "category": "Alpha因子",
            },
            "amount_ma_5": {"seq": 9, "name_cn": "五日均额", "category": "形态因子"},
            "mom_conflict": {"seq": 9, "name_cn": "冲突用例", "category": "规模因子"},
        },
        formulas=[
            {"name": "feat_demo_pricevol", "category": "日频量价"},
            {"name": "feat_demo_other", "category": "其他/未分类"},
            {"name": "feat_demo_micro", "category": "高频/盘口微结构"},
        ],
    )


# ── 层级优先级 ────────────────────────────────────────────────────


@pytest.mark.unit
def test_exact_table_beats_vocab_sources(vocab):
    # name_map 里标了「形态因子」，手工表层必须赢（成交额族 → flow）
    assert tx.classify_code("amount_ma_5") == "flow"


@pytest.mark.unit
def test_name_map_layer_and_unified_mapping(vocab):
    assert tx.classify_code("f13_0001") == "valuation"
    assert tx.classify_code("Alpha101_001") == "alpha_classic"


@pytest.mark.unit
def test_name_map_beats_prefix(vocab):
    # mom_ 前缀规则是 momentum，但 name_map 标了「规模因子」——词表优先
    assert tx.classify_code("mom_conflict") == "style"


@pytest.mark.unit
def test_formulas_layer_and_source_other_not_guessed(vocab):
    assert tx.classify_code("feat_demo_pricevol") == "price_volume"
    assert tx.classify_code("feat_demo_micro") == "micro"
    # 源词表标「其他/未分类」的按原样落 other（不拿词根硬猜）
    assert tx.classify_code("feat_demo_other") == "other"


@pytest.mark.unit
def test_prefix_layer(vocab):
    assert tx.classify_code("mom_kdj_d") == "momentum"  # 前缀先于 kdj 词根
    assert tx.classify_code("vol_std_60") == "volatility"
    assert tx.classify_code("turn_1") == "price_volume"
    assert tx.classify_code("amt_close_pos") == "flow"
    assert tx.classify_code("lu_days_20") == "concept"
    assert tx.classify_code("mg_net_ratio_5") == "flow"


@pytest.mark.unit
def test_keyword_layer(vocab):
    assert tx.classify_code("rd_amihud_20d") == "price_volume"
    assert tx.classify_code("rd_mfi_14") == "flow"
    assert tx.classify_code("rd_overnight_gap") == "momentum"
    assert tx.classify_code("rd_ma_align_5_10_20") == "pattern"


# ── 降级与兜底 ────────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_vocab_files_degrade(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "quantdb"))
    _write_vocab(tmp_path, name_map=None, formulas=None)
    # 词表全缺：前缀/词根层照常，f13 无来源 → other（不算错，是缺词表）
    assert tx.classify_code("mom_kdj_d") == "momentum"
    assert tx.classify_code("f13_0001") == "other"


@pytest.mark.unit
def test_corrupt_vocab_file_degrades_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_QUANTDB_DATA_DIR", str(tmp_path / "quantdb"))
    _write_vocab(tmp_path, name_map=None, formulas=None)
    d = tmp_path / "factor1300"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name_map.json").write_text("{ 坏 json", encoding="utf-8")
    assert tx.classify_code("f13_0001") == "other"  # 读失败 → 降级，不抛


@pytest.mark.unit
def test_unknown_and_empty_codes_fall_to_other(vocab):
    assert tx.classify_code("zzz_unknown_code") == "other"
    assert tx.classify_code("") == "other"
    assert tx.classify_code("中文码") == "other"
    # 输出恒为合法类别 id
    for code in ("", "zzz", "f13_0001", "feat_demo_other"):
        assert tx.classify_code(code) in tx.CATEGORY_BY_ID


# ── 中文名与富化 ──────────────────────────────────────────────────


@pytest.mark.unit
def test_chinese_name_only_when_cjk(vocab):
    assert tx.chinese_name("f13_0001") == "市盈率TTM"
    # Alpha101_1 不含汉字 → 不采纳（保持代码原样更有辨识度）
    assert tx.chinese_name("Alpha101_001") is None
    assert tx.chinese_name("not_in_map") is None


@pytest.mark.unit
def test_enrich_returns_new_dict_and_preserves_real_name(vocab):
    src = {"code": "f13_0001", "name_cn": "f13_0001", "l1": "factor1300"}
    out = tx.enrich(src)
    assert out is not src and src == {
        "code": "f13_0001",
        "name_cn": "f13_0001",
        "l1": "factor1300",
    }
    assert out["category"] == "valuation" and out["category_name"] == "估值"
    assert out["name_cn"] == "市盈率TTM"  # name_cn == code → 补中文名

    named = {"code": "f13_0001", "name_cn": "自定义名"}
    assert tx.enrich(named)["name_cn"] == "自定义名"  # 真名不覆盖

    bare = {"code": "gtja_042"}
    out2 = tx.enrich(bare)
    assert out2["category"] == "alpha_classic" and out2.get("name_cn") is None


@pytest.mark.unit
def test_category_for_unknown_id_falls_back(vocab):
    assert tx.category_for("zzz_unknown").id == "other"
    assert tx.category_for("f13_0001").name == "估值"


# ── 真库不变量（只读；数据不在则跳过） ─────────────────────────────


@pytest.mark.integration
def test_real_catalog_all_classified():
    import os
    from pathlib import Path

    root = Path(os.getenv("QM_QUANTDB_DATA_DIR", "/data/quantdb"))
    f = root / "factor_research_private" / "factors.json"
    if not f.exists():
        pytest.skip(f"私人因子库快照不存在: {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    factors = data.get("factors", data) if isinstance(data, dict) else data
    assert len(factors) > 1000
    dist: dict[str, int] = {}
    for e in factors:
        cid = tx.classify_code(e["code"])
        assert cid in tx.CATEGORY_BY_ID, e["code"]
        dist[cid] = dist.get(cid, 0) + 1
    # 兜底桶占比守卫：分类质量退化时（如词表路径失效）会显著抬升
    assert dist.get("other", 0) / len(factors) < 0.25
    # 至少 6 个语义类有量（词表整层全丢时这里会炸）
    assert sum(1 for k, v in dist.items() if k != "other" and v >= 20) >= 6
