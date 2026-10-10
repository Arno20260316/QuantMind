"""分钟原生挖掘的 RDLoopWrapper 接线契约（移植 P1.5）。

包装层做三件事，本测试逐条钉死：

1. **频率解析**：``QM_MINING_FREQ`` 缺省 day；非法值 / 非 A 股市场选分钟 → 显式
   报错（绝不静默按日线跑出"自称分钟因子"的东西）；
2. **分钟数据面**：``_generate_minute_h5`` 从 provider bins 产 1min MultiIndex h5
   （与因子评估读的是同一份 bin）；必须显式窗口且落在分钟覆盖内；debug 变体与
   全量变体分开缓存；
3. **``_ensure_data_file`` 分钟分支**：落 ``daily_pv.h5``（debug/all 两份）、
   跳过日线种子因子、**不动** ``~/.qlib`` 全局 symlink（并发日线任务共用），
   生成失败即 ``MinuteDataError``；日线任务行为零变化。

合成数据复用 ``test_minute_qlib_provider`` 的构造器（同一套 bin 口径），
provider 走真实 ``build_minute_provider``——不用手搭目录冒充。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.services.engine import minute_qlib_provider as MP
from backend.services.engine.rd_agent.rd_loop_wrapper import (
    MinuteDataError,
    RDLoopWrapper,
)
from backend.tests.test_minute_qlib_provider import (
    BARS,
    DAY1,
    DAY2,
    HOLE_AFTER,
    _write_day_provider,
    _write_source,
)

_MINUTE_FIELDS = {"$open", "$high", "$low", "$close", "$volume", "$amount", "$vwap", "$factor"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清掉窗口/频率 env：日线默认行为与分钟场景各自显式声明。"""
    for key in (
        "QM_MINING_FREQ",
        "QM_MINING_WINDOW_START",
        "QM_MINING_WINDOW_END",
        "QM_VALIDATION_WINDOW_START",
        "QM_VALIDATION_WINDOW_END",
        "QM_MINING_DATASETS",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def minute_provider(tmp_path: Path) -> Path:
    """真实构建的合成分钟 provider（build_minute_provider 产物）。"""
    source = _write_source(tmp_path)
    day = _write_day_provider(tmp_path)
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    return provider


@pytest.fixture()
def hole_provider(tmp_path: Path) -> Path:
    """带 58 天空洞的 provider（源里 DAY2 之后直跳 HOLE_AFTER）。"""
    source = _write_source(tmp_path, days=(DAY1, DAY2, HOLE_AFTER))
    day = _write_day_provider(
        tmp_path, days=(DAY1, DAY2, HOLE_AFTER, date(2026, 3, 5))
    )
    provider = tmp_path / "qlib" / "cn_data_1min"
    MP.build_minute_provider(
        provider_dir=provider, source_dir=source, day_provider_dir=day
    )
    return provider


def _set_window(monkeypatch, start, end) -> None:
    monkeypatch.setenv("QM_MINING_WINDOW_START", start.isoformat())
    monkeypatch.setenv("QM_MINING_WINDOW_END", end.isoformat())


# ── 1. 频率解析 ─────────────────────────────────────────────────────


def test_freq_defaults_to_day_and_reads_env(monkeypatch):
    wrapper = RDLoopWrapper("a_share")
    assert wrapper._freq() == "day"
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    assert wrapper._freq() == "1min"
    monkeypatch.setenv("QM_MINING_FREQ", "")
    assert wrapper._freq() == "day"


def test_freq_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("QM_MINING_FREQ", "5min")
    with pytest.raises(ValueError) as exc:
        RDLoopWrapper("a_share")._freq()
    assert "5min" in str(exc.value)


def test_freq_minute_rejected_for_non_a_share(monkeypatch):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    with pytest.raises(ValueError) as exc:
        RDLoopWrapper("hong_kong")._freq()
    assert "A 股" in str(exc.value)


# ── 2. 分钟 h5 生成 ─────────────────────────────────────────────────


def test_generate_minute_h5_requires_explicit_window(tmp_path, minute_provider):
    """分钟挖掘必须显式窗口：默认窗口是日线尺度（2016 年起），不静默截断。"""
    wrapper = RDLoopWrapper("a_share")
    with pytest.raises(MinuteDataError) as exc:
        wrapper._generate_minute_h5(minute_provider, str(tmp_path / "x.h5"))
    assert "显式指定" in str(exc.value)


def test_generate_minute_h5_rejects_window_outside_coverage(
    tmp_path, minute_provider, monkeypatch
):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1.replace(year=2020), DAY1.replace(month=2))
    with pytest.raises(MinuteDataError) as exc:
        RDLoopWrapper("a_share")._generate_minute_h5(minute_provider, str(tmp_path / "x.h5"))
    assert "超出分钟数据覆盖" in str(exc.value)


