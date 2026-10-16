"""聪明钱采集脚本单测（不触网：akshare 全部打桩）。

钉住的行为：
- 归一化契约：中文列→英文列、代码后缀式、日期字符串、金额元/量股、
  前向收益列改名 fwd_*（封存）、占比列恒等式定标（百分数→分数）
- 幂等：重复合并同行数不变；同键新数据覆盖旧数据（修订语义）
- 水位：切片失败停在上一成功切片（下次重叠自愈）；全空不推进水位
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.scripts import smart_money_sync as sms

# ---------------------------------------------------------------- fixtures


def _lhb_detail_raw(n: int = 12, percent_ratio: bool = True) -> pd.DataFrame:
    """akshare stock_lhb_detail_em 原样列（单位：元，占比按百分数或分数）。"""
    rows = []
    for i in range(n):
        lhb_amount = 5e8
        market_amount = 2e9
        net = 1e8
        net_ratio = (net / lhb_amount) * (100.0 if percent_ratio else 1.0)  # 20.0 或 0.2
        rows.append(
            {
                "序号": i + 1,
                "代码": f"00{i:04d}",
                "名称": f"测试{i}",
                "上榜日": "2026-09-02",
                "解读": "3家机构买入，成功率26.20%",
                "收盘价": 10.0 + i,
                "涨跌幅": 10.02,
                "龙虎榜净买额": net,
                "龙虎榜买入额": 2e8,
                "龙虎榜卖出额": 1e8,
                "龙虎榜成交额": lhb_amount,
                "市场总成交额": market_amount,
                "净买额占总成交比": net_ratio,
                "成交额占总成交比": (lhb_amount / market_amount) * (100.0 if percent_ratio else 1.0),
                "换手率": 12.5,
                "流通市值": 5e9,
                "上榜原因": "日涨幅偏离值达到7%的前5只证券",
                "上榜后1日": 1.2,
                "上榜后2日": -0.5,
                "上榜后5日": 3.1,
                "上榜后10日": 8.8,
            }
        )
    return pd.DataFrame(rows)


def _lhb_inst_raw(n: int = 12, mktcap_yi: float = 38.31, percent_ratio: bool = True) -> pd.DataFrame:
    """akshare stock_lhb_jgmmtj_em 原样列（流通市值实测为亿元；占比发布口径）。"""
    rows = []
    for i in range(n):
        inst_net = 2e7
        market_amount = 6e8
        rows.append(
            {
                "序号": i + 1, "代码": f"60{i:04d}", "名称": f"机构股{i}",
                "收盘价": 20.0 + i, "涨跌幅": 5.0, "买方机构数": 3, "卖方机构数": 1,
                "机构买入总额": 3e7, "机构卖出总额": 1e7, "机构买入净额": inst_net,
                "市场总成交额": market_amount,
                "机构净买额占总成交额比": (inst_net / market_amount) * (100.0 if percent_ratio else 1.0),
                "换手率": 8.0, "流通市值": mktcap_yi,
                "上榜原因": "日涨幅偏离值达到7%的前5只证券", "上榜日期": "2026-09-02",
            }
        )
    return pd.DataFrame(rows)


def _survey_raw() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "序号": 1, "代码": "600036", "名称": "招商银行", "最新价": 40.0,
                "涨跌幅": 0.5, "接待机构数量": 25, "接待方式": "电话会议",
                "接待人员": "董秘", "接待地点": "线上", "接待日期": "2026-10-10",
                "公告日期": "2026-10-14",
            },
            {
                "序号": 2, "代码": "000011", "名称": "深物业A", "最新价": 8.0,
                "涨跌幅": -0.2, "接待机构数量": 3, "接待方式": "实地调研",
                "接待人员": "证代", "接待地点": "公司", "接待日期": "2026-10-09",
                "公告日期": None,  # 缺公告日 → 回退接待日
            },
        ]
    )


# ---------------------------------------------------------------- normalize


class TestNormalizeLhbDetail:
    def test_契约列与前向收益封存(self):
        # Act
        out = sms.normalize_lhb_detail(_lhb_detail_raw())

        # Assert：中文列消失，英文契约列齐全
        assert "上榜后1日" not in out.columns
        for col in ("symbol", "trade_date", "lhb_net_buy", "net_ratio", "fwd_ret_1d", "fwd_ret_10d"):
            assert col in out.columns
        assert out["symbol"].iloc[0] == "000000.SZ"
        assert out["trade_date"].iloc[0] == "2026-09-02"
        assert out["fwd_ret_5d"].iloc[0] == 3.1
        # 流通市值已是元尺度（5e9）→ 定标器不得再乘
        assert out["float_mktcap"].iloc[0] == pytest.approx(5e9)

    def test_占比百分数定标归一到分数(self):
        # Act
        out = sms.normalize_lhb_detail(_lhb_detail_raw(percent_ratio=True))

        # Assert：20.0(%) → 0.2
        assert out["net_ratio"].iloc[0] == pytest.approx(0.2)
        assert out["amount_ratio"].iloc[0] == pytest.approx(0.25)  # 5e8/2e9 = 25% → 0.25

    def test_占比本就是分数时保持原样(self):
        out = sms.normalize_lhb_detail(_lhb_detail_raw(percent_ratio=False))
        assert out["net_ratio"].iloc[0] == pytest.approx(0.2)

    def test_无效代码与无效日期被丢弃(self):
        raw = _lhb_detail_raw()
        raw.loc[0, "上榜日"] = None
        raw.loc[0, "代码"] = "BADDATA"
        out = sms.normalize_lhb_detail(raw)
        assert len(out) == 11


class TestNormalizeLhbInst:
    def test_流通市值亿元归一到元(self):
        out = sms.normalize_lhb_inst(_lhb_inst_raw(mktcap_yi=38.31))
        # Assert：38.31 亿元 → 3.831e9 元
        assert out["float_mktcap"].iloc[0] == pytest.approx(3.831e9)

    def test_占比百分数定标归一到分数(self):
        out = sms.normalize_lhb_inst(_lhb_inst_raw(percent_ratio=True))
        # 2e7/6e8 = 3.333% → 0.03333
        assert out["inst_net_ratio"].iloc[0] == pytest.approx(2e7 / 6e8)
        assert out["symbol"].iloc[0] == "600000.SH"


class TestNormalizeBlockTrade:
    def test_万股万元换算为股与元(self):
        raw = pd.DataFrame(
            [
                {
                    "序号": 1, "交易日期": "2026-10-13", "证券代码": "301050", "证券简称": "雷电微力",
                    "涨跌幅": 2.126, "收盘价": 26.42, "成交价": 20.70, "折溢率": -0.216503,
                    "成交笔数": 1, "成交总量": 494.42, "成交总额": 10234.58,
                    "成交总额/流通市值": 1.571919,
                }
            ]
        )
        # Act
        out = sms.normalize_block_trade(raw)

        # Assert
        assert out["symbol"].iloc[0] == "301050.SZ"
        assert out["volume"].iloc[0] == pytest.approx(494.42e4)
        assert out["amount"].iloc[0] == pytest.approx(10234.58e4)
        assert out["premium"].iloc[0] == pytest.approx(-0.216503)  # 分数，不再动
        assert out["amount_ratio"].iloc[0] == pytest.approx(0.01571919)  # 百分数 → 分数


class TestNormalizeSurvey:
    def test_事件日取公告日并回退接待日(self):
        out = sms.normalize_survey(_survey_raw())
        assert out["trade_date"].iloc[0] == "2026-10-14"
        assert out["trade_date"].iloc[1] == "2026-10-09"  # 缺公告日 → 接待日
        assert out["symbol"].tolist() == ["600036.SH", "000011.SZ"]


# ---------------------------------------------------------------- merge / io


class TestMergeWrite:
    def test_重复合并幂等且同键新值覆盖旧值(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sms, "SMART_MONEY_DIR", tmp_path)
        df1 = sms.normalize_lhb_detail(_lhb_detail_raw(5))
        n1 = sms.merge_write("lhb_detail", df1, sms.DATASETS["lhb_detail"]["keys"])
        assert n1 == 5

        # 同数据二次合并：行数不变（幂等）
        n2 = sms.merge_write("lhb_detail", df1, sms.DATASETS["lhb_detail"]["keys"])
        assert n2 == 5

        # 同键修订：净买额翻倍 → 覆盖旧值且行数不变
        revised = df1.copy()
        revised["lhb_net_buy"] = revised["lhb_net_buy"] * 2
        n3 = sms.merge_write("lhb_detail", revised, sms.DATASETS["lhb_detail"]["keys"])
        assert n3 == 5
        stored = pd.read_parquet(tmp_path / "lhb_detail.parquet")
        assert stored["lhb_net_buy"].iloc[0] == pytest.approx(2e8)


# ---------------------------------------------------------------- sync 水位


class TestSyncWatermark:
    def _stub_fetch(self, fail_ranges: list[tuple[str, str]]):
        def fetch(dataset: str, start: str, end: str) -> pd.DataFrame:
            for fs, fe in fail_ranges:
                if not (end < fs or start > fe):  # 区间相交
                    raise RuntimeError("stub upstream failure")
            dates = pd.date_range(start, end, freq="W-MON")
            if len(dates) == 0:
                return pd.DataFrame()
            raw = _lhb_detail_raw(len(dates))
            raw["上榜日"] = [d.strftime("%Y-%m-%d") for d in dates]
            return raw

        return fetch

    def _patch(self, tmp_path, monkeypatch, fetch):
        monkeypatch.setattr(sms, "SMART_MONEY_DIR", tmp_path)
        monkeypatch.setattr(sms, "META_PATH", tmp_path / "_sync_meta.json")
        monkeypatch.setattr(sms, "_fetch_range", fetch)
        monkeypatch.setattr(sms, "RETRY_WAITS", (0, 0, 0))

    def test_切片失败水位停在上一成功切片_下次重叠自愈(self, tmp_path, monkeypatch):
        # 2 月切片失败；1 月成功
        fetch = self._stub_fetch(fail_ranges=[("20240201", "20240229")])
        self._patch(tmp_path, monkeypatch, fetch)

        meta: dict = {}
        sms.sync_dataset("lhb_detail", pd.Timestamp("2024-01-05").date(),
                         pd.Timestamp("2024-03-10").date(), meta)
        # 水位=min(抓取覆盖 1-31, 有数据最后日 1-29) = 1-29
        assert meta["lhb_detail"]["last_date"] == "2024-01-29"

        # 上游恢复 → 再跑：从 1-26（重叠 3 天）起，越过此前失败的 2 月
        fetch2 = self._stub_fetch(fail_ranges=[])
        monkeypatch.setattr(sms, "_fetch_range", fetch2)
        sms.sync_dataset("lhb_detail", pd.Timestamp("2024-01-05").date(),
                         pd.Timestamp("2024-03-10").date(), meta)
        assert meta["lhb_detail"]["last_date"] >= "2024-03-01"

    def test_区间全空不推进水位不落盘(self, tmp_path, monkeypatch):
        # 恒空桩（模拟上游静默空响应）
        self._patch(tmp_path, monkeypatch, lambda d, s, e: pd.DataFrame())

        meta: dict = {}
        sms.sync_dataset("lhb_detail", pd.Timestamp("2024-01-05").date(),
                         pd.Timestamp("2024-01-31").date(), meta)
        assert "lhb_detail" not in meta
        assert not (tmp_path / "lhb_detail.parquet").exists()


class TestMonthChunks:
    def test_边界(self):
        chunks = sms._month_chunks(
            pd.Timestamp("2024-01-05").date(), pd.Timestamp("2024-03-10").date()
        )
        assert [(str(a), str(b)) for a, b in chunks] == [
            ("2024-01-05", "2024-01-31"),
            ("2024-02-01", "2024-02-29"),  # 闰年
            ("2024-03-01", "2024-03-10"),
        ]


class TestExtendHistory:
    def _patch(self, tmp_path, monkeypatch, calls):
        def fetch(dataset: str, start: str, end: str) -> pd.DataFrame:
            calls.append((start, end))
            dates = pd.date_range(start, end, freq="W-MON")
            if len(dates) == 0:
                return pd.DataFrame()
            raw = _lhb_detail_raw(len(dates))
            raw["上榜日"] = [d.strftime("%Y-%m-%d") for d in dates]
            return raw

        monkeypatch.setattr(sms, "SMART_MONEY_DIR", tmp_path)
        monkeypatch.setattr(sms, "META_PATH", tmp_path / "_sync_meta.json")
        monkeypatch.setattr(sms, "_fetch_range", fetch)
        monkeypatch.setattr(sms, "RETRY_WAITS", (0, 0, 0))

    def test_扩历史只补缺口_尾部水位不动(self, tmp_path, monkeypatch):
        calls: list[tuple[str, str]] = []
        self._patch(tmp_path, monkeypatch, calls)
        meta = {"lhb_detail": {"last_date": "2024-03-01", "rows": 3}}

        sms.sync_dataset(
            "lhb_detail", pd.Timestamp("2024-01-05").date(),
            pd.Timestamp("2024-06-30").date(), meta, extend_history=True,
        )

        # 抓取段 = [floor, last−1]：2024-01-05 → 2024-02-29（按月切片）
        assert calls[0][0] == "20240105"
        assert calls[-1][1] == "20240229"
        # 尾部水位保持 2024-03-01（扩档不能让增量续跑从缺口末日起跳）
        assert meta["lhb_detail"]["last_date"] == "2024-03-01"

    def test_floor不早于水位时无需扩档(self, tmp_path, monkeypatch):
        calls: list[tuple[str, str]] = []
        self._patch(tmp_path, monkeypatch, calls)
        meta = {"lhb_detail": {"last_date": "2024-03-01", "rows": 3}}

        sms.sync_dataset(
            "lhb_detail", pd.Timestamp("2024-03-01").date(),
            pd.Timestamp("2024-06-30").date(), meta, extend_history=True,
        )

        assert calls == []
        assert meta["lhb_detail"] == {"last_date": "2024-03-01", "rows": 3}


class TestCumulativeMode:
    """survey：上游=「公告日>date」近 365 天滚动全量 → 单次调用+幂等合并。

    2026-10-16 实测钉死：任何 date 都拿不到 365 天前的调研（留存窗），
    逐日循环调用是纯冗余。
    """

    def _patch(self, tmp_path, monkeypatch, calls: list[str]) -> None:
        def fetch(day: str) -> pd.DataFrame:
            calls.append(day)
            return _survey_raw()

        monkeypatch.setattr(sms, "SMART_MONEY_DIR", tmp_path)
        monkeypatch.setattr(sms, "META_PATH", tmp_path / "_sync_meta.json")
        monkeypatch.setattr(sms, "_fetch_survey_since", fetch)
        monkeypatch.setattr(sms, "RETRY_WAITS", (0, 0, 0))

    def test_单次调用_下界为start前一日_水位按数据与end收敛(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._patch(tmp_path, monkeypatch, calls)
        meta: dict = {}

        sms.sync_dataset("survey", pd.Timestamp("2026-09-01").date(),
                         pd.Timestamp("2026-10-15").date(), meta)

        # 严格大于语义 → 传 start−1 才含 start 当日；只调用一次
        assert calls == ["20260831"]
        # 水位 = min(end, 数据最大公告日=10-14)
        assert meta["survey"]["last_date"] == "2026-10-14"
        assert meta["survey"]["rows"] == 2

    def test_重复运行幂等不翻倍(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._patch(tmp_path, monkeypatch, calls)
        meta: dict = {}
        args = (pd.Timestamp("2026-09-01").date(), pd.Timestamp("2026-10-15").date())
        sms.sync_dataset("survey", *args, meta)
        sms.sync_dataset("survey", *args, meta)
        stored = pd.read_parquet(tmp_path / "survey.parquet")
        assert len(stored) == 2

    def test_扩历史不改尾部水位(self, tmp_path, monkeypatch):
        calls: list[str] = []
        self._patch(tmp_path, monkeypatch, calls)
        meta = {"survey": {"last_date": "2026-10-15", "rows": 2}}

        sms.sync_dataset("survey", pd.Timestamp("2026-09-01").date(),
                         pd.Timestamp("2026-10-20").date(), meta, extend_history=True)

        assert calls == ["20260831"]
        assert meta["survey"]["last_date"] == "2026-10-15"  # 尾部水位不动

    def test_调用失败不落盘不推进水位(self, tmp_path, monkeypatch):
        def fail(day: str) -> pd.DataFrame:
            raise RuntimeError("stub upstream failure")

        monkeypatch.setattr(sms, "SMART_MONEY_DIR", tmp_path)
        monkeypatch.setattr(sms, "META_PATH", tmp_path / "_sync_meta.json")
        monkeypatch.setattr(sms, "_fetch_survey_since", fail)
        monkeypatch.setattr(sms, "RETRY_WAITS", (0, 0, 0))
        meta: dict = {}

        sms.sync_dataset("survey", pd.Timestamp("2026-09-01").date(),
                         pd.Timestamp("2026-10-15").date(), meta)

        assert "survey" not in meta
        assert not (tmp_path / "survey.parquet").exists()


# ---------------------------------------------------------------- 席位/大宗明细（v2 数据面）


def _seat_raw() -> pd.DataFrame:
    """akshare stock_lhb_stock_detail_em 原样列：占比实测**已是分数**（6.51% → 0.0651）。"""
    return pd.DataFrame(
        [
            {
                "序号": 1, "交易营业部名称": "机构专用", "买入金额": 1.2e8,
                "买入金额-占总成交比例": 0.0651, "卖出金额": None,
                "卖出金额-占总成交比例": None, "净额": 1.2e8, "类型": "买入",
            },
            {
                "序号": 2, "交易营业部名称": "华鑫证券上海分公司", "买入金额": 8e7,
                "买入金额-占总成交比例": 0.0434, "卖出金额": 5e7,
                "卖出金额-占总成交比例": 0.0271, "净额": 3e7, "类型": "买入",
            },
            {
                "序号": 3, "交易营业部名称": None, "买入金额": 1e7,
                "买入金额-占总成交比例": 0.005, "卖出金额": None,
                "卖出金额-占总成交比例": None, "净额": 1e7, "类型": "买入",
            },
        ]
    )


def _dzjy_raw() -> pd.DataFrame:
    """akshare stock_dzjy_mrmx 原样列：成交额/流通市值实测为**百分数**（0.005926% → /100）。"""
    return pd.DataFrame(
        [
            {
                "序号": 1, "交易日期": "2026-10-15", "证券代码": "600036",
                "证券简称": "招商银行", "涨跌幅": -0.05, "收盘价": 40.0,
                "成交价": 39.2, "折溢率": -0.02, "成交量": 395918,
                "成交额": 395918 * 39.2, "成交额/流通市值": 0.5926,
                "买方营业部": "机构专用", "卖方营业部": "中信证券股份有限公司总部",
            },
            {
                "序号": 2, "交易日期": "2026-10-15", "证券代码": "000001",
                "证券简称": "平安银行", "涨跌幅": 1.2, "收盘价": 11.5,
                "成交价": 11.62, "折溢率": 0.0104, "成交量": 1000000,
                "成交额": 11620000.0, "成交额/流通市值": 0.005926,
                "买方营业部": "华泰证券股份有限公司北京分公司", "卖方营业部": "机构专用",
            },
        ]
    )


class TestNormalizeLhbSeat:
    def test_占比保持分数不缩放_对侧NaN保留(self):
        out = sms.normalize_lhb_seat(_seat_raw(), "2026-10-15", "600036.SH", "buy")

        assert len(out) == 2  # 席位名缺失行丢弃
        first = out.iloc[0]
        assert first["buy_ratio"] == pytest.approx(0.0651)  # 分数原样（不 ×100 也不 ÷100）
        assert first["buy_amount"] == pytest.approx(1.2e8)
        assert pd.isna(first["sell_amount"])  # 买入榜行对侧为 NaN → 原样保留
        assert pd.isna(first["sell_ratio"])
        assert first["trade_date"] == "2026-10-15"
        assert first["symbol"] == "600036.SH"
        assert first["side"] == "buy"
        # 双向都出现的席位两列都在
        second = out.iloc[1]
        assert second["sell_amount"] == pytest.approx(5e7)
        assert second["net_amount"] == pytest.approx(3e7)


class TestNormalizeDzjyDetail:
    def test_占比百分数归一分数_代码后缀式_折溢率分数(self):
        out = sms.normalize_dzjy_detail(_dzjy_raw())

        assert len(out) == 2
        assert list(out["symbol"]) == ["600036.SH", "000001.SZ"]  # 后缀式
        assert out.iloc[0]["trade_date"] == "2026-10-15"
        # 成交额/流通市值：百分数 → 分数（0.005926 → 0.00005926）
        assert out.iloc[1]["amount_ratio"] == pytest.approx(0.00005926)
        # 折溢率 = 分数（-0.02 = -2%），不再除 100
        assert out.iloc[0]["premium"] == pytest.approx(-0.02)
        # 席位文本保留（analytics.classify_seat 的输入）
        assert out.iloc[0]["seat_buyer"] == "机构专用"
        assert out.iloc[1]["seat_seller"] == "机构专用"
        # 价×量恒等式（单位定标）：成交额 ≈ 成交价 × 成交量
        assert out.iloc[0]["amount"] == pytest.approx(395918 * 39.2)


class TestSeatUnsupported:
    @pytest.mark.parametrize(
        ("symbol", "expect"),
        [
            ("111024.SH", True),  # 转债（沪 11 开头）
            ("110043.SH", True),
            ("123456.SZ", True),  # 转债（深 12 开头）
            ("600036.SH", False),
            ("000001.SZ", False),
            ("300750.SZ", False),
            ("11102.SH", False),  # 非 6 位
            ("11ABCD.SZ", False),  # 非全数字
        ],
    )
    def test_仅转债码判不支持(self, symbol, expect):
        assert sms._is_seat_unsupported(symbol) is expect


class TestSeatPending:
    def test_新到旧排序_完成排除_窗过滤(self):
        detail = pd.DataFrame(
            [
                ("2026-01-05", "A.SH"),
                ("2026-01-06", "A.SH"),
                ("2026-01-06", "B.SH"),
                ("2026-01-07", "A.SH"),
                ("2026-01-08", "B.SH"),
                ("2026-01-09", "C.SH"),
            ],
            columns=["trade_date", "symbol"],
        )
        out = sms._seat_pending(
            detail,
            pd.Timestamp("2026-01-06").date(),
            pd.Timestamp("2026-01-08").date(),
            {("2026-01-07", "A.SH")},
        )
        # 01-05/01-09 出窗；01-07 A 已完成；同日保持原表序（稳定排序）
        assert out == [
            ("2026-01-08", "B.SH"),
            ("2026-01-06", "A.SH"),
            ("2026-01-06", "B.SH"),
        ]
