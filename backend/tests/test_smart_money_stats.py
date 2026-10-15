"""聪明钱市场级统计单测（纯函数，不触盘）。

钉住的行为：
- daily_market_stats：家数/金额聚合、元与万元 → 亿元换算、大宗溢价占比（剔除
  无效行）、两融 5 日窗 min_periods=3、缺表不产列、缺日 NaN 不补 0
- branch_rank：窗口边界（end 含当日、窗外剔除）、净买降序、n_days、空表
- north_summary：分数差 → 百分点、增/减榜、全 NaN delta 返回空载荷
- latest_event_map：跨表取最新（Timestamp 与 str 比较回归——曾 TypeError）、
  同日跨表并列龙虎榜优先、超 end 排除、周末事件归下一交易日
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.shared.smart_money import stats

CAL = pd.bdate_range("2024-09-02", periods=10)  # 09-02(一) ~ 09-13(五)，无 CN 节假日
D0, D1, D2, D3, D5, D9 = CAL[0], CAL[1], CAL[2], CAL[3], CAL[5], CAL[9]


class TestDailyMarketStats:
    def test_龙虎榜家数与亿元换算(self):
        raw = {
            "lhb_detail": pd.DataFrame(
                [
                    {"trade_date": D0, "symbol": "A.SH", "lhb_net_buy": 2e8},
                    {"trade_date": D0, "symbol": "B.SZ", "lhb_net_buy": 1e8},
                    {"trade_date": D5, "symbol": "A.SH", "lhb_net_buy": -1e8},
                ]
            )
        }
        out = stats.daily_market_stats(CAL, raw)
        assert out.loc[D0, "lhb_n_stocks"] == 2.0
        assert out.loc[D0, "lhb_net_buy_yi"] == pytest.approx(3.0)
        assert out.loc[D5, "lhb_net_buy_yi"] == pytest.approx(-1.0)
        # 无事件日 NaN（缺失绝不补 0）
        assert np.isnan(out.loc[D1, "lhb_n_stocks"])

    def test_大宗溢价占比剔除无效行(self):
        raw = {
            "block_trade": pd.DataFrame(
                [
                    {"trade_date": D0, "symbol": "A.SH", "amount": 1e8, "premium": 0.05},
                    {"trade_date": D0, "symbol": "B.SZ", "amount": 3e8, "premium": -0.02},
                    {"trade_date": D0, "symbol": "C.SH", "amount": -5.0, "premium": 0.1},
                ]
            )
        }
        out = stats.daily_market_stats(CAL, raw)
        assert out.loc[D0, "dzjy_amount_yi"] == pytest.approx(4.0)  # 负成交额行剔除
        assert out.loc[D0, "dzjy_premium_share"] == pytest.approx(0.25)  # 仅溢价额计入分子

    def test_两融万元换亿元且5日窗min_periods(self):
        margin = pd.DataFrame(
            [
                {"trade_date": d, "symbol": "X.SH", "finance_net": 10000.0, "finance_balance": 500000.0}
                for d in CAL[:6]
            ]
        )
        out = stats.daily_market_stats(CAL, {}, margin)
        assert np.isnan(out.loc[D0, "margin_net_5d_yi"])  # 观测 <3 → 不出值
        assert out.loc[D2, "margin_net_5d_yi"] == pytest.approx(3.0)  # 3×1 万万元 → 3 亿元
        assert out.loc[D5, "margin_net_5d_yi"] == pytest.approx(5.0)
        assert out.loc[D5, "margin_bal_yi"] == pytest.approx(50.0)

    def test_空raw不产出列(self):
        out = stats.daily_market_stats(CAL, {})
        assert len(out) == len(CAL)
        assert "lhb_n_stocks" not in out.columns  # 无表 → 无列（界面按列存在性兜底）


class TestBranchRank:
    def test_窗口聚合与净买降序(self):
        raw = pd.DataFrame(
            [
                # D0 在窗（CAL[1..5]）之外 → 剔除
                {"trade_date": D0, "branch_name": "甲", "net_amount": 1e8,
                 "buy_amount": 1.2e8, "sell_amount": 2e7, "stocks_bought": "X"},
                {"trade_date": D2, "branch_name": "甲", "net_amount": 2e8,
                 "buy_amount": 2.5e8, "sell_amount": 5e7, "stocks_bought": "Y"},
                {"trade_date": D5, "branch_name": "乙", "net_amount": 5e8,
                 "buy_amount": 6e8, "sell_amount": 1e8, "stocks_bought": "Z"},
            ]
        )
        out = stats.branch_rank(raw, CAL, D5, window=5, top_n=10)
        assert [r["branch_name"] for r in out] == ["乙", "甲"]
        jia = out[1]
        assert jia["net_amount"] == pytest.approx(2e8)  # D0 窗外不计
        assert jia["n_days"] == 1
        assert jia["stocks_bought"] == "Y"  # 窗内最近一次非空文本

    def test_空表与end早于日历(self):
        assert stats.branch_rank(None, CAL, D5) == []
        assert stats.branch_rank(pd.DataFrame(), CAL, D5) == []
        raw = pd.DataFrame([{"trade_date": D0, "branch_name": "甲", "net_amount": 1e8}])
        assert stats.branch_rank(raw, CAL, D0 - pd.Timedelta(days=1)) == []


class TestNorthSummary:
    def test_空与全NaN_delta返回空载荷(self):
        assert stats.north_summary(None)["quarter"] is None
        assert stats.north_summary(pd.DataFrame())["quarter"] is None
        only_one = pd.DataFrame(
            [{"symbol": "A.SH", "stock_name": "甲", "quarter": "2026Q3",
              "report_date": "2026-09-30", "pct": 0.03, "delta": np.nan}]
        )
        s = stats.north_summary(only_one)
        assert s["quarter"] is None and s["top_inc"] == []

    def test_百分点换算与增减榜(self):
        df = pd.DataFrame(
            [
                {"symbol": "A.SH", "stock_name": "甲", "quarter": "2026Q3",
                 "report_date": "2026-09-30", "pct": 0.05, "delta": 0.02},
                {"symbol": "B.SZ", "stock_name": "乙", "quarter": "2026Q3",
                 "report_date": "2026-09-30", "pct": 0.01, "delta": -0.01},
                {"symbol": "C.SH", "stock_name": "丙", "quarter": "2026Q3",
                 "report_date": "2026-09-30", "pct": 0.02, "delta": 0.005},
            ]
        )
        s = stats.north_summary(df)
        assert s["quarter"] == "2026Q3" and s["report_date"] == "2026-09-30"
        assert s["n_total"] == 3 and s["n_inc"] == 2 and s["n_dec"] == 1
        assert s["top_inc"][0]["symbol"] == "A.SH"
        assert s["top_inc"][0]["delta_pp"] == pytest.approx(2.0)  # 分数差 → 百分点
        assert s["top_inc"][0]["pct"] == pytest.approx(5.0)
        assert s["top_dec"][0]["symbol"] == "B.SZ"
        assert s["top_dec"][0]["delta_pp"] == pytest.approx(-1.0)


class TestLatestEventMap:
    def test_跨表取最新_新旧类型比较回归(self):
        # 旧实现把已存日期格式化为 str 后再与 Timestamp 比较 → TypeError；
        # 本测钉死：跨表（lhb_detail vs survey）比较照常取更晚者。
        raw = {
            "lhb_detail": pd.DataFrame([{"trade_date": D1, "symbol": "A.SH"}]),
            "survey": pd.DataFrame([{"trade_date": D3, "symbol": "A.SH", "inst_count": 5}]),
        }
        m = stats.latest_event_map(raw, CAL, D9)
        assert m["A.SH"] == {"type": "survey", "date": f"{D3:%Y-%m-%d}"}

    def test_同日跨表并列_龙虎榜优先(self):
        raw = {
            "lhb_detail": pd.DataFrame([{"trade_date": D2, "symbol": "A.SH"}]),
            "lhb_inst": pd.DataFrame([{"trade_date": D2, "symbol": "A.SH", "inst_net_buy": 1.0}]),
        }
        m = stats.latest_event_map(raw, CAL, D9)
        assert m["A.SH"]["type"] == "lhb"

    def test_同表多日取最新且超end排除(self):
        raw = {
            "lhb_detail": pd.DataFrame(
                [
                    {"trade_date": D1, "symbol": "A.SH"},
                    {"trade_date": D5, "symbol": "A.SH"},
                    {"trade_date": D9, "symbol": "A.SH"},
                ]
            )
        }
        m = stats.latest_event_map(raw, CAL, D5)
        assert m["A.SH"]["date"] == f"{D5:%Y-%m-%d}"

    def test_周末事件归下一交易日(self):
        sat = pd.Timestamp("2024-09-07")  # 周六 → 下一交易日 CAL[5]=09-09
        raw = {"survey": pd.DataFrame([{"trade_date": sat, "symbol": "B.SZ", "inst_count": 1}])}
        m = stats.latest_event_map(raw, CAL, D9)
        assert m["B.SZ"]["date"] == f"{CAL[5]:%Y-%m-%d}"

    def test_空raw返回空(self):
        assert stats.latest_event_map({}, CAL, D9) == {}
