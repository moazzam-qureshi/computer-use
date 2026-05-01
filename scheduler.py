"""
Run upwork_driver.py on a schedule, forever. Once a day, also run the
search-driven research crawler (upwork_research.py) at a configurable hour.

Cycle:
  - Wake up at a randomized interval (15-20 min by default).
  - Invoke the scanner with --reload-first.
  - If we haven't run the research crawler today AND the current hour is
    >= --research-hour, also invoke upwork_research.py.
  - Sleep until next cycle.

Research-crawler tracking is stored in scheduler_state.json
(`research_last_run_date`).

Stop with Ctrl+C. State (DB, scheduler_state.json) persists between runs.

Usage:
    uv run scheduler.py
    uv run scheduler.py --min 15 --max 20
    uv run scheduler.py --max-jobs 10
    uv run scheduler.py --research-hour 6  # daily crawl at 06:00 local time
    uv run scheduler.py --no-research      # disable the daily crawler
"""
from __future__ import annotations

import argparse
import io
import json
import random
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# UTF-8 console output (only when running as main script)
if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


STATE_FILE = Path("scheduler_state.json")


def log(msg: str) -> None:
    print(f"[scheduler {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------
# State persistence
# ----------------------------------------------------------------------------

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _research_already_ran_today(state: dict) -> bool:
    last = state.get("research_last_run_date")
    return last == date.today().isoformat()


def _mark_research_ran_today(state: dict) -> None:
    state["research_last_run_date"] = date.today().isoformat()
    _save_state(state)


# ----------------------------------------------------------------------------
# Subprocess invocations
# ----------------------------------------------------------------------------

def run_scanner(max_jobs: int, model: str, actions_per_hour: int) -> int:
    """Run upwork_driver.py once. Returns exit code."""
    cmd = [
        sys.executable, "-u", "upwork_driver.py",
        "--reload-first",
        "--max-jobs", str(max_jobs),
        "--model", model,
        "--actions-per-hour", str(actions_per_hour),
    ]
    log(f"Starting scanner: {' '.join(cmd[3:])}")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, check=False)
        elapsed = time.time() - t0
        log(f"Scanner complete in {elapsed:.0f}s (exit code {proc.returncode})")
        return proc.returncode
    except KeyboardInterrupt:
        raise
    except Exception as ex:
        log(f"Scanner errored: {ex}")
        return -1


def run_research_crawler(max_jobs: int, actions_per_hour: int) -> int:
    """Run upwork_research.py for all queries in research_queries.txt."""
    cmd = [
        sys.executable, "-u", "upwork_research.py",
        "--max-jobs", str(max_jobs),
        "--actions-per-hour", str(actions_per_hour),
    ]
    log(f"Starting research crawler: {' '.join(cmd[3:])}")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, check=False)
        elapsed = time.time() - t0
        log(f"Research crawler complete in {elapsed:.0f}s (exit code {proc.returncode})")
        return proc.returncode
    except KeyboardInterrupt:
        raise
    except Exception as ex:
        log(f"Research crawler errored: {ex}")
        return -1


# ----------------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=15, help="min minutes between cycles")
    ap.add_argument("--max", type=int, default=20, help="max minutes between cycles")
    ap.add_argument("--max-jobs", type=int, default=10, help="forwarded to scanner")
    ap.add_argument("--model", default="gpt-4o-mini", help="forwarded to scanner")
    ap.add_argument("--actions-per-hour", type=int, default=40,
                    help="scanner pacing budget per cycle")
    ap.add_argument("--research-hour", type=int, default=6,
                    help="hour of day (0-23, local) to run the research crawler. "
                         "Crawler runs once per day on the first cycle that lands at "
                         "or after this hour.")
    ap.add_argument("--research-max-jobs", type=int, default=20,
                    help="max jobs per query for the research crawler")
    ap.add_argument("--research-actions-per-hour", type=int, default=80,
                    help="research crawler pacing budget (read-only, can be higher)")
    ap.add_argument("--no-research", action="store_true",
                    help="disable the daily research crawler")
    args = ap.parse_args()

    if args.min > args.max:
        print("--min must be <= --max", file=sys.stderr)
        sys.exit(1)
    if not (0 <= args.research_hour <= 23):
        print("--research-hour must be in 0..23", file=sys.stderr)
        sys.exit(1)

    log(f"Started. Cycle interval: {args.min}-{args.max} min. "
        f"Scanner: max_jobs={args.max_jobs}, model={args.model}, "
        f"actions/hr={args.actions_per_hour}.")
    if args.no_research:
        log("Research crawler DISABLED (--no-research).")
    else:
        log(f"Research crawler: daily at hour={args.research_hour}, "
            f"max_jobs/query={args.research_max_jobs}, "
            f"actions/hr={args.research_actions_per_hour}.")
    log("Ctrl+C to stop.")

    cycle = 0
    try:
        while True:
            cycle += 1
            log(f"=== Cycle {cycle} ===")
            run_scanner(args.max_jobs, args.model, args.actions_per_hour)

            # Daily research crawler check
            if not args.no_research:
                state = _load_state()
                if not _research_already_ran_today(state):
                    if datetime.now().hour >= args.research_hour:
                        log(f"Research crawler hasn't run today and hour "
                            f">= {args.research_hour} — kicking it off")
                        rc = run_research_crawler(
                            args.research_max_jobs, args.research_actions_per_hour
                        )
                        if rc == 0:
                            _mark_research_ran_today(state)
                        else:
                            log(f"Research crawler exited with {rc}; will retry "
                                f"on the next cycle (state not marked)")

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
