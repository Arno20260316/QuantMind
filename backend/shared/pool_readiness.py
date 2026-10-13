"""候选池就绪判据：五段串联，只回答「**今天该有的池，现在到哪一步了**」。

为什么值得单独一个模块：这条链断了三次，每次的**可见面**都不一样——2026-10-13 实锤是
alpha_library 缺 dt=20261012（02:30 内存闸门跳过）→ CUSTOM 重建 fail-closed 拒写 →
推理 6 批全 `主模型维度门禁未通过` → 池文件根本没生成 → 决策轮 09:35 起 `rows=0`，
`l2.pool_not_member` 拦下全部开仓，而**第一个告警是开盘之后**才发的。

三个读者（宿主盘前告警、后台状态卡、排障的人）各写一份判据必然漂移，故判据只此一份：

* :func:`assess` —— 纯函数，零 IO，吃五个事实吐一份报告（测试与调用方都好摆事实）；
* :func:`collect` —— 从磁盘/PG 收那五个事实（注入探针，便于替换）；
* :func:`pool_snapshot` —— 池文件的事实（**文件名与两代 schema 由
  :mod:`backend.shared.decision_context_source` 决定**，不在这里再写一份后缀表）。

两条容易被写坏的判据：

1. **「断链」≠「按设计清空」**。方向闸判看空 → 池文件**在**、``candidates: []``，这是正常
   行为（10-12 复盘「强烈看空 -4.8/11」跑出健康链也是这个形状）。把它报成故障会天天误报，
   值班的人很快就会开始忽略这条告警——见 :func:`is_bearish_direction`。
2. **缺就缺，不许谎报**。上游没有日K分区时，下游段一律不得说「就绪」（旧实现里
   「取不到就当 0/就绪」的形态在本仓反复出事）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: 段序**是契约**：状态卡与告警文案按它排版，顺序变了两个读者都会错位（测试锁死）。
STAGE_KEYS: tuple[str, ...] = (
    "kline",
    "alpha_library",
    "custom_dataset",
    "inference",
    "pool",
)

_STAGE_LABELS: dict[str, str] = {
    "kline": "日K分区",
    "alpha_library": "alpha 因子库",
    "custom_dataset": "自定义因子集",
    "inference": "当日推理批次",
    "pool": "候选池",
}

#: 「方向闸清空」的判据词。与 ``decision_round_alerts._BEARISH_MARKER`` 同口径——
#: 那是消费侧，这是生产侧，两侧说的是同一件事，词必须一样。
BEARISH_MARKER = "看空"

#: 候选池文件名两代。**只作兜底**：正常路径走
#: :func:`backend.shared.decision_context_source.pool_path`（那里是唯一出处）。
_POOL_SUFFIXES = ("_agent_picks.json", "_picks.json")


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    """池文件的事实（``exists`` 与 ``readable`` 分开：读不动≠不存在，处置不同）。"""

    exists: bool
    readable: bool
    rows: int
    direction: str
    source: str


@dataclass(frozen=True, slots=True)
class StageReport:
    """一段的结论。``detail`` 是给人看的原话（含实测日期），不写「异常」这种空话。"""

    key: str
    label: str
    ok: bool
    detail: str
    by_design: bool = False
    value: str | None = None


@dataclass(frozen=True, slots=True)
class Readiness:
    """五段报告。``blocked_stage`` = **第一个**不就绪的段（None = 链通）。

    ``target_*`` 三项说的是「目标日是谁定的」：日期判错时（日历不可用退回工作日启发式，
    长假会被判成交易日）报告面上必须看得出来，否则人会按错误的日期去追一个根本不该
    存在的池。纯函数 :func:`assess` 不填它们（调用方给的就是事实），:func:`collect` 填。
    """

    target: str
    kline_dt: str | None
    ok: bool
    by_design: bool
    blocked_stage: str | None
    stages: tuple[StageReport, ...]
    target_source: str = ""
    target_degraded: bool = False

    @property
    def blocked_label(self) -> str:
        if not self.blocked_stage:
            return ""
        return _STAGE_LABELS.get(self.blocked_stage, self.blocked_stage)

    def stage(self, key: str) -> StageReport | None:
        for s in self.stages:
            if s.key == key:
                return s
        return None


def is_bearish_direction(text: object) -> bool:
    """方向文案是否判空（「看空」子串覆盖「强烈看空」「复盘判强烈看空（…）」）。"""
    return BEARISH_MARKER in str(text or "")


@dataclass(frozen=True, slots=True)
class TargetDay:
    """「今天该有哪一交易日的池」+ **是谁回答的**（日历来源必须落在报告面上）。

    ``degraded=True`` 表示交易日历读不到、退回了「工作日启发式」——清明/国庆这种
    周中长假会被它判成交易日，于是盘前会按错误的日期去找池文件。这不是「静默降级
    所以无所谓」：告警文案与状态卡都要把来源显示出来，人才知道该不该信这一次判定。
    """

    day: str
    source: str
    degraded: bool


def target_trade_day(today: date | None = None) -> TargetDay:
    """今天（``today``）该有哪一交易日的候选池。

    池文件按 ``buy_date``（=信号日=目标交易日）命名，而数据截止日是**上一个**交易日：
    周五凌晨产出的是**周一**开仓那份池。所以判据不能拿「今天」直接去查数据日，得先
    按交易日历推到目标日。

    三个来源按可用性依次取（**两个运行时各有各的日历实现，本仓既有事实**）：

    1. ``backend.shared.trading_calendar``（容器：exchange_calendars，权威）；
    2. ``scripts.trading_cal``（宿主：JSON 日历 + QuantDB 分区，宿主没装 xcal）；
    3. 工作日启发式（**degraded**：有长假就会错，见 :class:`TargetDay`）。
    """
    d = today or date.today()

    try:
        from backend.shared.trading_calendar import (  # noqa: PLC0415
            is_trading_day_xcal,
            next_trading_day_xcal,
        )

        is_td = is_trading_day_xcal("SSE", d)
        if is_td is not None:
            nxt = d if is_td else next_trading_day_xcal("SSE", d)
            if nxt is not None:
                return TargetDay(nxt.strftime("%Y%m%d"), "exchange_calendars", False)
    except Exception as exc:  # noqa: BLE001 - 换下一个来源
        logger.debug("trading_calendar 不可用：%s", exc)

    try:
        import importlib  # noqa: PLC0415

        tc = importlib.import_module("scripts.trading_cal")
        if tc.is_trading_day(d):
            return TargetDay(d.strftime("%Y%m%d"), "scripts.trading_cal", False)
        return TargetDay(
            tc.next_trading_day(d).strftime("%Y%m%d"), "scripts.trading_cal", False
        )
    except Exception as exc:  # noqa: BLE001 - 换兜底
        logger.warning("交易日本地日历不可用，退回工作日启发式：%s", exc)

    # 兜底：工作日启发式（周六/周日顺延到周一）。**有长假会判错**，故标 degraded。
    nxt = d
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return TargetDay(nxt.strftime("%Y%m%d"), "weekday-heuristic", True)


def latest_partition(root: str | Path | None) -> str | None:
    """``root`` 下 ``dt=YYYYMMDD`` 分区的最新日期串；目录不在/无分区 → ``None``。"""
    if root is None:
        return None
    d = Path(root)
    if not d.is_dir():
        return None
    dates = [p.name[3:] for p in d.iterdir() if p.is_dir() and p.name.startswith("dt=")]
    return max(dates) if dates else None


def _pool_path_in(base: Path, day: str) -> Path | None:
    for suffix in _POOL_SUFFIXES:
        p = base / f"{day}{suffix}"
        if p.exists():
            return p
    return None


def pool_snapshot(day: str, *, reports_dir: str | Path | None = None) -> PoolSnapshot:
    """读池文件 → :class:`PoolSnapshot`（**不抛异常**：告警脚本失败一次就是漏一次喊人）。

    ``reports_dir`` 不给时走 :func:`~backend.shared.decision_context_source.pool_path`
    —— 报告根（``QM_REPORTS_DIR``）与后缀表由那一处决定，本函数不另立一套。
    """
    if reports_dir is None:
        try:
            from backend.shared.decision_context_source import pool_path

            path = pool_path(day)
        except Exception as exc:  # noqa: BLE001 - 探针失败按「无法判定」处理
            logger.warning("池文件路径解析失败（day=%s）：%s", day, exc)
            return PoolSnapshot(False, False, 0, "", "")
    else:
        path = _pool_path_in(Path(reports_dir) / "stock_picks", day)

    if path is None:
        return PoolSnapshot(False, False, 0, "", "")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("池文件读不动 %s：%s", path, exc)
        return PoolSnapshot(True, False, 0, "", str(path))
    if not isinstance(doc, dict):
        return PoolSnapshot(True, False, 0, "", str(path))

    # 行数组键两代（与 load_pool_doc 同口径）：只认 ``picks`` 会把在产的池静默读成空池。
    picks = doc.get("picks")
    if picks is None:
        picks = doc.get("candidates")
    rows = len(picks) if isinstance(picks, list) else 0
    md = doc.get("market_direction")
    direction = ""
    if isinstance(md, dict):
        direction = str(md.get("direction") or "")
    if not direction:
        # 老池（night_pool_agent 代）没有 market_direction，方向只写在 gate 文案里。
        direction = str(doc.get("gate") or "")
    return PoolSnapshot(True, True, rows, direction, str(path))


def _cmp_detail(label: str, dt: str | None, kline_dt: str | None) -> str:
    return f"最新 dt={dt or '—'}，日K dt={kline_dt or '—'}"


def assess(
    *,
    target: str,
    kline_dt: str | None,
    alpha_dt: str | None,
    custom_dt: str | None,
    inference_dates: Iterable[str],
    pool: PoolSnapshot,
) -> Readiness:
    """五个事实 → 五段报告（**纯函数**：不读盘、不连库、不看时钟）。

    日期一律 ``YYYYMMDD`` 字符串，同口径可直接比大小。
    """
    dates = {str(d) for d in (inference_dates or ())}
    need = kline_dt  # 上游基准：日K 落到哪天，今天就该有哪个 data_date 的池

    stages: list[StageReport] = []

    # ① 日K分区
    stages.append(
        StageReport(
            key="kline",
            label=_STAGE_LABELS["kline"],
            ok=need is not None,
            detail=f"最新 dt={need or '—'}",
            value=need,
        )
    )

    # ② alpha 因子库（10-13 的第一根因就在这一段）
    alpha_ok = bool(need) and bool(alpha_dt) and str(alpha_dt) >= str(need)
    stages.append(
        StageReport(
            key="alpha_library",
            label=_STAGE_LABELS["alpha_library"],
            ok=alpha_ok,
            detail=_cmp_detail("alpha", alpha_dt, need),
            value=alpha_dt,
        )
    )

    # ③ 自定义因子集（CUSTOM 重建产物，推理的直读源）
    custom_ok = bool(need) and bool(custom_dt) and str(custom_dt) >= str(need)
    stages.append(
        StageReport(
            key="custom_dataset",
            label=_STAGE_LABELS["custom_dataset"],
            ok=custom_ok,
            detail=_cmp_detail("custom", custom_dt, need),
            value=custom_dt,
        )
    )

    # ④ 当日推理批次：按 data_trade_date **精确**匹配（pick_candidates 就是这么查的）
    has_batch = bool(need) and str(need) in dates
    if not need:
        inf_detail = "无日K分区，不知道要查哪个数据日"
    elif has_batch:
        inf_detail = f"data_trade_date={need} 的已完成批次：有"
    else:
        inf_detail = (
            f"data_trade_date={need} 的已完成批次：无"
            f"（近 14 天共 {len(dates)} 个数据日有批）"
        )
    stages.append(
        StageReport(
            key="inference",
            label=_STAGE_LABELS["inference"],
            ok=has_batch,
            detail=inf_detail,
            value=need if has_batch else None,
        )
    )

    # ⑤ 候选池
    if not pool.exists:
        pool_ok, by_design, detail = False, False, f"缺 {target} 的候选池文件"
    elif not pool.readable:
        pool_ok, by_design, detail = False, False, "候选池文件存在但读不动"
    elif pool.rows > 0:
        pool_ok, by_design, detail = True, False, f"{pool.rows} 只候选"
    elif is_bearish_direction(pool.direction):
        pool_ok, by_design, detail = True, True, f"方向闸清空（{pool.direction}）"
    else:
        pool_ok, by_design, detail = (
            False,
            False,
            f"0 只候选且非方向闸清空（direction={pool.direction or '—'}）",
        )
    stages.append(
        StageReport(
            key="pool",
            label=_STAGE_LABELS["pool"],
            ok=pool_ok,
            detail=detail,
            by_design=by_design,
            value=str(pool.rows),
        )
    )

    # 链是**串行**的：下游产物不可能比自己的输入新。上游有一段不就绪，下游一律
    # 判不就绪（``detail`` 保留实测原话并注明成因）——否则「日K 分区都不在了，候选池
    # 却显示 ✓」这种自相矛盾的报告会让人不再相信这张表。
    out: list[StageReport] = []
    upstream_blocked = False
    for s in stages:
        if upstream_blocked:
            out.append(
                replace(
                    s, ok=False, by_design=False, detail=f"{s.detail}（上游未就绪）"
                )
            )
        else:
            out.append(s)
            upstream_blocked = not s.ok

    blocked = next((s.key for s in out if not s.ok), None)
    return Readiness(
        target=str(target),
        kline_dt=need,
        ok=blocked is None,
        by_design=blocked is None and any(s.by_design for s in out),
        blocked_stage=blocked,
        stages=tuple(out),
    )


# ── 事实收集（磁盘 / PG）─────────────────────────────────────────────

_DATA_ROOT_ENV = "QM_QUANTDB_DATA_DIR"
_CUSTOM_ROOT_ENV = "QM_QUANTCUSTOM_DATA_DIR"


def _repo_data_dir(name: str) -> Path:
    # 宿主：本文件在 <repo>/backend/shared/ → parents[2] 即仓库根；data/ 是软链。
    return Path(__file__).resolve().parents[2] / "data" / name


def quantdb_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.getenv(_DATA_ROOT_ENV)
    return Path(env) if env else _repo_data_dir("quantdb")


def quantcustom_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.getenv(_CUSTOM_ROOT_ENV)
    return Path(env) if env else _repo_data_dir("quantcustom")


ALPHA_LIBRARY_REL = "6_ml_datasets/alpha_library"
KLINE_REL = "1_kline_data/daily_forward"
CUSTOM_L1_REL = "6_ml_datasets/l1_factors"


def collect(
    *,
    target: str | None = None,
    data_root: str | Path | None = None,
    custom_root: str | Path | None = None,
    reports_dir: str | Path | None = None,
    inference_probe: Callable[[str], Iterable[str]] | None = None,
    today: date | None = None,
) -> Readiness:
    """从磁盘 + PG 收事实 → :func:`assess`。

    ``target`` 不给时按 :func:`target_trade_day` 推（今天该有哪一交易日的池），
    并把「谁定的这个日期」写进报告面。

    ``inference_probe(kline_dt) -> Iterable[str]``：给定数据日，返回有**已完成**推理批的
    日期集合。不给或探针抛异常 → 该段按「不可用」记（``ok=False``、detail 说明原因），
    **不猜成通过**。
    """
    if target is None:
        td = target_trade_day(today)
        target, src, degraded = td.day, td.source, td.degraded
    else:
        target, src, degraded = str(target), "explicit", False

    qdb = quantdb_root(data_root)
    cst = quantcustom_root(custom_root)
    kline_dt = latest_partition(qdb / KLINE_REL)
    alpha_dt = latest_partition(qdb / ALPHA_LIBRARY_REL)
    custom_dt = latest_partition(cst / CUSTOM_L1_REL)

    dates: frozenset[str] = frozenset()
    if inference_probe is not None and kline_dt:
        try:
            dates = frozenset(str(d) for d in inference_probe(kline_dt))
        except Exception as exc:  # noqa: BLE001 - 探不到 ≠ 就绪
            logger.warning("推理批次探针失败（data=%s）：%s", kline_dt, exc)

    return replace(
        assess(
            target=target,
            kline_dt=kline_dt,
            alpha_dt=alpha_dt,
            custom_dt=custom_dt,
            inference_dates=dates,
            pool=pool_snapshot(target, reports_dir=reports_dir),
        ),
        target_source=src,
        target_degraded=degraded,
    )


def pg_inference_probe(
    *, connect: Callable[[], object] | None = None
) -> Callable[[str], set[str]]:
    """宿主侧推理批次探针（psycopg2，环境契约同 ``postmarket_pipeline._pg_connect``）。

    返回 ``probe(kline_dt) -> {data_trade_date}``：查 ``qm_model_inference_runs`` 里
    ``status='completed'`` 的批（**不筛模型**：任一模型有批，池就能算出来——
    ``pick_candidates`` 的取数口径就是按 data_trade_date 取最大 prediction 日）。
    """
    import psycopg2  # noqa: PLC0415 宿主已装；延迟导入让不碰 PG 的调用方零依赖

    def _default_connect():
        return psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "quantmind"),
            password=os.getenv("POSTGRES_PASSWORD", "quantmind2026"),
            dbname=os.getenv("POSTGRES_DB", "quantmind"),
            connect_timeout=5,
        )

    def probe(_kline_dt: str) -> set[str]:
        conn = (connect or _default_connect)()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT data_trade_date::text FROM qm_model_inference_runs "
                    "WHERE status = 'completed' AND data_trade_date >= now()::date - 14"
                )
                return {str(r[0]).replace("-", "") for r in cur.fetchall()}
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关连接失败不影响已取到的结果
                pass

    return probe
