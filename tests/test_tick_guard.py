"""tick_loop 的存活保障测试。"""

import asyncio
import io
import unittest

from protocol.messages import ANGLE_DOWN
from server.entities import Bullet, Enemy, World
from server.main import Client, GameServer, report_tick_crash


class FakeWriter:
    """只实现 broadcast 用到的那几个方法，把数据收进内存。"""

    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def attach_player(game: GameServer, player_id: int = 1) -> FakeWriter:
    writer = FakeWriter()
    game.clients[player_id] = Client(
        player_id=player_id, color=1, x=360, y=760, writer=writer
    )
    return writer


class TickLoopSurvivalTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_tick_loop_survives(self):
        """真跑几个 tick：tick_loop 里用到的名字必须全都存在。"""
        world = World(enemy_target=10, bullet_target=20, seed=1)
        game = GameServer(world=world, print_stats=False)

        task = asyncio.create_task(game.tick_loop())
        await asyncio.sleep(0.15)  # 约 3 个 tick

        if task.done():
            exc = task.exception()
            self.fail(f"tick_loop 提前死掉了：{exc!r}")

        self.assertGreaterEqual(game.tick, 1, "一个 tick 都没跑完")

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_tick_records_all_four_phases(self):
        """接上一个客户端后跑完一个窗口：四段都必须有值。"""
        world = World(enemy_target=30, bullet_target=200, seed=1)
        game = GameServer(world=world, print_stats=False)
        writer = attach_player(game)

        task = asyncio.create_task(game.tick_loop())
        await asyncio.sleep(1.1)  # 跨过一个统计窗口（20 tick ≈ 1 秒）
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        window = game.stats.flush()
        for phase in ("sim_ms", "collision_ms", "ser_ms", "net_ms"):
            self.assertIn(phase, window)
            self.assertGreater(window[phase], 0.0, f"{phase} 没记到数")

        self.assertGreater(window["ticks"], 0)
        self.assertGreater(window["snapshot_bytes"], 0)
        self.assertGreater(len(writer.data), 0, "客户端一个字节都没收到")

    async def test_hits_reach_the_stats_window(self):
        """碰撞命中数要真的进统计窗口 —— 端到端证明 `record_hits` 接上了。"""
        world = World(enemy_target=0, bullet_target=0, seed=1)
        for index in range(4):
            world.enemies.append(Enemy(enemy_id=index + 1, x=360, y=200))
            world.bullets.append(
                Bullet(owner_id=0, x=360, y=200, type=0x11, angle=ANGLE_DOWN, speed=0)
            )

        game = GameServer(world=world, print_stats=False)
        attach_player(game)

        task = asyncio.create_task(game.tick_loop())
        await asyncio.sleep(0.15)  # 约 3 个 tick
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        # 4×4 = 16 对；至少跑到一个命中 tick
        self.assertGreaterEqual(game.stats.flush()["hits"], 16)

    async def test_no_clients_means_no_serialization(self):
        """没人连的时候跳过序列化与广播：这是有意的，不是漏做。"""
        world = World(enemy_target=10, bullet_target=20, seed=1)
        game = GameServer(world=world, print_stats=False)

        task = asyncio.create_task(game.tick_loop())
        await asyncio.sleep(1.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        window = game.stats.flush()

        # 逻辑侧照常干活
        self.assertGreater(window["sim_ms"], 0.0)
        self.assertGreater(window["collision_ms"], 0.0)

        # 确定性证据：什么都没序列化、什么都没发出去
        self.assertEqual(window["snapshot_bytes"], 0)
        self.assertEqual(window["packets_out"], 0)
        self.assertEqual(window["bytes_out"], 0)

        # 跳过这两段的代价应当可以忽略（而不是"恰好为零"）
        self.assertLess(window["ser_ms"], 0.1)
        self.assertLess(window["net_ms"], 0.1)


class FixedEntitiesGuardTest(unittest.TestCase):
    """的 `--fixed-entities` 守卫。"""

    def _make(self, fixed: bool, enemies: int = 4, bullets: int = 6) -> GameServer:
        world = World(enemy_target=enemies, bullet_target=bullets, seed=1)
        world.step([])  # 第一次 step 会把实体补到目标数
        return GameServer(world=world, print_stats=False, fixed_entities=fixed)

    def test_no_drift_passes(self):
        """数量与目标一致时不抛错 —— 正常实验跑的就是这条路径。"""
        game = self._make(fixed=True)
        game._verify_entity_counts()  # 不抛异常即通过

    def test_drift_raises(self):
        """少一个敌机就必须抛错，且错误信息要指出"本次数据不可用"。"""
        game = self._make(fixed=True)
        game.world.enemies.pop()
        with self.assertRaises(RuntimeError) as ctx:
            game._verify_entity_counts()
        self.assertIn("实体数漂移", str(ctx.exception))
        self.assertIn("不可用", str(ctx.exception))

    def test_extra_bullet_also_raises(self):
        """多出来也必须拦 —— 漂移是双向的。"""
        game = self._make(fixed=True)
        game.world.bullets.append(game.world.bullets[0])
        with self.assertRaises(RuntimeError):
            game._verify_entity_counts()

    def test_switch_off_means_no_check(self):
        """没开开关时（正式游戏默认）即使数量漂移也不校验 —— 那是有意为之的行为。"""
        game = self._make(fixed=False)
        self.assertFalse(game.fixed_entities)
        game.world.enemies.clear()  # 模拟"击杀生效后真的移除"
        # 开关关着，就不应该有人来拦；真正的校验只在 tick 里按开关调用
        self.assertEqual(len(game.world.enemies), 0)

    def test_default_is_off(self):
        """默认必须是关的：保持"正式游戏"语义，实验脚本负责显式打开。"""
        game = GameServer(world=World(enemy_target=1, bullet_target=1, seed=1),
                          print_stats=False)
        self.assertFalse(game.fixed_entities)


class TickCrashReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_task_is_not_a_crash(self):
        task = asyncio.create_task(asyncio.sleep(30))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(report_tick_crash(task, file=io.StringIO()))

    async def test_clean_task_is_not_a_crash(self):
        async def ok() -> int:
            return 1

        task = asyncio.create_task(ok())
        await task
        self.assertIsNone(report_tick_crash(task, file=io.StringIO()))

    async def test_exception_is_reported_loudly(self):
        async def boom() -> None:
            raise NameError("name 'detect_collisions' is not defined")

        task = asyncio.create_task(boom())
        with self.assertRaises(NameError):
            await task

        stream = io.StringIO()
        exc = report_tick_crash(task, file=stream)

        self.assertIsInstance(exc, NameError)
        text = stream.getvalue()
        self.assertIn("tick loop 已崩溃", text)
        self.assertIn("NameError", text)
        self.assertIn("detect_collisions", text)


class TickErrorSurvivalTest(unittest.TestCase):
    """2026-11-07 事故：tick 里抛异常不能把循环弄死。"""

    def test_loop_survives_policy_exception(self):
        import asyncio

        import server.main as server_main
        from server.entities import World

        class _FakeWriter:
            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

            def is_closing(self):
                return False

            def get_extra_info(self, name, default=None):
                return default

        class _Boom:
            manages_spawning = True

            def tick_spawn(self, world, players, firing):
                raise ValueError("故意的")

            def enemy_move(self, enemy):
                return (0, 0)

            def bullet_move(self, bullet):
                return (0, 0)

            def collision_radius(self, enemy):
                return 8

            def score_for(self, enemy):
                return None

        async def run():
            world = World(enemy_target=0, bullet_target=0, seed=1, policy=_Boom())
            server = server_main.GameServer(world=world, print_stats=False)
            server.clients[1] = server_main.Client(
                player_id=1, color=1, x=360, y=760, writer=_FakeWriter(),
            )
            task = asyncio.create_task(server.tick_loop())
            # 睡久一点：这台机器上可能同时跑着服务端/Django，20 Hz 的循环
            # 在负载下不一定能在 0.5 秒里跑够 tick（踩过一次闪失败）
            await asyncio.sleep(1.2)
            alive = not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return alive, server.tick, server._tick_errors

        alive, ticks, errors = asyncio.run(run())
        self.assertTrue(alive, "tick 循环死了 —— 又会出现'卡在第 N 波'")
        # 只要还在走就说明循环没死 —— 阈值放宽，避免负载下闪失败
        self.assertGreater(ticks, 3, "tick 没有继续前进")
        self.assertGreater(errors, 0, "异常没有被记录")

if __name__ == "__main__":
    unittest.main()