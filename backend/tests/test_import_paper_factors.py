"""论文因子落库导入器（``scripts/alpha_agent/import_paper_factors.py``）测试。

用迷你报告（结构同 T-FM-01 报告 JSON）+ 假 persistence 钉契约：

- tier 过滤（fdr=过 FDR、selected=去重入选、all=全量）；
- 身份稳定（stable_factor_id：同 fid 恒同 id，tier 无关——复跑只更新不新增）；
- 落库行形状（``fm:`` 前缀 factor_code、formulation=FM 原式、metadata
  **不得有 formulation 键**、可翻译因子带 ``qlib_expression``）；
- dry-run 不触库、``--apply`` 才写，且 save/update 的参数与统计派同款；
- 真报告文件在本机时补一组整报告计数钉子（110/102/20、31/79）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IMPORTER_PATH = ROOT / "scripts" / "alpha_agent" / "import_paper_factors.py"


def _load_importer():
    spec = importlib.util.spec_from_file_location("_importer_under_test", IMPORTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def imp():
    return _load_importer()


def _factor(
    fid: str,
    expr: str,
    *,
    fdr: bool = True,
    selected: bool = False,
    rank_ic: float = 0.02,
) -> dict:
    return {
        "key": f"fm{fid}",
        "fid": fid,
        "expression": expr,
        "min_history": 3,
        "best_horizon": 1,
        "horizons": [
            {
                "horizon": 1,
                "n_days": 10,
                "ic_mean": 0.01,
                "rank_ic_mean": 0.011,
                "icir": 0.1,
                "rank_ic_t": 2.0,
                "rank_ic_p": 0.04,
                "rank_ic_win_rate": 0.6,
                "fdr_pass": fdr,
            }
        ],
        "fdr_horizons": [1] if fdr else [],
        "rank_ic_mean": rank_ic,
        "rank_ic_win_rate": 0.6,
        "t_stat": 2.1,
        "p_value": 0.04,
        "icir": 0.2,
        "fold_ics": [0.01, 0.02],
        "fold_sign_consistency": 1.0,
        "stability_eligible": True,
        "stability_note": None,
        "selection_score": rank_ic,
        "reselect_rate": 0.5,
        "reselect_hits": 1,
        "reselect_checkpoints": 2,
        "max_abs_corr": 0.3,
        "corr_warn": False,
        "max_abs_corr_vs_house": 0.4,
        "closest_house": "amihud_20d",
        "reject_reason": None,
        "selected": selected,
        "complexity": {
            "node_count": 3,
            "op_count": 2,
            "depth": 2,
            "max_window": 5,
            "literal_count": 1,
        },
    }


def _mini_report() -> dict:
    return {
        "task": "T-FM-01",
        "fdr_q": 0.1,
        "vwap_convention": "amount/volume×$factor",
        "returns_convention": "close/close.shift(1)-1",
        "neutralize": "none",
        "tradability": {"filter": {"n_kept": 100}},
        "window": {"start": "2021-10-09", "end": "2025-10-09"},
        "paper": {
            "factors": [
                _factor("001", "Mean($close, 5)", fdr=True, selected=True),
                _factor("002", "Neg(CsRank($returns))", fdr=True, selected=True),
                _factor("003", "Std($volume, 10)", fdr=False, selected=False),
            ],
            "dedup": {"kept": ["fm001", "fm002"]},
        },
    }


class _FakePersistence:
    def __init__(self):
        self.ensured = False
        self.saved: list[dict] = []
        self.metrics: list[dict] = []

    async def ensure_tables(self):
        self.ensured = True

    async def save_factor(self, **kwargs):
        self.saved.append(kwargs)

    async def update_factor_metrics(self, **kwargs):
        self.metrics.append(kwargs)


REPORT_PATH = Path("/tmp/_mini_paper_report.json")


def _rows(imp, tier: str):
    return imp.build_rows(_mini_report(), tier=tier, report_path=REPORT_PATH)


class TestTierFilter:
    def test_fdr_selected_all(self, imp):
        assert [r["key"] for r in _rows(imp, "fdr")] == ["fm001", "fm002"]
        assert [r["key"] for r in _rows(imp, "selected")] == ["fm001", "fm002"]
        assert [r["key"] for r in _rows(imp, "all")] == ["fm001", "fm002", "fm003"]

    def test_unknown_tier_raises(self, imp):
        with pytest.raises(ValueError, match="未知 tier"):
            imp.select_factors(_mini_report(), "bogus")


class TestRowShape:
    def test_factor_code_prefix_and_formulation(self, imp):
        row = next(r for r in _rows(imp, "all") if r["key"] == "fm001")
        assert row["factor_code"] == "fm:Mean($close, 5)"
        assert row["factor_formulation"] == "Mean($close, 5)"
        assert row["factor_name"] == "FactorMiner 论文因子（fm001）"

    def test_metadata_has_no_formulation_key(self, imp):
        """admin 管线 _extract_qlib_expression 会把 metadata['formulation'] 当
        qlib 表达式直喂求值器——FM 原式不是 qlib 语法，绝不能进这个键。"""
        for row in _rows(imp, "all"):
            assert "formulation" not in row["metadata"]
            assert row["metadata"]["fm_expression"] == row["factor_formulation"]

    def test_qlib_expression_present_only_when_translatable(self, imp):
        rows = {r["key"]: r for r in _rows(imp, "all")}
        assert rows["fm001"]["qlib_expression"] == "Mean($close, 5)"
        assert rows["fm001"]["qlib_gap"] is None
        assert rows["fm002"]["qlib_expression"] is None
        assert "CsRank" in rows["fm002"]["qlib_gap"]

    def test_metadata_is_strict_json(self, imp):
        meta = _rows(imp, "all")[0]["metadata"]
        text = json.dumps(meta, ensure_ascii=False, allow_nan=False)
        loaded = json.loads(text)
        assert loaded["source"] == "factorminer"
        assert loaded["market"] == "a_share"
        assert loaded["universe"] == "csi300"
        assert loaded["task_id"] == "T-FM-01"
        assert set(loaded["horizons"]) == {"1"}
        assert loaded["fdr_pass"] is True
        assert loaded["window"]["start"] == "2021-10-09"

    def test_ic_value_from_horizon_one(self, imp):
        row = _rows(imp, "all")[0]
        assert row["ic_value"] == 0.01  # horizon 1 的 ic_mean
        assert row["date_range"] == "2021-10-09~2025-10-09"


class TestIdentityStability:
    def test_same_fid_same_id_across_calls_and_tiers(self, imp):
        a = {r["key"]: r["factor_id"] for r in _rows(imp, "all")}
        b = {r["key"]: r["factor_id"] for r in _rows(imp, "fdr")}
        c = {r["key"]: r["factor_id"] for r in _rows(imp, "selected")}
        for key in ("fm001", "fm002"):
            assert a[key] == b[key] == c[key]
        assert len(set(a.values())) == len(a)  # 不同 fid 不同 id
        assert _rows(imp, "all")[0]["scheme"] == "config"


class TestApply:
    def test_apply_rows_writes_save_and_metrics(self, imp):
        rows = _rows(imp, "fdr")
        fake = _FakePersistence()

        summary = asyncio.run(imp.apply_rows(rows, persistence=fake))

        assert summary == {"saved": 2}
        assert fake.ensured
        assert [s["factor_id"] for s in fake.saved] == [r["factor_id"] for r in rows]
        for saved in fake.saved:
            assert saved["data_source"] == "factorminer"
            assert saved["market"] == "a_share"
            assert saved["universe"] == "csi300"
            assert saved["user_id"] == "10000001"
            assert saved["factor_code"].startswith("fm:")
        for metric in fake.metrics:
            assert metric["status"] == "completed"
            assert metric["rank_icir"] == metric["icir"]
            assert metric["universe"] == "csi300"
            assert metric["date_range"] == "2021-10-09~2025-10-09"

    def test_main_dry_run_does_not_touch_persistence(self, imp, monkeypatch, capsys):
        REPORT_PATH.write_text(json.dumps(_mini_report()), encoding="utf-8")

        def _explode(*args, **kwargs):
            raise AssertionError("dry-run 不应触库")

        monkeypatch.setattr(imp, "apply_rows", _explode)

        assert imp.main(["--report", str(REPORT_PATH), "--tier", "fdr"]) == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "共 2 条" in out

    def test_main_apply_calls_apply_rows(self, imp, monkeypatch):
        REPORT_PATH.write_text(json.dumps(_mini_report()), encoding="utf-8")
        calls: list = []
        sentinel = object()

        async def _fake_apply(rows, *, persistence):
            calls.append((rows, persistence))
            return {"saved": len(rows)}

        monkeypatch.setattr(imp, "apply_rows", _fake_apply)
        # 真 persistence 构造要走 qlib_app.services（链上拉 qlib），venv 里挂掉
        monkeypatch.setattr(imp, "_default_persistence", lambda: sentinel)

        assert imp.main(["--report", str(REPORT_PATH), "--apply"]) == 0
        assert len(calls) == 1
        rows, persistence = calls[0]
        assert [r["key"] for r in rows] == ["fm001", "fm002"]
        assert persistence is sentinel


class TestDefaultReportResolution:
    """默认报告路径解析：宿主候选 → 容器候选（/data）存在性择一。

    容器里 ``/app/data`` 是空目录、真数据卷挂在 ``/data``——若只认宿主
    路径，容器内复跑导入器不带 ``--report`` 必 FileNotFoundError。
    """

    def test_prefers_host_candidate_when_present(self, imp, tmp_path, monkeypatch):
        host = tmp_path / "host.json"
        host.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(imp, "_HOST_REPORT", host)
        monkeypatch.setattr(imp, "_CONTAINER_REPORT", tmp_path / "container.json")
        assert imp.default_report() == host

    def test_falls_back_to_container_candidate(self, imp, tmp_path, monkeypatch):
        container = tmp_path / "container.json"
        container.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(imp, "_HOST_REPORT", tmp_path / "host.json")
        monkeypatch.setattr(imp, "_CONTAINER_REPORT", container)
        assert imp.default_report() == container

    def test_missing_candidates_yield_host_path_for_error_message(
        self, imp, tmp_path, monkeypatch
    ):
        host = tmp_path / "host.json"
        monkeypatch.setattr(imp, "_HOST_REPORT", host)
        monkeypatch.setattr(imp, "_CONTAINER_REPORT", tmp_path / "container.json")
        # 都不在时返回宿主候选——load_report 的报错信息里给的是预期位置
        assert imp.default_report() == host


@pytest.mark.skipif(
    not _load_importer().default_report().exists(),
    reason="T-FM-01 报告不在本机（data/refs/factorminer/）",
)
class TestRealReport:
    def test_tier_counts_and_coverage(self, imp):
        report = imp.load_report(imp.default_report())
        fdr = imp.select_factors(report, "fdr")
        selected = imp.select_factors(report, "selected")
        all_factors = imp.select_factors(report, "all")
        assert len(fdr) == 102
        assert len(selected) == 20
        assert len(all_factors) == 110

        rows = imp.build_rows(report, tier="all", report_path=imp.default_report())
        n_qlib = sum(1 for r in rows if r["qlib_expression"])
        assert n_qlib == 31  # 与 qlib_export.library_coverage 快照一致
        assert all(r["factor_code"].startswith("fm:") for r in rows)
        assert all(
            r["metadata"]["fdr_pass"] == bool(r["metadata"]["fdr_horizons"])
            for r in rows
        )