def test_generate_minute_h5_rejects_window_spanning_hole(
    tmp_path, hole_provider, monkeypatch
):
    """跨空洞的窗口在覆盖内也拦：起点 2026-01-05 在覆盖内，但中间 58 天没数据。"""
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, HOLE_AFTER)
    with pytest.raises(MinuteDataError) as exc:
        RDLoopWrapper("a_share")._generate_minute_h5(
            hole_provider, str(tmp_path / "x.h5")
        )
    msg = str(exc.value)
    assert "空洞" in msg
    assert f"{DAY2} ~ {HOLE_AFTER}" in msg
    assert f"可用连续块 {HOLE_AFTER} ~ {HOLE_AFTER}" in msg


def test_generate_minute_h5_accepts_window_inside_usable_block(
    tmp_path, hole_provider, monkeypatch
):
    """空洞之后的连续块可正常挖掘（default 分钟窗口就是这一段）。"""
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, HOLE_AFTER, HOLE_AFTER)
    out = tmp_path / "usable.h5"
    assert RDLoopWrapper("a_share")._generate_minute_h5(hole_provider, str(out)) is True
    frame = pd.read_hdf(out, key="data")
    assert sorted(frame.index.get_level_values(1).unique()) == ["sh600000", "sz000001"]
    assert len(frame) == 2 * len(BARS)


def test_generate_minute_h5_writes_minute_indexed_frame(
    tmp_path, minute_provider, monkeypatch
):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    out = tmp_path / "target" / "daily_pv.h5"
    out.parent.mkdir()

    assert RDLoopWrapper("a_share")._generate_minute_h5(minute_provider, str(out)) is True

    frame = pd.read_hdf(out, key="data")
    assert isinstance(frame.index, pd.MultiIndex)
    assert frame.index.names[1] == "instrument"
    assert set(frame.columns) == _MINUTE_FIELDS
    assert sorted(frame.index.get_level_values(1).unique()) == ["sh600000", "sz000001"]
    assert len(frame) == 2 * len(BARS) * 2  # 2 标的 × 2 日 × 5 bar
    # 时间索引的粒度是分钟（首 bar = 09:30）
    assert frame.index.get_level_values(0).min().strftime("%H:%M") == "09:30"
    # 共享缓存落在 provider 目录内（provider 原子换入 → 缓存随之失效）
    cache = minute_provider / "h5_cache" / f"minute_pv_all_{DAY1}_{DAY2}.h5"
    assert cache.is_file()


def test_generate_minute_h5_uses_cache_on_second_call(
    tmp_path, minute_provider, monkeypatch
):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    wrapper = RDLoopWrapper("a_share")
    out_a = tmp_path / "a.h5"
    out_b = tmp_path / "b.h5"

    assert wrapper._generate_minute_h5(minute_provider, str(out_a)) is True
    cache = minute_provider / "h5_cache" / f"minute_pv_all_{DAY1}_{DAY2}.h5"
    stamp = cache.stat().st_mtime_ns
    assert wrapper._generate_minute_h5(minute_provider, str(out_b)) is True

    assert cache.stat().st_mtime_ns == stamp  # 未重建
    assert out_b.is_file()
    assert pd.read_hdf(out_b, key="data").index.equals(pd.read_hdf(out_a, key="data").index)


def test_generate_minute_h5_debug_variant_has_own_cache(
    tmp_path, minute_provider, monkeypatch
):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    out = tmp_path / "debug.h5"
    assert RDLoopWrapper("a_share")._generate_minute_h5(
        minute_provider, str(out), debug=True
    )
    assert (minute_provider / "h5_cache" / f"minute_pv_debug_{DAY1}_{DAY2}.h5").is_file()


def test_generate_minute_h5_returns_false_on_read_failure(
    tmp_path, minute_provider, monkeypatch
):
    """读取异常 → False（调用方响亮失败），不抛到外层假装 h5 已就位。"""
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)

    def boom(*args, **kwargs):
        raise RuntimeError("bin 损坏")

    monkeypatch.setattr(MP, "read_minute_frame", boom)
    assert RDLoopWrapper("a_share")._generate_minute_h5(minute_provider, str(tmp_path / "x.h5")) is False


# ── 3. 提示词与数据面接线 ───────────────────────────────────────────


