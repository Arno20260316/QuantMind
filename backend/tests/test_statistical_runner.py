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
                    "--task-id",
                    "t_runner_guard",
                    "--user-id",
                    "u_test",
                    "--market",
                    "hong_kong",
                    "--log-dir",
                    tmp,
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
        assert validation.iso()[0] > mining.iso()[1], (
            "默认双窗不共享边界日：检验窗必须从挖掘窗结束的次日开始（预留 label 缓冲，"
            "边界日收益不喂进两侧）"
        )


class TestFactorMetadata:
    def _report(self, runner):
        from backend.services.engine.alpha_agent.statistical import engine as eng

        horizons = (
            eng.HorizonStats(
                horizon=1,
                n_days=80,
                ic_mean=0.031,
                rank_ic_mean=0.041,
                icir=0.9,
                rank_ic_t=3.1,
                rank_ic_p=0.002,
                rank_ic_win_rate=0.6,
                fdr_pass=True,
                rank_ic_t_naive=1.9,
                rank_ic_t_lags=5,
            ),
            eng.HorizonStats(
                horizon=5,
                n_days=75,
                ic_mean=0.012,
                rank_ic_mean=0.018,
                icir=0.4,
                rank_ic_t=1.2,
                rank_ic_p=0.23,
                rank_ic_win_rate=0.52,
                fdr_pass=False,
            ),
        )
        return eng.CandidateReport(
            key="reversal_5d",
            name="5 日反转",
            family="reversal",
            formulation="close[t-5]/close[t] - 1",
            source="Jegadeesh(1990)",
            expression="Ref($close,5)/$close-1",
            min_history=5,
            horizons=horizons,
            best_horizon=1,
            rank_ic_mean=0.041,
            icir=0.9,
            t_stat=3.1,
            p_value=0.002,
            fdr_pass=True,
            fdr_horizons=(1,),
            oos_verified=True,
            oos_horizon=1,
            oos_rank_ic=0.033,
            oos_n_days=60,
            quantile_monotonicity=0.9,
            spread_mean=0.004,
            selected=True,
            factor_autocorr=0.85,
            factor_autocorr_days=90,
            rebalance_days=5,
            turnover_daily=0.12,
            ann_turnover=30.24,
            ic_net_mean=0.028,
            ic_net_days=78,
            regime_ic=(
                eng.RegimeStat(state="bull", n_days=40, rank_ic_mean=0.05, weak=False),
                eng.RegimeStat(
                    state="neutral", n_days=30, rank_ic_mean=0.01, weak=False
                ),
                eng.RegimeStat(state="bear", n_days=8, rank_ic_mean=-0.02, weak=False),
            ),
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
        meta = runner._factor_metadata(
            self._report(runner), task_id="t1", market="a_share", audit=audit
        )

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
        # T-FE-04：NW 带宽与朴素对照列随 metadata 落库——复核自相关修正
        # 是否生效（rank_ic_t 与 rank_ic_t_naive 分离才是正常）
        assert meta["horizons"]["1"]["rank_ic_t_lags"] == 5
        assert meta["horizons"]["1"]["rank_ic_t_naive"] == pytest.approx(1.9)
        # 未喂对照列（dataclass 默认 NaN）的周期 → 严格 JSON null，不是 0
        assert meta["horizons"]["5"]["rank_ic_t_naive"] is None
        # T-FE-03：交易性证据随 metadata 落库（调仓建议/换手/扣费净 IC/分状态）
        assert meta["factor_autocorr"] == pytest.approx(0.85)
        assert meta["factor_autocorr_days"] == 90, "ρ₁ 的配对日数须落库（证据闸可复核）"
        assert meta["rebalance_days"] == 5
        assert meta["turnover_daily"] == pytest.approx(0.12)
        assert meta["ann_turnover"] == pytest.approx(30.24)
        assert meta["ic_net_mean"] == pytest.approx(0.028)
        assert meta["ic_net_days"] == 78
        assert meta["regime_ic"]["bull"] == {
            "n_days": 40,
            "rank_ic_mean": 0.05,
            "weak": False,
        }
        assert set(meta["regime_ic"]) == {"bull", "neutral", "bear"}

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
            key="k",
            name="k",
            family="f",
            formulation="x",
            source="s",
            expression="$close",
            min_history=5,
            horizons=(
                eng.HorizonStats(
                    horizon=20,
                    n_days=0,
                    ic_mean=nan,
                    rank_ic_mean=nan,
                    icir=nan,
                    rank_ic_t=nan,
                    rank_ic_p=nan,
                    rank_ic_win_rate=nan,
                    fdr_pass=False,
                ),
            ),
            best_horizon=None,
            rank_ic_mean=nan,
            icir=nan,
            t_stat=nan,
            p_value=nan,
            fdr_pass=False,
            fdr_horizons=(),
            oos_verified=False,
            oos_horizon=None,
            oos_rank_ic=None,
            oos_n_days=0,
            quantile_monotonicity=None,
            spread_mean=None,
            reject_reason="样本内有效日不足",
            selected=False,
        )
        audit = {
            "window": {"mining_start": "2025-01-02", "mining_end": "2025-06-30"},
            "neutralize_mode": "industry",
            "filter": {"n_kept": 1},
            "universe": "csi300",
        }
        meta = runner._factor_metadata(
            report, task_id="t1", market="a_share", audit=audit
        )

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
        horizon=1,
        n_days=80,
        ic_mean=0.031,
        rank_ic_mean=0.041,
        icir=0.9,
        rank_ic_t=3.1,
        rank_ic_p=0.002,
        rank_ic_win_rate=0.6,
        fdr_pass=True,
    )


