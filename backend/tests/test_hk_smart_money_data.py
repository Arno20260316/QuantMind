"""港股聪明钱数据面集成测试（直连真实 parquet/产物，无网络依赖）。

照 test_quanthk_institutional.py 同款直连模式。两层：

1. **源侧对账（无产物依赖，永远跑）**：CCASS A 席（南向结算席）最新分区
   持股量 vs hsgt_south 同日总量 ±2%——独立重算，防「财务双计」口径漂移；
2. **产物面（依赖手动首建，未建 skip）**：港股聪明钱产物为手动首建（OSS
   首装不排调度），未首建时 skip 而非红——首建后自动纳入：
   - 最新分区事件冒烟（量级/键唯一/方向与类别域/counts 对账一致）；
   - 封存前视列成熟度（远日成熟、尾日 NaN——绝不填 0）；
   - 类型覆盖：事件与持仓 = CCASS 5 类，flows = 6 类（含南向）；
   - 席位榜三窗口形状与单调（all 窗口事件数 ≥ 20 窗口）；
   - 产物自洽：as_of ↔ evidence.eval_end ↔ seats.last_event_date ↔ 对账比。
"""

from __future__ import annotations

import duckdb
import pytest

from backend.services.api.market_analysis_hk import quanthk_feed as feed
from backend.services.api.market_analysis_shared import market_days
from backend.shared.hk_smart_money import (
    CATEGORIES,
    CCASS_CATEGORIES,
    SOUTHBOUND_SEAT_IDS,
)
from backend.shared.hk_smart_money import datasets as ds
from backend.shared.hk_smart_money import events as ev

SEAT_WINDOWS = ("20", "60", "all")


def _sum_qty(rel: str, dt: str, extra_where: str = "") -> int:
    path = feed.DATA_DIR / rel / f"dt={dt}" / "data.parquet"
    where = f"WHERE {extra_where}" if extra_where else ""
    row = (
        duckdb.connect()
        .execute(f"SELECT sum(holding_quantity) FROM read_parquet('{path}') {where}")
        .fetchone()
    )
    return int(row[0] or 0)


def _common_date(rel_a: str, rel_b: str) -> str:
    """两 rel 的最新公共分区日（各自原始字串）；无交集则 fail。"""
    da = {
        d.replace("-", ""): d
        for d in market_days.list_partition_dates(rel_a, feed.DATA_DIR)
    }
    db = {
        d.replace("-", ""): d
        for d in market_days.list_partition_dates(rel_b, feed.DATA_DIR)
    }
    assert da, f"{rel_a} 无分区"
    assert db, f"{rel_b} 无分区"
    common = sorted(set(da) & set(db), reverse=True)
    assert common, f"{rel_a} 与 {rel_b} 无公共日"
    return common[0]


@pytest.fixture(scope="module")
def product():
    if not (ds.product_dir() / "latest.json").is_file():
        pytest.skip("聪明钱产物未首建（build_hk_smart_money.py）——首建后自动纳入")
    return ds.product_dir()


# ---------------------------------------------------------------- 源侧对账


def test_南向对账A席_vs_hsgt_south_独立重算():
    """±2% 数据质量门：独立从源分区分算（不读产物结论），防双重计数漂移。"""
    dt = _common_date(feed.CCASS_TOP50_REL, feed.SOUTH_REL)
    seats = ",".join(f"'{s}'" for s in SOUTHBOUND_SEAT_IDS)
    a_qty = _sum_qty(feed.CCASS_TOP50_REL, dt, f"participant_id IN ({seats})")
    s_qty = _sum_qty(feed.SOUTH_REL, dt)
    assert a_qty > 0 and s_qty > 0
    ratio = a_qty / s_qty
    assert abs(ratio - 1.0) < 0.02, (
        f"A 席/南向 {dt} 比值 {ratio:.4f} 偏离 1.0（口径漂移？A={a_qty} S={s_qty}）"
    )


# ---------------------------------------------------------------- 事件仓冒烟


def test_事件仓最新分区冒烟(product):
    dates = ds.event_dates()
    assert dates, "事件仓为空"
    assert dates == tuple(sorted(dates))
    assert all(len(d) == 8 and d.isdigit() for d in dates)
    # 链条存活：事件尾日跟随 CCASS（一周容差=防周末/假期边界）
    assert dates[-1] in ds.ccass_dates()[-5:], (
        "事件仓落后 CCASS 超 5 个分区——链条停摆？"
    )

    latest = dates[-1]
    df = ds.load_events(dates=[latest])
    assert 200 < len(df) < 50_000, f"最新分区 {latest} 事件数 {len(df)} 量级异常"
    assert int(df[["symbol", "participant_id"]].duplicated().sum()) == 0
    assert set(df["direction"].unique()) <= {1, -1}
    assert set(df["category"].unique()) <= set(CCASS_CATEGORIES)
    assert bool((df["delta_value"] != 0).all())
    assert bool(df["symbol"].str.fullmatch(r"\d{4}\.HK").all())
    assert bool(df["delta_qty"].astype("int64").ne(0).all())

    # counts.json 与数据对账（同源见证）
    counts = ev.read_counts(latest)
    assert counts.get("priced") is True
    assert counts.get("events_n") == len(df)
    assert counts.get("suspect_n", 0) <= counts.get("events_n", 0)
    assert counts.get("a_seat_qty_sum", 0) > 0
    assert counts.get("south_qty_sum", 0) > 0


