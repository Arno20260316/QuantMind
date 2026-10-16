"""港股聪明钱 · 产物构建裁定闸门单测（tmp 产物目录，不触真实分区）。

钉住的行为：
- 入柱裁定（resolve_admitted）：evidence.json 唯一来源；缺失/未知键/缺 direction/
  非法 direction 一律 BuildRefused（绝不静默 +1——方向搞反 = 榜单调头）；
  admitted 为空 = evidence-only（不拒绝）；build() 在触事件仓之前即拒绝
- 新鲜度（_freshness）：partial 只看 as_of 之后的行情**交易日**数 > 4；休市日
  （CCASS 有分区、行情无该日）是结构性缺口，不触发 partial
- 共识空态（_consensus）：零入柱 → evidence_only=true + 空榜；非空入柱但无
  评分日 → 空榜不炸
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.scripts.market_analysis import build_hk_smart_money as bhk
from backend.shared.hk_smart_money import datasets as ds


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Path:
    """产物目录指向 tmp（resolve_admitted/_consensus 只走 load_product_json）。"""
    monkeypatch.setattr(ds, "product_dir", lambda: tmp_path)
    return tmp_path


def _write_evidence(root: Path, payload: dict) -> None:
    (root / "evidence.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------- 入柱裁定


class TestAdmittedGate:
    def test_证据缺失拒绝且build不落盘(self, env):
        with pytest.raises(bhk.BuildRefused, match="证据未裁定"):
            bhk.resolve_admitted()
        # build() 在 resolve 即拒绝——不触事件仓、不落任何产物
        with pytest.raises(bhk.BuildRefused, match="证据未裁定"):
            bhk.build(rebuild_days=0)
        assert not (env / "latest.json").exists()
        assert not (env / "meta.json").exists()

    def test_admitted为空是evidence_only不拒绝(self, env):
        _write_evidence(env, {"admitted": [], "signals": []})
        assert bhk.resolve_admitted() == ()
        # admitted 键缺失（None）同语义
        _write_evidence(env, {"signals": []})
        assert bhk.resolve_admitted() == ()

    def test_admitted带方向解析(self, env):
        _write_evidence(
            env,
            {
                "admitted": ["hk::net5_z", "southbound::chg5"],
                "signals": [
                    {"name": "hk::net5_z", "direction": -1},
                    {"name": "southbound::chg5", "direction": 1},
                    {"name": "us_eu::net20_tt", "direction": 1},  # 未入柱忽略
                ],
            },
        )
        assert bhk.resolve_admitted() == (("hk::net5_z", -1), ("southbound::chg5", 1))

    def test_未知键拒绝(self, env):
        # southbound 无 breadth5 变体（SOUTH_SIGNALS 不含）；拼错的类别同拒
        for bad in ("southbound::breadth5", "no_such::net5_z", "hk"):
            _write_evidence(env, {"admitted": [bad]})
            with pytest.raises(bhk.BuildRefused, match="未知信号"):
                bhk.resolve_admitted()

    def test_缺方向拒绝(self, env):
        # 方向决定分位是否取反——缺失绝不静默默认 +1
        _write_evidence(env, {"admitted": ["hk::net5_z"], "signals": []})
        with pytest.raises(bhk.BuildRefused, match="direction"):
            bhk.resolve_admitted()

    def test_非法方向值拒绝(self, env):
        for bad_dir in (0, 2, "1", None):
            _write_evidence(
                env,
                {
                    "admitted": ["hk::net5_z"],
                    "signals": [{"name": "hk::net5_z", "direction": bad_dir}],
                },
            )
            with pytest.raises(bhk.BuildRefused, match="direction"):
                bhk.resolve_admitted()


# ---------------------------------------------------------------- 新鲜度


class TestFreshness:
    CAL = pd.bdate_range("2026-09-28", "2026-10-15")  # 14 个交易日

    def _stub_dates(self, monkeypatch, *, ccass: str, kline: str | None, south=None):
        monkeypatch.setattr(ds, "latest_ccass_date", lambda: ccass)
        monkeypatch.setattr(ds, "latest_kline_date", lambda: kline)
        monkeypatch.setattr(ds, "latest_south_date", lambda: south or ccass)

    def test_最新即日不partial(self, monkeypatch):
        self._stub_dates(monkeypatch, ccass="20261015", kline="20261015")
        fresh = bhk._freshness(self.CAL, "20261015")
        assert fresh["ccass_date"] == "2026-10-15"
        assert fresh["kline_date"] == "2026-10-15"
        assert fresh["ccass_lag_days"] == 0
        assert fresh["partial"] is False

    def test_滞后4日以内不partial_第5日触发(self, monkeypatch):
        # 边界：> STALE_PARTIAL_TRADE_DAYS(=4) 才 partial
        self._stub_dates(monkeypatch, ccass="20261009", kline="20261015")
        assert bhk._freshness(self.CAL, "20261009")["ccass_lag_days"] == 4
        assert bhk._freshness(self.CAL, "20261009")["partial"] is False
        assert bhk._freshness(self.CAL, "20261008")["ccass_lag_days"] == 5
        assert bhk._freshness(self.CAL, "20261008")["partial"] is True

    def test_休市缺口不触发partial(self, monkeypatch):
        # 2026-01-01（元旦）无行情分区 = calendar 结构性缺日；as_of=最后交易日
        # → 真实交易日滞后 0，即便相邻自然日密集也不 partial（旧实现把
        # days_skipped_unpriced 计 partial 是假阳性，2026-10-16 修）
        cal = pd.bdate_range("2025-12-29", "2026-01-09")
        cal = cal[cal != pd.Timestamp("2026-01-01")]  # 删元旦（休市）
        self._stub_dates(monkeypatch, ccass="20260109", kline="20260109")
        fresh = bhk._freshness(cal, "20260109")
        assert fresh["ccass_lag_days"] == 0
        assert fresh["partial"] is False

    def test_无行情分区lag为None不partial(self, monkeypatch):
        self._stub_dates(monkeypatch, ccass="20261015", kline=None)
        fresh = bhk._freshness(self.CAL, "20261015")
        assert fresh["ccass_lag_days"] is None
        assert fresh["partial"] is False


# ---------------------------------------------------------------- 共识空态


class TestConsensusEmptyState:
    def test_零入柱诚实空态(self, env):
        out = bhk._consensus((), "2026-10-15", "2026-10-16T00:00:00")
        assert out["evidence_only"] is True
        assert out["adopted"] == [] and out["directions"] == {}
        assert out["items"] == [] and out["as_of"] == "2026-10-15"

    def test_有入柱但无评分日空榜不炸(self, env):
        spec = (("hk::net5_z", -1),)
        out = bhk._consensus(spec, None, "2026-10-16T00:00:00")
        assert out["evidence_only"] is False
        assert out["adopted"] == ["hk::net5_z"]
        assert out["directions"] == {"hk::net5_z": -1}
        assert out["items"] == [] and out["as_of"] is None
