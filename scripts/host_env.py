"""宿主脚本的 PG 地址解析（**唯一实现**）。

为什么需要它：宿主上的常驻/手工脚本连 PG 时读 ``POSTGRES_HOST``，而这个变量**只有
crontab 头部有**——``quantmind-db`` 只发布在 LAN 地址上（回环 5432 无监听）。于是
「cron 里一切正常、手工一跑就炸」：

* 2026-10-13 ``alpha_refresh_retry.sh`` 首次手工实跑 → 备选池重算整步
  ``Connection refused``；
* 同日 ``preopen_pool_check.py`` 手工跑 → 推理探针连不上 → 第 ④ 段被判「**断**」
  → 推出一条**假**的「候选池未就绪」告警（链其实是通的，方向闸按设计清空）。

地址的**唯一事实源**是 `.env` 里的 ``QM_DB_BIND``：``docker-compose.yml`` 就是拿它
发布 db 端口的（``${QM_DB_BIND:-127.0.0.1}:5432:5432``），``scripts/boot_guard.sh``
也读同一个键。这里做同一件事，**不另立第二个键**。

只读 `.env`（宿主侧文件），不读 ``config/runtime.env``（那是密钥面，且宿主上通常
root-only）；容器内 ``POSTGRES_HOST`` 恒已由 compose 注入，故本模块在容器里是空转。

用法::

    from host_env import resolve_postgres_host, ensure_postgres_host

    resolve_postgres_host()   # -> "192.168.31.68" / None（纯读，不碰环境）
    ensure_postgres_host()    # 未设且可解析时写进 os.environ，返回结果
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 「DB 端口发布到哪个地址」的唯一键（compose 与 boot_guard.sh 同源）。
DB_BIND_KEY = "QM_DB_BIND"
#: 环境变量名（与 ``postmarket_pipeline._pg_connect`` / ``pool_readiness`` 同一契约）。
POSTGRES_HOST_KEY = "POSTGRES_HOST"

#: 这些取值表示「就是本机/未指定」，补进 ``POSTGRES_HOST`` 没有任何意义——
#: 而 ``127.0.0.1`` 恰恰是各脚本自身的缺省值，等于什么都没做。
_LOOPBACK = {"", "127.0.0.1", "0.0.0.0", "localhost", "::1"}


def read_env_value(path: str | Path, key: str) -> str:
    """从 ``.env`` 风格文件里取单键（去首尾引号）；无文件/无键返回空串。

    与 ``scripts/boot_guard.sh`` 的 ``env_get`` 同口径：取**最后一条**匹配，
    引号成对剥掉。带 ``export `` 前缀的写法也认。
    """
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export ") :].strip()
        if k == key:
            value = v.strip().strip("'\"")
    return value


def resolve_postgres_host() -> str | None:
    """``.env`` 的 ``QM_DB_BIND`` → 可用的 ``POSTGRES_HOST``；回环/未设 → ``None``。

    返回 ``None`` 的语义是「**没有可用的地址**」，不是「地址就是回环」——调用方
    据此保持各自缺省（不假装知道地址，也不把回环当结论）。
    """
    bind = read_env_value(ROOT / ".env", DB_BIND_KEY)
    return None if bind.lower() in _LOOPBACK else bind


def ensure_postgres_host() -> str | None:
    """未设 ``POSTGRES_HOST`` 且能解析出地址时写进环境；返回最终值（可能 ``None``）。

    幂等：显式设过的（crontab 头部、部署方 export）一律尊重，本函数一个字节都不改。
    """
    current = os.environ.get(POSTGRES_HOST_KEY, "").strip()
    if current:
        return current
    resolved = resolve_postgres_host()
    if resolved:
        os.environ[POSTGRES_HOST_KEY] = resolved
    return resolved
