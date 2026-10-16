"""聪明钱分析引擎单测（纯函数金样 + 口径封存 + JSON 安全）。

钉住的行为（改动即红，口径见细案 §3）：
- 前向收益与验证脚本逐字同款：fwd(t,h)=close[t+h]/close[t+1]−1（h=2 ⇔ 1 个
  交易日持有），clip(−0.5,+1.0)；h<2 全 NaN（h=1 同价出场恒 0，无意义）；
  入场价 0 → NaN（不是 inf/1.0）
- 追踪：Top-N 组合收益/等权基准/超额、窗口 bp 汇总、分箱 h5 超额与单调性、
  累计超额曲线、最近案例「已实现 h10 / 持有中」（未闭合 → None，绝不填 0）
- 事件 CAR：族内当日截面分位 q5≥80 / q1≤20、k=2..car_days+1、未闭合 None
- IC 衰减：秩相关（并列平均秩）、n_days 口径、滚动窗口 min(win,20)
- 席位：净买 = Σ买−Σ卖（同日同股同席双向相抵）、已闭合样本分母、
  baseline 全市场胜率、四类分席、三档窗口（近 20 / 近 60 / 全史；
  日历不足窗天数 → 退化全史不虚报）
- 行业流：窗过滤（出窗不计）、机构专用买方 +/卖方 −、按 |机构+大宗| 排序
- JSON 安全：全部产物 json.dumps(allow_nan=False) 可序列化
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backend.shared.smart_money import analytics as A

# 12 个交易日世界：A 每日 +5%（growth），B/C 持平（flat）
CAL = pd.bdate_range("2026-01-05", periods=12)
F2 = 1.05**1 - 1  # 5.00%
F5 = 1.05**4 - 1  # 21.55%（h=5 ⇔ 4 个交易日持有）
F10 = 1.05**9 - 1  # 55.13%
F8 = 1.05**8 - 1  # 47.75%（持有中案例的浮动收益）
EW3 = F2 / 3  # 3 标的等权基准（h=2）


def _close3() -> pd.DataFrame:
    grown = [100.0 * 1.05**i for i in range(len(CAL))]
    return pd.DataFrame(
        {"A.SH": grown, "B.SH": 100.0, "C.SH": 100.0},
        index=CAL,
    )


def _scores3(date: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame(
        {"symbol": ["A.SH", "B.SH", "C.SH"], "score": [90.0, 60.0, 30.0]}
    ).assign(trade_date=date)


def _scores_by_date3() -> dict:
    return {CAL[i]: _scores3(CAL[i]) for i in range(5)}


def _names3() -> pd.Series:
    # 故意缺 C.SH：名字缺省必须落 None（不编造）
    return pd.Series({"A.SH": "甲股", "B.SH": "乙股"})


class TestForwardReturns:
    def test_与验证脚本逐字同款(self):
        """fwd(t,h)=close[t+h]/close[t+1]−1，clip(−0.5,+1.0)。"""
        rng = np.random.default_rng(7)
        close = pd.DataFrame(
            100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, (30, 4)), axis=0)),
            index=pd.bdate_range("2026-01-05", periods=30),
            columns=["W.SH", "X.SH", "Y.SH", "Z.SH"],
        )
        for h in (2, 5, 10):
            expect = (close.shift(-h) / close.shift(-1) - 1.0).clip(-0.5, 1.0)
            pd.testing.assert_frame_equal(A.forward_returns(close, h), expect)

    def test_h2即1个交易日持有_末日未闭合为NaN(self):
        close = _close3()
        fwd = A.forward_returns(close, 2)
        assert fwd.at[CAL[0], "A.SH"] == pytest.approx(F2, abs=1e-9)
        assert fwd.at[CAL[0], "B.SH"] == pytest.approx(0.0, abs=1e-12)
        # 末日与倒数第 1 日视野未闭合
        assert pd.isna(fwd.at[CAL[-1], "A.SH"])
        assert pd.isna(fwd.at[CAL[-2], "A.SH"])
        # 倒数第 2 日（t+2=末日）已闭合
        assert fwd.at[CAL[-3], "A.SH"] == pytest.approx(F2, abs=1e-9)

    def test_h小于2全NaN与clip及入场0(self):
        close = _close3()
        assert A.forward_returns(close, 1).isna().all().all()
        # 暴跌 clip 到下界 −0.5：close[2]/close[1]−1 = 10/50−1 = −0.8
        crash = pd.DataFrame({"X.SH": [100.0, 50.0, 10.0, 10.0]}, index=CAL[:4])
        assert A.forward_returns(crash, 2).at[CAL[0], "X.SH"] == -0.5
        # 入场价 0（脏价）→ NaN，不是 inf/1.0
        zero = pd.DataFrame({"X.SH": [100.0, 0.0, 50.0, 10.0]}, index=CAL[:4])
        assert pd.isna(A.forward_returns(zero, 2).at[CAL[0], "X.SH"])

    def test_超额是减全市场等权且行内和为零(self):
        close = _close3()
        e = A.excess_returns(close, 2)
        assert e.at[CAL[0], "A.SH"] == pytest.approx(F2 - EW3, abs=1e-12)
        assert e.at[CAL[0], "B.SH"] == pytest.approx(-EW3, abs=1e-12)
        assert float(e.loc[CAL[0]].sum()) == pytest.approx(0.0, abs=1e-12)


class TestTrackRecord:
    def _run(self) -> dict:
        return A.track_record(
            _close3(),
            _scores_by_date3(),
            names=_names3(),
            top_n=2,
            bucket_min_names=3,
        )

    def test_逐日收益基准超额(self):
        out = self._run()
        assert out["horizons"] == [2, 3, 5, 10]
        d0 = out["daily"][0]
        assert d0["date"] == "2026-01-05"
        # Top2 = 甲(涨) + 乙(平)：ret = F/2，bench = F/3，excess = F/6
        assert d0["ret_2"] == pytest.approx(F2 / 2, abs=2e-5)
        assert d0["bench_2"] == pytest.approx(F2 / 3, abs=2e-5)
        assert d0["excess_2"] == pytest.approx(F2 / 6, abs=2e-5)
        assert d0["ret_10"] == pytest.approx(F10 / 2, abs=2e-5)
        assert d0["excess_10"] == pytest.approx(F10 / 6, abs=2e-5)
        # h=10 视野未闭合 → None（不是 0）
        assert out["daily"][2]["ret_10"] is None
        assert out["daily"][2]["excess_10"] is None

    def test_窗口汇总bp与胜率(self):
        out = self._run()
        w = {x["window"]: x for x in out["windows"]}
        assert set(w) == {"20d", "60d", "120d", "all"}
        h5 = w["all"]["h5"]
        assert h5["n_days"] == 5  # 5 个计分日全部 h5 闭合
        assert h5["mean_excess_bp"] == pytest.approx(F5 / 6 * 1e4, abs=0.2)
        assert h5["mean_ret_bp"] == pytest.approx(F5 / 2 * 1e4, abs=0.2)
        assert h5["hit_rate"] == 1.0
        h10 = w["all"]["h10"]
        assert h10["n_days"] == 2  # 仅前 2 个计分日闭合
        assert h10["mean_excess_bp"] == pytest.approx(F10 / 6 * 1e4, abs=0.2)

    def test_分箱单调与样本数(self):
        out = self._run()
        b = out["buckets"]
        assert b["labels"] == ["<50", "50-70", "70-85", "85+"]
        # 丙(30) → <50，乙(60) → 50-70，甲(90) → 85+；70-85 无样本
        assert b["n_days"] == [5, 5, 0, 5]
        assert b["mean_excess_bp"][0] == pytest.approx(-F5 / 3 * 1e4, abs=0.2)
        assert b["mean_excess_bp"][1] == pytest.approx(-F5 / 3 * 1e4, abs=0.2)
        assert b["mean_excess_bp"][2] is None
        assert b["mean_excess_bp"][3] == pytest.approx(F5 * 2 / 3 * 1e4, abs=0.2)
        assert b["monotonicity"] == pytest.approx(0.855, abs=0.02)

    def test_累计超额曲线(self):
        out = self._run()
        assert set(out["cum_excess"]) == {"h2", "h3", "h5", "h10"}
        h2 = out["cum_excess"]["h2"]
        assert len(h2) == 5
        assert h2[0] == {"date": "2026-01-05", "cum": pytest.approx(F2 / 6, abs=1e-4)}
        assert h2[-1]["cum"] == pytest.approx(F2 / 6 * 5, abs=1e-4)

    def test_最近案例_已实现与持有中(self):
        out = self._run()
        assert len(out["recent"]) == 15  # 5 日 × 3 标的
        got = {(r["date"], r["symbol"]): r for r in out["recent"]}
        # 第 0 日：h10 已闭合（出场日 t+10 ≤ 末日），浮动收益=入场到末日
        d0 = got[("2026-01-05", "A.SH")]
        assert d0["name"] == "甲股"
        assert d0["status"] == "已实现h10"
        assert d0["ret_10"] == pytest.approx(F10, abs=2e-5)
        assert d0["ret_so_far"] == pytest.approx(1.05**10 - 1, abs=2e-5)
        # 第 2 日：t+10 > 末日 → 持有中，浮动收益=入场(第 3 日收盘)至今
        d2 = got[("2026-01-07", "A.SH")]
        assert d2["status"] == "持有中"
        assert d2["ret_10"] is None  # 未闭合 → None，绝不填 0
        assert d2["hold_days"] == 8
        assert d2["ret_so_far"] == pytest.approx(F8, abs=2e-5)
        # 名字缺省 → None（不编造）
        assert got[("2026-01-05", "B.SH")]["name"] == "乙股"
        assert got[("2026-01-05", "C.SH")]["name"] is None

    def test_最近案例symbol归前缀式(self):
        """scores 内部为后缀式；recent 输出须归一为前端/API 契约的前缀式。"""
        close = pd.DataFrame(
            {"300931.SZ": 100.0, "600519.SH": 100.0},
            index=pd.bdate_range("2026-01-05", periods=5),
        )
        scores = {
            CAL[i]: pd.DataFrame(
                {"symbol": ["300931.SZ", "600519.SH"], "score": [90.0, 30.0]}
            )
            for i in range(5)
        }
        out = A.track_record(close, scores, top_n=1, bucket_min_names=2)
        syms = {r["symbol"] for r in out["recent"]}
        assert syms == {"SZ300931", "SH600519"}


class TestEventStudy:
    def _run(self) -> dict:
        grown = [100.0 * 1.05**i for i in range(len(CAL))]
        close = pd.DataFrame(
            {
                "A.SH": grown,
                "B.SH": 100.0,
                "C.SH": 100.0,
                "D.SH": 100.0,
                "E.SH": 100.0,
            },
            index=CAL,
        )
        rows = [
            {"symbol": s, "score": v}
            for s, v in (
                ("A.SH", 90),
                ("B.SH", 60),
                ("C.SH", 30),
                ("D.SH", 20),
                ("E.SH", 10),
            )
        ]
        scores = {CAL[i]: pd.DataFrame(rows) for i in range(2)}
        events = pd.DataFrame(
            {
                "trade_date": [CAL[0], CAL[1], CAL[0]],
                "symbol": ["A.SH", "A.SH", "E.SH"],
            }
        )
        return A.event_study(
            close, scores, {"lhb": events}, car_days=20, min_daily_names=5
        )

    def test_分位与样本计数(self):
        fam = self._run()["families"]["lhb"]
        assert fam["n_events"] == 3
        assert fam["n_q5"] == 2  # A 两日（pct=100）
        assert fam["n_q1"] == 1  # E（pct=20 恰在界内）
        assert fam["n_scored"] == 3

    def test_CAR点值_k从2起_未闭合为None(self):
        fam = self._run()["families"]["lhb"]
        assert len(fam["car_q5_bp"]) == 20
        # k=2（第 1 点）：q5=甲超额(4/5 涨幅)，q1=戊(−1/5)，spread=整幅
        assert fam["car_q5_bp"][0] == pytest.approx(F2 * 0.8 * 1e4, abs=0.2)
        assert fam["car_q1_bp"][0] == pytest.approx(-F2 * 0.2 * 1e4, abs=0.2)
        assert fam["spread_bp"][0] == pytest.approx(F2 * 1e4, abs=0.2)
        # k=11（第 10 点）：仅第 0 日闭合，仍有值
        assert fam["car_q5_bp"][9] == pytest.approx((1.05**10 - 1) * 0.8 * 1e4, abs=0.2)
        # k=12 起全部未闭合 → None
        assert fam["car_q5_bp"][10] is None
        assert fam["car_q5_bp"][-1] is None
        assert fam["spread_bp"][-1] is None

    def test_无事件族返回空序列(self):
        close = _close3()
        out = A.event_study(close, _scores_by_date3(), {"block": pd.DataFrame()})
        fam = out["families"]["block"]
        assert fam["n_events"] == 0
        assert (
            fam["car_q5_bp"] == []
        )  # 无事件 → 空列表（有事件但全未闭合才是 None 序列）


class TestICDecay:
    def _run(self) -> dict:
        return A.ic_decay(
            _close3(),
            _scores_by_date3(),
            horizons=(2, 3, 10),
            min_names=3,
            rolling_h=2,
            rolling_window=2,
        )

    def test_衰减与有效日数(self):
        out = self._run()
        d = {x["h"]: x for x in out["decay"]}
        # 分数秩 [3,2,1] vs 收益秩 [3,1.5,1.5] → r = 1.5/√3
        ic = 1.5 / np.sqrt(3.0)
        assert d[2]["ic"] == pytest.approx(ic, abs=5e-4)
        assert d[3]["ic"] == pytest.approx(ic, abs=5e-4)
        assert d[2]["n_days"] == 5
        assert d[10]["n_days"] == 2
        assert d[2]["ic_pos_share"] == 1.0
        # 每日 IC 完全相同 → std=0 → ICIR 无定义记 None（不填 0）
        assert d[2]["icir"] is None
        assert out["decay"][0]["h"] == 2  # 顺序随 horizons

    def test_滚动IC窗口(self):
        out = self._run()
        roll = out["rolling"]
        assert roll["h"] == 2 and roll["window"] == 2
        assert len(roll["series"]) == 4  # 5 个有效日 → 2 日窗滚动 4 点
        assert roll["series"][0]["ic"] == pytest.approx(1.5 / np.sqrt(3.0), abs=5e-4)


class TestSeatStats:
    def _seat(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                # 净买 → 事件
                ("2026-01-05", "A.SH", "机构专用", 1e8, 0.0),
                ("2026-01-05", "C.SH", "华鑫证券上海分公司", 1e8, 0.0),
                # 净卖 → 不是事件
                ("2026-01-05", "B.SH", "机构专用", 0.0, 5e7),
                # 同日双向相抵净 0 → 不是事件
                ("2026-01-05", "A.SH", "散户大本营", 1e8, 1e8),
            ],
            columns=["trade_date", "symbol", "seat_name", "buy_amount", "sell_amount"],
        )

    def _run(self) -> dict:
        return A.seat_stats(self._seat(), _close3(), horizon=5, min_trades=1)

    def test_净买口径与已闭合样本(self):
        out = self._run()
        assert out["n_events"] == 2  # 净卖/净 0 不算
        assert out["horizon"] == 5
        assert out["windows"] == ["20", "60", "all"]
        # 12 日 < 20 → 三窗同界（退化全史）：全市场胜率 1/3
        for label in ("20", "60", "all"):
            assert out["baseline_win_rate"][label] == pytest.approx(1 / 3, abs=5e-4)

    def test_席位榜与分席(self):
        out = self._run()
        seats = {r["seat_name"]: r for r in out["seats"]}
        inst = seats["机构专用"]
        assert inst["class"] == "inst"
        assert inst["class_label"] == "机构专用"
        all_w = inst["windows"]["all"]
        assert all_w == {
            "n": 1,
            "win_rate": 1.0,
            "avg_excess_bp": pytest.approx(F5 * 2 / 3 * 1e4, abs=0.2),
        }
        # 12 日窗不足 → 三窗退化全史（同一组指标）
        assert inst["windows"]["20"] == inst["windows"]["all"]
        assert inst["windows"]["60"] == inst["windows"]["all"]
        assert inst["total_net_yi"] == pytest.approx(1.0, abs=1e-9)
        assert inst["last_seen"] == "2026-01-05"
        br = seats["华鑫证券上海分公司"]
        assert br["class"] == "branch"
        assert br["windows"]["all"]["win_rate"] == 0.0
        assert br["windows"]["all"]["avg_excess_bp"] == pytest.approx(
            -F5 / 3 * 1e4, abs=0.2
        )
        # 全史胜率高者排前
        assert out["seats"][0]["seat_name"] == "机构专用"
        # 分席聚合
        cls = {c["class"]: c for c in out["classes"]}
        assert cls["inst"]["win_rate"] == 1.0
        assert cls["inst"]["n_seats"] == 1
        assert cls["branch"]["win_rate"] == 0.0
        # 散户账户（净 0）不出现
        assert "散户大本营" not in seats

    def test_样本不足不进榜_仍进分席(self):
        out = A.seat_stats(self._seat(), _close3(), horizon=5, min_trades=5)
        assert out["seats"] == []
        cls = {c["class"]: c for c in out["classes"]}
        assert cls["inst"]["n_events"] == 1

    def test_窗口切片近20与全史_eq日历不足退化(self):
        # 25 个交易日：窗 20 → 起点第 6 个交易日（索引 5）；窗 60 不足 → 全史
        cal = pd.bdate_range("2026-01-05", periods=25)
        close = pd.DataFrame({"A.SH": 100.0}, index=cal)  # 单标的 → 超额恒 0
        seat = pd.DataFrame(
            [
                # 索引 0（出近 20 窗；h5 已闭合）
                (cal[0].strftime("%Y-%m-%d"), "A.SH", "机构专用", 1e8, 0.0),
                # 索引 18（在近 20 窗内；18+5=23 ≤ 24 已闭合）
                (cal[18].strftime("%Y-%m-%d"), "A.SH", "机构专用", 2e8, 0.0),
            ],
            columns=["trade_date", "symbol", "seat_name", "buy_amount", "sell_amount"],
        )
        out = A.seat_stats(seat, close, min_trades=1)
        row = out["seats"][0]
        assert row["windows"]["all"]["n"] == 2
        assert row["windows"]["20"]["n"] == 1
        assert row["windows"]["60"]["n"] == 2  # 25 ≤ 60 → 退化全史
        assert row["windows"]["all"]["win_rate"] == 0.0  # 超额恒 0 → 不 > 0
        # 全史 baseline 也恒 0
        assert out["baseline_win_rate"]["all"] == 0.0

    def test_全史样本达标但按cap截断(self):
        out = A.seat_stats(self._seat(), _close3(), horizon=5, min_trades=1, list_cap=1)
        assert len(out["seats"]) == 1
        assert out["seats"][0]["seat_name"] == "机构专用"  # 全史胜率高者优先保留


class TestClassifySeat:
    @pytest.mark.parametrize(
        ("seat", "expect"),
        [
            ("机构专用", "inst"),
            ("沪股通专用", "north"),
            ("深股通专用", "north"),
            ("华泰证券股份有限公司总部", "branch"),
            ("东方财富证券股份有限公司拉萨东环路第二证券营业部", "branch"),
            ("中信建投证券股份有限公司北京朝阳分公司", "branch"),
            ("某某私募基金管理有限公司", "other"),
            ("", "other"),
            (None, "other"),
        ],
    )
    def test_席位分类只按文本(self, seat, expect):
        assert A.classify_seat(seat) == expect


class TestIndustryFlow:
    CAL_I = pd.bdate_range("2026-01-05", periods=25)  # 窗=20 → 起点为第 5 个交易日

    def _industry(self) -> pd.Series:
        return pd.Series(
            {
                "600000.SH": "银行",
                "600519.SH": "白酒",
                "000001.SZ": "银行",
                "600036.SH": "银行",
            }
        )

    def _run(self) -> list[dict]:
        c = self.CAL_I
        lhb = pd.DataFrame(
            [
                ("2026-01-09", "600000.SH", 1e8),  # 出窗 → 不计
                ("2026-01-12", "600000.SH", 2e8),
                ("2026-01-19", "600519.SH", 5e8),
            ],
            columns=["trade_date", "symbol", "lhb_net_buy"],
        )
        inst = pd.DataFrame(
            [
                ("2026-01-13", "600000.SH", 1e8),
                ("2026-01-14", "600519.SH", -2e8),
            ],
            columns=["trade_date", "symbol", "inst_net_buy"],
        )
        dzjy = pd.DataFrame(
            [
                # 机构买方 → +
                ("2026-01-12", "600519.SH", 3e8, "机构专用", "某营业部"),
                # 机构卖方 → −
                ("2026-01-13", "000001.SZ", 1e8, "某营业部", "机构专用"),
            ],
            columns=["trade_date", "symbol", "amount", "seat_buyer", "seat_seller"],
        )
        scores = pd.DataFrame(
            {
                "symbol": ["600000.SH", "000001.SZ", "600519.SH"],
                "score": [80.0, 60.0, 90.0],
            }
        )
        return A.industry_flow(
            self._industry(),
            c,
            lhb=lhb,
            inst=inst,
            dzjy=dzjy,
            scores_latest=scores,
            window=20,
        )

    def test_窗过滤与符号与排序(self):
        rows = self._run()
        assert [r["industry"] for r in rows] == ["白酒", "银行"]
        wine = rows[0]
        assert wine["lhb_net_yi"] == pytest.approx(5.0)
        assert wine["inst_net_yi"] == pytest.approx(-2.0)
        assert wine["dzjy_inst_net_yi"] == pytest.approx(3.0)  # 机构买方为 +
        assert wine["n_stocks"] == 1
        assert wine["avg_score"] == pytest.approx(90.0)
        bank = rows[1]
        assert bank["lhb_net_yi"] == pytest.approx(2.0)  # 出窗的 1e8 未计入
        assert bank["inst_net_yi"] == pytest.approx(1.0)
        assert bank["dzjy_inst_net_yi"] == pytest.approx(-1.0)  # 机构卖方为 −
        assert bank["n_stocks"] == 2
        assert bank["avg_score"] == pytest.approx(70.0)

    def test_空日历返回空(self):
        assert A.industry_flow(self._industry(), pd.DatetimeIndex([])) == []


class TestJsonSafety:
    def test_全部产物allow_nan_false可序列化(self):
        close = _close3()
        scores = _scores_by_date3()
        tr = A.track_record(close, scores, names=_names3(), top_n=2, bucket_min_names=3)
        ev = A.event_study(
            close,
            scores,
            {"lhb": pd.DataFrame({"trade_date": [CAL[0]], "symbol": ["A.SH"]})},
            car_days=5,
            min_daily_names=3,
        )
        ics = A.ic_decay(close, scores, horizons=(2, 5), min_names=3)
        st = A.seat_stats(
            pd.DataFrame(
                [("2026-01-05", "A.SH", "机构专用", 1e8, 0.0)],
                columns=[
                    "trade_date",
                    "symbol",
                    "seat_name",
                    "buy_amount",
                    "sell_amount",
                ],
            ),
            close,
        )
        industry = pd.Series({"A.SH": "银行", "B.SH": "银行", "C.SH": "银行"})
        fl = A.industry_flow(industry, CAL)
        for payload in (tr, ev, ics, st, fl):
            json.dumps(payload, allow_nan=False)  # 不得抛 ValueError
