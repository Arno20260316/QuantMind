"""通达信桥数据采集落库契约——全部 tdx_* 表结构 + 启动期自愈（与 `db_init.sql` 同口径）。

背景
----
Windows 通达信桥（``tools/bridge-windows/``，HTTP :8550）经 ``/api/v1/tdx/call``
暴露 tqcenter 专业数据面（GP/BK/SC/GO 专业序列、more_info 全字段、股本/分红/可转债/
新股/ETF追踪、股票/板块/日历引用面）。本模块是采集链（
``backend/services/live_trading/services/tdx_ingest/``）的**唯一建表出处**。

一次建全（含尚未接入的表）
--------------------------
批次 1 只采集 P0 数据集，但本模块**一次性建全部 20 张表**（数据 13 + 引用 5 +
运维 2）——包括批次 2 才接入的 ``tdx_fin_report``/``tdx_kzz_daily``/``tdx_trackzs_daily``
等。理由：两份 DDL 手抄件（本模块 + ``db_init.sql``）的漂移风险随**修改次数**增长，
批 2 再翻一次 = 多一轮"改一抄一"的机会；表建着不用零成本（空表无 IO）。

DDL 两份手抄件（同 decision_ledger_contract 的路线，理由见那份 docstring）
--------------------------------------------------------------------------
* 全新安装 → ``backend/shared/db_init.sql``（同一份 DDL，带注释头）；
* 老库自愈 → 本模块的 ``ensure_tdx_ingest_tables*``，由 trade 服务启动期调用。
有测试守着（``backend/tests/test_tdx_ingest_contract.py``）。

口径红线（沿用全库约定）
------------------------
* 代码双列：``stock_code`` = 后缀式（桥原生 ``600036.SH``，采集直写）；
  ``symbol`` = 前缀式（``SH600036``，平台查询口径），由 :func:`to_rows_symbol` 派生；
* 瞬时时间一律 ``TIMESTAMPTZ`` + ``NOW()``（aware UTC，ADR-0003）；
* ``trade_date``/``end_date``/``sg_date`` 等业务日一律 ``VARCHAR(8)`` ``YYYYMMDD``
  （与 QuantDB 分区口径一致，避免 DATE 类型在各层时区转换中漂移）；
* 数值列 ``DOUBLE PRECISION``；桥返回的空串/``"--"``/缺失 → NULL（**不填 0**，
  0 有真实语义）；原始 JSON 一律留 ``raw`` JSONB 全量供对账。
* 长表三张（gpjy/bkjy/scjy）带 ``value_text`` 文本兜底列：GP24/GP34/GP40 是时间型、
  GP41 是日期型字段，数值列放不下时原样落此列——**不因解析不了数字而丢值**。
"""

from __future__ import annotations

import logging
from collections.abc import Collection

logger = logging.getLogger(__name__)

