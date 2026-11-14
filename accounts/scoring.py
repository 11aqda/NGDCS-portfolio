"""成绩写入：同时写 MySQL 和 Redis（写透缓存）。"""

from __future__ import annotations

import logging

from django.conf import settings

from accounts.models import ScoreRecord

logger = logging.getLogger(__name__)

# 熔断：Redis 写失败后短时间内不再尝试，避免每局都白等一个超时
_REDIS_DOWN_UNTIL = 0.0
_REDIS_BREAKER_SECONDS = 5.0
_REDIS_TIMEOUT = 0.25


def _redis_client():
    """和 `accounts/views.py` 里同一套配置（RESP2 + 禁重试 + 紧超时）。"""
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    return redis.Redis(
        **settings.REDIS,
        protocol=2,
        socket_connect_timeout=_REDIS_TIMEOUT,
        socket_timeout=_REDIS_TIMEOUT,
        retry=Retry(NoBackoff(), 0),
    )


# 不能用 `ZADD ... GT`：`GT` 是 Redis 6.2 才加的选项
# 本机是 5.0.14.1（Windows 版），直接 `ZADD key GT score member` 会报
# `syntax error`（2026-11-01 实测，写缓存全部静默失败）。
# 所以用一段 Lua 做原子的"只在更高分时更新"——Lua 在 5.0 上可用
# 而且比"先 ZSCORE 再 ZADD"更安全（没有读改写竞态）。
_ZADD_IF_HIGHER = """
local current = redis.call('ZSCORE', KEYS[1], ARGV[1])
if (not current) or tonumber(ARGV[2]) > tonumber(current) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
  return 1
end
return 0
"""


def update_leaderboard_cache(user_id: int, best_score: int) -> bool:
    """把用户最高分写进 ZSET。失败只记日志，绝不影响成绩入库。"""
    global _REDIS_DOWN_UNTIL
    import time

    if time.monotonic() < _REDIS_DOWN_UNTIL:
        return False

    try:
        client = _redis_client()
        client.eval(_ZADD_IF_HIGHER, 1, settings.LEADERBOARD_KEY,
                    str(user_id), str(int(best_score)))
        return True
    except Exception as exc:  # noqa: BLE001
        _REDIS_DOWN_UNTIL = time.monotonic() + _REDIS_BREAKER_SECONDS
        logger.warning("排行榜缓存写入失败（不影响入库）：%s", exc)
        return False


def record_result(user_id: int, score: int, level: int = 1, kills: int = 0) -> ScoreRecord:
    """记一局的成绩：先写 MySQL（真相来源），再更新 Redis（读路径）。"""
    record = ScoreRecord.objects.create(
        user_id=user_id, score=int(score), level=int(level), kills=int(kills)
    )
    best = (
        ScoreRecord.objects.filter(user_id=user_id)
        .order_by("-score")
        .values_list("score", flat=True)
        .first()
    )
    update_leaderboard_cache(user_id, int(best or score))
    return record


def rebuild_cache_from_mysql() -> int:
    """把 MySQL 里的最高分全量灌进 ZSET —— Redis 重启后用它重建。返回条数。"""
    from django.db.models import Max

    rows = ScoreRecord.objects.values("user_id").annotate(best=Max("score"))
    count = 0
    for row in rows:
        if update_leaderboard_cache(row["user_id"], int(row["best"])):
            count += 1
    return count