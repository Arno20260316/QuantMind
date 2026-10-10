"""统计派面板装配（移植 P2 / Tier 2）——暖机口径 / 取数整形 / 元数据解析。

契约：

1. **暖机**：按交易日历向前回看 N 个交易日（不是自然日）；回看越界退到日历
   首日；负数即拒；
2. **取数整形**：qlib 原始帧（index=(instrument, datetime)，``$`` 列）→ 标准
   面板（index=(trade_date, symbol)，float64，日期升序）；缺列即拒；
3. **元数据**：``J_start``（YYYYMMDD 数字/字符串）→ Timestamp；``IsSTGP``/
   ``IsQuitGP`` → bool；``ActiveCapital`` → 数值；索引一律代码数字，重复
   symbol 保留首条；空表/缺 symbol 列的行为明确；
4. **映射**：``map_to_panel`` 前缀/后缀式混用都能对上（代码数字匹配）。

全部离线：行情加载器用替身，元数据用构造表。
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

from backend.services.engine.alpha_agent.statistical import panel as pn  # noqa: E402


def _calendar(n: int = 10) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=n)


def _raw_frame() -> pd.DataFrame:
    """qlib 风格原始帧：index=(instrument, datetime)，列为 $ 字段。"""
    idx = pd.MultiIndex.from_product(
        [["sh600000", "sz000001"], _calendar(3)], names=["instrument", "datetime"]
    )
    data = {
        "$open": np.arange(len(idx), dtype=float) + 1,
        "$high": np.arange(len(idx), dtype=float) + 2,
        "$low": np.arange(len(idx), dtype=float) + 0.5,
        "$close": np.arange(len(idx), dtype=float) + 1.5,
        "$volume": 100.0,
        "$amount": 1e4,
    }
    return pd.DataFrame(data, index=idx)


class TestWarmup:
    def test_counts_trading_days_not_calendar_days(self):
        cal = _calendar(10)
        got = pn.warmup_start(cal[5], cal, 3)
        assert got == cal[2]

    def test_clamps_at_calendar_start(self):
        cal = _calendar(5)
        assert pn.warmup_start(cal[1], cal, 100) == cal[0]

    def test_zero_warmup_is_start_and_negative_rejected(self):
        cal = _calendar(5)
        assert pn.warmup_start(cal[3], cal, 0) == cal[3]
        with pytest.raises(ValueError, match="warmup_bars"):
            pn.warmup_start(cal[3], cal, -1)


class TestNormalizePanel:
    def test_shapes_columns_and_dtype(self):
        panel = pn.normalize_panel(_raw_frame())
        assert list(panel.columns) == list(pn.PANEL_COLUMNS)
        assert panel.index.names == ["trade_date", "symbol"]
        assert panel.index.is_monotonic_increasing
        assert set(panel.index.get_level_values(1)) == {"sh600000", "sz000001"}
        assert panel["close"].dtype == float

    def test_missing_field_rejected(self):
        raw = _raw_frame().drop(columns=["$amount"])
        with pytest.raises(ValueError, match="amount"):
            pn.normalize_panel(raw)

    def test_empty_returns_empty(self):
        assert pn.normalize_panel(pd.DataFrame()).empty


class TestLoadPricePanel:
    def test_loader_receives_warmup_adjusted_start(self):
        cal = _calendar(10)
        seen = {}

        def loader(instruments, fields, start, end):
            seen.update(instruments=instruments, fields=fields, start=start, end=end)
            return _raw_frame()

        panel = pn.load_price_panel(
            "2024-01-08",
            "2024-01-12",
            symbols=["sh600000", "sz000001"],
            warmup_bars=4,
            calendar=cal,
            loader=loader,
        )
        assert seen["start"] == "2024-01-02", "01-08 前回看 4 个交易日"
        assert seen["fields"] == list(pn.PRICE_FIELDS)
        assert seen["instruments"] == ["sh600000", "sz000001"]
        assert not panel.empty

    def test_empty_universe_rejected(self):
        with pytest.raises(ValueError, match="标的池为空"):
            pn.load_price_panel(
                "2024-01-08", "2024-01-12", symbols=[], calendar=_calendar(3),
                loader=lambda *a: _raw_frame(),
            )


class TestQlibInstruments:
    """标的池解析：``D.instruments`` 只回**配置字典**，真实列表必须经
    ``D.list_instruments(..., as_list=True)``。

    实测踩过：直接 ``list(qd.D.instruments(...))`` 拿到的是字典的两个键名，
    任何市场都恒为 2 个「标的」，引擎随后全候选「样本内有效日不足」——
    真跑一次任务才暴露（单测此前只注入 loader 替身，没走这条路径）。
    """

    def _install_fake_qlib(self, monkeypatch, resolved):
        import types  # noqa: PLC0415

        calls: dict = {}

        class _D:
            @staticmethod
            def instruments(market="all"):
                calls["market"] = market
                return {"market": market, "filter_pipe": []}

            @staticmethod
            def list_instruments(cfg, as_list=False):
                calls["cfg"] = cfg
                calls["as_list"] = as_list
                return resolved

        fake_data = types.SimpleNamespace(D=_D)
        fake_qlib = types.ModuleType("qlib")
        fake_qlib.init = lambda **kw: calls.update(init=kw)
        fake_qlib.data = fake_data
        fake_const = types.ModuleType("qlib.constant")
        fake_const.REG_CN = "cn"
        fake_qlib.constant = fake_const
        monkeypatch.setitem(sys.modules, "qlib", fake_qlib)
        monkeypatch.setitem(sys.modules, "qlib.data", fake_data)
        monkeypatch.setitem(sys.modules, "qlib.constant", fake_const)
        return calls

    def test_resolves_config_dict_through_list_instruments(self, monkeypatch):
        resolved = ["sh600000", "sz000001", "bj920000"]
        calls = self._install_fake_qlib(monkeypatch, resolved)

        got = pn.qlib_instruments("csi300", qlib_dir="/tmp/qlib")

        assert got == resolved, "必须回真列表，不是配置字典的键名（恒 2 个）"
        assert calls["market"] == "csi300"
        assert calls["cfg"] == {"market": "csi300", "filter_pipe": []}
        assert calls["as_list"] is True
        assert calls["init"] == {"provider_uri": "/tmp/qlib", "region": "cn"}


class TestSymbolMetadata:
    def _raw_instrument_detail(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Symbol": ["000001.SZ", "600000.SH", "688981.SH", "000001.SZ"],
                "Name": ["平安银行", "浦发银行", "中芯国际", "重复行"],
                "rs_hyname": ["全国性银行", "股份制银行", "半导体", "x"],
                "J_start": [19910403, "19991110", 20190716.0, 19910403],
                "IsSTGP": [0, 1, 0, 0],
                "IsQuitGP": [0, 0, 1, 0],
                "ActiveCapital": [1940560.13, "1234.5", None, 1.0],
            }
        )

    def test_parsing_and_digit_index(self):
        meta = pn.build_symbol_metadata(self._raw_instrument_detail())
        assert list(meta.index) == ["000001", "600000", "688981"], "重复 code 保留首条"
        assert meta.loc["000001", "listing_date"] == pd.Timestamp("1991-04-03")
        assert meta.loc["600000", "listing_date"] == pd.Timestamp("1999-11-10")
        assert meta.loc["688981", "listing_date"] == pd.Timestamp("2019-07-16")
        assert bool(meta.loc["600000", "is_st"]) is True
        assert bool(meta.loc["688981", "is_quit"]) is True
        assert meta.loc["600000", "float_shares"] == pytest.approx(1234.5)
        assert np.isnan(meta.loc["688981", "float_shares"])

    def test_missing_columns_tolerated(self):
        raw = pd.DataFrame({"Symbol": ["000001.SZ"]})
        meta = pn.build_symbol_metadata(raw)
        assert meta.loc["000001", "name"] == ""
        assert meta.loc["000001", "listing_date"] is None
        assert bool(meta.loc["000001", "is_st"]) is False

    def test_empty_and_missing_symbol(self):
        assert pn.build_symbol_metadata(pd.DataFrame()).empty
        with pytest.raises(ValueError, match="symbol"):
            pn.build_symbol_metadata(pd.DataFrame({"Foo": [1]}))

    def test_bad_listing_date_becomes_none(self):
        raw = pd.DataFrame({"Symbol": ["000001.SZ"], "J_start": ["not-a-date"]})
        assert pn.build_symbol_metadata(raw).loc["000001", "listing_date"] is None


class TestMapToPanel:
    def test_prefix_suffix_mixed(self):
        panel = pn.normalize_panel(_raw_frame())
        industry = pd.Series(
            {"600000": "银行", "000001": "银行2"}, name="industry"
        )
        mapped = pn.map_to_panel(panel, industry)
        for (_, symbol), value in mapped.items():
            assert value == ("银行" if symbol == "sh600000" else "银行2")

    def test_unknown_symbol_maps_nan(self):
        panel = pn.normalize_panel(_raw_frame())
        mapped = pn.map_to_panel(panel, pd.Series({"999999": "x"}))
        assert mapped.isna().all()
