"""stock_display_name 四层解析/形态归一/指数/缺名回退/异常安全测试。

口径见 ``docs/QQ推送_中文名与排版设计_20261016.md`` §2：
调用方 hint → A股 mapper → 港/美 security_master → 内置指数 → 查不到只回代码（绝不编造）。
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.shared import stock_display_name as sdn
from backend.shared.stock_name_mapper import (
    get_stock_name_mapper,
    resolve_name as _cn_resolve_name,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每例：清缓存 + A股 mapper 置空表（防宿主 stocks_index.json 泄漏）+ 摘环境变量。"""
    sdn.reset_cache()
    monkeypatch.setattr(get_stock_name_mapper(), "_mapping", {}, raising=False)
    monkeypatch.delenv("QM_QUANTHK_DATA_DIR", raising=False)
    monkeypatch.delenv("QM_QUANTUS_DATA_DIR", raising=False)
    yield
    sdn.reset_cache()


def _write_master(root: Path, rows: list[tuple[str, str, str]]) -> Path:
    """按 security_master 相对路径铺一份最小 parquet（symbol/cn_name/en_name）。"""
    path = root / "2_base_sector" / "security_master" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "symbol": [r[0] for r in rows],
                "cn_name": [r[1] for r in rows],
                "en_name": [r[2] for r in rows],
            }
        ),
        path,
    )
    return path


def _set_cn_mapping(monkeypatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(get_stock_name_mapper(), "_mapping", mapping, raising=False)


class TestIndexNames:
    def test_suffix_and_prefix_forms_hit_index_table(self):
        assert sdn.resolve("000001.SH") == "上证指数"
        assert sdn.resolve("SH000001") == "上证指数"
        assert sdn.resolve("000300.SH") == "沪深300"

    def test_hk_us_index_forms(self):
        assert sdn.resolve("HSI") == "恒生指数"
        assert sdn.resolve("hsi.hk") == "恒生指数"
        assert sdn.resolve("IXIC") == "纳斯达克指数"

    def test_bare_six_digit_is_not_index(self):
        # 000001 裸码是平安银行不是上证指数——歧义形态必须留给 A股 mapper；
        # mapper 空表时解析为「无名字」，绝不冒充指数。
        assert sdn.resolve("000001") == ""

    def test_index_display_code(self):
        assert sdn.display_code("000001.SH") == "SH000001"
        assert sdn.display_code("HSI") == "HSI"


class TestAstock:
    def test_any_layer_symbol_resolves_via_mapper(self, monkeypatch):
        _set_cn_mapping(monkeypatch, {"600519.SH": "贵州茅台"})
        assert sdn.resolve("600519.SH") == "贵州茅台"
        assert sdn.resolve("SH600519") == "贵州茅台"
        assert sdn.resolve("600519") == "贵州茅台"

    def test_identity_and_display_code_prefix_form(self, monkeypatch):
        _set_cn_mapping(monkeypatch, {"600519.SH": "贵州茅台", "002074.SZ": "国轩高科"})
        assert sdn.identity("600519.SH") == "贵州茅台(SH600519)"
        assert sdn.display_code("600519") == "SH600519"
        assert sdn.display_code("002074.SZ") == "SZ002074"

    def test_missing_name_returns_code_only(self):
        assert sdn.resolve("600519.SH") == ""
        assert sdn.identity("600519.SH") == "SH600519"


class TestHongKong:
    @pytest.fixture(autouse=True)
    def _hk_master(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path))
        _write_master(
            tmp_path,
            [
                ("0700.HK", "腾讯控股", "Tencent Holdings"),
                ("2555.HK", "茶百道", "Chabaidao"),
                ("0005.HK", "", "HSBC Holdings"),  # cn 缺 → en 兜底
            ],
        )

    def test_direct_key_form(self):
        assert sdn.resolve("2555.HK") == "茶百道"
        assert sdn.resolve("0700.HK") == "腾讯控股"

    def test_variant_forms_normalized(self):
        assert sdn.resolve("HK.0700") == "腾讯控股"
        assert sdn.resolve("HK0700") == "腾讯控股"
        assert sdn.resolve("700") == "腾讯控股"

    def test_en_name_fallback(self):
        assert sdn.resolve("0005.HK") == "HSBC Holdings"

    def test_identity_and_miss(self):
        assert sdn.identity("2555.HK") == "茶百道(2555.HK)"
        assert sdn.resolve("9999.HK") == ""
        assert sdn.identity("9999.HK") == "9999.HK"  # 缺名只回代码，绝不 代码(代码)