def test_minute_prompt_block_only_for_minute_tasks(
    tmp_path, minute_provider, monkeypatch
):
    wrapper = RDLoopWrapper("a_share")
    assert wrapper._minute_prompt_block() == ""  # 日线任务：零注入

    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    wrapper._generate_minute_h5(minute_provider, str(tmp_path / "x.h5"))
    block = wrapper._minute_prompt_block()
    assert "1 分钟" in block
    assert f"{DAY1} ~ {DAY2}" in block
    assert "$amount" in block and "$vwap" in block


def test_ensure_data_file_minute_branch(tmp_path, minute_provider, monkeypatch, caplog):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    monkeypatch.setenv("QM_MINING_DATASETS", "l1_factors,features_daily")
    wrapper = RDLoopWrapper("a_share")
    monkeypatch.setattr(wrapper, "_ensure_minute_provider", lambda: minute_provider)

    global_link = Path.home() / ".qlib" / "qlib_data" / "cn_data"
    before = os.readlink(global_link) if global_link.is_symlink() else None

    with caplog.at_level(logging.WARNING):
        wrapper._ensure_data_file(str(tmp_path))

    all_h5 = tmp_path / "git_ignore_folder/factor_implementation_source_data/daily_pv.h5"
    debug_h5 = (
        tmp_path / "git_ignore_folder/factor_implementation_source_data_debug/daily_pv.h5"
    )
    assert all_h5.is_file() and debug_h5.is_file()
    frame = pd.read_hdf(all_h5, key="data")
    assert isinstance(frame.index, pd.MultiIndex) and set(frame.columns) == _MINUTE_FIELDS
    # 日线种子因子被跳过（引用了也是空列，不给 LLM 埋雷）
    assert wrapper._base_features_path is None
    # 全局 symlink 未被分钟任务碰过（并发日线任务共用它）
    after = os.readlink(global_link) if global_link.is_symlink() else None
    assert after == before
    # 数据集选择被如实忽略并记 warning（不是静默丢弃）
    assert any("忽略数据集选择" in rec.getMessage() for rec in caplog.records)


def test_ensure_data_file_minute_tolerates_unregistered_dataset_key(
    tmp_path, minute_provider, monkeypatch, caplog
):
    """未登记的数据集 key（手工 env）在分钟模式只记 warning，不炸整轮挖掘。"""
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    monkeypatch.setenv("QM_MINING_DATASETS", "typo_dataset")
    wrapper = RDLoopWrapper("a_share")
    monkeypatch.setattr(wrapper, "_ensure_minute_provider", lambda: minute_provider)

    with caplog.at_level(logging.WARNING):
        wrapper._ensure_data_file(str(tmp_path))

    assert any(
        "忽略数据集选择" in rec.getMessage() and "typo_dataset" in rec.getMessage()
        for rec in caplog.records
    )


def test_ensure_data_file_minute_failure_raises(tmp_path, minute_provider, monkeypatch):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    _set_window(monkeypatch, DAY1, DAY2)
    wrapper = RDLoopWrapper("a_share")
    monkeypatch.setattr(wrapper, "_ensure_minute_provider", lambda: minute_provider)
    monkeypatch.setattr(
        wrapper, "_generate_minute_h5", lambda *a, **kw: False
    )

    with pytest.raises(MinuteDataError) as exc:
        wrapper._ensure_data_file(str(tmp_path))
    assert "分钟数据面生成失败" in str(exc.value)


def test_ensure_data_file_day_mode_untouched_by_minute_branch(tmp_path, monkeypatch):
    """日线任务不进分钟分支：照常走 QuantDB/回退逻辑。

    日线分支的下游全部打桩（h5 复制 / 种子因子 / 全局 symlink 维护）——本测试
    只断言"没走分钟分支"，不顺带真拷贝生产 h5 或改动 ``~/.qlib``。
    """
    called: list[str] = []
    wrapper = RDLoopWrapper("a_share")
    monkeypatch.setattr(
        wrapper, "_ensure_minute_provider", lambda: called.append("minute") or Path("/nope")
    )
    monkeypatch.setattr(wrapper, "_resolve_quantdb_dir", lambda: None)
    monkeypatch.setattr(wrapper, "_copy_h5_source", lambda *a, **k: None)
    monkeypatch.setattr(wrapper.adapter, "generate_base_factors_json", lambda d: None)
    monkeypatch.setattr(os, "symlink", lambda *a, **k: None)
    monkeypatch.setattr(os, "unlink", lambda *a, **k: None)
    monkeypatch.setattr(os, "rename", lambda *a, **k: None)

    wrapper._ensure_data_file(str(tmp_path))

    assert called == []
