"""快照编码器：同一份世界状态，三种编码方式（E1 实验用）。"""

from __future__ import annotations

import json
import struct
from functools import lru_cache
from typing import Protocol as TypingProtocol

from protocol.messages import ENEMY_BOSS
from protocol.snapshot import (
    BULLET,
    ENEMY,
    ITEM,
    PLAYER,
    SNAPSHOT_HEADER,
    BulletSnapshot,
    EnemySnapshot,
    ItemSnapshot,
    PlayerSnapshot,
    Snapshot,
    _check_count,
    pack_snapshot,
)

# 各条目的 `struct` 格式（去掉 `!`，因为整帧只留一个 `!` 在最前面）。
# 从 Struct 对象里读出来，而不是再抄一遍 —— 这样协议改了这里自动跟着变。
_HEADER_FMT = SNAPSHOT_HEADER.format.lstrip("!")
_PLAYER_FMT = PLAYER.format.lstrip("!")
_ENEMY_FMT = ENEMY.format.lstrip("!")
_BULLET_FMT = BULLET.format.lstrip("!")
_ITEM_FMT = ITEM.format.lstrip("!")

ENCODER_NAMES = ("binary", "binary_fast", "json")


class SnapshotEncoder(TypingProtocol):
    """编码器接口：同一份世界状态 → 一帧 S_SNAPSHOT 的载荷。"""

    name: str

    def encode(
        self,
        tick: int,
        players: list[PlayerSnapshot],
        enemies: list[EnemySnapshot] | None = None,
        bullets: list[BulletSnapshot] | None = None,
        items: list[ItemSnapshot] | None = None,
        level: int = 1,
        wave: int = 1,
        wave_state: int = 0,
    ) -> bytes: ...


class BinaryEncoder:
    """协议的标准编码：逐条目 `struct.pack` 再拼接。"""

    name = "binary"

    def encode(
        self, tick, players, enemies=None, bullets=None, items=None,
        level=1, wave=1, wave_state=0,
    ):
        return pack_snapshot(
            tick, players, enemies, bullets, items, level, wave, wave_state
        )


@lru_cache(maxsize=64)
def _fast_struct(player_count: int, enemy_count: int, bullet_count: int, item_count: int):
    """按实体数组合缓存整帧的 `Struct`。"""
    fmt = (
        "!"
        + _HEADER_FMT
        + _PLAYER_FMT * player_count
        + _ENEMY_FMT * enemy_count
        + _BULLET_FMT * bullet_count
        + _ITEM_FMT * item_count
    )
    return struct.Struct(fmt)


class FastBinaryEncoder:
    """同一个协议、逐字节相同的输出，但整帧只调一次 `struct.pack`。"""

    name = "binary_fast"

    def encode(
        self, tick, players, enemies=None, bullets=None, items=None,
        level=1, wave=1, wave_state=0,
    ):
        enemies = enemies or []
        bullets = bullets or []
        items = items or []

        _check_count(len(players), 0xFF, "玩家")
        _check_count(len(enemies), 0xFF, "敌机")
        _check_count(len(bullets), 0xFFFF, "子弹")
        _check_count(len(items), 0xFF, "道具")

        boss_present = 1 if any(e.type == ENEMY_BOSS for e in enemies) else 0

        args: list = [
            tick,
            level,
            wave,
            wave_state,
            len(players),
            len(enemies),
            len(bullets),
            len(items),
            boss_present,
        ]
        for p in players:
            args += (p.player_id, p.x, p.y, p.power, p.color, p.flags, p.score)
        for e in enemies:
            args += (e.enemy_id, e.x, e.y, e.type, e.hp)
        for b in bullets:
            args += (b.owner_id, b.x, b.y, b.type, b.angle)
        for i in items:
            args += (i.item_id, i.x, i.y, i.type)

        layout = _fast_struct(len(players), len(enemies), len(bullets), len(items))
        return layout.pack(*args)


class JsonEncoder:
    """短键名 JSON —— 对照组。"""

    name = "json"

    def encode(
        self, tick, players, enemies=None, bullets=None, items=None,
        level=1, wave=1, wave_state=0,
    ):
        enemies = enemies or []
        bullets = bullets or []
        items = items or []

        boss_present = 1 if any(e.type == ENEMY_BOSS for e in enemies) else 0
        payload = {
            "t": tick,
            "l": level,
            "w": wave,
            "s": wave_state,
            "bp": boss_present,
            "p": [
                {
                    "i": p.player_id, "x": p.x, "y": p.y, "pw": p.power,
                    "c": p.color, "f": p.flags, "sc": p.score,
                }
                for p in players
            ],
            "e": [
                {"i": e.enemy_id, "x": e.x, "y": e.y, "ty": e.type, "h": e.hp}
                for e in enemies
            ],
            "b": [
                {"o": b.owner_id, "x": b.x, "y": b.y, "ty": b.type, "a": b.angle}
                for b in bullets
            ],
            "i": [
                {"i": i.item_id, "x": i.x, "y": i.y, "ty": i.type} for i in items
            ],
        }
        # separators 去掉空格、ensure_ascii 关掉转义 —— 都是"认真写 JSON"会做的
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


_ENCODERS: dict[str, type] = {
    "binary": BinaryEncoder,
    "binary_fast": FastBinaryEncoder,
    "json": JsonEncoder,
}


def get_encoder(name: str) -> SnapshotEncoder:
    """按名字取编码器。名字不认识就报错，不要静默退回 binary ——
    否则 `--encoder jsno` 这种手误会让实验白跑一轮且数据标错编码。"""
    try:
        return _ENCODERS[name]()
    except KeyError:
        raise ValueError(
            f"未知编码器 {name!r}；可选：{', '.join(ENCODER_NAMES)}"
        ) from None


def unpack_snapshot_json(payload: bytes) -> Snapshot:
    """JSON 快照的解码 —— 与 `protocol.snapshot.unpack_snapshot` 对称。"""
    data = json.loads(payload.decode("utf-8"))
    return Snapshot(
        tick=data["t"],
        level=data["l"],
        wave=data["w"],
        wave_state=data["s"],
        players=tuple(
            PlayerSnapshot(
                player_id=p["i"], x=p["x"], y=p["y"], power=p["pw"],
                color=p["c"], flags=p["f"], score=p["sc"],
            )
            for p in data["p"]
        ),
        enemies=tuple(
            EnemySnapshot(enemy_id=e["i"], x=e["x"], y=e["y"], type=e["ty"], hp=e["h"])
            for e in data["e"]
        ),
        bullets=tuple(
            BulletSnapshot(
                owner_id=b["o"], x=b["x"], y=b["y"], type=b["ty"], angle=b["a"]
            )
            for b in data["b"]
        ),
        items=tuple(
            ItemSnapshot(item_id=i["i"], x=i["x"], y=i["y"], type=i["ty"])
            for i in data["i"]
        ),
        boss_present=bool(data["bp"]),
    )


# 想再压缩 JSON 的话（本实验不实现，只在实验记录里讨论量级）：
# 把条目从对象改成数组 `[1,100,200,0,3]`，省掉重复的键名，
# 体积大约能降到对象形式的一半 —— 但代价是自描述性，前后端都得靠位置约定去读。