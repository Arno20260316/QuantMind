"""挖掘数据集注册表（单源，叶子模块：只依赖标准库）。

背景：挖掘数据面（``rd_loop_wrapper._generate_h5_from_parquet``）把 QuantDB
的预计算列并入 daily_pv.h5 供 LLM 因子表达式直接引用（``$<输出列名>``）。
2026-10 之前这份白名单写死在 ``rd_loop_wrapper``（three 组：features_daily /
l1_factors / l2_factors），任务无法选择；现在数据集选择要能**真正控制喂给
LLM 的特征**（移植 P1），因此把映射抽到本模块做单源：

- 提示面（``market_adapters/a_share.py`` 的 ``_l2_referenceable_columns`` 等）
- 数据面（``rd_loop_wrapper._merge_enrich / _merge_l2_factors``）
- 校验面（``alpha_agent.mining_config`` 的请求校验与 UI 目录）

三方共用同一份 key/映射，避免"提示里能引用、h5 里没有"或反向的漂移。

``l2_factors`` 的映射是 ``None``：它的列清单现场读盘（历史教训：parquet
物理列序随日漂移，依赖静态名单会与上游发布漂移），由
``rd_loop_wrapper.l2_factor_columns`` 单源提供。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

#: features_daily 数据集富化列：{源列名: h5 输出名}（输出列加 "$" 前缀后
#: 供 LLM 引用，如 ``$rsi_14``）。与 ``_ENRICH_FEATURES_DAILY`` 同源。
FEATURES_DAILY: dict[str, str] = {
    "rsi_14": "rsi_14",
    "macd_hist": "macd_hist",
    "vol_atr_14": "atr_14",
    "beta_20": "beta_20",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "ps_ttm": "ps_ttm",
    "dividend_rate": "div_yield",
    "total_mv": "total_mv",
    "float_mv": "float_mv",
    "net_profit_ttm": "np_ttm",
}

#: l1_factors 数据集富化列（量价衍生/资金流/筹码/风格）。
L1_FACTORS: dict[str, str] = {
    "turn_5": "turn_5",
    "turn_20": "turn_20",
    "turn_z_20": "turn_z_20",
    "amt_net_flow_5": "netflow_5",
    "amt_net_flow_20": "netflow_20",
    "mfi_14": "mfi_14",
    "obv_slope_20": "obv_slope",
    "vol_parkinson_20": "parkinson_20",
    "tech_bb_width": "bb_width",
    "tech_bb_pos": "bb_pos",
    "tech_adx_14": "adx_14",
    "tech_max_drawdown_20": "maxdd_20",
    "fun_bp": "bp",
    "fun_ep": "ep",
    "fun_roe": "roe",
    "fun_peg": "peg",
    "fun_np_growth": "np_growth",
    "chip_profit_ratio_20": "chip_profit_20",
    "style_idio_vol_20": "idio_vol_20",
    "ind_strength_20": "ind_strength",
    "concept_hot_score": "concept_hot",
}

#: L2 微观结构因子数据集 key（列清单动态，见模块 docstring）。
L2_DATASET = "l2_factors"

#: 数据集 key → 富化映射；``None`` = 动态列集（现场读盘）。
ENRICH_BY_DATASET: dict[str, dict[str, str] | None] = {
    "features_daily": FEATURES_DAILY,
    "l1_factors": L1_FACTORS,
    L2_DATASET: None,
}

#: 可选的挖掘数据集（顺序即 UI 展示顺序；新增数据集 = 在此登记 + 在
#: rd_loop_wrapper 数据面接上对应合并逻辑）。
SUPPORTED_DATASETS: tuple[str, ...] = ("features_daily", "l1_factors", L2_DATASET)

#: 中文标签（前端目录展示；未知 key 回落原始 key 的约定在调用方）。
DATASET_LABELS: dict[str, str] = {
    "features_daily": "日线因子宽表",
    "l1_factors": "L1 因子库",
    L2_DATASET: "L2 微观结构因子",
}

#: 数据集短码（h5 缓存文件名用，见 rd_loop_wrapper._h5_cache_name）。
DATASET_SHORT_NAMES: dict[str, str] = {
    "features_daily": "fd",
    "l1_factors": "l1",
    L2_DATASET: "l2",
}


def resolve_datasets(selected: Sequence[str] | None) -> tuple[str, ...]:
    """把请求里的数据集选择归一为登记过的 key 元组（**登记序**、去重）。

    空 / None = **全选**（与迁移前行为一致——默认喂全套特征）。顺序取登记序
    而非提交序：选择集合是 h5 缓存的构成因子（按数据集分文件），规范化顺序
    保证「同集合、不同勾选顺序」命中同一份缓存。未登记 key 抛 ``ValueError``
    （消息列出可选项，UI 直接透出）。
    """
    if not selected:
        return SUPPORTED_DATASETS
    cleaned = {str(raw or "").strip() for raw in selected}
    cleaned.discard("")
    unknown = sorted(k for k in cleaned if k not in SUPPORTED_DATASETS)
    if unknown:
        raise ValueError(
            f"未登记的挖掘数据集：{', '.join(unknown)}；"
            f"可选：{', '.join(SUPPORTED_DATASETS)}"
        )
    return tuple(k for k in SUPPORTED_DATASETS if k in cleaned) or SUPPORTED_DATASETS


def datasets_from_env(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """读任务环境 ``QM_MINING_DATASETS``（逗号分隔）→ 登记序数据集元组。

    数据面按此选择富化列（``rd_loop_wrapper``）。未设 = 全选（迁移前行为）；
    非法 key 抛 ``ValueError``——选择集决定喂给 LLM 的列清单，静默容忍会让
    「选了 A 却拿到 B」变成哑故障。
    """
    raw = (os.environ if env is None else env).get("QM_MINING_DATASETS", "")
    return resolve_datasets([p for p in str(raw or "").split(",") if p.strip()])
