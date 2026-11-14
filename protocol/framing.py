"""12 字节帧头 + 载荷。"""

from __future__ import annotations

import asyncio
import socket
import struct
from dataclasses import dataclass

MAGIC = 0x4A59
VERSION = 1

# ! = 网络字节序（大端）；HBBII = 2 + 1 + 1 + 4 + 4 = 12 字节
HEADER = struct.Struct("!HBBII")

# 单个载荷上限：防止对端报一个 4 GB 的长度，把内存吃光
MAX_PAYLOAD = 64 * 1024


class ProtocolError(Exception):
    """帧格式错误、连接提前关闭、载荷超限。"""


@dataclass(frozen=True, slots=True)
class Frame:
    """一帧的语义字段（magic / version 已校验，不再返回）。"""

    msg_type: int
    seq: int
    payload: bytes


def parse_header(header: bytes) -> tuple[int, int, int]:
    """校验 12 B 帧头，返回 (msg_type, seq, length)。"""
    if len(header) != HEADER.size:
        raise ProtocolError(f"帧头长度错误：{len(header)} != {HEADER.size}")

    magic, version, msg_type, seq, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise ProtocolError(f"magic 不匹配：0x{magic:04X} != 0x{MAGIC:04X}")
    if version != VERSION:
        raise ProtocolError(f"version 不支持：{version} != {VERSION}")
    if length > MAX_PAYLOAD:
        raise ProtocolError(f"载荷超限：{length} B > {MAX_PAYLOAD} B")
    return msg_type, seq, length


def pack_frame(msg_type: int, seq: int, payload: bytes) -> bytes:
    """把 type / seq / payload 封装成“12 B 帧头 + 载荷”。"""
    if not 0 <= msg_type <= 0xFF:
        raise ProtocolError(f"type 超出 uint8：{msg_type}")
    if not 0 <= seq <= 0xFFFFFFFF:
        raise ProtocolError(f"seq 超出 uint32：{seq}")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"载荷超限：{len(payload)} B > {MAX_PAYLOAD} B")
    return HEADER.pack(MAGIC, VERSION, msg_type, seq, len(payload)) + payload


def send_frame(sock: socket.socket, msg_type: int, seq: int, payload: bytes) -> None:
    """封装并发送一帧。sendall 负责把数据全部写进内核缓冲区。"""
    sock.sendall(pack_frame(msg_type, seq, payload))


async def send_frame_async(
    writer: asyncio.StreamWriter, msg_type: int, seq: int, payload: bytes
) -> None:
    """asyncio 版发送：write + drain，确保数据交给内核。"""
    writer.write(pack_frame(msg_type, seq, payload))
    await writer.drain()


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """从 socket 精确读满 n 字节。"""
    if n == 0:
        return b""

    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)  # 阻塞；最多读 remaining 字节
        if not chunk:  # 对端关闭
            if remaining == n:
                # 一个字节都没读到，说明正好在帧边界上，属于正常断开
                return None
            raise ProtocolError(f"连接提前关闭：还差 {remaining} B 才读完")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket) -> Frame | None:
    """读一个完整帧；对端在帧边界关闭时返回 None。"""
    header = recv_exact(sock, HEADER.size)
    if header is None:
        return None

    msg_type, seq, length = parse_header(header)
    payload = recv_exact(sock, length)
    if payload is None:
        raise ProtocolError("帧头之后连接关闭，帧不完整")
    return Frame(msg_type=msg_type, seq=seq, payload=payload)


async def recv_frame_async(reader: asyncio.StreamReader) -> Frame | None:
    """asyncio 版接收：先读满 12 B 帧头，再按 length 读满载荷。"""
    try:
        header = await reader.readexactly(HEADER.size)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None  # 帧边界上的正常关闭
        raise ProtocolError(
            f"连接提前关闭：帧头只收到 {len(exc.partial)} B"
        ) from exc

    msg_type, seq, length = parse_header(header)

    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        missing = exc.expected - len(exc.partial)
        raise ProtocolError(f"连接提前关闭：载荷还差 {missing} B") from exc
    return Frame(msg_type=msg_type, seq=seq, payload=payload)


class FrameParser:
    """非阻塞接收用的帧解析器：喂任意分片，吐出完整 Frame。"""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> None:
        """把一次 recv 到的字节追加到缓冲区。"""
        self.buffer.extend(data)

    def next_frame(self) -> Frame | None:
        """取出一个完整帧；数据不够时返回 None，不阻塞。"""
        if len(self.buffer) < HEADER.size:
            return None

        header = bytes(self.buffer[: HEADER.size])
        msg_type, seq, length = parse_header(header)

        total = HEADER.size + length
        if len(self.buffer) < total:
            return None  # 半包：等下一次 feed

        payload = bytes(self.buffer[HEADER.size : total])
        del self.buffer[:total]
        return Frame(msg_type=msg_type, seq=seq, payload=payload)
