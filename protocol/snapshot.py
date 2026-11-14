"""S_SNAPSHOT 编解码：快照头 + 玩家 / 敌机 / 子弹 / 道具条目。"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from protocol.framing import ProtocolError
from protocol.messages import ENEMY_BOSS

# !IBBBBBHBB = 4 + 5 + 2 + 2 = 13 B
SNAPSHOT_HEADER = struct.Struct("!IBBBBBHBB")

# !HHHBBBH = 2 + 2 + 2 + 1 + 1 + 1 + 2 = 11 B
PLAYER = struct.Struct("!HHHBBBH")

# !HHHBH = 2 + 2 + 2 + 1 + 2 = 9 B
ENEMY = struct.Struct("!HHHBH")

# !HHHBB = 2 + 2 + 2 + 1 + 1 = 8 B
BULLET = struct.Struct("!HHHBB")

# !HHHB = 2 + 2 + 2 + 1 = 7 B
ITEM = struct.Struct("!HHHB")

assert SNAPSHOT_HEADER.size == 13
assert PLAYER.size == 11
assert ENEMY.size == 9
assert BULLET.size == 8
assert ITEM.size == 7


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    player_id: int
    x: int
    y: int
    power: int = 1
    color: int = 0
    flags: int = 1  # 1 = 在线
    score: int = 0


@dataclass(frozen=True, slots=True)
class EnemySnapshot:
    enemy_id: int
    x: int
    y: int
    type: int = 0x01
    hp: int = 1


@dataclass(frozen=True, slots=True)
class BulletSnapshot:
    owner_id: int  # 敌方子弹为 0
    x: int
    y: int
    type: int = 0x01
    angle: int = 0  # 0 指向正上方，顺时针（02 ）


@dataclass(frozen=True, slots=True)
class ItemSnapshot:
    item_id: int
    x: int
    y: int
    type: int = 0x01


@dataclass(frozen=True, slots=True)
class Snapshot:
    tick: int
    level: int
    wave: int
    wave_state: int
    players: tuple[PlayerSnapshot, ...]
    enemies: tuple[EnemySnapshot, ...] = ()
    bullets: tuple[BulletSnapshot, ...] = ()
    items: tuple[ItemSnapshot, ...] = ()
    boss_present: bool = False

    @property
    def entity_count(self) -> int:
        """快照里的实体总数（E9 的横轴就是它）。"""
        return (
            len(self.players) + len(self.enemies) + len(self.bullets) + len(self.items)
        )


def _check_count(count: int, limit: int, name: str) -> None:
    if count > limit:
        raise ProtocolError(f"{name} 数量超出上限：{count} > {limit}")


def pack_snapshot(
    tick: int,
    players: list[PlayerSnapshot],
    enemies: list[EnemySnapshot] | None = None,
    bullets: list[BulletSnapshot] | None = None,
    items: list[ItemSnapshot] | None = None,
    level: int = 1,
    wave: int = 1,
    wave_state: int = 0,
) -> bytes:
    """把快照头 + 各类条目打包成 S_SNAPSHOT 的载荷。"""
    enemies = enemies or []
    bullets = bullets or []
    items = items or []

    _check_count(len(players), 0xFF, "玩家")
    _check_count(len(enemies), 0xFF, "敌机")
    _check_count(len(bullets), 0xFFFF, "子弹")
    _check_count(len(items), 0xFF, "道具")

    # 推导而不是传参：boss_present 和敌机的 0xFF 类型永远一致
    boss_present = 1 if any(e.type == ENEMY_BOSS for e in enemies) else 0

    header = SNAPSHOT_HEADER.pack(
        tick,
        level,
        wave,
        wave_state,
        len(players),
        len(enemies),
        len(bullets),
        len(items),
        boss_present,
    )
    body = b"".join(
        PLAYER.pack(p.player_id, p.x, p.y, p.power, p.color, p.flags, p.score)
        for p in players
    )
    body += b"".join(
        ENEMY.pack(e.enemy_id, e.x, e.y, e.type, e.hp) for e in enemies
    )
    body += b"".join(
        BULLET.pack(b.owner_id, b.x, b.y, b.type, b.angle) for b in bullets
    )
    body += b"".join(ITEM.pack(i.item_id, i.x, i.y, i.type) for i in items)
    return header + body


def _unpack_many(layout, factory, payload: bytes, offset: int, count: int):
    entries = []
    for _ in range(count):
        entries.append(factory(*layout.unpack_from(payload, offset)))
        offset += layout.size
    return tuple(entries), offset


def unpack_snapshot(payload: bytes) -> Snapshot:
    """解析 S_SNAPSHOT 载荷。"""
    if len(payload) < SNAPSHOT_HEADER.size:
        raise ProtocolError(f"快照太短：{len(payload)} B")

    (
        tick,
        level,
        wave,
        wave_state,
        player_count,
        enemy_count,
        bullet_count,
        item_count,
        boss_present,
    ) = SNAPSHOT_HEADER.unpack_from(payload, 0)

    expected = (
        SNAPSHOT_HEADER.size
        + player_count * PLAYER.size
        + enemy_count * ENEMY.size
        + bullet_count * BULLET.size
        + item_count * ITEM.size
    )
    if len(payload) != expected:
        raise ProtocolError(f"快照长度不匹配：{len(payload)} != {expected}")

    offset = SNAPSHOT_HEADER.size
    players, offset = _unpack_many(PLAYER, PlayerSnapshot, payload, offset, player_count)
    enemies, offset = _unpack_many(ENEMY, EnemySnapshot, payload, offset, enemy_count)
    bullets, offset = _unpack_many(BULLET, BulletSnapshot, payload, offset, bullet_count)
    items, offset = _unpack_many(ITEM, ItemSnapshot, payload, offset, item_count)

    return Snapshot(
        tick=tick,
        level=level,
        wave=wave,
        wave_state=wave_state,
        players=players,
        enemies=enemies,
        bullets=bullets,
        items=items,
        boss_present=bool(boss_present),
    )