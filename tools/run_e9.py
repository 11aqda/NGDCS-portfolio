"""E9 实验运行器：逐档位起服务端 + N 个机器人，每档跑 R 轮。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from protocol.encoders import ENCODER_NAMES

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# E9 的档位（敌机, 子弹）。
# 前 6 档来自 03-实验清单；后 3 档是 2026-10-25 追加的 ——
# 实测 100/800 时单核只占约 11%，碰不到 的 50% 舒适线，加档才能出拐点。
DEFAULT_LEVELS = (
    (10, 50),
    (20, 100),
    (30, 200),
    (50, 400),
    (80, 600),
    (100, 800),
    (150, 1200),
    (200, 1600),
    (250, 2000),
)

CRASH_MARKER = "tick loop 已崩溃"


def scenario_name(
    prefix: str, enemies: int, bullets: int, rep: int, encoder: str = ""
) -> str:
    """`E9-e30-b200-r2` / `E1-e30-b200-json-r1` —— 档位 + 轮次（+ 编码）。"""
    tail = f"-{encoder}" if encoder else ""
    return f"{prefix}-e{enemies}-b{bullets}{tail}-r{rep}"


def parse_levels(text: str) -> tuple[tuple[int, int], ...]:
    """解析 `30/200,100/800` 这种写法。"""
    levels = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        enemies, _, bullets = chunk.partition("/")
        if not bullets:
            raise ValueError(f"档位要写成 敌机/子弹，收到 {chunk!r}")
        levels.append((int(enemies), int(bullets)))
    return tuple(levels)


def parse_encoders(text: str) -> tuple[str, ...]:
    """解析 `binary,json` 这种写法；空串 = 不指定编码（场景名里也不带）。"""
    names = tuple(chunk.strip() for chunk in text.split(",") if chunk.strip())
    for name in names:
        if name not in ENCODER_NAMES:
            raise ValueError(f"未知编码器 {name!r}；可选：{', '.join(ENCODER_NAMES)}")
    return names


def build_server_cmd(
    args, enemies: int, bullets: int, name: str, encoder: str = ""
) -> list[str]:
    cmd = [
        sys.executable, "-X", "utf8", "-u", "-m", "server.main",
        str(args.port),
        "--enemies", str(enemies),
        "--bullets", str(bullets),
        "--scenario", name,
        "--metrics", str(args.metrics),
        "--seed", str(args.seed),
    ]
    if encoder:
        cmd += ["--encoder", encoder]
    return cmd


def build_bot_cmd(args, name: str, out_path: Path) -> list[str]:
    return [
        sys.executable, "-X", "utf8", "-u", "-m", "bot.main",
        "--port", str(args.port),
        "--clients", str(args.clients),
        "--duration", str(args.duration),
        "--ramp", str(args.ramp),
        "--seed", str(args.bot_seed),
        "--out", str(out_path),
    ]


def system_cpu_percent(window: float = 1.0) -> float | None:
    """采一次整机 CPU 占用，用来佐证"跑实验时机器是不是空闲的"。"""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.cpu_percent(interval=window)


def count_rows(csv_path: Path, scenario: str) -> int:
    """数一数这个场景在指标 CSV 里落了多少行 —— 校验采集真的发生了。"""
    if not csv_path.exists():
        return 0
    count = 0
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith(scenario + ",") or f",{scenario}," in line:
                count += 1
    return count


def run_one(args, enemies: int, bullets: int, rep: int, encoder: str = "") -> dict:
    name = scenario_name(args.prefix, enemies, bullets, rep, encoder)
    log_path = args.log_dir / f"{name}.log"
    bot_json = args.run_dir / f"{name}.json"

    result: dict = {
        "scenario": name,
        "enemies": enemies,
        "bullets": bullets,
        "rep": rep,
        "encoder": encoder or "",
        "clients_requested": args.clients,
        "duration_s": args.duration,
    }

    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.run_dir.mkdir(parents=True, exist_ok=True)

    result["system_cpu_before"] = system_cpu_percent(0.5)
    started = time.time()

    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            build_server_cmd(args, enemies, bullets, name, encoder),
            cwd=PROJECT_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(args.startup)
            # 机器人输出写文件而不是管道：管道会引入缓冲与编码问题，
            # 而 --out 本来就给出干净的 JSON
            bot_log = args.log_dir / f"{name}.bot.log"
            with bot_log.open("w", encoding="utf-8") as botlog:
                subprocess.run(
                    build_bot_cmd(args, name, bot_json),
                    cwd=PROJECT_ROOT,
                    stdout=botlog,
                    stderr=subprocess.STDOUT,
                    timeout=args.duration + 120,
                )
            time.sleep(args.teardown)
        except subprocess.TimeoutExpired:
            result["error"] = "机器人超时"
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
                server.kill()
                server.wait(timeout=10)

    result["seconds"] = round(time.time() - started, 1)
    result["system_cpu_after"] = system_cpu_percent(0.5)

    # 机器人汇总
    if bot_json.exists():
        try:
            summary = json.loads(bot_json.read_text(encoding="utf-8"))
            result["clients_connected"] = summary.get("clients_connected")
            result["snapshots"] = summary.get("snapshots")
            result["errors"] = summary.get("errors")
            result["error_samples"] = summary.get("error_samples")
            result["interval_p50_ms"] = summary.get("interval_p50_ms")
            result["rtt_p95_ms"] = summary.get("rtt_p95_ms")
        except json.JSONDecodeError as exc:
            result["error"] = f"机器人 JSON 解析失败：{exc}"
    else:
        result["error"] = "机器人没有写出汇总文件"

    # 服务端日志里有没有崩溃标记
    text = log_path.read_text(encoding="utf-8", errors="replace")
    result["server_crashed"] = CRASH_MARKER in text

    # 采集校验：这一轮到底落了行没有
    result["metric_rows"] = count_rows(args.metrics, name)

    result["ok"] = bool(
        not result.get("error")
        and not result["server_crashed"]
        and result.get("clients_connected") == args.clients
        and result.get("errors") == 0
        and result["metric_rows"] > 0
    )
    return result


def format_percent(value) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="跑 E9 实体数实验（逐档位 × 多轮）")
    parser.add_argument(
        "--levels", default="",
        help="档位，形如 30/200,100/800；默认用内置的 9 档",
    )
    parser.add_argument("--reps", type=int, default=2, help="每个档位跑几轮（每个档位重复跑，取一致结果）")
    parser.add_argument("--duration", type=float, default=180.0, help="每轮多少秒")
    parser.add_argument("--clients", type=int, default=4, help="机器人数量（E9 控制变量 = 4）")
    parser.add_argument("--ramp", type=float, default=4.0, help="每秒起几个机器人")
    parser.add_argument("--port", type=int, default=9527)
    parser.add_argument("--prefix", default="E9", help="场景名前缀")
    parser.add_argument("--seed", type=int, default=1, help="服务端实体生成种子（固定 = 可复现）")
    parser.add_argument("--bot-seed", type=int, default=99, help="机器人按键随机种子")
    parser.add_argument("--startup", type=float, default=3.0, help="起服务端后等几秒再上机器人")
    parser.add_argument("--teardown", type=float, default=1.0, help="机器人跑完后等几秒再关服务端")
    parser.add_argument("--metrics", type=Path, default=PROJECT_ROOT / "data" / "server_metrics.csv")
    parser.add_argument("--log-dir", type=Path, default=PROJECT_ROOT / "data" / "logs")
    parser.add_argument("--run-dir", type=Path, default=PROJECT_ROOT / "data" / "runs")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "runs" / "manifest.json")
    parser.add_argument(
        "--reset-metrics", action="store_true",
        help="开跑前清空指标 CSV（正式实验建议加：否则旧数据会混进图里）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    parser.add_argument("--stop-on-fail", action="store_true", help="有一轮不达标就停下")
    parser.add_argument(
        "--encoders",
        default="",
        help="要对比的编码器（E1 用），逗号分隔，如 binary,binary_fast,json；"
        "留空 = 不指定（服务端用默认 binary，场景名里也不带编码段，保持 E9 的命名不变）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    levels = parse_levels(args.levels) if args.levels else DEFAULT_LEVELS
    encoders = parse_encoders(args.encoders)
    combos = [(e, b, enc) for enc in (encoders or ("",)) for e, b in levels]

    per_round = args.startup + args.duration + args.teardown + 3
    total_rounds = len(combos) * args.reps

    log(f"Python：{sys.executable}")
    log(f"档位 {len(levels)} 个 × 编码 {len(encoders) or 1} 种 × {args.reps} 轮 = {total_rounds} 轮")
    log(f"每轮约 {per_round:.0f} 秒 → 预计 {total_rounds * per_round / 60:.0f} 分钟")
    log(f"控制变量：{args.clients} 个机器人、seed {args.seed}、ramp {args.ramp}")
    log(f"编码器：{', '.join(encoders) if encoders else 'binary（默认，场景名不带编码段）'}")
    log(f"指标 CSV：{args.metrics}")

    if args.reset_metrics and args.metrics.exists():
        args.metrics.unlink()
        scenario_csv = args.metrics.with_name("server_scenario.csv")
        if scenario_csv.exists():
            scenario_csv.unlink()
        log(f"已清空 {args.metrics}（含 server_scenario.csv）")
    elif args.metrics.exists() and not args.dry_run:
        log("指标 CSV 已存在且未加 --reset-metrics：旧数据会一起进图，确认这是你要的")

    if args.dry_run:
        print()
        for rep in range(1, args.reps + 1):
            for enemies, bullets, encoder in combos:
                name = scenario_name(args.prefix, enemies, bullets, rep, encoder)
                enc_text = f"编码 {encoder or 'binary':<7}"
                print(f"  {name:<28} {enc_text} 敌机 {enemies:>3}  子弹 {bullets:>4}  "
                      f"{args.duration:.0f}s × {args.clients} 机器人")
        print("\n（--dry-run：没有真的执行）")
        return

    results: list[dict] = []
    started = time.time()

    for rep in range(1, args.reps + 1):
        log(f"===== 第 {rep} 轮开始 =====")
        for index, (enemies, bullets, encoder) in enumerate(combos, start=1):
            name = scenario_name(args.prefix, enemies, bullets, rep, encoder)
            log(f"  [{rep}.{index}/{len(combos)}] {name} 开始"
                f"（编码 {encoder or 'binary'}，敌机 {enemies} 子弹 {bullets}）")

            result = run_one(args, enemies, bullets, rep, encoder)
            results.append(result)

            status = "OK " if result["ok"] else "!!!"
            extra = ""
            if not result["ok"]:
                problems = []
                if result.get("error"):
                    problems.append(str(result["error"]))
                if result.get("server_crashed"):
                    problems.append("服务端 tick loop 崩溃")
                if result.get("errors"):
                    problems.append(f"机器人报错 {result['errors']} 条")
                if result["metric_rows"] == 0:
                    problems.append("没落任何指标行")
                extra = "  ← " + "；".join(problems)
            log(
                f"    {status} {result['seconds']:.0f}s  "
                f"连接 {result.get('clients_connected')}/{args.clients}  "
                f"快照 {result.get('snapshots')}  "
                f"指标行 {result['metric_rows']}  "
                f"整机CPU {format_percent(result['system_cpu_before'])}"
                f"→{format_percent(result['system_cpu_after'])}{extra}"
            )

            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(
                json.dumps(
                    {"args": vars(args) | {"metrics": str(args.metrics)}, "results": results},
                    ensure_ascii=False, indent=2, default=str,
                )
                + "\n",
                encoding="utf-8",
            )

            if not result["ok"] and args.stop_on_fail:
                log("--stop-on-fail：有一轮不达标，停下")
                return

    elapsed = time.time() - started
    good = sum(1 for item in results if item["ok"])
    log(f"===== 全部结束：{good}/{len(results)} 轮达标，用时 {elapsed / 60:.1f} 分钟 =====")
    log(f"清单已写入 {args.manifest}")
    log(f"下一步：python -X utf8 -m tools.plot --csv {args.metrics} --out docs/figures --scenario {args.prefix}-")

    if good != len(results):
        log("有不达标的轮次，先看 manifest 里的 problems 再决定要不要重跑")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[run_e9] 已中断")