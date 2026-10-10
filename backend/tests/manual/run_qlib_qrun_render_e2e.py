"""端到端验证：渲染后的 qlib conf 真能被 ``qrun`` 跑通（回归证据，几分钟）。

为什么需要它：vendored RD-Agent 的 ``conf_*.yaml`` 是 Jinja 模板却没有渲染环节，
叠加 mlflow 3.x 默认禁用文件后端 → 挖掘循环里的实验**从未**成功过（全盘 0 个
``qlib_res.csv``）。两个修复（``qlib_conf_template`` 渲染补丁 +
``rd_loop_wrapper`` 的 ``MLFLOW_ALLOW_FILE_STORE``）必须有一条**真跑 qrun** 的
证据链，单测只覆盖到"渲染产物合法"。

流程（不跑 LLM、不花钱，LGBM 训练几分钟）：

1. 取**运行时装**的 rdagent 包里的真模板 ``conf_baseline.yaml``；
2. 走与补丁同一条代码路径（``render_conf_file``）渲染到 scratch 目录；
3. ``MLFLOW_ALLOW_FILE_STORE=true qrun <rendered>``（mlflow 3.x 文件后端开关）；
4. ``python read_exp_res.py`` 取 mlflow 指标 → 断言 ``qlib_res.csv``/``ret.pkl`` 落盘。

``--window mining`` 走**生产代码路径**算窗口（覆盖 → 默认窗口 → 回测末端保护 →
qlib 分段），不是手写常量：默认窗口锚数据末端，不退让就必然崩在 qlib 的 t+1
时间步上（``TradeCalendarManager.get_step_time`` 要取下一根 bar）。``--no-guard``
关掉末端保护即复现那个 IndexError（出证据用，默认不开）。

用法（在 quantmind 容器内，注意必须带 mlflow 开关，或让脚本自己带）：

    docker exec -w /app quantmind python backend/tests/manual/run_qlib_qrun_render_e2e.py
    docker exec -w /app quantmind python backend/tests/manual/run_qlib_qrun_render_e2e.py --window mining

退出码 0 = 端到端通过。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from backend.services.engine.rd_agent.qlib_conf_template import (  # noqa: E402
    render_conf_file,
)

#: 与 rd_loop_wrapper._configure_env 的旧静态兜底一致（无自定义窗口时的默认）
LEGACY_CTX = {
    "train_start": "2016-01-01",
    "train_end": "2020-12-31",
    "valid_start": "2021-01-01",
    "valid_end": "2022-12-31",
    "test_start": "2023-01-01",
    "test_end": "2025-12-31",
    "feature_names": "['rev1']",
    "feature_expressions": "['Ref($close, 1)/$close - 1']",
}

#: 因子上下文（任何窗口下都用的最小合法因子）
_FACTOR_CTX = {
    "feature_names": "['rev1']",
    "feature_expressions": "['Ref($close, 1)/$close - 1']",
}

_QMTIMEOUT = 1800


def _template_dir() -> Path:
    from rdagent.scenarios.qlib.experiment import workspace as ws

    return Path(ws.__file__).resolve().parent / "factor_template"


def _mining_ctx(guarded: bool) -> tuple[dict, str]:
    """生产路径：覆盖 → 默认窗口 → 末端保护 → qlib 分段 → conf 上下文。"""
    from backend.services.engine.alpha_agent.mining_config import (
        default_windows,
        qlib_segments,
    )
    from backend.services.engine.rd_agent.rd_loop_wrapper import (
        RDLoopWrapper,
        qlib_backtest_data_end,
        quantdb_coverage_from_dir,
    )

    coverage = quantdb_coverage_from_dir(RDLoopWrapper._resolve_quantdb_dir())
    guard = qlib_backtest_data_end(coverage) if guarded else None
    mining, validation = default_windows(coverage, data_end=guard)
    seg = qlib_segments(mining, validation)
    note = f"coverage={coverage} guard={guard}"
    return (
        {
            "train_start": seg["train"][0],
            "train_end": seg["train"][1],
            "valid_start": seg["valid"][0],
            "valid_end": seg["valid"][1],
            "test_start": seg["test"][0],
            "test_end": seg["test"][1],
            **_FACTOR_CTX,
        },
        note,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", choices=["legacy", "mining"], default="legacy")
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="mining 路径关掉回测末端保护（复现 qlib t+1 时间步 IndexError）",
    )
    parser.add_argument("--workdir", default="", help="留空则用临时目录")
    args = parser.parse_args()

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="qm_qrun_e2e_"))
    workdir.mkdir(parents=True, exist_ok=True)
    tpl = _template_dir()
    for name in ("conf_baseline.yaml", "read_exp_res.py"):
        shutil.copy(tpl / name, workdir / name)

    note = ""
    if args.window == "legacy":
        ctx = LEGACY_CTX
    else:
        ctx, note = _mining_ctx(guarded=not args.no_guard)
        if note:
            print(f"[0/3] {note}")
    rendered = render_conf_file(workdir / "conf_baseline.yaml", ctx)
    print(f"[1/3] 渲染完成: {rendered} (window={args.window}, test={ctx['test_start']}~{ctx['test_end']})")

    env = {**os.environ, "MLFLOW_ALLOW_FILE_STORE": "true"}
    proc = subprocess.run(
        ["qrun", rendered.name],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=_QMTIMEOUT,
    )
    tail = "\n".join((proc.stdout or proc.stderr).strip().splitlines()[-12:])
    print(f"[2/3] qrun 退出码 {proc.returncode}\n{tail}")
    if proc.returncode != 0:
        print(f"FAIL: qrun 未通过；scratch 目录保留在 {workdir}")
        return 1

    proc2 = subprocess.run(
        [sys.executable, "read_exp_res.py"],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(f"[3/3] read_exp_res 退出码 {proc2.returncode}\n{(proc2.stdout or proc2.stderr).strip()[-400:]}")

    ok = proc2.returncode == 0 and (workdir / "qlib_res.csv").exists() and (workdir / "ret.pkl").exists()
    if ok:
        metrics = (workdir / "qlib_res.csv").read_text(encoding="utf-8").strip().splitlines()
        print(f"PASS: 产物已落盘（qlib_res.csv {len(metrics) - 1} 项指标, ret.pkl 就绪）→ {workdir}")
        return 0
    print(f"FAIL: 缺产物（qlib_res.csv/ret.pkl）；scratch 目录保留在 {workdir}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
