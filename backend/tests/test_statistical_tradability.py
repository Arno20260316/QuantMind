"""统计派 A 股可交易性过滤（移植 P2 / Tier 1）——板块幅度 / 逐级归因 / 格式无关。

契约：

1. **板块识别与涨跌停幅度**：主板 10%、创业板 20%（2020-08-24 前 10%）、
   科创板 20%、北交所 30%、ST 5%；容差 0.2%；
2. **收盘触板判定**：+9.8% 算主板涨停（容差内），+9.0% 不算；首根 bar 不判；
3. **逐级归因**：每样本只归到第一个命中的原因（停牌→ST→次新→涨跌停→流动性），
   各级计数之和 = 总剔除；
4. **格式无关**：面板 symbol 与 ST 池/上市日表前缀式后缀式混用都能对上（走代码
   数字匹配）；上市日缺失 → 保守剔除（未知不放行）；
5. 次新按**面板日历的交易日**计数（不是自然日）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.statistical import tradability as td  # noqa: E402


def _panel(rows: list[tuple[str, str, float, float, float]]) -> pd.DataFrame:
    """(date, symbol, close, volume, amount) → 面板（open/high/low 复制 close）。"""
    frame = pd.DataFrame(rows, columns=["trade_date", "symbol", "close", "volume", "amount"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for col in ("open", "high", "low"):
        frame[col] = frame["close"]
    return frame.set_index(["trade_date", "symbol"]).sort_index()


# ── 板块与幅度 ───────────────────────────────────────────────────────


class TestBoardAndWidth:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("600000.SH", "main"),
            ("000001.SZ", "main"),
            ("002594.SZ", "main"),
            ("300750.SZ", "gem"),
            ("301001.SZ", "gem"),
            ("688981.SH", "star"),
            ("689009.SH", "star"),
            ("830799.BJ", "bse"),
            ("920001.BJ", "bse"),
            ("SZ300123", "gem"),  # 前缀式也认
        ],
    )
    def test_board_of(self, symbol, expected):
        assert td.board_of(symbol) == expected

    def test_limit_width_by_board_and_date(self):
        late = pd.Timestamp("2024-01-01")
        assert td.limit_width("600000.SH", late) == 0.10
        assert td.limit_width("300750.SZ", late) == 0.20
        assert td.limit_width("688981.SH", late) == 0.20
        assert td.limit_width("830799.BJ", late) == 0.30
        assert td.limit_width("600000.SH", late, is_st=True) == 0.05
        # 创业板注册制前：10%
        assert td.limit_width("300750.SZ", pd.Timestamp("2020-08-21")) == 0.10
        assert td.limit_width("300750.SZ", pd.Timestamp("2020-08-24")) == 0.20


# ── 触板判定 ─────────────────────────────────────────────────────────


def _move(symbol: str, ret: float, n: int = 2) -> pd.DataFrame:
    """第 2 根 bar 相对第 1 根涨 ret 的两日面板。"""
    rows = [
        ("2024-01-01", symbol, 10.0, 100.0, 1e5),
        ("2024-01-02", symbol, 10.0 * (1 + ret), 100.0, 1e5),
    ]
    return _panel(rows)


class TestLimitLocked:
    def test_main_board_threshold_with_tolerance(self):
        assert bool(td.limit_locked(_move("600000.SH", 0.098)).iloc[-1]) is True
        assert bool(td.limit_locked(_move("600000.SH", 0.099)).iloc[-1]) is True
        assert bool(td.limit_locked(_move("600000.SH", 0.09)).iloc[-1]) is False
        assert bool(td.limit_locked(_move("600000.SH", -0.101)).iloc[-1]) is True

    def test_gem_uses_twenty_percent_after_reform(self):
        assert bool(td.limit_locked(_move("300750.SZ", 0.10)).iloc[-1]) is False
        assert bool(td.limit_locked(_move("300750.SZ", 0.199)).iloc[-1]) is True

    def test_first_bar_never_locked(self):
        locked = td.limit_locked(_move("600000.SH", 0.10))
        assert bool(locked.iloc[0]) is False

    def test_st_column_tightens_width(self):
        frame = _move("600000.SH", 0.06)  # 6%：主板不算，ST 算
        frame["is_st"] = [False, True]
        assert bool(td.limit_locked(frame).iloc[-1]) is True


# ── 逐级过滤与归因 ───────────────────────────────────────────────────


def _four_names(day: str = "2024-01-02") -> pd.DataFrame:
    """四个样本：正常 / 停牌 / 涨停 / 低成交额（第 1 日为基准，第 2 日判定）。"""
    symbols = ("600001.SH", "600002.SH", "600003.SH", "600004.SH")
    prev_day = day.replace("02", "01")
    prev = _panel([(prev_day, s, 10.0, 100.0, 1e6) for s in symbols])
    panel = _panel(
        [
            (day, "600001.SH", 10.0, 100.0, 1e6),  # 正常
            (day, "600002.SH", 10.0, 0.0, 0.0),  # 停牌
            (day, "600003.SH", 11.0, 100.0, 1e6),  # 前收 10 → +10% 涨停
            (day, "600004.SH", 10.0, 100.0, 1.0),  # 成交额垫底
        ]
    )
    return pd.concat([prev, panel]).sort_index()


class TestBuildMask:
    def test_counts_and_attribution(self):
        panel = _four_names()
        cfg = td.TradabilityConfig(min_list_days=0, min_amount_quantile=0.5)
        mask, report = td.build_mask(panel, cfg=cfg)
        assert report.n_input == 8
        assert report.n_suspended == 1
        assert report.n_limit_locked == 1
        # 第 1 日四只成交额全等 → 并列不剔；第 2 日只有 600004 严格低于中位
        assert report.n_low_liquidity == 1
        assert report.n_kept == report.n_input - (
            report.n_suspended + report.n_st + report.n_new_listing
            + report.n_limit_locked + report.n_low_liquidity
        )

    def test_st_symbols_match_ignoring_format(self):
        panel = _four_names()
        cfg = td.TradabilityConfig(
            st_snapshot_symbols=("SH600001",), min_list_days=0
        )
        mask, report = td.build_mask(panel, cfg=cfg)
        assert report.n_st == 2, "前缀式 ST 池也要匹配后缀式面板（两日各一条）"

    def test_new_listing_by_trading_days_and_unknown_dropped(self):
        # 跨周末（01-05 周五 → 01-08 周一）：自然日计 4 天，交易日只算 2 天
        dates = pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"])
        rows = [(d.strftime("%Y-%m-%d"), "600001.SH", 10.0, 100.0, 1e6) for d in dates]
        rows += [(d.strftime("%Y-%m-%d"), "600002.SH", 10.0, 100.0, 1e6) for d in dates]
        panel = _panel(rows)
        listing = pd.Series({"600001.SH": pd.Timestamp("2024-01-04")})  # 600002 缺上市日
        cfg = td.TradabilityConfig(min_list_days=3)
        mask, report = td.build_mask(panel, cfg=cfg, listing_dates=listing)
        kept_a = mask.xs("600001.SH", level=1)
        assert kept_a.tolist() == [False, False, False, True], (
            "上市后第 3 个交易日（01-09）才放行——01-08 是交易日第 2 天"
        )
        assert not mask.xs("600002.SH", level=1).any(), "上市日未知 → 保守剔除"
        assert report.n_new_listing == 7

    def test_listed_before_panel_start_is_not_new(self):
        """回归：上市日早于面板起点 ≠ 未知（曾因 reindex 落位被当未知全市场误杀）。"""
        panel = _four_names()
        listing = pd.Series(
            {
                "600001.SH": pd.Timestamp("2001-01-01"),   # 早于面板起点：老票
                "600002.SH": pd.Timestamp("2001-01-01"),
                "600003.SH": pd.Timestamp("2001-01-01"),
                "600004.SH": pd.Timestamp("2024-02-01"),   # 晚于面板终点：全段次新
            }
        )
        mask, report = td.build_mask(
            panel, cfg=td.TradabilityConfig(min_list_days=120), listing_dates=listing
        )
        assert report.n_new_listing == 2, "只有晚于面板终点的那个全程剔除"
        assert mask.xs("600001.SH", level=1).all()
        assert not mask.xs("600004.SH", level=1).any()

    def test_earlier_stage_wins_in_attribution(self):
        panel = _four_names()
        cfg = td.TradabilityConfig(
            st_snapshot_symbols=("600002.SH",), min_list_days=0  # 停牌且 ST
        )
        _, report = td.build_mask(panel, cfg=cfg)
        assert report.n_suspended == 1 and report.n_st == 1, "停牌日归停牌、另一日归 ST"

    def test_disabled_switches(self):
        panel = _four_names()
        cfg = td.TradabilityConfig(
            exclude_limit_locked=False, min_amount_quantile=0.0, min_list_days=0
        )
        _, report = td.build_mask(panel, cfg=cfg)
        assert report.n_limit_locked == 0 and report.n_low_liquidity == 0
