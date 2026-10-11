"""因子概率板 P3 · 决策轮只读叠加（``factor_board.consume``）单测。

口径（细案 §十一）：只标注 + 稳定重排（共振靠前前置、共振靠后后置）；不新增行、
不改 score/rank、不并名单；开关默认关（关态与 ``load_pool=load_pool_doc`` 直接
接线等价）；板缺失/叠加链异常 → 原 doc（增益组件永不拦决策轮）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.trade.services import decision_round_io as RIO
from backend.shared import decision_context_source as DCS
from backend.shared.decision.context import DirectionBlock, PoolRow
from backend.shared.decision_context_source import PoolDoc
from backend.shared.factor_board import consume as CONSUME
from backend.shared.factor_board import sectors as S

FLAG = CONSUME.OVERLAY_FLAG


# ── 构造 ─────────────────────────────────────────────────────────────


def _stock(sym: str, quadrant: str) -> dict:
    return {
        "symbol": sym,
        "name": sym,
        "composite": 0.1,
        "model_rank": 0.9,
        "quadrant": quadrant,
        "sector_l2": None,
    }


def _board(decision_date: str = "2026-10-12") -> dict:
    return {
        "schema_version": "factor_board/1.1",
        "meta": {"decision_date": decision_date},
        "stocks": {
            "top": [_stock("600000.SH", "HM")],
            "bottom": [_stock("000002.SZ", "ML")],
            "resonant_long": [_stock("600036.SH", "HH"), _stock("600519.SH", "HH")],
            "resonant_short": [_stock("000001.SZ", "LL")],
        },
    }


def _write_board(root: Path, board: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if board is not None:
        (root / "latest.json").write_text(
            json.dumps(board, ensure_ascii=False), encoding="utf-8"
        )
    return root


def _rows() -> tuple[PoolRow, ...]:
    """文件序 = A(名单外) B(共振空) C(共振多) D(中性) —— 排序后应为 C A D B。"""
    return (
        PoolRow(code="300001.SZ", name="A", score=1.0, fusion=0.5, rank=1),
        PoolRow(
            code="000001.SZ", name="B", score=2.0, fusion=0.6, rank=2, remark="旧注"
        ),
        PoolRow(code="600036.SH", name="C", score=3.0, fusion=0.7, rank=3),
        PoolRow(code="600000.SH", name="D", score=4.0, fusion=0.8, rank=4),
    )


def _pooldoc(rows: tuple[PoolRow, ...] | None = None) -> PoolDoc:
    return PoolDoc(
        rows=rows if rows is not None else _rows(),
        direction=DirectionBlock(),
        missing_columns=(),
        source="test",
    )


# ── load_overlay ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_overlay_四名单建索引_共振判定优先(tmp_path):
    # Arrange：600036.SH 同时出现在 resonant_long 与 top —— 共振判定必须赢
    board = _board()
    board["stocks"]["top"].append(_stock("600036.SH", "HH"))
    board_dir = _write_board(tmp_path / "board", board)

    # Act
    ov = CONSUME.load_overlay(board_dir, as_of="20261013")

    # Assert
    assert ov is not None
    assert set(ov.entries) == {
        "600000.SH",
        "000002.SZ",
        "600036.SH",
        "600519.SH",
        "000001.SZ",
    }
    assert ov.entries["600036.SH"].verdict == S.VERDICT_LONG
    assert ov.entries["000001.SZ"].verdict == S.VERDICT_SHORT
    assert ov.entries["600000.SH"].verdict == S.VERDICT_NEUTRAL
    assert ov.entries["600036.SH"].quadrant == "HH"
    assert ov.entries["600036.SH"].composite == 0.1
    assert ov.entries["600036.SH"].model_rank == 0.9
    assert ov.decision_date == "2026-10-12"
    assert ov.stale_days == 1  # 板决策日 10-12 → 池日 10-13，正常时点差
    assert ov.note == ""


@pytest.mark.unit
def test_load_overlay_缺板与坏JSON_返回None(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert CONSUME.load_overlay(empty) is None
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "latest.json").write_text("{oops", encoding="utf-8")
    assert CONSUME.load_overlay(bad) is None


@pytest.mark.unit
def test_load_overlay_陈旧注记_阈值边界与缺省(tmp_path):
    board_dir = _write_board(tmp_path / "board", _board("2026-10-12"))

    # 恰好 3 天：不注记（谓词 > OVERLAY_STALE_DAYS）
    ov3 = CONSUME.load_overlay(board_dir, as_of="20261015")
    assert ov3.stale_days == CONSUME.OVERLAY_STALE_DAYS
    assert ov3.note == ""

    # 4 天：注记带出处
    ov4 = CONSUME.load_overlay(board_dir, as_of="20261016")
    assert ov4.stale_days == CONSUME.OVERLAY_STALE_DAYS + 1
    assert "板陈旧" in ov4.note and "2026-10-12" in ov4.note

    # 板批目标日晚于池日（负时点差）：如实记录，不当陈旧告警
    ov_neg = CONSUME.load_overlay(board_dir, as_of="20261010")
    assert ov_neg.stale_days == -2
    assert ov_neg.note == ""

    # 没给池日：拿不到 stale_days，也不注记
    ov_na = CONSUME.load_overlay(board_dir)
    assert ov_na.stale_days is None
    assert ov_na.note == ""


# ── annotate_pool（纯函数） ──────────────────────────────────────────


@pytest.mark.unit
def test_annotate_pool_标注与稳定排序_不改分数名次(tmp_path):
    # Arrange
    ov = CONSUME.load_overlay(_write_board(tmp_path / "board", _board()))
    rows = _rows()

    # Act
    out = CONSUME.annotate_pool(rows, ov)

    # Assert：共振多前置 → 中性/名单外按文件序 → 共振空后置
    assert [r.code for r in out] == ["600036.SH", "300001.SZ", "600000.SH", "000001.SZ"]
    assert out[0].remark == "共振·靠前"
    assert out[1].remark == ""  # 名单外：原样
    assert out[2].remark == ""  # top 名单内但非共振象限：不打标签
    assert out[3].remark == "旧注｜共振·靠后"  # 原备注追加
    # 不新增行、不改 code/score/fusion/rank（只有 remark 变）
    assert {r.code for r in out} == {r.code for r in rows}
    c = out[0]
    assert (c.name, c.score, c.fusion, c.rank) == ("C", 3.0, 0.7, 3)


@pytest.mark.unit
def test_annotate_pool_overlay缺失或空行_原样同序():
    rows = _rows()
    assert CONSUME.annotate_pool(rows, None) == rows
    assert CONSUME.annotate_pool((), None) == ()


# ── 包装层 _load_pool_with_overlay ───────────────────────────────────


@pytest.mark.unit
def test_包装_关态原对象直通_与直接接线等价(monkeypatch):
    doc = _pooldoc()
    calls: list[str] = []
    monkeypatch.setattr(DCS, "load_pool_doc", lambda day: calls.append(day) or doc)
    monkeypatch.delenv(FLAG, raising=False)

    got = RIO._load_pool_with_overlay("20261013")

    assert got is doc
    assert calls == ["20261013"]

    # 词表纪律：只有小写 true 判开（'1' 不算开）
    monkeypatch.setenv(FLAG, "1")
    assert RIO._load_pool_with_overlay("20261013") is doc


@pytest.mark.unit
def test_包装_开态_真池文件真板_两代schema都标注(tmp_path, monkeypatch):
    board_dir = _write_board(tmp_path / "board", _board())
    reports = tmp_path / "reports"
    (reports / "stock_picks").mkdir(parents=True)
    # pick_candidates 代：candidates/symbol（裸码）
    (reports / "stock_picks" / "20261013_picks.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {"symbol": "300001", "name": "A"},
                    {"symbol": "600036", "name": "C"},
                    {"symbol": "000001", "name": "B"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QM_REPORTS_DIR", str(reports))
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(board_dir))
    monkeypatch.setenv(FLAG, "true")

    got = RIO._load_pool_with_overlay("20261013")

    assert got is not None
    assert [r.code for r in got.rows] == ["600036.SH", "300001.SZ", "000001.SZ"]
    assert got.rows[0].remark == "共振·靠前"
    assert got.rows[2].remark == "共振·靠后"
    # 包装只换 rows：其余字段原样（方向块与池文件解析结果保持同一对象值）
    assert isinstance(got, PoolDoc)
    assert got.direction == _pooldoc().direction


@pytest.mark.unit
def test_包装_开态_agent_picks代也标注(tmp_path, monkeypatch):
    board_dir = _write_board(tmp_path / "board", _board())
    reports = tmp_path / "reports"
    (reports / "stock_picks").mkdir(parents=True)
    (reports / "stock_picks" / "20261013_agent_picks.json").write_text(
        json.dumps(
            {"picks": [{"code": "600036.SH", "name": "C", "reason": "动量"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QM_REPORTS_DIR", str(reports))
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(board_dir))
    monkeypatch.setenv(FLAG, "true")

    got = RIO._load_pool_with_overlay("20261013")

    assert got is not None
    assert got.rows[0].remark == "动量｜共振·靠前"


@pytest.mark.unit
def test_包装_开态_板缺失或异常_原doc(tmp_path, monkeypatch):
    doc = _pooldoc()
    monkeypatch.setattr(DCS, "load_pool_doc", lambda day: doc)
    monkeypatch.setenv(FLAG, "true")

    # 板缺失（空目录）→ 原 doc
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(empty))
    assert RIO._load_pool_with_overlay("20261013") is doc

    # 叠加链异常 → 原 doc
    def _boom(**kwargs):
        raise RuntimeError("板读炸了")

    monkeypatch.setattr(CONSUME, "load_overlay", _boom)
    assert RIO._load_pool_with_overlay("20261013") is doc


@pytest.mark.unit
def test_包装_池缺失_返回None且不读板(monkeypatch):
    monkeypatch.setattr(DCS, "load_pool_doc", lambda day: None)
    monkeypatch.setenv(FLAG, "true")
    called: list[int] = []
    monkeypatch.setattr(
        CONSUME, "load_overlay", lambda **kwargs: called.append(1) or None
    )

    assert RIO._load_pool_with_overlay("20261013") is None
    assert called == []


@pytest.mark.unit
def test_包装_开态_陈旧板打注记(tmp_path, monkeypatch, caplog):
    board_dir = _write_board(tmp_path / "board", _board("2026-10-01"))
    reports = tmp_path / "reports"
    (reports / "stock_picks").mkdir(parents=True)
    (reports / "stock_picks" / "20261013_picks.json").write_text(
        json.dumps({"candidates": [{"symbol": "600036", "name": "C"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("QM_REPORTS_DIR", str(reports))
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(board_dir))
    monkeypatch.setenv(FLAG, "true")

    with caplog.at_level("WARNING"):
        got = RIO._load_pool_with_overlay("20261013")

    assert got is not None
    assert got.rows[0].remark == "共振·靠前"  # 陈旧不放弃标注
    assert any("板叠加注记" in rec.getMessage() for rec in caplog.records)


# ── 集成：盘外一轮 run_once · 真 prompt 两态对照（细案 §十一） ─────────


@pytest.mark.asyncio
async def test_集成_盘外一轮_prompt两态对照_共振标签与重排(tmp_path, monkeypatch):
    """盘外触发一轮（编排真、写入全替身）→ 实拦一条腿 + prompt 含共振标签。

    编排全替身（与 ``test_decision_round.make_harness`` 同一矩阵），**只把
    ``load_pool`` 换成被测量的真包装**：真池文件 + 真板产物走真
    ``render_prompt``；``is_trading_time=否`` ⇒ in_session=False ⇒
    ``refusing_submitter``（细案：一条腿不发，不触碰真实交易时段）。
    """
    from datetime import date

    from backend.services.trade.services.decision_round import run_once
    from backend.services.trade.services.decision_round_core import (
        SLOTS_BY_HHMM,
        STATUS_OK,
    )
    from backend.tests.test_decision_round import NOW, SNAPS, make_harness

    slot_0935 = SLOTS_BY_HHMM["0935"]
    # 池行三只要过 l1/l2 闸（缺现价 → l2.pool_row_invalid 被剔）：给可得价
    snaps = {
        **SNAPS,
        "300333.SZ": {"Now": 10.0, "PreClose": 9.8, "timestamp": NOW.timestamp()},
        "600111.SH": {"Now": 11.0, "PreClose": 10.8, "timestamp": NOW.timestamp()},
        "300222.SZ": {"Now": 12.0, "PreClose": 11.8, "timestamp": NOW.timestamp()},
    }

    # Arrange：板（共振多=600111.SH、共振空=300222.SZ）；池文件序 = B(平) P(多) S(空)
    board = {
        "schema_version": "factor_board/1.1",
        "meta": {"decision_date": "2026-10-12"},
        "stocks": {
            "top": [],
            "bottom": [],
            "resonant_long": [_stock("600111.SH", "HH")],
            "resonant_short": [_stock("300222.SZ", "LL")],
        },
    }
    board_dir = _write_board(tmp_path / "board", board)
    reports = tmp_path / "reports"
    (reports / "stock_picks").mkdir(parents=True)
    (reports / "stock_picks" / "20261013_picks.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {"symbol": "300333", "name": "B", "score": 1.0},
                    {"symbol": "600111", "name": "P", "score": 2.0},
                    {"symbol": "300222", "name": "S", "score": 3.0},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QM_REPORTS_DIR", str(reports))
    monkeypatch.setenv("QM_FACTOR_BOARD_DIR", str(board_dir))

    async def _run(flag_value: str | None, tag: str) -> tuple[str, dict, list]:
        if flag_value is None:
            monkeypatch.delenv(FLAG, raising=False)
        else:
            monkeypatch.setenv(FLAG, flag_value)
        calls: list[str] = []

        def _recording_loader(day: str):
            calls.append(day)
            return RIO._load_pool_with_overlay(day)  # 被测量的真包装

        h = make_harness(
            load_pool=_recording_loader,
            snaps=snaps,
            is_trading_time=lambda now: False,  # 盘外：refusing_submitter
        )
        result = await run_once(slot_0935, deps=h.deps, now=NOW, day=date(2026, 10, 13))
        assert result.status == STATUS_OK, f"{tag}: {result.note}"
        # 盘外实拦：执行段拿到的必须是拒发器（一条腿不发，口径见 refusing_submitter）
        assert h.log["exec"][0]["submitter"] is not None
        return h.log["llm"][0]["prompt"], h.log["ledger"][0][0].context_meta, calls

    # Act：同一轮编排，开关两态各跑一遍
    prompt_off, meta_off, pool_calls = await _run(None, "关态")
    prompt_on, meta_on, _ = await _run("true", "开态")

    # Assert 池日：盘外一轮读的仍是「当日」池（%Y%m%d）
    assert pool_calls == ["20261013"]
    # 关态：标签不出现、文件序、上下文 meta 与开态同形（只有池行内容变）
    assert "共振·靠前" not in prompt_off and "共振·靠后" not in prompt_off
    assert prompt_off.index("300333.SZ") < prompt_off.index("600111.SH")
    assert meta_off["pool"]["shown"] == meta_on["pool"]["shown"] == 3
    assert meta_off["round"]["in_session"] is False
    assert meta_on["round"]["in_session"] is False
    # 开态：标签上行列、共振靠前前置、共振靠后后置、其余保序；不新增行
    assert "共振·靠前" in prompt_on and "共振·靠后" in prompt_on
    assert prompt_on.index("600111.SH") < prompt_on.index("300333.SZ")
    assert prompt_on.index("300333.SZ") < prompt_on.index("300222.SZ")
    for sym in ("300333.SZ", "600111.SH", "300222.SZ"):
        assert sym in prompt_on
    assert meta_on["pool"]["shown"] == 3  # 行数不变
