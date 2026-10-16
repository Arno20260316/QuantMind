"""聪明钱数据面读取（纯读 + mtime 缓存；api/engine 可安全 import）。

分层与口径（细案 docs/市场分析_聪明钱追踪与选股_细案_20261015.md §2）：
- 原始层（采集脚本 backend/scripts/smart_money_sync.py 落盘；代码一律后缀式）：
  {QuantDB}/2_base_sector/smart_money/*.parquet
- 既有面（QuantDB）：
  - 两融 margin_trading/dt=*：**单位万元**（2026-10-15 抽查 000001.SZ
    finance_balance=437799.16 ≈ 43.8 亿元）。老分区（≤20260917）repay 槽位存在
    列错位（实测见 l1_factors_dev/compute_dev.py），本模块只用 finance_balance /
    finance_net（两代均正确）；time 列三种写法混存 → format="mixed"。
  - 北向 hsgt_north/quarter=*：季度披露（2024-08-19 起无日频）；holding_percentage
    为**分数**（0.0103=1.03%）；report_date=季末、query_date=快照日。
  - 行情 daily_forward/dt=*：前复权（qfq；禁 daily_backward）。早年负价损坏
    已于 2026-09-22 修复。
  - instrument_detail.parquet：Name / rs_hyname（行业名，通达信口径）。
- 产物层（build_smart_money.py 落盘）：
  {QM_MARKET_SNAPSHOT_DIR 或 容器 /data 或 {cwd}/data}/market-analysis/smart_money/

前向收益列 fwd_ret_* 是东财「上榜后表现」展示列（封存规则见细案 §2.2）：
本模块原样读出交由展示面，信号侧靠白名单取列保证其不可入信号。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any
from collections.abc import Callable

import pandas as pd

from backend.shared.quantdb_paths import resolve_quantdb_dir, resolve_quantdb_subdir
from backend.shared.stock_utils import StockCodeUtil

log = logging.getLogger(__name__)

RAW_DATASETS = (
    "lhb_detail",
    "lhb_inst",
    "lhb_branch",
    "block_trade",
    "survey",
    "dzjy_detail",  # 大宗明细（机构专用接盘柱用；2026-10-16 起）
    "lhb_seat",  # 龙虎榜席位明细（席位构成/分类；2026-10-16 起）
)

# ---------------------------------------------------------------- 通用缓存

#: {(key): (签名, 值)}；签名=相关文件 mtime 元组，mtime 变即失效（同步脚本原子替换）
_cache: dict[str, tuple[tuple[float, ...], Any]] = {}


def _cached(key: str, paths: list[Path], loader: Callable[[], Any]) -> Any:
    # 签名含路径：同名文件在不同根（测试 tmp / 便携包切换）互不串缓存
    sig: list[tuple[str, float]] = []
    for p in paths:
        try:
            sig.append((str(p), p.stat().st_mtime))
        except OSError:
            sig.append((str(p), -1.0))
    sig_t = tuple(sig)
    hit = _cache.get(key)
    if hit is not None and hit[0] == sig_t:
        return hit[1]
    val = loader()
    _cache[key] = (sig_t, val)
    return val


# ---------------------------------------------------------------- 原始层（自建）


def raw_dir() -> Path:
    return resolve_quantdb_subdir("2_base_sector", "smart_money")


def load_raw(name: str) -> pd.DataFrame:
    """单张原始表；缺失返回空 DataFrame（不做任何插补）。

    trade_date 统一转 datetime64（落盘为 'YYYY-MM-DD' 字符串）。
    """
    if name not in RAW_DATASETS:
        raise ValueError(f"未知数据集 {name!r}（可选：{RAW_DATASETS}）")
    path = raw_dir() / f"{name}.parquet"

    def _load() -> pd.DataFrame:
        if not path.is_file():
            log.warning("聪明钱原始表缺失：%s（先跑 smart_money_sync.py）", path)
            return pd.DataFrame()
        df = pd.read_parquet(path)
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        return df

    return _cached(f"raw:{name}", [path], _load)


def load_sync_meta() -> dict:
    path = raw_dir() / "_sync_meta.json"

    def _load() -> dict:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("_sync_meta.json 读取失败：%s", exc)
            return {}

    return _cached("raw:meta", [path], _load)


def raw_freshness() -> dict[str, dict]:
    """各原始表新鲜度（供概览卡与 meta.json）：last_date/rows/fetched_at。"""
    meta = load_sync_meta()
    out: dict[str, dict] = {}
    for name in RAW_DATASETS:
        m = meta.get(name, {})
        out[name] = {
            "last_date": m.get("last_date"),
            "rows": m.get("rows"),
            "fetched_at": m.get("fetched_at"),
        }
    return out


# ---------------------------------------------------------------- 产物层（build 落盘）


def product_dir() -> Path:
    """市场分析产物根下的 smart_money/（env > 容器 /data > {cwd}/data，同 flow_evidence 约定）。

    容器判定兜底不可省：celery-worker 容器未注入 ``QM_MARKET_SNAPSHOT_DIR``
    （compose 只给了 quantmind 服务），只靠 env/cwd 两段时 worker 任务的产物会
    落到容器可写层 ``{cwd}/data/…``——API（读 /data）永远 404，且容器一重建
    产物即蒸发（flow_evidence 同类教训）。**改路径解析必须同时核对**：
    datasets.product_dir / build_smart_money / API smart_money / celery wrapper。
    """
    env = os.getenv("QM_MARKET_SNAPSHOT_DIR")
    if env:
        return Path(env).expanduser() / "smart_money"
    if Path("/app").exists() and not os.environ.get("QUANTMINT_HOST_MODE"):
        return Path("/data/market-analysis/smart_money")
    return Path(os.getcwd()) / "data" / "market-analysis" / "smart_money"


def load_product_json(name: str) -> dict | list | None:
    """读产物 JSON（latest/trends/evidence/meta）；缺失或损坏返回 None。"""
    path = product_dir() / f"{name}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s 读取失败：%s", path, exc)
        return None


def score_dates() -> list[str]:
    """可用评分快照日期（YYYYMMDD 升序）。"""
    root = product_dir() / "scores"
    out = [p.name[3:] for p in root.glob("dt=*") if p.name[3:].isdigit()]
    return sorted(out)


def load_scores(date: str) -> pd.DataFrame | None:
    """单日评分快照；缺失返回 None。"""
    path = product_dir() / "scores" / f"dt={date}" / "data.parquet"
    if not path.is_file():
        return None
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        log.warning("评分快照 %s 读取失败：%s", path, exc)
        return None


# ---------------------------------------------------------------- 既有面（QuantDB）


def _partition_max_date(subdir: tuple[str, ...], prefix_len: int = 3) -> str | None:
    """dt=*/ 分区最大日期名（YYYYMMDD 字符串）；无分区返回 None。"""
    root = resolve_quantdb_subdir(*subdir)
    best: str | None = None
    for p in root.glob("dt=*"):
        name = p.name[prefix_len:]
        if name.isdigit() and (best is None or name > best):
            best = name
    return best


def latest_margin_date() -> str | None:
    return _partition_max_date(("2_base_sector", "margin_trading"))


def latest_kline_date() -> str | None:
    return _partition_max_date(("1_kline_data", "daily_forward"))


def margin_daily(start: str | date | pd.Timestamp, end: str | date | pd.Timestamp | None = None) -> pd.DataFrame:
    """两融日频（**万元**）：[trade_date, symbol, finance_balance, finance_net]。

    只用 finance_balance / finance_net —— 老分区列错位只影响 repay 槽位，
    这两列两代均正确（恒等式 finance_net == finance_buy − repay 逐行成立）。
    time 三种写法混存 → format="mixed"（否则 reindex 后静默全 NaN）。
    """
    root = resolve_quantdb_subdir("2_base_sector", "margin_trading")
    floor = pd.Timestamp(start).strftime("%Y%m%d")
    ceil = pd.Timestamp(end).strftime("%Y%m%d") if end is not None else None
    frames: list[pd.DataFrame] = []
    cols = ["symbol", "time", "finance_balance", "finance_net"]
    for f in sorted(root.glob("dt=*/data.parquet")):
        dt = f.parent.name[3:]
        if not dt.isdigit() or dt < floor or (ceil is not None and dt > ceil):
            continue
        try:
            frames.append(pd.read_parquet(f, columns=cols))
        except (OSError, ValueError) as exc:  # 单分区坏不拖垮整体（对应日缺失）
            log.warning("两融分区 %s 读取失败：%s", f.parent.name, exc)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "symbol", *cols[2:]])
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], format="mixed")
    return df.rename(columns={"time": "trade_date"})


def north_quarterly() -> pd.DataFrame:
    """北向季度持仓（2025Q1→）：quarter/report_date/symbol/stock_name/
    holding_quantity/holding_percentage（分数）。"""
    root = resolve_quantdb_subdir("2_base_sector", "hsgt_north")

    def _load() -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for f in sorted(root.glob("quarter=*/data.parquet")):
            q = f.parent.name.split("=", 1)[1]
            try:
                s = pd.read_parquet(
                    f,
                    columns=[
                        "symbol",
                        "stock_name",
                        "holding_quantity",
                        "holding_percentage",
                        "report_date",
                    ],
                )
            except (OSError, ValueError) as exc:
                log.warning("北向分区 %s 读取失败：%s", f.parent.name, exc)
                continue
            s = s.assign(quarter=q)
            frames.append(s)
        if not frames:
            return pd.DataFrame(
                columns=[
                    "quarter",
                    "report_date",
                    "symbol",
                    "stock_name",
                    "holding_quantity",
                    "holding_percentage",
                ]
            )
        out = pd.concat(frames, ignore_index=True)
        out["report_date"] = pd.to_datetime(
            out["report_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        return out.sort_values(["quarter", "symbol"]).reset_index(drop=True)

    return _cached("north", [root], _load)


def north_delta() -> pd.DataFrame:
    """每股最新季末 vs 上一季末的持仓占比差（分数差）。

    列：symbol/stock_name/quarter/report_date/pct/pct_prev/delta。
    只有一季数据时 delta 全 NaN（披露性缺失，绝不补 0）。
    """
    q = north_quarterly()
    if q.empty:
        return pd.DataFrame(
            columns=["symbol", "stock_name", "quarter", "report_date", "pct", "pct_prev", "delta"]
        )
    quarters = sorted(q["quarter"].unique())
    latest = quarters[-1]
    cur = q[q["quarter"] == latest].set_index("symbol")
    out = pd.DataFrame(
        {
            "stock_name": cur["stock_name"],
            "report_date": cur["report_date"],
            "pct": cur["holding_percentage"],
        }
    ).assign(quarter=latest)
    if len(quarters) >= 2:
        prev = (
            q[q["quarter"] == quarters[-2]]
            .set_index("symbol")["holding_percentage"]
            .reindex(out.index)
        )
        out["pct_prev"] = prev
        out["delta"] = out["pct"] - prev
    else:
        out["pct_prev"] = pd.NA
        out["delta"] = pd.NA
    return out.reset_index().rename(columns={"index": "symbol"})


def latest_quarter() -> dict | None:
    """最新季度摘要：{quarter, report_date}（无数据返回 None）。"""
    q = north_quarterly()
    if q.empty:
        return None
    latest = q["quarter"].max()
    sub = q[q["quarter"] == latest]
    return {"quarter": latest, "report_date": str(sub["report_date"].iloc[0])}


def trading_calendar(
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
) -> pd.DatetimeIndex:
    """daily_forward 分区名给出的交易日历（升序去重；只读目录名+time 列）。

    比 close_matrix 轻：构建端只需要日历，不需要价格面板。
    """
    root = resolve_quantdb_subdir("1_kline_data", "daily_forward")
    floor = pd.Timestamp(start).strftime("%Y%m%d")
    ceil = pd.Timestamp(end).strftime("%Y%m%d")
    days: list[pd.Timestamp] = []
    for p in sorted(root.glob("dt=*")):
        dt = p.name[3:]
        if not dt.isdigit() or dt < floor or dt > ceil:
            continue
        days.append(pd.Timestamp(dt))
    return pd.DatetimeIndex(sorted(set(days)))


def close_matrix(
    start: str | date | pd.Timestamp,
    end: str | date | pd.Timestamp,
    column: str = "close",
) -> pd.DataFrame:
    """daily_forward 前复权宽表 {index=trade_date, columns=symbol 后缀式}。

    逐分区按目录名裁剪（不做 duckdb 全 glob：本函数只在验证/构建脚本里用，
    显式裁剪更稳）。同 (symbol,time) 重复行保留最后一条。
    """
    root = resolve_quantdb_subdir("1_kline_data", "daily_forward")
    floor = pd.Timestamp(start).strftime("%Y%m%d")
    ceil = pd.Timestamp(end).strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []
    for f in sorted(root.glob("dt=*/data.parquet")):
        dt = f.parent.name[3:]
        if not dt.isdigit() or dt < floor or dt > ceil:
            continue
        frames.append(pd.read_parquet(f, columns=["symbol", "time", column]))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).drop_duplicates(subset=["symbol", "time"])
    return df.pivot_table(index="time", columns="symbol", values=column, aggfunc="last").sort_index()


# ---------------------------------------------------------------- 名称 / 行业


def _instrument_detail() -> pd.DataFrame:
    path = resolve_quantdb_subdir(
        "2_base_sector", "instrument_detail", "instrument_detail.parquet"
    )

    def _load() -> pd.DataFrame:
        if not path.is_file():
            log.warning("instrument_detail.parquet 缺失：%s", path)
            return pd.DataFrame(columns=["Symbol", "Name", "rs_hyname"])
        return pd.read_parquet(path, columns=["Symbol", "Name", "rs_hyname"])

    return _cached("instrument_detail", [path], _load)


def name_map() -> pd.Series:
    """symbol（后缀式）→ 证券名称。"""
    inst = _instrument_detail()
    if inst.empty:
        return pd.Series(dtype=str)
    s = inst["Name"].astype(str)
    s.index = inst["Symbol"].astype(str)
    return s


def industry_map() -> pd.Series:
    """symbol（后缀式）→ 行业名（rs_hyname，通达信口径；缺失为 NaN 由调用方兜底）。"""
    inst = _instrument_detail()
    if inst.empty:
        return pd.Series(dtype=str)
    s = inst["rs_hyname"].astype(str).replace({"": pd.NA, "nan": pd.NA})
    s.index = inst["Symbol"].astype(str)
    return s


# ---------------------------------------------------------------- 个股事件时间线


def _sym(symbol: str) -> str:
    code = StockCodeUtil.to_suffix(symbol)
    return code or symbol


def event_timeline(symbol: str, limit: int = 20, lookback_days: int = 365) -> list[dict]:
    """近 `lookback_days` 内该股聪明钱事件（5 表聚合，日期降序，截断 limit）。

    每项含 type/label/date + 各表事实列。lhb 项额外带 fwd_ret_*（东财前向收益，
    **仅展示**，前端须标注「历史回溯统计（非预测）」，绝不可入信号）。
    """
    sym = _sym(symbol)
    floor = pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    events: list[dict] = []

    def _recent(name: str) -> pd.DataFrame:
        df = load_raw(name)
        if df.empty or "symbol" not in df.columns:
            return df
        sub = df[(df["symbol"] == sym) & (df["trade_date"] >= floor)]
        return sub.sort_values("trade_date", ascending=False)

    for _, r in _recent("lhb_detail").iterrows():
        events.append(
            {
                "type": "lhb",
                "label": "龙虎榜",
                "date": r["trade_date"].strftime("%Y-%m-%d"),
                "reason": s_or_none(r.get("reason")),
                "interpretation": s_or_none(r.get("interpretation")),
                "close": _f(r.get("close")),
                "pct_chg": _f(r.get("pct_chg")),
                "net_buy": _f(r.get("lhb_net_buy")),
                "net_ratio": _f(r.get("net_ratio")),
                "fwd_ret_1d": _f(r.get("fwd_ret_1d")),
                "fwd_ret_2d": _f(r.get("fwd_ret_2d")),
                "fwd_ret_5d": _f(r.get("fwd_ret_5d")),
                "fwd_ret_10d": _f(r.get("fwd_ret_10d")),
            }
        )
    for _, r in _recent("lhb_inst").iterrows():
        events.append(
            {
                "type": "inst",
                "label": "机构席位",
                "date": r["trade_date"].strftime("%Y-%m-%d"),
                "reason": s_or_none(r.get("reason")),
                "inst_buy": _f(r.get("inst_buy")),
                "inst_sell": _f(r.get("inst_sell")),
                "inst_net_buy": _f(r.get("inst_net_buy")),
                "inst_net_ratio": _f(r.get("inst_net_ratio")),
                "inst_buy_seats": _f(r.get("inst_buy_seats")),
                "inst_sell_seats": _f(r.get("inst_sell_seats")),
            }
        )
    for _, r in _recent("block_trade").iterrows():
        events.append(
            {
                "type": "block_trade",
                "label": "大宗交易",
                "date": r["trade_date"].strftime("%Y-%m-%d"),
                "deal_price": _f(r.get("deal_price")),
                "close": _f(r.get("close")),
                "premium": _f(r.get("premium")),
                "amount": _f(r.get("amount")),
                "amount_ratio": _f(r.get("amount_ratio")),
                "n_trades": _f(r.get("n_trades")),
            }
        )
    for _, r in _recent("survey").iterrows():
        events.append(
            {
                "type": "survey",
                "label": "机构调研",
                "date": r["trade_date"].strftime("%Y-%m-%d"),
                "inst_count": _f(r.get("inst_count")),
                "reception_type": s_or_none(r.get("reception_type")),
                "reception_place": s_or_none(r.get("reception_place")),
            }
        )
    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:limit]


def _f(v: Any) -> float | None:
    """numpy/NaN → JSON 安全 float|None（缺失一律 None，绝不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if pd.notna(f) else None


def s_or_none(v: Any) -> str | None:
    """pandas 标量 → JSON 安全 str|None（字符串事实列的唯一过闸口）。

    parquet 可空字符串列（pandas ``string`` dtype）读出的 pd.NA 直进 JSON 会炸
    响应序列化（PydanticSerializationError 500，2026-10-16 stock/SH603668
    reception_place 实案）；np.nan 走 float 路径会落成非法 JSON ``NaN``、
    经 str() 又会落成 ``"nan"`` 显示。已字符串化的 NA 拼写一并清零。
    注意：**不能**用 ``v or ""`` 兜底——pd.NA 布尔求值抛 TypeError。
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    s = str(v).strip()
    return None if s in ("", "nan", "NaN", "NaT", "<NA>") else s
