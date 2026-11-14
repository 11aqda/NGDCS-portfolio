"""玩家运动学：纯函数，不碰 socket / asyncio，所以能直接单测。"""

from __future__ import annotations

from protocol.messages import KEY_LEFT, KEY_RIGHT, KEYS_MASK
from protocol.world import PLAYER_MARGIN, WORLD_WIDTH

# 15 px/tick × 20 tick/s = 300 px/s → 2.4 秒横穿 720 px 屏幕
PLAYER_SPEED_PER_TICK = 15

MIN_X = PLAYER_MARGIN
MAX_X = WORLD_WIDTH - PLAYER_MARGIN


def keys_to_dx(keys: int) -> int:
    """把按键位掩码翻译成这一 tick 的水平位移（像素）。"""
    keys &= KEYS_MASK
    direction = (1 if keys & KEY_RIGHT else 0) - (1 if keys & KEY_LEFT else 0)
    return direction * PLAYER_SPEED_PER_TICK


def clamp_x(x: int) -> int:
    """把横坐标钳制在场地内，留 PLAYER_MARGIN 的余量。"""
    return max(MIN_X, min(MAX_X, x))


def apply_input(x: int, keys: int) -> int:
    """走一个 tick：先按输入位移，再钳制。"""
    return clamp_x(x + keys_to_dx(keys))