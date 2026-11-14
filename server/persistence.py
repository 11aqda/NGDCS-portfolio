"""TCP 服务端 ↔ Django 数据层的桥。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_READY = False
_UNAVAILABLE = False


def _ensure_django() -> bool:
    """懒加载 Django。只初始化一次，失败也记住，不再重试刷屏。"""
    global _READY, _UNAVAILABLE
    if _READY:
        return True
    if _UNAVAILABLE:
        return False

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")
    try:
        import django

        django.setup()
        _READY = True
        return True
    except Exception as exc:  # noqa: BLE001
        _UNAVAILABLE = True
        logger.warning("Django 初始化失败，成绩持久化不可用：%s", exc)
        return False


def _authenticate_blocking(token: str) -> tuple[int, str] | None:
    """token → (user_id, nickname)。无效返回 None。"""
    if not _ensure_django():
        return None
    from accounts.models import AuthToken

    row = (
        AuthToken.objects.filter(token=token)
        .values("user_id", "user__username")
        .first()
    )
    if row is None:
        return None
    return row["user_id"], row["user__username"]


def _record_blocking(user_id: int, score: int, level: int, kills: int) -> bool:
    if not _ensure_django():
        return False
    from accounts.scoring import record_result

    record_result(user_id, score, level=level, kills=kills)
    return True


async def authenticate_token(token: str) -> tuple[int, str] | None:
    """异步包装：真正的查询在线程池里跑，不阻塞事件循环。"""
    try:
        return await asyncio.to_thread(_authenticate_blocking, token)
    except Exception as exc:  # noqa: BLE001 - 数据库故障不该让连接崩掉
        logger.warning("token 认证失败（数据库不可用？）：%s", exc)
        return None


async def persist_score(user_id: int, score: int, level: int = 1, kills: int = 0) -> bool:
    """异步写一局成绩（MySQL + Redis，写透）。失败只记日志。"""
    try:
        return await asyncio.to_thread(_record_blocking, user_id, score, level, kills)
    except Exception as exc:  # noqa: BLE001
        logger.warning("成绩写入失败（不影响对局）：%s", exc)
        return False