def _tier3_report(runner):
    """入选候选（带全套 Tier 3 证据）——与引擎产出的字段一一对应。"""
    from backend.services.engine.alpha_agent.statistical import engine as eng

    return eng.CandidateReport(
        key="reversal_5d",
        name="5 日反转",
        family="reversal",
        formulation="close[t-5]/close[t] - 1",
        source="Jegadeesh(1990)",
        expression="Ref($close,5)/$close-1",
        min_history=5,
        horizons=(_horizon(runner),),
        best_horizon=1,
        rank_ic_mean=0.041,
        icir=0.9,
        t_stat=3.1,
        p_value=0.002,
        fdr_pass=True,
        fdr_horizons=(1,),
        oos_verified=True,
        oos_horizon=1,
        oos_rank_ic=0.033,
        oos_n_days=60,
        quantile_monotonicity=0.9,
        spread_mean=0.004,
        selected=True,
        fold_ics=(0.05, 0.04, 0.03, 0.05),
        fold_sign_consistency=1.0,
        selection_score=0.031,  # 任意透传值（本测试只钉序列化，不重算公式）
        reselect_rate=2 / 3,
        reselect_hits=2,
        reselect_checkpoints=3,
        ic_half_life=float("nan"),
        ic_decay_slope=-0.12,
        max_abs_corr=0.31,
        op_count=3,
        literal_count=2,
    )


def _composite_report(runner, *, eligible: bool):
    from backend.services.engine.alpha_agent.statistical import engine as eng

    return eng.CompositeReport(
        key="ic_weighted_composite",
        name="IC 加权合成因子",
        horizon=1,
        components=("reversal_5d", "momentum_20d"),
        weights=(0.6, -0.4),
        component_rank_ics=(0.06, -0.04),
        z_window=60,
        expression="0.600000*(...)-0.400000*(...)",
        min_history=64,
        horizons=(_horizon(runner),),
        best_horizon=1,
        rank_ic_mean=0.051,
        icir=1.1,
        t_stat=3.9,
        p_value=0.0003,
        oos_verified=True,
        oos_horizon=1,
        oos_rank_ic=0.044,
        oos_n_days=60,
        quantile_monotonicity=0.8,
        spread_mean=0.005,
        eligible=eligible,
        reject_reason="" if eligible else "样本外衰减过大",
    )


