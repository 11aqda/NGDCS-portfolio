"""用户态 TCP 网络损伤代理 —— E4 的"软件版 netem"。"""

from __future__ import annotations

import argparse
import asyncio
import random
import time

CHUNK = 64 * 1024

# Linux TCP 的最小重传超时。netem 丢一个段，应用层至少等这么久才看到数据。
DEFAULT_RTO_MS = 200.0


class ProxyStats:
    """代理侧计数 —— 用来证明"损伤真的注入了"，而不是只有配置没有效果。"""

    def __init__(self) -> None:
        self.connections = 0
        self.up_bytes = 0      # 客户端 → 服务端
        self.down_bytes = 0    # 服务端 → 客户端
        self.simulated_retransmits = 0
        self.delay_events = 0

    def as_dict(self) -> dict:
        return {
            "connections": self.connections,
            "up_bytes": self.up_bytes,
            "down_bytes": self.down_bytes,
            "simulated_retransmits": self.simulated_retransmits,
            "delay_events": self.delay_events,
        }


class LinkConfig:
    def __init__(self, delay_ms: float = 0.0, jitter_ms: float = 0.0,
                 loss_pct: float = 0.0, rto_ms: float = DEFAULT_RTO_MS) -> None:
        self.delay_ms = max(0.0, delay_ms)
        self.jitter_ms = max(0.0, jitter_ms)
        self.loss_pct = min(100.0, max(0.0, loss_pct))
        self.rto_ms = max(0.0, rto_ms)

    def next_delay_s(self, rng: random.Random) -> tuple[float, bool]:
        """这一块要延迟多久（秒），以及是否模拟了一次"丢包重传"。"""
        delay = self.delay_ms
        if self.jitter_ms > 0:
            delay += rng.uniform(0.0, self.jitter_ms)
        retransmit = False
        if self.loss_pct > 0 and rng.random() * 100.0 < self.loss_pct:
            delay += self.rto_ms
            retransmit = True
        return delay / 1000.0, retransmit


async def _pump(
    src: asyncio.StreamReader,
    dst: asyncio.StreamWriter,
    link: LinkConfig,
    rng: random.Random,
    stats: ProxyStats,
    direction: str,
) -> None:
    """把一个方向的字节流按延迟规则搬到对端。保序 + 绝对截止时刻。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
    loop = asyncio.get_running_loop()

    async def reader() -> None:
        while True:
            data = await src.read(CHUNK)
            if not data:
                break
            delay_s, retransmit = link.next_delay_s(rng)
            if retransmit:
                stats.simulated_retransmits += 1
            if delay_s > 0:
                stats.delay_events += 1
            await queue.put((loop.time() + delay_s, data))
        await queue.put(None)  # 结束标记

    async def writer() -> None:
        while True:
            item = await queue.get()
            if item is None:
                break
            release_at, data = item
            remaining = release_at - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                dst.write(data)
                await dst.drain()
            except (ConnectionError, RuntimeError):
                break
            if direction == "up":
                stats.up_bytes += len(data)
            else:
                stats.down_bytes += len(data)

    try:
        await asyncio.gather(reader(), writer())
    finally:
        try:
            dst.close()
        except (ConnectionError, RuntimeError):
            pass


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream: tuple[str, int],
    link: LinkConfig,
    stats: ProxyStats,
    rng: random.Random,
) -> None:
    stats.connections += 1
    try:
        server_reader, server_writer = await asyncio.open_connection(*upstream)
    except OSError:
        client_writer.close()
        return

    try:
        await asyncio.gather(
            _pump(client_reader, server_writer, link, rng, stats, "up"),
            _pump(server_reader, client_writer, link, rng, stats, "down"),
        )
    except (ConnectionError, RuntimeError):
        pass
    finally:
        for writer in (client_writer, server_writer):
            try:
                writer.close()
            except (ConnectionError, RuntimeError):
                pass


async def serve(args: argparse.Namespace) -> None:
    link = LinkConfig(args.delay_ms, args.jitter_ms, args.loss_pct, args.rto_ms)
    stats = ProxyStats()
    rng = random.Random(args.seed)

    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, (args.upstream_host, args.upstream), link, stats, rng),
        args.listen_host,
        args.listen,
    )
    print(
        f"[proxy] {args.listen_host}:{args.listen} -> "
        f"{args.upstream_host}:{args.upstream}  "
        f"delay={link.delay_ms:g}ms(+jitter {link.jitter_ms:g}) "
        f"loss={link.loss_pct:g}%(+RTO {link.rto_ms:g}ms)",
        flush=True,
    )

    async with server:
        try:
            await asyncio.sleep(args.duration if args.duration > 0 else 1e9)
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
    print(f"[proxy] stats: {stats.as_dict()}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用户态 TCP 网络损伤代理（E4 用）")
    parser.add_argument("--listen", type=int, default=9700, help="代理监听端口")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--upstream", type=int, default=9527, help="真实服务端端口")
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--delay-ms", type=float, default=0.0,
                        help="每个方向的固定延迟；RTT 约增加 2×该值")
    parser.add_argument("--jitter-ms", type=float, default=0.0, help="随机抖动上限")
    parser.add_argument("--loss-pct", type=float, default=0.0,
                        help="模拟丢包率：按概率给数据块加一个 RTO 量级的延迟"
                             "（TCP 会重传，应用层看到的是「来晚了」而不是「丢了」）")
    parser.add_argument("--rto-ms", type=float, default=DEFAULT_RTO_MS,
                        help="模拟重传的额外延迟（默认 200 ms = Linux TCP 最小 RTO）")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=0.0,
                        help="跑多少秒后退出（0 = 一直跑，用 Ctrl+C 停）")
    return parser.parse_args()


def main() -> None:
    asyncio.run(serve(parse_args()))


if __name__ == "__main__":
    main()