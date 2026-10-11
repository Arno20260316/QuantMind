"""挖掘因子毕业器（quantcustom → quantdb 镜像）的不变量。

这里守的都是**静默损坏**——不报错、只让数据悄悄变样：

1. **镜像 ≠ 合并**。只允许写 ``6_ml_datasets/rd_mined`` 自己的目录；一旦碰到
   ``l1_factors`` / ``l2_factors``，就是把用户产出混进官方行情库，而 CN 训练
   会把它当行情读 —— 出事时没有任何一层会报错。用「污染哨兵」目录断言。
2. **幂等**。第二次跑必须零写入（`is_noop`）。判据是 (size, mtime_ns)：物化器
   新增因子会重写**全部**分区对齐列集，所以列漂移必然改时间戳，无需额外 schema 比对。
3. **默认不删**。源里没有的分区要留在目标里 —— 误删会让训练静默少一段历史
   （少一天数据不会报错，只会让回测结果悄悄变）。只有显式 `--prune` 才收敛。
4. **原子**。写 ``.tmp`` 再 ``os.replace``；跑完不留 ``.tmp`` 残骸（残留会让
   下次扫描把它当成未知文件）。
5. **单分区失败不中断整批**，但要记进 ``failed`` 让调用方拿到非零退出码 ——
   毕业一半而日志说「完成」是最坏的结果。
6. **注册按字典契约**。``discover()`` 返回 ``{source: to_dict()}``；把这个
   dict 当对象用（``status.files``）不会在镜像阶段暴露 —— 镜像成功而字段
   注册缺席，训练页只会看到一座空库。

纯文件系统测试（tmp_path）；注册环节用 monkeypatch 断 DB，不碰真实数据根。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.scripts import promote_rd_mined as prm


# ---------------------------------------------------------------------------
# 夹具：造一个假的「源根 / 目标根」
# ---------------------------------------------------------------------------
def _write_partition(root: Path, dataset: str, dt: str, payload: bytes) -> Path:
    d = root / "6_ml_datasets" / dataset / f"dt={dt}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "data.parquet"
    f.write_bytes(payload)
    return f


def _split(dataset_dir: Path) -> tuple[Path, str]:
    """把 ``.../6_ml_datasets/rd_mined`` 还原成 (数据根, 库名)，配合 _write_partition。"""
    return dataset_dir.parents[1], dataset_dir.name


@pytest.fixture()
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """(源数据集目录, 目标数据集目录)，均为 6_ml_datasets/rd_mined。"""
    src_root = tmp_path / "quantcustom"
    dst_root = tmp_path / "quantdb"
    return prm.resolve_roots(src_root, dst_root)


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def test_scan_only_picks_dt_partitions(roots):
    """只认 dt=*/data.parquet；清单、别的目录一律不算分区。"""
    src, _ = roots
    prm.scan_partitions(src)  # 目录不存在 → 空
    assert prm.scan_partitions(src) == {}

    _write_partition(*_split(src), "20200102", b"a")
    (src / "_promote_manifest.json").write_text("{}", encoding="utf-8")
    (src / "not_a_partition").mkdir(parents=True, exist_ok=True)
    (src / "not_a_partition" / "data.parquet").write_bytes(b"x")

    found = prm.scan_partitions(src)
    assert set(found) == {"dt=20200102"}


# ---------------------------------------------------------------------------
# 计划：幂等 / 增量
# ---------------------------------------------------------------------------
def test_first_promote_copies_everything(roots):
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    _write_partition(*_split(src), "20200103", b"bb")

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))

    assert plan.to_copy == ("dt=20200102", "dt=20200103")
    assert plan.unchanged == ()
    assert not plan.is_noop


def test_second_promote_is_a_noop(roots):
    """幂等：镜像一次后再计划，零拷贝零删除。"""
    src, dst = roots
    for dt, payload in (("20200102", b"a"), ("20200103", b"bb")):
        _write_partition(*_split(src), dt, payload)
    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    prm.apply_sync(src, dst, plan)

    again = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))

    assert again.is_noop, f"重复毕业产生了写入：{again.summary()}"
    assert len(again.unchanged) == 2


def test_changed_mtime_triggers_recopy(roots):
    """内容变了（列集漂移会重写分区）→ 必须重拷，不能因为路径相同就跳过。"""
    src, dst = roots
    f = _write_partition(*_split(src), "20200102", b"a")
    prm.apply_sync(
        src, dst, prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    )

    f.write_bytes(b"changed-content")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10**9))

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    assert plan.to_copy == ("dt=20200102",)


def test_changed_size_triggers_recopy(roots):
    """同样大小但内容不同、且时间戳恰好没变 —— 按 size 兜住。"""
    src, dst = roots
    f = _write_partition(*_split(src), "20200102", b"aaaa")
    st = f.stat()
    prm.apply_sync(
        src, dst, prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    )

    f.write_bytes(b"bbbbbbbb")  # 长度不同
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # 时间戳还原

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    assert plan.to_copy == ("dt=20200102",)


def test_force_recopies_identical_partitions(roots):
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    prm.apply_sync(
        src, dst, prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    )

    forced = prm.plan_sync(
        prm.scan_partitions(src), prm.scan_partitions(dst), force=True
    )
    assert forced.to_copy == ("dt=20200102",)


# ---------------------------------------------------------------------------
# 删除语义：默认保守
# ---------------------------------------------------------------------------
def test_target_only_partitions_are_kept_by_default(roots):
    """源里没有的分区默认保留 —— 误删会让训练静默少一段历史。"""
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    _write_partition(*_split(dst), "20200103", b"old")

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))

    assert plan.to_prune == ()
    prm.apply_sync(src, dst, plan)
    assert (dst / "dt=20200103" / "data.parquet").is_file(), "默认不该删目标独有分区"


def test_prune_removes_target_only_partitions(roots):
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    _write_partition(*_split(dst), "20200103", b"old")

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst), prune=True)

    assert plan.to_prune == ("dt=20200103",)
    prm.apply_sync(src, dst, plan)
    assert not (dst / "dt=20200103").exists()


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------
def test_copied_bytes_match_source(roots):
    src, dst = roots
    payload = b"\x00\x01parquet-ish\xff"
    _write_partition(*_split(src), "20200102", payload)

    prm.apply_sync(
        src, dst, prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    )

    assert (dst / "dt=20200102" / "data.parquet").read_bytes() == payload


def test_no_tmp_files_left_behind(roots):
    """原子写留下的 .tmp 必须清干净，否则下次扫描会把它当未知文件。"""
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")

    prm.apply_sync(
        src, dst, prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    )

    assert list(dst.rglob("*.tmp")) == []


def test_dry_run_writes_nothing(roots):
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))

    result = prm.apply_sync(src, dst, plan, dry_run=True)

    assert result.copied == ["dt=20200102"]
    assert not dst.exists() or list(dst.rglob("data.parquet")) == []


# ---------------------------------------------------------------------------
# 边界：只动自己那一个库
# ---------------------------------------------------------------------------
def test_promote_never_touches_sibling_datasets(roots):
    """**镜像不是合并**：绝不允许写 l1_factors / l2_factors。

    CN 训练把 l1_factors 当行情源读；把它改了不会报错，只会让所有 CN 模型
    静默换了一套行情。这里用哨兵文件断言兄弟库字节不变。
    """
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    sentinels = {}
    for sibling in ("l1_factors", "l2_factors"):
        for root in (src, dst):
            p = _write_partition(
                root, sibling, "20200102", f"{sibling}-sentinel".encode()
            )
            sentinels[p] = p.read_bytes()

    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst), prune=True)
    prm.apply_sync(src, dst, plan)

    for path, before in sentinels.items():
        assert path.exists(), f"兄弟库被删：{path}"
        assert path.read_bytes() == before, f"兄弟库被改写：{path}"
    # 且源/目标兄弟库都没被卷进拷贝计划
    assert all("l1_factors" not in n and "l2_factors" not in n for n in plan.to_copy)


def test_apply_reports_failures_without_aborting_the_batch(roots):
    """单个分区失败要记进 failed（调用方据此非零退出），不能整批中断。"""
    src, dst = roots
    _write_partition(*_split(src), "20200102", b"a")
    _write_partition(*_split(src), "20200103", b"b")

    # 源文件在计划之后消失 —— 模拟并发写盘/被清理
    plan = prm.plan_sync(prm.scan_partitions(src), prm.scan_partitions(dst))
    (src / "dt=20200102" / "data.parquet").unlink()

    result = prm.apply_sync(src, dst, plan)

    assert [f["partition"] for f in result.failed] == ["dt=20200102"]
    assert result.copied == ["dt=20200103"], "一个失败不该拖垮其余分区"


# ---------------------------------------------------------------------------
# 根目录解析
# ---------------------------------------------------------------------------
def test_resolve_roots_prefers_explicit_args(tmp_path):
    src, dst = prm.resolve_roots(tmp_path / "s", tmp_path / "d")

    assert src == tmp_path / "s" / "6_ml_datasets" / "rd_mined"
    assert dst == tmp_path / "d" / "6_ml_datasets" / "rd_mined"


def test_resolve_roots_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv(prm._SOURCE_ENV, str(tmp_path / "envsrc"))
    monkeypatch.setenv(prm._TARGET_ENV, str(tmp_path / "envdst"))

    src, dst = prm.resolve_roots()

    assert src == tmp_path / "envsrc" / "6_ml_datasets" / "rd_mined"
    assert dst == tmp_path / "envdst" / "6_ml_datasets" / "rd_mined"


# ---------------------------------------------------------------------------
# 注册移交：discover() 的契约是 dict[str, dict]
# ---------------------------------------------------------------------------
class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_register_fields_consumes_dict_shaped_discover(monkeypatch):
    """``discover()`` 返回 ``{source: to_dict()}``；注册必须按字典取值。

    历史 bug：把 status 当对象用（``status.files``），``--register`` 一跑就
    AttributeError —— 镜像成功了，字段注册却整段缺席。
    """
    from backend.services.api.routers.admin import quantdb_factor_catalog as qfc
    from backend.services.engine.data_platform import quantdb_factor_reader as qfr
    from backend.shared import database_manager_v2 as dbm

    fake_status = {
        "dataset_id": prm.DATASET,
        "path": "/data/quantdb/6_ml_datasets/rd_mined",
        "files": 1637,
        "columns": ["symbol", "date", "alpha_001"],
        "column_types": {"symbol": "object", "alpha_001": "double"},
        "schema_hash": "deadbeef",
        "min_date": "2020-01-02",
        "max_date": "2026-09-30",
        "ready": True,
        "missing_required": [],
        "reason": None,
    }

    class FakeReader:
        def __init__(self, market=None):
            assert market == prm.TARGET_MARKET

        def discover(self, market=None):
            return {prm.DATASET: fake_status}

    seen: dict = {}

    async def fake_ensure_schema(session):
        seen["ensured"] = True

    async def fake_record(session, source, status, market):
        seen["source"], seen["status"], seen["market"] = source, status, market

    monkeypatch.setattr(qfr, "QuantDBFactorReader", FakeReader)
    monkeypatch.setattr(qfc, "_ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(qfc, "record_source_fields", fake_record)
    monkeypatch.setattr(dbm, "get_session", _FakeSession)

    assert prm._register_fields() == 0
    assert seen["ensured"] is True
    assert seen["source"] == prm.DATASET
    assert seen["market"] == prm.TARGET_MARKET
    assert seen["status"] is fake_status, "扫描结果要原样透传，别复制、别翻译"


def test_register_fields_missing_dataset_writes_nothing(monkeypatch):
    """CN 侧没发现该库 → 非零退出，且绝不进入 DB 会话。"""
    from backend.services.api.routers.admin import quantdb_factor_catalog as qfc
    from backend.services.engine.data_platform import quantdb_factor_reader as qfr
    from backend.shared import database_manager_v2 as dbm

    class FakeReader:
        def __init__(self, market=None):
            pass

        def discover(self, market=None):
            return {}

    def _must_not_open_session():
        raise AssertionError("未发现目标库时不该进入 DB 会话")

    async def _must_not_record(*args, **kwargs):
        raise AssertionError("未发现目标库时不该写注册表")

    monkeypatch.setattr(qfr, "QuantDBFactorReader", FakeReader)
    monkeypatch.setattr(qfc, "record_source_fields", _must_not_record)
    monkeypatch.setattr(dbm, "get_session", _must_not_open_session)

    assert prm._register_fields() == 1


# ---------------------------------------------------------------------------
# 目录发布（--publish）：superseded 列的三条收敛路径（T-FM-02）
# ---------------------------------------------------------------------------
class _Rows:
    """最小结果集替身：mappings()/scalars()/all()/first() 与 SQLAlchemy 同形。"""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _CatalogSession:
    """目录发布用最小会话：只认版本查询与 enabled 列查询，其余 SQL 响亮拒绝。

    「响亮拒绝」本身是断言：发布路径若悄悄发明了第三条 SQL，替身炸掉，
    而不是把意外写库当成通过。``rollback`` 记录次数（评审 M4/M3 的
    中止路径必须回滚，否则孤儿草稿/半截写会被会话上下文提交）。
    """

    def __init__(self, *, version=None, enabled=()):
        self._version = version
        self._enabled = list(enabled)
        self.queries: list[str] = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "qm_training_factor_catalog_version" in sql:
            self.queries.append("version")
            return _Rows([self._version] if self._version else [])
        if "qm_training_factor_mapping" in sql:
            self.queries.append("enabled")
            return _Rows(list(self._enabled))
        raise AssertionError(f"意外的 SQL：{sql}")

    def remove_enabled(self, columns) -> int:
        """模拟 disable 真正生效：从启用集移除并返回被移除的列数。"""
        drop = set(columns)
        before = len(self._enabled)
        self._enabled = [c for c in self._enabled if c not in drop]
        return before - len(self._enabled)

    async def rollback(self) -> None:
        self.rollbacks += 1


def _install_publish_env(
    monkeypatch,
    tmp_path,
    *,
    version,
    enabled,
    superseded,
    clone_rows=5,
    disable_works=True,
):
    """把 ``_publish_catalog`` 的 DB/清单依赖全换替身。

    返回 ``(flow, session, seen_manifest_roots)``。``disable_works=False``
    时 disable 替身「不生效」（rowcount=0、启用集不变），用于钉复核闸门
    会中止发布。``seen_manifest_roots`` 记录被顶替清单实际读自哪个根。
    """
    import contextlib

    from backend.scripts import rd_mined_materialize as mat
    from backend.services.api.routers.admin import quantdb_factor_catalog as qfc
    from backend.shared import database_manager_v2 as dbm

    session = _CatalogSession(version=version, enabled=enabled)
    flow: list[tuple] = []
    seen_manifest_roots: list = []

    @contextlib.asynccontextmanager
    async def _fake_session():
        yield session

    async def fake_ensure(_session):
        flow.append(("ensure",))

    async def fake_create(_session, dataset, name, market, created_by=None):
        flow.append(("create", dataset, market))
        return "v-new"

    async def fake_seed(_session, payload):
        flow.append(("seed", payload.get("version_id")))
        return {"seeded_fields": 7}

    async def fake_clone(_session, *, draft_id, market, source_dataset):
        flow.append(("clone", draft_id))
        return clone_rows

    async def fake_disable(_session, version_id, columns):
        flow.append(("disable", version_id, list(columns)))
        if not disable_works:
            return 0  # 模拟 UPDATE 命中 0 行：禁用没生效
        return session.remove_enabled(columns)

    async def fake_publish(_session, version_id):
        flow.append(("publish", version_id))

    manifest = {
        f"f-{col}": {"status": "superseded", "column": col} for col in superseded
    }

    def fake_load_manifest(root):
        seen_manifest_roots.append(Path(root))
        return manifest

    monkeypatch.setattr(qfc, "_ensure_schema", fake_ensure)
    monkeypatch.setattr(qfc, "create_catalog_draft", fake_create)
    monkeypatch.setattr(qfc, "seed_catalog_mappings", fake_seed)
    monkeypatch.setattr(qfc, "seed_draft_from_published", fake_clone)
    monkeypatch.setattr(qfc, "disable_catalog_mappings", fake_disable)
    monkeypatch.setattr(qfc, "publish_catalog_version", fake_publish)
    monkeypatch.setattr(dbm, "get_session", _fake_session)
    monkeypatch.setattr(mat, "_load_manifest", fake_load_manifest)
    monkeypatch.setattr(prm, "resolve_roots", lambda: (tmp_path / "s", tmp_path / "t"))
    return flow, session, seen_manifest_roots


@pytest.mark.unit
def test_publish_noop_when_published_has_no_superseded(monkeypatch, tmp_path):
    """线上目录已不含被顶替列（启用面板干净）→ 真正的 no-op，不发新版本。"""
    flow, _session, seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-pub", "status": "published"},
        enabled=["c_a"],
        superseded={"c_dead"},
    )
    assert prm._publish_catalog() == 0
    assert flow == [("ensure",)]
    src, _dst = prm.resolve_roots()
    assert seen == [src], "缺省源根下清单读自 resolve_roots 的源库"


@pytest.mark.unit
def test_publish_republishes_when_published_still_enables_superseded(
    monkeypatch, tmp_path
):
    """线上仍启用被顶替列 → 克隆线上（保手工勾选）→ 禁用 → 重发布。

    已发布版本不可改写，也绝不静默放过：拒绝走「原地改旧版」或「不管它」。
    """
    flow, session, _seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-pub", "status": "published"},
        enabled=["c_a", "c_dead"],
        superseded={"c_dead"},
    )
    assert prm._publish_catalog() == 0

    assert ("create", prm.DATASET, prm.TARGET_MARKET) in flow
    assert ("clone", "v-new") in flow
    assert ("disable", "v-new", ["c_dead"]) in flow
    assert (
        flow.index(("clone", "v-new"))
        < flow.index(("disable", "v-new", ["c_dead"]))
        < flow.index(("publish", "v-new"))
    ), "顺序：先克隆（吃到现状）再禁用再发布"
    assert ("publish", "v-pub") not in flow, "旧版本绝不被当成新草稿发布"
    assert session.queries == ["version", "enabled", "enabled"], (
        "第一次查线上启用面，第二次是禁用后的复核（评审 M3）"
    )


@pytest.mark.unit
def test_publish_aborts_when_clone_from_published_is_empty(monkeypatch, tmp_path):
    """线上启用着被顶替列却一行没克隆到 → 回滚 + 中止（rc=1），绝不发布空版本。

    发布会把旧版转 archived 且不可逆：克隆得 0 行说明中间层坏了，
    此时发布等于把线上训练口径清空。必须 rollback——不 rollback 的话
    get_session 会把「正常 return」当成功提交，孤儿空草稿被后续 promote
    选中、publish 又失败，目录永久卡死（评审 M4）。
    """
    flow, session, _seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-pub", "status": "published"},
        enabled=["c_dead"],
        superseded={"c_dead"},
        clone_rows=0,
    )
    assert prm._publish_catalog() == 1
    assert ("publish", "v-new") not in flow
    assert not any(step[0] == "disable" for step in flow)
    assert session.rollbacks == 1, "中止必须回滚，不给孤儿草稿留提交机会"


@pytest.mark.unit
def test_publish_aborts_when_disable_does_not_take_effect(monkeypatch, tmp_path):
    """禁用没生效（rowcount=0、启用集没变）→ 复核逮住 → 回滚中止发布。

    评审 M3：发布不可逆，带着仍启用的已下架列发布 = 下架决定当场作废。
    禁用行数只进日志是不够的。
    """
    flow, session, _seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-draft", "status": "draft"},
        enabled=["c_dead"],
        superseded={"c_dead"},
        disable_works=False,
    )
    assert prm._publish_catalog() == 1
    assert not any(step[0] == "publish" for step in flow)
    assert session.rollbacks == 1


@pytest.mark.unit
def test_publish_reads_superseded_from_explicit_source_root(monkeypatch, tmp_path):
    """显式源根穿透到清单读取（评审 M5）：--source-root 指到别处时，
    被顶替清单必须跟着走——否则读默认库下架、给指定库发版，口径劈叉。"""
    explicit = tmp_path / "elsewhere" / "6_ml_datasets" / "rd_mined"
    flow, _session, seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-pub", "status": "published"},
        enabled=["c_a"],
        superseded={"c_dead"},
    )
    assert prm._publish_catalog(explicit) == 0
    assert seen == [explicit]
    assert flow == [("ensure",)]


@pytest.mark.unit
def test_main_threads_resolved_source_root_into_publish(monkeypatch, tmp_path):
    """main() 把 CLI 解析出的实际源根传给 _publish_catalog（评审 M5）。"""
    src_root = tmp_path / "quantcustom"
    dst_root = tmp_path / "quantdb"
    _write_partition(src_root, prm.DATASET, "20200102", b"a")

    seen: list = []

    def fake_publish(root=None):
        seen.append(root)
        return 0

    monkeypatch.setattr(prm, "_publish_catalog", fake_publish)

    rc = prm.main(
        [
            "--source-root",
            str(src_root),
            "--target-root",
            str(dst_root),
            "--publish",
        ]
    )

    assert rc == 0
    src, _dst = prm.resolve_roots(src_root, dst_root)
    assert seen == [src], "发布读清单的源根必须是 CLI 解析出的那个"


@pytest.mark.unit
def test_publish_disables_before_publishing_existing_draft(monkeypatch, tmp_path):
    """既有草稿：发布前把被顶替列禁用（列序按 sorted，确定性）。"""
    flow, session, _seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version={"version_id": "v-draft", "status": "draft"},
        enabled=[],
        superseded={"c_dead", "c_dead2"},
    )
    assert prm._publish_catalog() == 0
    assert flow[-2] == ("disable", "v-draft", ["c_dead", "c_dead2"])
    assert flow[-1] == ("publish", "v-draft")
    assert not any(step[0] in ("create", "seed", "clone") for step in flow)
    assert session.queries == ["version", "enabled"], (
        "草稿分支只多一次禁用后复核（评审 M3）"
    )


@pytest.mark.unit
def test_publish_new_draft_seeds_then_disables_then_publishes(monkeypatch, tmp_path):
    """无任何版本：新建 → 全量播种 → 禁用被顶替列 → 发布。"""
    flow, _session, _seen = _install_publish_env(
        monkeypatch,
        tmp_path,
        version=None,
        enabled=[],
        superseded={"c_dead"},
    )
    assert prm._publish_catalog() == 0
    assert [step[0] for step in flow] == [
        "ensure",
        "create",
        "seed",
        "disable",
        "publish",
    ]
    assert flow[3] == ("disable", "v-new", ["c_dead"])


# ---------------------------------------------------------------------------
# 推荐集写目录：剔除已顶替下架列（评审 M8）
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_drop_superseded_filters_rd_mined_recommendation_only(monkeypatch, tmp_path):
    """apply 脚本写目录前剔除已下架列（评审 M8）。

    脚本会把推荐集列重置 ``enabled = TRUE``；对 rd_mined 而言，被顶替下架
    的列只要仍留在推荐集里就会被这一步**复活**进训练口径。其它数据集没有
    替换机制，原样放行（不误剔）。
    """
    from backend.scripts import apply_factor_selection_to_catalog as afs
    from backend.scripts import rd_mined_materialize as mat

    manifest = {
        "f1": {"status": "superseded", "column": "c_dead"},
        "f2": {
            "status": "rejected_duplicate",
            "column": "c_carried",
            "superseded_by": "f1",
        },
    }
    seen_roots: list = []

    def fake_load(root):
        seen_roots.append(root)
        return manifest

    monkeypatch.setattr(mat, "_load_manifest", fake_load)
    monkeypatch.setattr(prm, "resolve_roots", lambda: (tmp_path / "s", tmp_path / "t"))

    kept, dropped = afs._drop_superseded("rd_mined", {"c_dead", "c_ok", "c_carried"})

    assert kept == {"c_ok"}
    assert dropped == {"c_dead", "c_carried"}, (
        "并集判据：带 superseded_by 标记的重跑条目同样算下架"
    )
    assert seen_roots == [tmp_path / "s"], "清单读自 resolve_roots 的源根"

    same = {"c_dead", "c_ok"}
    assert afs._drop_superseded("l1_factors", same) == (same, set())