class TestTier3Metadata:
    def test_factor_metadata_carries_tier3_evidence(self, runner):
        """折/再选择/半衰期/相关性证据必须随 metadata 落库——否则「为什么
        这条因子被去重挤掉/被稳定性闸拒」在库外无据可查。"""
        import json  # noqa: PLC0415

        audit = {
            "window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
            "neutralize_mode": "industry",
            "filter": {"n_kept": 1},
        }

        meta = runner._factor_metadata(
            _tier3_report(runner),
            task_id="t1",
            market="a_share",
            audit=audit,
            task_fingerprint="fp-abc",
            identity_scheme="config",
        )

        assert meta["fold_ics"] == [0.05, 0.04, 0.03, 0.05]
        assert meta["selection_score"] == pytest.approx(0.031), (
            "选择分（|RankIC|×折间符号一致性×简单性因子）是 Top-K 排序与"
            "去重步行序的依据，必须落库可审计（夹具值为任意透传，本测试只"
            "钉序列化；公式乘法由 tier3 复杂度惩罚测试另行钉住）"
        )
        # T-FE-05：结构复杂度计数随行落库——选择分里的简单性因子可复算
        assert meta["op_count"] == 3
        assert meta["literal_count"] == 2
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

        audit = {
            "window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
            "neutralize_mode": "industry",
            "filter": {"n_kept": 1},
            "universe": "csi300",
        }

        meta = runner._composite_metadata(
            _composite_report(runner, eligible=True),
            task_id="t1",
            market="a_share",
            audit=audit,
            task_fingerprint="fp-abc",
            identity_scheme="config",
        )

        assert meta["composite"] is True
        assert meta["components"] == ["reversal_5d", "momentum_20d"]
        assert meta["weights"] == [0.6, -0.4], "负权重是信息（方向），不许被归一吃掉"
        assert meta["weight_horizon"] == 1 and meta["z_window"] == 60
        assert meta["fdr_applied"] is False and meta["neutralized"] is False
        assert meta["universe"] == "csi300"
        # NW 审计列在合成路径同样随行（默认 NaN/0 → null/0，不炸严格 JSON）
        assert meta["horizons"]["1"]["rank_ic_t_naive"] is None
        assert meta["horizons"]["1"]["rank_ic_t_lags"] == 0
        # T-FE-03：合成报告未喂交易性证据 → 默认值（NaN→null / None），不出假数字
        assert meta["rebalance_days"] is None
        assert meta["regime_ic"] is None
        assert meta["factor_autocorr"] is None
        assert meta["factor_autocorr_days"] == 0
        assert meta["ic_net_days"] == 0
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
                result,
                task_id="t1",
                user_id="u1",
                market="a_share",
                universe="csi300",
                audit={
                    "window": {
                        "mining_start": "2021-01-01",
                        "mining_end": "2024-12-31",
                    },
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
            runner,
            monkeypatch,
            composite=_composite_report(runner, eligible=True),
            fingerprint="fp-abc",
        )

        assert counts == (1, 1)
        expected = [
            dd.stable_factor_id(
                user_id="u1",
                market="a_share",
                universe="csi300",
                candidate_key=key,
                task_fingerprint="fp-abc",
                task_id="t1",
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
            runner,
            monkeypatch,
            composite=_composite_report(runner, eligible=False),
            fingerprint="fp-abc",
        )

        assert counts == (1, 0)
        assert len(recorder.saved) == 1
        assert "ic_weighted_composite" not in [c["factor_name"] for c in recorder.saved]

    def test_missing_fingerprint_falls_back_to_task_identity(self, runner, monkeypatch):
        """缺指纹：身份退回任务级（scheme=task），metadata 可追溯——不装作
        是配置身份（那会让不同任务互相幂等覆盖）。"""
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            dedup as dd,
        )

        counts, recorder = self._run(
            runner,
            monkeypatch,
            composite=_composite_report(runner, eligible=True),
            fingerprint="",
        )

        assert counts == (1, 1)
        meta = recorder.saved[0]["metadata"]
        assert meta["identity_scheme"] == dd.SCHEME_TASK
        assert meta["task_fingerprint"] is None
        fallback_id, _ = dd.stable_factor_id(
            user_id="u1",
            market="a_share",
            universe="csi300",
            candidate_key="reversal_5d",
            task_fingerprint="",
            task_id="t1",
        )
        assert recorder.saved[0]["factor_id"] == fallback_id


