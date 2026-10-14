"""决策轮「在岗」标记——宿主对话轮据此让位的**唯一判据**（甲5）。

问题
----
上游 ``live_hourly_analysis.py`` 是**一个**回路：同一次调用既产分析（落
``log.jsonl`` → arena「模型对话」）又产决策（→ 闸门 → 下单）。迁 QM 时裂成两半，
且两半都还活着：

* **决策轮**（容器内 trade 服务，``QM_DECISION_ROUND_ENABLED``）——下单的那半，
  2026-10-14 起也写 ``log.jsonl``（``arena_files.append_round_dialogue``，甲3）；
* **对话轮**（宿主 cron ``scripts/live_model_analysis.py``）——只出观点的那半。

两边槽位几乎逐一对上（决策轮 11 槽含 08:30/09:00/10:00/11:00/12:00/13:00/14:00/14:45，
对话轮 cron 恰好也是这几档）。不归并就是**同一个小时往同一个文件追加两条卡片**：
一条是拍板落单的决策过程，一条是明令不下单的只读观点，token 用量还各记一份。

为什么是「标记文件」而不是别的信号
----------------------------------
判据必须在**宿主**侧成立，而宿主能看到的只有共享挂载（``data/``、``config/``）：

* **Redis 不行**——宿主脚本没有 Redis 客户端，也不该为它加一个（凭据在容器的
  ``config/runtime.env``，``600 root``，宿主 zbox 读不到）；
* **探测容器进程**（``docker exec`` / ``docker inspect``）不行——把编排细节焊进
  业务脚本，且每次起动一个 docker 客户端；
* **「查本槽 log.jsonl 里有没有决策轮条目」不行**——两边**同时**起步
  （cron ``HH:00:00`` vs 决策轮 30s 轮询），谁先落盘是不确定的竞态，不是判据。

所以决策轮 worker 每轮循环把自己的在岗状态写进宿主可见的
``config/decision_round.onduty.json``（``./config:/app/config`` 双向挂载，
容器内 ``/app/config`` 与宿主 ``<repo>/config`` 是同一份文件），对话轮读它。

语义：**两条都成立**才算「在岗到点」
------------------------------------
1. **新鲜**——``at`` 距现在 ``MAX_AGE_S`` 以内。worker 每轮询一次重写（缺省 30s），
   故进程一死/一停，标记最多 10 分钟就过期。**过期 = 不在岗 = 对话轮照跑**，这是
   故意选的方向：宁可退回「两边同跑 + 双写」的老行为，也不让展示面**静默变空**。
2. **到点**——当前 ``HH:MM`` 落在某个决策槽位之后的 ``SLOT_GUARD_MIN`` 分钟窗内。
   窗口只需盖住「两边同一秒起步、决策轮稍晚才认领」这一点点时间差，**不需要**覆盖
   决策轮 45 分钟的补跑窗（对话轮只在整点与 08:30/14:45 起步）。

槽位表**随标记下发**（写侧从 ``decision_round_core.SLOTS`` 取，读侧不硬编码）：
两边各写一份 = 必然漂移，而漂移的后果是**静默双写**——正是本模块要消灭的那个故障。

失败方向（本模块不抛异常，三处都 fail-open 到「对话轮照跑」）
-------------------------------------------------------------
* 标记不存在 / 读不动 / JSON 坏 / ``at`` 解析失败 → 判「不在岗」；
* 写侧失败 → ``logger.error`` 并返回 ``False``（调用方另有去重告警）；
* 宿主脚本 import 不到本模块 → 宿主侧 try/except 落回「照跑」。

**本模块不做任何交易判断**，它只回答「这一分钟该谁说话」。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 共享挂载点：容器内 ``/app/config``（``./config:/app/config``）与宿主
#: ``<repo>/config`` 是同一份文件——标记的全部意义就是两边看同一份。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKER_PATH = _PROJECT_ROOT / "config" / "decision_round.onduty.json"

#: 标记的最大年龄（秒）。worker 缺省 30s 重写一次，10 分钟 = 连丢 20 拍才算过期
#: （容器重启、服务重启、worker 被看门狗重拉都不至于让对话轮误判「没人在岗」）。
MAX_AGE_S = 600

#: 槽位之后多少分钟内算「决策轮主场」。见模块 docstring：只盖起步时间差。
SLOT_GUARD_MIN = 10

#: 写侧应报的开关名（写进标记只为让这个文件自证「是谁把我打开的」）。
ENV_FLAG = "QM_DECISION_ROUND_ENABLED"

#: 中国无夏令时。本模块**零依赖**（stdlib only）是硬要求：宿主 cron 脚本用的
#: ``/usr/bin/python3`` 是系统解释器，装没有 pydantic/sqlalchemy 的一整套后端依赖；
#: 为拿一个 CST 去 import ``decision_context_source`` 会把整条后端依赖链拖进来。
CST = timezone(timedelta(hours=8))


def _as_cn(ts: datetime | None) -> datetime:
    """归一成 CST aware；naive 值**按 CST 解释**（调用方的墙钟语义就是北京时间）。"""
    if ts is None:
        return datetime.now(CST)
    return ts.replace(tzinfo=CST) if ts.tzinfo is None else ts.astimezone(CST)


def _parse_at(raw: Any) -> datetime | None:
    """标记里的 ``at`` → aware datetime；解析不出来返回 ``None``（= 不在岗）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return _as_cn(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _minutes(hhmm: Any) -> int | None:
    """``"0935"`` → ``575``；格式不对返回 ``None``（坏项跳过而不是整份作废）。"""
    if not isinstance(hhmm, str) or len(hhmm) < 4:
        return None
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[2:4])
    except ValueError:
        return None


