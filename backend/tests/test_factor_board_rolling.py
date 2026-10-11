"""因子概率板 · 滚动重选单测（P2-f）：月份运算 / 台账幂等 / OOS 回填 / CLI 判据。

口径（rolling.py docstring）：
- 台账 ``{board_dir}/rolling/{YYYY-MM}.json``，YYYY-MM = 快照**应用月**
  = as_of 的下一个自然月；run@M+1 回填 {M}.json 的 oos（其应用月刚走完）；
- 同 as_of 重跑覆盖写但**保留已回填 oos**；existing as_of 更新 → 跳过不倒退；
- OOS = 前窗选、后窗验：窗 = 因子分区 ∈ (prev_as_of, 次月首日)，按冻结
  sgn/w 合成后逐日对 fwd5 求 RankIC；窗尾无前向的日 excluded_no_fwd 显形。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.shared.factor_board import config as C
from backend.shared.factor_board import rolling as ROLL

DATES = ["20260630", "20260731", "20260831", "20260930"]


# ═══════════════════ 纯函数：月份运算与触发判据 ═══════════════════


@pytest.mark.unit
def test_application_month_次月_含跨年():
    assert ROLL.application_month("20260930") == "2026-10"
    assert ROLL.application_month("20261215") == "2027-01"
    assert ROLL.application_month("20260131") == "2026-02"


@pytest.mark.unit
def test_next_month_first_dt_次月首日_含跨年():
    # 紧凑口径：与因子分区键同空间（ISO 混比曾致 OOS 窗恒空）
    assert ROLL._next_month_first_dt("2026-09") == "20261001"
    assert ROLL._next_month_first_dt("2026-12") == "20270101"


@pytest.mark.unit
def test_is_month_start_国庆场景_假日顺延只触发一次():
    # 9-29/9-30 为节前最后两个分区；10-08 是节后首个交易日
    d = ["20260929", "20260930"]
    assert ROLL.is_month_start(d, date(2026, 10, 8)) == (True, "20260930")
    # 10-08 收盘分区落地后，10-09 不应重复触发
    d2 = ["20260930", "20261008"]
    assert ROLL.is_month_start(d2, date(2026, 10, 9)) == (False, "20261008")
    # 空清单 → 不触发
    assert ROLL.is_month_start([], date(2026, 10, 8)) == (False, None)


@pytest.mark.unit
def test_latest_prior_date_严格小于今日():
    assert ROLL.latest_prior_date(DATES, date(2026, 9, 30)) == "20260831"
    assert ROLL.latest_prior_date(DATES, date(2026, 7, 1)) == "20260630"
    assert ROLL.latest_prior_date(["20261001"], date(2026, 6, 1)) is None


# ═══════════════════ 编排：run_rolling（打桩 run_selection/eval_oos） ═══════════════════


def _fake_run(*, as_of: str) -> SimpleNamespace:
    sel = pd.DataFrame(
        [
            {
                "lib": "l1_factors",
                "name": "turn_1",
                "group": "l1_factors/x",
                "sgn": 1,
                "w": 0.08,
                "ic60": 0.08,
                "win60": 0.62,
                "n_days": 60,
            },
            {
                "lib": "rd_mined",
                "name": "rev_9",
                "group": "rd_mined/x",
                "sgn": -1,
                "w": 0.12,
                "ic60": -0.12,
                "win60": 0.58,
                "n_days": 60,
            },
        ]
    )
    return SimpleNamespace(
        dates=["20260601", as_of],
        trade_date=as_of,
        fwd5={f"2026080{i}": None for i in range(1, 4)},
        cand=pd.DataFrame({"x": range(7)}),
        gate_info={"evaluated": 300, "gated": 140, "families": 8, "degraded": False},
        sel=sel,
        warnings=[],
    )


@pytest.fixture()
def rolling_env(monkeypatch, tmp_path: Path):
    """打桩 CP.list_factor_dates / run_selection / eval_oos；返回 (board_dir, calls)。"""
    calls: dict = {"selection": [], "oos": []}
    monkeypatch.setattr(ROLL.CP, "list_factor_dates", lambda root, **kw: list(DATES))

    def fake_run(root, *, as_of=None, today=None):
        calls["selection"].append(str(as_of))
        return _fake_run(as_of=str(as_of))

    monkeypatch.setattr(ROLL, "run_selection", fake_run)

    def fake_oos(root, factors, *, since_dt, until_dt_exclusive, horizon=5):
        calls["oos"].append((since_dt, until_dt_exclusive, len(factors)))
        return {
            "since": ROLL._iso_dt(since_dt),
            "until": ROLL._iso_dt(until_dt_exclusive),
            "horizon": 5,
            "rank_ic": 0.04,
            "icir": 0.3,
            "t": 1.9,
            "win": 0.55,
            "days": 20,
            "excluded_no_fwd": 5,
            "note": "test",
        }, []

    monkeypatch.setattr(ROLL, "eval_oos", fake_oos)
    return tmp_path, calls


def _read(board_dir: Path, month: str) -> dict:
    p = board_dir / C.ROLLING_DIRNAME / f"{month}.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_run_rolling_首跑写当月台本_缺上月台本记警(rolling_env):
    board_dir, calls = rolling_env

    summary = ROLL.run_rolling("/qdb", board_dir, as_of="20260831")

    assert summary["month"] == "2026-09"
    assert summary["as_of"] == "2026-08-31"
    assert summary["n_factors"] == 2
    assert summary["backfilled_oos"] is None and summary["skipped"] is False
    assert calls["selection"] == ["20260831"]  # as_of 原样透传选择管线
    led = _read(board_dir, "2026-09")
    assert led["schema"] == ROLL.LEDGER_SCHEMA
    assert led["month"] == "2026-09" and led["as_of"] == "2026-08-31"
    assert led["pipeline"] == "selection-only"
    assert led["window"]["end"] == "20260831" and led["window"]["horizon"] == 5
    assert led["coverage"]["selected"] == 2
    assert led["oos"] is None
    # 快照按 |ic60| 降序 + 字段齐全
    assert [f["name"] for f in led["factors"]] == ["rev_9", "turn_1"]
    assert led["factors"][0]["sgn"] == -1
    assert any("2026-08" in w for w in led["warnings"])  # 上月台本不存在
    assert calls["oos"] == []  # 无上月台本 → 不评估 OOS


@pytest.mark.unit
def test_run_rolling_次月回填上月oos_窗口为应用月(rolling_env):
    board_dir, calls = rolling_env
    ROLL.run_rolling("/qdb", board_dir, as_of="20260831")

    summary = ROLL.run_rolling("/qdb", board_dir, as_of="20260930")

    # 回填窗 = (上月 as_of, 次月首日) 开区间；上界紧凑口径（与分区键同空间）
    assert calls["oos"] == [("20260831", "20261001", 2)]
    assert summary["backfilled_oos"] == "2026-09"
    prev = _read(board_dir, "2026-09")
    assert prev["oos"]["rank_ic"] == pytest.approx(0.04)
    assert prev["oos"]["filled_by_month"] == "2026-10"
    assert str(prev["oos"]["filled_at"]).endswith("Z")
    assert prev["as_of"] == "2026-08-31"  # 原字段原样保留
    # 当月台本（10 月）已写、oos 待下月
    cur = _read(board_dir, "2026-10")
    assert cur["as_of"] == "2026-09-30" and cur["oos"] is None


@pytest.mark.unit
def test_run_rolling_同as_of重跑_保留已回填oos(rolling_env):
    board_dir, calls = rolling_env
    ROLL.run_rolling("/qdb", board_dir, as_of="20260831")
    ROLL.run_rolling("/qdb", board_dir, as_of="20260930")
    # 手动给 10 月台账塞入一份「已回填」证据（模拟未来某次运行写回）
    cur = _read(board_dir, "2026-10")
    cur["oos"] = {"rank_ic": 0.02, "days": 18}
    (board_dir / C.ROLLING_DIRNAME / "2026-10.json").write_text(
        json.dumps(cur, ensure_ascii=False), encoding="utf-8"
    )

    summary = ROLL.run_rolling("/qdb", board_dir, as_of="20260930")

    assert summary["skipped"] is False
    again = _read(board_dir, "2026-10")
    assert again["oos"] == {"rank_ic": 0.02, "days": 18}  # 证据不丢
    assert calls["selection"] == ["20260831", "20260930", "20260930"]


@pytest.mark.unit
def test_run_rolling_已有更新的as_of_跳过不倒退(rolling_env, monkeypatch):
    # Arrange：同月两个分区（08-15 / 08-31）——两跑都写应用月 2026-09
    board_dir, _ = rolling_env
    ROLL.run_rolling("/qdb", board_dir, as_of="20260831")
    monkeypatch.setattr(
        ROLL.CP,
        "list_factor_dates",
        lambda root, **kw: ["20260630", "20260731", "20260815", "20260831", "20260930"],
    )

    # Act：更早的 as_of 想写同一个月的台本
    summary = ROLL.run_rolling("/qdb", board_dir, as_of="20260815")

    # Assert：跳过写入不倒退（2026-09.json 保持 08-31 版）
    assert summary["skipped"] is True and summary["ledger"] is None
    assert any("跳过本次写入" in w for w in summary["warnings"])
    assert _read(board_dir, "2026-09")["as_of"] == "2026-08-31"


@pytest.mark.unit
def test_run_rolling_as_of非分区_钳到最近分区并记警(rolling_env):
    board_dir, calls = rolling_env

    summary = ROLL.run_rolling("/qdb", board_dir, as_of="2026-08-15")

    assert summary["as_of"] == "2026-07-31" and summary["month"] == "2026-08"
    assert calls["selection"] == ["20260731"]
    assert any("最近分区" in w for w in summary["warnings"])


@pytest.mark.unit
def test_run_rolling_as_of早于全部分区_报错(rolling_env):
    board_dir, _ = rolling_env
    with pytest.raises(RuntimeError, match="早于最早分区"):
        ROLL.run_rolling("/qdb", board_dir, as_of="20250101")


@pytest.mark.unit
def test_run_rolling_缺省as_of取最近prior分区(rolling_env):
    board_dir, calls = rolling_env
    summary = ROLL.run_rolling("/qdb", board_dir, today=date(2026, 9, 30))
    assert summary["as_of"] == "2026-08-31"  # 摘要里的 as_of 是 ISO 形态
    assert calls["selection"] == ["20260831"]


@pytest.mark.unit
def test_run_rolling_上月台本缺快照或as_of超前_跳过回填(rolling_env):
    board_dir, calls = rolling_env
    rdir = board_dir / C.ROLLING_DIRNAME
    rdir.mkdir(parents=True, exist_ok=True)
    # 情况一：缺 factors
    (rdir / "2026-08.json").write_text(
        json.dumps({"month": "2026-08", "as_of": "2026-07-31", "factors": []}),
        encoding="utf-8",
    )
    s1 = ROLL.run_rolling("/qdb", board_dir, as_of="20260831")
    assert s1["backfilled_oos"] is None
    assert any("缺快照" in w for w in s1["warnings"])
    # 情况二：as_of 超前（数据损坏）
    (rdir / "2026-08.json").write_text(
        json.dumps(
            {
                "month": "2026-08",
                "as_of": "2026-12-31",
                "factors": [{"lib": "l", "name": "n", "sgn": 1, "w": 0.1}],
            }
        ),
        encoding="utf-8",
    )
    s2 = ROLL.run_rolling("/qdb", board_dir, as_of="20260831")
    assert s2["backfilled_oos"] is None
    assert any("超前" in w for w in s2["warnings"])
    assert calls["oos"] == []


# ═══════════════════ eval_oos 实打（合成 QuantDB 树） ═══════════════════


def _dates(start: str, n: int) -> list[str]:
    """ISO 起点 + 天数 → 紧凑分区键清单（自然日连续，合成树用）。"""
    d0 = date.fromisoformat(start)
    return [(d0 + pd.Timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _write_eval_tree(root: Path, dates: list[str], n_syms: int = 6) -> list[str]:
    """因子 = 序号 j+1；close[j,t] = (j+1)^(t+1) → fwd5 收益单调于 j。

    前向收益 rank 与因子 rank 完全一致 → 后窗每日 RankIC 恒 = +1（已知答案）。
    """
    syms = [f"60000{j}.SH" for j in range(n_syms)]
    for t, dt in enumerate(dates):
        fp = root / "6_ml_datasets" / "l1_factors" / f"dt={dt}"
        fp.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {"symbol": syms, "f": [float(j + 1) for j in range(n_syms)]}
        ).to_parquet(fp / "data.parquet", index=False)
        kp = root / "1_kline_data" / "daily_forward" / f"dt={dt}"
        kp.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "symbol": syms,
                "close": [float((j + 1) ** (t + 1)) for j in range(n_syms)],
            }
        ).to_parquet(kp / "data.parquet", index=False)
    return dates


@pytest.mark.unit
def test_eval_oos_合成树_每日rankIC为1_窗尾无前向排除(tmp_path, monkeypatch):
    monkeypatch.setattr(ROLL.C, "MIN_COMPOSITE_IC_DAY", 3)
    _write_eval_tree(tmp_path, _dates("2026-01-01", 10))
    factors = [{"lib": "l1_factors", "name": "f", "group": "g", "sgn": 1, "w": 0.1}]

    # 窗 = (2026-01-01, 2026-01-07) → d2~d6 五个日；d6 的 t+5 无前向
    block, warnings = ROLL.eval_oos(
        tmp_path, factors, since_dt="20260101", until_dt_exclusive="20260107"
    )

    assert warnings == []
    assert block is not None
    assert block["since"] == "2026-01-01" and block["until"] == "2026-01-06"
    assert block["days"] == 4
    assert block["rank_ic"] == pytest.approx(1.0, abs=1e-9)
    assert block["win"] == pytest.approx(1.0)
    assert block["excluded_no_fwd"] == 1  # 窗尾无前向 → 诚实排除
    assert "excluded_no_fwd" in block and "排除" in block["note"]


@pytest.mark.unit
def test_eval_oos_无因子或空窗_返回None带警(tmp_path):
    _write_eval_tree(tmp_path, _dates("2026-01-01", 10))
    block, warnings = ROLL.eval_oos(
        tmp_path, [], since_dt="20260101", until_dt_exclusive="20260107"
    )
    assert block is None and any("无因子" in w for w in warnings)
    ok_factors = [{"lib": "l1_factors", "name": "f", "sgn": 1, "w": 0.1}]
    block2, warnings2 = ROLL.eval_oos(
        tmp_path, ok_factors, since_dt="20270101", until_dt_exclusive="20270107"
    )
    assert block2 is None and any("窗内无因子分区" in w for w in warnings2)


@pytest.mark.unit
def test_eval_oos_ISO边界与紧凑边界_同窗同值(tmp_path, monkeypatch):
    """回归：边界归一化——ISO 与紧凑必须落在同一窗（曾混比致窗恒空）。"""
    monkeypatch.setattr(ROLL.C, "MIN_COMPOSITE_IC_DAY", 3)
    _write_eval_tree(tmp_path, _dates("2026-01-01", 10))
    factors = [{"lib": "l1_factors", "name": "f", "group": "g", "sgn": 1, "w": 0.1}]

    compact, warn_c = ROLL.eval_oos(
        tmp_path, factors, since_dt="20260101", until_dt_exclusive="20260107"
    )
    iso, warn_i = ROLL.eval_oos(
        tmp_path, factors, since_dt="2026-01-01", until_dt_exclusive="2026-01-07"
    )

    assert warn_c == warn_i == []
    assert compact is not None and compact["days"] == 4
    assert iso == compact


@pytest.mark.unit
def test_run_rolling_真实eval_oos_次月回填不落空(tmp_path, monkeypatch):
    """回归（8 月回填落 null 的实证根因）：run_rolling 回填窗上界
    ``_next_month_first_dt`` 与真实 ``eval_oos`` 直连——旧版 ISO 上界与紧凑
    分区键混比，窗恒空。合成树跨月（1/25~2/10）走真实评估链。"""
    monkeypatch.setattr(ROLL.C, "MIN_COMPOSITE_IC_DAY", 3)

    def _fake_run_tree(root, *, as_of=None, today=None):
        """选择快照的因子名对齐合成树列（l1_factors.f）——回填走真实合成链。"""
        base = _fake_run(as_of=str(as_of))
        base.sel = pd.DataFrame(
            [
                {
                    "lib": "l1_factors",
                    "name": "f",
                    "group": "l1_factors/x",
                    "sgn": 1,
                    "w": 0.1,
                    "ic60": 0.1,
                    "win60": 0.6,
                    "n_days": 60,
                }
            ]
        )
        return base

    monkeypatch.setattr(ROLL, "run_selection", _fake_run_tree)
    _write_eval_tree(tmp_path, _dates("2026-01-25", 17))  # 1/25~1/31 + 2/1~2/10
    board_dir = tmp_path / "board"

    # 首跑（as_of=1/27）写 2026-02 台本；次跑（as_of=2/10）回填其 OOS
    s1 = ROLL.run_rolling(tmp_path, board_dir, as_of="20260127")
    assert s1["backfilled_oos"] is None
    s2 = ROLL.run_rolling(tmp_path, board_dir, as_of="20260210")

    assert s2["backfilled_oos"] == "2026-02"
    oos = _read(board_dir, "2026-02")["oos"]
    # 窗 = (2026-01-27, 2026-03-01) → 1/28~2/10 共 14 日；尾 5 日无前向显形排除
    assert oos["since"] == "2026-01-27" and oos["until"] == "2026-02-10"
    assert oos["days"] == 9 and oos["excluded_no_fwd"] == 5
    assert oos["rank_ic"] == pytest.approx(1.0, abs=1e-9)
    assert oos["filled_by_month"] == "2026-03"
    assert s2["warnings"] == []  # 不得再出现「窗内无因子分区」


# ═══════════════════ 台账读写 ═══════════════════


@pytest.mark.unit
def test_ledger_读缺文件或坏json返回None(tmp_path):
    assert ROLL.load_ledger(tmp_path, "2026-09") is None
    p = tmp_path / C.ROLLING_DIRNAME / "2026-09.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{broken", encoding="utf-8")
    assert ROLL.load_ledger(tmp_path, "2026-09") is None


@pytest.mark.unit
def test_write_ledger_原子落盘不留tmp(tmp_path):
    p = ROLL.write_ledger(tmp_path, "2026-09", {"month": "2026-09", "oos": None})
    assert p == tmp_path / C.ROLLING_DIRNAME / "2026-09.json"
    assert json.loads(p.read_text(encoding="utf-8"))["month"] == "2026-09"
    assert list(p.parent.glob("*.tmp")) == []


# ═══════════════════ CLI：触发判据与错误码 ═══════════════════


@pytest.mark.unit
def test_cli_非应用月首日_静默跳过退0(tmp_path, monkeypatch, capsys):
    from backend.scripts.market_analysis import rolling_reselect as CLI

    monkeypatch.setattr(CLI.CP, "list_factor_dates", lambda root, **kw: ["20260930"])
    monkeypatch.setattr(
        CLI.ROLL, "is_month_start", lambda dates, today: (False, "20260930")
    )
    monkeypatch.setattr(
        sys, "argv", ["rolling_reselect.py", "--data-dir", str(tmp_path)]
    )

    rc = CLI.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True and out["latest_prior_date"] == "20260930"


@pytest.mark.unit
def test_cli_数据根不存在_退2(tmp_path, monkeypatch, capsys):
    from backend.scripts.market_analysis import rolling_reselect as CLI

    monkeypatch.setattr(
        sys, "argv", ["rolling_reselect.py", "--data-dir", str(tmp_path / "nope")]
    )
    assert CLI.main() == 2


@pytest.mark.unit
def test_cli_显式as_of_跳过月初判据_抛硬门退1(tmp_path, monkeypatch):
    from backend.scripts.market_analysis import rolling_reselect as CLI
    from backend.shared.factor_board.builder import BuildError

    def boom(*a, **kw):
        raise BuildError("入选不足")

    monkeypatch.setattr(CLI.ROLL, "run_rolling", boom)
    monkeypatch.setattr(
        sys,
        "argv",
        ["rolling_reselect.py", "--data-dir", str(tmp_path), "--as-of", "2026-08-31"],
    )

    assert CLI.main() == 1
