"""qlib conf 模板渲染（``qlib_conf_template``）—— 让 qrun 拿到可执行配置。

背景（2026-10-10 实证）：vendored RD-Agent 的 ``factor_template/conf_*.yaml``
是 Jinja 模板却**没有渲染环节**，``qrun`` 直接吃未渲染文本 → YAML 把
``{{ x }}`` 解析成 flow mapping → ``Alpha158DL._parse_fields_info`` 抛
``NotImplementedError``。历史证据：104 份 conf 原文残留 + 全盘 0 个
``qlib_res.csv`` + 日志反复 ``Command failed (exit 1): qrun conf_baseline.yaml``。

本测试钉四件事：

1. **真模板能渲染成合法 YAML**：占位符清零、分段/回测窗等于传入上下文、
   因子 loader 的 feature 解析成两个列表（不是 flow mapping 垃圾）；
2. **缺省语义**：上下文缺键时用模板自带 ``| default(...)``；无 default 的键
   缺了要**报错**（StrictUndefined），不静默渲染成空值；
3. **补丁接线**：``install_qlib_execute_patch`` 幂等，且 ``execute()`` 实际
   执行的是 ``*.rendered.yaml``；渲染失败时**不跑 qrun**、按失败契约返回；
4. **分钟改写**（P1.5）：``QM_MINING_FREQ=1min`` 时执行的是
   ``*.minute.yaml``（provider_uri 换分钟目录 + Alpha158DL 注入 ``freq: "1min"``，
   日线富化 loader 原样保留）；provider 未就绪 / conf 结构不符 → **失败且不跑
   qrun**（绝不静默按日线口径产出"自称分钟因子"的东西）；日线任务零变化。

需要 jinja2/pyyaml（容器内）：
``python -m pytest backend/tests/test_qlib_conf_render.py -q``
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:  # pragma: no cover - 环境相关
    import yaml

    from backend.services.engine.rd_agent import qlib_conf_template as qct
except Exception:  # noqa: BLE001
    yaml = None
    qct = None


def _resolve_template_dir() -> Path:
    """模板目录：优先**运行时装**的 rdagent（qrun 实际消费的那份），
    退回仓库 vendored 树（容器里只装了包、没挂 `rd-agent/`）。"""
    try:
        from rdagent.scenarios.qlib.experiment import workspace as _ws

        installed = Path(_ws.__file__).resolve().parent / "factor_template"
        if installed.is_dir():
            return installed
    except Exception:  # noqa: BLE001
        pass
    return (
        Path(__file__).resolve().parents[2]
        / "rd-agent"
        / "rdagent"
        / "scenarios"
        / "qlib"
        / "experiment"
        / "factor_template"
    )


_TEMPLATE_DIR = _resolve_template_dir()

pytestmark = pytest.mark.skipif(
    qct is None or not _TEMPLATE_DIR.is_dir(),
    reason="jinja2/pyyaml 或 factor_template 不可用（需容器环境）",
)

#: 与 factor_runner.develop() 的 env_to_use 同形（自定义挖掘窗 2021-10~2025-10 +
#: 检验窗 2025-10~2026-10，见 mining_config.qlib_segments）
CTX = {
    "train_start": "2021-10-09",
    "train_end": "2024-06-29",
    "valid_start": "2024-06-29",
    "valid_end": "2025-10-09",
    "test_start": "2025-10-09",
    "test_end": "2026-10-09",
    "feature_names": "['mom_20', 'gap_10']",
    "feature_expressions": "['$close/Ref($close,20)-1', '$open/Ref($close,1)-1']",
}
MODEL_CTX = {
    **CTX,
    "n_epochs": "100",
    "lr": "2e-4",
    "early_stop": "10",
    "batch_size": "256",
    "weight_decay": "0.0001",
    "dataset_cls": "DatasetH",
    "num_features": "4",
    "step_len": "20",
    "num_timesteps": "20",
}

_TEMPLATES = ["conf_baseline.yaml", "conf_combined_factors.yaml"]

#: 分钟任务场景里假 provider 的根（断言用；真实路径由 QM_QLIB_MINUTE_DIR 决定）
_MINUTE_PROVIDER_URI = "/data/qlib/cn_data_1min"


@pytest.fixture(autouse=True)
def _clean_mining_env(monkeypatch):
    """默认把频率相关 env 清干净：日线断言不受外部环境泄漏影响。

    需要分钟场景的用例在测试体内自行 ``monkeypatch.setenv``（晚于本 fixture）。
    """
    monkeypatch.delenv("QM_MINING_FREQ", raising=False)
    monkeypatch.delenv("QM_QLIB_MINUTE_DIR", raising=False)


def _load(template_name: str, ctx: dict) -> tuple[str, dict]:
    text = qct.render_conf_template(
        (_TEMPLATE_DIR / template_name).read_text(encoding="utf-8"), ctx
    )
    return text, yaml.safe_load(text)


# ── 1. 真模板 → 合法 YAML ────────────────────────────────────────────


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_factor_templates_render_with_window(template_name):
    text, cfg = _load(template_name, CTX)

    assert "{{" not in text and "{%" not in text
    handler = cfg["data_handler_config"]
    # YAML 1.1 把裸日期解析成 date——str() 归一后比较
    assert str(handler["start_time"]) == CTX["train_start"]
    assert str(handler["end_time"]) == CTX["test_end"]

    features = handler["data_loader"]["kwargs"]["dataloader_l"][0]["kwargs"]["config"]["feature"]
    # 这正是 bug 现场：未渲染时该项是 flow mapping，Alpha158DL 解析直接抛错
    assert features == [["$close/Ref($close,20)-1", "$open/Ref($close,1)-1"], ["mom_20", "gap_10"]]

    segments = cfg["task"]["dataset"]["kwargs"]["segments"]
    assert [str(d) for d in segments["train"]] == [CTX["train_start"], CTX["train_end"]]
    assert [str(d) for d in segments["valid"]] == [CTX["valid_start"], CTX["valid_end"]]
    assert [str(d) for d in segments["test"]] == [CTX["test_start"], CTX["test_end"]]

    backtest = cfg["port_analysis_config"]["backtest"]
    assert str(backtest["start_time"]) == CTX["test_start"]
    assert str(backtest["end_time"]) == CTX["test_end"]


def test_combined_template_references_factor_parquet():
    """合并因子实验的数据面 = workspace 里的 parquet（factor_runner 写盘处）。"""
    _, cfg = _load("conf_combined_factors.yaml", CTX)
    loaders = cfg["data_handler_config"]["data_loader"]["kwargs"]["dataloader_l"]
    assert loaders[1]["class"] == "qlib.data.dataset.loader.StaticDataLoader"
    assert loaders[1]["kwargs"]["config"] == "combined_factors_df.parquet"


def test_sota_model_template_renders_with_model_context():
    text, cfg = _load("conf_combined_factors_sota_model.yaml", MODEL_CTX)
    assert "{{" not in text
    model_kwargs = cfg["task"]["model"]["kwargs"]
    assert model_kwargs["n_epochs"] == 100
    # YAML 1.1 不认 "2e-4"（缺小数点）→ 保持字符串，与上游同行为；下游自会转 float
    assert model_kwargs["lr"] == "2e-4"


# ── 2. 缺省与报错语义 ────────────────────────────────────────────────


def test_missing_context_falls_back_to_template_defaults():
    """日期键缺失 → 模板自带 ``| default(...)`` 生效（不是崩溃，也不是空值）；
    但 ``feature_*`` **没有 default**（模板要求调用方必须给），缺了就要报错。"""
    with pytest.raises(qct.ConfTemplateError):
        qct.render_conf_template(
            (_TEMPLATE_DIR / "conf_baseline.yaml").read_text(encoding="utf-8"), {}
        )

    _, cfg = _load("conf_baseline.yaml", {"feature_names": "[]", "feature_expressions": "[]"})
    handler = cfg["data_handler_config"]
    assert str(handler["start_time"]) == "2008-01-01"
    assert handler["end_time"] is None  # default("null") → YAML null
    assert [str(d) for d in cfg["task"]["dataset"]["kwargs"]["segments"]["train"]] == [
        "2008-01-01",
        "2014-12-31",
    ]
    features = handler["data_loader"]["kwargs"]["dataloader_l"][0]["kwargs"]["config"]["feature"]
    assert features == [[], []]


def test_undefined_without_default_rejected():
    with pytest.raises(qct.ConfTemplateError) as exc:
        qct.render_conf_template("start: {{ train_start }}\nend: {{ n_epochs }}\n", CTX)
    assert "n_epochs" in str(exc.value)


def test_rendered_leftover_placeholder_rejected():
    with pytest.raises(qct.ConfTemplateError) as exc:
        qct.render_conf_template("x: {{ '{{' }} y", {})
    assert "残留" in str(exc.value)


def test_render_conf_file_writes_sibling_and_keeps_template(tmp_path):
    template = _TEMPLATE_DIR / "conf_baseline.yaml"
    conf = tmp_path / "conf_baseline.yaml"
    conf.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    out = qct.render_conf_file(conf, CTX)

    assert out.name == "conf_baseline.rendered.yaml"
    assert out.parent == tmp_path
    assert "2021-10-09" in out.read_text(encoding="utf-8")
    assert "{{" in conf.read_text(encoding="utf-8")  # 模板原样保留（审计）

    # 换窗口重渲染：从模板重新渲染，不复用旧产物
    out2 = qct.render_conf_file(conf, {**CTX, "test_end": "2027-01-01"})
    assert out2 == out
    assert "2027-01-01" in out.read_text(encoding="utf-8")


def test_render_conf_file_missing_template_raises(tmp_path):
    with pytest.raises(qct.ConfTemplateError) as exc:
        qct.render_conf_file(tmp_path / "nope.yaml", CTX)
    assert "不可读" in str(exc.value)


# ── 3. 补丁接线（真实 QlibFBWorkspace.execute） ─────────────────────


try:  # pragma: no cover - 环境相关
    from rdagent.scenarios.qlib.experiment.workspace import QlibFBWorkspace
    from rdagent.scenarios.qlib.experiment import workspace as ws_mod

    HAVE_RDAGENT = True
except Exception:  # noqa: BLE001
    QlibFBWorkspace = None
    ws_mod = None
    HAVE_RDAGENT = False

needs_rdagent = pytest.mark.skipif(not HAVE_RDAGENT, reason="rdagent 不可用（需容器环境）")

#: 补丁改的是 rdagent 的类本身（进程级），用例间必须复位成原始实现
_ORIGINAL_WS_EXECUTE = QlibFBWorkspace.execute if HAVE_RDAGENT else None


@pytest.fixture()
def _fresh_patch():
    def _reset():
        if not HAVE_RDAGENT:
            return
        QlibFBWorkspace.execute = _ORIGINAL_WS_EXECUTE
        if "_qm_conf_rendering_patched" in QlibFBWorkspace.__dict__:
            delattr(QlibFBWorkspace, "_qm_conf_rendering_patched")

    _reset()
    yield
    _reset()


def _make_workspace(tmp_path: Path) -> "QlibFBWorkspace":
    """绕开 __init__（模板注入/全局 workspace 根目录），只要 workspace_path。"""
    ws = QlibFBWorkspace.__new__(QlibFBWorkspace)
    ws.workspace_path = tmp_path
    (tmp_path / "conf_baseline.yaml").write_text(
        (_TEMPLATE_DIR / "conf_baseline.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return ws


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
def test_patch_is_idempotent_and_renders_before_qrun(tmp_path, monkeypatch):
    assert qct.install_qlib_execute_patch() is True
    assert qct.install_qlib_execute_patch() is False  # 幂等

    calls: list[str] = []

    def fake_run(entry: str, local_path: str, env: dict | None = None) -> str:
        calls.append(entry)
        return "Epoch0: train -0.1, valid -0.2"

    monkeypatch.setattr(ws_mod, "_run_cmd_in_current_env", fake_run)
    monkeypatch.setattr(ws_mod.logger, "log_object", lambda *a, **k: None)
    # execute() 的成功判据：ret.pkl + qlib_res.csv 都在（read_exp_res.py 的产物）
    pd = pytest.importorskip("pandas")
    pd.DataFrame({"x": [1.0]}).to_pickle(tmp_path / "ret.pkl")
    (tmp_path / "qlib_res.csv").write_text("metric,0\nIC,0.031\n", encoding="utf-8")

    ws = _make_workspace(tmp_path)
    result, log = ws.execute(qlib_config_name="conf_baseline.yaml", run_env=CTX)

    assert calls == ["qrun conf_baseline.rendered.yaml", "python read_exp_res.py"]
    assert result is not None and "IC" in result.index
    rendered = (tmp_path / "conf_baseline.rendered.yaml").read_text(encoding="utf-8")
    assert "2021-10-09" in rendered and "{{" not in rendered
    assert "{{" in (tmp_path / "conf_baseline.yaml").read_text(encoding="utf-8")


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
def test_patch_does_not_run_qrun_when_render_fails(tmp_path, monkeypatch):
    """渲染失败不降级为"跑原始模板"（那正是要消灭的失败态）。"""
    assert qct.install_qlib_execute_patch() is True

    calls: list[str] = []
    monkeypatch.setattr(
        ws_mod, "_run_cmd_in_current_env", lambda entry, local_path, env=None: calls.append(entry) or ""
    )
    ws = _make_workspace(tmp_path)
    # 无 default 的占位符 + 上下文缺失 → StrictUndefined 报错
    (tmp_path / "conf_baseline.yaml").write_text("x: {{ n_epochs }}\n", encoding="utf-8")

    result, message = ws.execute(qlib_config_name="conf_baseline.yaml", run_env=CTX)

    assert result is None
    assert "渲染失败" in message
    assert calls == []


# ── 4. 分钟改写（纯函数） ────────────────────────────────────────────


def _overlay(template_name: str, ctx: dict = CTX) -> tuple[str, str, dict]:
    """渲染 → 分钟改写，返回 (渲染文本, 改写文本, 改写后的解析结果)。"""
    text, _ = _load(template_name, ctx)
    out = qct.apply_minute_overlay(text, provider_uri=_MINUTE_PROVIDER_URI, freq="1min")
    return text, out, yaml.safe_load(out)


def _alpha_entries(cfg: dict) -> list[dict]:
    loader = cfg["data_handler_config"]["data_loader"]
    nested = (loader.get("kwargs") or {}).get("dataloader_l")
    entries = nested if isinstance(nested, list) else [loader]
    return [e for e in entries if str(e.get("class") or "").endswith("Alpha158DL")]


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_minute_overlay_swaps_provider_and_sets_loader_freq(template_name):
    text, out, cfg = _overlay(template_name)

    assert out != text
    assert cfg["qlib_init"]["provider_uri"] == _MINUTE_PROVIDER_URI
    assert cfg["qlib_init"]["region"] == "cn"  # 只换 provider，不动其它 qlib_init 键
    alphas = _alpha_entries(cfg)
    assert len(alphas) == 1
    assert alphas[0]["kwargs"]["freq"] == "1min"
    # 特征表达式/标签/名称原样保留（改写只做 provider + freq 两件事）
    feature = alphas[0]["kwargs"]["config"]["feature"]
    assert feature == [["$close/Ref($close,20)-1", "$open/Ref($close,1)-1"], ["mom_20", "gap_10"]]
    label = alphas[0]["kwargs"]["config"]["label"]
    assert label == [["Ref($close, -2)/Ref($close, -1) - 1"], ["LABEL0"]]


def test_minute_overlay_keeps_daily_enrich_loader_untouched():
    """合并因子实验的日线富化 parquet loader：不注 freq、不删——数据面差异
    要留给数据侧（分钟模式下 h5 已是分钟口径），conf 改写不越权。"""
    _, _, cfg = _overlay("conf_combined_factors.yaml")
    loaders = cfg["data_handler_config"]["data_loader"]["kwargs"]["dataloader_l"]
    assert loaders[1]["class"] == "qlib.data.dataset.loader.StaticDataLoader"
    assert loaders[1]["kwargs"]["config"] == "combined_factors_df.parquet"
    assert "freq" not in loaders[1]["kwargs"]
    assert len(loaders) == 2  # 条目数不变


def test_minute_overlay_preserves_windows_and_backtest():
    _, _, cfg = _overlay("conf_baseline.yaml")
    handler = cfg["data_handler_config"]
    assert str(handler["start_time"]) == CTX["train_start"]
    assert str(handler["end_time"]) == CTX["test_end"]
    assert str(handler["instruments"]) == "csi300"
    segments = cfg["task"]["dataset"]["kwargs"]["segments"]
    assert [str(d) for d in segments["test"]] == [CTX["test_start"], CTX["test_end"]]
    backtest = cfg["port_analysis_config"]["backtest"]
    assert str(backtest["start_time"]) == CTX["test_start"]


def test_minute_overlay_does_not_mutate_rendered_text():
    text, out, _ = _overlay("conf_baseline.yaml")
    # 入参文本（rendered 产物，workspace 留档）仍是日线 provider
    assert "~/.qlib/qlib_data/cn_data" in text
    assert _MINUTE_PROVIDER_URI not in text
    assert _MINUTE_PROVIDER_URI in out


def test_minute_overlay_rejects_missing_provider_uri():
    base = "qlib_init:\n    region: cn\ntask: {}\n"
    with pytest.raises(qct.MinuteOverlayError) as exc:
        qct.apply_minute_overlay(base, provider_uri="/tmp/x", freq="1min")
    assert "provider_uri" in str(exc.value)


def test_minute_overlay_rejects_missing_data_loader():
    conf = (
        "qlib_init:\n    provider_uri: /day\n"
        "task:\n    dataset:\n        kwargs:\n            handler:\n"
        "                class: DataHandlerLP\n                kwargs: {}\n"
    )
    with pytest.raises(qct.MinuteOverlayError) as exc:
        qct.apply_minute_overlay(conf, provider_uri="/tmp/x", freq="1min")
    assert "data_loader" in str(exc.value)


def test_minute_overlay_rejects_conf_without_alpha_loader():
    conf = (
        "qlib_init:\n    provider_uri: /day\n"
        "task:\n    dataset:\n        kwargs:\n            handler:\n"
        "                kwargs:\n                    data_loader:\n"
        "                        class: NestedDataLoader\n"
        "                        kwargs:\n"
        "                            dataloader_l:\n"
        "                                - class: qlib.data.dataset.loader.StaticDataLoader\n"
        "                                  kwargs: {config: x.parquet}\n"
    )
    with pytest.raises(qct.MinuteOverlayError) as exc:
        qct.apply_minute_overlay(conf, provider_uri="/tmp/x", freq="1min")
    assert "Alpha158DL" in str(exc.value)


def test_minute_overlay_rejects_blank_provider_or_freq():
    _, text, _ = _overlay("conf_baseline.yaml")
    with pytest.raises(qct.MinuteOverlayError):
        qct.apply_minute_overlay(text, provider_uri="  ", freq="1min")
    with pytest.raises(qct.MinuteOverlayError):
        qct.apply_minute_overlay(text, provider_uri="/tmp/x", freq="")


def test_minute_overlay_rejects_non_mapping_and_bad_yaml():
    with pytest.raises(qct.MinuteOverlayError):
        qct.apply_minute_overlay("- a\n- b\n", provider_uri="/tmp/x", freq="1min")
    with pytest.raises(qct.MinuteOverlayError) as exc:
        qct.apply_minute_overlay("a: [\n", provider_uri="/tmp/x", freq="1min")
    assert "YAML" in str(exc.value)


def test_minute_overlay_rejects_non_mapping_loader_entry():
    conf = (
        "qlib_init:\n    provider_uri: /day\n"
        "task:\n    dataset:\n        kwargs:\n            handler:\n"
        "                kwargs:\n                    data_loader:\n"
        "                        class: NestedDataLoader\n"
        "                        kwargs:\n"
        "                            dataloader_l:\n"
        "                                - just-a-string\n"
    )
    with pytest.raises(qct.MinuteOverlayError) as exc:
        qct.apply_minute_overlay(conf, provider_uri="/tmp/x", freq="1min")
    assert "非映射" in str(exc.value)


def test_minute_overlay_handles_bare_alpha_loader():
    """裸 loader（无 NestedDataLoader 包装）也能改写——兜底形状。"""
    conf = (
        "qlib_init:\n    provider_uri: /day\n"
        "task:\n    dataset:\n        kwargs:\n            handler:\n"
        "                kwargs:\n                    data_loader:\n"
        "                        class: qlib.contrib.data.loader.Alpha158DL\n"
        "                        kwargs: {config: {feature: [[], []]}}\n"
    )
    cfg = yaml.safe_load(qct.apply_minute_overlay(conf, provider_uri="/min", freq="1min"))
    loader = cfg["task"]["dataset"]["kwargs"]["handler"]["kwargs"]["data_loader"]
    assert loader["kwargs"]["freq"] == "1min"
    assert loader["kwargs"]["config"] == {"feature": [[], []]}
    assert cfg["qlib_init"]["provider_uri"] == "/min"


# ── 5. 补丁接线（分钟模式经真实 execute） ───────────────────────────


def _make_minute_provider(root: Path) -> Path:
    """最小可用分钟 provider 布局（``is_minute_provider_ready`` 全部判据占上）。"""
    (root / "calendars").mkdir(parents=True)
    (root / "calendars" / "1min.txt").write_text("2026-01-05 09:30\n2026-01-05 09:31\n")
    (root / "calendars" / "day.txt").write_text("2026-01-05\n2026-01-06\n")
    (root / "instruments").mkdir()
    (root / "features").mkdir()
    (root / "meta.json").write_text('{"version": 2}', encoding="utf-8")
    return root


def _stub_qrun(monkeypatch, calls: list[str]) -> None:
    """拦截 qrun/read_exp_res；产物就位使 execute() 判成功。"""
    monkeypatch.setattr(
        ws_mod, "_run_cmd_in_current_env", lambda entry, local_path, env=None: calls.append(entry) or ""
    )
    monkeypatch.setattr(ws_mod.logger, "log_object", lambda *a, **k: None)


def _materialize_success(tmp_path: Path) -> None:
    pd = pytest.importorskip("pandas")
    pd.DataFrame({"x": [1.0]}).to_pickle(tmp_path / "ret.pkl")
    (tmp_path / "qlib_res.csv").write_text("metric,0\nIC,0.031\n", encoding="utf-8")


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_patch_runs_minute_conf_when_freq_is_minute(tmp_path, monkeypatch, template_name):
    provider = _make_minute_provider(tmp_path / "min_provider")
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(provider))
    assert qct.install_qlib_execute_patch() is True
    calls: list[str] = []
    _stub_qrun(monkeypatch, calls)
    _materialize_success(tmp_path)

    ws = QlibFBWorkspace.__new__(QlibFBWorkspace)
    ws.workspace_path = tmp_path
    (tmp_path / template_name).write_text(
        (_TEMPLATE_DIR / template_name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    result, _log = ws.execute(qlib_config_name=template_name, run_env=CTX)

    stem = Path(template_name).stem
    assert result is not None
    assert calls == [f"qrun {stem}.minute.yaml", "python read_exp_res.py"]
    minute_cfg = yaml.safe_load((tmp_path / f"{stem}.minute.yaml").read_text(encoding="utf-8"))
    assert minute_cfg["qlib_init"]["provider_uri"] == str(provider)
    assert _alpha_entries(minute_cfg)[0]["kwargs"]["freq"] == "1min"
    # 渲染产物仍在 workspace 留档（日线 provider），审计链完整
    rendered = (tmp_path / f"{stem}.rendered.yaml").read_text(encoding="utf-8")
    assert "~/.qlib/qlib_data/cn_data" in rendered


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
def test_patch_fails_without_minute_provider_and_does_not_run_qrun(tmp_path, monkeypatch):
    monkeypatch.setenv("QM_MINING_FREQ", "1min")
    monkeypatch.setenv("QM_QLIB_MINUTE_DIR", str(tmp_path / "not_built"))
    assert qct.install_qlib_execute_patch() is True
    calls: list[str] = []
    _stub_qrun(monkeypatch, calls)

    ws = _make_workspace(tmp_path)
    result, message = ws.execute(qlib_config_name="conf_baseline.yaml", run_env=CTX)

    assert result is None
    assert "分钟配置准备失败" in message and "未就绪" in message
    assert calls == []
    assert not (tmp_path / "conf_baseline.minute.yaml").exists()


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
def test_patch_day_freq_writes_no_minute_conf(tmp_path, monkeypatch):
    """显式 ``QM_MINING_FREQ=day``：执行文件与旧行为一致，不产 minute 文件。"""
    monkeypatch.setenv("QM_MINING_FREQ", "day")
    assert qct.install_qlib_execute_patch() is True
    calls: list[str] = []
    _stub_qrun(monkeypatch, calls)
    _materialize_success(tmp_path)

    ws = _make_workspace(tmp_path)
    result, _log = ws.execute(qlib_config_name="conf_baseline.yaml", run_env=CTX)

    assert result is not None
    assert calls == ["qrun conf_baseline.rendered.yaml", "python read_exp_res.py"]
    assert not (tmp_path / "conf_baseline.minute.yaml").exists()


@needs_rdagent
@pytest.mark.usefixtures("_fresh_patch")
def test_patch_rejects_bogus_freq_without_running_qrun(tmp_path, monkeypatch):
    """频率串非法：显式失败，绝不当成默认日线跑。"""
    monkeypatch.setenv("QM_MINING_FREQ", "5min")
    assert qct.install_qlib_execute_patch() is True
    calls: list[str] = []
    _stub_qrun(monkeypatch, calls)

    ws = _make_workspace(tmp_path)
    result, message = ws.execute(qlib_config_name="conf_baseline.yaml", run_env=CTX)

    assert result is None
    assert "频率" in message
    assert calls == []
