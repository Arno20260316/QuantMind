"""聪明钱数据面读取单测（tmp parquet，不触网）。

钉住的行为：
- 两融读参：老/新两代分区（老缺 slo_sell_volume）都能读；time 三种写法混存解析
- 北向：季度差分；新进标的 delta=NaN（缺失≠0）；单季时全 NaN
- 事件时间线：5 表聚合、日期降序、fwd_* 展示列透出、前后缀入参兼容
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.shared.smart_money import datasets


@pytest.fixture(autouse=True)
def _clear_cache():
    datasets._cache.clear()
    yield
    datasets._cache.clear()


def _patch_quantdb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        datasets, "resolve_quantdb_subdir", lambda *parts: tmp_path.joinpath(*parts)
    )


class TestMarginDaily:
    def test_老新两代分区与混存时间写法(self, tmp_path, monkeypatch):
        _patch_quantdb(monkeypatch, tmp_path)
        root = tmp_path / "2_base_sector" / "margin_trading"
        (root / "dt=20240102").mkdir(parents=True)
        (root / "dt=20240103").mkdir()
        # 老代分区：无 slo_sell_volume（列错位只影响 repay 槽；balance/net 两代均正确）
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ", "time": "2024-01-02 00:00:00",
                    "finance_balance": 437799.16, "slo_volume": 1.0,
                    "finance_buy": 8575.4, "finance_repay": 12262.72,
                    "slo_repay": 0.0, "finance_net": -3687.32, "slo_net": 0.0,
                }
            ]
        ).to_parquet(root / "dt=20240102" / "data.parquet")
        # 新代分区：time 两行紧凑写法混存（跨分区还有 str/datetime64 混杂，
        # concat 后为 object → format="mixed" 逐元素解析）
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ", "time": "2024-01-03 00:00:00",
                    "finance_balance": 437000.0, "slo_volume": 1.0,
                    "finance_buy": 8000.0, "slo_sell_volume": 0.0,
                    "finance_repay": 9000.0, "slo_repay": 0.0,
                    "finance_net": -1000.0, "slo_net": 0.0,
                },
                {
                    "symbol": "600036.SH", "time": "20240103",
                    "finance_balance": 500000.0, "slo_volume": 2.0,
                    "finance_buy": 10000.0, "slo_sell_volume": 0.0,
                    "finance_repay": 8000.0, "slo_repay": 0.0,
                    "finance_net": 2000.0, "slo_net": 0.0,
                },
            ]
        ).to_parquet(root / "dt=20240103" / "data.parquet")

        # Act
        df = datasets.margin_daily("2024-01-01", "2024-01-05")

        # Assert：两分区三行全解析成功（旧实现混存写法会静默 NaT）
        assert len(df) == 3
        assert df["trade_date"].notna().all()
        assert df["trade_date"].max() == pd.Timestamp("2024-01-03")
        assert set(df.columns) == {"trade_date", "symbol", "finance_balance", "finance_net"}

    def test_区间裁剪与空区间(self, tmp_path, monkeypatch):
        _patch_quantdb(monkeypatch, tmp_path)
        root = tmp_path / "2_base_sector" / "margin_trading"
        (root / "dt=20240102").mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ", "time": "2024-01-02",
                    "finance_balance": 1.0, "slo_volume": 0.0, "finance_buy": 0.0,
                    "slo_sell_volume": 0.0, "finance_repay": 0.0, "slo_repay": 0.0,
                    "finance_net": 0.0, "slo_net": 0.0,
                }
            ]
        ).to_parquet(root / "dt=20240102" / "data.parquet")
        assert datasets.margin_daily("2024-02-01").empty
        assert len(datasets.margin_daily("2024-01-01", "2024-01-31")) == 1


class TestNorth:
    def _write_quarter(self, root: Path, q: str, rows: list[dict]) -> None:
        d = root / f"quarter={q}"
        d.mkdir(parents=True)
        pd.DataFrame(rows).to_parquet(d / "data.parquet")

    def test_季度差分_新进NaN_离场剔除(self, tmp_path, monkeypatch):
        _patch_quantdb(monkeypatch, tmp_path)
        root = tmp_path / "2_base_sector" / "hsgt_north"
        self._write_quarter(
            root, "2026Q2",
            [
                {"symbol": "600036.SH", "stock_name": "招商银行", "holding_quantity": 1,
                 "holding_percentage": 0.03, "report_date": "2026-06-30"},
                {"symbol": "000001.SZ", "stock_name": "平安银行", "holding_quantity": 1,
                 "holding_percentage": 0.01, "report_date": "2026-06-30"},
                {"symbol": "600000.SH", "stock_name": "浦发银行", "holding_quantity": 1,
                 "holding_percentage": 0.02, "report_date": "2026-06-30"},
            ],
        )
        self._write_quarter(
            root, "2026Q3",
            [
                {"symbol": "600036.SH", "stock_name": "招商银行", "holding_quantity": 1,
                 "holding_percentage": 0.04, "report_date": "2026-09-30"},
                {"symbol": "000001.SZ", "stock_name": "平安银行", "holding_quantity": 1,
                 "holding_percentage": 0.005, "report_date": "2026-09-30"},
                {"symbol": "300750.SZ", "stock_name": "宁德时代", "holding_quantity": 1,
                 "holding_percentage": 0.02, "report_date": "2026-09-30"},
            ],
        )

        d = datasets.north_delta().set_index("symbol")

        assert d.loc["600036.SH", "delta"] == pytest.approx(0.01)
        assert d.loc["000001.SZ", "delta"] == pytest.approx(-0.005)
        # 新进（无上季对比）→ NaN，绝不补 0
        assert pd.isna(d.loc["300750.SZ", "delta"])
        # 离场（Q3 无）不出现在快照
        assert "600000.SH" not in d.index
        assert str(d.loc["600036.SH", "report_date"]) == "2026-09-30"

    def test_单季时delta全NaN(self, tmp_path, monkeypatch):
        _patch_quantdb(monkeypatch, tmp_path)
        root = tmp_path / "2_base_sector" / "hsgt_north"
        self._write_quarter(
            root, "2026Q3",
            [{"symbol": "600036.SH", "stock_name": "招商银行", "holding_quantity": 1,
              "holding_percentage": 0.04, "report_date": "2026-09-30"}],
        )
        d = datasets.north_delta()
        assert len(d) == 1 and pd.isna(d["delta"].iloc[0])
        assert datasets.latest_quarter()["quarter"] == "2026Q3"


class TestEventTimeline:
    def test_聚合排序与前后缀兼容(self, tmp_path, monkeypatch):
        raw = tmp_path / "smart_money"
        raw.mkdir()
        monkeypatch.setattr(datasets, "raw_dir", lambda: raw)
        today = pd.Timestamp.today().normalize()
        d1 = (today - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        d2 = (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        pd.DataFrame(
            [
                {"symbol": "600036.SH", "name": "招商银行", "trade_date": d1,
                 "reason": "日涨幅偏离值达到7%的前5只证券", "interpretation": "3家机构买入",
                 "close": 40.0, "pct_chg": 7.2, "lhb_net_buy": 1.2e8, "net_ratio": 0.05,
                 "fwd_ret_1d": 1.1, "fwd_ret_2d": -0.5, "fwd_ret_5d": 3.1, "fwd_ret_10d": 8.8},
                {"symbol": "000001.SZ", "name": "平安银行", "trade_date": d1,
                 "reason": "x", "interpretation": "y", "close": 10.0, "pct_chg": 1.0,
                 "lhb_net_buy": 1.0, "net_ratio": 0.01,
                 "fwd_ret_1d": 0.0, "fwd_ret_2d": 0.0, "fwd_ret_5d": 0.0, "fwd_ret_10d": 0.0},
            ]
        ).to_parquet(raw / "lhb_detail.parquet")
        pd.DataFrame(
            [
                {"symbol": "600036.SH", "name": "招商银行", "trade_date": d2,
                 "inst_count": 25, "reception_type": "电话会议", "reception_place": "线上"},
            ]
        ).to_parquet(raw / "survey.parquet")

        ev = datasets.event_timeline("SH600036", limit=10)

        assert [e["type"] for e in ev] == ["survey", "lhb"]  # 日期降序
        assert ev[1]["date"] == d1
        assert ev[1]["fwd_ret_5d"] == pytest.approx(3.1)
        assert ev[0]["inst_count"] == pytest.approx(25)
        # 他股事件不串入
        assert all(e["type"] != "lhb" or e["net_buy"] != pytest.approx(1.0) for e in ev)

    def test_原始表缺失返回空列表(self, tmp_path, monkeypatch):
        raw = tmp_path / "smart_money"
        raw.mkdir()
        monkeypatch.setattr(datasets, "raw_dir", lambda: raw)
        assert datasets.event_timeline("600036.SH") == []

    def test_字符串列NA不回吐(self, tmp_path, monkeypatch):
        """可空 string dtype 列读出的 pd.NA 一律 None——生产 500 回归
        （2026-10-16 stock/SH603668 reception_place=pd.NA →
        PydanticSerializationError）。时间线整体须 JSON 严格可序列化。"""
        raw = tmp_path / "smart_money"
        raw.mkdir()
        monkeypatch.setattr(datasets, "raw_dir", lambda: raw)
        d1 = (pd.Timestamp.today().normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        pd.DataFrame(
            {
                "symbol": ["600036.SH", "600036.SH"],
                "trade_date": [d1, d1],
                "reason": pd.array([pd.NA, "日涨幅偏离值达7%"], dtype="string"),
                "interpretation": pd.array([None, "3家机构买入"], dtype="string"),
                "close": [40.0, 40.0],
                "pct_chg": [7.2, 7.2],
                "lhb_net_buy": [1.2e8, 1.2e8],
                "net_ratio": [0.05, 0.05],
                "fwd_ret_1d": [1.1, 1.1],
                "fwd_ret_2d": [None, None],
                "fwd_ret_5d": [3.1, 3.1],
                "fwd_ret_10d": [8.8, 8.8],
            }
        ).to_parquet(raw / "lhb_detail.parquet")
        pd.DataFrame(
            {
                "symbol": ["600036.SH"],
                "trade_date": [d1],
                "inst_count": [25],
                "reception_type": pd.array([pd.NA], dtype="string"),
                "reception_place": pd.array([pd.NA], dtype="string"),
            }
        ).to_parquet(raw / "survey.parquet")

        ev = datasets.event_timeline("600036.SH", limit=10)

        survey = next(e for e in ev if e["type"] == "survey")
        assert survey["reception_type"] is None
        assert survey["reception_place"] is None
        na = next(e for e in ev if e["type"] == "lhb" and e["reason"] is None)
        ok = next(e for e in ev if e["type"] == "lhb" and e["reason"] is not None)
        assert na["interpretation"] is None
        assert ok["reason"] == "日涨幅偏离值达7%"
        assert ok["interpretation"] == "3家机构买入"
        # 严格 JSON（allow_nan=False 同浏览器解析严格度）：NaN/pd.NA 都不得存活
        json.dumps(ev, allow_nan=False)


class TestStringSanitizer:
    def test_pandas空值一律None(self):
        assert datasets.s_or_none(pd.NA) is None
        assert datasets.s_or_none(float("nan")) is None
        assert datasets.s_or_none(pd.NaT) is None
        assert datasets.s_or_none(None) is None
        assert datasets.s_or_none("   ") is None
        # 已字符串化的 NA 拼写（历史 str() 转换的遗留污染）
        assert datasets.s_or_none("nan") is None
        assert datasets.s_or_none("<NA>") is None
        assert datasets.s_or_none("NaT") is None

    def test_保留真实文本并去空白(self):
        assert datasets.s_or_none(" 机构专用 ") == "机构专用"
        assert datasets.s_or_none("电话会议") == "电话会议"
        assert datasets.s_or_none("0") == "0"
