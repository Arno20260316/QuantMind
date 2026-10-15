"""因子研究 —— 快照（artifact）读取层（支持多数据集 × 市场）。

数据集：
- ``classic``（默认）：demo 复刻 82 因子，产物由 ``build_factor_research.py`` 写
  ``<market_root>/factor_research/``（长表打分 + 经典目录 catalog.py）；
  **A 股专属**（demo 复刻仅 CN 构建），非 CN 请求抛 ``MarketUnsupported``。
- ``private``：筛选最终保留 327 因子，产物由 ``build_factor_panel_private.py`` 写
  ``<market_root>/factor_research_private/``（宽表打分 + factors.json 目录）。

市场分层（2026-10-16）：全部函数带 ``market``（缺省 ``"CN"``，零行为变化）。
目录一律经 ``resolve_market_root(market)`` 解析 —— **不允许**只按数据集名拼路径，
否则非 CN 会静默读到 CN 的快照。市场键经 ``validate_market`` 严格校验（未知市场
抛错，绝不落 CN）。

本层只读 + 短 TTL 缓存，不触发计算。
"""

from __future__ import annotations

import copy
import json
import math
import threading
import time
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

TTL_SECONDS = 600  # 快照读取缓存（构建脚本重跑后 10 分钟内自动失效）
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()

# 数据集 → 产物目录名
DATASET_DIRS = {"classic": "factor_research", "private": "factor_research_private"}

# 合法数据集取值的唯一出处（须与 DATASET_DIRS 同步，有测试锁死）。
# 对外接口一律用它收口：dataset 会经 artifact_dir 拼进文件系统路径。
FactorDataset = Literal["classic", "private"]


class SnapshotMissing(FileNotFoundError):
    """快照产物不存在（没建过 / 正在重建）。

    单独成类型，是因为路由要靠它把「去点一键计算」这条路指对。原先路由按
    文案里的「缺失」二字判（`_snapshot_guard`），而**数据目录缺失**（6_ml_datasets
    未挂载/未生成）的报错也含这两个字，会被一并翻译成「请点快照-一键计算」——
    用户点了也只是白等一次几分钟的重建，真正的原因（数据没挂上）一个字都没提。
    """


class DedupMatrixMissing(RuntimeError):
    """去重相关矩阵缺失（private 未跑 build_factor_panel_corr.py / classic 无 corr.parquet）。

    与 ``SnapshotMissing`` 分开的原因同前：这不是「点快照-一键计算」能修的，
    路由据此给**各自**的重建指引（409），不把用户指向错误的按钮。
    """


class MarketUnsupported(ValueError):
    """该数据集在某市场不供给（classic 经典库为 A 股专属 demo 复刻）。

    独立成类型（而非文案判）：路由据此译 **422**（请求本身对，市场×数据集组合
    不供给），与「未知数据集」（400）和「快照缺失」（503）三条指引互不串门。
    继承 ``ValueError`` 以便既有捕获 ValueError 的调用面不炸，但路由须先判本类。
    """


