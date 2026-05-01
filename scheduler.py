"""
Run upwork_driver.py on a schedule, forever.

Cycle:
  - Wake up at a randomized interval (15-20 min by default).
  - Invoke the driver with --reload-first so the feed shows latest postings.
  - Driver reads, judges, dedups URLs, pings Discord on new matches.
  - Sleep until next cycle.

Stop with Ctrl+C. State (seen URLs, relevant_jobs.md, last_dump.json) persists
between runs.

Usage:
    uv run scheduler.py
    uv run scheduler.py --min 15 --max 20
    uv run scheduler.py --max-jobs 10  # forwarded to the driver each cycle
"""
from __future__ import annotations

import argparse
import io
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta

# UTF-8 console output (only when running as main script)
if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str) -> None:
    print(f"[scheduler {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_one_cycle(max_jobs: int, model: str, actions_per_hour: int) -> int:
    """Run upwork_driver.py once. Returns the driver's exit code."""
    cmd = [
        sys.executable, "-u", "upwork_driver.py",
        "--reload-first",
        "--max-jobs", str(max_jobs),
        "--model", model,
        "--actions-per-hour", str(actions_per_hour),
    ]
    log(f"Starting cycle: {' '.join(cmd[3:])}")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, check=False)
        elapsed = time.time() - t0
        log(f"Cycle complete in {elapsed:.0f}s (exit code {proc.returncode})")
        return proc.returncode
    except KeyboardInterrupt:
        raise
    except Exception as ex:
        log(f"Cycle errored: {ex}")
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=15, help="min minutes between cycles")
    ap.add_argument("--max", type=int, default=20, help="max minutes between cycles")
    ap.add_argument("--max-jobs", type=int, default=10, help="forwarded to driver")
    ap.add_argument("--model", default="gpt-4o-mini", help="forwarded to driver")
    ap.add_argument("--actions-per-hour", type=int, default=40,
                    help="pacing budget per cycle (forwarded to driver)")
    args = ap.parse_args()

    if args.min > args.max:
        print("--min must be <= --max", file=sys.stderr)
        sys.exit(1)

    log(f"Started. Cycle interval: {args.min}-{args.max} min. "
        f"Driver settings: max_jobs={args.max_jobs}, model={args.model}, "
        f"actions/hr={args.actions_per_hour}")
    log("Ctrl+C to stop.")

    cycle = 0
    try:
        while True:
            cycle += 1
            log(f"=== Cycle {cycle} ===")
            run_one_cycle(args.max_jobs, args.model, args.actions_per_hour)

            # Sleep until next cycle
            wait_min = random.uniform(args.min, args.max)
            wait_sec = wait_min * 60
            next_at = datetime.now() + timedelta(seconds=wait_sec)
            log(f"Sleeping {wait_min:.1f} min — next cycle at {next_at.strftime('%H:%M:%S')}")
            time.sleep(wait_sec)
    except KeyboardInterrupt:
        log("Stopped by user (Ctrl+C). Bye.")


if __name__ == "__main__":
    main()
