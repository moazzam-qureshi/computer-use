"""Run apply_executor against a single Order in the DB. Bypasses the scheduler.

For dev/debugging: lets you stage the apply form for a known order without
running the full Discord bot loop. The order must already exist in the DB
with a cover_letter_body (and ideally doc_url) populated.

Usage:
    # By order_id (most explicit):
    uv run python bin/run_apply_executor.py --order-id 42

    # OR pick the most recent awaiting_approval / approved order:
    uv run python bin/run_apply_executor.py --latest

    # By default really_submit=False (Phase 1 safety): the form is staged but
    # not submitted. Pass --really-submit to actually click Submit Proposal.
"""
from __future__ import annotations

import argparse
import sys
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import uiautomation as ua

from substrate import act, pacing
from scheduler.config import Settings
from storage.connection import Database
from storage.orders import OrderStore
from storage.jobs import JobStore
from storage.connects_ledger import ConnectsLedgerStore
from domain.humanization import Humanizer, default_envelope
from bidder.apply_executor import execute_approved_order


# Dev harness: same generous pacer as run_one_scan.
pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage apply form for a single Order.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--order-id", type=int, help="Run apply executor for this specific order_id.")
    g.add_argument("--latest", action="store_true",
                   help="Pick the most recent awaiting_approval or approved order.")
    g.add_argument("--url", type=str,
                   help="Bypass DB entirely: stage apply for this job URL with a synthetic cover letter. "
                        "Useful when you don't yet have a real signal-generated Order in the DB.")
    parser.add_argument("--cover-letter", type=str,
                        default="Hey, I spent some time going over your job description. Here's how I would approach it: <doc-url-placeholder>\nReply with a good time and we can hop on a 20-minute call.\n- Moazzam",
                        help="Cover letter text to paste (only used with --url).")
    parser.add_argument("--bid", type=float, default=100.0, help="Bid amount in USD (default 100).")
    parser.add_argument("--really-submit", action="store_true",
                        help="Actually click Submit Proposal. Default: stage and stop.")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    db = Database(settings.database_url)
    order_store = OrderStore(db)
    job_store = JobStore(db)
    connects = ConnectsLedgerStore(db)

    if args.url:
        # Synthetic mode: build an in-memory Order, no DB write. Skip status
        # transitions inside execute_approved_order by giving the order a
        # nullable order_id that the OrderStore can no-op on.
        from domain.types import Order

        class _NoopOrderStore:
            def update_status(self, *a, **k): pass
            def mark_submitted(self, *a, **k): pass

        order = Order(
            order_id=-1,
            signal_id=None,
            job_id="synthetic",
            setup_id=None,
            status="approved",
            bid_amount_usd=args.bid,
            connects_spent=None,
            cover_letter_body=args.cover_letter,
            doc_url=None,
            screening_answers_json=None,
            drafted_at=None,
            approved_at=None,
            submitted_at=None,
            failed_reason=None,
            idempotency_key="synthetic",
        )
        order_store_for_run = _NoopOrderStore()
        # Pass the raw job URL through; execute_approved_order calls
        # build_apply_url internally (legacy parity).
        job_url = args.url
        job_title = "(synthetic)"
    else:
        order_store_for_run = order_store
        if args.order_id:
            order = order_store.get(args.order_id)
            if order is None:
                print(f"Order {args.order_id} not found.", file=sys.stderr)
                return 1
        else:
            candidates = (order_store.list_by_status("approved")
                          + order_store.list_by_status("awaiting_approval"))
            if not candidates:
                print("No approved or awaiting_approval orders in DB. "
                      "Pass --url to test against a job URL directly.", file=sys.stderr)
                return 1
            order = max(candidates, key=lambda o: o.order_id or 0)
            print(f"Picked latest order_id={order.order_id} status={order.status}", flush=True)

        job = job_store.get(order.job_id)
        if job is None:
            print(f"Job {order.job_id} for order {order.order_id} not found.", file=sys.stderr)
            return 1
        job_url = job.url
        job_title = job.title

    print(f"Staging apply for order_id={order.order_id} bid=${args.bid} "
          f"really_submit={args.really_submit}", flush=True)
    print(f"  job: {job_title[:80]!r}", flush=True)
    print(f"  url: {job_url}", flush=True)
    print(f"  cover_letter ({len(order.cover_letter_body or '')} chars):", flush=True)
    print(f"    {(order.cover_letter_body or '')[:200]!r}", flush=True)

    # All Chrome titles legitimately seen during the apply flow:
    #   'Upwork'              - feed / job-detail tab
    #   'Submit a Proposal'   - apply page after navigation
    #   'Just a moment'       - Cloudflare challenge (transient, ~10-30s)
    #   'New Tab'             - between Ctrl+T and the page actually loading
    #   'Google Chrome'       - any other Chrome state
    # Without all of these, require_focus() raises FocusLost the moment
    # Cloudflare flips the title.
    from upwork.apply_form import TARGET_WINDOWS
    act.set_target_window(TARGET_WINDOWS)

    humanizer = Humanizer(rng=random.Random(), envelope=default_envelope())

    with ua.UIAutomationInitializerInThread():
        execute_approved_order(
            order, job_url,
            order_store=order_store_for_run,
            connects_ledger=connects,
            humanizer=humanizer,
            bid_amount_usd=args.bid,
            really_submit=args.really_submit,
        )

    print("Apply executor finished.", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