class TestSpawnValidationWiring:
    """收尾薄切片 ``_spawn_validation_and_write_result`` 的接线契约。

    这是 main() 与 T-MV-09 启动链之间唯一可测的缝：spawn 参数（尤其
    ``persisted`` = 入选+合成总数，误传会导致「明明落了因子却不验证」）与
    ``result.json`` 里的 ``validation_spawned`` 必须对得上——自动验证没挂上
    而 result.json 不写，上层看不出需要手工补跑。
    """

    def _spawn_recorder(self, monkeypatch, *, returns: bool) -> list[dict]:
        """打桩 ``validate_spawn.spawn_validation``（函数体内是调用时导入，
        打在源模块上即生效），记账并返回固定值。"""
        from backend.services.engine.alpha_agent import validate_spawn  # noqa: PLC0415

        calls: list[dict] = []

        def fake_spawn(**kwargs) -> bool:
            calls.append(kwargs)
            return returns

        monkeypatch.setattr(validate_spawn, "spawn_validation", fake_spawn)
        return calls

    def test_arguments_and_result_json(self, runner, monkeypatch, tmp_path):
        """spawn 拿到原样参数；result.json 补 validation_spawned 且严格 JSON。

        payload 里的 NaN（dedup/composite 段的「算不出」语义）必须落成
        null——裸 NaN 会让 launcher 侧的严格 JSON 解析整份读不出来。
        """
        import json  # noqa: PLC0415
        import logging  # noqa: PLC0415

        calls = self._spawn_recorder(monkeypatch, returns=True)
        log = logging.getLogger("test-spawn-wiring")
        payload = {
            "task_id": "t1",
            "algorithm": "statistical",
            "persisted_factors": 2,
            "persisted_composite": 1,
            "window": {"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
            "dedup": {"kept": ["a", "b"], "max_abs_corr": float("nan")},
        }

        spawned = runner._spawn_validation_and_write_result(
            task_id="t1",
            market="a_share",
            log_dir=tmp_path,
            persisted_total=3,
            payload=payload,
            logger=log,
        )

        assert spawned is True
        (call,) = calls
        assert call["task_id"] == "t1"
        assert call["market"] == "a_share"
        assert call["log_dir"] == tmp_path
        assert call["persisted"] == 3, "传的是入选+合成总数——少传会让合成因子没人验"
        assert call["log"] is log

        def _reject_const(const: str):  # pragma: no cover - 有 NaN 即失败
            raise AssertionError(f"result.json 含非标准 JSON 常量：{const}")

        data = json.loads(
            (tmp_path / "result.json").read_text(encoding="utf-8"),
            parse_constant=_reject_const,
        )
        assert data["validation_spawned"] is True
        assert data["persisted_factors"] == 2
        assert data["persisted_composite"] == 1
        assert data["window"] == payload["window"]
        assert data["dedup"]["max_abs_corr"] is None, "NaN → null（「算不出」不是 0）"

    def test_spawn_false_is_written_and_return_wins(
        self, runner, monkeypatch, tmp_path
    ):
        """没拉起（关了开关/无对象可验）→ result.json 如实写 false；payload
        里即使带陈旧的 validation_spawned 也被真实返回值覆盖。"""
        import json  # noqa: PLC0415
        import logging  # noqa: PLC0415

        self._spawn_recorder(monkeypatch, returns=False)

        spawned = runner._spawn_validation_and_write_result(
            task_id="t1",
            market="a_share",
            log_dir=tmp_path,
            persisted_total=0,
            payload={"task_id": "t1", "validation_spawned": "stale"},
            logger=logging.getLogger("test-spawn-wiring"),
        )

        assert spawned is False
        data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
        assert data["validation_spawned"] is False


class TestRegimeByMonth:
    """``_load_regime_by_month``（T-FE-03）：口径复用 ``market_regime`` 单源，
    任何失败降级 ``(None, source)``——报告少一列，挖掘主链不因 regime 数据
    缺席失败；source 记录降级原因（评审 M4：可追溯，不只一条 warning）。"""

    def test_index_history_failure_degrades_to_none(self, runner, monkeypatch):
        from backend.services.engine import regime_persist  # noqa: PLC0415

        def _boom(market: str, symbol: str):
            raise FileNotFoundError("qdb_index_daily 不可达")

        monkeypatch.setattr(regime_persist, "load_index_history", _boom)
        by_month, source = runner._load_regime_by_month()
        assert by_month is None
        assert source["market"] == "CN"
        assert "qdb_index_daily 不可达" in source["error"]

    def test_short_history_degrades_to_none(self, runner, monkeypatch):
        """单根 bar 也当「有历史」会让状态序列空转——显式拒绝，不硬凑月标签。"""
        from backend.services.engine import regime_persist  # noqa: PLC0415

        monkeypatch.setattr(
            regime_persist,
            "load_index_history",
            lambda market, symbol: {
                "dates": ["2023-01-03"],
                "closes": [4000.0],
                "volumes": [1.0e8],
            },
        )
        by_month, source = runner._load_regime_by_month()
        assert by_month is None
        assert "无日线历史" in source["error"]

    def test_rising_history_yields_bull_months(self, runner, monkeypatch):
        from datetime import date, timedelta  # noqa: PLC0415

        from backend.services.engine import regime_persist  # noqa: PLC0415

        dates: list[str] = []
        closes: list[float] = []
        volumes: list[float] = []
        day = date(2023, 1, 3)
        price = 4000.0
        while len(dates) < 130:
            if day.weekday() < 5:
                dates.append(day.isoformat())
                closes.append(price)
                volumes.append(1.0e8)
                price *= 1.002  # 每日 +0.2%：20 日滚动 ≈ +4.1%、波动≈0 → bull
            day += timedelta(days=1)

        monkeypatch.setattr(
            regime_persist,
            "load_index_history",
            lambda market, symbol: {
                "dates": dates,
                "closes": closes,
                "volumes": volumes,
            },
        )
        by_month, source = runner._load_regime_by_month()
        assert by_month is not None
        assert len(by_month) >= 3
        assert set(by_month.values()) == {"bull"}
        assert all(len(m) == 7 and m[4] == "-" for m in by_month), "键是 YYYY-MM"
        assert source == {"market": "CN", "index": "000300.SH", "months": len(by_month)}


class TestMainWiring:
    """``main()`` 全接线（评审 M5b）：单点各自绿不代表接线没断——regime 月
    状态必须真进 ``run_statistical_mining``，降级痕迹必须真落
    ``mining_report.json``。用替身记账，不取数、不写生产表。"""

    def _stub_everything(
        self, runner, monkeypatch, tmp_path, *, regime_result, calls, extra_argv=()
    ):
        from backend.services.engine.alpha_agent.statistical import engine as eng
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            tradability as td,
        )

        class _Win:
            def iso(self):
                return ("2021-01-01", "2024-12-31")

        monkeypatch.setattr(runner, "_resolve_windows", lambda: (_Win(), None, "stub"))
        monkeypatch.setattr(runner, "_load_regime_by_month", lambda: regime_result)
        # T-FE-09：台账落点指向 tmp——main 会读冷却名单并在收尾记账，
        # 测试绝不碰容器卷 /data/factor_variants/
        monkeypatch.setenv(
            "QM_VARIANT_LEDGER_PATH", str(tmp_path / "variant_ledger.json")
        )

        def _fake_run(*args, **kwargs):
            calls["engine"] = kwargs
            cand = eng.CandidateReport(
                key="reversal_5d",
                name="5 日反转",
                family="reversal",
                formulation="x",
                source="s",
                expression="$close",
                min_history=5,
                horizons=(),
                best_horizon=None,
                rank_ic_mean=float("nan"),
                icir=float("nan"),
                t_stat=float("nan"),
                p_value=float("nan"),
                fdr_pass=False,
                fdr_horizons=(),
                oos_verified=False,
                oos_horizon=None,
                oos_rank_ic=float("nan"),
                oos_n_days=0,
                quantile_monotonicity=float("nan"),
                spread_mean=float("nan"),
                selected=False,
                reject_reason="无有效 IC 日",
            )
            return eng.MiningResult(
                candidates=(cand,),
                selected=(),
                filter_report=td.FilterReport(
                    n_input=10,
                    n_suspended=0,
                    n_st=0,
                    n_new_listing=0,
                    n_limit_locked=0,
                    n_low_liquidity=0,
                ),
                neutralize_report=None,
                neutralize_mode="none",
                horizons=(1, 5),
                window={"mining_start": "2021-01-01", "mining_end": "2024-12-31"},
                panel_rows=100,
                n_symbols=10,
                universe="all_a",
                # 与真引擎同契约：回传实际生效的冷却集（报告留痕取它）
                cooled_keys=tuple(sorted(kwargs.get("cooled_keys") or ())),
            )

        monkeypatch.setattr(eng, "run_statistical_mining", _fake_run)

        async def _fake_persist(result, **kwargs):
            calls["persist"] = kwargs
            return 0, 0

        monkeypatch.setattr(runner, "_persist_selected", _fake_persist)

        def _fake_spawn(**kwargs):
            calls["spawn"] = kwargs
            return False

        monkeypatch.setattr(runner, "_spawn_validation_and_write_result", _fake_spawn)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_statistical_mining.py",
                "--task-id",
                "t1",
                "--user-id",
                "u1",
                "--market",
                "a_share",
                "--universe",
                "all_a",
                "--top-k",
                "3",
                "--log-dir",
                str(tmp_path),
                *extra_argv,
            ],
        )

    def test_month_states_reach_engine_and_source_lands_in_report(
        self, runner, monkeypatch, tmp_path
    ):
        import json  # noqa: PLC0415

        calls: dict = {}
        source = {"market": "CN", "index": "000300.SH", "months": 2}
        self._stub_everything(
            runner,
            monkeypatch,
            tmp_path,
            regime_result=({"2021-01": "bull", "2021-02": "neutral"}, source),
            calls=calls,
        )

        runner.main()

        assert calls["engine"]["regime_by_month"] == {
            "2021-01": "bull",
            "2021-02": "neutral",
        }, "月状态必须真进引擎调用——接线断=「分市场状态 IC」列静默留空"
        report = json.loads(
            (tmp_path / "mining_report.json").read_text(encoding="utf-8")
        )
        assert report["regime_source"] == source, "就绪痕迹（指数/月数）须落审计报告"
        assert report["window_kind"] == "stub"
        assert calls["spawn"]["log_dir"] == tmp_path

    def test_degraded_regime_leaves_trace_in_report(
        self, runner, monkeypatch, tmp_path
    ):
        """regime 取不到：引擎收到 None（少一列不失败），审计报告记下
        **为什么**——降级原因可追溯（评审 M4），不是只剩一条易丢的 warning。"""
        import json  # noqa: PLC0415

        calls: dict = {}
        source = {"market": "CN", "error": "qdb_index_daily 不可达"}
        self._stub_everything(
            runner,
            monkeypatch,
            tmp_path,
            regime_result=(None, source),
            calls=calls,
        )

        runner.main()

        assert calls["engine"]["regime_by_month"] is None
        report = json.loads(
            (tmp_path / "mining_report.json").read_text(encoding="utf-8")
        )
        assert report["regime_source"] == source

    def test_variant_ledger_cooling_reaches_engine_and_report(
        self, runner, monkeypatch, tmp_path
    ):
        """T-FE-09 全接线：台账里的生效冷却必须真进引擎调用，实际生效集
        （引擎回传）必须落 ``mining_report.json`` 的 ``variant_ledger`` 块。"""
        import json  # noqa: PLC0415

        _seed_active_cooling(tmp_path / "variant_ledger.json", "reversal_60d")
        calls: dict = {}
        self._stub_everything(
            runner,
            monkeypatch,
            tmp_path,
            regime_result=(None, {"market": "CN", "error": "stub"}),
            calls=calls,
        )

        runner.main()

        assert calls["engine"]["cooled_keys"] == frozenset({"reversal_60d"}), (
            "台账生效冷却必须真进引擎——接线断=冷却名单纯装饰"
        )
        report = json.loads(
            (tmp_path / "mining_report.json").read_text(encoding="utf-8")
        )
        block = report["variant_ledger"]
        assert block["cooled_applied"] == ["reversal_60d"]
        assert block["ledger_path"] == str(tmp_path / "variant_ledger.json")
        assert block["observations_written"] == 0, "替身无子变体候选 → 零观测不写账"
        assert "error" not in block

    def test_no_cooling_flag_bypasses_read(self, runner, monkeypatch, tmp_path):
        """``--no-cooling`` 是运维泄压阀：绕过名单（引擎收空集），观测照记。"""
        import json  # noqa: PLC0415

        _seed_active_cooling(tmp_path / "variant_ledger.json", "reversal_60d")
        calls: dict = {}
        self._stub_everything(
            runner,
            monkeypatch,
            tmp_path,
            regime_result=(None, {"market": "CN", "error": "stub"}),
            calls=calls,
            extra_argv=("--no-cooling",),
        )

        runner.main()

        assert calls["engine"]["cooled_keys"] == frozenset()
        report = json.loads(
            (tmp_path / "mining_report.json").read_text(encoding="utf-8")
        )
        assert report["variant_ledger"]["cooled_applied"] == []


