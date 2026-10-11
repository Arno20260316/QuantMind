"""``scripts/factor_registry_report.py``：注册中心报告纯函数（T-FE-07）。

口径纪律：命名违约与 code_fp 失配是**可见性报告**（不阻塞、不大扫除）；
标签覆盖的分母 = 全量因子，缺失维度记 unknown 不计覆盖；下游闭包只经
物化 manifest 的列名对拍（因子↔列↔catalog mapping）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.shared.factor_identity import code_fingerprint
from backend.scripts.factor_registry_report import (
    analyze_factor,
    build_report,
    render_markdown,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 11, tzinfo=UTC)


def _row(**over):
    base = {
        "factor_id": "f1",
        "factor_name": "动量_20",
        "factor_code": "Rank($close,20)",
        "status": "completed",
        "market": "a_share",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "metadata_json": "{}",
    }
    base.update(over)
    return base


class TestAnalyzeFactor:
    def test_completed_old_factor_is_active_with_tags(self):
        rec = analyze_factor(_row(), now=NOW)
        assert rec["state"] == "active"
        assert rec["maturity"] == "在用"
        assert rec["tags"]["transform"] == "Rank"
        assert rec["tags"]["maturity"] == "在用"
        assert rec["tags"]["frequency"] == "daily"
        assert rec["deps"]["windows"] == [20]
        assert rec["naming_violations"] == []
        assert rec["materialization"] is None  # 不在 manifest：无物化面

    def test_pending_without_run_is_experiment(self):
        rec = analyze_factor(_row(status="pending"), now=NOW)
        assert rec["state"] == "experiment"
        assert rec["maturity"] == "实验"

    def test_run_within_blind_is_observing(self):
        rec = analyze_factor(
            _row(status="pending"),
            first_completed_run_at=datetime(2026, 9, 10, tzinfo=UTC),
            now=NOW,
        )
        assert rec["state"] == "observing"
        assert rec["maturity"] == "观察"

    def test_name_empty_and_too_long_violations(self):
        assert (
            "name_empty"
            in analyze_factor(_row(factor_name="  "), now=NOW)["naming_violations"]
        )
        long_name = "长" * 65
        assert (
            "name_too_long"
            in analyze_factor(_row(factor_name=long_name), now=NOW)["naming_violations"]
        )

    def test_fm_prefix_conflict_violation(self):
        row = _row(
            factor_code="fm:ts_mean($close,5)",
            metadata_json='{"source": "statistical"}',
        )
        rec = analyze_factor(row, now=NOW)
        assert "fm_prefix_conflict" in rec["naming_violations"]

    def test_fm_prefix_missing_when_claimed_factorminer(self):
        row = _row(
            factor_code="Rank($close,20)",
            metadata_json='{"source": "factorminer"}',
        )
        rec = analyze_factor(row, now=NOW)
        assert "fm_prefix_missing" in rec["naming_violations"]

    def test_column_mismatch_flagged_against_expected(self):
        rec = analyze_factor(
            _row(),
            manifest_entry={"column": "hand_built_col", "status": "materialized"},
            expected_column="rd__20",
            now=NOW,
        )
        assert "column_mismatch" in rec["naming_violations"]
        assert rec["materialization"]["column"] == "hand_built_col"

    def test_column_match_no_violation(self):
        rec = analyze_factor(
            _row(),
            manifest_entry={"column": "rd__20", "status": "materialized"},
            expected_column="rd__20",
            now=NOW,
        )
        assert "column_mismatch" not in rec["naming_violations"]

    def test_code_fp_drift_detected(self):
        code = "Rank($close,20)"
        stored = code_fingerprint(code)
        assert stored is not None
        same = analyze_factor(
            _row(),
            manifest_entry={
                "column": "c1",
                "status": "materialized",
                "code_fp": stored,
            },
            expected_column="c1",
            now=NOW,
        )
        assert same["materialization"]["code_mismatch"] is False
        drift = analyze_factor(
            _row(factor_code="Rank($close,20) + 1"),
            manifest_entry={
                "column": "c1",
                "status": "materialized",
                "code_fp": stored,
            },
            expected_column="c1",
            now=NOW,
        )
        assert drift["materialization"]["code_mismatch"] is True

    def test_code_emptied_with_stored_fp_is_mismatch(self):
        drift = analyze_factor(
            _row(factor_code=""),
            manifest_entry={"column": "c1", "status": "materialized", "code_fp": "abc"},
            expected_column="c1",
            now=NOW,
        )
        assert drift["materialization"]["code_mismatch"] is True

    def test_no_stored_fp_is_not_mismatch(self):
        rec = analyze_factor(
            _row(),
            manifest_entry={"column": "c1", "status": "materialized"},
            expected_column="c1",
            now=NOW,
        )
        assert rec["materialization"]["code_mismatch"] is False


class TestBuildReport:
    def _records(self):
        return [
            analyze_factor(_row(factor_id="f1"), now=NOW),
            analyze_factor(_row(factor_id="f2", status="pending"), now=NOW),
            analyze_factor(
                _row(factor_id="f3", status="pending"),
                first_completed_run_at=datetime(2026, 9, 10, tzinfo=UTC),
                now=NOW,
            ),
        ]

    def test_summary_state_counts(self):
        report = build_report(self._records(), catalog_rows=[], generated_at=NOW)
        assert report["summary"]["total"] == 3
        assert report["summary"]["by_state"] == {
            "active": 1,
            "experiment": 1,
            "observing": 1,
        }

    def test_tag_coverage_counts_unknown_as_uncovered(self):
        report = build_report(self._records(), catalog_rows=[], generated_at=NOW)
        cov = report["tag_coverage"]
        # maturity 全因子可派生 → 覆盖 3/3；family 无写侧来源 → 0/3
        assert cov["maturity"]["covered"] == 3
        assert cov["family"]["covered"] == 0

    def test_naming_violations_listed_with_factor_id(self):
        records = [analyze_factor(_row(factor_id="fx", factor_name=""), now=NOW)]
        report = build_report(records, catalog_rows=[], generated_at=NOW)
        assert report["naming_violations"][0]["factor_id"] == "fx"
        assert "name_empty" in report["naming_violations"][0]["codes"]

    def test_code_drift_closure_attached_from_catalog_rows(self):
        rec = analyze_factor(
            _row(factor_id="fd"),
            manifest_entry={
                "column": "rd_drift",
                "status": "materialized",
                "code_fp": "old",
            },
            expected_column="rd_drift",
            now=NOW,
        )
        catalog_rows = [
            {
                "source_column": "rd_drift",
                "version_id": "v1",
                "version_name": "默认目录",
                "version_status": "published",
                "enabled": True,
            },
            {
                "source_column": "rd_other",
                "version_id": "v9",
                "version_name": "别处",
                "version_status": "draft",
                "enabled": True,
            },
        ]
        report = build_report([rec], catalog_rows=catalog_rows, generated_at=NOW)
        drift = report["code_drift"]
        assert len(drift) == 1
        assert drift[0]["factor_id"] == "fd"
        assert [d["version_id"] for d in drift[0]["downstream"]] == ["v1"]
        assert report["summary"]["code_drift_count"] == 1

    def test_superseded_drift_not_in_live_drift_list(self):
        rec = analyze_factor(
            _row(factor_id="fs"),
            manifest_entry={
                "column": "rd_old",
                "status": "superseded",
                "code_fp": "old",
            },
            expected_column="rd_old",
            now=NOW,
        )
        report = build_report([rec], catalog_rows=[], generated_at=NOW)
        # 非 materialized 的失配不进「下游失配」主表（列已不在活跃信息面），
        # 但记录数可见
        assert report["code_drift"] == []
        assert report["summary"]["code_drift_count"] == 0
        assert report["summary"]["code_drift_inactive_count"] == 1


class TestRenderMarkdown:
    def test_markdown_contains_key_sections(self):
        records = [
            analyze_factor(
                _row(factor_id="fx", factor_name=""),
                manifest_entry={
                    "column": "c1",
                    "status": "materialized",
                    "code_fp": "old",
                },
                expected_column="c1",
                now=NOW,
            )
        ]
        report = build_report(records, catalog_rows=[], generated_at=NOW)
        md = render_markdown(report)
        assert "因子注册中心报告" in md
        assert "命名违约" in md
        assert "fx" in md
        assert "code_fp" in md or "失配" in md
        assert "标签覆盖" in md
