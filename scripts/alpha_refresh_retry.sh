#!/usr/bin/env bash
# alpha_library 跳过后自动重试 + **把下游接上**（宿主 cron，JST 05:30 / 06:30）
#
# 为什么需要它（2026-10-13 事故）：alpha_library_refresh.sh 的内存闸门（可用内存
# <30GB 跳过，实测峰值 RSS ~24GB、swap 已满）把它整轮跳了 → alpha 停在 dt=20261009
# 而日K 已到 dt=20261012 → CUSTOM 重建撞 fail-closed「源库分区缺失…拒绝写入」→
# 6 个模型推理全撞「主模型维度门禁未通过」→ 当天候选池根本不存在 → 决策轮 rows=0，
# `l2.pool_not_member` 拦下全部开仓。而这一切**要等人**：第一个告警是开盘后 09:35
# 决策轮跑起来才发的（`empty_pool`），当天全部开仓机会已经错过。
#
# 为什么光多排几条 cron 去调刷新脚本不够：CUSTOM 的定时派发在 03:00 CST（=容器墙钟），
# 标记键 `quantmind:sync_schedule_last_run:CUSTOM:{date}` 一旦置位，market-sync-dispatch
# 当天不会再派。所以 05:30 之后补出的 alpha **不会**自动带动 CUSTOM 重建——本脚本
# 必须自己把下游接上，否则只是把 alpha 补新、CUSTOM 照样停在旧分区（假自愈）。
#
# 职责（只做「缺了才补」和「补完接下游」，其余交给已有的幂等链路）：
#   ① 读分区算 AL_DT / KL_DT（**不依赖 Redis**：宿主没装 redis-py）
#   ② 已最新 → 静默退出（绝大多数日子走这条，代价只有两次 ls）
#   ③ 串行调 alpha_library_refresh.sh（它自带 flock / 已最新 / 可写 / 内存四闸，盲跑无害）
#   ④ **重新实测**：仍没补新（内存还是不够等）→ 记日志退出，不猜也不硬来
#   ⑤ 补新成功 → 接下游：CUSTOM 重建（容器内 run_market_sync，复用唯一实现：三库增量
#      + 增量守卫 + fail-closed 源库预检）+ 备选池重算（postmarket_pipeline --picks-only，
#      恒重算幂等；缺推理批次时它自己会补跑推理 —— 已是既有路径）
#
# 时刻怎么定的：宿主 JST 05:30 = 北京 04:30（alpha 实测 43–53 分钟）→ 06:20 前后收工
# → CUSTOM 秒级 → 推理 ~6 分钟 → 池 ~06:30（北京）落地，**早于容器 08:00 CST 盘前推理、
# 远早于 09:30 开盘**。第 2 档 06:30 JST 是「第 1 档跑完仍缺」时的再试（例如内存到
# 05:30 还没释放），两档都在盘前。
#
# 告警分工：**盘前告警归 scripts/preopen_pool_check.py（JST 08:50）**，那一份会带五段链条
# 明细。本脚本只在**自愈成功**时推 🟢（这是非常事件，要说一声），失败默认只记日志；
# 只有被显式标为当日最后一档（`QM_RETRY_FINAL=1`）时才推 🟠，避免凌晨连推两条。
#
# 闸门：全程 flock 防重入；CUSTOM 调度被用户关掉时**尊重用户设置**，只补 alpha 不代跑重建。
#
# 用法::
#
#     bash scripts/alpha_refresh_retry.sh              # 正常跑
#     bash scripts/alpha_refresh_retry.sh --dry-run    # 只打印判断，不刷新/不接下游/不推 QQ
#     QM_RETRY_NOTIFY=0 ...                            # 静音 QQ（测试/手工排查）
#     QM_RETRY_FINAL=1 ...                             # 标记为当日最后一档（失败也推）
set -uo pipefail

QM_ROOT="${QM_ROOT:-/home/zbox/projects/quantmind}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

SCRIPT="$QM_ROOT/scripts/alpha_library_refresh.sh"
ALPHA_ROOT="$QM_ROOT/data/quantdb/6_ml_datasets/alpha_library"
KLINE_ROOT="$QM_ROOT/data/quantdb/1_kline_data/daily_forward"
LOG="$QM_ROOT/logs/keepers/alpha_refresh_retry.log"
LOCK="${QM_RETRY_LOCK:-/tmp/.qm_alpha_retry.lock}"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p "$(dirname "$LOG")" 2>/dev/null
log_line() { echo "$(date '+%F %T') $*" >> "$LOG"; }

