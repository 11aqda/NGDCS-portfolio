"""插值误差（`err_px`）的核心算法 —— E4 / E5 图 6-8~6-10、6-12 的唯一数据来源。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]

# 客户端渲染用的帧间隔基准。真实间隔应当用两帧的时间差算（下面就是这么做的），
# 这个常量只在"拿不到时间戳"时兜底（旧客户端的快照没有时间戳）。
DEFAULT_FRAME_S = 0.05


def clamp_alpha(now: float, t_prev: float, t_curr: float) -> float:
    """算插值系数，与客户端渲染完全一致：钳在 [0, 1]，不许外推。"""
    span = t_curr - t_prev
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (now - t_curr) / span))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_point(prev: Point, curr: Point, alpha: float) -> Point:
    return (lerp(prev[0], curr[0], alpha), lerp(prev[1], curr[1], alpha))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class PlayerInterp:
    """跟踪一个玩家的历史位置，并在每帧到达时给出"刚才显示得有多偏"。"""

    _prev: tuple[float, Point] | None = None
    _curr: tuple[float, Point] | None = None
    # 累计统计，便于直接上报
    samples: list[float] = field(default_factory=list)

    def displayed(self, now: float) -> Point | None:
        """这一时刻客户端会显示的位置。没有历史时返回 None。"""
        if self._curr is None:
            return None
        if self._prev is None:
            return self._curr[1]

        t_prev, p_prev = self._prev
        t_curr, p_curr = self._curr
        alpha = clamp_alpha(now, t_prev, t_curr)
        return lerp_point(p_prev, p_curr, alpha)

    def ingest(self, now: float, actual: Point) -> float | None:
        """喂入 `now` 时刻的真实位置，返回此前"显示位置"与它的距离。"""
        error: float | None = None
        if self._prev is not None and self._curr is not None:
            shown = self.displayed(now)
            if shown is not None:
                error = distance(shown, actual)
                self.samples.append(error)
        self._prev, self._curr = self._curr, (now, actual)
        return error

    # ------------------------------------------------------------ 统计

    def reset(self) -> None:
        self._prev = None
        self._curr = None
        self.samples.clear()

    @property
    def count(self) -> int:
        return len(self.samples)

    def summary(self) -> dict:
        """直接可以写进机器人汇总 JSON 的一组数。"""
        if not self.samples:
            return {"err_px_mean": None, "err_px_p95": None, "err_px_max": None,
                    "err_px_samples": 0}
        ordered = sorted(self.samples)
        index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return {
            "err_px_mean": round(sum(ordered) / len(ordered), 3),
            "err_px_p95": round(ordered[index], 3),
            "err_px_max": round(ordered[-1], 3),
            "err_px_samples": len(ordered),
        }