def test_封存前视列成熟度(product):
    """远日 fwd 超额应成熟（非空）；尾日未成熟应 NaN——绝不填 0 冒充。"""
    dates = ds.event_dates()
    old = dates[-40]
    df_old = ds.load_events(dates=[old], columns=["fwd_exc_10"])
    ripe = float(df_old["fwd_exc_10"].notna().mean())
    assert ripe > 0.9, f"{old} 的 h10 事后超额成熟率仅 {ripe:.2f}"
    latest = ds.load_events(dates=[dates[-1]], columns=["fwd_exc_10", "fwd_exc_20"])
    any_fwd = latest["fwd_exc_10"].notna() | latest["fwd_exc_20"].notna()
    assert float(any_fwd.mean()) < 0.5, "尾日事后超额不应大量成熟（未到期应 NaN）"
    assert float((latest["fwd_exc_20"].fillna(0) == 0).mean()) > 0.5  # NaN≠0


def test_类型覆盖6类(product):
    """事件与持仓 = CCASS 5 类；flows = 6 类（含南向）——南向不入事件仓防双重计数。"""
    dates = ds.event_dates()
    win = list(dates[-20:])
    evs = ds.load_events(dates=win, columns=["symbol", "category"])
    assert set(evs["category"].unique()) == set(CCASS_CATEGORIES)
    # flows 须 20 日窗覆盖 6 类：单日不稳（'other' 极稀疏；南向上游分区复制
    # 快照日 Δ 全 0 → 当日 0 行，是数据事实而非链路故障）
    flows = ds.load_flows(dates=win, columns=["dt", "category"])
    assert set(flows["category"].unique()) == set(CATEGORIES)
    hold = ds.load_holdings(dates=[dates[-1]])
    assert set(hold["category"].unique()) == set(CCASS_CATEGORIES)
    assert "southbound" not in set(hold["category"].unique())


# ---------------------------------------------------------------- 席位榜


def test_席位榜三窗口形状与单调(product):
    seats = ds.load_seats()
    assert seats is not None and len(seats) > 100
    assert set(seats["window"].unique()) == set(SEAT_WINDOWS)
    assert seats.groupby("window").size().nunique() == 1  # 三窗同席位数
    # 席位宇宙 = 全历史有事件者；20/60 窗可含 0 事件行（原值披露不删行，
    # 由 API min_events 过滤），'all' 窗必 ≥1
    zero_windows = set(seats.loc[seats["n_events"] == 0, "window"].unique())
    assert zero_windows <= {"20", "60"}
    assert int(seats.loc[seats["window"] == "all", "n_events"].min()) >= 1
    assert int(seats["eligible"].astype(bool).sum()) > 0
    assert bool(seats["shrunk_hit"].dropna().between(0.0, 1.0).all())
    latest = ds.event_dates()[-1]
    assert str(seats["last_event_date"].max()) <= latest
    # all 窗口事件数 ≥ 20 窗口（同席位口径单调）
    m = seats[seats["window"] == "20"].merge(
        seats[seats["window"] == "all"], on="participant_id", suffixes=("_20", "_all")
    )
    assert len(m) > 50
    assert bool((m["n_events_all"] >= m["n_events_20"]).all())


# ---------------------------------------------------------------- 产物自洽


def test_产物三面自洽(product):
    latest = ds.load_product_json("latest")
    consensus = ds.load_product_json("consensus")
    evidence = ds.load_product_json("evidence")
    seats = ds.load_seats()
    iso = latest["as_of"]
    compact = iso.replace("-", "")
    assert evidence["window"]["eval_end"] == iso
    assert str(seats["last_event_date"].max()) == compact
    assert evidence["admitted"] == latest["admitted"] == consensus["adopted"]
    # 产物内对账比与源侧同门限（±2%）
    ratio = evidence["reconciliation"]["ratio"]
    assert abs(ratio - 1.0) <= 0.02, f"产物对账比 {ratio} 超 ±2%"
    # 零入柱 honesty：无入柱则共识空态、evidence_only=true（真裁定非故障）
    if not latest["admitted"]:
        assert latest["evidence_only"] is True
        assert consensus["items"] == []