class TestUsStock:
    @pytest.fixture(autouse=True)
    def _us_master(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QM_QUANTUS_DATA_DIR", str(tmp_path))
        _write_master(
            tmp_path,
            [
                ("ORCL", "甲骨文", "Oracle"),
                ("NVDA", "英伟达", "NVIDIA"),
            ],
        )

    def test_ticker_and_variants(self):
        assert sdn.resolve("ORCL") == "甲骨文"
        assert sdn.resolve("orcl") == "甲骨文"
        assert sdn.resolve("US.ORCL") == "甲骨文"
        assert sdn.resolve("NVDA.US") == "英伟达"

    def test_display_code_and_identity(self):
        assert sdn.display_code("us.orcl") == "ORCL"
        assert sdn.identity("ORCL") == "甲骨文(ORCL)"


class TestHint:
    def test_hint_used_when_name_present(self):
        assert sdn.resolve("SZ002074", hint="国轩高科") == "国轩高科"

    def test_hint_that_is_same_security_code_is_ignored(self):
        # stock_name 字段若是「代码当名字」，不能拼出 002074.SZ(002074.SZ)。
        assert sdn.resolve("SZ002074", hint="002074.SZ") == ""
        assert sdn.identity("SZ002074", hint="SZ002074") == "SZ002074"

    def test_hint_with_empty_symbol(self):
        assert sdn.resolve("", hint="贵州茅台") == "贵州茅台"
        assert sdn.resolve("", hint="") == ""


class TestSafety:
    def test_empty_and_unrecognized(self):
        assert sdn.resolve("") == ""
        assert sdn.resolve("???") == ""
        assert sdn.display_code("") == ""
        assert sdn.display_code("???") == "???"
        assert sdn.identity("") == ""

    def test_mapper_exception_degrades_not_raises(self, monkeypatch):
        def _boom(_symbol):
            raise RuntimeError("index file corrupted")

        monkeypatch.setattr("backend.shared.stock_name_mapper.resolve_name", _boom)
        assert sdn.resolve("600519.SH") == ""
        assert sdn.identity("600519.SH") == "SH600519"

    def test_mapper_exception_does_not_block_hk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path))
        _write_master(tmp_path, [("0700.HK", "腾讯控股", "")])

        def _boom(_symbol):
            raise RuntimeError("index file corrupted")

        monkeypatch.setattr("backend.shared.stock_name_mapper.resolve_name", _boom)
        assert sdn.resolve("0700.HK") == "腾讯控股"

    def test_master_with_wrong_columns_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path))
        path = tmp_path / "2_base_sector" / "security_master" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"code": ["0700.HK"]}), path)
        assert sdn.resolve("0700.HK") == ""


class TestCacheInvalidation:
    def test_master_reloaded_when_mtime_changes(self, tmp_path, monkeypatch):
        import os

        monkeypatch.setenv("QM_QUANTHK_DATA_DIR", str(tmp_path))
        path = _write_master(tmp_path, [("0700.HK", "腾讯控股", "")])
        assert sdn.resolve("0700.HK") == "腾讯控股"

        _write_master(tmp_path, [("0700.HK", "腾讯控股(新)", "")])
        future = os.path.getmtime(path) + 10  # 防止秒级 mtime 相同导致缓存不失效
        os.utime(path, (future, future))
        assert sdn.resolve("0700.HK") == "腾讯控股(新)"
