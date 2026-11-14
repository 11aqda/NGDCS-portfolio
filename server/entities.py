"""占位实体：没有 AI 的敌机与子弹。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from protocol.messages import (
    ANGLE_DOWN,
    ANGLE_UP,
    BULLET_ENEMY_SPREAD,
    BULLET_PLAYER_L1,
    ENEMY_DIVE,
)
from protocol.snapshot import BulletSnapshot, EnemySnapshot
from protocol.world import WORLD_HEIGHT, WORLD_WIDTH

# 3 px/tick × 20 Hz = 60 px/s → 16 秒穿过 960 px 屏幕
ENEMY_SPEED_PER_TICK = 3

# 12 px/tick × 20 Hz = 240 px/s → 4 秒穿屏
BULLET_SPEED_PER_TICK = 12

# 生成位置左右留边，别贴着屏幕边缘刷出来
SPAWN_MARGIN = 40

# 玩家子弹从屏幕下方往上打
PLAYER_BULLET_SPAWN_Y = WORLD_HEIGHT - 60

# 碰撞判定半径（像素）：子弹约 4 px、敌机约 14 px，取半个身位做粗略 AABB。
# 占位阶段不追求判定精度，只追求算法复杂度与真实版本一致（01 的朴素 O(子弹×敌机)）
COLLISION_RADIUS = 8

# 八方向整数表（0° = 正上，顺时针，02 ）
_OCTANTS = (
    (0, -1),  # 0    正上
    (1, -1),  # 32   右上
    (1, 0),  # 64   正右
    (1, 1),  # 96   右下
    (0, 1),  # 128  正下
    (-1, 1),  # 160  左下
    (-1, 0),  # 192  正左
    (-1, -1),  # 224  左上
)


def angle_to_delta(angle: int, speed: int) -> tuple[int, int]:
    """把 uint8 角度换成整数位移 (dx, dy)。"""
    dx, dy = _OCTANTS[(angle >> 5) & 0x07]
    return dx * speed, dy * speed


@dataclass(slots=True)
class Enemy:
    enemy_id: int
    x: int
    y: int
    type: int = ENEMY_DIVE
    hp: int = 1
    # ---------------- 以下全是运行时字段， GamePolicy 才用 ----------------
    #
    # 它们不参与快照编码：`snapshot_enemies` 只取前 5 个字段
    #    （enemy_id / x / y / type / hp），所以快照字节不变。
    # `PlaceholderPolicy` 完全不读不写这些字段，默认路径因此零影响。
    #
    # 为什么挂在实体上而不是放进 GamePolicy 的 dict：敌机被移除时要同步清理 dict，
    # 漏了就是内存泄漏；挂在实体上随实体一起消失，省心。
    phase: int = 0      # 0x02 的阶段：0=下落到 home_y，1=向右，2=向左
    fire_cd: int = 0    # 距离下次开火还有几 tick（0 = 可开火）
    home_y: int = 0     # 0x02 固守的高度（出生时随机定，之后不再变）
    target_x: int = 0   # 0x03 当前的横向目标（每 0.5 s 重算一次，不每 tick 算）


@dataclass(slots=True)
class Bullet:
    owner_id: int  # 0 = 敌方子弹
    x: int
    y: int
    type: int
    angle: int
    speed: int = BULLET_SPEED_PER_TICK


class PlaceholderPolicy:
    """占位阶段的实体行为：出生点固定、速度恒定、弹道直线。"""

    # 出生与补弹仍由 World 自己负责（补到恒定数量 = E9 的自变量）。
    # GamePolicy 会把它设成 True，改成"按波次生成 + 敌机按冷却射击 + 玩家按射速开火"。
    # 这是 World.step 里唯一的分派点，默认路径只多读一个类属性，行为一字未改。
    manages_spawning = False

    def enemy_spawn(self, rng: random.Random) -> tuple[int, int]:
        """敌机从屏幕顶边进场，横向随机。恰好一次 rng 调用。"""
        return rng.randrange(SPAWN_MARGIN, WORLD_WIDTH - SPAWN_MARGIN), 0

    def enemy_move(self, enemy: Enemy) -> tuple[int, int]:
        """占位敌机统一向下匀速飞（协议里敌机没有 angle 字段，方向由 type 隐含）。"""
        return 0, ENEMY_SPEED_PER_TICK

    def bullet_spawn(
        self,
        rng: random.Random,
        players: list[tuple[int, int]],
        want_player: bool,
    ) -> tuple[int, int, int, int, int, int]:
        """一半玩家子弹（向上）、一半敌方子弹（向下），轮流出。"""
        if want_player and players:
            # 只取前两个元素：服务端现在会传 (id, x, y)，占位路径只用 x
            player_id, x = players[rng.randrange(len(players))][:2]
            return player_id, x, PLAYER_BULLET_SPAWN_Y, BULLET_PLAYER_L1, ANGLE_UP, BULLET_SPEED_PER_TICK

        return (
            0,  # 敌方子弹的 owner_id 固定为 0（02 ）
            rng.randrange(SPAWN_MARGIN, WORLD_WIDTH - SPAWN_MARGIN),
            0,
            BULLET_ENEMY_SPREAD,
            ANGLE_DOWN,
            BULLET_SPEED_PER_TICK,
        )

    def bullet_move(self, bullet: Bullet) -> tuple[int, int]:
        return angle_to_delta(bullet.angle, bullet.speed)

    def collision_radius(self, enemy: Enemy) -> int:
        """判定半径。占位路径就是模块常量（8），与 E9 基线一致。"""
        return COLLISION_RADIUS

    def score_for(self, enemy: Enemy) -> int | None:
        """击杀这个敌机该给多少分。返回 None 表示"用世界的默认值"。"""
        return None


class World:
    """一张只有「匀速直线飞」的场地。"""

    def __init__(
        self,
        enemy_target: int = 0,
        bullet_target: int = 0,
        seed: int = 1,
        enable_kills: bool = False,
        fixed_entities: bool = False,
        policy: "PlaceholderPolicy | None" = None,
    ) -> None:
        # 上限来自协议本身：enemy_count 是 uint8、bullet_count 是 uint16
        if not 0 <= enemy_target <= 0xFF:
            raise ValueError(f"敌机数超出快照头的 uint8：{enemy_target} > 255")
        if not 0 <= bullet_target <= 0xFFFF:
            raise ValueError(f"子弹数超出快照头的 uint16：{bullet_target} > 65535")

        self.enemy_target = enemy_target
        self.bullet_target = bullet_target
        self.rng = random.Random(seed)

        # 击杀是否真的生效（扣血 / 死亡 / 计分）。
        # 默认关：E9/E1/E2 的全部基线都是在「只判定不生效」下测的，
        # 打开会改变 collision 阶段的实际工作量，必须显式开启才不污染复现。
        self.enable_kills = enable_kills
        # 击杀后是否立刻补满（实验期需要数量恒定，）
        self.fixed_entities = fixed_entities
        # 关卡/波次：默认值刻意与快照编码器的默认值（level=1, wave=1）一致，
        # 所以默认路径传与不传，字节完全相同（黄金基线会证明）。
        # GamePolicy 会按实际波次更新 it。
        self.level = 1
        self.wave = 1
        # 0 = 正常作战；1 = 波间"下一波"提示中（客户端据此播大字）。
        # 默认 0 与编码器默认值一致 → 实验路径字节不变。
        self.wave_state = 0
        self.scores: dict[int, int] = {}
        self.kills = 0
        self.score_per_kill = 10
        # 非固定模式下每 tick 最多补几个 —— 否则击杀刚清掉的敌机下一 tick 就满了，
        # 玩家看不到"打掉了"的反馈
        self.respawn_per_tick = 2

        # 实体行为策略（扩展点）。默认 = 占位行为，与重构前逐字节相同。
        self.policy = policy if policy is not None else PlaceholderPolicy()

        self.enemies: list[Enemy] = []
        self.bullets: list[Bullet] = []
        self.next_enemy_id = 1
        self._player_bullet_turn = True

    # ------------------------------------------------------------ 每个 tick

    def step(
        self,
        players: list[tuple[int, int]],
        firing: set[int] | None = None,
    ) -> None:
        """players 是 [(player_id, x)]，玩家子弹从玩家的横向位置打出去。"""
        self._move()
        if self.policy.manages_spawning:
            self.policy.tick_spawn(self, players, firing or set())
        else:
            self._respawn(players)

    def _move(self) -> None:
        # 敌机条目里没有 angle 字段（02 ）—— 竖版飞机大战的敌机都是向下
        # 方向由 type 隐含，所以占位敌机统一向下飞。
        # 位移交给策略算（敌机类型轨迹不同），World 只负责累加与剔除。
        for enemy in self.enemies:
            dx, dy = self.policy.enemy_move(enemy)
            enemy.x += dx
            enemy.y += dy

        # 出屏即剔除：不写进快照 = 客户端删掉它。
        # 顺带保证坐标永远落在 uint16 值域内，不会像 记录的那样 struct.error。
        self.enemies = [e for e in self.enemies if 0 <= e.y <= WORLD_HEIGHT]

        alive: list[Bullet] = []
        for bullet in self.bullets:
            dx, dy = self.policy.bullet_move(bullet)
            bullet.x += dx
            bullet.y += dy
            if 0 <= bullet.x <= WORLD_WIDTH and 0 <= bullet.y <= WORLD_HEIGHT:
                alive.append(bullet)
        self.bullets = alive

    def _respawn(self, players: list[tuple[int, int]]) -> None:
        # 击杀生效且不要求实体恒定时，按速率补生：一次杀光会让场上空掉
        # 这正是"打掉了"的反馈；而实验期（fixed_entities）必须立刻补满，
        # 因为自变量是同屏实体数。
        capped = self.enable_kills and not self.fixed_entities
        spawned = 0
        while len(self.enemies) < self.enemy_target:
            if capped and spawned >= self.respawn_per_tick:
                break
            self.enemies.append(self._spawn_enemy())
            spawned += 1
        # 子弹不设上限：它是"发射物"，不是关卡难度，补慢了反而让弹幕变稀
        while len(self.bullets) < self.bullet_target:
            self.bullets.append(self._spawn_bullet(players))

    def new_enemy_id(self) -> int:
        """分配一个敌机 ID（uint16，用完绕回 1）。"""
        enemy_id = self.next_enemy_id
        self.next_enemy_id += 1
        if self.next_enemy_id > 0xFFFF:  # enemy_id 是 uint16，绕回来
            self.next_enemy_id = 1
        return enemy_id

    def _spawn_enemy(self) -> Enemy:
        x, y = self.policy.enemy_spawn(self.rng)
        return Enemy(enemy_id=self.new_enemy_id(), x=x, y=y)

    def _spawn_bullet(self, players: list[tuple[int, int]]) -> Bullet:
        """一半玩家子弹（向上）、一半敌方子弹（向下），轮流出。"""
        want_player = self._player_bullet_turn
        self._player_bullet_turn = not want_player

        owner_id, x, y, bullet_type, angle, speed = self.policy.bullet_spawn(
            self.rng, players, want_player
        )
        return Bullet(
            owner_id=owner_id,
            x=x,
            y=y,
            type=bullet_type,
            angle=angle,
            speed=speed,
        )

    # ------------------------------------------------------------ 导出快照

    def snapshot_enemies(self) -> list[EnemySnapshot]:
        return [
            EnemySnapshot(e.enemy_id, e.x, e.y, e.type, e.hp) for e in self.enemies
        ]

    def snapshot_bullets(self) -> list[BulletSnapshot]:
        return [
            BulletSnapshot(b.owner_id, b.x, b.y, b.type, b.angle) for b in self.bullets
        ]

    def counts(self) -> tuple[int, int]:
        return len(self.enemies), len(self.bullets)

    # ------------------------------------------------------------ 击杀结算

    def resolve_kills(self, players: list[tuple[int, int]] | None = None) -> tuple[int, int]:
        """让碰撞真的生效：扣血 → 死亡 → 计分 → 消耗子弹。返回 (命中数, 击杀数)。"""
        # 半径交给策略：占位路径返回 8（与 E9 的计数路径一致），
        # 游戏模式返回更大的值，让"视觉上打中了"真的算打中。
        # `detect_collisions`（E9 计数路径）必须继续用模块常量 8
        # 那是历史数据的一部分，一改 E9 的 hits 就没法比了。
        hits = 0
        killed = 0
        dead_enemies: set[int] = set()
        used_bullets: set[int] = set()

        for bi, bullet in enumerate(self.bullets):
            if bullet.owner_id == 0:
                continue  # 敌方子弹不参与"打敌机"
            bx = bullet.x
            by = bullet.y
            for ei, enemy in enumerate(self.enemies):
                if ei in dead_enemies:
                    continue
                # 每只敌机各自取半径（0x02 / 0x03 比 0x01 大一圈）。
                # 只在结算路径这么做；`detect_collisions`（E9 计数）
                # 继续用模块常量 8，否则 E9 的 hits 历史数据就没法比了。
                radius = self.policy.collision_radius(enemy)
                dx = bx - enemy.x
                if -radius <= dx <= radius:
                    dy = by - enemy.y
                    if -radius <= dy <= radius:
                        hits += 1
                        used_bullets.add(bi)
                        enemy.hp -= 1
                        if enemy.hp <= 0:
                            dead_enemies.add(ei)
                            killed += 1
                            self._award(
                                bullet.owner_id,
                                self.policy.score_for(enemy),
                            )
                        break  # 一颗子弹只命中一个目标

        if dead_enemies:
            self.enemies = [e for i, e in enumerate(self.enemies) if i not in dead_enemies]
        if used_bullets:
            self.bullets = [b for i, b in enumerate(self.bullets) if i not in used_bullets]
        self.kills += killed

        # 实验期必须当场补回目标数，不能等下一个 tick 的 `_respawn`。
        # 2026-10-31 实测踩到：`--fixed-entities` 的守卫在 `step()` 之后检查，
        # 那时已经补满了，所以守卫通过；而击杀发生在它之后
        # 于是快照里的敌机数变成 27~30 —— 守卫形同虚设，数据已经不符契约。
        # 快照字节数是 E9 那套「逐字节等于公式」校验的基础，一旦浮动就全废。
        if self.fixed_entities and killed:
            self._respawn(players or [])
        return hits, killed

    def _award(self, player_id: int, points: int | None = None) -> None:
        """加分。必须夹到 uint16 上限：`score` 是协议里的 uint16 字段
        越界会让 `struct.pack` 抛错 —— 那会像 E2 的 257 玩家一样直接搞死 tick 循环。"""
        gained = self.score_per_kill if points is None else points
        self.scores[player_id] = min(0xFFFF, self.scores.get(player_id, 0) + gained)


def detect_collisions(bullets: list[Bullet], enemies: list[Enemy]) -> int:
    """朴素 O(子弹 × 敌机) 碰撞判定，只数命中对数，不产生任何效果，返回命中数。"""
    radius = COLLISION_RADIUS
    hits = 0
    for bullet in bullets:
        bx = bullet.x
        by = bullet.y
        for enemy in enemies:
            dx = bx - enemy.x
            if -radius <= dx <= radius:
                dy = by - enemy.y
                if -radius <= dy <= radius:
                    hits += 1
    return hits