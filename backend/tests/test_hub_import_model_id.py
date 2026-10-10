"""广场导入本地 model_id 生成规则测试（ASCII 约束）。

背景（2026-10-09）：`_slugify_hub_name` 用 `str.isalnum()` 判字符，中文的
isalnum() 恒为 True 被原样保留 → 导入出的 model_id 形如
`mdl_cn_hub_T_10_LightGBM_选股_d899b0_0792`，滚动训练派生配方处
（`model_recipe._SAFE_MODEL_ID = [A-Za-z0-9._-]{1,128}`）直接拒绝：
「非法 model_id」。

口径：model_id 只允许 ASCII 字母数字与 `._-`；中文名去 `metadata.display_name`
保留展示，不进 ID。生成结果必须能过 `_SAFE_MODEL_ID.fullmatch`。
"""

from __future__ import annotations

import re

from backend.services.api.routers.hub_proxy import (
    _build_import_model_id,
    _build_legacy_import_model_id,
    _slugify_hub_name,
)

# 与 model_recipe._SAFE_MODEL_ID 同口径（滚动训练派生配方的校验器）
SAFE_MODEL_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def test_slugify_drops_cjk_keeps_ascii():
    assert _slugify_hub_name("T+10 LightGBM 选股") == "T_10_LightGBM"
    assert _slugify_hub_name("XGBoost基线模型") == "XGBoost"


def test_slugify_all_cjk_returns_empty():
    # 纯中文名 slug 为空 → 调用方回退 legacy 规则（ID 仍须 ASCII）
    assert _slugify_hub_name("选股模型") == ""


def test_import_model_id_is_ascii_when_name_has_cjk():
    model_id = _build_import_model_id(
        hub_model_id="mdl_lightgbm_20260912_a4d899b0",
        market="CN",
        hub_name="T+10 LightGBM 选股",
    )
    assert SAFE_MODEL_ID.fullmatch(model_id), model_id
    assert model_id.startswith("mdl_cn_hub_T_10_LightGBM_d899b0_")


def test_import_model_id_falls_back_ascii_when_name_all_cjk():
    model_id = _build_import_model_id(
        hub_model_id="mdl_xgboost_20260913_b955ffb4",
        market="CN",
        hub_name="选股模型",
    )
    assert SAFE_MODEL_ID.fullmatch(model_id), model_id


def test_legacy_import_model_id_is_ascii():
    model_id = _build_legacy_import_model_id(
        hub_model_id="mdl_lightgbm_20260912_a4d899b0", market="CN"
    )
    assert SAFE_MODEL_ID.fullmatch(model_id), model_id
