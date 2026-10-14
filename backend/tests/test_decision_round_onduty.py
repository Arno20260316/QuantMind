"""决策轮「在岗」标记（``backend/shared/decision_round_onduty.py``）测试。

这个模块是**两个进程之间的契约**（容器内的决策轮 worker 写、宿主的对话轮 cron
读），所以用例分两半：

* **写侧**：原子落盘、字段自证、失败不抛（写不出去只该少一个信号，不许影响真钱）；
* **读侧**：所有「读不出一条可信的标记」的形态都必须判**不在岗**——这是 fail-open
  的方向（判据失灵 → 回到两边同跑的老行为 → 展示面重复，但绝不静默变空）。

宿主侧还有一份同契约的用例在 ``test_live_model_analysis.py``（它按脚本的真实调用
路径走一遍），本文件只管模块本体的边界。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.shared import decision_round_onduty as OD

_CST = timezone(timedelta(hours=8))
_AT = datetime(2026, 10, 14, 13, 2, 30, tzinfo=_CST)
_SLOTS = ["0830", "0900", "0935", "1000", "1005", "1100", "1105", "1200", "1300"]


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "decision_round.onduty.json"


# ── 1. 写侧 ─────────────────────────────────────────────────────────────


def test_write_then_read_roundtrip_carries_slots_and_self_describes(tmp_path):
    """写进去的要能原样读出来，且**自证是谁开的**（flag）——运维看这一个文件就够。"""
    path = _marker(tmp_path)
    assert OD.write_on_duty(_SLOTS, now=_AT, path=path, pid=4242) is True

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["at"] == _AT.isoformat()
    assert raw["pid"] == 4242
    assert raw["flag"] == "QM_DECISION_ROUND_ENABLED"
    assert raw["slots"] == _SLOTS
    assert raw["guard_min"] == OD.SLOT_GUARD_MIN

    got = OD.read_on_duty(now=_AT, path=path)
    assert got is not None and got["slots"] == _SLOTS


def test_write_leaves_no_temp_file_behind(tmp_path):
    """``os.replace`` 自证：目录里只该有标记本体（半个文件 = 读侧误判「不在岗」）。"""
    path = _marker(tmp_path)
    OD.write_on_duty(_SLOTS, now=_AT, path=path)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_write_creates_missing_parents_and_never_raises_on_failure(tmp_path):
    """父目录不存在要自建；写不出去（目标是目录）只返回 False——**不抛**。"""
    nested = tmp_path / "config" / "decision_round.onduty.json"
    assert OD.write_on_duty(_SLOTS, now=_AT, path=nested) is True

    # 目标是目录：os.replace 必定失败 → 写侧吞掉并返回 False
    assert OD.write_on_duty(_SLOTS, now=_AT, path=tmp_path) is False


# ── 2. 读侧：所有不可信形态都判「不在岗」 ────────────────────────────────


def test_missing_or_unreadable_marker_means_not_on_duty(tmp_path):
    assert OD.read_on_duty(now=_AT, path=_marker(tmp_path)) is None
    assert OD.on_duty_for(_AT, path=_marker(tmp_path))[0] is False


def test_corrupt_or_non_dict_payload_means_not_on_duty(tmp_path):
    """半截 JSON / 顶层不是对象——都要判不在岗，而不是炸给调用方看。"""
    path = _marker(tmp_path)
    for payload in ('{"at": "2026-10-14T13:00:00+08:00"', "[1, 2]", "null", "\x00\xff"):
        path.write_text(payload, encoding="utf-8")
        assert OD.read_on_duty(now=_AT, path=path) is None


def test_unparseable_at_means_not_on_duty(tmp_path):
    """``at`` 是判新鲜的唯一依据；解析不出来 = 没有可信的新鲜度 = 不在岗。"""
    path = _marker(tmp_path)
    for bad in (None, "", "   ", 1750000000, "2026-10-14 13:00:00 北京"):
        path.write_text(json.dumps({"at": bad, "slots": _SLOTS}), encoding="utf-8")
        assert OD.read_on_duty(now=_AT, path=path) is None


def test_stale_marker_expires_so_the_dialogue_round_resumes(tmp_path):
    """worker 死了 → 心跳停 → 标记过期 → 对话轮**自动照跑**（展示面不许静默变空）。"""
    path = _marker(tmp_path)
    OD.write_on_duty(_SLOTS, now=_AT, path=path)
    inside = _AT + timedelta(seconds=OD.MAX_AGE_S)
    outside = _AT + timedelta(seconds=OD.MAX_AGE_S + 1)
    assert OD.read_on_duty(now=inside, path=path) is not None
    assert OD.read_on_duty(now=outside, path=path) is None


def test_clock_skewed_future_marker_is_not_trusted(tmp_path):
    """标记时间跑到未来（时钟漂移/写侧用了错的时区）同样不可信。"""
    path = _marker(tmp_path)
    OD.write_on_duty(_SLOTS, now=_AT, path=path)
    skewed = _AT - timedelta(seconds=OD.MAX_AGE_S + 1)
    assert OD.read_on_duty(now=skewed, path=path) is None


# ── 3. 判据：新鲜 + 到点，两条都要成立 ───────────────────────────────────


def test_on_duty_only_within_the_slot_window(tmp_path):
    path = _marker(tmp_path)
    slots = ["0935", "1300"]

    def at(hh: int, mm: int) -> datetime:
        return datetime(2026, 10, 14, hh, mm, tzinfo=_CST)

    # 槽位写上、窗内、窗外各取一点（窗 = 槽位 + SLOT_GUARD_MIN 分钟，闭区间）
    for moment, expect in (
        (at(9, 34), False),
        (at(9, 35), True),
        (at(9, 35 + OD.SLOT_GUARD_MIN), True),
        (at(9, 36 + OD.SLOT_GUARD_MIN), False),
        (at(13, 0), True),
        (at(12, 59), False),
    ):
        OD.write_on_duty(slots, now=moment, path=path)
        ok, why = OD.on_duty_for(moment, path=path)
        assert ok is expect, (moment, why)
        assert why  # 无论让不让位都要有人话理由（静默跳过是本次要消灭的故障）


def test_on_duty_reason_names_the_slot_and_the_heartbeat(tmp_path):
    """让位理由要能自证：哪一槽、心跳几点——宿主日志里就靠这一行复盘。"""
    path = _marker(tmp_path)
    moment = datetime(2026, 10, 14, 13, 2, tzinfo=_CST)
    OD.write_on_duty(["1300"], now=moment, path=path)
    ok, why = OD.on_duty_for(moment, path=path)
    assert ok and "13:00" in why and "13:02" in why


def test_on_duty_naive_now_is_read_as_beijing_wall_clock(tmp_path):
    """naive 墙钟按 CST 解释（调用方给的「现在」语义就是北京时间）。"""
    path = _marker(tmp_path)
    OD.write_on_duty(["1300"], now=_AT, path=path)
    naive = datetime(2026, 10, 14, 13, 3, 0)  # noqa: DTZ001 - 被测的就是 naive 分支
    assert OD.on_duty_for(naive, path=path)[0] is True


def test_garbage_slots_are_skipped_not_fatal(tmp_path):
    """坏槽位项跳过即可：一项格式错不该让整份标记作废。"""
    path = _marker(tmp_path)
    moment = datetime(2026, 10, 14, 13, 2, tzinfo=_CST)
    OD.write_on_duty(["nope", "", "13", None, "1300"], now=moment, path=path)
    assert OD.on_duty_for(moment, path=path)[0] is True


def test_marker_lands_on_the_shared_config_mount():
    """默认落点必须与 docker-compose 的 ``./config:/app/config`` 对上。

    写成别处 = 容器里写的宿主读不到，**两边永远各跑各的**，且全链无报错——
    本仓在「哨兵写 db0 而冲刷读 db2」上已经栽过一次同一形状的跟头。
    """
    assert OD.MARKER_PATH.parent.name == "config"
    assert OD.MARKER_PATH.name == "decision_round.onduty.json"
    assert OD.MARKER_PATH.is_absolute()
