"""港股聪明钱 · 事件层单元测试（合成数据，纯内存，不读真实分区）。

见证：三条件入事件闸边界 / Δ价值与事后收益附加口径 / first_seen-退出边界不产
事件但计数 / 过户嫌疑阈值边界与出流 / episode 冷却语义（1=首日、续存 0、反向
重置、嫌疑不刷新状态）/ A 席与 hkscc 排除 / 类型持仓聚合 / 南向完整表口径 /
空日 schema 稳定（parquet union 前提）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.shared.hk_smart_money import EVENT_COLUMNS, FLOW_COLUMNS
from backend.shared.hk_smart_money import datasets as ds
from backend.shared.hk_smart_money.events import EventBuilder

CAL = pd.bdate_range("2026-01-05", periods=30)
SYMS = ["0001.HK", "0002.HK", "0003.HK"]

HK_SEAT = ("C00001", "ABC SECURITIES LIMITED")
HK_SEAT2 = ("C00002", "XYZ SECURITIES LIMITED")
HK_SEAT3 = ("C00003", "DEF SECURITIES LIMITED")


def _close(price: float = 10.0, vary_first: bool = False) -> pd.DataFrame:
    data = {s: np.full(len(CAL), price, dtype="float64") for s in SYMS}
    if vary_first:
        data[SYMS[0]] = np.arange(10.0, 10.0 + len(CAL))
    return pd.DataFrame(data, index=CAL)


def _amount(value: float = 1e9, small: dict[str, float] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(value, index=CAL, columns=SYMS, dtype="float64")
    for s, v in (small or {}).items():
        df[s] = v
    return df


def _builder(tmp_path, close=None, amount=None) -> EventBuilder:
    close = _close() if close is None else close
    amount = _amount() if amount is None else amount
    return EventBuilder(close, amount, ds.forward_matrices(close), data_dir=tmp_path)


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).astype({"holding_quantity": "int64"})


def _row(seat: tuple[str, str], stock: str, qty: int) -> dict:
    return {
        "participant_id": seat[0],
        "participant_name": seat[1],
        "stock_code": stock,
        "stock_name": "TEST CO",
        "holding_quantity": qty,
    }


def _run(b: EventBuilder, frames: list[pd.DataFrame], start_idx: int = 1):
    """逐日推进（frames[i] 对应 CAL[start_idx+i]），返回最后一日结果。"""
    prev = frames[0]
    out = None
    for i, cur in enumerate(frames[1:], start=start_idx + 1):
        out = b.process_day(CAL[i].strftime("%Y%m%d"), prev, cur, i)
        prev = cur
    return out


# ---------------------------------------------------------------- 入闸条件


def test_gates_value_pct_price(tmp_path):
    b = _builder(tmp_path)
    prev = _frame(
        [
            _row(HK_SEAT, "0001.HK", 1_000_000),
            _row(HK_SEAT2, "0002.HK", 1_000_000),
            _row(HK_SEAT3, "0003.HK", 20_000_000),
        ]
    )
    cur = _frame(
        [
            _row(HK_SEAT, "0001.HK", 1_200_000),  # 2M 港元 / 20% → 过闸
            _row(HK_SEAT2, "0002.HK", 1_050_000),  # 50 万 < 1M → 拒
            _row(HK_SEAT3, "0003.HK", 20_200_000),  # 1% < 5% → 拒
        ]
    )
    events, flows, counts, _u, _h = _run(b, [prev, cur])
    assert counts["raw_moves_n"] == 3
    assert counts["events_n"] == 1
    assert list(events["symbol"]) == ["0001.HK"]
    row = events.iloc[0]
    assert row["delta_qty"] == 200_000
    assert row["delta_value"] == 2_000_000.0  # 10 HKD 收盘
    assert abs(row["delta_pct"] - 0.2) < 1e-12
    assert row["direction"] == 1
    assert row["category"] == "hk"
    assert not row["transfer_suspect"]
    assert flows["net_value"].sum() == 2_000_000.0


def test_price_gate_excludes_penny_stock(tmp_path):
    b = _builder(tmp_path, close=_close(price=0.4))
    prev = _frame([_row(HK_SEAT, "0001.HK", 1_000_000)])
    cur = _frame([_row(HK_SEAT, "0001.HK", 1_200_000)])
    events, _f, counts, _u, _h = _run(b, [prev, cur])
    assert counts["events_n"] == 0
    assert events.empty


def test_first_seen_exit_boundary_counted_not_events(tmp_path):
    b = _builder(tmp_path)
    prev = _frame(
        [
            _row(HK_SEAT, "0001.HK", 1_000_000),
            _row(HK_SEAT2, "0002.HK", 1_000_000),  # 次日退出 top50
        ]
    )
    cur = _frame(
        [
            _row(HK_SEAT, "0001.HK", 1_000_000),  # 无变动
            _row(HK_SEAT3, "0003.HK", 5_000_000),  # 首现
        ]
    )
    events, _f, counts, universe, _h = _run(b, [prev, cur])
    assert counts["first_seen_n"] == 1
    assert counts["exit_n"] == 1
    assert counts["raw_moves_n"] == 0
    assert counts["events_n"] == 0
    assert events.empty
    assert set(universe["symbol"]) == {"0001.HK", "0003.HK"}


# ---------------------------------------------------------------- 价值与事后收益


def test_delta_value_and_forward_attach(tmp_path):
    close = _close(vary_first=True)  # 0001.HK 每日 +1（10 起）
    b = _builder(tmp_path, close=close)
    prev = _frame([_row(HK_SEAT, "0001.HK", 1_000_000)])
    cur = _frame([_row(HK_SEAT, "0001.HK", 1_200_000)])
    dt_idx = 6  # CAL[6]，收盘 16
    events, _f, _c, _u, _h = b.process_day(
        CAL[dt_idx].strftime("%Y%m%d"), prev, cur, dt_idx
    )
    row = events.iloc[0]
    assert row["price"] == 16.0
    assert row["delta_value"] == 200_000 * 16.0
    fwd10 = close["0001.HK"].iloc[dt_idx + 10] / close["0001.HK"].iloc[dt_idx + 1] - 1
    assert abs(row["fwd_ret_10"] - fwd10) < 1e-12
    # 全市场中位 = 0（另两票平价）→ exc = ret
    assert abs(row["fwd_exc_10"] - fwd10) < 1e-12


# ---------------------------------------------------------------- 过户嫌疑


def test_transfer_suspect_boundary_and_flow_exclusion(tmp_path):
    # 0002 当日成交额 2e6 → 阈值 2.4e6；Δvalue = 300k×10 = 3e6 > 2.4e6 → 嫌疑
    b = _builder(tmp_path, amount=_amount(small={"0002.HK": 2e6}))
    prev = _frame(
        [
            _row(HK_SEAT2, "0002.HK", 1_000_000),
            _row(HK_SEAT, "0001.HK", 1_000_000),
        ]
    )
    cur = _frame(
        [
            _row(HK_SEAT2, "0002.HK", 1_300_000),  # 嫌疑
            _row(HK_SEAT, "0001.HK", 1_200_000),  # 2M = 2e6 ≤ 1.2×1e9 → 非嫌疑
        ]
    )
    events, flows, counts, _u, _h = _run(b, [prev, cur])
    assert counts["events_n"] == 2
    assert counts["suspect_n"] == 1
    suspect = events[events["transfer_suspect"]]
    assert list(suspect["symbol"]) == ["0002.HK"]
    # 嫌疑出流；仅 0001 进 flows
    assert set(flows["symbol"]) == {"0001.HK"}
    assert counts["flows_rows"] == 1


def test_suspect_boundary_strictly_greater(tmp_path):
    # |Δvalue| = 2.4e6 = 1.2 × 2e6 → 不嫌疑（严格大于才打标）
    b = _builder(tmp_path, amount=_amount(small={"0001.HK": 2e6}))
    prev = _frame([_row(HK_SEAT, "0001.HK", 1_000_000)])
    cur = _frame([_row(HK_SEAT, "0001.HK", 1_240_000)])  # 240k×10 = 2.4e6
    events, _f, _c, _u, _h = _run(b, [prev, cur])
    assert bool(events.iloc[0]["transfer_suspect"]) is False


# ---------------------------------------------------------------- episode 冷却


def test_episode_cooldown_and_reset(tmp_path):
    qtys = [
        1_000_000,  # d0 基准（prev=None 不差分）
        1_200_000,  # d1 首事件 lead=1（+200k）
        1_400_000,  # d2 同向续存 lead=0（距 d1=1）
        1_400_000,  # d3 无变动
        1_500_000,  # d4 续存 lead=0（距 d2=2）
        1_500_000,
        1_500_000,
        1_500_000,
        1_500_000,
        1_500_000,
        1_500_000,
        1_600_000,  # d11 距 d4=7 > 5 → 新 episode lead=1
        1_300_000,  # d12 反向 → 重置 lead=1
        1_200_000,  # d13 同向续存 lead=0
    ]
    b = _builder(tmp_path)
    frames = [_frame([_row(HK_SEAT, "0001.HK", q)]) for q in qtys]
    b.process_day(CAL[0].strftime("%Y%m%d"), None, frames[0], 0)
    leads = []
    prev = frames[0]
    for i in range(1, len(frames)):
        events, *_ = b.process_day(CAL[i].strftime("%Y%m%d"), prev, frames[i], i)
        leads.append(int(events.iloc[0]["episode_lead"]) if len(events) else None)
        prev = frames[i]
    assert leads == [1, 0, None, 0, None, None, None, None, None, None, 1, 1, 0]


def test_suspect_does_not_refresh_episode(tmp_path):
    # 可判别场景：正常 d1 → 嫌疑 d6（若刷新状态则 d8 距 2 → 续存）→ 正常 d8。
    # 嫌疑不触碰状态 ⇒ d8 距 d1 = 7 > 5 → 必须 lead=1。
    b = _builder(tmp_path, amount=_amount(small={"0001.HK": 2e6}))  # 阈值 2.4M
    qtys = [
        1_000_000,  # d0 基准
        1_200_000,  # d1 Δ200k → dv 2M 非嫌疑 → lead=1（状态 (d1,+1)）
        1_200_000,  # d2
        1_200_000,  # d3
        1_200_000,  # d4
        1_200_000,  # d5
        1_700_000,  # d6 Δ500k → dv 5M > 2.4M → 嫌疑（不标 lead、不刷新）
        1_700_000,  # d7
        1_900_000,  # d8 Δ200k → dv 2M 非嫌疑 → 距 d1=7 > 5 → lead=1
    ]
    frames = [_frame([_row(HK_SEAT, "0001.HK", q)]) for q in qtys]
    b.process_day(CAL[0].strftime("%Y%m%d"), None, frames[0], 0)
    out: dict[int, tuple] = {}
    prev = frames[0]
    for i in range(1, len(frames)):
        out[i] = b.process_day(CAL[i].strftime("%Y%m%d"), prev, frames[i], i)
        prev = frames[i]
    e1, _f1, _c1, _u, _h = out[1]
    assert int(e1.iloc[0]["episode_lead"]) == 1
    assert not bool(e1.iloc[0]["transfer_suspect"])
    e6, f6, c6, _u6, _h6 = out[6]
    assert bool(e6.iloc[0]["transfer_suspect"]) is True
    assert int(e6.iloc[0]["episode_lead"]) == 0  # 嫌疑不标首日
    assert c6["suspect_n"] == 1 and c6["flows_rows"] == 0 and f6.empty  # 嫌疑出流
    e8, _f8, _c8, _u8, _h8 = out[8]
    assert int(e8.iloc[0]["episode_lead"]) == 1  # 嫌疑未刷新状态：距 d1 = 7 > 5


# ---------------------------------------------------------------- 席位排除与持仓


def test_southbound_seat_and_hkscc_excluded(tmp_path):
    hkscc = ("C09999", "香港中央結算(代理人)有限公司")
    a_seat = ("A00002", "香港中央結算有限公司")
    b = _builder(tmp_path)
    prev = _frame(
        [
            _row(a_seat, "0001.HK", 10_000_000),
            _row(hkscc, "0002.HK", 10_000_000),
        ]
    )
    cur = _frame(
        [
            _row(a_seat, "0001.HK", 11_000_000),  # 巨量变动但不进事件
            _row(hkscc, "0002.HK", 11_000_000),
        ]
    )
    events, _f, counts, _u, holdings = _run(b, [prev, cur])
    assert counts["events_n"] == 0
    assert counts["a_seat_qty_sum"] == 11_000_000
    assert set(holdings["category"]) & {"southbound", "hkscc"} == set()


def test_holdings_aggregation(tmp_path):
    b = _builder(tmp_path)
    prev = _frame([_row(HK_SEAT, "0001.HK", 600_000)])
    cur = _frame(
        [
            _row(HK_SEAT, "0001.HK", 600_000),
            _row(HK_SEAT2, "0001.HK", 400_000),
        ]
    )
    _e, _f, _c, _u, holdings = _run(b, [prev, cur])
    row = holdings[(holdings["symbol"] == "0001.HK") & (holdings["category"] == "hk")]
    assert len(row) == 1
    assert int(row.iloc[0]["qty_sum"]) == 1_000_000
    assert int(row.iloc[0]["n_seats"]) == 2


# ---------------------------------------------------------------- 南向流


def test_south_flows_complete_table_semantics(tmp_path):
    b = _builder(tmp_path)
    prev = pd.DataFrame(
        {"symbol": ["0001.HK", "0002.HK"], "holding_quantity": [1_000, 500]}
    )
    cur = pd.DataFrame(
        {"symbol": ["0001.HK", "0003.HK"], "holding_quantity": [1_500, 200]}
    )
    flows = b.south_flows("20260105", prev, cur)
    by = flows.set_index("symbol")
    assert set(by.index) == {"0001.HK", "0002.HK", "0003.HK"}
    assert by.loc["0001.HK", "net_qty"] == 500
    assert by.loc["0001.HK", "net_value"] == 500 * 10.0
    assert by.loc["0001.HK", "n_up"] == 1
    assert by.loc["0002.HK", "net_qty"] == -500  # 缺席=0 → 清仓流出
    assert by.loc["0002.HK", "n_dn"] == 1
    assert by.loc["0003.HK", "net_qty"] == 200
    assert (flows["category"] == "southbound").all()
    # 首日（无前序分区）不产流
    assert b.south_flows("20260105", None, cur).empty


# ---------------------------------------------------------------- schema 稳定


def test_empty_day_schema_matches_contract(tmp_path):
    b = _builder(tmp_path)
    cur = _frame([_row(HK_SEAT, "0001.HK", 1_000_000)])
    # prev=None（序列首日）
    events, flows, counts, universe, holdings = b.process_day("20260105", None, cur, 0)
    assert list(events.columns) == list(EVENT_COLUMNS)
    assert list(flows.columns) == list(FLOW_COLUMNS)
    assert counts["priced"] is True and counts["events_n"] == 0
    assert len(universe) == 1 and not holdings.empty
    # 无变动日
    events2, flows2, _c2, _u2, _h2 = _run(b, [cur, cur])
    assert list(events2.columns) == list(EVENT_COLUMNS)
    assert list(flows2.columns) == list(FLOW_COLUMNS)
    assert events2.empty and flows2.empty