def _variant_report(
    runner,
    *,
    key: str = "reversal_60d",
    parent: str = "reversal_20d",
    status: str = "ok",
    ic_ir: float | None = 0.1,
    n_days: int = 60,
):
    """子变体报告：带父本与残差观测（形状 = 引擎出口 ``variant_residual``）。"""
    from backend.services.engine.alpha_agent.statistical import engine as eng

    nan = float("nan")
    stat = {
        "horizon": 1,
        "status": status,
        "ic_ir": ic_ir,
        "ic_mean": 0.012 if ic_ir is not None else None,
        "n_days": n_days,
        "basis": [parent] if status == "ok" else [],
        "n_basis": 1 if status == "ok" else 0,
        "max_basis_abs_corr": 0.42,
    }
    return eng.CandidateReport(
        key=key,
        name=f"{key} 变体",
        family="reversal",
        formulation="x",
        source="s",
        expression="$close",
        min_history=60,
        horizons=(),
        best_horizon=1,
        rank_ic_mean=nan,
        icir=nan,
        t_stat=nan,
        p_value=nan,
        fdr_pass=False,
        fdr_horizons=(),
        oos_verified=False,
        oos_horizon=None,
        oos_rank_ic=nan,
        oos_n_days=0,
        quantile_monotonicity=nan,
        spread_mean=nan,
        selected=False,
        reject_reason="稳定性窗不过",
        variant_of=parent,
        variant_residual=(stat,),
    )


