"""挖掘算法注册表（移植 P2）——manifest 装载 / 校验 / 提交闸门。

钉住的边：

1. **内建清单齐全且真实**：``rdagent`` + ``statistical`` 两个内置 manifest 装载
   通过；``runner`` 路径在仓内真实存在（声明了却跑不起来的算法不许出现在
   可选列表里）；``markets`` 不越过市场适配器注册表（防手写错市场名）。
2. **能力面是闸门唯一依据**：统计派仅 A 股、仅日线、不消费数据集选择；组合
   违例逐条报错，顺序固定（存在 → 启用 → 市场 → 频率 → 数据集，先来先报）。
3. **名实一致**：manifest ``key`` 必须等于文件名；bad YAML / 缺 runner / runner
   绝对路径或含 ``..`` / freq 越出 mining_config 支持集 / stages 空或重 key →
   装载即拒。
4. **坏文件不拖垮目录**：``list_algorithms`` 如实报 ``valid=False`` + 原因，
   好文件照常列出。
5. **未指定 = 默认 rdagent**（迁移前唯一路径；老前端零改动）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.engine.alpha_agent import algorithm_registry as reg  # noqa: E402

BUILTIN_KEYS = {"rdagent", "statistical"}


def _minimal(**over) -> dict:
    data = {
        "key": "alpha",
        "name": "测试算法",
        "version": "0.1.0",
        "desc": "测试用",
        "markets": ["a_share"],
        "freqs": ["day"],
        "stages": [{"key": "run", "label": "运行"}],
        "runner": "scripts/run.py",
    }
    data.update(over)
    return data


def _tmp_registry(tmp_path: Path) -> Path:
    """带一个真实 runner 文件的临时仓根（runner 存在性校验的基准）。"""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "run.py").write_text("print('ok')", encoding="utf-8")
    return tmp_path


def _write_manifest(tmp_path: Path, name: str, data) -> Path:
    text = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
    path = tmp_path / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ── 内建清单 ─────────────────────────────────────────────────────────


class TestBuiltins:
    def test_catalog_exact_and_all_valid(self):
        items = reg.list_algorithms()
        assert {item["key"] for item in items} == BUILTIN_KEYS
        assert all(item["valid"] for item in items), [i for i in items if not i["valid"]]
        assert all(item["enabled"] for item in items)
        # 确定性顺序（key 升序）：前端下拉顺序稳定
        assert [item["key"] for item in items] == sorted(BUILTIN_KEYS)

    def test_rdagent_capabilities(self):
        algo = reg.load_algorithm("rdagent")
        assert algo.runner == "scripts/alpha_agent/run_rd_agent.py"
        assert "a_share" in algo.markets
        assert set(algo.freqs) == {"day", "1min"}
        assert algo.uses_datasets is True
        assert algo.uses_llm is True, "演化派走 LLM 链路（池注入/env/loop_n/direction）"
        assert algo.requires_gpu is True, "演化派跑 GPU 硬件锁"
        assert [s.key for s in algo.stages] == [
            "hypothesis",
            "experiment",
            "coder",
            "runner",
            "feedback",
        ]

    def test_statistical_capabilities(self):
        algo = reg.load_algorithm("statistical")
        assert algo.runner == "scripts/alpha_agent/run_statistical_mining.py"
        assert algo.markets == ("a_share",), "统计派直读 QuantDB A 股日线，仅 A 股"
        assert algo.freqs == ("day",), "统计派无分钟实现，频率面必须如实收窄"
        assert algo.uses_datasets is False, "统计派不喂 LLM，数据集选择对它无意义"
        assert algo.uses_llm is False, "统计派无 LLM 链路：不得注入提示词池/LLM env"
        assert algo.requires_gpu is False, "统计派纯 CPU：GPU 锁不得拦它"

    def test_capability_fields_in_catalog_summary(self):
        """目录接口出参必须带能力面——前端按它决定是否显示 loop_n/direction。"""
        by_key = {item["key"]: item for item in reg.list_algorithms()}
        assert by_key["rdagent"]["uses_llm"] is True
        assert by_key["rdagent"]["requires_gpu"] is True
        assert by_key["statistical"]["uses_llm"] is False
        assert by_key["statistical"]["requires_gpu"] is False

    def test_runner_files_exist_on_disk(self):
        for item in reg.list_algorithms():
            assert item["valid"], item
            assert (ROOT / item["runner"]).is_file(), item["runner"]

    def test_manifest_markets_within_adapter_registry(self):
        """market 名打错（如 hongkong）应被测试拦下——能力面不许越出市场注册表。"""
        from backend.services.engine.rd_agent.market_adapters import list_markets

        available = {m["market_id"] for m in list_markets()}
        for item in reg.list_algorithms():
            # crypto 按 ENABLE_CRYPTO 开关注册（生产可关）——豁免可用性，只查拼写
            assert set(item["markets"]) - {"crypto"} <= available, item["key"]


# ── validate_manifest 结构校验 ───────────────────────────────────────


class TestValidateManifest:
    def test_minimal_valid(self, tmp_path):
        root = _tmp_registry(tmp_path)
        algo = reg.validate_manifest(_minimal(), repo_root=root)
        assert algo.key == "alpha" and algo.enabled is True
        assert algo.vendor == "" and algo.tags == ()
        assert algo.uses_datasets is True  # 缺省真：不声明=消费数据集
        # 运行时能力面缺省亦真 = 迁移前唯一路径（rdagent）的行为——显式 false 才收窄
        assert algo.uses_llm is True and algo.requires_gpu is True

    @pytest.mark.parametrize(
        "missing", ["key", "name", "version", "desc", "markets", "freqs", "stages", "runner"]
    )
    def test_required_fields(self, tmp_path, missing):
        root = _tmp_registry(tmp_path)
        data = _minimal()
        data.pop(missing)
        with pytest.raises(reg.AlgorithmError, match=missing):
            reg.validate_manifest(data, repo_root=root)

    def test_key_must_be_filename(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="文件名与 key 不一致"):
            reg.validate_manifest(_minimal(), expected_key="other", repo_root=root)

    @pytest.mark.parametrize("bad_key", ["Alpha", "上号", "a b", "", "a" * 65])
    def test_key_charset_strict(self, tmp_path, bad_key):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError):
            reg.validate_manifest(_minimal(key=bad_key), repo_root=root)

    def test_freq_must_stay_within_mining_config(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="不在支持集"):
            reg.validate_manifest(_minimal(freqs=["5min"]), repo_root=root)

    def test_empty_and_dup_and_bad_items(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="markets"):
            reg.validate_manifest(_minimal(markets=[]), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="重复项"):
            reg.validate_manifest(_minimal(markets=["a_share", "a_share"]), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="非法项"):
            reg.validate_manifest(_minimal(markets=["a_share", 3]), repo_root=root)

    def test_stages_contract(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="stages"):
            reg.validate_manifest(_minimal(stages=[]), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="重复"):
            reg.validate_manifest(
                _minimal(stages=[{"key": "a", "label": "甲"}, {"key": "a", "label": "乙"}]),
                repo_root=root,
            )
        with pytest.raises(reg.AlgorithmError, match="label"):
            reg.validate_manifest(_minimal(stages=[{"key": "a"}]), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="阶段 key 非法"):
            reg.validate_manifest(
                _minimal(stages=[{"key": "A B", "label": "甲"}]), repo_root=root
            )

    def test_runner_path_safety(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="仓根相对路径"):
            reg.validate_manifest(_minimal(runner="/abs/run.py"), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="不得含"):
            reg.validate_manifest(_minimal(runner="../run.py"), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="不存在"):
            reg.validate_manifest(_minimal(runner="scripts/ghost.py"), repo_root=root)

    def test_bool_fields_strict(self, tmp_path):
        root = _tmp_registry(tmp_path)
        with pytest.raises(reg.AlgorithmError, match="enabled"):
            reg.validate_manifest(_minimal(enabled="yes"), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="uses_datasets"):
            reg.validate_manifest(_minimal(uses_datasets=1), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="uses_llm"):
            reg.validate_manifest(_minimal(uses_llm="no"), repo_root=root)
        with pytest.raises(reg.AlgorithmError, match="requires_gpu"):
            reg.validate_manifest(_minimal(requires_gpu=0), repo_root=root)


# ── list_algorithms 的坏文件纪律 ─────────────────────────────────────


class TestListAlgorithms:
    def test_bad_file_reported_not_dropped(self, tmp_path):
        root = _tmp_registry(tmp_path)
        registry_dir = tmp_path / "manifests"
        registry_dir.mkdir()
        _write_manifest(registry_dir, "alpha", _minimal())
        _write_manifest(registry_dir, "broken", "key: [unclosed\n: :")
        _write_manifest(registry_dir, "ghost", _minimal(key="ghost", runner="scripts/x.py"))

        items = reg.list_algorithms(registry_dir=registry_dir, repo_root=root)
        by_key = {item["key"]: item for item in items}
        assert [i["key"] for i in items] == ["alpha", "broken", "ghost"], "key 升序"
        assert by_key["alpha"]["valid"] is True
        assert by_key["broken"]["valid"] is False and by_key["broken"]["error"]
        assert by_key["ghost"]["valid"] is False
        assert "不存在" in by_key["ghost"]["error"]

    def test_missing_dir_is_empty_not_crash(self, tmp_path):
        assert reg.list_algorithms(registry_dir=tmp_path / "nope", repo_root=tmp_path) == []


# ── 提交闸门 resolve_algorithm ───────────────────────────────────────


class TestNormalize:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_means_default(self, value):
        assert reg.normalize_algorithm(value) == reg.DEFAULT_ALGORITHM_KEY

    @pytest.mark.parametrize("value", ["RdAgent", " STATISTICAL "])
    def test_case_and_space_normalized(self, value):
        assert reg.normalize_algorithm(value) == value.strip().lower()

    @pytest.mark.parametrize("value", ["bad key!", "上号", "a/b"])
    def test_invalid_charset_rejected(self, value):
        with pytest.raises(reg.AlgorithmError):
            reg.normalize_algorithm(value)


class TestResolve:
    def test_default_resolves_to_rdagent(self):
        algo = reg.resolve_algorithm(None, market="a_share", freq=None)
        assert algo.key == "rdagent"

    def test_unknown_lists_available(self):
        with pytest.raises(reg.AlgorithmError, match="不存在") as ei:
            reg.resolve_algorithm("quantum", market="a_share", freq=None)
        assert "statistical" in str(ei.value) and "rdagent" in str(ei.value)

    def test_statistical_happy_path(self):
        algo = reg.resolve_algorithm(
            "statistical", market="a_share", freq="day", datasets=None
        )
        assert algo.key == "statistical"

    def test_disabled_algorithm_rejected(self, tmp_path):
        root = _tmp_registry(tmp_path)
        registry_dir = tmp_path / "manifests"
        registry_dir.mkdir()
        _write_manifest(registry_dir, "alpha", _minimal(enabled=False))
        with pytest.raises(reg.AlgorithmError, match="已停用"):
            reg.resolve_algorithm(
                "alpha", market="a_share", freq="day", registry_dir=registry_dir, repo_root=root
            )

    def test_market_gate(self):
        with pytest.raises(reg.AlgorithmError, match="不支持市场 hong_kong"):
            reg.resolve_algorithm("statistical", market="hong_kong", freq="day")

    def test_freq_gate(self):
        with pytest.raises(reg.AlgorithmError, match="不支持频率 1min"):
            reg.resolve_algorithm("statistical", market="a_share", freq="1min")

    def test_datasets_gate_but_default_passes(self):
        with pytest.raises(reg.AlgorithmError, match="不消费数据集选择"):
            reg.resolve_algorithm(
                "statistical", market="a_share", freq="day", datasets=["kline_1d"]
            )
        # None/[] = 未缩窄（全选默认）——不拦
        reg.resolve_algorithm("statistical", market="a_share", freq="day", datasets=[])
        reg.resolve_algorithm("statistical", market="a_share", freq="day", datasets=None)

    def test_rdagent_full_surface(self):
        reg.resolve_algorithm("rdagent", market="us_stock", freq="day", datasets=["x"])
        reg.resolve_algorithm("rdagent", market="a_share", freq="1min")

    def test_check_order_market_before_freq_before_datasets(self):
        """多重违例先来先报：市场 → 频率 → 数据集（消息稳定，前端可断言）。"""
        with pytest.raises(reg.AlgorithmError, match="不支持市场"):
            reg.resolve_algorithm(
                "statistical", market="us_stock", freq="1min", datasets=["x"]
            )
