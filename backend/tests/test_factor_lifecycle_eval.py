"""``scripts/factor_lifecycle_eval.py``：月度评估器纯函数（T-FE-06）。

口径纪律：cell 选择=家市场优先否则最新（家庭市场缺测时诚实降级为其他
市场证据，记录里写明）；「不可算」永不冒充「差」；盲窗内连击达标 →
observing_protected（记录不动作）；--apply 幂等（已在 downweighted 不重复
迁移、无事件追加）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.scripts import factor_lifecycle_eval as fle
from backend.scripts.factor_lifecycle_eval import (
    DOWNWEIGHT_REASON,
    build_plan,
    crowding_for_records,
    crowding_from_series,
    evaluate_factor,
    lifecycle_write_payload,
    load_crowding_by_column,
    materially_same_lifecycle,
    pick_cell,
    render_markdown,
    series_payload_of,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 11, tzinfo=UTC)
ASOF = date(2026, 10, 11)


def _row(**over):
    base = {
        "factor_id": "f1",
        "factor_name": "弱动量",
        "factor_code": "Rank($close,20)",
        "status": "completed",
        "market": "a_share",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "metadata_json": "{}",
    }
    base.update(over)
    return base


def _cell(run_id="r1", market="a_share", status="completed", created=None):
    return {
        "run_id": run_id,
        "factor_id": "f1",
        "market": market,
        "status": status,
        "created_at": created or datetime(2026, 10, 9, 8, tzinfo=UTC),
    }


def _weak_series(end=date(2026, 9, 30), n=200):
    """±0.01 交替 → mean≈0、std>0 → ICIR≈0（有效弱读数）。"""
    dates = [(end - timedelta(days=i)).isoformat() for i in range(n)][::-1]
    values = [0.01 if i % 2 == 0 else -0.01 for i in range(n)]
    return {"dates": dates, "ic": values}


OLD_RUN = datetime(2026, 1, 10, tzinfo=UTC)  # 早已过盲窗


class TestPickCell:
    def test_prefers_home_market_over_newer(self):
        cells = [
            _cell(
                "r_new", market="crypto", created=datetime(2026, 10, 11, 8, tzinfo=UTC)
            ),
            _cell(
                "r_home", market="a_share", created=datetime(2026, 10, 9, 8, tzinfo=UTC)
            ),
        ]
        assert pick_cell(cells, market="a_share")["run_id"] == "r_home"

    def test_falls_back_to_newest_when_no_home_match(self):
        cells = [
            _cell(
                "r_old", market="crypto", created=datetime(2026, 10, 9, 8, tzinfo=UTC)
            ),
            _cell(
                "r_new", market="crypto", created=datetime(2026, 10, 11, 8, tzinfo=UTC)
            ),
        ]
        assert pick_cell(cells, market="us_stock")["run_id"] == "r_new"

    def test_tiebreak_by_run_id_desc(self):
        same = datetime(2026, 10, 9, 8, tzinfo=UTC)
        cells = [
            _cell("r1", market="crypto", created=same),
            _cell("r9", market="crypto", created=same),
        ]
        assert pick_cell(cells, market="a_share")["run_id"] == "r9"

    def test_empty_returns_none(self):
        assert pick_cell([], market="a_share") is None


class TestEvaluateFactor:
    def test_weak_three_months_active_triggers_downweight(self):
        rec = evaluate_factor(
            _row(),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
            backfill_months=3,
        )
        assert rec["state_before"] == "active"
        assert rec["eval_status"] == "ok"
        assert rec["verdict"]["streak"] == 3
        assert rec["verdict"]["decision"] == "downweight"
        assert rec["action"] == "downweight"
        assert rec["state_after"] == "downweighted"
        assert len(rec["readings"]) == 3
        assert rec["applied"] is False

    def test_weak_but_blind_protects(self):
        rec = evaluate_factor(
            _row(status="pending"),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=datetime(2026, 9, 10, tzinfo=UTC),
            asof=ASOF,
            now=NOW,
            backfill_months=3,
        )
        assert rec["state_before"] == "observing"
        assert rec["verdict"]["decision"] == "observing_protected"
        assert rec["action"] == "none"
        assert rec["state_after"] == "observing"

    def test_no_cell_is_no_run(self):
        rec = evaluate_factor(
            _row(status="pending"),
            cell=None,
            series=None,
            first_completed_run_at=None,
            asof=ASOF,
            now=NOW,
        )
        assert rec["eval_status"] == "no_run"
        assert rec["readings"] == []
        assert rec["action"] == "none"
        assert rec["state_before"] == "experiment"

    def test_cell_not_completed_is_no_completed_series(self):
        rec = evaluate_factor(
            _row(),
            cell=_cell(status="failed"),
            series=None,
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
        )
        assert rec["eval_status"] == "no_completed_series"

    def test_completed_cell_without_series_payload(self):
        rec = evaluate_factor(
            _row(),
            cell=_cell(),
            series=None,
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
        )
        assert rec["eval_status"] == "no_series_payload"

    def test_already_downweighted_stays(self):
        rec = evaluate_factor(
            _row(metadata_json='{"lifecycle": {"state": "downweighted"}}'),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
            backfill_months=3,
        )
        assert rec["state_before"] == "downweighted"
        assert rec["action"] == "already"
        assert rec["state_after"] == "downweighted"

    def test_too_few_days_bucket(self):
        short = _weak_series(n=10)
        rec = evaluate_factor(
            _row(),
            cell=_cell(),
            series=short,
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
            backfill_months=1,
        )
        assert rec["eval_status"] == "too_few_days"
        assert rec["verdict"]["streak"] == 0
        assert rec["action"] == "none"


class TestBuildPlan:
    def _records(self):
        weak = evaluate_factor(
            _row(factor_id="fw"),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
            backfill_months=3,
        )
        protected = evaluate_factor(
            _row(factor_id="fp", status="pending"),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=datetime(2026, 9, 10, tzinfo=UTC),
            asof=ASOF,
            now=NOW,
            backfill_months=3,
        )
        no_run = evaluate_factor(
            _row(factor_id="fn", status="pending"),
            cell=None,
            series=None,
            first_completed_run_at=None,
            asof=ASOF,
            now=NOW,
        )
        return [weak, protected, no_run]

    def test_plan_buckets(self):
        plan = build_plan(self._records())
        assert plan["downweight_ids"] == ["fw"]
        assert plan["protected_ids"] == ["fp"]
        assert plan["by_eval_status"]["no_run"] == 1

    def test_weak_watch_includes_streak_1_2(self):
        # 单月弱读数（streak=1）→ 早警清单，不动作
        rec = evaluate_factor(
            _row(),
            cell=_cell(),
            series=_weak_series(),
            first_completed_run_at=OLD_RUN,
            asof=ASOF,
            now=NOW,
            backfill_months=1,
        )
        plan = build_plan([rec])
        assert plan["weak_watch_ids"] == ["f1"]
        assert plan["downweight_ids"] == []


class TestLifecycleWritePayload:
    def _rec(self, *, action="downweight", eval_status="ok", streak=3, latest=None):
        return {
            "factor_id": "f1",
            "factor_name": "弱动量",
            "eval_status": eval_status,
            "action": action,
            "blind_until": "2027-03-09T00:00:00Z",
            "verdict": {
                "streak": streak,
                "decision": "downweight" if action == "downweight" else "hold",
                "latest": latest
                if latest is not None
                else {
                    "month_end": "2026-09-30",
                    "n_days": 150,
                    "icir": 0.05,
                    "status": "ok",
                },
            },
        }

    def test_downweight_transition_sets_state_and_evidence(self):
        payload = lifecycle_write_payload(self._rec(), existing_lifecycle={}, now=NOW)
        assert payload["state"] == "downweighted"
        assert payload["reason"] == DOWNWEIGHT_REASON
        assert payload["since"].endswith("Z")
        assert payload["evidence"]["streak"] == 3
        assert payload["evidence"]["icir"] == 0.05
        assert payload["blind_until"] == "2027-03-09T00:00:00Z"
        assert payload["last_eval"]["status"] == "ok"

    def test_hold_updates_last_eval_and_preserves_existing(self):
        existing = {"since": "2026-01-01T00:00:00Z", "state": "active"}
        payload = lifecycle_write_payload(
            self._rec(action="none"), existing_lifecycle=existing, now=NOW
        )
        assert payload["state"] == "active"  # 非迁移不动显式覆盖
        assert payload["since"] == "2026-01-01T00:00:00Z"
        assert payload["last_eval"]["streak"] == 3
        assert payload["last_eval"]["evaluated_at"].endswith("Z")

    def test_no_run_leaves_existing_untouched(self):
        existing = {"state": "downweighted"}
        payload = lifecycle_write_payload(
            self._rec(action="none", eval_status="no_run", streak=0, latest={}),
            existing_lifecycle=existing,
            now=NOW,
        )
        assert payload == existing

    def test_last_eval_carries_insufficient_status(self):
        rec = self._rec(
            action="none", eval_status="no_completed_series", streak=0, latest={}
        )
        payload = lifecycle_write_payload(rec, existing_lifecycle={}, now=NOW)
        assert payload["last_eval"]["status"] == "no_completed_series"
        assert payload["last_eval"]["icir"] is None


class TestSeriesPayloadOf:
    """store.get_series 行 → 曲线载荷解包（载荷挂在行内 ``series`` 键下）。"""

    def test_unwraps_nested_series_dict(self):
        payload = {"dates": ["2026-09-30"], "ic": [0.01]}
        fetched = {"run_id": "r1", "market": "a_share", "series": payload}
        assert series_payload_of(fetched) == payload

    def test_none_row_is_none(self):
        assert series_payload_of(None) is None

    def test_row_without_payload_is_none(self):
        assert series_payload_of({"run_id": "r1", "series": None}) is None
        assert series_payload_of({}) is None

    def test_non_mapping_payload_is_none(self):
        assert series_payload_of({"series": "not-a-dict"}) is None


class TestMateriallySameLifecycle:
    """--apply 幂等判据：除 last_eval.evaluated_at 外全等 = 实质相同 → 不写。"""

    def test_same_except_evaluated_at_is_same(self):
        a = {
            "state": "active",
            "last_eval": {
                "month_end": "2026-09-30",
                "icir": 0.05,
                "evaluated_at": "2026-10-01T00:00:00Z",
            },
        }
        b = {
            "state": "active",
            "last_eval": {
                "month_end": "2026-09-30",
                "icir": 0.05,
                "evaluated_at": "2026-11-01T00:00:00Z",
            },
        }
        assert materially_same_lifecycle(a, b) is True

    def test_state_difference_is_not_same(self):
        a = {"state": "active", "last_eval": {"icir": 0.05, "evaluated_at": "x"}}
        b = {"state": "downweighted", "last_eval": {"icir": 0.05, "evaluated_at": "x"}}
        assert materially_same_lifecycle(a, b) is False

    def test_reading_difference_is_not_same(self):
        a = {"last_eval": {"icir": 0.05, "evaluated_at": "x"}}
        b = {"last_eval": {"icir": 0.15, "evaluated_at": "x"}}
        assert materially_same_lifecycle(a, b) is False

    def test_missing_last_eval_key_active_on_one_side(self):
        assert materially_same_lifecycle({}, {"last_eval": {"icir": 0.05}}) is False
        assert (
            materially_same_lifecycle({"state": "active"}, {"state": "active"}) is True
        )


class TestRenderMarkdown:
    def test_markdown_contains_key_sections(self):
        records = [
            evaluate_factor(
                _row(),
                cell=_cell(),
                series=_weak_series(),
                first_completed_run_at=OLD_RUN,
                asof=ASOF,
                now=NOW,
                backfill_months=3,
            )
        ]
        report = {
            "schema_version": 1,
            "generated_at": "2026-10-11T00:00:00Z",
            "asof": ASOF.isoformat(),
            "mode": "dry-run",
            "records": records,
        }
        md = render_markdown(report)
        assert "生命周期评估" in md
        assert "降权" in md
        assert "f1" in md
        assert "dry-run" in md

    def test_markdown_includes_crowding_rows_when_present(self):
        records = [
            {
                "factor_id": "f9abcdef123456",
                "factor_name": "带拥挤度",
                "materialized_column": "rd_x",
                "crowding": {
                    "score": 0.42,
                    "turnover_pct": 0.5,
                    "ic_autocorr_lag1": 0.34,
                    "n_days": 120,
                },
                "verdict": {},
                "action": "none",
                "readings": [],
            }
        ]
        report = {
            "schema_version": 1,
            "generated_at": "2026-10-11T00:00:00Z",
            "asof": ASOF.isoformat(),
            "mode": "dry-run",
            "records": records,
        }
        md = render_markdown(report)
        assert "拥挤度" in md
        assert "rd_x" in md
        assert "0.420" in md
        assert "个物化列有读数" in md


def _dense_series(n: int = 400, end: date = date(2026, 9, 30)):
    """逐日 ic/turnover（带自相关与趋势）——crowding 可算的合成序列。"""
    dates, ics, tns = [], [], []
    for i in range(n):
        d = end - timedelta(days=n - 1 - i)
        dates.append(d.strftime("%Y%m%d"))
        ics.append(0.02 + 0.005 * ((i % 7) - 3))
        tns.append(0.1 + 0.001 * i)
    return dates, ics, tns


class TestCrowdingFromSeries:
    """细案 §七.1 顺带记录：rd_mined factor_series → crowding_score（记录不裁决）。"""

    def test_score_computed_from_daily_ic_and_turnover(self):
        dates, ics, tns = _dense_series()
        out = crowding_from_series(dates, ics, tns, asof=date(2026, 9, 30))
        assert out["score"] is not None
        assert 0.0 <= out["score"] <= 1.0
        assert out["turnover_pct"] is not None
        assert out["n_days"] > 0

    def test_asof_slices_future_rows(self):
        dates, ics, tns = _dense_series()
        early = crowding_from_series(
            dates, ics, tns, asof=date(2026, 9, 30) - timedelta(days=300)
        )
        full = crowding_from_series(dates, ics, tns, asof=date(2026, 9, 30))
        assert early["n_days"] < full["n_days"]

    def test_iso_dates_also_accepted(self):
        dates, ics, tns = _dense_series(n=100)
        iso = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
        out = crowding_from_series(iso, ics, tns, asof=date(2026, 9, 30))
        assert out["n_days"] > 0

    def test_missing_turnover_yields_no_data_shape_not_zero(self):
        dates, ics, _ = _dense_series(n=100)
        out = crowding_from_series(
            dates, ics, [None] * len(dates), asof=date(2026, 9, 30)
        )
        assert out["score"] is None  # 「无数据」不是「0 分」

    def test_garbage_rows_skipped_not_crash(self):
        out = crowding_from_series(
            ["", "bad", "20260930"],
            [None, "x", 0.01],
            [None, 1.0, 0.2],
            asof=date(2026, 9, 30),
        )
        assert out is not None
        # 只有第三行有效 → 单点无自相关可算，但形状完整不炸
        assert "score" in out

    def test_non_finite_ic_never_leaks_nan_into_score_fields(self):
        dates, ics, tns = _dense_series(n=120)
        ics = [None if i % 3 == 0 else v for i, v in enumerate(ics)]
        out = crowding_from_series(dates, ics, tns, asof=date(2026, 9, 30))
        for value in out.values():
            if isinstance(value, float):
                assert value == value and value not in (float("inf"), float("-inf"))


class TestCrowdingForRecords:
    def test_attaches_by_materialized_column_and_none_without(self):
        records = [
            {"factor_id": "a", "materialized_column": "rd_x"},
            {"factor_id": "b", "materialized_column": "rd_missing"},
            {"factor_id": "c"},
        ]
        crowding = {"rd_x": {"score": 0.42, "n_days": 100}}
        out = crowding_for_records(records, crowding)
        assert out[0]["crowding"] == {"score": 0.42, "n_days": 100}
        assert out[1]["crowding"] is None
        assert out[2]["crowding"] is None
        # 原记录不被改动（不可变返回新对象）
        assert "crowding" not in records[0]


class TestLoadCrowdingByColumn:
    def test_missing_file_returns_empty_not_fake(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            fle, "_factor_series_path", lambda: tmp_path / "nope.parquet"
        )
        assert load_crowding_by_column(["rd_x"], asof=ASOF) == {}

    def test_no_columns_requested_returns_empty(self):
        assert load_crowding_by_column([], asof=ASOF) == {}

    def test_reads_parquet_filters_columns_and_computes(self, tmp_path, monkeypatch):
        pytest.importorskip("pyarrow")
        import pandas as pd

        dates, ics, tns = _dense_series(n=200)
        df = pd.DataFrame(
            {
                "factor": ["rd_x"] * 200 + ["rd_other"] * 200,
                "date": dates + dates,
                "ic": ics + ics,
                "turnover": tns + tns,
            }
        )
        path = tmp_path / "factor_series.parquet"
        df.to_parquet(path)
        monkeypatch.setattr(fle, "_factor_series_path", lambda: path)

        out = load_crowding_by_column(["rd_x"], asof=date(2026, 9, 30))
        assert set(out.keys()) == {"rd_x"}  # 未请求的列不返回
        assert out["rd_x"]["score"] is not None

    def test_processing_error_returns_empty_not_raise(self, tmp_path, monkeypatch):
        # 评审 LOW-4（2026-10-11）：旧 try 只包读取+过滤——groupby/逐列计算
        # 抛错时异常穿透，「顺带记录面」会把评估主链拖垮。整段都该降级。
        pytest.importorskip("pyarrow")
        import pandas as pd

        dates, ics, tns = _dense_series(n=10)
        df = pd.DataFrame(
            {"factor": ["rd_x"] * 10, "date": dates, "ic": ics, "turnover": tns}
        )
        path = tmp_path / "factor_series.parquet"
        df.to_parquet(path)
        monkeypatch.setattr(fle, "_factor_series_path", lambda: path)

        def _boom(*args, **kwargs):
            raise ValueError("compute exploded")

        monkeypatch.setattr(fle, "crowding_from_series", _boom)
        assert load_crowding_by_column(["rd_x"], asof=ASOF) == {}
