"""T-FE-08 PIT 核查三件套（``audit_pit_trio``）纯函数契约测试。

钉住四类判据原语（分类器 / 前驱日 / 幻影跨度 / 判定），它们是整份审计
报告数字的唯一出处；另测报告渲染对缺键的容忍（先例
``test_audit_fm_structure.py``）——外部报告 JSON 缺段不得把整份审计搞崩。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_AUDIT_PATH = ROOT / "backend" / "scripts" / "audit" / "audit_pit_trio.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "_audit_pit_trio_under_test", _AUDIT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def audit():
    return _load_audit()


# ── 分类器 ──────────────────────────────────────────────────────────


def _classify_case(audit):
    stored = np.array([100.0, 100.0, 200.0, 300.0, np.nan, 50.0, np.nan])
    pit1 = np.array([100.0, 110.0, 120.0, 130.0, np.nan, 50.0, 100.0])
    pit0 = np.array([100.0, 110.0, 200.0, 130.0, 100.0, 50.0, 100.0])
    naive = np.array([100.0, 110.0, 999.0, 300.0, 100.0, 50.0, 100.0])
    return audit._classify_cells(stored, pit1, pit0, naive)


def test_classify_precedence_and_labels(audit):
    labels = _classify_case(audit)
    assert list(labels) == [
        "match_pit1",  # 三视图同值 → 取最高优先 match_pit1
        "diverged",  # 三视图均不可解释
        "match_pit0_only",  # 仅公告当日口径可解释
        "match_naive_only",  # 前瞻：只有 naive 可解释
        "missing",  # 参照（pit1）不可算
        "match_pit1",  # 全等
        "stored_nan",  # 源侧空值，参照可算
    ]


def test_classify_tolerance_boundary(audit):
    # rtol=1e-3：100.0 的 ±0.1 内视为一致
    labels = audit._classify_cells(
        np.array([100.05, 100.2]),
        np.array([100.0, 100.0]),
        np.array([100.0, 100.0]),
        np.array([100.0, 100.0]),
    )
    assert list(labels) == ["match_pit1", "diverged"]


def test_classify_all_equal_zero_values(audit):
    labels = audit._classify_cells(
        np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([0.0])
    )
    assert list(labels) == ["match_pit1"]


# ── 判别力旗标与摘要 ────────────────────────────────────────────────


def test_disc_flags(audit):
    pit1 = np.array([100.0, 120.0, 100.0, np.nan])
    pit0 = np.array([100.0, 200.0, 100.0, 100.0])
    naive = np.array([100.0, 120.0, 130.0, 100.0])
    d_naive, d_lag = audit._disc_flags(pit1, pit0, naive)
    assert list(d_naive) == [False, False, True, False]  # 仅 idx2 naive≠pit1
    assert list(d_lag) == [False, True, False, False]  # 仅 idx1 pit0≠pit1


def test_summarize_labels(audit):
    labels = _classify_case(audit)
    pit1 = np.array([100.0, 110.0, 120.0, 130.0, np.nan, 50.0, 100.0])
    pit0 = np.array([100.0, 110.0, 200.0, 130.0, 100.0, 50.0, 100.0])
    naive = np.array([100.0, 110.0, 999.0, 300.0, 100.0, 50.0, 100.0])
    d_naive, d_lag = audit._disc_flags(pit1, pit0, naive)
    s = audit._summarize_labels(labels, d_naive, d_lag)
    assert s["n_total"] == 7
    assert s["counts"]["match_pit1"] == 2
    assert s["counts"]["match_naive_only"] == 1
    # 判别格（naive≠pit1 且参照可算）：idx2（stored=pit0）、idx3（stored=naive）
    assert s["disc_naive_n"] == 2
    assert s["leak_naive_only_in_disc"] == 1
    assert s["leak_rate_in_disc"] == pytest.approx(0.5)
    # 判别格（pit0≠pit1）：仅 idx2
    assert s["disc_lag_n"] == 1
    assert s["match_pit0_only_in_disc_lag"] == 1


def test_summarize_labels_no_discriminating_cells(audit):
    labels = np.array(["match_pit1", "match_pit1"], dtype=object)
    z = np.zeros(2, dtype=bool)
    s = audit._summarize_labels(labels, z, z)
    assert s["disc_naive_n"] == 0
    assert s["leak_rate_in_disc"] is None


# ── 前驱交易日 ──────────────────────────────────────────────────────


def test_prev_trading_day(audit):
    cal = ["20260105", "20260106", "20260107"]
    assert audit._prev_trading_day(cal, "20260106") == "20260105"
    assert audit._prev_trading_day(cal, "20260108") == "20260107"
    assert audit._prev_trading_day(cal, "20260105") is None
    assert audit._prev_trading_day(cal, "20260104") is None
    assert audit._prev_trading_day([], "20260106") is None


# ── 幻影跨度 ────────────────────────────────────────────────────────


def test_spans_from_pairs(audit):
    cal = [f"2026010{i}" for i in range(1, 8)]
    pairs = [
        ("20260101", "A"),
        ("20260102", "A"),
        ("20260103", "A"),
        ("20260106", "A"),
        ("20260107", "A"),
        ("20260104", "B"),
        ("20260105", "B"),
        ("20260106", "B"),
        ("20269999", "C"),  # 不在日历内 → 忽略
    ]
    spans = audit._spans_from_pairs(pairs, cal)
    assert spans == [
        {"symbol": "A", "start": "20260101", "end": "20260103", "n_days": 3},
        {"symbol": "B", "start": "20260104", "end": "20260106", "n_days": 3},
        {"symbol": "A", "start": "20260106", "end": "20260107", "n_days": 2},
    ]


# ── 退市判定 ────────────────────────────────────────────────────────


def test_fixture_verdict(audit):
    assert (
        audit._fixture_verdict([True, True, True], [0, 0, 0, 0])
        == "survivorship_filter_confirmed"
    )
    assert audit._fixture_verdict([True, False], [0, 0]) == "not_confirmed"  # 任一在场
    assert (
        audit._fixture_verdict([True], [0, 3]) == "not_confirmed"
    )  # 累计差非零=成员被剔除
    assert audit._fixture_verdict([], []) == "not_confirmed"  # 无样本
    assert (
        audit._fixture_verdict([True], [0], [True]) == "survivorship_filter_confirmed"
    )
    assert (
        audit._fixture_verdict([True], [0], [False]) == "not_confirmed"
    )  # 对照缺席=方法瑕疵


def test_fixtures_row_contract(audit):
    assert len(audit.FIXTURES) >= 3
    for row in audit.FIXTURES:
        assert set(row) >= {"code", "name", "delist", "probes", "evidence", "control"}
        assert len(row["code"]) >= 8 and "." in row["code"]
        assert row["probes"], "每个 fixture 至少一个探针日"
        for p in row["probes"]:
            assert len(p) == 8 and p.isdigit()


# ── 代码转换 / parquet 工具 ──────────────────────────────────────────


def test_to_qlib_symbol(audit):
    assert audit._to_qlib_symbol("600401.SH") == "sh600401"
    assert audit._to_qlib_symbol("000029.SZ") == "sz000029"
    assert audit._to_qlib_symbol("920000.BJ") == "bj920000"
    assert audit._to_qlib_symbol("SH600036") == "sh600036"


def test_parquet_nrows_and_symbol_diff(audit, tmp_path):
    pa = tmp_path / "a.parquet"
    pb = tmp_path / "b.parquet"
    pd.DataFrame({"symbol": ["A", "B", "C"]}).to_parquet(pa)
    pd.DataFrame({"symbol": ["B", "C"]}).to_parquet(pb)
    assert audit._parquet_nrows(pa) == 3
    assert audit._symbol_diff(pa, pb) == {"A"}
    assert audit._symbol_diff(pb, pa) == set()


def test_instrument_ranges(audit, tmp_path):
    f = tmp_path / "all.txt"
    f.write_text(
        "sh600000\t2016-01-04\t2026-10-09\nsz000029\t2016-01-04\t2020-11-06\n",
        encoding="utf-8",
    )
    ranges = audit._load_instrument_ranges(f)
    assert ranges["sz000029"] == ("2016-01-04", "2020-11-06")
    assert audit._instrument_covers(ranges["sz000029"], "2019-06-03")
    assert not audit._instrument_covers(ranges["sz000029"], "2021-01-04")
    assert not audit._instrument_covers(
        ("2016-01-04", ""), "2019-01-01"
    )  # 空 end 不覆盖


# ── 渲染与退出码 ────────────────────────────────────────────────────


def test_render_md_tolerates_missing_sections(audit):
    md = audit._render_md({})
    assert isinstance(md, str) and "T-FE-08" in md


def test_render_md_contains_counts(audit):
    """真形状渲染：财务计数行 / 六载体退市矩阵 / 同基冻结与标签一致列。"""
    result = {
        "generated_at": "2026-10-11 12:00:00",
        "financial": {
            "dates": ["20260415"],
            "rtol": 1e-3,
            "n_financial_symbols": 5559,
            "n_loaded_symbols": 5559,
            "datasets": {
                "valuation": {
                    "20260415": {
                        "status": "ok",
                        "n_rows": 5300,
                        "fields": {
                            "net_profit_ttm": {
                                "n_total": 100,
                                "counts": {
                                    "match_pit1": 90,
                                    "match_naive_only": 3,
                                    "diverged": 2,
                                    "stored_nan": 1,
                                    "missing": 4,
                                },
                                "disc_naive_n": 10,
                                "leak_naive_only_in_disc": 3,
                                "leak_rate_in_disc": 0.3,
                                "disc_lag_n": 5,
                                "match_pit0_only_in_disc_lag": 1,
                            }
                        },
                        "pe_consistency": {
                            "status": "ok",
                            "n_checked": 100,
                            "match_rate": 1.0,
                        },
                    }
                }
            },
        },
        "delisted": {
            "verdict": "survivorship_filter_confirmed",
            "status": "known_open",
            "gone_counts": [{"from": "20180102", "to": "20190603", "gone": 0}],
            "fixtures": [
                {
                    "code": "600401.SH",
                    "name": "退市海润",
                    "delist": "2019-07-08",
                    "control_present_all_probes": True,
                    "qlib_range": ["2016-01-04", "2019-07-08"],
                    "probes": [
                        {
                            "probe": "20190603",
                            "absent_panel": True,
                            "presence": {
                                "daily_unadjusted": False,
                                "daily_forward": False,
                                "daily_backward": False,
                                "valuation": False,
                                "financial_file": False,
                                "qlib_instrument": False,
                            },
                        }
                    ],
                }
            ],
        },
        "suspension": {
            "datasets": {
                "features_daily": {
                    "phantom_rows": 12345,
                    "phantom_symbols": 42,
                    "top_spans": [
                        {
                            "symbol": "000029.SZ",
                            "start": "20160914",
                            "end": "20201106",
                            "n_days": 940,
                            "freeze": {
                                "status": "ok",
                                "prev_panel_date": "20160913",
                                "prev_panel_close": 11.17,
                                "phantom_close": 11.17,
                                "is_frozen": True,
                                "raw_prev_close_aux": 9.11,
                            },
                            "resume": {
                                "status": "ok",
                                "resume_date": "20201109",
                                "panel_resume_close": 12.26,
                                "panel_jump_vs_frozen": 0.0976,
                                "stored_label_col": "return_1d",
                                "stored_label": 9.76,
                                "label_vs_panel_jump_consistent": True,
                            },
                            "raw_evidence": {"absent_all_samples": False},
                            "qlib": {"status": "ok", "all_nan_in_span": True},
                        },
                        {
                            "symbol": "600001.SH",
                            "start": "20170101",
                            "end": "20180101",
                            "n_days": 3,
                            "freeze": {
                                "status": "ok",
                                "prev_panel_close": None,
                                "phantom_close": 20.0,
                                "is_frozen": None,
                            },
                            "raw_evidence": {},
                            "qlib": {},
                        },
                        {
                            "symbol": "600002.SH",
                            "start": "20170101",
                            "end": "20180101",
                            "n_days": 3,
                            "freeze": {"status": "prev_partition_missing"},
                            "raw_evidence": {},
                            "qlib": {},
                        },
                    ],
                }
            }
        },
    }
    md = audit._render_md(result)
    assert "20260415" in md
    assert "survivorship_filter_confirmed" in md
    assert "12345" in md
    assert "000029.SZ" in md
    # 财务计数行按真形状：判别 10 / 泄漏 3 / 泄漏率 0.3 / pit0_only 1
    assert "| 10 | **3** | 0.3 | 1 |" in md
    assert "| 100 查 | 1 一致 |" in md
    # 退市矩阵含六载体全列；已知 False 渲染缺席词（与未知 "?" 分列，见下测试）
    for col in ("raw 未复权", "前复权", "后复权", "valuation", "财报文件", "qlib 区间"):
        assert col in md
    assert "| **缺席** | **缺席** | **缺席** | **缺席** | **无** |" in md
    # 停牌行含同基冻结（面板基）+ 标签一致列
    assert "close 11.17→11.17 冻结" in md
    assert "return_1d=9.76" in md
    # 行尾三列：标签一致 ✓ / raw证据省略号态 — / qlib 全 NaN
    assert "| ✓ | — | 全 NaN（抽样，正确） |" in md
    # NaN 值域：无值可比（不是「非冻结」）；非 ok 状态照实显示
    assert "close —→20 无值可比" in md
    assert "prev_partition_missing" in md


def test_render_delisted_unknown_presence_question_mark(audit):
    """旧形状/缺键 presence 渲染 "?"（不可判定），不得冒充「缺席」肯定断言。"""
    dl = {
        "verdict": "survivorship_filter_confirmed",
        "status": "known_open",
        "fixtures": [
            {
                "code": "600401.SH",
                "name": "退市海润",
                "delist": "2019-07-08",
                "probes": [{"probe": "20190603", "presence": {}}],
            }
        ],
    }
    md = audit._render_md({"delisted": dl})
    assert "| ? | ? | ? | ? | ? |" in md
    for line in md.splitlines():
        if "600401.SH" in line:
            assert "缺席" not in line and "在场" not in line


def test_render_delisted_gone_lines_annotation(audit):
    dl = {
        "anchors": {
            "gone_counts": [
                {
                    "from": "20180102",
                    "to": "20190603",
                    "gone": 3,
                    "all_returned_by_latest": True,
                },
                {
                    "from": "20190603",
                    "to": "20210104",
                    "gone": 2,
                    "all_returned_by_latest": False,
                },
                {
                    "from": "20210104",
                    "to": "20240102",
                    "gone": 0,
                    "all_returned_by_latest": True,
                },
            ]
        }
    }
    md = audit._render_md({"delisted": dl})
    assert "= 3（全数回归=停牌非退市）" in md
    assert "存在不回归成员" in md
    assert "= 0（该区间无成员消失）" in md
    # gone=0 行不得携带「全数回归」注（空集 all() 恒真，纯机械注记、语义为空）
    for line in md.splitlines():
        if "symbols(" in line and "= 0" in line:
            assert "全数回归" not in line


def test_render_md_lists_all_critical_items(audit):
    datasets = {
        "valuation": {
            f"2026{i:04d}": {
                "fields": {"net_profit_ttm": {"leak_naive_only_in_disc": 1}}
            }
            for i in range(101, 126)  # 25 > 任何截断阈值
        }
    }
    result = {"financial": {"datasets": datasets}}
    crit = audit._critical_items(result)
    assert len(crit) == 25
    md = audit._render_md(result)
    for c in crit:
        assert f"  - {c}" in md


def test_critical_items(audit):
    clean = {
        "financial": {
            "datasets": {
                "valuation": {
                    "20260415": {
                        "status": "ok",
                        "fields": {
                            "net_profit_ttm": {"leak_naive_only_in_disc": 0},
                        },
                    }
                }
            }
        },
        "delisted": {"verdict": "not_confirmed"},
        "suspension": {"datasets": {"features_daily": {"phantom_rows": 0}}},
    }
    assert audit._critical_items(clean) == []
    dirty = {
        "financial": {
            "datasets": {
                "valuation": {
                    "20260415": {
                        "status": "ok",
                        "fields": {
                            "net_profit_ttm": {"leak_naive_only_in_disc": 2},
                        },
                    },
                }
            }
        },
        "delisted": {"verdict": "survivorship_filter_confirmed"},
        "suspension": {"datasets": {"features_daily": {"phantom_rows": 5}}},
    }
    items = audit._critical_items(dirty)
    assert len(items) == 3


# ── 评审处置回归（2026-10-11 HIGH-1/HIGH-2/守卫/状态机/相扫原语）────


def test_label_col_candidates_include_legacy_return_1d(audit):
    """HIGH-1：候选表必须覆盖旧代命名 ``return_1d``（≤20260918 分区唯一存在的
    标签列）——漏掉它复牌缝检查在全部历史数据上是死代码。"""
    assert "return_1d" in audit._LABEL_COL_CANDIDATES
    assert "future_return_1d" in audit._LABEL_COL_CANDIDATES


def test_symbol_diff_null_guard(audit, tmp_path):
    """MEDIUM-2：守卫真正要挡的是**子查询侧** NULL——``x NOT IN (B, NULL)``
    恒 NULL 会把真实差集吞成空集；NULL 只在外层时裸 ``NOT IN`` 同样返回差集
    （旧写法断言恒过=假通过，评审复核实测）。两侧都放 NULL 钉住。"""
    pa = tmp_path / "a.parquet"
    pb = tmp_path / "b.parquet"
    pd.DataFrame({"symbol": ["A", "B", None]}).to_parquet(pa)
    pd.DataFrame({"symbol": ["B", None]}).to_parquet(pb)
    assert audit._symbol_diff(pa, pb) == {"A"}


def test_root_guard_rejects_wrong_root(audit, tmp_path):
    """MEDIUM-1：错根必须拒绝出报告（CLI 退 2），不得零样本静默出「净报告」。"""
    err = audit._root_guard(tmp_path)
    assert err is not None and "daily_unadjusted" in err
    (tmp_path / "1_kline_data" / "daily_unadjusted").mkdir(parents=True)
    assert audit._root_guard(tmp_path) is None


def test_delisted_status_follows_verdict(audit):
    """MEDIUM-3：status 随判定走，不硬编码 known_open。"""
    assert (
        audit._delisted_status("survivorship_filter_confirmed", [True]) == "known_open"
    )
    assert audit._delisted_status("not_confirmed", [True, True]) == "resolved"
    assert audit._delisted_status("not_confirmed", [True, False]) == "inconclusive"
    assert audit._delisted_status("not_confirmed", []) == "inconclusive"


_SYNTH_DAYS = ("20260105", "20260106", "20260107", "20260108", "20260109")
# AAA.SZ 在 raw 的停牌日 = 0107/0108（幻影两天），0109 复牌
_SYNTH_RAW_PRESENCE = {
    "20260105": True,
    "20260106": True,
    "20260107": False,
    "20260108": False,
    "20260109": True,
}


def _write_partition(base: Path, dt: str, df: pd.DataFrame) -> None:
    d = base / f"dt={dt}"
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / "data.parquet")


def _build_synth_tree(root: Path) -> Path:
    """合成 quantdb 树：面板基 = raw 基 ×4/3（复权差），幻影行 close 与面板前值
    严格冻结（20.0）；复牌日面板 22.0（跳空 +10%），旧代 return_1d 标签 10.0（%）。
    raw 前值 15.0 / 复牌 16.0 与面板不同基——旧实现跨基比 raw 会判 not-frozen。
    末日在 raw 另放两只出货集没有的 symbol（DDD.SZ / 600000.SH）→ 反向覆盖差
    与 prefix_counts 键序钉住（须 sorted，不得随 set 迭代序漂移）。"""
    raw = root / "1_kline_data" / "daily_unadjusted"
    ds = root / "6_ml_datasets" / "features_daily"
    for d in _SYNTH_DAYS:
        raw_rows = [{"symbol": "BBB.SZ", "close": 3.0}]
        if _SYNTH_RAW_PRESENCE[d]:
            raw_rows.append(
                {"symbol": "AAA.SZ", "close": 16.0 if d == "20260109" else 15.0}
            )
        if d == "20260109":
            raw_rows += [
                {"symbol": "DDD.SZ", "close": 9.0},
                {"symbol": "600000.SH", "close": 10.0},
            ]
        _write_partition(raw, d, pd.DataFrame(raw_rows))
        ds_rows = [
            {"symbol": "BBB.SZ", "close": 4.0, "return_1d": np.nan},
            {
                "symbol": "AAA.SZ",
                "close": 22.0 if d == "20260109" else 20.0,
                "return_1d": 10.0 if d == "20260108" else np.nan,
            },
        ]
        _write_partition(ds, d, pd.DataFrame(ds_rows))
    return raw


def test_enrich_span_same_basis_freeze_and_resume_label(audit, tmp_path):
    """HIGH-2 钉住：冻结/跳空一律在出货集自身复权基内判定。

    面板前值 20.0 与幻影行 20.0 相等（真冻结）但 raw 前值 15.0 不等——按面板基
    判 frozen=True；复牌跳空 = 22/20−1 = 10%，与 return_1d=10.0（%）一致；
    raw 两个 close 只进旁证列。"""
    raw = _build_synth_tree(tmp_path)
    cal = audit._partition_dates(raw)
    span = {"symbol": "AAA.SZ", "start": "20260107", "end": "20260108", "n_days": 2}
    e = audit._enrich_span(tmp_path, "features_daily", span, cal, raw, None, {})
    fz = e["freeze"]
    assert fz["status"] == "ok" and fz["is_frozen"] is True
    assert fz["prev_panel_close"] == pytest.approx(20.0)
    assert fz["phantom_close"] == pytest.approx(20.0)
    assert fz["raw_prev_close_aux"] == pytest.approx(15.0)  # 旁证列，基不同
    rz = e["resume"]
    assert rz["resume_date"] == "20260109"
    assert rz["seamless_with_span_end"] is True
    assert rz["panel_jump_vs_frozen"] == pytest.approx(0.1)
    assert rz["raw_resume_close_aux"] == pytest.approx(16.0)
    assert rz["stored_label_col"] == "return_1d"
    assert rz["stored_label"] == pytest.approx(10.0)
    assert rz["stored_label_units"] == "percent"
    assert rz["label_vs_panel_jump_consistent"] is True


def test_enrich_span_value_missing_and_partition_missing_states(audit, tmp_path):
    """评审 LOW-1/2/4 钉住：NaN 判「无值可比」（is_frozen=None）而非「非冻结」；
    前一分区文件缺失 = prev_partition_missing 而非 calendar_start；qlib 无日历时
    calendar_missing 不冒充「全 NaN」。"""
    raw = tmp_path / "1_kline_data" / "daily_unadjusted"
    ds = tmp_path / "6_ml_datasets" / "features_daily"
    for dt, ccc, raw_c in (
        ("20260105", 19.0, 14.0),
        ("20260106", float("nan"), 14.5),
        ("20260107", 20.0, 15.0),
    ):
        _write_partition(ds, dt, pd.DataFrame([{"symbol": "CCC.SZ", "close": ccc}]))
        _write_partition(raw, dt, pd.DataFrame([{"symbol": "CCC.SZ", "close": raw_c}]))
    cal = audit._partition_dates(raw)
    span = {"symbol": "CCC.SZ", "start": "20260107", "end": "20260107", "n_days": 1}

    # ① 面板前值 NaN：ok 状态但判词是 None（不可判定），raw 旁证仍照记
    e = audit._enrich_span(tmp_path, "features_daily", span, cal, raw, None, {})
    fz = e["freeze"]
    assert fz["status"] == "ok"
    assert fz["is_frozen"] is None
    assert fz["prev_panel_close"] is None  # NaN → _f 归一为 None
    assert fz["raw_prev_close_aux"] == pytest.approx(14.5)
    # ② 前一交易日分区文件缺失：不得伪装成 calendar_start
    (ds / "dt=20260106" / "data.parquet").unlink()
    e2 = audit._enrich_span(tmp_path, "features_daily", span, cal, raw, None, {})
    assert e2["freeze"]["status"] == "prev_partition_missing"
    # ③ qlib 日历缺失 vs 无 qlib vs bin 缺失，三态不得混同
    e3 = audit._enrich_span(tmp_path, "features_daily", span, cal, raw, tmp_path, {})
    assert e3["qlib"]["status"] == "calendar_missing"
    e4 = audit._enrich_span(tmp_path, "features_daily", span, cal, raw, None, {})
    assert e4["qlib"]["status"] == "qlib_unavailable"
    e5 = audit._enrich_span(
        tmp_path, "features_daily", span, cal, raw, tmp_path, {"2026-01-07": 0}
    )
    assert e5["qlib"]["status"] == "bin_missing"
    assert e5["qlib"]["all_nan_in_span"] is False  # bin 不在 ≠ 全 NaN


def test_audit_suspension_partition_scan_synthetic(audit, tmp_path):
    """MEDIUM-4：相扫原语端到端合成——分区 anti-join → 幻影对 → 跨度 → 富化。"""
    _build_synth_tree(tmp_path)
    sus = audit.audit_suspension(
        tmp_path, datasets=("features_daily",), top_k=5, qlib_dir=None
    )
    entry = sus["datasets"]["features_daily"]
    assert entry["n_partitions_scanned"] == len(_SYNTH_DAYS)
    assert entry["phantom_rows"] == 2  # AAA.SZ × {0107, 0108}
    assert entry["phantom_symbols"] == 1
    assert entry["phantom_first_date"] == "20260107"
    assert entry["phantom_last_date"] == "20260108"
    assert entry["phantom_by_partition"] == {"20260107": 1, "20260108": 1}
    assert entry["missing_rows"] == 2  # DDD.SZ / 600000.SH 仅 raw 有（末日）
    assert entry["missing_symbols"] == 2
    assert entry["missing_rows_bse_92"] == 0
    mlp = entry["missing_last_partition"]
    assert mlp["date"] == "20260109"
    assert mlp["prefix_counts"] == {"60": 1, "DD": 1}
    # 键序须 sorted，不得随 set 迭代序（PYTHONHASHSEED）漂移（评审 LOW）
    assert list(mlp["prefix_counts"]) == ["60", "DD"]
    assert [s["symbol"] for s in entry["top_spans"]] == ["AAA.SZ"]
    top = entry["top_spans"][0]
    assert (top["start"], top["end"], top["n_days"]) == ("20260107", "20260108", 2)
    assert top["freeze"]["is_frozen"] is True
    assert top["resume"]["panel_jump_vs_frozen"] == pytest.approx(0.1)
