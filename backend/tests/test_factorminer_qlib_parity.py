"""31 条可翻译论文因子：qlib 翻译产物 vs FM 求值器 逐值对拍（容器集成）。

T-FM-01 落库的两层结构：``fm:`` 原式走 FM 求值器（全部 110 条），
``qlib_export`` 的翻译产物只作可携带元数据——但**元数据也要是真的**：
本测在真 qlib 数据上把 31 条翻译逐值对拍 FM 侧求值（``runtime.evaluate_window``
= 生产求值入口）。

venv 无 qlib → skip；容器里跑：

    docker exec -w /app quantmind python -m pytest \
        backend/tests/test_factorminer_qlib_parity.py -q

**容差为什么不是纯相对 1e-4（实测钻探结论，2026-10-11）**：两边输入同源
（runtime 也走 ``panel._qlib_features`` 的 ``D.features``），但 qlib 的 .bin
是 float32，其算子链**全程 float32**；FM 侧把同一批值升成 float64 再算。
逐点差即线路宽度差，实测：FM ≡ float64 精算（3.8e-16），qlib ≡ float32 精算
（逐位 0.0）。两类伪差：

1. **相消放大**：输出是两个近等值之差的商时（如 fm006 的
   ``(close-vwap)/vwap ≈ 1e-4``），操作数量级（价格 ~1e2）上的 ~ulp32 绝对
   误差（~1e-7）除以小输出，成大**相对**差——纯相对容差会把它判死。
   故垫 ``_ATOL``（实测全库最大绝对伪差 1.37e-7，7 倍余量；换标的池/市场
   ——价格量级变——须复核）。
2. **离散放大（近并列秩翻转）**：``Rank``（两边同一个
   ``rolling(n, min_periods=1).rank(pct=True)`` 调用）的窗口里两个近等值
   在 float32 侧碰撞、float64 侧不碰时，平均秩差半个身位，产物差恰为
   1/n 的整数/半整数倍——实钉 fm026=1/18、fm027=1/12、fm091=1/48，全部
   精确命中。占比全库 ≤ 2.3%。

对不上 = 真的语义两说（算子/窗宽/符号错——会在值域层大面积红），必须修映射
或把算子退回 gap——不许把语义对不上的翻译留在 metadata 里。第 2 类伪差
**按因子白名单**走二层放行（``_TIE_BAD_FRACTION_ALLOWANCE``：占比上限 +
幅度 ≤ 0.1 + 全秩 Spearman ≥ 0.999——防稀疏但系统性的失真；伪差对
Spearman 的影响约 1e-9 量级）。白名单外的因子出现任何超差即红：那是
新现象，先钻探定性（float32/float64 复算 + 1/n 量子核验）再决定是伪差
还是语义两说，不许顺手放宽阈值。

第二个测试（``test_unexercised_op_mappings_match_on_real_data``）用探针
表达式补齐 31 条翻译**没实测到**的 9 个映射专支（Delay/TsMax/TsMin/
TsArgMax/SignedPower/Log/Med/Max/Min），防止这些分支在覆盖阴影里腐掉。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent.factorminer import (  # noqa: E402
    qlib_export,
    runtime,
)
from backend.services.engine.alpha_agent.factorminer.library import LIBRARY  # noqa: E402
from backend.services.engine.alpha_agent.statistical import (  # noqa: E402
    panel as panel_mod,
)

QLIB_DIR = os.environ.get("QM_TEST_QLIB_DIR", "/data/qlib/cn_data")

N_INSTRUMENTS = 40
START, END = "2024-01-02", "2024-06-28"
#: qlib 侧“深跑道”取数起点：qlib 自动扩史在第一根输出上有欠一档的边角
#: （实测首日窗宽差 1）；从一年前取、本地裁到 [START, END] 比较，两边
#: 滚动窗在窗口内看到的都是稳态数据。FM 侧（runtime）自带 warmup 回退。
DEEP_START = "2023-01-02"

#: 值域容差：|diff| ≤ _ATOL + _RTOL·|qlib|（坐标见模块 docstring 第 1 类伪差）
_ATOL = 1e-6
_RTOL = 1e-4

#: 第 2 类伪差（近并列秩翻转）**按因子白名单**放行：fid → 超差格占比上限。
#: 数值 = 2026-10-11 钻探实测钉值 × ~1.5（026 实钉 30 格≈0.6%、027 实钉
#: 107 格≈2.3%、091 实钉 2 格）。白名单外任何因子出现超差 = 新现象，
#: 钻探定性（1/n 量子 + float32/float64 复算）后**再**往表里添——先证后放。
_TIE_BAD_FRACTION_ALLOWANCE: dict[str, float] = {
    "026": 0.010,
    "027": 0.035,
    "091": 0.001,
}
#: 白名单放行的公共闸：幅度上限与全秩秩相关下限
_MAX_TIE_BAD_DIFF = 0.1
_MIN_SPEARMAN = 0.999


def _compare_factor(fid: str, got: pd.Series, exp_col: pd.Series) -> str | None:
    """FM 长序列 vs qlib 列（(instrument, datetime) 深跑道）→ 失败描述或 None。"""
    # qlib 侧换轴到 (trade_date, symbol) 与 FM 同轴；reindex 到 FM 的窗口
    # 网格即完成「深跑道 → 裁窗口」的动作
    exp = exp_col.swaplevel().sort_index()
    exp.index = exp.index.set_names(["trade_date", "symbol"])

    both = pd.DataFrame({"fm": got, "qlib": exp.reindex(got.index)})
    fm_ok = both["fm"].notna()
    qlib_ok = both["qlib"].notna()
    if not bool(fm_ok.sum()):
        return f"fm{fid}: FM 侧全为 NaN（取数/暖机异常）"
    # 掩码一致性**单向**检查（FM 出值 ⇒ qlib 必须出值）：反向（qlib 出值而
    # FM 为 NaN）是设计行为——窗口内新上市标的 FM 按 position<warmup 掩掉，
    # qlib 深跑道则有值，不构成分叉。
    fm_only = int((fm_ok & ~qlib_ok).sum())
    if fm_only:
        return f"fm{fid}: {fm_only} 格 FM 出值而 qlib 为 NaN（暖机语义分叉）"

    joined = both[fm_ok]
    fm_v = joined["fm"].to_numpy(dtype="float64")
    q_v = joined["qlib"].to_numpy(dtype="float64")
    diff = np.abs(fm_v - q_v)
    over = diff > _ATOL + _RTOL * np.abs(q_v)
    n_bad = int(over.sum())
    if n_bad == 0:
        return None
    frac = n_bad / len(joined)
    max_diff = float(diff[over].max())
    allowance = _TIE_BAD_FRACTION_ALLOWANCE.get(fid)
    if (
        allowance is not None
        and frac <= allowance
        and max_diff <= _MAX_TIE_BAD_DIFF
        and float(pd.Series(fm_v).corr(pd.Series(q_v), method="spearman"))
        >= _MIN_SPEARMAN
    ):
        return None
    return (
        f"fm{fid}: {n_bad}/{len(joined)} 格超差（占比 {frac:.2%}，最大绝对差 "
        f"{max_diff:.3e}）——白名单外因子若确为近并列秩翻转（差 = 1/n 量子），"
        "先用 float32/float64 复算钻探定性，再把实测占比写进 "
        "_TIE_BAD_FRACTION_ALLOWANCE；否则是语义两说，改映射或退 gap"
    )


@pytest.mark.integration
def test_translated_expressions_match_fm_evaluator_on_real_data() -> None:
    pytest.importorskip("qlib")
    if not Path(QLIB_DIR).is_dir():
        pytest.skip(f"qlib 数据目录不存在: {QLIB_DIR}（QM_TEST_QLIB_DIR 可覆盖）")

    import qlib
    from qlib.data import D

    qlib.init(provider_uri=QLIB_DIR, region="cn")

    coverage = qlib_export.library_coverage()
    assert coverage.n_translatable == 31, "翻译覆盖变了（对拍是 31 条的钉子）"
    instruments = panel_mod.qlib_instruments("csi300", qlib_dir=QLIB_DIR)[
        :N_INSTRUMENTS
    ]
    assert instruments, "csi300 标的池为空——检查 qlib 缓存"

    translated = dict(coverage.translatable)  # fid → qlib 表达式
    # qlib 侧一次取齐（列名 = 表达式），深跑道 + 本地裁剪
    qlib_frame = D.features(
        instruments,
        list(translated.values()),
        start_time=DEEP_START,
        end_time=END,
        freq="day",
    )

    failures: list[str] = []
    checked_total = 0
    for factor in LIBRARY:
        qlib_expr = translated.get(factor.fid)
        if qlib_expr is None:
            continue
        got = runtime.evaluate_window(
            factor.expression,
            instruments=instruments,
            start=START,
            end=END,
            qlib_dir=QLIB_DIR,
        )
        checked_total += int(got.notna().sum())
        failure = _compare_factor(factor.fid, got, qlib_frame[qlib_expr])
        if failure:
            failures.append(f"{failure}，翻译={qlib_expr}")

    assert checked_total > 31 * 1000, f"对拍点太少：{checked_total}"
    assert not failures, "翻译与 FM 求值器不等价：\n" + "\n".join(failures)


#: 31 条翻译实测未覆盖的 9 个映射专支（2026-10-11 算子谱比对：可翻译算子
#: 共 30 个，31 条只实测到 28 个）——探针表达式直接对拍这些分支。
_OP_PROBES: dict[str, str] = {
    "delay": "Delay($close, 5)",
    "tsmax": "TsMax($close, 10)",
    "tsmin": "TsMin($volume, 5)",
    "tsargmax": "TsArgMax($close, 5)",
    "signedpower": "SignedPower($volume, 0.5)",
    "log": "Log($volume)",
    "med": "Med($close, 5)",
    "max": "Max($close, 5)",
    "min": "Min($open, 4)",
}


@pytest.mark.integration
def test_unexercised_op_mappings_match_on_real_data() -> None:
    pytest.importorskip("qlib")
    if not Path(QLIB_DIR).is_dir():
        pytest.skip(f"qlib 数据目录不存在: {QLIB_DIR}（QM_TEST_QLIB_DIR 可覆盖）")

    import qlib
    from qlib.data import D

    qlib.init(provider_uri=QLIB_DIR, region="cn")

    instruments = panel_mod.qlib_instruments("csi300", qlib_dir=QLIB_DIR)[
        :N_INSTRUMENTS
    ]
    assert instruments, "csi300 标的池为空——检查 qlib 缓存"

    translated: dict[str, str] = {}
    for name, fm_expr in _OP_PROBES.items():
        probed = qlib_export.export(fm_expr)
        assert probed.ok, f"探针 {name}（{fm_expr}）翻译失败：{probed.gap}"
        translated[name] = probed.expression  # type: ignore[assignment]

    qlib_frame = D.features(
        instruments,
        list(translated.values()),
        start_time=DEEP_START,
        end_time=END,
        freq="day",
    )

    failures: list[str] = []
    checked_total = 0
    for name, fm_expr in _OP_PROBES.items():
        got = runtime.evaluate_window(
            fm_expr,
            instruments=instruments,
            start=START,
            end=END,
            qlib_dir=QLIB_DIR,
        )
        checked_total += int(got.notna().sum())
        failure = _compare_factor(name, got, qlib_frame[translated[name]])
        if failure:
            failures.append(f"{failure}（翻译={translated[name]}）")

    assert checked_total > len(_OP_PROBES) * 1000, f"对拍点太少：{checked_total}"
    assert not failures, "探针映射与 FM 求值器不等价：\n" + "\n".join(failures)
