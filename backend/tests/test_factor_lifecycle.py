"""``shared/factor_lifecycle.py``：生命周期状态机 + 12m 评估判定律 + 台账 IO（T-FE-06）。

口径纪律：三态分流（ok/不足/陈旧）永不把「不可算」冒充「差」；连击计数
只认连续有效弱读数；盲窗内判定动作降级为记录（observing_protected）。
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.shared.factor_lifecycle import (
    BLIND_DAYS,
    DECISION_DOWNWEIGHT,
    DECISION_HOLD,
    DECISION_OBSERVING_PROTECTED,
    ICIR_FLOOR,
    MIN_N_DAYS,
    READING_FLAT,
    READING_NO_SERIES,
    READING_OK,
    READING_SERIES_STALE,
    READING_TOO_FEW,
    STATE_ACTIVE,
    STATE_DOWNWEIGHTED,
    STATE_EXPERIMENT,
    STATE_OBSERVING,
    STALE_DAYS,
    append_event,
    blind_until_of,
    has_completed_backtest,
    lifecycle_view,
    load_ledger,
    maturity_of,
    month_ends_back,
    read_month,
    redact_blind_readings,
    resolve_state,
    save_ledger,
    streak_of,
    upsert_readings,
    verdict_of,
)

UTC = timezone.utc


def _series(n_days: int, *, end: date, values: list[float] | None = None):
    """构造逐日序列：连续自然日（测试口径，交易日无关）+ 给定值。"""
    dates = [(end - timedelta(days=i)).isoformat() for i in range(n_days)][::-1]
    if values is None:
        values = [0.1 if i % 2 == 0 else -0.05 for i in range(n_days)]
    return {"dates": dates, "ic": values}


class TestResolveState:
    def test_no_completed_run_is_experiment(self):
        assert (
            resolve_state(
                explicit_state=None,
                has_completed_run=False,
                blind_until=None,
                now=datetime(2026, 10, 11, tzinfo=UTC),
            )
            == STATE_EXPERIMENT
        )

    def test_completed_run_inside_blind_window_is_observing(self):
        now = datetime(2026, 10, 11, tzinfo=UTC)
        assert (
            resolve_state(
                explicit_state=None,
                has_completed_run=True,
                blind_until=now + timedelta(days=1),
                now=now,
            )
            == STATE_OBSERVING
        )

    def test_completed_run_past_blind_window_is_active(self):
        now = datetime(2026, 10, 11, tzinfo=UTC)
        assert (
            resolve_state(
                explicit_state=None,
                has_completed_run=True,
                blind_until=now - timedelta(seconds=1),
                now=now,
            )
            == STATE_ACTIVE
        )

    def test_explicit_downweighted_wins_over_derived(self):
        now = datetime(2026, 10, 11, tzinfo=UTC)
        assert (
            resolve_state(
                explicit_state=STATE_DOWNWEIGHTED,
                has_completed_run=True,
                blind_until=now - timedelta(days=400),
                now=now,
            )
            == STATE_DOWNWEIGHTED
        )

    def test_unknown_explicit_state_ignored_falls_back_to_derived(self):
        now = datetime(2026, 10, 11, tzinfo=UTC)
        assert (
            resolve_state(
                explicit_state="banana",
                has_completed_run=True,
                blind_until=now - timedelta(days=400),
                now=now,
            )
            == STATE_ACTIVE
        )


class TestBlindUntil:
    def test_first_completed_run_anchors_blind_window(self):
        created = datetime(2026, 1, 1, tzinfo=UTC)
        first_run = datetime(2026, 2, 1, tzinfo=UTC)
        result = blind_until_of(created_at=created, first_completed_run_at=first_run)
        assert result == first_run + timedelta(days=BLIND_DAYS)

    def test_falls_back_to_row_created_at_without_run(self):
        created = datetime(2026, 1, 1, tzinfo=UTC)
        assert blind_until_of(
            created_at=created, first_completed_run_at=None
        ) == created + timedelta(days=BLIND_DAYS)

    def test_naive_input_treated_as_utc_returns_aware(self):
        naive = datetime(2026, 1, 1)
        result = blind_until_of(created_at=naive, first_completed_run_at=None)
        assert result.tzinfo is not None
        assert result == datetime(2026, 6, 30, tzinfo=UTC)

    def test_both_anchors_none_returns_none_not_fabricated(self):
        # 评审 LOW-2（2026-10-11）：无锚点不虚构 now+180d 伪终点——
        # 没有事实锚点就没有盲窗，诚实交 None 给调用方。
        assert blind_until_of(created_at=None, first_completed_run_at=None) is None


class TestMaturityOf:
    def test_state_labels(self):
        assert maturity_of(STATE_EXPERIMENT) == "实验"
        assert maturity_of(STATE_OBSERVING) == "观察"
        assert maturity_of(STATE_ACTIVE) == "在用"
        assert maturity_of(STATE_DOWNWEIGHTED) == "退役"

    def test_validating_is_display_only_when_experiment_has_active_run(self):
        assert maturity_of(STATE_EXPERIMENT, has_active_validation=True) == "验证中"
        # 非实验态不受验证标记影响
        assert maturity_of(STATE_ACTIVE, has_active_validation=True) == "在用"

    def test_unknown_state_is_unknown_not_guessed(self):
        assert maturity_of("banana") == "unknown"


class TestReadMonth:
    MONTH_END = date(2026, 9, 30)

    def _read(self, series, **kw):
        return read_month(series, month_end=self.MONTH_END, **kw)

    def test_ok_reading_computes_icir_ddof1(self):
        series = _series(150, end=self.MONTH_END)

        reading = self._read(series)

        assert reading.status == READING_OK
        assert reading.n_days == 150
        expected = statistics.mean(series["ic"]) / statistics.stdev(series["ic"])
        assert reading.icir == pytest.approx(expected)
        # icir ≈ 0.33 ≥ 0.2 → 不算弱读数
        assert reading.icir > ICIR_FLOOR
        assert reading.below_floor is False

    def test_window_is_half_open_excludes_exactly_365d_ago(self):
        old = (self.MONTH_END - timedelta(days=365)).isoformat()  # 恰 365 天前：排除
        inside = (self.MONTH_END - timedelta(days=364)).isoformat()
        recent = (self.MONTH_END - timedelta(days=1)).isoformat()
        series = {"dates": [old, inside, recent], "ic": [9.0, 0.1, -0.1]}

        reading = self._read(series, min_n_days=1)

        # old 若混入会多一天并拉爆 icir；n_days=2 证明半开窗正确
        assert reading.n_days == 2
        assert reading.status == READING_OK

    def test_none_ic_values_do_not_count_as_days(self):
        series = _series(100, end=self.MONTH_END)
        series["ic"] = [None] * 50 + series["ic"][50:]

        reading = self._read(series, min_n_days=40)

        assert reading.n_days == 50
        assert reading.status == READING_OK

    def test_stale_series_refused(self):
        series = _series(150, end=self.MONTH_END - timedelta(days=STALE_DAYS + 1))

        reading = self._read(series)

        assert reading.status == READING_SERIES_STALE
        assert reading.icir is None

    def test_too_few_days_refused_not_claimed_bad(self):
        series = _series(MIN_N_DAYS - 1, end=self.MONTH_END)

        reading = self._read(series)

        assert reading.status == READING_TOO_FEW
        assert reading.icir is None
        assert reading.below_floor is None

    def test_flat_series_is_undecidable(self):
        series = _series(150, end=self.MONTH_END, values=[0.0] * 150)

        reading = self._read(series)

        assert reading.status == READING_FLAT
        assert reading.icir is None

    def test_empty_series_no_series(self):
        assert self._read({"dates": [], "ic": []}).status == READING_NO_SERIES
        assert self._read(None).status == READING_NO_SERIES

    def test_backfill_determinism_ignores_data_after_month_end(self):
        # 同一序列，对 2026-06-30 的读数不受 7 月后数据影响
        series = _series(400, end=self.MONTH_END)
        baseline = read_month(
            {**series, "dates": [], "ic": []}, month_end=date(2026, 6, 30)
        )
        with_full = read_month(series, month_end=date(2026, 6, 30))
        truncated = read_month(
            {
                "dates": [d for d in series["dates"] if d <= "2026-06-30"],
                "ic": series["ic"][: sum(d <= "2026-06-30" for d in series["dates"])],
            },
            month_end=date(2026, 6, 30),
        )
        assert baseline.status == READING_NO_SERIES
        assert with_full == truncated

    def test_non_finite_ic_not_counted_never_poisons_icir(self):
        # 评审 LOW-1（2026-10-11）：NaN/inf 不是读数——旧实现 NaN 进样本，
        # 均值/方差传染成 NaN 却仍标 ok（JSON 序列化还会炸）。
        series = _series(150, end=self.MONTH_END)
        series["ic"] = series["ic"][:125] + [float("nan")] * 24 + [float("inf")]

        reading = self._read(series)

        assert reading.n_days == 125
        assert reading.status == READING_OK
        assert reading.icir is not None and math.isfinite(reading.icir)

    def test_all_non_finite_ic_is_no_series_not_ok(self):
        series = _series(150, end=self.MONTH_END)
        series["ic"] = [float("nan")] * 150

        assert self._read(series).status == READING_NO_SERIES

    def test_stale_gate_sees_only_data_upto_month_end(self):
        # 评审 LOW-1：陈旧闸的「序列最新日」只看 ≤ month_end 的数据（与
        # docstring/回填确定性一致）——旧实现取全序列最大日，序列只有月末
        # 之后的数据时（3-31 参考点 vs 6-9 月才有的序列）误判为「不陈旧」。
        series = _series(120, end=date(2026, 9, 30))  # 数据全在 4 月之后

        reading = read_month(series, month_end=date(2026, 3, 31))

        assert reading.status == READING_SERIES_STALE
        assert reading.icir is None

    def test_weak_reading_below_floor_flagged(self):
        values = [0.01 if i % 2 == 0 else -0.012 for i in range(200)]
        series = _series(200, end=self.MONTH_END, values=values)

        reading = self._read(series)

        assert reading.status == READING_OK
        assert reading.icir < ICIR_FLOOR
        assert reading.below_floor is True


class TestStreak:
    def _reading(self, month_end, *, status=READING_OK, floor_hit=False):
        return read_month(
            _series(
                150,
                end=date.fromisoformat(month_end),
                values=(
                    [0.01 if i % 2 == 0 else -0.012 for i in range(150)]
                    if floor_hit
                    else [0.1 if i % 2 == 0 else -0.05 for i in range(150)]
                ),
            ),
            month_end=date.fromisoformat(month_end),
        )

    def test_trailing_weak_count(self):
        readings = [
            self._reading("2026-06-30", floor_hit=True),
            self._reading("2026-07-31", floor_hit=True),
            self._reading("2026-08-31", floor_hit=True),
        ]
        assert streak_of(readings) == 3

    def test_recovery_resets_streak(self):
        readings = [
            self._reading("2026-06-30", floor_hit=True),
            self._reading("2026-07-31", floor_hit=False),  # 回升
            self._reading("2026-08-31", floor_hit=True),
            self._reading("2026-09-30", floor_hit=True),
        ]
        assert streak_of(readings) == 2

    def test_insufficient_reading_breaks_streak_not_counted(self):
        insufficient = read_month({"dates": [], "ic": []}, month_end=date(2026, 8, 31))
        readings = [
            self._reading("2026-06-30", floor_hit=True),
            self._reading("2026-07-31", floor_hit=True),
            insufficient,
            self._reading("2026-09-30", floor_hit=True),
        ]
        assert streak_of(readings) == 1

    def test_empty_gives_zero(self):
        assert streak_of([]) == 0


class TestVerdict:
    def _weak(self, month_end):
        return read_month(
            _series(
                150,
                end=date.fromisoformat(month_end),
                values=[0.01 if i % 2 == 0 else -0.012 for i in range(150)],
            ),
            month_end=date.fromisoformat(month_end),
        )

    def test_below_threshold_hold(self):
        verdict = verdict_of(
            [self._weak("2026-08-31"), self._weak("2026-09-30")],
            blind_until=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert verdict["decision"] == DECISION_HOLD
        assert verdict["streak"] == 2

    def test_three_month_streak_triggers_downweight(self):
        readings = [
            self._weak("2026-07-31"),
            self._weak("2026-08-31"),
            self._weak("2026-09-30"),
        ]
        verdict = verdict_of(readings, blind_until=datetime(2026, 1, 1, tzinfo=UTC))
        assert verdict["decision"] == DECISION_DOWNWEIGHT
        assert verdict["streak"] == 3
        assert verdict["latest"]["month_end"] == "2026-09-30"

    def test_blind_window_protects_from_downweight(self):
        readings = [
            self._weak("2026-07-31"),
            self._weak("2026-08-31"),
            self._weak("2026-09-30"),
        ]
        verdict = verdict_of(readings, blind_until=datetime(2026, 12, 31, tzinfo=UTC))
        assert verdict["decision"] == DECISION_OBSERVING_PROTECTED
        assert verdict["streak"] == 3

    def test_month_end_on_blind_day_is_protected(self):
        # 评审 MEDIUM-1（2026-10-11）：盲窗终点当天（如 21:00Z 到期）跑评估，
        # 最新月末 == 终点日 —— 旧日粒度判据 `latest_day < blind.date()` 判
        # 假（10-31 不 < 10-31）→ 窗口真正结束前 ~11h 就被降权。同日保守判保护。
        readings = [
            self._weak("2026-08-31"),
            self._weak("2026-09-30"),
            self._weak("2026-10-31"),
        ]
        verdict = verdict_of(
            readings, blind_until=datetime(2026, 10, 31, 21, 0, tzinfo=UTC)
        )
        assert verdict["decision"] == DECISION_OBSERVING_PROTECTED

    def test_month_end_day_after_blind_day_still_downweights(self):
        readings = [
            self._weak("2026-08-31"),
            self._weak("2026-09-30"),
            self._weak("2026-10-31"),
        ]
        verdict = verdict_of(
            readings, blind_until=datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
        )
        assert verdict["decision"] == DECISION_DOWNWEIGHT


class TestRedactBlindReadings:
    """装配层元数据脱敏（评审 security-MEDIUM，2026-10-11）。

    ``metadata_json`` 是 ``lifecycle`` 视图之外的同源出口——盲窗内/证据降级
    时只裁视图字段等于没裁（``_decode_metadata`` 原样透传）。
    """

    def test_strips_last_eval_and_evidence_returns_new_dict(self):
        meta = {
            "task_id": "t1",
            "lifecycle": {
                "state": "downweighted",
                "blind_until": "2026-10-31T21:00:00Z",
                "last_eval": {"month_end": "2026-09-30", "icir": -0.1},
                "evidence": {"month_end": "2026-09-30", "streak": 3},
            },
        }

        out = redact_blind_readings(meta)

        assert out == {
            "task_id": "t1",
            "lifecycle": {
                "state": "downweighted",
                "blind_until": "2026-10-31T21:00:00Z",
            },
        }
        assert out is not meta
        # 输入不可变（持久层证据原地保留）
        assert "last_eval" in meta["lifecycle"]
        assert "evidence" in meta["lifecycle"]

    def test_no_lifecycle_key_passthrough_new_dict(self):
        meta = {"task_id": "t1", "source": "rd_agent"}
        out = redact_blind_readings(meta)
        assert out == meta
        assert out is not meta

    def test_non_mapping_lifecycle_left_as_is(self):
        out = redact_blind_readings({"lifecycle": "banana"})
        assert out == {"lifecycle": "banana"}

    def test_none_and_non_mapping_give_empty_dict(self):
        assert redact_blind_readings(None) == {}
        assert redact_blind_readings("boom") == {}


class TestMonthEndsBack:
    def test_mid_month_starts_from_previous_month_end(self):
        ends = month_ends_back(date(2026, 10, 11), 4)
        assert [d.isoformat() for d in ends] == [
            "2026-06-30",
            "2026-07-31",
            "2026-08-31",
            "2026-09-30",
        ]

    def test_exact_month_end_included(self):
        ends = month_ends_back(date(2026, 9, 30), 2)
        assert [d.isoformat() for d in ends] == ["2026-08-31", "2026-09-30"]

    def test_crosses_year_and_february(self):
        ends = month_ends_back(date(2027, 3, 15), 5)
        assert [d.isoformat() for d in ends] == [
            "2026-10-31",
            "2026-11-30",
            "2026-12-31",
            "2027-01-31",
            "2027-02-28",
        ]


class TestLedger:
    def test_load_missing_file_gives_fresh_skeleton(self, tmp_path):
        ledger = load_ledger(tmp_path / "nope.json")
        assert ledger["schema_version"] == 1
        assert ledger["factors"] == {}

    def test_load_corrupt_file_gives_skeleton_not_raise(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_ledger(p)["factors"] == {}

    def test_save_roundtrip_and_atomic(self, tmp_path):
        p = tmp_path / "ledger.json"
        ledger = load_ledger(p)
        ledger = upsert_readings(
            ledger,
            "f1",
            [
                read_month(
                    _series(150, end=date(2026, 9, 30)), month_end=date(2026, 9, 30)
                )
            ],
            run_id="r1",
            now=datetime(2026, 10, 1, tzinfo=UTC),
        )
        save_ledger(p, ledger)

        reloaded = load_ledger(p)
        assert reloaded["factors"]["f1"]["readings"][0]["month_end"] == "2026-09-30"
        assert not list(tmp_path.glob("*.tmp*"))

    def test_upsert_replaces_same_month_and_prunes_to_24(self, tmp_path):
        ledger = load_ledger(tmp_path / "l.json")
        ends = month_ends_back(date(2026, 3, 15), 26)
        readings = [
            read_month(_series(150, end=e, values=[0.0] * 150), month_end=e)
            for e in ends
        ]
        for _ in range(3):  # 同一批重放三次
            ledger = upsert_readings(
                ledger,
                "f1",
                readings,
                run_id="r1",
                now=datetime(2026, 3, 15, tzinfo=UTC),
            )
        stored = ledger["factors"]["f1"]["readings"]
        # 幂等（同 month_end 替换不追加）+ 上限 24 剪枝（保留最近 24 期、升序）
        assert [r["month_end"] for r in stored] == [e.isoformat() for e in ends[-24:]]

    def test_upsert_immutability_input_unchanged(self, tmp_path):
        ledger = load_ledger(tmp_path / "l.json")
        reading = read_month(
            _series(150, end=date(2026, 9, 30)), month_end=date(2026, 9, 30)
        )
        before = json.loads(json.dumps(ledger))
        upsert_readings(
            ledger, "f2", [reading], run_id="r1", now=datetime(2026, 10, 1, tzinfo=UTC)
        )
        assert ledger == before  # 返回新对象，不改入参


class TestSeriesWindowEdge:
    def test_reading_excludes_future_dates_beyond_month_end(self):
        series = _series(150, end=date(2026, 9, 30))
        extra_dates = ["2026-10-05", "2026-10-06"]
        padded = {
            "dates": series["dates"] + extra_dates,
            "ic": series["ic"] + [5.0, 5.0],
        }
        r_small = read_month(padded, month_end=date(2026, 9, 30))
        r_big = read_month(series, month_end=date(2026, 9, 30))
        assert r_small == r_big


class TestLifecycleView:
    NOW = datetime(2026, 10, 11, tzinfo=UTC)

    def test_observing_hides_last_eval(self):
        row = {
            "created_at": datetime(2026, 9, 1, tzinfo=UTC),
            "metadata": {"lifecycle": {"last_eval": {"icir": 0.1, "streak": 2}}},
        }
        view = lifecycle_view(
            row,
            first_completed_run_at=datetime(2026, 9, 10, tzinfo=UTC),
            now=self.NOW,
        )
        assert view["state"] == STATE_OBSERVING
        assert view["maturity"] == "观察"
        assert view["last_eval"] is None  # 盲窗：研究员面不可见
        assert view["blind_until"].endswith("Z")

    def test_active_reveals_last_eval(self):
        row = {
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata": {"lifecycle": {"last_eval": {"icir": 0.35, "streak": 0}}},
        }
        view = lifecycle_view(
            row,
            first_completed_run_at=datetime(2026, 1, 5, tzinfo=UTC),
            now=self.NOW,
        )
        assert view["state"] == STATE_ACTIVE
        assert view["last_eval"] == {"icir": 0.35, "streak": 0}

    def test_explicit_downweighted_state_wins_and_eval_visible(self):
        row = {
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata": {
                "lifecycle": {
                    "state": "downweighted",
                    "last_eval": {"icir": -0.05, "streak": 3},
                }
            },
        }
        view = lifecycle_view(
            row,
            first_completed_run_at=datetime(2026, 1, 5, tzinfo=UTC),
            now=self.NOW,
        )
        assert view["state"] == STATE_DOWNWEIGHTED
        assert view["maturity"] == "退役"
        assert view["last_eval"] == {"icir": -0.05, "streak": 3}

    def test_no_created_at_no_run_is_experiment_without_blind(self):
        view = lifecycle_view({}, first_completed_run_at=None, now=self.NOW)
        assert view["state"] == STATE_EXPERIMENT
        assert view["blind_until"] is None
        assert view["last_eval"] is None

    def test_validating_display_flag_passthrough(self):
        row = {"created_at": datetime(2026, 10, 1, tzinfo=UTC)}
        view = lifecycle_view(
            row,
            first_completed_run_at=None,
            now=self.NOW,
            has_active_validation=True,
        )
        assert view["state"] == STATE_EXPERIMENT
        assert view["maturity"] == "验证中"

    def test_metadata_json_string_form_supported(self):
        row = {
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "metadata_json": '{"lifecycle": {"state": "downweighted"}}',
        }
        view = lifecycle_view(
            row,
            first_completed_run_at=datetime(2026, 1, 5, tzinfo=UTC),
            now=self.NOW,
        )
        assert view["state"] == STATE_DOWNWEIGHTED

    def test_status_completed_without_run_derives_active(self):
        # 存量旧流程因子（成功回测过、无 run 台账行）→ 在用，不是实验
        row = {
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "status": "completed",
        }
        view = lifecycle_view(row, first_completed_run_at=None, now=self.NOW)
        assert view["state"] == STATE_ACTIVE
        assert view["maturity"] == "在用"


class TestHasCompletedBacktest:
    """成功率证据 = run 台账 completed 行 ∨ factor.status=completed（旧流程）。"""

    def test_run_row_present_is_true(self):
        assert (
            has_completed_backtest(
                {"status": "pending"},
                first_completed_run_at=datetime(2026, 10, 9, tzinfo=UTC),
            )
            is True
        )

    def test_status_completed_without_run_is_true(self):
        assert has_completed_backtest({"status": "completed"}) is True

    def test_status_pending_without_run_is_false(self):
        assert has_completed_backtest({"status": "pending"}) is False

    def test_status_failed_without_run_is_false(self):
        assert has_completed_backtest({"status": "failed"}) is False

    def test_missing_status_and_run_is_false(self):
        assert has_completed_backtest({}) is False


class TestAppendEvent:
    """状态变更史：不可变并入、带 UTC 戳、按上限剪枝、保留既有读数。"""

    def test_appends_with_stamp_and_returns_new(self, tmp_path):
        ledger = load_ledger(tmp_path / "l.json")
        ledger = upsert_readings(
            ledger,
            "f1",
            [
                read_month(
                    _series(150, end=date(2026, 9, 30)), month_end=date(2026, 9, 30)
                )
            ],
            run_id="r1",
            now=datetime(2026, 10, 1, tzinfo=UTC),
        )
        before = json.loads(json.dumps(ledger))
        out = append_event(
            ledger,
            "f1",
            from_state=STATE_ACTIVE,
            to_state=STATE_DOWNWEIGHTED,
            reason="rolling_12m_icir_below_floor_3_months",
            evidence={"streak": 3, "icir": 0.05},
            now=datetime(2026, 10, 11, tzinfo=UTC),
        )
        assert ledger == before  # 返回新对象，不改入参
        events = out["factors"]["f1"]["events"]
        assert events[0]["to"] == STATE_DOWNWEIGHTED
        assert events[0]["from"] == STATE_ACTIVE
        assert events[0]["at"].endswith("Z")
        assert events[0]["evidence"]["streak"] == 3
        # 既有读数不被事件并入挤掉
        assert out["factors"]["f1"]["readings"][0]["month_end"] == "2026-09-30"

    def test_multiple_events_keep_order_and_prune_to_24(self, tmp_path):
        ledger = load_ledger(tmp_path / "l.json")
        for i in range(26):
            ledger = append_event(
                ledger,
                "f1",
                from_state=STATE_ACTIVE,
                to_state=STATE_OBSERVING,
                reason=f"r{i}",
                now=datetime(2026, 10, 11, tzinfo=UTC),
            )
        events = ledger["factors"]["f1"]["events"]
        assert len(events) == 24
        assert events[-1]["reason"] == "r25"  # 剪最旧、保序

    def test_evidence_optional_and_none_state_allowed(self, tmp_path):
        ledger = load_ledger(tmp_path / "l.json")
        out = append_event(
            ledger,
            "f1",
            from_state=None,
            to_state=STATE_DOWNWEIGHTED,
            reason="x",
            now=datetime(2026, 10, 11, tzinfo=UTC),
        )
        assert out["factors"]["f1"]["events"][0]["evidence"] is None
        assert out["factors"]["f1"]["events"][0]["from"] is None
