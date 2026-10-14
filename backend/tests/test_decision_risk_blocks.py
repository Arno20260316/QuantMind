"""``decision_risk_blocks``（Stage 2 第二批）单测：风险三文件 → 两槽提示词文本。

口径纪律（模块 docstring）：未接线/文件缺失 → 空串（宁整段不出，不拿半截数据
吓模型）；宇宙一条未命中 → 空串（历史字节冻结）；行数封顶、理由截断；港美代码
不归一为裸 6 位 → 不进块。
"""

from __future__ import annotations

from datetime import datetime

from backend.shared.decision_risk_blocks import (
    INDUSTRY_MAX_LINES,
    RISK_MAX_LINES,
    build_industry_caution_block,
    build_risk_block,
)

TODAY = datetime(2026, 9, 24, 9, 40)
NAMES = {"600036.SH": "招商银行", "601988.SH": "中国银行", "600519.SH": "贵州茅台"}


# ── build_risk_block ─────────────────────────────────────────────────
def test_risk_block_degrades_to_empty_when_unwired_or_missing_or_codeless():
    risk = {"asof": "2026-09-24", "items": {"600036": {"reason": "立案调查"}}}
    # 未接线（旧替身）：哪怕数据齐全也不出块
    assert build_risk_block(risk, None, ["600036.SH"], [], wired=False) == ""
    # 两个文件都缺席
    assert build_risk_block(None, None, ["600036.SH"], []) == ""
    # 宇宙两侧都空：没有可命中的标的
    assert build_risk_block(risk, None, [], []) == ""


def test_risk_block_renders_four_sections_in_order_with_side_tags():
    risk = {
        "asof": "2026-09-24",
        "items": {"600036": {"reason": "立案调查", "expire": "2026-10-01"}},
        "watch": {"601988": {"reason": "监管关注函", "until": "2026-09-30"}},
        "warns": {"600519": "高质押率 61%"},
    }
    fund = {
        "asof": "2026-09-24",
        "items": {"600036": {"flags": ["fin", "trend"], "reason": "两季扣非为负"}},
    }
    block = build_risk_block(
        risk, fund, ["600036.SH"], ["601988.SH", "600519.SH"], NAMES, now=TODAY
    )
    assert block.splitlines() == [
        "【事件风险警示（系统风险清单，硬约束）】（清单日期 2026-09-24）",
        "- 600036.SH 招商银行：立案调查（有效期至 2026-10-01）",  # items：持仓侧，无 tag
        "- 601988.SH 中国银行：监管关注函（观察至 2026-09-30）（候选池命中，本轮勿买）",
        "- 600519.SH 贵州茅台：高质押率 61%（候选池命中，本轮勿买）",  # warns
        "- 600036.SH 招商银行：基本面红旗（财务/趋势）：两季扣非为负",
    ]


def test_risk_block_fund_only_still_renders_and_carries_fund_asof():
    fund = {
        "asof": "2026-09-23",
        "items": {"601988": {"flags": ["未知tag"], "reason": "商誉占净资产 45%"}},
    }
    block = build_risk_block(None, fund, ["601988.SH"], [], NAMES, now=TODAY)
    assert block.splitlines() == [
        "【事件风险警示（系统风险清单，硬约束）】（清单日期 2026-09-23，非当日）",
        "- 601988.SH 中国银行：基本面红旗（未知tag）：商誉占净资产 45%",
    ]
    # now 缺省时不判陈旧（调用方没给现在，就不许替它判「非当日」）
    block = build_risk_block(None, fund, ["601988.SH"], [], NAMES)
    assert "非当日" not in block


def test_risk_block_truncates_reasons_at_eighty_chars():
    long_reason = "连" * 120
    risk = {"asof": "2026-09-24", "warns": {"600036": long_reason}}
    block = build_risk_block(risk, None, ["600036.SH"], [], NAMES, now=TODAY)
    body = block.splitlines()[1]
    assert body == f"- 600036.SH 招商银行：{'连' * 80}…"


