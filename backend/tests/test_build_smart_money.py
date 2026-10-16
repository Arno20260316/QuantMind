"""聪明钱产物构建单测（tmp parquet 全链，不触网）。

钉住的行为：
- 端到端：事件池日逐一出分（scores/dt=*）、latest.json 排序/分值/covered/
  pct 缺失为 null、前缀式代码、5 日展示窗金额、北向 delta_pp、latest_event
- 入柱裁定：evidence.json.admitted 优先于 signals.ADMITTED_PILLARS；
  未知柱/双空 → BuildRefused 且不落盘
- 窗口：--rebuild-days N 只重写最近 N 个交易日分区；重复构建幂等
- sync 失败/表陈旧 → 仍构建但 meta.partial=true
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.scripts.market_analysis import build_smart_money as bsm
from backend.shared.smart_money import datasets, signals

CAL = pd.bdate_range("2024-09-02", periods=10)  # 2024-09-02 ~ 09-13，无 CN 节假日
D0 = CAL[0]
D5, D6, DLAST = CAL[5], CAL[6], CAL[9]
A, B, C = "600036.SH", "000001.SZ", "601318.SH"
#: 入柱（名, 方向±1）；端到端金样取全正向，方向翻转由专门测试钉住
ADMITTED3 = (("inst_net_ratio_5d", 1), ("lhb_net_ratio_5d", 1), ("survey_inst_90d", 1))
ADMITTED3_NAMES = [n for n, _ in ADMITTED3]


class _Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.raw = tmp_path / "smart_money"
        self.product = tmp_path / "product"
        self.quantdb = tmp_path / "quantdb"
        self.raw.mkdir()
        self.product.mkdir()


@pytest.fixture
def env(tmp_path, monkeypatch) -> _Env:
    e = _Env(tmp_path)
    datasets._cache.clear()
    monkeypatch.setattr(datasets, "raw_dir", lambda: e.raw)
    monkeypatch.setattr(datasets, "product_dir", lambda: e.product)
    monkeypatch.setattr(
        datasets, "resolve_quantdb_subdir", lambda *p: e.quantdb.joinpath(*p)
    )
    monkeypatch.setattr(signals, "ADMITTED_PILLARS", ADMITTED3)

    # ---- 行情（daily_forward 分区：目录名=日历 + close 面板供 analytics）
    # A 每日 +1（100→109），B/C 恒 100——战绩/事件研究/席位全链需要收盘面板
    for i, d in enumerate(CAL):
        p = e.quantdb / "1_kline_data" / "daily_forward" / f"dt={d:%Y%m%d}"
        p.mkdir(parents=True)
        pd.DataFrame(
            [
                {"symbol": A, "time": d, "close": 100.0 + i},
                {"symbol": B, "time": d, "close": 100.0},
                {"symbol": C, "time": d, "close": 100.0},
            ]
        ).to_parquet(p / "data.parquet")

    # ---- 证券主表
    inst_dir = e.quantdb / "2_base_sector" / "instrument_detail"
    inst_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"Symbol": A, "Name": "测试甲", "rs_hyname": "银行"},
            {"Symbol": B, "Name": "测试乙", "rs_hyname": "银行"},
            {"Symbol": C, "Name": "测试丙", "rs_hyname": "保险"},
        ]
    ).to_parquet(inst_dir / "instrument_detail.parquet")

    # ---- 北向两季（delta = 0.04−0.03 → delta_pp=1.0）
    for q, pct in (("2026Q2", 0.03), ("2026Q3", 0.04)):
        d = e.quantdb / "2_base_sector" / "hsgt_north" / f"quarter={q}"
        d.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "symbol": A,
                    "stock_name": "测试甲",
                    "holding_quantity": 1,
                    "holding_percentage": pct,
                    "report_date": "2026-09-30",
                }
            ]
        ).to_parquet(d / "data.parquet")

    # ---- 原始层（A 事件在 D0 与末日；B/C 事件在 D5/D6——末日池 = A/B/C 三只）
    pd.DataFrame(
        [
            {
                "trade_date": D0,
                "symbol": A,
                "reason": "x",
                "lhb_net_buy": 1e7,
                "market_amount": 5e8,
            },
            {
                "trade_date": DLAST,
                "symbol": A,
                "reason": "x",
                "lhb_net_buy": 2e7,
                "market_amount": 4e8,
            },
        ]
    ).to_parquet(e.raw / "lhb_detail.parquet")
    pd.DataFrame(
        [
            {
                "trade_date": D0,
                "symbol": A,
                "reason": "x",
                "inst_net_buy": 6e7,
                "market_amount": 6e8,
            },
            {
                "trade_date": DLAST,
                "symbol": A,
                "reason": "x",
                "inst_net_buy": 3e7,
                "market_amount": 3e8,
            },
        ]
    ).to_parquet(e.raw / "lhb_inst.parquet")
    pd.DataFrame(
        [
            {
                "trade_date": D5,
                "branch_name": "某营业部",
                "net_amount": 9e7,
                "buy_amount": 1e8,
                "sell_amount": 1e7,
                "stocks_bought": "测试丙",
            }
        ]
    ).to_parquet(e.raw / "lhb_branch.parquet")
    pd.DataFrame(
        [
            {
                "trade_date": D5,
                "symbol": C,
                "premium": 0.05,
                "amount": 1e8,
                "amount_ratio": 0.02,
            }
        ]
    ).to_parquet(e.raw / "block_trade.parquet")
    pd.DataFrame(
        [
            {
                "trade_date": D5,
                "symbol": C,
                "inst_count": 10,
                "reception_type": "电话会议",
            },
            {
                "trade_date": D6,
                "symbol": B,
                "inst_count": 20,
                "reception_type": "实地调研",
            },
        ]
    ).to_parquet(e.raw / "survey.parquet")

    # ---- 水位元数据（新鲜 → partial=False）
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    meta = {n: {"last_date": today, "rows": 1} for n in datasets.RAW_DATASETS}
    (e.raw / "_sync_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return e


class TestBuildEndToEnd:
    def test_产物端到端(self, env):
        # Act
        meta = bsm.build(rebuild_days=0, run_sync=None)

        # Assert —— 分区：池非空日逐一落盘（A 窗 5 日 ∪ C 窗 5 日 ∪ B 窗 → 全历 10 日）
        dates = datasets.score_dates()
        assert len(dates) == len(CAL)
        assert dates[0] == f"{D0:%Y%m%d}"
        assert dates[-1] == f"{DLAST:%Y%m%d}"

        latest = datasets.load_product_json("latest")
        assert latest["as_of"] == f"{DLAST:%Y-%m-%d}"
        assert latest["admitted"] == ADMITTED3_NAMES
        assert latest["partial"] is False
        assert [p["name"] for p in latest["pillars"]] == ADMITTED3_NAMES
        assert all(p["direction"] == 1 for p in latest["pillars"])

        items = latest["items"]
        assert [it["symbol"] for it in items] == ["SH600036", "SZ000001", "SH601318"]
        a_item, b_item, c_item = items
        # A：机构比值·龙虎榜比值均榜内第一(100)，调研缺 → 填 50：(100+100+50)/3
        assert a_item["score"] == pytest.approx(250 / 3, abs=0.01)
        assert a_item["covered"] == 2
        assert a_item["pct"]["survey_inst_90d"] is None
        assert a_item["val"]["inst_net_ratio_5d"] == pytest.approx(3e7 / 3e8)
        assert a_item["name"] == "测试甲" and a_item["industry"] == "银行"
        assert a_item["inst_net_5d"] == pytest.approx(3e7)  # 5 日窗只含末日
        assert a_item["lhb_net_5d"] == pytest.approx(2e7)
        assert a_item["margin_net_5d"] is None  # 无两融数据 → null 不补 0
        assert a_item["north_q_delta_pp"] == pytest.approx(1.0)
        assert a_item["latest_event"] == {"type": "lhb", "date": f"{DLAST:%Y-%m-%d}"}
        # B：调研 100；两事件柱缺 → 50：(50+50+100)/3
        assert b_item["score"] == pytest.approx(200 / 3, abs=0.01)
        assert b_item["covered"] == 1
        # C：调研 10 < B 的 20 → pct 50：(50+50+50)/3
        assert c_item["score"] == pytest.approx(50.0)

        assert latest["summary"]["pool_size"] == 3
        assert latest["summary"]["covered_mean"] == pytest.approx(4 / 3, abs=0.01)

        # overview：龙虎榜/机构末日值 + 北向摘要 + 营业部
        ov = latest["overview"]
        assert ov["lhb"]["date"] == f"{DLAST:%Y-%m-%d}"
        assert ov["lhb"]["n_stocks"] == 1
        assert ov["inst"]["net_buy_yi"] == pytest.approx(0.3)
        assert ov["dzjy"]["amount_yi"] == pytest.approx(1.0)  # 1e8 元 → 亿元
        assert ov["north"]["n_inc"] == 1 and ov["north"]["quarter"] == "2026Q3"
        assert ov["branches_top"][0]["branch_name"] == "某营业部"
        # 两融无数据 → margin 段全 null（缺失绝不补 0）
        assert ov["margin"]["balance_yi"] is None

        # trends：日历长度条数（键=原始统计列名）；两融列全 null
        trends = datasets.load_product_json("trends")
        assert len(trends["lhb"]) == len(CAL)
        assert trends["lhb"][-1]["lhb_n_stocks"] == 1
        assert all(r["margin_net_5d_yi"] is None for r in trends["margin"])

        # meta
        m = datasets.load_product_json("meta")
        assert m["admitted"] == ADMITTED3_NAMES
        assert m["score_dates_total"] == len(dates)
        assert meta["sync_ok"] is True

        # ---- analytics 产物（细案 §4）：三段 JSON + overview.industry + meta.analytics
        tr = datasets.load_product_json("track_record")
        assert tr["top_n"] == 10 and len(tr["daily"]) == len(CAL)
        assert tr["rolling_ic"]["h"] == 10 and tr["rolling_ic"]["window"] == 60
        # 最近案例 D0：A 榜前二；T+1 入场（101）、T+2 出场（102）→ ret_2 = 102/101−1
        item = tr["recent"][0]
        # 产物符号统一前缀式（与 items/前端契约一致；A 为数据层后缀式输入）
        assert item["symbol"] == "SH600036" and item["score"] == pytest.approx(250 / 3, abs=0.01)
        assert item["ret_2"] == pytest.approx(102 / 101 - 1, abs=1e-4)
        assert item["status"] == "持有中"  # pos+10 > last_pos → 未闭合
        assert item["ret_so_far"] == pytest.approx(109 / 101 - 1, abs=1e-4)

        es = datasets.load_product_json("event_study")
        assert es["car_days"] == 20
        assert set(es["families"]) == {"龙虎榜", "机构席位", "大宗交易", "机构调研"}
        assert len(es["families"]["大宗交易"]["car_q5_bp"]) == 20

        st = datasets.load_product_json("seats")
        assert st["horizon"] == 5 and st["n_events"] == 0  # 无席位表 → 空榜不炸

        row0 = ov["industry"][0]
        assert row0["industry"] == "银行"
        assert row0["lhb_net_yi"] == pytest.approx(0.3)
        assert row0["avg_score"] == pytest.approx(75.0)  # (250/3 + 200/3)/2
        assert m["analytics"]["scores_days"] == len(CAL)
        assert m["analytics"]["seat_coverage"] == {
            "rows": 0,
            "start": None,
            "end": None,
            "partial": True,
        }

    def test_重复构建幂等(self, env):
        bsm.build(rebuild_days=0, run_sync=None)
        first = datasets.load_scores(f"{D6:%Y%m%d}")
        bsm.build(rebuild_days=0, run_sync=None)
        second = datasets.load_scores(f"{D6:%Y%m%d}")
        pd.testing.assert_frame_equal(first, second)

    def test_rebuild_days只重写近N日(self, env):
        meta = bsm.build(rebuild_days=1, run_sync=None)
        assert meta["score_dates_written"] == 1
        assert datasets.score_dates() == [f"{DLAST:%Y%m%d}"]


class TestAdmittedGate:
    def test_evidence优先于常量_含方向(self, env):
        (env.product / "evidence.json").write_text(
            json.dumps(
                {
                    "admitted": ["survey_inst_90d"],
                    "signals": [{"name": "survey_inst_90d", "direction": -1}],
                }
            ),
            encoding="utf-8",
        )
        assert bsm.resolve_admitted() == (("survey_inst_90d", -1),)

    def test_evidence缺方向拒绝(self, env):
        # 方向决定分位是否取反——缺失绝不静默默认 +1
        (env.product / "evidence.json").write_text(
            json.dumps({"admitted": ["survey_inst_90d"], "signals": []}),
            encoding="utf-8",
        )
        with pytest.raises(bsm.BuildRefused, match="direction"):
            bsm.resolve_admitted()

    def test_未知柱拒绝(self, env):
        (env.product / "evidence.json").write_text(
            json.dumps({"admitted": ["no_such_pillar"]}), encoding="utf-8"
        )
        with pytest.raises(bsm.BuildRefused, match="未知柱"):
            bsm.resolve_admitted()

    def test_双空拒绝出分且不落盘(self, env, monkeypatch):
        monkeypatch.setattr(signals, "ADMITTED_PILLARS", ())
        with pytest.raises(bsm.BuildRefused):
            bsm.build(rebuild_days=0, run_sync=None)
        assert datasets.score_dates() == []
        assert datasets.load_product_json("latest") is None


class TestDirectionAlignment:
    def test_负向柱分位取反贯通产物(self, env):
        # lhb 柱方向 = −1：A 在 lhb 柱榜内第一（raw pct=100）→ 对齐 0；
        # A 得分 (inst 100 + lhb 0 + survey 缺 50)/3 = 50
        (env.product / "evidence.json").write_text(
            json.dumps(
                {
                    "admitted": ADMITTED3_NAMES,
                    "signals": [
                        {"name": "inst_net_ratio_5d", "direction": 1},
                        {"name": "lhb_net_ratio_5d", "direction": -1},
                        {"name": "survey_inst_90d", "direction": 1},
                    ],
                }
            ),
            encoding="utf-8",
        )

        bsm.build(rebuild_days=0, run_sync=None)

        latest = datasets.load_product_json("latest")
        a = next(it for it in latest["items"] if it["symbol"] == "SH600036")
        assert a["pct"]["lhb_net_ratio_5d"] == pytest.approx(0.0)
        assert a["val"]["lhb_net_ratio_5d"] == pytest.approx(2e7 / 4e8)  # 原值保留
        assert a["score"] == pytest.approx(150 / 3, abs=0.01)
        by_name = {p["name"]: p["direction"] for p in latest["pillars"]}
        assert by_name["lhb_net_ratio_5d"] == -1


class TestPartial:
    def test_sync失败仍构建并置partial(self, env):
        meta = bsm.build(rebuild_days=0, run_sync=lambda: False)
        assert meta["sync_ok"] is False and meta["partial"] is True
        assert datasets.load_product_json("latest")["partial"] is True  # 产物照常

    def test_sync抛异常也被隔离(self, env):
        def boom() -> bool:
            raise RuntimeError("upstream down")

        meta = bsm.build(rebuild_days=0, run_sync=boom)
        assert meta["partial"] is True

    def test_表陈旧置partial(self, env):
        meta = {
            n: {"last_date": "2024-01-01", "rows": 1} for n in datasets.RAW_DATASETS
        }
        (env.raw / "_sync_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        datasets._cache.clear()
        m = bsm.build(rebuild_days=0, run_sync=None)
        assert m["partial"] is True