#: 全部表名（数据 + 引用 + 运维），契约测试与自愈共用唯一出处。
TABLES: tuple[str, ...] = (
    # —— 数据表（13）——
    "tdx_gpjy_long",  # 个股专业序列（GP01–GP46 长表，行=股票×日×字段）
    "tdx_bkjy_long",  # 板块专业序列（BK5–BK19 长表）
    "tdx_scjy_long",  # 市场级序列（SC01–SC42 长表，无股票维度）
    "tdx_gp_one_data",  # 一致预期（GO 快照，值含数值/文本/日期混合）
    "tdx_zdt_daily",  # 涨跌停状态（15 字段宽表；挂起中，见数据集注册表）
    "tdx_more_info_daily",  # 收盘 more_info（88 字段；热列 + raw JSONB）
    "tdx_fin_report",  # 财务全科目（FN 系列；JSONB 行式，批 2 接入）
    "tdx_gb_info",  # 股本（流通/总股本）
    "tdx_divid_factors",  # 分红送配（全史，一次调用即全量）
    "tdx_kzz_daily",  # 可转债行情/条款（批 2 接入）
    "tdx_ipo_info",  # 新股/新债发行（批 2 接入）
    "tdx_trackzs_daily",  # ETF 追踪指数（批 2 接入）
    "tdx_pricevol_daily",  # 全市场收盘快照（pricevol 单次全 A）
    # —— 引用表（5）——
    "tdx_stock_list_snapshot",  # 股票清单日快照（market 分类）
    "tdx_sector_list",  # 板块清单日快照
    "tdx_sector_members",  # 板块成分（批 2 接入）
    "tdx_stock_info_snapshot",  # 个股基础信息快照（轮转；批 2 接入）
    "tdx_trading_dates",  # 交易日历
    # —— 运维表（2）——
    "tdx_ingest_cursor",  # 采集水位/状态（断点续跑 + blocked 记忆）
    "tdx_ingest_runs",  # 每档每集运行台账（面板与验收的证据面）
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tdx_gpjy_long (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    field_code  VARCHAR(16) NOT NULL,
    v1          DOUBLE PRECISION,
    v2          DOUBLE PRECISION,
    value_text  TEXT NOT NULL DEFAULT '',
    units       VARCHAR(16) NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date, field_code)
);
CREATE INDEX IF NOT EXISTS idx_tdx_gpjy_long_date ON tdx_gpjy_long (trade_date);
CREATE INDEX IF NOT EXISTS idx_tdx_gpjy_long_symbol ON tdx_gpjy_long (symbol, trade_date);
CREATE TABLE IF NOT EXISTS tdx_bkjy_long (
    bk_code     VARCHAR(16) NOT NULL,
    bk_name     VARCHAR(64) NOT NULL DEFAULT '',
    trade_date  VARCHAR(8)  NOT NULL,
    field_code  VARCHAR(16) NOT NULL,
    v1          DOUBLE PRECISION,
    v2          DOUBLE PRECISION,
    value_text  TEXT NOT NULL DEFAULT '',
    units       VARCHAR(16) NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bk_code, trade_date, field_code)
);
CREATE INDEX IF NOT EXISTS idx_tdx_bkjy_long_date ON tdx_bkjy_long (trade_date);
CREATE TABLE IF NOT EXISTS tdx_scjy_long (
    trade_date  VARCHAR(8)  NOT NULL,
    field_code  VARCHAR(16) NOT NULL,
    v1          DOUBLE PRECISION,
    v2          DOUBLE PRECISION,
    value_text  TEXT NOT NULL DEFAULT '',
    units       VARCHAR(16) NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, field_code)
);
CREATE TABLE IF NOT EXISTS tdx_gp_one_data (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    field_code  VARCHAR(16) NOT NULL,
    value_num   DOUBLE PRECISION,
    value_text  TEXT NOT NULL DEFAULT '',
    units       VARCHAR(16) NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date, field_code)
);
CREATE INDEX IF NOT EXISTS idx_tdx_gp_one_data_date ON tdx_gp_one_data (trade_date);
CREATE INDEX IF NOT EXISTS idx_tdx_gp_one_data_symbol ON tdx_gp_one_data (symbol, trade_date);
CREATE TABLE IF NOT EXISTS tdx_zdt_daily (
    stock_code        VARCHAR(16) NOT NULL,
    symbol            VARCHAR(16) NOT NULL,
    trade_date        VARCHAR(8)  NOT NULL,
    zdt_status_now    INTEGER,
    zdt_status_ori    INTEGER,
    first_time_zt     VARCHAR(16) NOT NULL DEFAULT '',
    last_time_zt      VARCHAR(16) NOT NULL DEFAULT '',
    last_open_time_zt VARCHAR(16) NOT NULL DEFAULT '',
    first_time_dt     VARCHAR(16) NOT NULL DEFAULT '',
    last_time_dt      VARCHAR(16) NOT NULL DEFAULT '',
    last_open_time_dt VARCHAR(16) NOT NULL DEFAULT '',
    open_times_zt     INTEGER,
    open_times_dt     INTEGER,
    fd_vol_max_zt     DOUBLE PRECISION,
    fd_vol_max_dt     DOUBLE PRECISION,
    vol_zt            DOUBLE PRECISION,
    days_zt_series    INTEGER,
    days_dt_series    INTEGER,
    zt_status_days    TEXT NOT NULL DEFAULT '',
    zt_recent_days    TEXT NOT NULL DEFAULT '',
    raw               JSONB NOT NULL DEFAULT '{}',
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_zdt_daily_date ON tdx_zdt_daily (trade_date);
CREATE TABLE IF NOT EXISTS tdx_more_info_daily (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    zaf         DOUBLE PRECISION,
    f_hsl       DOUBLE PRECISION,
    zjl         DOUBLE PRECISION,
    zjl_hb      DOUBLE PRECISION,
    ltsz        DOUBLE PRECISION,
    zsz         DOUBLE PRECISION,
    zt_price    DOUBLE PRECISION,
    dt_price    DOUBLE PRECISION,
    hq_date     VARCHAR(8) NOT NULL DEFAULT '',
    raw         JSONB NOT NULL DEFAULT '{}',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_more_info_daily_date ON tdx_more_info_daily (trade_date);
CREATE INDEX IF NOT EXISTS idx_tdx_more_info_daily_symbol ON tdx_more_info_daily (symbol, trade_date);
CREATE TABLE IF NOT EXISTS tdx_fin_report (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    report_date VARCHAR(8)  NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_fin_report_date ON tdx_fin_report (report_date);
CREATE TABLE IF NOT EXISTS tdx_gb_info (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    end_date    VARCHAR(8)  NOT NULL,
    ltgb        DOUBLE PRECISION,
    zgb         DOUBLE PRECISION,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, end_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_gb_info_date ON tdx_gb_info (end_date);
CREATE TABLE IF NOT EXISTS tdx_divid_factors (
    stock_code   VARCHAR(16) NOT NULL,
    symbol       VARCHAR(16) NOT NULL,
    ex_date      VARCHAR(8)  NOT NULL,
    div_type     VARCHAR(8)  NOT NULL DEFAULT '',
    bonus        DOUBLE PRECISION,
    allo_price   DOUBLE PRECISION,
    share_bonus  DOUBLE PRECISION,
    allotment    DOUBLE PRECISION,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, ex_date, div_type)
);
CREATE INDEX IF NOT EXISTS idx_tdx_divid_factors_date ON tdx_divid_factors (ex_date);
CREATE TABLE IF NOT EXISTS tdx_kzz_daily (
    kzz_code      VARCHAR(16) NOT NULL,
    symbol        VARCHAR(16) NOT NULL,
    trade_date    VARCHAR(8)  NOT NULL,
    name          VARCHAR(64) NOT NULL DEFAULT '',
    hs_code       VARCHAR(16) NOT NULL DEFAULT '',
    hs_name       VARCHAR(64) NOT NULL DEFAULT '',
    kzz_now       DOUBLE PRECISION,
    ag_now        DOUBLE PRECISION,
    cur_rate      DOUBLE PRECISION,
    expire_yield  DOUBLE PRECISION,
    force_redeem  DOUBLE PRECISION,
    put_back      DOUBLE PRECISION,
    put_date      VARCHAR(8) NOT NULL DEFAULT '',
    end_date      VARCHAR(8) NOT NULL DEFAULT '',
    raw           JSONB NOT NULL DEFAULT '{}',
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kzz_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_kzz_daily_date ON tdx_kzz_daily (trade_date);
CREATE TABLE IF NOT EXISTS tdx_ipo_info (
    ipo_code    VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    name        VARCHAR(64) NOT NULL DEFAULT '',
    sg_code     VARCHAR(16) NOT NULL DEFAULT '',
    sg_date     VARCHAR(8)  NOT NULL DEFAULT '',
    max_sg      DOUBLE PRECISION,
    pe_issue    DOUBLE PRECISION,
    sg_price    DOUBLE PRECISION,
    ipo_type    INTEGER,
    raw         JSONB NOT NULL DEFAULT '{}',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ipo_code, sg_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_ipo_info_sg_date ON tdx_ipo_info (sg_date);
CREATE TABLE IF NOT EXISTS tdx_trackzs_daily (
    zs_code     VARCHAR(16) NOT NULL,
    zs_name     VARCHAR(64) NOT NULL DEFAULT '',
    etf_code    VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    etf_name    VARCHAR(64) NOT NULL DEFAULT '',
    iopv        DOUBLE PRECISION,
    now_price   DOUBLE PRECISION,
    pre_close   DOUBLE PRECISION,
    sz          DOUBLE PRECISION,
    zgb         DOUBLE PRECISION,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (zs_code, etf_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_trackzs_daily_date ON tdx_trackzs_daily (trade_date);
CREATE TABLE IF NOT EXISTS tdx_pricevol_daily (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    last_close  DOUBLE PRECISION,
    now_price   DOUBLE PRECISION,
    zaf         DOUBLE PRECISION,
    volume_hand DOUBLE PRECISION,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_tdx_pricevol_daily_date ON tdx_pricevol_daily (trade_date);
CREATE TABLE IF NOT EXISTS tdx_stock_list_snapshot (
    trade_date  VARCHAR(8)  NOT NULL,
    market_key  VARCHAR(16) NOT NULL,
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    name        VARCHAR(64) NOT NULL DEFAULT '',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, market_key, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_tdx_stock_list_snapshot_stock ON tdx_stock_list_snapshot (stock_code, trade_date);
CREATE TABLE IF NOT EXISTS tdx_sector_list (
    trade_date   VARCHAR(8)  NOT NULL,
    sector_code  VARCHAR(16) NOT NULL,
    symbol       VARCHAR(16) NOT NULL,
    name         VARCHAR(64) NOT NULL DEFAULT '',
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, sector_code)
);
CREATE TABLE IF NOT EXISTS tdx_sector_members (
    trade_date   VARCHAR(8)  NOT NULL,
    sector_code  VARCHAR(16) NOT NULL,
    stock_code   VARCHAR(16) NOT NULL,
    symbol       VARCHAR(16) NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, sector_code, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_tdx_sector_members_stock ON tdx_sector_members (stock_code, trade_date);
CREATE TABLE IF NOT EXISTS tdx_stock_info_snapshot (
    stock_code  VARCHAR(16) NOT NULL,
    symbol      VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    name        VARCHAR(64) NOT NULL DEFAULT '',
    industry    VARCHAR(64) NOT NULL DEFAULT '',
    raw         JSONB NOT NULL DEFAULT '{}',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE TABLE IF NOT EXISTS tdx_trading_dates (
    market_key  VARCHAR(16) NOT NULL,
    trade_date  VARCHAR(8)  NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (market_key, trade_date)
);
CREATE TABLE IF NOT EXISTS tdx_ingest_cursor (
    dataset          VARCHAR(32) NOT NULL,
    window_key       VARCHAR(32) NOT NULL,
    cursor           JSONB NOT NULL DEFAULT '{}',
    status           VARCHAR(16) NOT NULL DEFAULT '',
    last_error       TEXT NOT NULL DEFAULT '',
    last_target_date VARCHAR(8) NOT NULL DEFAULT '',
    rows_total       BIGINT NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataset, window_key)
);
CREATE TABLE IF NOT EXISTS tdx_ingest_runs (
    id            BIGSERIAL PRIMARY KEY,
    dataset       VARCHAR(32) NOT NULL,
    slot          VARCHAR(8)  NOT NULL,
    target_date   VARCHAR(8)  NOT NULL,
    status        VARCHAR(16) NOT NULL,
    rows_written  BIGINT NOT NULL DEFAULT 0,
    rows_archived BIGINT NOT NULL DEFAULT 0,
    calls         INTEGER NOT NULL DEFAULT 0,
    duration_ms   BIGINT NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tdx_ingest_runs_dataset ON tdx_ingest_runs (dataset, target_date DESC);
CREATE INDEX IF NOT EXISTS idx_tdx_ingest_runs_day ON tdx_ingest_runs (target_date, started_at DESC);
"""

#: 建表**之后**追加的列（老库补列用）。本批全部为新表，暂无补列；
#: 将来加列在此登记一行（格式见 decision_ledger_contract._COLUMN_TOPUPS）。
_COLUMN_TOPUPS: tuple[tuple[str, str], ...] = ()

_COLUMNS_SQL = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = :t"
)


def _missing_topups(current: Collection[str]) -> tuple[str, ...]:
    """登记了但库里没有的列。表存在 ≠ 列全（``CREATE TABLE IF NOT EXISTS`` 的盲区）。"""
    have = set(current)
    return tuple(name for name, _ in _COLUMN_TOPUPS if name not in have)


def _all_present(session_execute) -> bool:
    """全部表都在且无缺列 → 零 DDL 快路径。一次性查全部表的列集。"""
    seen: dict[str, set[str]] = {}
    for table in TABLES:
        seen[table] = {str(r[0]) for r in session_execute(table)}
    if not all(seen.values()):
        return False
    return not _missing_topups({c for cols in seen.values() for c in cols})


def ensure_tdx_ingest_tables() -> bool:
    """幂等建全部 tdx_* 表（表在且列全即零 DDL 快路径；失败仅告警不抛出）。"""
    from sqlalchemy import text

    from backend.shared.sync_db import sync_session

    try:
        with sync_session() as session:
            ready = _all_present(
                lambda t: session.execute(text(_COLUMNS_SQL), {"t": t}).all()
            )
        if ready:
            return True
        with sync_session() as session:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            for statement in _CREATE_SQL.strip().split(";\n"):
                if statement.strip():
                    session.execute(text(statement))
            session.commit()
        logger.info("[TdxIngestContract] %d 张 tdx_* 表已就绪", len(TABLES))
        return True
    except Exception as exc:  # noqa: BLE001 - 不阻断业务
        logger.warning("[TdxIngestContract] 自愈建表失败（不阻断）: %s", exc)
        return False


async def ensure_tdx_ingest_tables_async() -> bool:
    """trade 服务启动期自愈（与 :func:`ensure_tdx_ingest_tables` 等价）。"""
    from sqlalchemy import text as _text

    from backend.shared.database_manager_v2 import get_session

    try:
        async with get_session(read_only=True) as session:
            ready = True
            for table in TABLES:
                rows = (await session.execute(_text(_COLUMNS_SQL), {"t": table})).all()
                if not rows:
                    ready = False
                    break
        if ready:
            return True
        async with get_session() as session:
            await session.execute(_text("SET LOCAL lock_timeout = '3s'"))
            for statement in _CREATE_SQL.strip().split(";\n"):
                if statement.strip():
                    await session.execute(_text(statement))
            await session.commit()
        logger.info("[TdxIngestContract] %d 张 tdx_* 表已就绪（async）", len(TABLES))
        return True
    except Exception as exc:  # noqa: BLE001 - 不阻断启动
        logger.warning("[TdxIngestContract] 自愈建表失败（不阻断）: %s", exc)
        return False


__all__ = [
    "TABLES",
    "ensure_tdx_ingest_tables",
    "ensure_tdx_ingest_tables_async",
]
