"""港股聪明钱 · 数据面（quanthk 原始分区 + 产物目录；三件套之 IO 层）。

- 原始面：ccass_top50 / hsgt_south / daily_forward 分区（只读；采集由港股
  市场同步链维护——本特性无独立采集脚本，构建只读）；
- 产物面：``/data/market-analysis/hk_smart_money``（events/ flows/ scores/
  分区 + seats.parquet + latest/consensus/trends/evidence/meta）。

目录解析：quanthk 侧复用 ``quanthk_hub._resolve_quanthk_data_dir``
（env > /data/quanthk > 项目根 data/quanthk）；产物侧三段解析照 A 股
smart_money（env QM_MARKET_SNAPSHOT_DIR > /data/market-analysis > cwd 兜底）。
⚠ 改产物路径必须同时核对 4 处（本模块 / build 脚本 / API / celery wrapper）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from backend.services.engine.data_platform.quanthk_hub import _resolve_quanthk_data_dir

log = logging.getLogger(__name__)

CCASS_REL = "2_base_sector/ccass_top50"
SOUTH_REL = "2_base_sector/hsgt_south"
KLINE_REL = "1_kline_data/daily_forward"

#: ccass_top50 读取列（裁剪；holding_percentage 供档案展示）
CCASS_COLUMNS: tuple[str, ...] = (
    "stock_code",
    "stock_name",
    "participant_id",
    "participant_name",
    "holding_quantity",
)
SOUTH_COLUMNS: tuple[str, ...] = (
    "symbol",
    "stock_name",
    "holding_quantity",
    "holding_percentage",
)

_DATE_RE = re.compile(r"^dt=(\d{8})$")


# ---------------------------------------------------------------- 目录解析


def data_dir() -> Path:
    """quanthk 数据根（env QM_QUANTHK_DATA_DIR > /data/quanthk > 项目根）。"""
    return _resolve_quanthk_data_dir()


def _list_dates(rel: str) -> tuple[str, ...]:
    """dt=YYYYMMDD 分区日期升序（要求分区内含 data.parquet，防半写目录）。"""
    root = data_dir() / rel
    if not root.is_dir():
        return ()
    out: list[str] = []
    for name in os.listdir(root):
        m = _DATE_RE.match(name)
        if m and (root / name / "data.parquet").is_file():
            out.append(m.group(1))
    return tuple(sorted(out))


# 分区清单进程内缓存（脚本短生命周期；API 不列分区，只用产物）
@lru_cache(maxsize=8)
def ccass_dates() -> tuple[str, ...]:
    return _list_dates(CCASS_REL)


@lru_cache(maxsize=8)
def south_dates() -> tuple[str, ...]:
    return _list_dates(SOUTH_REL)


@lru_cache(maxsize=8)
def kline_dates() -> tuple[str, ...]:
    return _list_dates(KLINE_REL)


def latest_ccass_date() -> str | None:
    d = ccass_dates()
    return d[-1] if d else None


def latest_south_date() -> str | None:
    d = south_dates()
    return d[-1] if d else None


def latest_kline_date() -> str | None:
    d = kline_dates()
    return d[-1] if d else None


# ---------------------------------------------------------------- 原始面读取


def load_ccass(dt: str) -> pd.DataFrame:
    """单日 CCASS top50 明细（去重 (participant, stock)，仅 .HK，qty int64）。

    分区内无 dt 列（hive 路径承载）；重复行按 keep=last（同步端 merge 语义）。
    """
    path = data_dir() / CCASS_REL / f"dt={dt}" / "data.parquet"
    df = pd.read_parquet(path, columns=list(CCASS_COLUMNS))
    df["stock_code"] = df["stock_code"].astype(str)
    df = df[df["stock_code"].str.endswith(".HK")]
    df = df.drop_duplicates(["participant_id", "stock_code"], keep="last")
    df["holding_quantity"] = df["holding_quantity"].astype("int64")
    return df


def load_south(dt: str) -> pd.DataFrame:
    """单日南向持股（hsgt_south dt= 主布局；旧每股文件布局已停更，禁读）。

    口径：holding_percentage==0 但 quantity>1e6 = 披露缺失 → NaN（防假清仓，
    与 quanthk_feed._south_cur_sanitized 同规）。
    """
    path = data_dir() / SOUTH_REL / f"dt={dt}" / "data.parquet"
    df = pd.read_parquet(path, columns=list(SOUTH_COLUMNS))
    df["symbol"] = df["symbol"].astype(str)
    bad = df["holding_percentage"].fillna(0.0).eq(0.0) & (
        df["holding_quantity"].fillna(0) > 1_000_000
    )
    df.loc[bad, "holding_percentage"] = np.nan
    df = df.drop_duplicates("symbol", keep="last")
    df["holding_quantity"] = df["holding_quantity"].astype("int64")
    return df


def load_prices(kline_dates_: Sequence[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """收盘/成交额宽表（index=归一化交易日 DatetimeIndex，columns=symbol 后缀式）。

    只读传入分区（显式路径列表，不 glob ``**``——daily_forward 为原始不复权价，
    涨跌与事后收益一律由 close 比值构成）。
    """
    paths = [
        str(data_dir() / KLINE_REL / f"dt={d}" / "data.parquet") for d in kline_dates_
    ]
    if not paths:
        empty = pd.DataFrame()
        return empty, empty
    con = duckdb.connect()
    try:
        df = con.execute(
            "SELECT symbol, time, close, amount FROM read_parquet($paths)",
            {"paths": paths},
        ).fetchdf()
    finally:
        con.close()
    df["trade_date"] = pd.to_datetime(df["time"]).dt.normalize()
    close = df.pivot_table(
        index="trade_date", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    amount = df.pivot_table(
        index="trade_date", columns="symbol", values="amount", aggfunc="last"
    ).sort_index()
    return close, amount


def forward_matrices(
    close: pd.DataFrame,
) -> dict[int, tuple[pd.DataFrame, pd.DataFrame]]:
    """h → (fwd_ret, fwd_exc)。防前视口径唯一实现（细案 §3.3，HK 先例）：

    ``fwd(t,h) = close[t+h]/close[t+1] − 1``（T 盘后信号 → T+1 收盘入场），
    clip(−0.5, +1.0) 防仙股；``exc = fwd − 当日全体有效中位``（分数制，
    展示换算 bp/百分比由前端或调用方完成）。
    """
    out: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for h in (5, 10, 20):
        ret = (close.shift(-h) / close.shift(-1) - 1).clip(-0.5, 1.0)
        ret = ret.replace([np.inf, -np.inf], np.nan)
        exc = ret.sub(ret.median(axis=1), axis=0)
        out[h] = (ret, exc)
    return out


def south_qty_matrix(close_index: pd.DatetimeIndex, symbols: pd.Index) -> pd.DataFrame:
    """南向持股量宽表（cal × symbol）。

    口径：hsgt_south 是**完整持仓表**（捕获日缺席 = 当日无南向持仓 = 0，与
    ccass top50 的截断语义不同）——捕获日缺席按 0 覆盖，非捕获日 ffill
    （差分自动把缺口累积到下一捕获日）；首次捕获之前记 NaN。
    """
    frames: list[pd.Series] = []
    for d in south_dates():
        s = load_south(d).set_index("symbol")["holding_quantity"]
        s = s.reindex(symbols).fillna(0.0).astype("float64")
        s.name = pd.Timestamp(d)
        frames.append(s)
    if not frames:
        return pd.DataFrame(index=close_index, columns=symbols, dtype="float64")
    wide = pd.DataFrame(frames)  # index=捕获日，columns=symbol
    return wide.reindex(index=close_index, columns=symbols).ffill()


# ---------------------------------------------------------------- 产物面解析


def product_dir() -> Path:
    """市场分析产物根下的 hk_smart_money/（env > 容器 /data > cwd，同 A 股约定）。

    容器判定兜底不可省：celery-worker 容器未注入 QM_MARKET_SNAPSHOT_DIR，
    只靠 env/cwd 两段时 worker 产物会落到容器可写层、API（读 /data）永远 404
    且容器重建即蒸发（A 股 smart_money/flow_evidence 同类教训）。
    """
    env = os.getenv("QM_MARKET_SNAPSHOT_DIR")
    if env:
        return Path(env).expanduser() / "hk_smart_money"
    if Path("/app").exists() and not os.environ.get("QUANTMINT_HOST_MODE"):
        return Path("/data/market-analysis/hk_smart_money")
    return Path(os.getcwd()) / "data" / "market-analysis" / "hk_smart_money"


def load_product_json(name: str) -> dict | list | None:
    """读产物 JSON（latest/consensus/trends/evidence/meta）；缺失或损坏返回 None。"""
    path = product_dir() / f"{name}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s 读取失败：%s", path, exc)
        return None


def write_json_atomic(name: str, payload: Any) -> Path:
    """产物 JSON 原子写（allow_nan=False：NaN 漏入即崩溃，绝不产出非法 JSON）。"""
    path = product_dir() / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 产物分区


def _product_dates(kind: str) -> tuple[str, ...]:
    root = product_dir() / kind
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            m.group(1)
            for name in os.listdir(root)
            if (m := _DATE_RE.match(name)) and (root / name / "data.parquet").is_file()
        )
    )


def event_dates() -> tuple[str, ...]:
    return _product_dates("events")


def flow_dates() -> tuple[str, ...]:
    return _product_dates("flows")


def score_dates() -> tuple[str, ...]:
    return _product_dates("scores")


def _read_many(
    paths: Sequence[str], columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """DuckDB 批量读分区（显式路径列表；columns 为内部常量白名单）。"""
    if not paths:
        return pd.DataFrame()
    cols = ", ".join(columns) if columns else "*"
    con = duckdb.connect()
    try:
        return con.execute(
            f"SELECT {cols} FROM read_parquet($paths)", {"paths": list(paths)}
        ).fetchdf()
    finally:
        con.close()


def load_events(
    dates: Sequence[str] | None = None, columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """事件仓（可按日期子集/列裁剪；dt 列 = YYYYMMDD 字符串）。"""
    use = list(dates) if dates is not None else list(event_dates())
    paths = [
        str(product_dir() / "events" / f"dt={d}" / "data.parquet")
        for d in use
        if (product_dir() / "events" / f"dt={d}" / "data.parquet").is_file()
    ]
    return _read_many(paths, columns)


def load_flows(
    dates: Sequence[str] | None = None, columns: Sequence[str] | None = None
) -> pd.DataFrame:
    """逐日类型净流（6 类型长格式）。"""
    use = list(dates) if dates is not None else list(flow_dates())
    paths = [
        str(product_dir() / "flows" / f"dt={d}" / "data.parquet")
        for d in use
        if (product_dir() / "flows" / f"dt={d}" / "data.parquet").is_file()
    ]
    return _read_many(paths, columns)


def load_universe(dates: Sequence[str] | None = None) -> pd.DataFrame:
    """逐日 CCASS 在册股票（[dt, symbol]；信号定义域与 0 填充的判据）。"""
    use = list(dates) if dates is not None else list(event_dates())
    frames: list[pd.DataFrame] = []
    for d in use:
        p = product_dir() / "events" / f"dt={d}" / "universe.parquet"
        if p.is_file():
            u = pd.read_parquet(p)
            u["dt"] = d
            frames.append(u)
    if not frames:
        return pd.DataFrame(columns=["dt", "symbol"])
    return pd.concat(frames, ignore_index=True)


def load_holdings(dates: Sequence[str] | None = None) -> pd.DataFrame:
    """逐日类型持仓（[dt, symbol, category, qty_sum, n_seats]；CCASS 5 类）。

    信号定义域（当日或前日 qty>0）与 overview 类型持仓市值的唯一数据源。
    """
    use = list(dates) if dates is not None else list(event_dates())
    paths = [
        str(product_dir() / "events" / f"dt={d}" / "holdings.parquet")
        for d in use
        if (product_dir() / "events" / f"dt={d}" / "holdings.parquet").is_file()
    ]
    return _read_many(paths)


def load_scores(dt: str) -> pd.DataFrame | None:
    path = product_dir() / "scores" / f"dt={dt}" / "data.parquet"
    if not path.is_file():
        return None
    return pd.read_parquet(path)


def load_seats() -> pd.DataFrame | None:
    path = product_dir() / "seats.parquet"
    if not path.is_file():
        return None
    return pd.read_parquet(path)