def _sanitize(o):
    """NaN/Inf → None（快照 JSON 由 pandas 写出，可能含非有限值；FastAPI 序列化会 500）。"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


def artifact_dir(dataset: str = "classic", market: str = "CN") -> Path:
    """数据集 × 市场 → 产物目录。**fail-closed**：未知 dataset/market 直接抛错。

    这是对外参数进入文件系统路径的唯一入口，原先写的是
    ``DATASET_DIRS.get(dataset, dataset)`` —— 未命中的取值被原样当路径组件，
    而 ``Path`` 保留 ``..``（由内核在 ``open()`` 时解析），于是
    ``?dataset=../../../../etc`` 就能读出库外文件；``POST /build`` 还会
    ``mkdir(parents=True)`` 在库外建目录。合法取值只有 ``DATASET_DIRS`` 的键。

    市场侧同款 fail-closed：``validate_market`` 对未知市场抛错（绝不落 CN），
    ``classic`` 为 A 股专属（非 CN 抛 ``MarketUnsupported`` → 路由 422），
    根解析走 ``resolve_market_root`` 唯一入口（CN 即原 ``resolve_quantdb_dir()``，
    零行为变化）。
    """
    from backend.shared.feature_source import resolve_market_root, validate_market

    try:
        name = DATASET_DIRS[dataset]
    except KeyError:
        raise ValueError(
            f"未知因子数据集 {dataset!r}（合法取值：{'、'.join(sorted(DATASET_DIRS))}）"
        ) from None
    mk = validate_market(market)
    if mk != "CN" and dataset == "classic":
        raise MarketUnsupported(
            f"经典因子库（classic）为 A 股专属 demo 复刻（build_factor_research.py），"
            f"{mk} 市场不供给；本市场请用「私人因子库」数据集"
        )
    return resolve_market_root(mk) / name


def _cached_obj(key: str, loader):
    """按 key 的 TTL 缓存读取（key 需含文件路径与过滤参数）。"""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    obj = loader()
    with _lock:
        _cache[key] = (now, obj)
    return obj


def _file_key(p: Path, prefix: str, extra: str = "") -> str:
    """缓存 key = 路径 + mtime + 参数 —— 构建脚本重跑后旧缓存立即失效（不受 TTL 限制）。"""
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return f"{prefix}:{p}:{mtime}:{extra}"


def panel_stamp(dataset: str = "classic", market: str = "CN") -> str:
    """面板快照标识（路径 + mtime）。

    给上层当缓存键用：重建脚本一落盘，键就变，上层那份重算几十秒的上下文自动作废 ——
    不需要再靠 TTL 猜什么时候过期。
    """
    return _file_key(artifact_dir(dataset, market) / "factor_panel.parquet", "obj")


def load_json(name: str, dataset: str = "classic", market: str = "CN"):
    p = artifact_dir(dataset, market) / name

    def _rd():
        if not p.exists():
            return None
        return _sanitize(json.loads(p.read_text(encoding="utf-8")))

    return _cached_obj(_file_key(p, "json"), _rd)


def load_parquet(
    name: str, dataset: str = "classic", market: str = "CN", **kwargs
) -> pd.DataFrame | None:
    p = artifact_dir(dataset, market) / name

    def _rd():
        if not p.exists():
            return None
        return pd.read_parquet(p, **kwargs)

    return _cached_obj(_file_key(p, "pq", repr(sorted(kwargs.items(), key=str))), _rd)


def metrics(dataset: str = "classic", market: str = "CN") -> dict:
    return load_json("metrics.json", dataset, market) or {
        "leaderboard": [],
        "metrics": {},
        "meta": {},
    }


def factors_meta(dataset: str = "private", market: str = "CN") -> dict | None:
    """私人因子库目录（factors.json：factors/l1_order/l2_order/meta）。"""
    return load_json("factors.json", dataset, market)


def holdings(market: str = "CN") -> dict:
    return load_json("holdings_latest.json", "classic", market) or {
        "date": None,
        "holdings": {},
    }


def ic_table(dataset: str = "classic", market: str = "CN") -> pd.DataFrame | None:
    return load_parquet("ic.parquet", dataset, market)


def corr_table(dataset: str = "classic", market: str = "CN") -> pd.DataFrame | None:
    return load_parquet("corr.parquet", dataset, market)


def dedup_corr(
    dataset: str = "classic", market: str = "CN"
) -> tuple[list[str], np.ndarray] | None:
    """去重相关矩阵 ``(names, 方阵)``；产物缺失返回 None（调用方抛 DedupMatrixMissing）。

    classic：``corr.parquet`` 长表（build_factor_research 生成；覆盖 73/82 名字，
    未覆盖者去重时按覆盖回退必保留）；
    private：``cross_corr.npz``（``backend/scripts/build_factor_panel_corr.py``
    生成，全量因子；面板重建后须重跑该脚本）。
    """
    from backend.services.engine.factor_research import dedup as dedup_lib

    if dataset == "classic":
        df = corr_table(dataset, market)
        if df is None or len(df) == 0:
            return None
        return dedup_lib.matrix_from_pairs(df)
    p = artifact_dir(dataset, market) / "cross_corr.npz"

    def _rd():
        if not p.exists():
            return None
        with np.load(p, allow_pickle=False) as z:
            return [str(s) for s in z["names"]], np.asarray(z["matrix"])

    return _cached_obj(_file_key(p, "npz"), _rd)


def benchmark_table(
    dataset: str = "classic", market: str = "CN"
) -> pd.DataFrame | None:
    """基准净值长表（trade_date, nav, index_code）。CN=沪深300/中证800/中证500；
    非 CN 基准代码由各市场私有面板构建器写入（HK=HSI.HK 等，FUTURES 为空表）。"""
    return load_parquet("benchmarks.parquet", dataset, market)


def panel(dataset: str = "classic", market: str = "CN"):
    """月末名次面板（scorecard.Panel；进程内缓存）。"""
    from backend.services.engine.factor_research import scorecard

    p = artifact_dir(dataset, market) / "factor_panel.parquet"

    def _rd():
        if not p.exists():
            return None
        return scorecard.Panel(pd.read_parquet(p))

    return _cached_obj(_file_key(p, "obj"), _rd)


def stock_snapshot(market: str = "CN") -> pd.DataFrame | None:
    """最新截面个股元数据（名称/行业/市值/PE/PB/近一年日均成交额）。

    数据集无关（股票池元数据同一份）。CN：优先经典目录、私人库回退经典；
    非 CN 只有私人目录（classic 为 A 股专属，不得去碰 classic 路径）。
    """
    from backend.shared.feature_source import validate_market

    if validate_market(market) == "CN":
        df = load_parquet("stock_snapshot.parquet", "classic", "CN")
        if df is not None:
            return df
    return load_parquet("stock_snapshot.parquet", "private", market)


def scores_for(
    codes: list[str], dataset: str = "classic", market: str = "CN"
) -> pd.DataFrame | None:
    """按因子读取月末打分（长表：trade_date/symbol/factor_code/score）。

    classic：长表 parquet 行组过滤；private：宽表按列裁剪 + melt（不缓存，避免大对象驻留）。
    """
    codes = list(codes)
    if dataset != "private":
        return load_parquet(
            "monthly_scores.parquet",
            dataset,
            market,
            filters=[("factor_code", "in", codes)],
        )
    p = artifact_dir("private", market) / "monthly_scores.parquet"
    if not p.exists():
        return None
    import pyarrow.parquet as pq

    names = set(pq.ParquetFile(p).schema.names)
    cols = [c for c in codes if c in names]
    if not cols:
        return pd.DataFrame()
    df = pd.read_parquet(p, columns=["trade_date", "symbol", *cols])
    return df.melt(
        id_vars=["trade_date", "symbol"],
        value_vars=cols,
        var_name="factor_code",
        value_name="score",
    ).dropna(subset=["score"])


def fwd_returns(dataset: str = "classic", market: str = "CN") -> pd.DataFrame | None:
    return load_parquet("fwd_returns.parquet", dataset, market)


_EMPTY_SCREENING: dict = {
    "counts": {
        "candidates": 0,
        "kept": 0,
        "gated_out": 0,
        "deduped": 0,
        "total_considered": 0,
    },
    "kept": [],
    "dropped_gated": [],
    "dropped_duplicate": [],
    "gates": {},
    "cross_corr": "",
    "generated_at": None,
}


def screening(market: str = "CN") -> dict:
    """因子筛选结果（CN 七库联合筛选产物；缺失时返回空结构）。

    非 CN 诚实缺席（该产品为 A 股口径的七库筛选，别的市场不供给）：返回同构
    空结构 + ``available: false`` + ``reason`` —— 前端据此渲染阻断面而不是
    「暂未筛选」（后者会让人等一个永远不会跑的构建）。
    """
    from backend.shared.feature_source import validate_market

    mk = validate_market(market)
    if mk != "CN":
        return {
            **copy.deepcopy(_EMPTY_SCREENING),
            "available": False,
            "market": mk,
            "reason": (
                f"联合筛选为 A 股口径产物（七库筛选），{mk} 市场暂未接入；"
                "可用排行榜的 ICIR/覆盖率门槛做同类过滤"
            ),
        }
    # CN 存量行为逐字不变（含缺产物时的空结构形状）
    return load_json("screening/factor_selection.json", "classic", mk) or copy.deepcopy(
        _EMPTY_SCREENING
    )


def clear_cache() -> None:
    with _lock:
        _cache.clear()
