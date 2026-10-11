"""挖掘配置（自定义挖掘时间 / 检验区间 / 数据集选择）纯函数契约测试。

契约（移植 P1）：
1. **区间解析**：仅收 ``YYYY-MM-DD``；非法格式、非法日历日一律 ValueError
   且消息带中文字段名（用户在 UI 上要能看懂拦的是哪个区间）。
2. **区间顺序**：start ≤ end；检验区间**不得与挖掘区间重叠**（起点须
   ≥ 挖掘终点）——样本外检验与挖掘段重叠即泄漏，必须显式拒绝。
3. **数据覆盖**：给了覆盖区间（QuantDB 交易日历 min/max）时越界即拒；
   没给覆盖（数据源不可用）不拦——宁可放行由引擎侧失败，不做假校验。
4. **默认窗口**：锚定覆盖末端（无覆盖锚今天）前推 5 年 = 前 4 年挖掘 +
   后 1 年检验（参考实现口径）；覆盖期不足 5 年时整体前移仍保持
   4+1 比例，且不得早于覆盖起点。
5. **qlib 分段**：有检验区间 → test = 检验区间；无 → 把挖掘区间按
   70% / 15% / 15% 切 train/valid/test（cut 点用**交易日数近似不可得**，
   按自然日切分并在文档中言明）。data_handler 起止 = 全数据面。
6. **任务环境变量**：只发 ``QM_*`` 前缀（分段结果、窗口、数据集），
   旧 ``QLIB_FACTOR_*`` 同步注入保持链路一致；不产生空值键。
7. **数据集注册表**：空/None = 全选（现状默认）；未登记 key ValueError
   且消息列出可选项；去重且按**登记序**归一（缓存身份稳定）；``l2_factors``
   为动态列集（映射 None）。
8. **env 读取端**（``window_from_env`` / ``datasets_from_env``）：与写入端
   ``task_env`` 同键往返；未设 = None/全选（默认路径零变化）；半截配置即拒。
9. **挖掘频率**（P1.5）：``day``（缺省）/ ``1min``（分钟原生挖掘，仅 A 股）；
   未传 freq = 不发 ``QM_MINING_FREQ`` 键（默认路径零变化）。

TDD：先红后绿。本文件只测纯函数——不碰 DB / 不碰子进程 / 不写生产状态。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import mining_config as MC  # noqa: E402
from backend.services.engine.rd_agent import mining_datasets as MD  # noqa: E402


# ── 1. 区间解析 ──────────────────────────────────────────────────────


def test_parse_window_accepts_iso_dates():
    w = MC.parse_window("2021-01-04", "2024-12-31", label="挖掘数据区间")
    assert w.start == date(2021, 1, 4)
    assert w.end == date(2024, 12, 31)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2021/01/04", "2024-12-31"),
        ("20210104", "2024-12-31"),
        ("2021-13-01", "2024-12-31"),
        ("", "2024-12-31"),
        ("2021-01-04", ""),
    ],
)
def test_parse_window_rejects_bad_input(start, end):
    with pytest.raises(ValueError) as exc:
        MC.parse_window(start, end, label="挖掘数据区间")
    assert "挖掘数据区间" in str(exc.value)


def test_parse_window_rejects_reversed_range():
    with pytest.raises(ValueError):
        MC.parse_window("2024-01-01", "2023-01-01", label="挖掘数据区间")


def test_parse_optional_window_all_empty_is_none():
    assert MC.parse_optional_window("", "", label="因子检验区间") is None
    assert MC.parse_optional_window(None, None, label="因子检验区间") is None


def test_parse_optional_window_half_empty_rejected():
    with pytest.raises(ValueError) as exc:
        MC.parse_optional_window("2024-01-01", "", label="因子检验区间")
    assert "因子检验区间" in str(exc.value)


# ── 2/3. 顺序、非重叠与覆盖校验 ─────────────────────────────────────


def _w(s: str, e: str) -> MC.Window:
    return MC.parse_window(s, e, label="测试区间")


def test_validation_must_not_overlap_mining():
    mining = _w("2020-01-01", "2023-12-31")
    with pytest.raises(ValueError) as exc:
        MC.validate_windows(mining, _w("2023-01-01", "2024-12-31"))
    assert "检验" in str(exc.value)


def test_validation_start_equal_mining_end_allowed():
    """边界：检验起点 == 挖掘终点放行（与 参考实现同口径，两端均含）。"""
    MC.validate_windows(_w("2020-01-01", "2023-12-31"), _w("2023-12-31", "2024-12-31"))


def test_window_outside_coverage_rejected():
    cov = _w("2016-01-04", "2026-10-09")
    with pytest.raises(ValueError) as exc:
        MC.validate_coverage(_w("2015-01-01", "2020-01-01"), cov, label="挖掘数据区间")
    assert "覆盖" in str(exc.value)


def test_window_within_coverage_ok():
    cov = _w("2016-01-04", "2026-10-09")
    MC.validate_coverage(_w("2016-01-04", "2026-10-09"), cov, label="挖掘数据区间")


def test_no_coverage_skips_check():
    MC.validate_coverage(_w("2000-01-01", "2030-01-01"), None, label="挖掘数据区间")


# ── 4. 默认窗口 ─────────────────────────────────────────────────────


def test_default_windows_anchor_to_coverage_end():
    cov = _w("2016-01-04", "2026-10-09")
    mining, valid = MC.default_windows(cov, today=date(2026, 10, 10))
    # 5 年跨度：2021-10-09 ~ 2026-10-09；前 4 年挖掘、后 1 年检验
    assert mining.start == date(2021, 10, 9)
    assert mining.end == date(2025, 10, 9)
    # 检验自挖掘终点**次日**起算：两窗不共享边界日（旧实现 split 日双算，
    # 那天前向收益在样本内外各被用一次）
    assert valid.start == date(2025, 10, 10)
    assert valid.end == date(2026, 10, 9)
    assert valid.start > mining.end


def test_default_windows_fall_back_to_today_without_coverage():
    mining, valid = MC.default_windows(None, today=date(2026, 10, 10))
    assert valid.end == date(2026, 10, 10)
    assert mining.start == date(2021, 10, 10)
    assert valid.start == date(2025, 10, 11), "检验起点 = 挖掘终点次日，不共享边界日"


def test_default_windows_clamp_to_coverage_start():
    """覆盖期不足 5 年：整体前移，挖掘起点不得早于覆盖起点。"""
    cov = _w("2016-01-04", "2018-06-30")
    mining, valid = MC.default_windows(cov, today=date(2026, 10, 10))
    assert mining.start == date(2016, 1, 4)
    assert valid.end == date(2018, 6, 30)
    assert mining.end <= valid.end  # 可用跨度不足 4 年时挖掘终点收敛到覆盖末端
    # split 顶到覆盖末端 → 检验窗退化为单日窗，且这**恰是挖掘窗的最后一天**
    # （共享末日：没有非重叠检验窗可放，如实保留旧行为；validate_windows 只按
    # valid.start >= mining.end 放行，documented 于 default_windows docstring）
    assert mining.end == date(2018, 6, 30)
    assert valid.start == valid.end == date(2018, 6, 30)
    MC.validate_windows(mining, valid)


# ── 4b. 回测末端保护（qlib 需要 t+1 次日行情） ───────────────────────


def _cov(s: str, e: str) -> MC.Window:
    return _w(s, e)


def test_clamp_to_data_end_only_moves_end():
    w = _w("2021-01-01", "2026-10-09")
    assert MC.clamp_to_data_end(w, date(2026, 10, 8)) == _w("2021-01-01", "2026-10-08")
    # 已在保护范围内 / 无保护值 → 原样返回（同一个对象，不做无谓重建）
    assert MC.clamp_to_data_end(w, date(2026, 10, 9)) is w
    assert MC.clamp_to_data_end(w, None) is w


def test_resolve_windows_clamps_both_windows_to_backtest_guard():
    cov = _cov("2016-01-04", "2026-10-09")

    # 挖掘窗终点压线（无检验窗）→ 退让一天
    mining, valid = MC.resolve_windows("2021-01-01", "2026-10-09", "", "", coverage=cov, data_end=date(2026, 10, 8))
    assert mining.iso() == ("2021-01-01", "2026-10-08") and valid is None

    # 检验窗终点压线 → 同样退让；非重叠约束（起点 ≥ 挖掘终点）照旧成立
    mining, valid = MC.resolve_windows(
        "2021-01-01", "2025-12-31", "2026-01-01", "2026-10-09", coverage=cov, data_end=date(2026, 10, 8)
    )
    assert mining.iso() == ("2021-01-01", "2025-12-31")
    assert valid.iso() == ("2026-01-01", "2026-10-08")
    MC.validate_windows(mining, valid)


def test_resolve_windows_coverage_check_uses_raw_input_not_clamped():
    """终点写超覆盖照旧报错——末端保护不得把越界输入"修好"成合法。"""
    with pytest.raises(ValueError) as exc:
        MC.resolve_windows(
            "2021-01-01",
            "2030-01-01",
            "",
            "",
            coverage=_cov("2016-01-04", "2026-10-09"),
            data_end=date(2026, 10, 8),
        )
    assert "超出数据覆盖范围" in str(exc.value)


def test_resolve_windows_rejects_window_entirely_beyond_guard():
    """起点都晚于保护末端 → 显式报错（消息给出可用的最晚终点），不静默截空。

    两个入口都要拦：挖掘窗、检验窗（后者是"用户只选了末端那一天做样本外检验"
    的常见写法）。
    """
    with pytest.raises(ValueError) as exc:
        MC.resolve_windows("2026-10-09", "2026-10-09", "", "", data_end=date(2026, 10, 8))
    assert "回测可用末端" in str(exc.value) and "2026-10-08" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        MC.resolve_windows(
            "2021-01-01",
            "2026-10-09",
            "2026-10-09",
            "2026-10-09",
            data_end=date(2026, 10, 8),
        )
    assert "因子检验区间" in str(exc.value)


def test_default_windows_anchor_backtest_guard():
    """默认窗锚保护末端：预填的默认值本身就是能跑完的窗口。"""
    mining, valid = MC.default_windows(
        _cov("2016-01-04", "2026-10-09"), data_end=date(2026, 10, 8)
    )
    assert valid.end == date(2026, 10, 8)
    # 4+1 年切分不受锚点前移影响；检验起点仍是挖掘终点次日
    assert mining.end == date(2025, 10, 8)
    assert valid.start == mining.end + timedelta(days=1)


# ── 5. qlib 分段 ────────────────────────────────────────────────────


def test_segments_with_validation_use_it_as_test():
    mining = _w("2020-01-01", "2023-12-31")
    valid = _w("2024-01-01", "2024-12-31")
    seg = MC.qlib_segments(mining, valid)
    assert seg["test"] == ("2024-01-01", "2024-12-31")
    # train/valid 覆盖挖掘段，且无缺口
    assert seg["train"][0] == "2020-01-01"
    assert seg["valid"][1] == "2023-12-31"
    assert seg["train"][1] >= seg["valid"][0]


def test_segments_without_validation_split_mining():
    mining = _w("2020-01-01", "2024-12-31")  # 1826 天
    seg = MC.qlib_segments(mining, None)
    assert seg["train"][0] == "2020-01-01"
    assert seg["test"][1] == "2024-12-31"
    # 三段单调不重叠
    assert seg["train"][1] <= seg["valid"][0]
    assert seg["valid"][1] <= seg["test"][0]


def test_handler_span_covers_validation_end():
    mining = _w("2020-01-01", "2023-12-31")
    valid = _w("2024-01-01", "2025-06-30")
    span = MC.handler_span(mining, valid)
    assert span == ("2020-01-01", "2025-06-30")


# ── 6. 任务环境变量 ─────────────────────────────────────────────────


def test_task_env_emits_windows_and_segments():
    mining = _w("2020-01-01", "2023-12-31")
    valid = _w("2024-01-01", "2024-12-31")
    env = MC.task_env(mining, valid, datasets=("features_daily", "l1_factors"))
    assert env["QM_MINING_WINDOW_START"] == "2020-01-01"
    assert env["QM_MINING_WINDOW_END"] == "2023-12-31"
    assert env["QM_VALIDATION_WINDOW_START"] == "2024-01-01"
    assert env["QM_VALIDATION_WINDOW_END"] == "2024-12-31"
    assert env["QM_MINING_DATASETS"] == "features_daily,l1_factors"
    assert env["QM_QLIB_TEST_START"] == "2024-01-01"
    # 旧链路变量同步注入（防止任何下游读取方拿到不一致窗口）
    assert env["QLIB_FACTOR_TRAIN_START"] == "2020-01-01"
    assert env["QLIB_FACTOR_TEST_END"] == "2024-12-31"
    assert all(v for v in env.values())


def test_task_env_without_validation_omits_validation_keys():
    env = MC.task_env(
        _w("2020-01-01", "2024-12-31"), None, datasets=MD.SUPPORTED_DATASETS
    )
    assert "QM_VALIDATION_WINDOW_START" not in env
    assert env["QM_MINING_DATASETS"] == "features_daily,l1_factors,l2_factors"


def test_task_env_clamps_backtest_tail_with_validation():
    """env 里写出的端点 = qrun 实际消费的端点：检验段终点退让一个交易日。

    不退让就是"默认窗口必然跑挂"：默认/前端预填的终点锚在数据末端，qlib 回测
    取下一根 bar 的时间步（t+1 成交）时 IndexError（2026-10-10 实证）。
    """
    env = MC.task_env(
        _w("2021-10-09", "2025-10-09"),
        _w("2025-10-09", "2026-10-09"),
        datasets=MD.SUPPORTED_DATASETS,
        data_end=date(2026, 10, 8),
    )
    assert env["QM_VALIDATION_WINDOW_END"] == "2026-10-08"
    assert env["QM_QLIB_TEST_START"] == "2025-10-09"
    assert env["QM_QLIB_TEST_END"] == "2026-10-08"
    assert env["QLIB_FACTOR_TEST_END"] == "2026-10-08"
    # 挖掘段终点（"最后一根 bar"）同样不落在回测尾巴上
    assert env["QM_MINING_WINDOW_END"] == "2025-10-09"


def test_task_env_clamps_mining_end_when_no_validation():
    """无检验窗：test 段 = 挖掘段末 15% → 挖掘终点也必须退让（否则同样崩）。"""
    env = MC.task_env(
        _w("2020-01-01", "2026-10-09"),
        None,
        datasets=MD.SUPPORTED_DATASETS,
        data_end=date(2026, 10, 8),
    )
    assert env["QM_MINING_WINDOW_END"] == "2026-10-08"
    assert env["QM_QLIB_TEST_END"] == "2026-10-08"
    assert env["QLIB_FACTOR_TEST_END"] == "2026-10-08"
    assert env["QM_QLIB_TEST_START"] <= "2026-10-08"


def test_task_env_rejects_window_beyond_guard():
    with pytest.raises(ValueError) as exc:
        MC.task_env(
            _w("2026-10-09", "2026-10-09"),
            None,
            datasets=MD.SUPPORTED_DATASETS,
            data_end=date(2026, 10, 8),
        )
    assert "回测可用末端" in str(exc.value)


# ── 7. 数据集注册表 ─────────────────────────────────────────────────


def test_supported_datasets_are_stable_and_labelled():
    assert MD.SUPPORTED_DATASETS == ("features_daily", "l1_factors", "l2_factors")
    for key in MD.SUPPORTED_DATASETS:
        assert MD.DATASET_LABELS[key]


def test_resolve_datasets_empty_means_all():
    assert MD.resolve_datasets(None) == MD.SUPPORTED_DATASETS
    assert MD.resolve_datasets([]) == MD.SUPPORTED_DATASETS


def test_resolve_datasets_dedupes_in_registry_order():
    """同集合不同提交序必须归一为同一元组——h5 缓存按数据集分文件，
    顺序不定会让同一份选择命中不到同一份缓存。"""
    assert MD.resolve_datasets(["l2_factors", "features_daily", "l2_factors"]) == (
        "features_daily",
        "l2_factors",
    )


def test_resolve_datasets_ignores_blank_entries():
    assert MD.resolve_datasets(["", "  ", "l1_factors"]) == ("l1_factors",)


def test_resolve_datasets_rejects_unknown_with_hint():
    with pytest.raises(ValueError) as exc:
        MD.resolve_datasets(["features_daily", "no_such_dataset"])
    msg = str(exc.value)
    assert "no_such_dataset" in msg
    assert "features_daily" in msg


def test_datasets_from_env_default_and_parse():
    assert MD.datasets_from_env({}) == MD.SUPPORTED_DATASETS
    assert MD.datasets_from_env({"QM_MINING_DATASETS": "  "}) == MD.SUPPORTED_DATASETS
    assert MD.datasets_from_env({"QM_MINING_DATASETS": "l2_factors, features_daily"}) == (
        "features_daily",
        "l2_factors",
    )
    with pytest.raises(ValueError):
        MD.datasets_from_env({"QM_MINING_DATASETS": "bogus"})


def test_dataset_short_names_cover_registry():
    for key in MD.SUPPORTED_DATASETS:
        assert MD.DATASET_SHORT_NAMES[key]


# ── 8. 窗口环境变量读取（写入端 task_env 的镜像） ────────────────────


def test_window_from_env_absent_returns_none():
    assert MC.window_from_env({}) is None
    assert MC.window_from_env({"QM_VALIDATION_WINDOW_START": "2024-01-01"}) is None


def test_window_from_env_roundtrips_task_env():
    mining, valid = _w("2020-01-01", "2023-12-31"), _w("2024-01-01", "2024-12-31")
    env = MC.task_env(mining, valid, datasets=MD.SUPPORTED_DATASETS)
    parsed = MC.window_from_env(env)
    assert parsed == (mining, valid)


def test_window_from_env_rejects_half_and_reversed():
    with pytest.raises(ValueError) as exc:
        MC.window_from_env({"QM_MINING_WINDOW_START": "2020-01-01"})
    assert "挖掘数据区间" in str(exc.value)
    with pytest.raises(ValueError):
        MC.window_from_env(
            {
                "QM_MINING_WINDOW_START": "2020-01-01",
                "QM_MINING_WINDOW_END": "2023-12-31",
                "QM_VALIDATION_WINDOW_START": "2023-01-01",
                "QM_VALIDATION_WINDOW_END": "2024-01-01",
            }
        )


def test_enrich_mapping_matches_wrapper_whitelist():
    """富化列映射与 rd_loop_wrapper 现行白名单必须同源（防提示与数据漂移）。"""
    from backend.services.engine.rd_agent import rd_loop_wrapper as W

    assert MD.FEATURES_DAILY == W._ENRICH_FEATURES_DAILY
    assert MD.L1_FACTORS == W._ENRICH_L1
    assert MD.ENRICH_BY_DATASET["features_daily"] is MD.FEATURES_DAILY
    assert MD.ENRICH_BY_DATASET["l1_factors"] is MD.L1_FACTORS
    assert MD.ENRICH_BY_DATASET["l2_factors"] is None  # 动态列集，现场读盘


# ── 9. 挖掘频率（day / 1min，移植 P1.5） ──────────────────


def test_normalize_freq_defaults_to_day():
    assert MC.normalize_freq(None) == "day"
    assert MC.normalize_freq("") == "day"
    assert MC.normalize_freq("  ") == "day"
    assert MC.normalize_freq("DAY") == "day"
    assert MC.normalize_freq("1min") == "1min"
    assert MC.normalize_freq("1MIN") == "1min"


def test_normalize_freq_rejects_unknown_value():
    with pytest.raises(ValueError) as exc:
        MC.normalize_freq("5min")
    assert "5min" in str(exc.value) and "day" in str(exc.value)


def test_normalize_freq_minute_only_for_a_share():
    assert MC.normalize_freq("1min", market="a_share") == "1min"
    assert MC.normalize_freq("1min", market="A_SHARE") == "1min"
    assert MC.normalize_freq("day", market="crypto") == "day"  # 日线不受限
    with pytest.raises(ValueError) as exc:
        MC.normalize_freq("1min", market="crypto")
    assert "crypto" in str(exc.value)


def test_freq_from_env_reads_task_env_roundtrip():
    mining, valid = _w("2020-01-01", "2023-12-31"), _w("2024-01-01", "2024-12-31")
    env = MC.task_env(mining, valid, datasets=MD.SUPPORTED_DATASETS, freq="1min")
    assert env["QM_MINING_FREQ"] == "1min"
    assert MC.freq_from_env(env) == "1min"


def test_task_env_omits_freq_key_when_not_given():
    """默认路径零变化：不传 freq 就不发键（下游按 day 处理）。"""
    mining, valid = _w("2020-01-01", "2023-12-31"), _w("2024-01-01", "2024-12-31")
    env = MC.task_env(mining, valid, datasets=MD.SUPPORTED_DATASETS)
    assert "QM_MINING_FREQ" not in env
    assert MC.freq_from_env(env) == "day"


def test_task_env_explicit_day_freq_is_emitted():
    mining, valid = _w("2020-01-01", "2023-12-31"), _w("2024-01-01", "2024-12-31")
    env = MC.task_env(mining, valid, datasets=MD.SUPPORTED_DATASETS, freq="day")
    assert env["QM_MINING_FREQ"] == "day"
