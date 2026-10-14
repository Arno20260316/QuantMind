"""信号分快照口径的单测（**唯一实现**共享模块）。

口径漂移的代价：自选页显示 +0.12、哨兵按 -0.03 报警 —— 用户没法信任任何一边。
所以这里盯死三件事：键形归一（裸 6 位 ↔ prefix）、实时/日频如实标注、
分数为 NULL 时**保留 None**（「没有分数」和「分数是 0」不是一回事）。
"""

from __future__ import annotations

from backend.shared.signal_scores import (
    A_SHARE_MARKET_SQL,
    MIN_SIGNAL_COVERAGE,
    SQL_LATEST_ANY_DATE,
    SQL_LATEST_COVERED_DATE,
    SQL_SCORES_BY_DATE,
    SYMBOL_FOLD_KEY,
    build_score_map,
    normalize_a_share_symbol,
    score_freq_of,
)


class TestNormalizeAShareSymbol:
    def test_bare_six_digits_gain_market_prefix(self):
        # engine_signal_scores.symbol 是裸 6 位；持仓/自选侧是 prefix
        assert normalize_a_share_symbol("600036") == "SH600036"
        assert normalize_a_share_symbol("000001") == "SZ000001"
        assert normalize_a_share_symbol("300750") == "SZ300750"
        assert normalize_a_share_symbol("430047") == "BJ430047"

    def test_suffix_form_converts_to_prefix(self):
        assert normalize_a_share_symbol("600036.SH") == "SH600036"

    def test_lowercase_prefix_upper_cases(self):
        assert normalize_a_share_symbol("sh600036") == "SH600036"

    def test_prefix_passes_through(self):
        assert normalize_a_share_symbol("SH600036") == "SH600036"

    def test_non_a_share_is_none(self):
        # 港股/美股/空值一律 None —— 调用方按「这行不参与」处理，不猜市场
        assert normalize_a_share_symbol("AAPL") is None
        assert normalize_a_share_symbol("00700.HK") is None
        assert normalize_a_share_symbol("") is None
        assert normalize_a_share_symbol(None) is None


class TestBuildScoreMap:
    def test_maps_rows_to_prefix_keyed_entries(self):
        rows = [("600036", 0.31, "BUY", "batch", "2026-09-19", 0.882)]
        score_map, realtime_rows = build_score_map(rows, "2026-09-19")

        assert score_map == {
            "SH600036": {
                "value": 0.31,
                "side": "BUY",
                "freq": "daily",
                "asOf": "2026-09-19",
                "rank_pct": 0.882,
            }
        }
        assert realtime_rows == 0

    def test_rank_pct_is_additive_with_none_for_old_rows(self):
        # rank_pct 是 additive 契约列：老行（4/5 列）与 NULL 值都 → 键在值为 None
        # （「没分位」与「分位是 0」不是一回事——0 是这批里垫底，None 是不知道），
        # 决策轮的「信号分」只看这一列，None 就不给后缀。
        rows = [
            ("600036", 0.31, "BUY", "batch", "2026-09-19"),  # 5 列老调用方
            ("000001", 0.1, "HOLD", "batch", "2026-09-19", None),  # 6 列 NULL
        ]
        score_map, _ = build_score_map(rows, "2026-09-19")

        assert score_map["SH600036"]["rank_pct"] is None
        assert score_map["SZ000001"]["rank_pct"] is None
        assert "rank_pct" in score_map["SH600036"]  # 键必须在，不能悄悄缺

    def test_realtime_source_is_flagged_and_counted(self):
        # source='realtime' 是盘中热集推理落库的行 —— 前端据此标「实时」，不然
        # 用户会把盘中分当昨天日频分看
        rows = [
            ("600036", 0.4, "BUY", "realtime", "2026-09-20"),
            ("000001", 0.1, "HOLD", "batch", "2026-09-20"),
        ]
        score_map, realtime_rows = build_score_map(rows, "2026-09-20")

        assert score_map["SH600036"]["freq"] == "realtime"
        assert score_map["SZ000001"]["freq"] == "daily"
        assert realtime_rows == 1

    def test_null_score_is_kept_as_none_not_zero(self):
        rows = [("600036", None, None, "batch", None)]
        score_map, _ = build_score_map(rows, "2026-09-19")

        assert score_map["SH600036"]["value"] is None
        assert score_map["SH600036"]["side"] is None

    def test_as_of_falls_back_to_trade_date_when_row_lacks_it(self):
        # 老调用方（不需要 asOf 的查询）只取 4 列，索引越界不能炸
        rows = [("600036", 0.2, "BUY", "batch")]
        score_map, _ = build_score_map(rows, "2026-09-19")

        assert score_map["SH600036"]["asOf"] == "2026-09-19"

    def test_non_a_share_and_blank_rows_are_skipped(self):
        rows = [
            ("00700.HK", 0.9, "BUY", "batch", "2026-09-19"),
            ("", 0.5, "BUY", "batch", "2026-09-19"),
        ]
        score_map, realtime_rows = build_score_map(rows, "2026-09-19")

        assert score_map == {}
        assert realtime_rows == 0

    def test_last_row_wins_for_duplicate_symbol(self):
        # SQL 侧 DISTINCT ON 已保证每标的仅一行；万一有人改成全量取，
        # 后出现（created_at 更新）的那条必须覆盖前一条，而不是被前一条挡住
        rows = [
            ("600036", 0.1, "BUY", "batch", "2026-09-19"),
            ("600036", -0.2, "SELL", "realtime", "2026-09-19"),
        ]
        score_map, realtime_rows = build_score_map(rows, "2026-09-19")

        assert score_map["SH600036"]["value"] == -0.2
        assert score_map["SH600036"]["side"] == "SELL"
        assert realtime_rows == 1


