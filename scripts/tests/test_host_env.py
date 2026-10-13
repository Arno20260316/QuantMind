"""宿主 PG 地址解析的单测（``scripts/host_env.py``）。

为什么值得测：这个模块存在的唯一理由是「cron 里好好的、手工一跑就炸」。它一错，错的
方向是**静默**的——解析不出就是不 export，各脚本照样退回回环 ``127.0.0.1``，症状是
`Connection refused`，看上去像网络问题而不像给你补地址的那个函数没干活。

三条纪律各锁一组用例：

* 地址的事实源只有 ``.env`` 的 ``QM_DB_BIND``（compose 与 boot_guard.sh 同源），
  这里**不许另立第二个键**；
* 回环 / 未设 → 返回 ``None``（「没有可用地址」不是「地址就是回环」，调用方据此保持
  自己的缺省，别假装知道）；
* 显式设过 ``POSTGRES_HOST`` 的一律尊重，一个字节都不改。

跑法::

    python3 -m pytest scripts/tests/test_host_env.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/

import host_env as he  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """把 ``host_env.ROOT`` 指到 tmp 仓库（模块按所在目录推仓库根）。"""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(he, "ROOT", root)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    return root


def _write_env(root: Path, body: str) -> None:
    (root / ".env").write_text(body, encoding="utf-8")


class TestReadEnvValue:
    def test_missing_file_returns_empty(self, repo):
        assert he.read_env_value(repo / ".env", "QM_DB_BIND") == ""

    def test_strips_quotes_and_export_prefix(self, repo):
        p = repo / ".env"
        p.write_text(
            'QM_DB_BIND="10.1.2.3"\n'
            "export QM_OTHER='10.9.9.9'\n"
            "# QM_DB_BIND=commented-out\n",
            encoding="utf-8",
        )
        assert he.read_env_value(p, "QM_DB_BIND") == "10.1.2.3"
        assert he.read_env_value(p, "QM_OTHER") == "10.9.9.9"

    def test_last_occurrence_wins(self, repo):
        """与 ``boot_guard.sh`` 的 ``env_get``（``tail -1``）同口径。"""
        p = repo / ".env"
        p.write_text("QM_DB_BIND=1.1.1.1\nQM_DB_BIND=2.2.2.2\n", encoding="utf-8")
        assert he.read_env_value(p, "QM_DB_BIND") == "2.2.2.2"

    def test_substring_key_is_not_matched(self, repo):
        """``QM_DB_BIND_BACKUP`` 不许被当成 ``QM_DB_BIND``（前缀匹配的经典坑）。"""
        p = repo / ".env"
        p.write_text("QM_DB_BIND_BACKUP=9.9.9.9\n", encoding="utf-8")
        assert he.read_env_value(p, "QM_DB_BIND") == ""


class TestResolvePostgresHost:
    def test_lan_bind_resolves(self, repo):
        _write_env(repo, "DB_HOST=db\nQM_DB_BIND=192.168.31.68\n")
        assert he.resolve_postgres_host() == "192.168.31.68"

    @pytest.mark.parametrize(
        "value", ["", "127.0.0.1", "0.0.0.0", "localhost", "::1", " 127.0.0.1 "]
    )
    def test_loopback_and_unset_resolve_to_none(self, repo, value):
        """回环/未设 → ``None``（「没有可用地址」，不是「地址就是回环」）。"""
        _write_env(repo, f"QM_DB_BIND={value}\n")
        assert he.resolve_postgres_host() is None

    def test_missing_env_file_resolves_to_none(self, repo):
        assert he.resolve_postgres_host() is None


class TestEnsurePostgresHost:
    def test_explicit_env_is_respected_untouched(self, repo, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "10.9.9.9")
        _write_env(repo, "QM_DB_BIND=192.168.31.68\n")
        assert he.ensure_postgres_host() == "10.9.9.9"
        # 一个字节都不许改（crontab 头部与部署方 export 是权威）
        import os

        assert os.environ["POSTGRES_HOST"] == "10.9.9.9"

    def test_unset_is_filled_from_env_file(self, repo):
        import os

        _write_env(repo, "QM_DB_BIND=10.1.2.3\n")
        assert he.ensure_postgres_host() == "10.1.2.3"
        assert os.environ["POSTGRES_HOST"] == "10.1.2.3"

    def test_unresolvable_leaves_environment_alone(self, repo):
        """解析不出 → **不 export**，调用方保持自身缺省（现在是回环，这是诚实的状态）。"""
        import os

        _write_env(repo, "QM_DB_BIND=127.0.0.1\n")
        assert he.ensure_postgres_host() is None
        assert "POSTGRES_HOST" not in os.environ

    def test_idempotent_second_call(self, repo):
        import os

        _write_env(repo, "QM_DB_BIND=10.1.2.3\n")
        he.ensure_postgres_host()
        os.environ["POSTGRES_HOST"] = "10.5.5.5"  # 有人改过
        assert he.ensure_postgres_host() == "10.5.5.5"  # 不覆盖

    def test_blank_env_value_is_treated_as_unset(self, repo, monkeypatch):
        """``POSTGRES_HOST=""``（compose 注入空串那种）与未设同义，允许补上。"""
        monkeypatch.setenv("POSTGRES_HOST", "   ")
        _write_env(repo, "QM_DB_BIND=10.1.2.3\n")
        assert he.ensure_postgres_host() == "10.1.2.3"
