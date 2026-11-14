"""客户端非阻塞网络层。"""

from __future__ import annotations

import socket

from protocol.framing import HEADER, Frame, FrameParser, ProtocolError, pack_frame

# 发送队列上限：服务端不读的时候不能让内存无限涨。
# 正常情况下每帧都能排空（输入 13 B/帧），排到 64 KiB 说明连接已经不正常了。
MAX_OUTBOX = 64 * 1024


class NetworkClient:
    def __init__(self, host: str, port: int) -> None:
        # 建立连接这一次可以阻塞（最多 5 s）；连上后立刻切成非阻塞
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.setblocking(False)

        self.parser = FrameParser()
        self.outbox = bytearray()
        self.seq = 0
        self.connected = True
        self.last_error = ""
        self.dropped_frames = 0

    def poll(self) -> list[Frame]:
        """每帧调一次：尽量把发件发出去，再把能收的收进来。"""
        if not self.connected and not self.parser.buffer:
            return []

        self.flush()
        self._read_available()
        return self._parse_available()

    def send(self, msg_type: int, payload: bytes) -> int | None:
        """把一帧排进发送队列，返回它的 seq；队列满则丢弃并返回 None。"""
        frame = pack_frame(msg_type, self.seq + 1, payload)
        if len(self.outbox) + len(frame) > MAX_OUTBOX:
            # 丢帧而不是无限堆积：宁可少发一条输入，也不能把内存吃光
            self.dropped_frames += 1
            self.last_error = f"outbox full ({len(self.outbox)} B); dropped a frame"
            return None

        self.seq += 1
        self.outbox.extend(frame)
        return self.seq

    def flush(self) -> None:
        """尽量把发送队列写出去；写不完的部分留到下一帧。"""
        while self.outbox:
            try:
                written = self.sock.send(self.outbox)
            except BlockingIOError:
                return  # 内核发送缓冲区满了，下一帧再来
            except InterruptedError:
                continue  # 被信号打断，重试
            except OSError as exc:
                self.connected = False
                self.last_error = repr(exc)
                return

            if written <= 0:
                return
            del self.outbox[:written]

    def _read_available(self) -> None:
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                return  # 当前没有更多数据，正常
            except InterruptedError:
                continue  # 被信号打断，重试
            except OSError as exc:
                self.connected = False
                self.last_error = repr(exc)
                return

            if not data:
                self.connected = False
                self.last_error = "server closed the connection"
                return

            self.parser.feed(data)

    def _parse_available(self) -> list[Frame]:
        frames: list[Frame] = []
        try:
            while True:
                frame = self.parser.next_frame()
                if frame is None:
                    return frames
                frames.append(frame)
        except ProtocolError as exc:
            self.connected = False
            self.last_error = str(exc)
            return frames

    def close(self) -> None:
        self.connected = False
        try:
            self.sock.close()
        except OSError:
            pass