def test_risk_block_caps_lines_and_counts_the_overflow():
    codes = [f"60000{i}.SH" for i in range(RISK_MAX_LINES + 2)]
    risk = {
        "asof": "2026-09-24",
        "warns": {f"60000{i}": "高质押" for i in range(RISK_MAX_LINES + 2)},
    }
    block = build_risk_block(risk, None, codes, [], now=TODAY)
    lines = block.splitlines()
    assert len(lines) == RISK_MAX_LINES + 2  # 头 + 8 条 + 溢出行
    assert lines[-1] == "- （另有 2 条命中未列，按名单执行）"
    assert all(line.startswith("- 60000") for line in lines[1 : RISK_MAX_LINES + 1])


def test_risk_block_normalizes_key_forms_skips_foreign_and_bad_values():
    risk = {"items": {"600036": "坏值（无细节也得出一行）"}}
    # 前缀式入参照常命中裸码键；港股不归一（.HK 剥离后非 6 位）→ 不进块；
    # names 缺席 → 行首只留代码（无尾随空格）
    block = build_risk_block(risk, None, ["SH600036"], ["00700.HK"], names=None)
    # 清单没给 asof → 头部不带日期（也不许编一个）
    assert block.splitlines() == [
        "【事件风险警示（系统风险清单，硬约束）】",
        "- SH600036：",
    ]
    assert "00700" not in block


# ── build_industry_caution_block ─────────────────────────────────────
def test_industry_block_only_lists_sectors_that_touch_the_universe():
    industry = {
        "asof": "2026-09-24",
        "warn_codes": {"600036": "银行", "000001": "银行", "600519": "白酒"},
        "items": [
            {"industry": "银行", "rate": 0.714, "n": 30, "total": 42},
            {"industry": "白酒", "rate": 0.5, "n": 10, "total": 20},
            {"industry": "地产", "rate": 0.9, "n": 9, "total": 10},  # 宇宙没碰到 → 不列
        ],
    }
    block = build_industry_caution_block(industry, ["600036.SH"], [])
    assert block.splitlines() == [
        "【行业整体劣化（赛道级软提示，不硬拦）】（清单日期 2026-09-24）",
        "- 银行：长期排除清单剔除率 71.4%（30/42 只）——赛道整体劣化，新开仓谨慎",
    ]
    # 候选一侧同样算「碰到」：池里 600519 命中白酒
    block = build_industry_caution_block(industry, [], ["600519.SH"])
    assert "白酒" in block and "银行" not in block


def test_industry_block_sorts_by_rate_desc_and_caps_the_list():
    sectors = ["银行", "白酒", "地产", "券商", "保险", "煤炭"]
    warn_codes = {f"60000{i}": sectors[i] for i in range(len(sectors))}
    # 顺序打乱传入，渲染必须按剔除率降序取前 5
    items = [
        {"industry": sectors[i], "rate": (i + 1) / 100, "n": i, "total": 10}
        for i in range(len(sectors))
    ][::-1]
    block = build_industry_caution_block(
        {"warn_codes": warn_codes, "items": items},
        [f"60000{i}.SH" for i in range(len(sectors))],
        [],
    )
    lines = block.splitlines()
    assert len(lines) == 1 + INDUSTRY_MAX_LINES
    assert lines[1].startswith("- 煤炭：")  # rate 0.06，最高
    assert lines[-1].startswith("- 白酒：")  # rate 0.02，第 5 高
    assert "银行" not in block  # rate 0.01 最低，被截断


def test_industry_block_degrades_to_empty_on_every_shortfall():
    industry = {
        "asof": "2026-09-24",
        "warn_codes": {"600036": "银行"},
        "items": [{"industry": "银行", "rate": 0.7, "n": 7, "total": 10}],
    }
    assert build_industry_caution_block(industry, ["600036.SH"], [], wired=False) == ""
    assert build_industry_caution_block(None, ["600036.SH"], []) == ""
    assert build_industry_caution_block(industry, [], []) == ""
    # 宇宙与劣化行业无交集
    assert build_industry_caution_block(industry, ["600519.SH"], []) == ""
    # 交集行业在 items 里没有行（清单只给了别家行业）
    no_row = {"warn_codes": {"600036": "银行"}, "items": [{"industry": "地产"}]}
    assert build_industry_caution_block(no_row, ["600036.SH"], []) == ""


def test_industry_block_unparseable_rate_renders_placeholder():
    industry = {
        "warn_codes": {"600036": "银行"},
        "items": [{"industry": "银行", "rate": None, "n": 30, "total": 42}],
    }
    block = build_industry_caution_block(industry, ["600036.SH"], [])
    assert "剔除率 —（30/42 只）" in block
