"""聪明钱信号纯函数单测（金样 + 反前视封存 + 缺失语义）。

钉住的行为：
- 柱金样：同股同日多行求和 / 除一次性市场成交额；5 日窗边界（出窗 NaN 不补 0）
- z 柱：恒定序列（std=0）→ NaN；缺失日为 NaN 不当 0
- 封存：毒化 fwd_ret_* 后全部柱值逐位不变（东财前向收益永不入信号）
- 池：5 交易日窗并集、出窗移除、周末事件归下一交易日、两融 |z20|≥2 入池
- 复合分：池内截面分位、缺失柱记 50 且 covered 计数（缺失 ≠ 0）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.shared.smart_money import signals

CAL = pd.bdate_range("2024-01-01", periods=30)  # 30 个交易日


def _inst_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": CAL[0],
                "symbol": "A.SH",
                "inst_net_buy": 2e7,
                "market_amount": 6e8,
            },
            {
                "trade_date": CAL[0],
                "symbol": "B.SH",
                "inst_net_buy": -1e7,
                "market_amount": 5e8,
            },
        ]
    )


class TestPillarGolden:
    def test_机构净买占比5日累计_金样与窗口边界(self):
        # Act
        out = signals.compute_pillars(
            CAL, {"lhb_inst": _inst_rows()}, None, names=["inst_net_ratio_5d"]
        )["inst_net_ratio_5d"]

        # Assert：当日值 = 净买额/市场成交额
        assert out.loc[CAL[0], "A.SH"] == pytest.approx(2e7 / 6e8)
        assert out.loc[CAL[0], "B.SH"] == pytest.approx(-1e7 / 5e8)
        # 窗内携带：第 5 个交易日仍在窗内
        assert out.loc[CAL[4], "A.SH"] == pytest.approx(2e7 / 6e8)
        # 出窗：第 6 个交易日起 NaN（不是 0）
        assert pd.isna(out.loc[CAL[5], "A.SH"])

    def test_同股同日多行按净额求和_市场成交额不重复计(self):
        inst = pd.DataFrame(
            [
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "inst_net_buy": 2e7,
                    "market_amount": 6e8,
                },
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "inst_net_buy": 1e7,
                    "market_amount": 6e8,
                },
            ]
        )
        out = signals.compute_pillars(
            CAL, {"lhb_inst": inst}, None, names=["inst_net_ratio_5d"]
        )["inst_net_ratio_5d"]
        assert out.loc[CAL[0], "A.SH"] == pytest.approx(3e7 / 6e8)

    def test_大宗折溢率按成交额加权(self):
        bt = pd.DataFrame(
            [
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "premium": 0.10,
                    "amount": 3.0,
                },
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "premium": -0.10,
                    "amount": 1.0,
                },
            ]
        )
        out = signals.compute_pillars(
            CAL, {"block_trade": bt}, None, names=["dzjy_premium_5d"]
        )["dzjy_premium_5d"]
        # (0.10*3 + (-0.10)*1) / 4 = 0.05
        assert out.loc[CAL[0], "A.SH"] == pytest.approx(0.05)


class TestDzjyInstPillars:
    """机构专用**买方**（接盘）大宗柱：机构出货行不得计入、不入事件池。"""

    def _detail(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                # 机构专用作买方（接盘）→ 计入
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "seat_buyer": "机构专用",
                    "seat_seller": "某证券营业部",
                    "premium": 0.10,
                    "amount": 3.0,
                    "amount_ratio": 0.02,
                },
                # 机构专用作买方第二笔 → 并入同股同日
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "seat_buyer": "机构专用",
                    "seat_seller": "机构专用",
                    "premium": -0.10,
                    "amount": 1.0,
                    "amount_ratio": 0.01,
                },
                # 机构专用作**卖方**（出货）→ 接盘柱不得计入（毒值 9.9 校验）
                {
                    "trade_date": CAL[0],
                    "symbol": "B.SH",
                    "seat_buyer": "某证券营业部",
                    "seat_seller": "机构专用",
                    "premium": 9.9,
                    "amount": 100.0,
                    "amount_ratio": 0.5,
                },
            ]
        )

    def test_机构接盘占比只计机构专用买方(self):
        # Act
        out = signals.compute_pillars(
            CAL,
            {"dzjy_detail": self._detail()},
            None,
            names=["dzjy_inst_amount_ratio_5d"],
        )["dzjy_inst_amount_ratio_5d"]

        # Assert：A 两笔机构买方占比求和 (0.02+0.01)；5 日窗内携带、出窗 NaN
        assert out.loc[CAL[0], "A.SH"] == pytest.approx(0.03)
        assert out.loc[CAL[4], "A.SH"] == pytest.approx(0.03)
        assert pd.isna(out.loc[CAL[5], "A.SH"])
        # B 是机构出货 → 不出现该列（缺失 ≠ 0）
        assert "B.SH" not in out.columns

    def test_机构接盘折溢率按成交额加权_机构出货行不污染(self):
        out = signals.compute_pillars(
            CAL,
            {"dzjy_detail": self._detail()},
            None,
            names=["dzjy_inst_premium_5d"],
        )["dzjy_inst_premium_5d"]
        # (0.10×3 + (−0.10)×1) / (3+1) = 0.05
        assert out.loc[CAL[0], "A.SH"] == pytest.approx(0.05)
        assert "B.SH" not in out.columns

    def test_缺seat_buyer列返回全空(self):
        df = self._detail().drop(columns=["seat_buyer"])
        out = signals.compute_pillars(
            CAL, {"dzjy_detail": df}, None, names=["dzjy_inst_premium_5d"]
        )["dzjy_inst_premium_5d"]
        assert out.empty

    def test_大宗明细不入事件池(self):
        # 池来源仍是 _EVENT_TABLES（与 block_trade 同事件，不重复入池）
        pool = signals.compute_pool(CAL, {"dzjy_detail": self._detail()})
        assert not pool.to_numpy().any()


class TestMarginPillars:
    def _margin(self) -> pd.DataFrame:
        rows = []
        for i, d in enumerate(CAL):
            rows.append(
                {
                    "trade_date": d,
                    "symbol": "CONST.SH",
                    "finance_net": 1e4,
                    "finance_balance": 1e5,
                }
            )
            rows.append(
                {
                    "trade_date": d,
                    "symbol": "VAR.SH",
                    "finance_net": 1e4 + (i % 3) * 1e4,
                    "finance_balance": 1e5 + i * 1e3,
                }
            )
        return pd.DataFrame(rows)

    def test_恒定序列z为NaN不补0(self):
        z = signals.compute_pillars(CAL, {}, self._margin(), names=["margin_net_5d_z"])[
            "margin_net_5d_z"
        ]
        assert np.isfinite(z.loc[CAL[-1], "VAR.SH"])
        # 恒定序列 std=0 → NaN（缺失 ≠ 0）
        assert pd.isna(z.loc[CAL[-1], "CONST.SH"])

    def test_两融缺失日不当0_长缺口后为NaN(self):
        margin = pd.DataFrame(
            [
                {
                    "trade_date": d,
                    "symbol": "A.SH",
                    "finance_net": 1e4,
                    "finance_balance": 1e5,
                }
                for d in CAL[:10]
            ]
        )
        z = signals.compute_pillars(CAL, {}, margin, names=["margin_net_5d_z"])[
            "margin_net_5d_z"
        ]
        assert pd.isna(z.loc[CAL[25], "A.SH"])

    def test_余额20日变化率(self):
        chg = signals.compute_pillars(
            CAL, {}, self._margin(), names=["margin_bal_chg_20d"]
        )["margin_bal_chg_20d"]
        # VAR: balance = 1e5 + i*1e3 → CAL[20]/CAL[0]-1 = 2e4/1e5
        assert chg.loc[CAL[20], "VAR.SH"] == pytest.approx(2e4 / 1e5)
        assert pd.isna(chg.loc[CAL[19], "VAR.SH"])  # 不足 21 行

    def test_余额截尾滞后按最近披露ffill_仍出值(self):
        """两融披露滞后行情 1 日（截尾日缺当日余额）→ 存量口径 ffill 出值。

        余额是存量水平：以最近披露值充当当日水平不构成前视；流量列
        （finance_net）不 ffill，其尾部容忍由 5 日均的 min_periods 承担。
        """
        margin = self._margin()
        # 抹掉 VAR.SH 最后一天的余额（模拟：行情已到 T，两融只到 T-1）
        margin = margin[
            ~((margin["trade_date"] == CAL[-1]) & (margin["symbol"] == "VAR.SH"))
        ]
        chg = signals.compute_pillars(
            CAL, {}, margin, names=["margin_bal_chg_20d"]
        )["margin_bal_chg_20d"]
        # 末值 ffill 取 CAL[28] 余额（1e5+28e3），基准 CAL[9]（1e5+9e3）
        expect = (1e5 + 28e3) / (1e5 + 9e3) - 1.0
        assert chg.loc[CAL[-1], "VAR.SH"] == pytest.approx(expect)


class TestForwardReturnSealed:
    def test_毒化fwd列后全部柱值逐位不变(self):
        base = pd.DataFrame(
            [
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "reason": "日涨幅偏离值达到7%的前5只证券",
                    "lhb_net_buy": 1e8,
                    "market_amount": 5e8,
                    "fwd_ret_1d": 1.0,
                    "fwd_ret_2d": 2.0,
                    "fwd_ret_5d": 3.0,
                    "fwd_ret_10d": 4.0,
                }
            ]
        )
        poisoned = base.copy()
        poisoned[["fwd_ret_1d", "fwd_ret_2d", "fwd_ret_5d", "fwd_ret_10d"]] = -999.0

        a = signals.compute_pillars(CAL, {"lhb_detail": base}, None)
        b = signals.compute_pillars(CAL, {"lhb_detail": poisoned}, None)

        for k in a:
            pd.testing.assert_frame_equal(a[k], b[k])


class TestPool:
    def test_事件窗口5日并集与出窗(self):
        lhb = pd.DataFrame(
            [
                {
                    "trade_date": CAL[0],
                    "symbol": "A.SH",
                    "lhb_net_buy": 1e7,
                    "market_amount": 1e9,
                }
            ]
        )
        sv = pd.DataFrame([{"trade_date": CAL[1], "symbol": "B.SH", "inst_count": 5}])
        pool = signals.compute_pool(CAL, {"lhb_detail": lhb, "survey": sv})

        assert bool(pool.loc[CAL[0], "A.SH"]) and bool(pool.loc[CAL[4], "A.SH"])
        assert not bool(pool.loc[CAL[5], "A.SH"])
        assert bool(pool.loc[CAL[1], "B.SH"]) and bool(pool.loc[CAL[5], "B.SH"])
        assert not bool(pool.loc[CAL[6], "B.SH"])

    def test_周末事件归入下一交易日(self):
        sat = pd.Timestamp("2024-01-06")  # 周六；下个交易日 = 2024-01-08（CAL[5]）
        sv = pd.DataFrame([{"trade_date": sat, "symbol": "C.SH", "inst_count": 3}])
        pool = signals.compute_pool(CAL, {"survey": sv})
        assert not bool(pool.loc[CAL[0], "C.SH"])  # 周五（事件尚未发生）
        assert bool(pool.loc[CAL[5], "C.SH"])

    def test_两融异动入池(self):
        rows = []
        for i, d in enumerate(CAL):
            net = 0.0 if i < 25 else 1e6
            rows.append(
                {
                    "trade_date": d,
                    "symbol": "A.SH",
                    "finance_net": net,
                    "finance_balance": 1e5,
                }
            )
        pool = signals.compute_pool(CAL, {}, pd.DataFrame(rows))
        assert bool(pool.loc[CAL[-1], "A.SH"])


class TestScores:
    def test_复合分填充分位与覆盖数(self):
        pool = pd.DataFrame(False, index=CAL, columns=["A.SH", "B.SH", "C.SH"])
        pool.loc[CAL[0]] = [True, True, True]
        p1 = pd.DataFrame({"A.SH": [5.0], "B.SH": [3.0]}, index=[CAL[0]])
        p2 = pd.DataFrame({"A.SH": [1.0]}, index=[CAL[0]])

        sc = signals.compute_scores(
            CAL[0], pool, {"p1": p1, "p2": p2}, [("p1", 1), ("p2", 1)]
        ).set_index("symbol")

        # A：两柱均有值且各自池内第一 → (100+100)/2
        assert sc.loc["A.SH", "score"] == pytest.approx(100.0)
        assert sc.loc["A.SH", "covered"] == 2
        # B：p1 池内分位 50（2 人中靠后），p2 缺失记 50 → (50+50)/2，covered=1
        assert sc.loc["B.SH", "score"] == pytest.approx(50.0)
        assert sc.loc["B.SH", "covered"] == 1
        # C：全缺 → 50 且 covered=0（缺失≠0 的可审计披露）
        assert sc.loc["C.SH", "score"] == pytest.approx(50.0)
        assert sc.loc["C.SH", "covered"] == 0
        # 池外标的不得出现
        assert "D.SH" not in sc.index

    def test_负向柱分位取反_HK先例方向对齐(self):
        # 负向 IC 柱（方向=−1）：原值最强者 raw pct=100 → 对齐 0，
        # 使柱分高 = 历史有利方向（否则复合分被反向柱主导）。
        pool = pd.DataFrame(False, index=CAL, columns=["A.SH", "B.SH"])
        pool.loc[CAL[0]] = [True, True]
        p1 = pd.DataFrame({"A.SH": [5.0], "B.SH": [3.0]}, index=[CAL[0]])

        sc = signals.compute_scores(CAL[0], pool, {"p1": p1}, [("p1", -1)]).set_index(
            "symbol"
        )

        assert sc.loc["A.SH", "pct_p1"] == pytest.approx(0.0)  # 100−100
        assert sc.loc["B.SH", "pct_p1"] == pytest.approx(50.0)  # 100−50
        assert sc.loc["A.SH", "score"] == pytest.approx(0.0)
        assert sc.loc["A.SH", "val_p1"] == pytest.approx(5.0)  # 原值不变（下钻展示）

    def test_池为空日返回空表(self):
        pool = pd.DataFrame(False, index=CAL, columns=["A.SH"])
        sc = signals.compute_scores(CAL[0], pool, {}, [("p1", 1)])
        assert sc.empty
