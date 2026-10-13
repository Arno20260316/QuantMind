#!/usr/bin/env python3
"""盘前补池就绪检查（宿主 cron，JST 08:50 = 北京 07:50，开盘前 1h40m）。

为什么要有这个：2026-10-13 候选池整天没产出（alpha 因子库 02:30 因内存不足被跳过 →
CUSTOM 重建拒写 → 6 个模型推理全撞维度门禁 → 池文件不存在），而**第一个告警是开盘后
09:35 决策轮跑起来才发的**（``empty_pool``）。那时当天全部开仓机会已经错过。本脚本把
同一件事挪到开盘前，且用与状态卡**同一份判据**（``backend.shared.pool_readiness``）。

判据三态（关键是第 2 条）：

* 池文件**不在** / 读不动 / 0 只候选且非方向闸清空 → 🔴 **断链，推 QQ**（带断在哪一段）；
* 池文件在、0 只候选、方向判**看空** → 方向闸**按设计**清空（10-12 复盘「强烈看空
  -4.8/11」，健康链跑出来也是这个形状）→ **不推**，只记日志。把它当故障推会天天误报，
  值班的人很快就会开始忽略这条告警；
* 池文件在、有候选 → 正常 → 只记日志。

交易日历读不到时按**工作日启发式**兜底并在文案里显式标注（长假会判错，明说好过假装准）。

用法::

    python3 scripts/preopen_pool_check.py             # 正常跑（当天已推过则静默退出）
    python3 scripts/preopen_pool_check.py --dry-run   # 只打印，不推 QQ、不写状态
    python3 scripts/preopen_pool_check.py --force     # 忽略当天已推过去重
    python3 scripts/preopen_pool_check.py --target 20261013
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    # cron 从 $HOME 起跑；判据要 import backend.*
    sys.path.insert(0, str(ROOT))

LOG = ROOT / "logs" / "keepers" / "preopen_pool_check.log"
#: 当天已推过告警的标记（一天一条，避免同一故障刷屏后被人静音）
SENT_DIR = ROOT / "logs" / "keepers" / ".preopen_pool_check_sent"
CST = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    line = f"[{datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 日志写不动不能拦住告警


def _judge():
    """判据与探针的取数点（**单一注入点**：测试换它，生产就是共享判据本体）。

    判据故意不在本模块重写：宿主告警与后台状态卡读的是同一份
    ``backend.shared.pool_readiness``，两边各写一套必然漂移。
    """
    from backend.shared.pool_readiness import collect, pg_inference_probe

    return collect, pg_inference_probe


def _fmt_stage_line(stage) -> str:
    mark = "✓" if stage.ok else "✗"
    tail = "（方向闸清空，正常）" if stage.by_design else ""
    return f"{mark} {stage.label}：{stage.detail}{tail}"


def _alert_body(report, *, degraded_note: str) -> str:
    """告警正文：先说结论，再给五段链条——值班的人一眼看出卡在哪。"""
    lines = [
        f"目标交易日 {report.target} 的候选池**没有就绪**。",
        f"卡在：**{report.blocked_label or report.blocked_stage or '未知'}**",
        "",
    ]
    lines.extend(_fmt_stage_line(s) for s in report.stages)
    if degraded_note:
        lines.append("")
        lines.append(degraded_note)
    lines.append("")
    lines.append(
        "开盘前决策轮会因「候选池非成员」拦下全部新开仓。"
        "补池最快路径：确认内存后重跑 scripts/alpha_refresh_retry.sh，"
        "或看后台「数据管理」页的补池就绪卡。"
    )
    return "\n".join(lines)


def _send(title: str, body: str, *, dry_run: bool) -> None:
    if dry_run:
        log(f"[dry-run] 将推送：{title}\n{body}")
        return
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from push_notify import notify  # noqa: PLC0415

        notify(title, body)
        log(f"已推送 QQ：{title}")
    except Exception as exc:  # noqa: BLE001 - 推送失败不能让 cron 报错刷屏
        log(f"⚠️ QQ 推送失败（故障依旧存在）：{exc}")


def _already_sent(target: str) -> bool:
    return (SENT_DIR / f"{target}.flag").exists()


def _mark_sent(target: str) -> None:
    try:
        SENT_DIR.mkdir(parents=True, exist_ok=True)
        (SENT_DIR / f"{target}.flag").write_text(
            datetime.now(CST).isoformat(), encoding="utf-8"
        )
    except OSError as exc:
        log(f"⚠️ 去重标记写不动（下次可能重复推）：{exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前补池就绪检查")
    ap.add_argument("--target", help="目标交易日 YYYYMMDD（缺省按交易日历推）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推 QQ、不写状态")
    ap.add_argument("--force", action="store_true", help="忽略当天已推过去重")
    args = ap.parse_args()

    collect, pg_inference_probe = _judge()
    try:
        report = collect(target=args.target, inference_probe=pg_inference_probe())
    except Exception as exc:  # noqa: BLE001 - 判据本身炸了本身就是事故，得报出来
        log(f"❌ 就绪判据执行失败：{exc}")
        _send(
            "🔴 补池就绪检查自身失败",
            f"判据脚本抛异常，当前无从判断候选池状态：\n`{type(exc).__name__}: {exc}`",
            dry_run=args.dry_run,
        )
        return 1

    degraded_note = ""
    if report.target_degraded:
        degraded_note = (
            f"⚠️ 交易日历不可用（来源={report.target_source}），"
            "目标日按工作日启发式推出——周中长假会判错，请人工核对。"
        )

    summary = " / ".join(f"{s.label}={'ok' if s.ok else '断'}" for s in report.stages)

    if report.ok:
        if report.by_design:
            log(f"链通，池按方向闸清空（正常）：{report.target} ｜ {summary}")
        else:
            log(f"链通：{report.target} ｜ {summary}")
        return 0

    log(f"❌ 断链：{report.target} ｜ 卡在 {report.blocked_stage} ｜ {summary}")
    if degraded_note:
        log(degraded_note)

    if not args.force and _already_sent(report.target):
        log("当天已推送过，跳过重复推送（--force 可强制）")
        return 0

    _send(
        "🟠 盘前预警：候选池未就绪",
        _alert_body(report, degraded_note=degraded_note),
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        _mark_sent(report.target)
    return 2


if __name__ == "__main__":
    sys.exit(main())
