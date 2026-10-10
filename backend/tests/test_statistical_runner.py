"""统计派 runner（``scripts/alpha_agent/run_statistical_mining.py``）契约测试。

runner 是算法 manifest 声明的子进程入口（algorithm_registry 的
``test_runner_files_exist_on_disk`` 只查文件在不在）——这里钉它的**可执行
契约**：市场闸、窗口解析（env 优先 / 默认兜底）、落库 metadata 的审计字段。

不测整条落库链路：那要写生产 ``rd_agent_factors`` 表（测试不碰生产行，
既往教训见 DB 清理相关事故）。落库由真实任务验收覆盖。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "alpha_agent" / "run_statistical_mining.py"


def _load_runner():
    """按路径装载 runner 模块（scripts/ 不是包，走 importlib）。"""
    spec = importlib.util.spec_from_file_location("_runner_under_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _load_runner()


class TestInvocationContract:
    def test_non_a_share_market_exits_2(self):
        """非 A 股市场在**取数之前**拒绝（exit 2）——manifest 声明 markets 收窄，
        runner 是最后一道闸，不能等跑完数据集才说市场不对。"""
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--task-id", "t_runner_guard",
                    "--user-id", "u_test",
                    "--market", "hong_kong",
                    "--log-dir", tmp,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "只支持 A 股" in (proc.stdout + proc.stderr)


class TestWindowResolution:
    def test_env_windows_win(self, runner, monkeypatch):
        """任务 env 给了窗口就用它，并如实标 kind=custom。"""
        monkeypatch.setattr(runner, "_data_end", lambda: None)
        monkeypatch.setenv("QM_MINING_WINDOW_START", "2024-01-02")
        monkeypatch.setenv("QM_MINING_WINDOW_END", "2025-06-30")
        monkeypatch.setenv("QM_VALIDATION_WINDOW_START", "2025-07-01")
        monkeypatch.setenv("QM_VALIDATION_WINDOW_END", "2025-12-31")

        mining, validation, kind = runner._resolve_windows()

        assert kind == "custom"
        assert mining.iso() == ("2024-01-02", "2025-06-30")
        assert validation.iso() == ("2025-07-01", "2025-12-31")

    def test_env_mining_only_marks_custom_mining_only(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "_data_end", lambda: None)
        monkeypatch.setenv("QM_MINING_WINDOW_START", "2024-01-02")
        monkeypatch.setenv("QM_MINING_WINDOW_END", "2025-06-30")
        monkeypatch.delenv("QM_VALIDATION_WINDOW_START", raising=False)
        monkeypatch.delenv("QM_VALIDATION_WINDOW_END", raising=False)

        _mining, validation, kind = runner._resolve_windows()

        assert kind == "custom_mining_only"
        assert validation is None, "没给检验窗就是 None——不拿默认检验窗冒充用户选择"

    def test_no_env_falls_back_to_default_4y1y(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "_data_end", lambda: None)
        for key in (
            "QM_MINING_WINDOW_START",
            "QM_MINING_WINDOW_END",
            "QM_VALIDATION_WINDOW_START",
            "QM_VALIDATION_WINDOW_END",
        ):
            monkeypatch.delenv(key, raising=False)

        mining, validation, kind = runner._resolve_windows()

        assert kind == "default_4y1y"
        assert validation is not None, "默认路径挖掘+检验双窗齐备"
        start, end = mining.iso()
        assert start < end


class TestFactorMetadata:
    def _report(self, runner):
        from backend.services.engine.alpha_agent.statistical import engine as eng

        horizons = (
            eng.HorizonStats(
                horizon=1, n_days=80, ic_mean=0.031, rank_ic_mean=0.041,
                icir=0.9, rank_ic_t=3.1, rank_ic_p=0.002, rank_ic_win_rate=0.6,
                fdr_pass=True,
            ),
            eng.HorizonStats(
                horizon=5, n_days=75, ic_mean=0.012, rank_ic_mean=0.018,
                icir=0.4, rank_ic_t=1.2, rank_ic_p=0.23, rank_ic_win_rate=0.52,
                fdr_pass=False,
            ),
        )
        return eng.CandidateReport(
            key="reversal_5d", name="5 日反转", family="reversal",
            formulation="close[t-5]/close[t] - 1", source="Jegadeesh(1990)",
            expression="Ref($close,5)/$close-1", min_history=5,
            horizons=horizons, best_horizon=1, rank_ic_mean=0.041, icir=0.9,
            t_stat=3.1, p_value=0.002, fdr_pass=True, fdr_horizons=(1,),
            oos_verified=True, oos_horizon=1, oos_rank_ic=0.033, oos_n_days=60,
            quantile_monotonicity=0.9, spread_mean=0.004, selected=True,
        )

    def test_metadata_carries_full_audit(self, runner):
        """落库 metadata 必须自带口径：窗口/中性化/过滤/分周期统计量。

        任务详情页与因子列表只能靠这份 metadata 复核 IC——缺一项就等于
        「数字在库里但没人能解释它怎么来的」。
        """
        audit = {
            "window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
            "neutralize_mode": "industry",
            "filter": {"n_kept": 4700},
        }
        meta = runner._factor_metadata(self._report(runner), task_id="t1", market="a_share", audit=audit)

        assert meta["source"] == "statistical"
        assert meta["algorithm"] == "statistical"
        assert meta["candidate_key"] == "reversal_5d"
        assert meta["literature"] == "Jegadeesh(1990)"
        assert meta["best_horizon"] == 1
        assert meta["oos_verified"] is True
        assert meta["window"] == audit["window"]
        assert meta["neutralize_mode"] == "industry"
        assert meta["filter"] == audit["filter"]
        # 分周期统计量：键是 str（JSON 对象键），值带 FDR 判定
        assert set(meta["horizons"]) == {"1", "5"}
        assert meta["horizons"]["1"]["fdr_pass"] is True
        assert meta["horizons"]["5"]["fdr_pass"] is False
        assert meta["horizons"]["1"]["n_days"] == 80

    def test_metadata_is_strict_json_with_nan_as_null(self, runner):
        """被闸门拒掉的周期 IC 是 NaN——metadata 必须是严格 JSON。

        实测事故：五条入选因子带着 ``NaN`` 落库，Postgres ``json`` 列整批拒收
        （``Token "NaN" is invalid``），入选 5 落库 0。NaN 的语义是「没数」，
        落成 ``null``（展示面「缺失一律 —」同一口径），不是 0。
        """
        import json  # noqa: PLC0415

        from backend.services.engine.alpha_agent.statistical import engine as eng  # noqa: PLC0415

        nan = float("nan")
        report = eng.CandidateReport(
            key="k", name="k", family="f", formulation="x", source="s",
            expression="$close", min_history=5,
            horizons=(
                eng.HorizonStats(
                    horizon=20, n_days=0, ic_mean=nan, rank_ic_mean=nan,
                    icir=nan, rank_ic_t=nan, rank_ic_p=nan, rank_ic_win_rate=nan,
                    fdr_pass=False,
                ),
            ),
            best_horizon=None, rank_ic_mean=nan, icir=nan, t_stat=nan, p_value=nan,
            fdr_pass=False, fdr_horizons=(), oos_verified=False, oos_horizon=None,
            oos_rank_ic=None, oos_n_days=0, quantile_monotonicity=None,
            spread_mean=None, reject_reason="样本内有效日不足", selected=False,
        )
        audit = {
            "window": {"mining_start": "2025-01-02", "mining_end": "2025-06-30"},
            "neutralize_mode": "industry",
            "filter": {"n_kept": 1},
            "universe": "csi300",
        }
        meta = runner._factor_metadata(report, task_id="t1", market="a_share", audit=audit)

        # allow_nan=False：严格 JSON 才许出门（裸 NaN 会被 Postgres/JSON.parse 拒）
        text = json.dumps(meta, ensure_ascii=False, allow_nan=False)
        assert json.loads(text)["horizons"]["20"]["rank_ic_mean"] is None
        assert meta["rank_icir"] is None
        assert meta["universe"] == "csi300"


class _RecordingPersistence:
    """持久化替身：记录调用，绝不碰生产 ``rd_agent_factors`` 表。"""

    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.metrics: list[dict] = []

    async def ensure_tables(self) -> None:
        return None

    async def save_factor(self, **kwargs) -> None:
        self.saved.append(kwargs)

    async def update_factor_metrics(self, **kwargs) -> None:
        self.metrics.append(kwargs)


def _horizon(runner):
    from backend.services.engine.alpha_agent.statistical import engine as eng

    return eng.HorizonStats(
        horizon=1, n_days=80, ic_mean=0.031, rank_ic_mean=0.041, icir=0.9,
        rank_ic_t=3.1, rank_ic_p=0.002, rank_ic_win_rate=0.6, fdr_pass=True,
    )


def _tier3_report(runner):
    """入选候选（带全套 Tier 3 证据）——与引擎产出的字段一一对应。"""
    from backend.services.engine.alpha_agent.statistical import engine as eng

    return eng.CandidateReport(
        key="reversal_5d", name="5 日反转", family="reversal",
        formulation="close[t-5]/close[t] - 1", source="Jegadeesh(1990)",
        expression="Ref($close,5)/$close-1", min_history=5,
        horizons=(_horizon(runner),), best_horizon=1, rank_ic_mean=0.041,
        icir=0.9, t_stat=3.1, p_value=0.002, fdr_pass=True, fdr_horizons=(1,),
        oos_verified=True, oos_horizon=1, oos_rank_ic=0.033, oos_n_days=60,
        quantile_monotonicity=0.9, spread_mean=0.004, selected=True,
        fold_ics=(0.05, 0.04, 0.03, 0.05), fold_sign_consistency=1.0,
        reselect_rate=2 / 3, reselect_hits=2, reselect_checkpoints=3,
        ic_half_life=float("nan"), ic_decay_slope=-0.12,
        max_abs_corr=0.31,
    )


def _composite_report(runner, *, eligible: bool):
    from backend.services.engine.alpha_agent.statistical import engine as eng

    return eng.CompositeReport(
        key="ic_weighted_composite", name="IC 加权合成因子", horizon=1,
        components=("reversal_5d", "momentum_20d"), weights=(0.6, -0.4),
        component_rank_ics=(0.06, -0.04), z_window=60,
        expression="0.600000*(...)-0.400000*(...)", min_history=64,
        horizons=(_horizon(runner),), best_horizon=1, rank_ic_mean=0.051,
        icir=1.1, t_stat=3.9, p_value=0.0003, oos_verified=True, oos_horizon=1,
        oos_rank_ic=0.044, oos_n_days=60, quantile_monotonicity=0.8,
        spread_mean=0.005, eligible=eligible,
        reject_reason="" if eligible else "样本外衰减过大",
    )


class TestTier3Metadata:
    def test_factor_metadata_carries_tier3_evidence(self, runner):
        """折/再选择/半衰期/相关性证据必须随 metadata 落库——否则「为什么
        这条因子被去重挤掉/被稳定性闸拒」在库外无据可查。"""
        import json  # noqa: PLC0415

        audit = {"window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
                 "neutralize_mode": "industry", "filter": {"n_kept": 1}}

        meta = runner._factor_metadata(
            _tier3_report(runner), task_id="t1", market="a_share", audit=audit,
            task_fingerprint="fp-abc", identity_scheme="config",
        )

        assert meta["fold_ics"] == [0.05, 0.04, 0.03, 0.05]
        assert meta["reselect_rate"] == pytest.approx(2 / 3)
        assert meta["reselect_checkpoints"] == 3
        assert meta["ic_decay_slope"] == pytest.approx(-0.12)
        assert meta["max_abs_corr"] == pytest.approx(0.31)
        assert meta["identity_scheme"] == "config"
        assert meta["task_fingerprint"] == "fp-abc"
        # NaN 半衰期（不衰减/算不出）→ null，不是 0
        assert meta["ic_half_life"] is None
        json.dumps(meta, ensure_ascii=False, allow_nan=False)

    def test_composite_metadata_declares_what_it_did_not_do(self, runner):
        """合成因子 metadata 必须写明「不进 FDR / 未中性化」——这是报告数字与
        落库表达式等价的前提，藏起来就等于让回测方以为是同口径。"""
        import json  # noqa: PLC0415

        audit = {"window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
                 "neutralize_mode": "industry", "filter": {"n_kept": 1},
                 "universe": "csi300"}

        meta = runner._composite_metadata(
            _composite_report(runner, eligible=True),
            task_id="t1", market="a_share", audit=audit,
            task_fingerprint="fp-abc", identity_scheme="config",
        )

        assert meta["composite"] is True
        assert meta["components"] == ["reversal_5d", "momentum_20d"]
        assert meta["weights"] == [0.6, -0.4], "负权重是信息（方向），不许被归一吃掉"
        assert meta["weight_horizon"] == 1 and meta["z_window"] == 60
        assert meta["fdr_applied"] is False and meta["neutralized"] is False
        assert meta["universe"] == "csi300"
        json.dumps(meta, ensure_ascii=False, allow_nan=False)


class TestPersistSelectedWiring:
    """身份（P3 指纹）+ 合成落库闸：用替身记账，不写生产表。"""

    def _run(self, runner, monkeypatch, *, composite, fingerprint: str):
        import asyncio  # noqa: PLC0415
        import types  # noqa: PLC0415

        from backend.services.engine.qlib_app.services import (  # noqa: PLC0415
            rd_agent_persistence,
        )

        recorder = _RecordingPersistence()
        monkeypatch.setattr(
            rd_agent_persistence, "RDAgentFactorPersistence", lambda: recorder
        )
        result = types.SimpleNamespace(
            candidates=(_tier3_report(runner),), composite=composite
        )
        counts = asyncio.run(
            runner._persist_selected(
                result, task_id="t1", user_id="u1", market="a_share",
                universe="csi300", audit={
                    "window": {"mining_start": "2021-01-01",
                               "mining_end": "2024-12-31"},
                },
                task_fingerprint=fingerprint,
            )
        )
        return counts, recorder

    def test_identity_and_eligible_composite_persisted(self, runner, monkeypatch):
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            dedup as dd,
        )

        counts, recorder = self._run(
            runner, monkeypatch,
            composite=_composite_report(runner, eligible=True),
            fingerprint="fp-abc",
        )

        assert counts == (1, 1)
        expected = [
            dd.stable_factor_id(
                user_id="u1", market="a_share", universe="csi300",
                candidate_key=key, task_fingerprint="fp-abc", task_id="t1",
            )[0]
            for key in ("reversal_5d", "ic_weighted_composite")
        ]
        assert [c["factor_id"] for c in recorder.saved] == expected
        assert [c["factor_code"] for c in recorder.saved] == [
            "Ref($close,5)/$close-1",
            "0.600000*(...)-0.400000*(...)",
        ], "落的是 qlib 表达式（回测链路消费它），不是 redis 出的因子值"
        assert recorder.saved[1]["metadata"]["identity_scheme"] == "config"
        assert len(recorder.metrics) == 2, "因子指标与因子行成对落"

    def test_ineligible_composite_is_accounted_not_persisted(self, runner, monkeypatch):
        """合成因子样本外闸不过 → 只记账不落库（闸的实际作用），返回值如实。"""
        counts, recorder = self._run(
            runner, monkeypatch,
            composite=_composite_report(runner, eligible=False),
            fingerprint="fp-abc",
        )

        assert counts == (1, 0)
        assert len(recorder.saved) == 1
        assert "ic_weighted_composite" not in [
            c["factor_name"] for c in recorder.saved
        ]

    def test_missing_fingerprint_falls_back_to_task_identity(self, runner, monkeypatch):
        """缺指纹：身份退回任务级（scheme=task），metadata 可追溯——不装作
        是配置身份（那会让不同任务互相幂等覆盖）。"""
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            dedup as dd,
        )

        counts, recorder = self._run(
            runner, monkeypatch,
            composite=_composite_report(runner, eligible=True),
            fingerprint="",
        )

        assert counts == (1, 1)
        meta = recorder.saved[0]["metadata"]
        assert meta["identity_scheme"] == dd.SCHEME_TASK
        assert meta["task_fingerprint"] is None
        fallback_id, _ = dd.stable_factor_id(
            user_id="u1", market="a_share", universe="csi300",
            candidate_key="reversal_5d", task_fingerprint="", task_id="t1",
        )
        assert recorder.saved[0]["factor_id"] == fallback_id
