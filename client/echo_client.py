"""12 字节帧头的回声客户端。"""

import socket
import sys
import time

from protocol.framing import ProtocolError, recv_frame, send_frame
from protocol.messages import ECHO

HOST = "127.0.0.1"
PORT = 9000


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT

    # create_connection：DNS/连接/超时一次搞定，超时避免连不上时永久卡住
    with socket.create_connection((host, port), timeout=5) as sock:
        print(f"[client] connected to {host}:{port}（输入一行回车发送，空行退出）")
        seq = 0
        while True:
            try:
                line = input("> ")
            except EOFError:  # 管道输入结束时也会走到这里
                break
            line = line.strip()
            if not line:
                break

            seq += 1
            payload = line.encode("utf-8")  # 网络上只能传字节，不能传 str
            t0 = time.perf_counter()
            try:
                send_frame(sock, ECHO, seq, payload)
                echoed = recv_frame(sock)  # 阻塞，直到读到一个完整帧
            except (ProtocolError, OSError) as exc:
                print(f"[client] error: {exc}")
                break
            rtt_ms = (time.perf_counter() - t0) * 1000

            if echoed is None:
                print("[client] server closed the connection")
                break

            if echoed.msg_type != ECHO or echoed.seq != seq:
                print(
                    f"[client] 响应不匹配：期望 type=0x{ECHO:02X} seq={seq}，"
                    f"收到 type=0x{echoed.msg_type:02X} seq={echoed.seq}"
                )

            text = echoed.payload.decode("utf-8", errors="replace")
            shown = text if len(text) <= 60 else text[:60] + "…"
            print(
                f"[client] echo type=0x{echoed.msg_type:02X} seq={echoed.seq} "
                f"{len(echoed.payload)} B: {shown!r}  RTT {rtt_ms:.3f} ms"
            )


if __name__ == "__main__":
    main()
