"""Run one Researcher pass against the live VirtualBox Chrome.

For verification before the autonomous scheduler is wired up. Drives a
real deep_search for each query, runs forensics via gpt-5-mini, prints
the findings to stdout, and persists them to the live DB.

Usage:
    # Default: read query portfolio from system_config (empty on a
    # fresh install — use --query/--filters to override for a one-off pass).
    uv run python bin/run_one_researcher_pass.py

    # One-off pass against a specific query, no portfolio config required:
    uv run python bin/run_one_researcher_pass.py \\
        --query "RAG engineer" \\
        --filter payment_verified=1 \\
        --filter hourly_rate=60- \\
        --max-jobs 10

    # Multiple queries:
    uv run python bin/run_one_researcher_pass.py \\
        --query "RAG engineer" --query "voice AI agent"

Prerequisites (Windows / VirtualBox VM):
    1. uv run python substrate/launch_chrome.py --profile "Moazzam" \\
           --url "https://www.upwork.com/nx/find-work/" --kill-existing
    2. .env present with OPENAI_API_KEY and DATABASE_URL set.
    3. Migrations applied: uv run python bin/migrate.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from substrate import act, pacing

# Dev harness: lift the per-hour action budget so a multi-query pass
# (~30-60 min wall-clock under default pacing) doesn't get parked.
pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))

from researcher.loop import run_researcher_pass
from storage.connection import Database


def _build_override_portfolio(args) -> list[dict]:
    """Build a one-off portfolio from --query/--filter args. Empty if no
    --query passed (caller falls back to system_config)."""
    if not args.query:
        return []
    filters_dict: dict[str, str] = {}
    for raw in args.filter or []:
        if "=" not in raw:
            print(f"[researcher] bad --filter {raw!r}; expected key=value",
                  file=sys.stderr)
            sys.exit(2)
        k, v = raw.split("=", 1)
        filters_dict[k.strip()] = v.strip()
    return [
        {"query": q, "filters": filters_dict.copy(),
         "added_at": "manual", "added_by": "verify-script"}
        for q in args.query
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query", action="append", default=[],
        help="One-off query (can be passed multiple times). If omitted, "
             "the system_config query portfolio is used.",
    )
    parser.add_argument(
        "--filter", action="append", default=[],
        help="key=value filter applied to all --query args. Same allowed "
             "keys as upwork.search.ALLOWED_FILTER_KEYS.",
    )
    parser.add_argument(
        "--max-jobs", type=int, default=10,
        help="Cap deep_search jobs per query. Default 10 (fast verify); "
             "production default is 30.",
    )
    parser.add_argument(
        "--window-title", default="Upwork",
        help="Substrate focus target. Default 'Upwork'.",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set; check your .env", file=sys.stderr)
        return 2

    # Configure substrate window targets (matches scheduler/main.py default).
    act.set_target_window(("Upwork", "Google Chrome"))

    db = Database(os.environ["DATABASE_URL"])
    override = _build_override_portfolio(args)
    if override:
        print(f"[researcher] using one-off portfolio: "
              f"{len(override)} queries", flush=True)
    else:
        print("[researcher] using system_config query portfolio", flush=True)

    print(f"[researcher] starting pass (max_jobs_per_query={args.max_jobs})...",
          flush=True)
    summary = run_researcher_pass(
        db,
        max_jobs_per_query=args.max_jobs,
        window_title=args.window_title,
        portfolio_override=override or None,
    )

    print("\n" + "=" * 72)
    print("[researcher] pass summary")
    print("=" * 72)
    print(json.dumps(
        {k: v for k, v in summary.items() if k != "per_query"},
        indent=2,
    ))
    print("\n[researcher] per-query:")
    for pq in summary.get("per_query", []):
        print(f"  - {pq.get('query', '?')!r}: status={pq.get('status', '?')}, "
              f"scanned={pq.get('scanned', 0)}, n_findings={pq.get('n_findings', 0)}"
              + (f", error={pq['error']}" if pq.get("error") else ""))

    # Pull the new findings we just persisted and print them so you can
    # eyeball whether gpt-5-mini's output is actually useful.
    if summary.get("findings_persisted", 0) > 0:
        from storage.findings import FindingStore
        fs = FindingStore(db)
        new = fs.list_by_status("new", days_back=1, limit=20)
        print("\n" + "=" * 72)
        print(f"[researcher] new findings ({len(new)}):")
        print("=" * 72)
        for f in new:
            print(f"\n#{f.finding_id} [{f.urgency}] {f.finding_type}")
            print(f"  HEADLINE: {f.headline}")
            print(f"  WHY:      {f.why_specific}")
            print(f"  PORTFOLIO: {f.portfolio_tie}")
            print(f"  ACTION:   {f.suggested_action}")
            print(f"  EVIDENCE: {f.evidence_job_ids}")

    return 0 if summary.get("queries_failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