def write_on_duty(
    slots: Iterable[str],
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
    pid: int | None = None,
) -> bool:
    """写「决策轮在岗」标记（原子替换）。失败只记日志并返回 ``False``，**不抛**。

    原子性是必需的：读侧撞上写一半的文件会 JSON 解析失败 → 判「不在岗」→ 双写。
    ``os.replace`` 同目录改名，任何时刻读到的都是完整的一份。

    ``tmp`` 名带 pid：同机若有第二个写者（CLI 重跑是短进程、不写标记，但别假设），
    两个临时文件不会互相覆盖。
    """
    target = Path(path) if path is not None else MARKER_PATH
    at = _as_cn(now)
    payload = {
        "at": at.isoformat(),
        "pid": os.getpid() if pid is None else int(pid),
        "flag": ENV_FLAG,
        "slots": [str(s) for s in slots],
        "guard_min": SLOT_GUARD_MIN,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except Exception as exc:  # noqa: BLE001 写不出去不许影响真钱回路
        logger.error(
            "[DecisionRound] 在岗标记写入失败（%s）：宿主对话轮将按「无人在岗」照跑，"
            "与决策轮同槽双写。%s",
            target,
            exc,
        )
        return False


def read_on_duty(
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """读标记；**不存在 / 读不动 / JSON 坏 / 不新鲜**一律返回 ``None``（= 不在岗）。"""
    target = Path(path) if path is not None else MARKER_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 文件不在/没权限/半截 JSON 都是「不在岗」
        return None
    if not isinstance(raw, dict):
        return None
    at = _parse_at(raw.get("at"))
    if at is None:
        return None
    # 时钟未来/过去都按「不可信」处理——容器的 TZ 与宿主差一小时是显示差，
    # 但真出现时钟漂移时，标记的绝对时间就失去意义了。
    if abs((_as_cn(now) - at).total_seconds()) > MAX_AGE_S:
        return None
    return raw


def on_duty_for(
    now: datetime | None = None,
    *,
    path: str | Path | None = None,
) -> tuple[bool, str]:
    """当前时刻是否该由决策轮说话：``(是否让位, 人话理由)``——读侧**唯一入口**。

    理由串是给日志与 QQ 看的：让位是**看得见的行为**，不是静默跳过（静默跳过
    正是「模型对话这月没有总结」那类投诉的成因）。
    """
    moment = _as_cn(now)
    mark = read_on_duty(now=moment, path=path)
    if mark is None:
        return False, "无新鲜在岗标记（决策轮未开 / 未跑 / 标记过期）"
    at = _parse_at(mark.get("at"))
    cur = moment.strftime("%H%M")
    cur_min = _minutes(cur)
    guard_raw = mark.get("guard_min")
    guard = (
        guard_raw if isinstance(guard_raw, int) and guard_raw >= 0 else SLOT_GUARD_MIN
    )
    slots = mark.get("slots")
    for slot in slots if isinstance(slots, list) else []:
        slot_min = _minutes(slot)
        if slot_min is None or cur_min is None:
            continue
        if 0 <= cur_min - slot_min <= guard:
            return True, (
                f"决策轮在岗（心跳 {at:%H:%M}）且当前 {cur[:2]}:{cur[2:]} 落在其槽位 "
                f"{slot[:2]}:{slot[2:]} 的 {guard} 分钟窗内"
            )
    return False, (
        f"决策轮在岗（心跳 {at:%H:%M}），但当前 {cur[:2]}:{cur[2:]} 不在任何槽位窗内"
    )