class TestScoreFreqOf:
    """分频标注的唯一判定：只有 ``source='realtime'`` 才敢说「实时」。

    宁可把实时行标成日频（欠标 → 用户当成降级路径保守看），也不能把批次日频
    标成实时 —— 后者会让用户拿隔夜分当盘中分下单。所以未知/空/大小写不符
    一律日频。
    """

    def test_realtime_source_is_realtime(self):
        assert score_freq_of("realtime") == "realtime"

    def test_batch_source_is_daily(self):
        assert score_freq_of("batch") == "daily"

    def test_unknown_or_blank_source_is_daily(self):
        for raw in (None, "", "  ", "Realtime", "REALTIME", "realtime_v2", 0):
            assert score_freq_of(raw) == "daily"


class TestSqlConstants:
    def test_market_predicate_tolerates_null_market(self):
        # 老库 market 列未回填；裸 `market = 'CN'` 会把 CN 行整片查空（静默）。
        # 且 `'CUSTOM'`（自定义市场，a_share 适配器 + SSE 日历 + SH000300 基准构建，
        # 落库 symbol 100% A 股形态）必须与 'CN' 同等对待 —— 2026-10-13 全市场带
        # BUY/SELL 的行**只**存在于 CUSTOM 桶，漏掉它候选信号列表整片为空。
        for sql in (SQL_LATEST_COVERED_DATE, SQL_LATEST_ANY_DATE, SQL_SCORES_BY_DATE):
            assert A_SHARE_MARKET_SQL in sql
            assert "'CUSTOM'" in sql

    def test_coverage_threshold_is_bound_not_inlined(self):
        assert ":min_cov" in SQL_LATEST_COVERED_DATE
        assert MIN_SIGNAL_COVERAGE == 1000

    def test_coverage_count_folds_symbol_identity(self):
        # T4-1 审计 M2：实时行=后缀身份（600036.SH）、批量行=裸 6 位——同一只股
        # 在 COUNT(DISTINCT symbol) 下算成两只，覆盖日闸门会被热集注水假过线。
        # 计数必须先折叠市场段再 DISTINCT。
        assert "COUNT(DISTINCT CASE" in SQL_LATEST_COVERED_DATE
        assert "[.](SH|SZ|BJ)$" in SQL_LATEST_COVERED_DATE
        assert "left(symbol, 6)" in SQL_LATEST_COVERED_DATE

    def test_scores_query_picks_latest_row_per_symbol(self):
        # 去重键必须折到裸六位（SYMBOL_FOLD_KEY），不能按字面 symbol：前缀形排序恒在
        # 数字形之后，Python 侧「后写覆盖」会等价于「前缀形恒胜」，与 created_at 无关。
        assert SYMBOL_FOLD_KEY in SQL_SCORES_BY_DATE
        assert "DISTINCT ON (symbol)" not in SQL_SCORES_BY_DATE
        assert "created_at DESC" in SQL_SCORES_BY_DATE

    def test_scores_query_carries_rank_pct(self):
        # rank_pct 是决策轮行情行内「信号分」的唯一原料（折算研究评分刻度）；
        # 少取这一列 = 提示词永远没有信号分，且 SQL 层无任何报错（静默空转）。
        assert "rank_pct" in SQL_SCORES_BY_DATE

    def test_scores_query_folds_prefix_form_too(self):
        # 前缀形（'SH600036'）也要折——它是自定义市场桶的唯一写法
        assert "substr(symbol, 3, 6)" in SYMBOL_FOLD_KEY
        assert "^(SH|SZ|BJ)[0-9]{6}$" in SYMBOL_FOLD_KEY
