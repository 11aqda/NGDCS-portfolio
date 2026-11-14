"""指标落盘：把每秒统计追加写进 CSV，供 `tools/plot.py` 出图。"""

from __future__ import annotations

import csv
import time
from pathlib import Path

try:  # psutil 只用来采 CPU / 内存，缺了不影响核心功能
    import psutil
except ImportError:  # pragma: no cover - 取决于环境
    psutil = None


METRICS_FIELDS = [
    "timestamp",
    "t_mono",
    "scenario_id",
    "client_count",
    "enemies",
    "bullets",
    "tick_ms_avg",
    "tick_ms_p95",
    "tick_ms_max",
    # tick 的四段拆解（2026-10-25 新增）：碰撞与通信必须能分开看
    "sim_ms",
    "collision_ms",
    "ser_ms",
    "net_ms",
    "encode_ms",
    "hits",
    "snapshot_bytes",
    "cpu_percent",
    "cpu_seconds",
    "mem_mb",
    "packets_out",
    "bytes_out",
    "packets_in",
    "bytes_in",
]

SCENARIO_FIELDS = [
    "timestamp",
    "scenario_id",
    "encoder",
    "tick_rate",
    "enemies",
    "bullets",
    "seed",
    "inject_loss",
    "inject_delay",
]


class MetricsSchemaError(RuntimeError):
    """已有 CSV 的表头与当前 `METRICS_FIELDS` 不一致。"""


def _open_for_append(path: Path, fields: list[str]):
    """打开 CSV 追加写；文件不存在或为空时先写表头。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0

    if not needs_header:
        with path.open(encoding="utf-8", newline="") as probe:
            try:
                existing = next(csv.reader(probe))
            except StopIteration:
                existing = []
        if existing and existing != list(fields):
            raise MetricsSchemaError(
                f"{path} 的表头与当前字段表不一致，拒绝追加（否则整列错位）：\n"
                f"  文件表头 {len(existing)} 列：{','.join(existing)}\n"
                f"  当前字段 {len(fields)} 列：{','.join(fields)}\n"
                f"  修法：python -X utf8 -m tools.migrate_metrics --dry-run 先看，"
                f"去掉 --dry-run 执行（会自动备份）"
            )

    handle = path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fields)
    if needs_header:
        writer.writeheader()
    return handle, writer


def write_scenario(
    path: str | Path,
    scenario_id: str,
    *,
    enemies: int = 0,
    bullets: int = 0,
    tick_rate: int = 20,
    seed: int = 1,
    encoder: str = "binary",
    inject_loss: float = 0.0,
    inject_delay: float = 0.0,
) -> None:
    """写一行场景信息（ 的 `server_scenario.csv`）。"""
    path = Path(path)
    handle, writer = _open_for_append(path, SCENARIO_FIELDS)
    try:
        writer.writerow(
            {
                "timestamp": _wall_clock(),
                "scenario_id": scenario_id,
                "encoder": encoder,
                "tick_rate": tick_rate,
                "enemies": enemies,
                "bullets": bullets,
                "seed": seed,
                "inject_loss": inject_loss,
                "inject_delay": inject_delay,
            }
        )
        handle.flush()
    finally:
        handle.close()


def _wall_clock() -> str:
    """本地墙上时钟，只用于人看（分析一律用 t_mono）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def cpu_percent_from_deltas(delta_cpu_s: float, delta_wall_s: float) -> float:
    """单核占用百分比：ΔCPU 秒数 ÷ Δ墙钟秒数 × 100。"""
    if delta_wall_s <= 0:
        return 0.0
    return delta_cpu_s / delta_wall_s * 100.0
    if delta_wall_s <= 0:
        return 0.0
    return delta_cpu_s / delta_wall_s * 100.0


class MetricsRecorder:
    """每秒写一行服务端指标。"""

    def __init__(
        self,
        path: str | Path,
        scenario_id: str = "",
        enable_process_stats: bool = True,
        live=None,
    ) -> None:
        self.path = Path(path)
        self.scenario_id = scenario_id or "adhoc"
        self.live = live
        self._t0 = time.monotonic()

        self._process = None
        self._last_cpu_total = 0.0
        self._last_wall = 0.0
        if enable_process_stats:
            self._init_process_stats()

        self._handle, self._writer = _open_for_append(self.path, METRICS_FIELDS)

    def _init_process_stats(self) -> None:
        if psutil is None:
            print("[metrics] 未安装 psutil，CPU / 内存两列将留空")
            return
        self._process = psutil.Process()
        # 记下基线：CPU 用量是「两次采样之间的增量」，没有基线就没有百分比
        cpu = self._process.cpu_times()
        self._last_cpu_total = cpu.user + cpu.system
        self._last_wall = time.monotonic()

    def _sample_process(self) -> tuple[float | str, float | str, float | str]:
        """返回 (本窗口 CPU%, 累计 CPU 秒数, 常驻内存 MB)。"""
        if self._process is None:
            return "", "", ""
        try:
            now = time.monotonic()
            cpu = self._process.cpu_times()
            mem_mb = self._process.memory_info().rss / 1024 / 1024

            cpu_total = cpu.user + cpu.system
            delta_cpu = cpu_total - self._last_cpu_total
            delta_wall = now - self._last_wall
            self._last_cpu_total = cpu_total
            self._last_wall = now
        except Exception:  # pragma: no cover - 进程信息偶发取不到
            return "", "", ""
        return (
            round(cpu_percent_from_deltas(delta_cpu, delta_wall), 2),
            round(cpu_total, 3),
            round(mem_mb, 1),
        )

    def write(
        self,
        *,
        client_count: int,
        enemies: int,
        bullets: int,
        stats: dict,
    ) -> dict:
        """把 `TickStats.flush` 的结果加上环境信息写成一行，并返回该行。"""
        cpu, cpu_seconds, mem_mb = self._sample_process()
        row = {
            "timestamp": _wall_clock(),
            "t_mono": round(time.monotonic() - self._t0, 3),
            "scenario_id": self.scenario_id,
            "client_count": client_count,
            "enemies": enemies,
            "bullets": bullets,
            "tick_ms_avg": stats["avg_ms"],
            "tick_ms_p95": stats["p95_ms"],
            "tick_ms_max": stats["max_ms"],
            "sim_ms": stats["sim_ms"],
            "collision_ms": stats["collision_ms"],
            "ser_ms": stats["ser_ms"],
            "net_ms": stats["net_ms"],
            # 嵌套在 ser 内的细分（不参与四段相加）：E1 的图 6-2 用它
            "encode_ms": stats["encode_ms"],
            "hits": stats["hits"],
            "snapshot_bytes": stats["snapshot_bytes"],
            "cpu_percent": cpu,
            "cpu_seconds": cpu_seconds,
            "mem_mb": mem_mb,
            "packets_out": stats["packets_out"],
            "bytes_out": stats["bytes_out"],
            "packets_in": stats["packets_in"],
            "bytes_in": stats["bytes_in"],
        }
        self._writer.writerow(row)
        # 每行都 flush：实验中途被 Ctrl+C 也不会丢掉已经采到的数据
        self._handle.flush()
        # CSV 与 Redis 用同一份 row，避免两套口径（--live-metrics 默认关）
        if self.live is not None:
            self.live.publish(row)
        return row

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:  # pragma: no cover
            pass