def _seed_active_cooling(
    ledger_path, key: str = "reversal_60d", universe: str = "all_a"
) -> None:
    """向台账种一条生效中的冷却（低 ICIR 高置信失败 → decide_cooling → save）。"""
    from backend.services.engine.alpha_agent.statistical import variant_ledger as vl

    obs = {
        "variant_key": key,
        "universe": universe,
        "window_start": "2023-01-01",
        "window_end": "2023-12-31",
        "run_id": "seed",
        "horizons": [
            {
                "horizon": 1,
                "status": "ok",
                "ic_ir": 0.1,
                "ic_mean": 0.01,
                "n_days": 60,
                "basis": [vl.BASELINE_BY_KEY[key]],
                "n_basis": 1,
                "max_basis_abs_corr": 0.5,
            }
        ],
        "best_horizon": 1,
        "at": "2026-01-01T00:00:00Z",
    }
    ledger = vl.upsert_observations(vl.empty_ledger(), [obs])
    ledger, updates = vl.decide_cooling(ledger)
    assert any(u["action"] == "cooled" for u in updates), "种子前提：已入冷却"
    vl.save_ledger(ledger_path, ledger)


class TestVariantLedgerWiring:
    """``_update_variant_ledger``（T-FE-09 收尾记账）的契约。

    两条纪律各钉一测：非对称否决（不可验证观测照记不判失败）与
    「写失败只留痕不拦收尾」（台账是审计面，挖掘主链已完成）。
    """

    def _result(self, runner, *, candidates, universe: str = "all_a"):
        import types  # noqa: PLC0415

        return types.SimpleNamespace(
            candidates=tuple(candidates),
            window={"mining_start": "2024-01-01", "mining_end": "2024-12-31"},
            universe=universe,
            cooled_keys=(),
        )

    def test_observations_written_and_cooling_decided(self, runner, tmp_path):
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            variant_ledger as vl,
        )

        ledger_path = tmp_path / "variant_ledger.json"
        block = runner._update_variant_ledger(
            self._result(runner, candidates=[_variant_report(runner)]),
            universe="all_a",
            run_id="t1",
            cooled_applied=["reversal_60d"],
            ledger_path=ledger_path,
        )

        assert block["observations_written"] == 1
        assert block["cooled_applied"] == ["reversal_60d"]
        assert block["ledger_path"] == str(ledger_path)
        assert not block.get("error")
        # 高置信失败（ok ∧ n_days=60 ≥ 40 ∧ ic_ir=0.1 < 0.3）→ 冷却
        assert [u["action"] for u in block["cooling_updates"]] == ["cooled"]
        ledger = vl.load_ledger(ledger_path)
        obs = ledger["variants"]["reversal_60d"]["observations"]
        assert len(obs) == 1
        assert obs[0]["baseline_key"] == "reversal_20d"
        assert obs[0]["window_end"] == "2024-12-31"
        assert obs[0]["run_id"] == "t1"
        assert ledger["cooling"][0]["variant_key"] == "reversal_60d"

    def test_unverifiable_observation_recorded_not_cooled(self, runner, tmp_path):
        """不可验证（insufficient_days）观测照记，但绝不判失败。"""
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            variant_ledger as vl,
        )

        ledger_path = tmp_path / "variant_ledger.json"
        block = runner._update_variant_ledger(
            self._result(
                runner,
                candidates=[
                    _variant_report(
                        runner, status="insufficient_days", ic_ir=None, n_days=0
                    )
                ],
            ),
            universe="all_a",
            run_id="t1",
            ledger_path=ledger_path,
        )

        assert block["observations_written"] == 1
        assert block["cooling_updates"] == []
        assert vl.load_ledger(ledger_path)["cooling"] == []

    def test_no_variant_candidates_leaves_ledger_untouched(self, runner, tmp_path):
        """零观测不建文件（台账只记子变体；根候选不入账）。"""
        ledger_path = tmp_path / "variant_ledger.json"
        block = runner._update_variant_ledger(
            self._result(runner, candidates=[_tier3_report(runner)]),
            universe="all_a",
            run_id="t1",
            ledger_path=ledger_path,
        )
        assert block["observations_written"] == 0
        assert not ledger_path.exists()

    def test_write_failure_is_recorded_not_raised(self, runner, monkeypatch, tmp_path):
        """台账写失败：不抛、不拦收尾，报告块如实记 error（不静默）。"""
        from backend.services.engine.alpha_agent.statistical import (  # noqa: PLC0415
            variant_ledger as vl,
        )

        def _boom(path, ledger):
            raise OSError("磁盘满")

        monkeypatch.setattr(vl, "save_ledger", _boom)
        block = runner._update_variant_ledger(
            self._result(runner, candidates=[_variant_report(runner)]),
            universe="all_a",
            run_id="t1",
            ledger_path=tmp_path / "variant_ledger.json",
        )
        assert "磁盘满" in block["error"]
        assert block["observations_written"] == 0
