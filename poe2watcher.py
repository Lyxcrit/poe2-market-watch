#!/usr/bin/env python3
"""
POE2 arbitrage snapshot watcher.

Watches POE2 Scout SnapshotPairs and runs poe2checker.py only when a fresh
snapshot hash appears. Uses poe2checker.py --json, then renders a compact
operator-style view focused on the best trade.

This does not automate the game client.
This does not place trades.
This only watches public snapshot data and runs local calculations.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text


DEFAULT_BASE = "https://poe2scout.com/api"
DEFAULT_REALM = "poe2"
DEFAULT_LEAGUE = "runes"
DEFAULT_CHECKER = "poe2checker.py"
DEFAULT_STATE_DIR = ".poe2watcher"


def parse_args() -> Tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        description="Watch POE2 Scout snapshots and run poe2checker.py only on fresh snapshots."
    )

    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--realm", default=DEFAULT_REALM)
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    parser.add_argument("--checker", default=DEFAULT_CHECKER)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)

    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--hold-seconds", type=int, default=300)
    parser.add_argument(
        "--run-initial",
        action="store_true",
        help="Run poe2checker.py immediately on first snapshot instead of waiting for a change.",
    )
    parser.add_argument(
        "--alt-screen",
        action="store_true",
        help="Use Rich alternate-screen mode. Default is off so terminal scrollback remains usable.",
    )

    parser.add_argument("--budget-currency", default="Divine Orb")
    parser.add_argument("--budget-amount", default="20")
    parser.add_argument("--min-edge", default="0.08")
    parser.add_argument("--fee-buffer", default="0.03")
    parser.add_argument("--min-row-volume", default="50000")
    parser.add_argument("--min-side-traded", default="25")
    parser.add_argument("--min-stock", default="50")
    parser.add_argument("--max-middle-units", default="500")
    parser.add_argument("--min-exit-row-volume", default="100000")
    parser.add_argument("--max-liquidity-use", default="0.10")
    parser.add_argument("--gold-profile", default="gold_profile.json")
    parser.add_argument("--max-gold", default="250000")
    parser.add_argument("--sort", default="profit_per_gold")
    parser.add_argument("--top", default="3")
    parser.add_argument("--debug-rejections", action="store_true")
    parser.add_argument("--prefer-liquid", action="store_true")

    args, extra_checker_args = parser.parse_known_args()
    return args, extra_checker_args


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "never"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def snapshot_url(args: argparse.Namespace) -> str:
    return f"{args.base.rstrip('/')}/{args.realm}/Leagues/{args.league}/SnapshotPairs"


def fetch_snapshot(url: str) -> Tuple[bytes, str, int, int]:
    headers = {
        "User-Agent": "poe2-arb-snapshot-watcher/0.2 contact:lyxcrit@gmail.com",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    raw = response.content
    digest = hashlib.sha256(raw).hexdigest()

    try:
        data = response.json()
        if isinstance(data, list):
            row_count = len(data)
        elif isinstance(data, dict):
            row_count = len(data.get("data", data.get("items", data.get("rows", []))))
        else:
            row_count = 0
    except Exception:
        row_count = 0

    return raw, digest, row_count, len(raw)


def write_snapshot(state_dir: Path, raw: bytes, digest: str) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)

    latest_path = state_dir / "latest_snapshot.json"
    hash_path = state_dir / f"snapshot_{digest[:12]}.json"

    latest_path.write_bytes(raw)
    hash_path.write_bytes(raw)

    return latest_path


def build_checker_command(
    args: argparse.Namespace,
    snapshot_file: Path,
    extra_checker_args: List[str],
) -> List[str]:
    cmd = [
        sys.executable,
        args.checker,
        "--json",
        "--snapshot-file",
        str(snapshot_file),
        "--realm",
        args.realm,
        "--league",
        args.league,
        "--budget-currency",
        args.budget_currency,
        "--budget-amount",
        str(args.budget_amount),
        "--min-edge",
        str(args.min_edge),
        "--fee-buffer",
        str(args.fee_buffer),
        "--min-row-volume",
        str(args.min_row_volume),
        "--min-side-traded",
        str(args.min_side_traded),
        "--min-stock",
        str(args.min_stock),
        "--max-middle-units",
        str(args.max_middle_units),
        "--min-exit-row-volume",
        str(args.min_exit_row_volume),
        "--max-liquidity-use",
        str(args.max_liquidity_use),
        "--gold-profile",
        args.gold_profile,
        "--max-gold",
        str(args.max_gold),
        "--sort",
        args.sort,
        "--top",
        str(args.top),
    ]

    if args.debug_rejections:
        cmd.append("--debug-rejections")

    if args.prefer_liquid:
        cmd.append("--prefer-liquid")

    cmd.extend(extra_checker_args)
    return cmd


def run_checker(
    args: argparse.Namespace,
    snapshot_file: Path,
    extra_checker_args: List[str],
) -> Tuple[int, str]:
    checker_path = Path(args.checker)
    if not checker_path.is_file():
        return 2, f"checker script not found: {checker_path}"

    cmd = build_checker_command(args, snapshot_file, extra_checker_args)

    completed = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    output_parts = []

    if completed.stdout:
        output_parts.append(completed.stdout.rstrip())

    if completed.stderr:
        output_parts.append("\n[stderr]\n" + completed.stderr.rstrip())

    return completed.returncode, "\n".join(output_parts).strip()


def format_checker_json_for_trade_window(raw_output: str) -> str:
    try:
        data = json.loads(raw_output)
    except Exception:
        return raw_output

    opps = data.get("opportunities", [])
    budget_currency = data.get("budget_currency", "budget currency")
    gold_cap = data.get("max_gold")
    row_count = data.get("row_count")
    graph_size = data.get("graph_size")

    if not opps:
        return (
            "NO EXECUTABLE ARBITRAGE FOUND\n"
            + "=" * 78
            + "\n"
            + f"Rows: {row_count} | Graph items: {graph_size} | Gold cap: {gold_cap}\n\n"
            + "Try loosening one setting at a time:\n"
            + "- --max-gold\n"
            + "- --max-middle-units\n"
            + "- --min-exit-row-volume\n"
            + "- --max-liquidity-use\n"
            + "- --min-edge\n"
        )

    lines: List[str] = []
    lines.append("FRESH SNAPSHOT ARBITRAGE WINDOW")
    lines.append("=" * 78)

    if gold_cap is not None:
        try:
            gold_cap_text = f"{float(gold_cap):,.0f}"
        except Exception:
            gold_cap_text = str(gold_cap)
        lines.append(f"Rows: {row_count} | Graph items: {graph_size} | Gold cap: {gold_cap_text}")
    else:
        lines.append(f"Rows: {row_count} | Graph items: {graph_size}")

    lines.append("")

    best = opps[0]
    best_plan = best["integer_plan"]
    best_liq = best.get("liquidity", {})

    lines.append("BEST TRADE")
    lines.append("-" * 78)
    lines.append(f"Route: {' -> '.join(best['route'])}")
    lines.append(f"Spend: {best_plan['start_to_use']} {budget_currency}")
    lines.append(f"Expected final: {best_plan['final_amount']} {budget_currency}")
    lines.append(
        f"Expected profit: +{best_plan['profit']} {budget_currency} "
        f"({best_plan['profit_pct']:.2f}%)"
    )
    lines.append(f"Estimated gold: {best_plan['total_gold']:,.0f}")

    ppg = best_plan.get("profit_per_million_gold")
    if ppg is not None:
        lines.append(f"Profit per 1M gold: {ppg:.4f} {budget_currency}")

    lines.append(
        f"Liquidity use: {best_liq.get('liquidity_use', 0):.2%} | "
        f"Middle units: {best_liq.get('middle_units', 0):.0f} | "
        f"Exit row volume: {best_liq.get('exit_row_volume', 0):,.0f}"
    )

    lines.append("")
    lines.append("TRADE STEPS")
    lines.append("-" * 78)

    for i, step in enumerate(best_plan["steps"], start=1):
        lines.append(
            f"{i}. {step['in']} {step['src']} -> {step['out']} {step['dst']} "
            f"| gold {step['gold']:,.0f}"
        )

    lines.append("")
    lines.append("VALIDATE IN THIS ORDER")
    lines.append("-" * 78)

    steps = best_plan["steps"]
    if len(steps) >= 3:
        exit_step = steps[2]
        middle_step = steps[1]
        start_step = steps[0]

        lines.append(
            f"1. EXIT LEG FIRST: confirm {exit_step['in']} {exit_step['src']} "
            f"-> about {exit_step['out']} {exit_step['dst']}"
        )
        lines.append(
            f"2. MIDDLE LEG: confirm {middle_step['in']} {middle_step['src']} "
            f"-> about {middle_step['out']} {middle_step['dst']}"
        )
        lines.append(
            f"3. START LEG LAST: confirm {start_step['in']} {start_step['src']} "
            f"-> about {start_step['out']} {start_step['dst']}"
        )

    if len(opps) > 1:
        lines.append("")
        lines.append("OTHER CANDIDATES")
        lines.append("-" * 78)

        for idx, opp in enumerate(opps[1:], start=2):
            plan = opp["integer_plan"]
            liq = opp.get("liquidity", {})
            ppg = plan.get("profit_per_million_gold")
            ppg_text = f"{ppg:.4f}" if ppg is not None else "n/a"

            lines.append(
                f"{idx}. {' -> '.join(opp['route'])} | "
                f"+{plan['profit']} {budget_currency} | "
                f"gold {plan['total_gold']:,.0f} | "
                f"profit/1M gold {ppg_text} | "
                f"middle {liq.get('middle_units', 0):.0f}"
            )

    lines.append("")
    lines.append("Manual only. Recheck the exit leg before executing.")

    return "\n".join(lines)


def render_dashboard(state: Dict[str, Any]) -> Group:
    title = Text("POE2 Arbitrage Watcher", style="bold cyan")
    subtitle = Text("Fresh snapshot -> gold-aware route -> manual validation", style="dim")

    header = Panel(
        Align.center(Group(title, subtitle)),
        border_style="cyan",
        box=box.ROUNDED,
    )

    status_text = Text()
    status_text.append("Mode: ", style="bold")
    status_text.append(str(state["mode"]), style="green")
    status_text.append("   |   Hash: ", style="bold")
    status_text.append(str(state.get("hash_short") or "unknown"), style="yellow")
    status_text.append("   |   Rows: ", style="bold")
    status_text.append(str(state.get("row_count") or "unknown"), style="white")
    status_text.append("   |   Last change: ", style="bold")
    status_text.append(fmt_time(state.get("last_change")), style="white")
    status_text.append("   |   Gold cap: ", style="bold")
    status_text.append(str(state.get("max_gold")), style="white")

    status_panel = Panel(
        status_text,
        title="Status",
        border_style="green",
        box=box.ROUNDED,
    )

    now = time.time()

    if state.get("hold_until") and state["hold_until"] > now:
        remaining = int(state["hold_until"] - now)
        progress = Progress(
            TextColumn("[bold yellow]Fresh trade window[/bold yellow]"),
            BarColumn(),
            TextColumn(f"{remaining}s remaining"),
            expand=True,
        )
        progress.add_task(
            "hold",
            total=state["hold_seconds"],
            completed=state["hold_seconds"] - remaining,
        )
        countdown_panel = Panel(progress, border_style="yellow", box=box.ROUNDED)
    else:
        next_poll = max(0, int(state.get("next_poll_at", now) - now))
        progress = Progress(
            TextColumn("[bold blue]Watching for new snapshot[/bold blue]"),
            BarColumn(),
            TextColumn(f"next poll in {next_poll}s"),
            expand=True,
        )
        completed = state["poll_seconds"] - next_poll
        completed = max(0, min(state["poll_seconds"], completed))
        progress.add_task("poll", total=state["poll_seconds"], completed=completed)
        countdown_panel = Panel(progress, border_style="blue", box=box.ROUNDED)

    if state.get("error"):
        output_panel = Panel(
            Text(state["error"], style="bold red"),
            title="Error",
            border_style="red",
            box=box.ROUNDED,
        )
    elif state.get("checker_output"):
        output_panel = Panel(
            Text(state["checker_output"], style="white"),
            title="Best Arbitrage Trade",
            border_style="magenta",
            box=box.ROUNDED,
        )
    else:
        output_panel = Panel(
            Text(
                "Waiting for a fresh snapshot hash. Use --run-initial for a first-run test.",
                style="dim",
            ),
            title="Best Arbitrage Trade",
            border_style="magenta",
            box=box.ROUNDED,
        )

    footer = Panel(
        Text(
            "Validate exit leg first. Do not execute from snapshot data alone.",
            style="bold yellow",
        ),
        border_style="yellow",
        box=box.ROUNDED,
    )

    return Group(header, status_panel, countdown_panel, output_panel, footer)


def main() -> None:
    args, extra_checker_args = parse_args()

    console = Console()
    url = snapshot_url(args)
    state_dir = Path(args.state_dir)

    state: Dict[str, Any] = {
        "league": f"{args.realm}/{args.league}",
        "url": url,
        "mode": "starting",
        "last_check": None,
        "last_change": None,
        "hash": None,
        "hash_short": None,
        "row_count": None,
        "byte_count": 0,
        "poll_seconds": args.poll_seconds,
        "hold_seconds": args.hold_seconds,
        "hold_until": None,
        "next_poll_at": time.time(),
        "checker_output": "",
        "error": "",
        "max_gold": args.max_gold,
        "top": args.top,
    }

    with Live(
        render_dashboard(state),
        console=console,
        refresh_per_second=4,
        screen=args.alt_screen,
    ) as live:
        while True:
            now = time.time()

            if state.get("hold_until") and state["hold_until"] > now:
                live.update(render_dashboard(state))
                time.sleep(1)
                continue

            if state.get("hold_until") and state["hold_until"] <= now:
                state["hold_until"] = None
                state["mode"] = "watching"
                state["next_poll_at"] = time.time() + args.poll_seconds

            if time.time() < state["next_poll_at"]:
                live.update(render_dashboard(state))
                time.sleep(1)
                continue

            state["mode"] = "polling"
            state["error"] = ""
            live.update(render_dashboard(state))

            try:
                raw, digest, row_count, byte_count = fetch_snapshot(url)
                state["last_check"] = utc_now()
                state["row_count"] = row_count
                state["byte_count"] = byte_count

                is_initial = state["hash"] is None
                is_changed = state["hash"] is not None and digest != state["hash"]

                state["hash"] = digest
                state["hash_short"] = digest[:12]

                should_run = is_changed or (is_initial and args.run_initial)

                if should_run:
                    state["mode"] = "fresh snapshot found"
                    state["last_change"] = utc_now()
                    live.update(render_dashboard(state))

                    snapshot_file = write_snapshot(state_dir, raw, digest)

                    state["mode"] = "running arbitrage scan"
                    live.update(render_dashboard(state))

                    return_code, output = run_checker(
                        args=args,
                        snapshot_file=snapshot_file,
                        extra_checker_args=extra_checker_args,
                    )

                    if return_code != 0:
                        state["error"] = (
                            f"poe2checker.py exited with code {return_code}\n\n{output}"
                        )
                        state["checker_output"] = ""
                    else:
                        state["checker_output"] = format_checker_json_for_trade_window(output)
                        state["error"] = ""

                    state["mode"] = "holding fresh output"
                    state["hold_until"] = time.time() + args.hold_seconds
                    live.update(render_dashboard(state))

                else:
                    state["mode"] = "watching"
                    state["next_poll_at"] = time.time() + args.poll_seconds
                    live.update(render_dashboard(state))

            except KeyboardInterrupt:
                raise
            except Exception as e:
                state["mode"] = "error"
                state["error"] = str(e)
                state["next_poll_at"] = time.time() + args.poll_seconds
                live.update(render_dashboard(state))
                time.sleep(2)


if __name__ == "__main__":
    main()
