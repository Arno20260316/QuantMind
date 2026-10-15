"""因子报告：纯函数与快照切片单测（无 DB、无 parquet 依赖）。

覆盖三处容易错的口径：
1. 秩相关（Spearman）——因子报告的 IC 与相关性都建立在它上面，符号/边界错会整页失真
2. 相关性切片——页面按 [当前因子, ...高相关因子] 取子矩阵，顺序必须与请求一致
3. 高相关因子排序——按 |ρ| 降序、排除自身（用于「这只因子是不是别人的复制品」）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.engine.factor_report import service


@pytest.mark.parametrize(
    "reverse,expected",
    [(False, 1.0), (True, -1.0)],
)
def test_spearman_单调情形(reverse, expected):
    import numpy as np

    n = 25  # _spearman 要求 ≥20 个样本（少于该数视为不可靠）
    a = np.arange(n, dtype=float)
    b = a[::-1].copy() if reverse else a * 3.0 + 1.0

    assert service._spearman(a, b) == pytest.approx(expected)


def test_spearman_常量序列返回_nan():
    import numpy as np

    v = service._spearman(np.arange(30, dtype=float), np.full(30, 7.0))
    assert v != v  # NaN —— 常量序列的秩相关未定义，必须判在转秩之前


def test_spearman_样本过少返回_nan():
    import numpy as np

    v = service._spearman(np.array([1.0, 2.0]), np.array([2.0, 1.0]))
    assert v != v


def test_rank_与_scipy_口径一致():
    import numpy as np

    x = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
    assert service._rank(x).tolist() == [3.0, 1.0, 2.0, 5.0, 4.0]


# ── 快照切片（monkeypatch 掉快照读取，避免依赖真实数据文件）──────────────

_FAKE_SNAPSHOT = {
    "meta": {"horizon": "fwd_ret_5"},
    "factors": [
        {"name": "f1", "library": "alpha158", "ic_mean": 0.01},
        {"name": "f2", "library": "gtja191", "ic_mean": -0.02},
        {"name": "f3", "library": "alpha101", "ic_mean": 0.03},
    ],
    "correlation": {
        "factors": ["f1", "f2", "f3"],
        "matrix": [
            [1.0, -0.9, 0.2],
            [-0.9, 1.0, -0.1],
            [0.2, -0.1, 1.0],
        ],
    },
}


@pytest.fixture()
def fake_snapshot(monkeypatch):
    monkeypatch.setattr(service, "load_snapshot", lambda *a, **k: json.loads(json.dumps(_FAKE_SNAPSHOT)))


def test_相关性切片保持请求顺序(fake_snapshot):
    res = service.correlation_slice("alpha_library", ["f3", "f1"])

    assert res["available"] is True
    assert res["factors"] == ["f3", "f1"]           # 顺序 = 请求顺序（页面靠它对齐标签）
    assert res["matrix"][0][1] == pytest.approx(0.2)
    assert res["matrix"][1][0] == pytest.approx(0.2)


def test_相关性切片忽略不存在的因子(fake_snapshot):
    res = service.correlation_slice("alpha_library", ["f2", "不存在"])

    assert res["factors"] == ["f2"]
    assert len(res["matrix"]) == 1


def test_高相关因子按绝对值降序且排除自身(fake_snapshot):
    res = service.top_correlated("alpha_library", "f1", top=5)

    assert [x["name"] for x in res] == ["f2", "f3"]   # |-0.9| > |0.2|
    assert res[0]["corr"] == pytest.approx(-0.9)


def test_快照缺失时返回未生成(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(service, "snapshot_path", lambda *a, **k: tmp_path / "nope.json")

    assert service.load_snapshot("alpha_library") is None
    res = service.correlation_slice("alpha_library", ["f1"])
    assert res["available"] is False
    assert "尚未生成" in res["reason"]


# ── 非有限值净化（2026-10-15 factor1300 事故回归）──────────────────────────
#
# 背景：全退化因子（整列 NaN）的 ic_mean/ls_mean 被构建器写成裸 NaN token；
# starlette 的 JSONResponse 用 allow_nan=False，快照里一个 NaN 就让
# /factor-report/summary 整个 500（页面回退展示旧数据集数据），detail 同样。


def test_json_safe_递归净化非有限值():
    dirty = {
        "a": float("nan"),
        "b": [1.0, float("inf"), float("-inf"), {"c": float("nan")}],
        "d": (2.5, 3.5),  # tuple → list
        "e": 1,           # int 不是 float，不动
        "f": "text",
    }
    clean = service._json_safe(dirty)

    assert clean == {
        "a": None,
        "b": [1.0, None, None, {"c": None}],
        "d": [2.5, 3.5],
        "e": 1,
        "f": "text",
    }
    # 净化后的对象必须走通 starlette 的序列化约束
    json.dumps(clean, allow_nan=False)


def test_finite_or_none_单值语义():
    assert service._finite_or_none(float("nan")) is None
    assert service._finite_or_none(float("inf")) is None
    assert service._finite_or_none(None) is None
    assert service._finite_or_none("x") is None
    assert service._finite_or_none(1.25) == 1.25


def test_load_snapshot_净化存量毒快照(monkeypatch, tmp_path: Path):
    p = tmp_path / "factor_report.json"
    # 模拟存量毒文件：py json.dump 默认把 NaN 写成裸 token（构建器的旧行为）
    p.write_text(
        json.dumps(
            {"factors": [{"name": "x", "ic_mean": float("nan"), "ls_mean": float("inf")}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "snapshot_path", lambda *a, **k: p)

    snap = service.load_snapshot("factor1300")

    assert snap["factors"][0]["ic_mean"] is None
    assert snap["factors"][0]["ls_mean"] is None
    json.dumps(snap, allow_nan=False)  # 端点序列化路径不再 500


def test_load_snapshot_文件重写后缓存失效(monkeypatch, tmp_path: Path):
    import os

    p = tmp_path / "factor_report.json"
    p.write_text('{"meta": {"n_factors": 1}}', encoding="utf-8")
    monkeypatch.setattr(service, "snapshot_path", lambda *a, **k: p)

    assert service.load_snapshot("alpha_library")["meta"]["n_factors"] == 1
    p.write_text('{"meta": {"n_factors": 2}}', encoding="utf-8")
    # mtime 撞同纳秒会让缓存误命中 —— 显式拨到另一个纪元确保判据被触及
    os.utime(p, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    assert service.load_snapshot("alpha_library")["meta"]["n_factors"] == 2


def test_相关性切片容忍None值(monkeypatch):
    snap = json.loads(json.dumps(_FAKE_SNAPSHOT))
    snap["correlation"]["matrix"][0][1] = None
    snap["correlation"]["matrix"][1][0] = None
    monkeypatch.setattr(service, "load_snapshot", lambda *a, **k: snap)

    res = service.correlation_slice("alpha_library", ["f1", "f2"])

    assert res["matrix"][0][1] is None
    json.dumps(res, allow_nan=False)