notify() {  # 推 QQ；失败只记日志（告警推不出去不能让 cron 报错刷屏）
    local title="$1" body="$2"
    if [ "${QM_RETRY_NOTIFY:-1}" != "1" ]; then
        log_line "（已静音 QM_RETRY_NOTIFY=0，未推：$title）"
        return 0
    fi
    local out rc
    out=$(cd "$QM_ROOT" && "$PYTHON_BIN" -c '
import sys
sys.path.insert(0, "scripts")
from push_notify import notify
notify(sys.argv[1], sys.argv[2])
' "$title" "$body" 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ]; then
        log_line "已推 QQ：$title"
    else
        log_line "⚠️ QQ 推送失败（rc=$rc，链路状态不受影响）：$(echo "$out" | tail -2 | tr '\n' ' ')"
    fi
}

newest_dt() { ls -d "$1"/dt=* 2>/dev/null | grep -oE '[0-9]{8}' | sort | tail -1; }

# 1) 防重入（两档 cron 可能撞在同一个还没跑完的窗口里）
exec 9>"$LOCK"
if ! flock -n 9; then
    log_line "跳过：上一次重试仍在运行"
    exit 0
fi

KL_DT=$(newest_dt "$KLINE_ROOT")
AL_DT=$(newest_dt "$ALPHA_ROOT")

if [ -z "$KL_DT" ]; then
    log_line "跳过：日K 分区目录为空（$KLINE_ROOT）"
    exit 0
fi
if [ -n "$AL_DT" ] && [ "$AL_DT" -ge "$KL_DT" ]; then
    # 链是好的——绝大多数日子走这条。写一行日志便于确认守护在跑，但不推 QQ。
    log_line "无事：已最新（alpha=$AL_DT kline=$KL_DT）"
    exit 0
fi

AVAIL_GB=$(awk '/^MemAvailable:/ {print int($2/1024/1024)}' /proc/meminfo)
log_line "alpha 滞后（alpha=${AL_DT:-无} kline=$KL_DT，可用内存 ${AVAIL_GB:-?}GB）→ 重试刷新"

if [ "$DRY_RUN" = "1" ]; then
    log_line "[dry-run] 将执行：bash $SCRIPT；随后 CUSTOM 重建 + postmarket --picks-only"
    exit 0
fi

# 2) 串行调刷新脚本（它自己会再判一次内存/可写/已最新，全部幂等）
bash "$SCRIPT" >> "$LOG" 2>&1
REFRESH_RC=$?
AL_NEW=$(newest_dt "$ALPHA_ROOT")

if [ -z "$AL_NEW" ] || [ "$AL_NEW" -lt "$KL_DT" ]; then
    MSG="alpha 仍滞后（alpha=${AL_NEW:-无} kline=$KL_DT，刷新 rc=$REFRESH_RC，可用内存 ${AVAIL_GB:-?}GB）——本轮 gate 见 alpha_library/cron_run.log"
    log_line "❌ $MSG"
    if [ "${QM_RETRY_FINAL:-0}" = "1" ]; then
        notify "🟠 补池重试未补上 alpha 因子库" \
"$MSG

候选池今天仍会缺——开盘前决策轮会因「候选池非成员」拦下全部新开仓。
下一步：确认内存后手工跑 scripts/alpha_refresh_retry.sh，或看后台「数据管理」页的补池就绪卡。"
    fi
    exit 1
fi

log_line "✅ alpha 已补齐（alpha=$AL_NEW kline=$KL_DT，刷新 rc=$REFRESH_RC）→ 接下游"

# 3) 下游① CUSTOM 数据集重建：容器内跑，复用唯一实现。
#    调度被用户关掉时尊重用户设置（只补 alpha，不代跑）；enabled=true 才代跑一次
#    （幂等：已最新则秒退，实测 0.09s）。
CUSTOM_OUT=$("$DOCKER_BIN" exec -i -w /app -e PYTHONPATH=/app quantmind "$PYTHON_BIN" - <<'PY' 2>&1
import json, os, sys
import redis
r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), socket_timeout=3)
raw = r.get("quantmind:sync_schedule:CUSTOM")
cfg = json.loads(raw) if raw else {}
if not cfg.get("enabled"):
    print("SKIP: CUSTOM 调度未启用，尊重用户设置不代跑")
    sys.exit(0)
from backend.services.engine.tasks.market_sync_scheduler import run_market_sync
res = run_market_sync("CUSTOM", cfg) or {}
inner = res.get("result") or {}
print(
    "OK: CUSTOM rebuild"
    f" mode={inner.get('mode')} written={inner.get('written')}"
    f" n_factors={inner.get('n_factors')} elapsed={inner.get('elapsed_s')}s"
    f" libs={res.get('libraries')}"
)
PY
)
CUSTOM_RC=$?
log_line "CUSTOM 重建 rc=$CUSTOM_RC：$(echo "$CUSTOM_OUT" | tail -2 | tr '\n' ' ')"

# 4) 下游② 备选池重算（宿主脚本；恒重算幂等，缺推理批次时它自己会补跑推理）
PICKS_OUT=$(cd "$QM_ROOT" && "$PYTHON_BIN" scripts/postmarket_pipeline.py --picks-only 2>&1)
PICKS_RC=$?
log_line "备选池重算 rc=$PICKS_RC：$(echo "$PICKS_OUT" | tail -3 | tr '\n' ' ')"

if [ "$CUSTOM_RC" -eq 0 ] && [ "$PICKS_RC" -eq 0 ]; then
    log_line "🚑 自愈完成（alpha=$AL_NEW）"
    notify "🟢 补池自愈完成" \
"alpha 因子库原被跳过（内存闸门），本轮自动补齐 dt=$AL_NEW，CUSTOM 重建与备选池重算已接上。

开盘前请看一眼后台「数据管理」页的补池就绪卡确认。"
    exit 0
fi

log_line "⚠️ alpha 已补但下游未走完（CUSTOM rc=$CUSTOM_RC，备选池 rc=$PICKS_RC）"
notify "🟠 补池自愈半途" \
"alpha 已补齐 dt=$AL_NEW，但下游没走完：CUSTOM 重建 rc=$CUSTOM_RC，备选池 rc=$PICKS_RC。

详见 logs/keepers/alpha_refresh_retry.log（本机 JST）。"
exit 1
