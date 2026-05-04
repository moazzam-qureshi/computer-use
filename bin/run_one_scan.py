"""Run one bidder scan-cycle against the live Chrome/Upwork window.

For development/debugging: lets you iterate on parsing, scoring, and matching
without restarting the full Discord bot. Skips Discord posting; signals fire
to stdout instead so you can see what would have been posted.

Usage:
    uv run python bin/run_one_scan.py [--cycle full_scan|panel_skim|skim_only]
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

# Dev harness: lift the per-hour action budget so we can iterate without
# the pacer parking us for an hour mid-cycle. Production uses scheduler.main
# with the default 40 actions/hr.
pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))
from scheduler.config import Settings
from storage.connection import Database
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.conversations import SystemConfigStore
from domain.humanization import Humanizer, default_envelope, CycleType
from bidder.scan_cycle import run_one_cycle


class _ForcedCycleHumanizer(Humanizer):
    """Humanizer that forces a specific cycle type. Lets us bypass no_op/skim_only during dev."""
    def __init__(self, *args, forced_cycle: CycleType, **kwargs):
        super().__init__(*args, **kwargs)
        self._forced_cycle = forced_cycle

    def sample_cycle_type(self):
        return self._forced_cycle


def _print_signal(signal, order, job):
    print("=" * 60)
    print(f"SIGNAL fired: order_id={order.order_id} setup_id={signal.primary_setup_id}")
    print(f"  job_id={job.job_id}")
    print(f"  title={job.title!r}")
    print(f"  budget={job.budget_kind} ${job.budget_min_usd}")
    print(f"  client_country={job.client_country} payment_verified={job.client_payment_verified}")
    print(f"  skills={job.skills}")
    print(f"  doc_url={order.doc_url}")
    print(f"  cover_letter (first 200 chars):")
    print(f"    {(order.cover_letter_body or '')[:200]!r}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bidder scan cycle against live Chrome.")
    parser.add_argument("--cycle", default="full_scan",
                        choices=["full_scan", "panel_skim", "skim_only"],
                        help="Force a specific cycle type (default: full_scan to maximize signal chance).")
    args = parser.parse_args(argv)

    forced = {
        "full_scan": CycleType.FULL_SCAN,
        "panel_skim": CycleType.PANEL_SKIM,
        "skim_only": CycleType.SKIM_ONLY,
    }[args.cycle]

    settings = Settings.from_env()
    db = Database(settings.database_url)

    humanizer = _ForcedCycleHumanizer(
        rng=random.Random(),
        envelope=default_envelope(),
        forced_cycle=forced,
    )

    print(f"Running one cycle of type: {forced.value}", flush=True)

    # Allow input primitives when ANY Chrome window is foreground. This covers
    # the moment between Ctrl+T (new tab, title still 'New Tab') and the page
    # finishing navigation to Upwork. After the page loads, the tab title
    # contains 'Upwork' and the more specific match also passes.
    act.set_target_window(("Upwork", "Google Chrome"))

    with ua.UIAutomationInitializerInThread():
        run_one_cycle(
            humanizer=humanizer,
            setups_store=SetupStore(db),
            signal_store=SignalStore(db),
            job_store=JobStore(db),
            order_store=OrderStore(db),
            enrichment_store=EnrichmentStore(db),
            portfolio=PortfolioStore(db),
            agent_runs=AgentRunStore(db),
            scrape_runs=ScrapeRunStore(db),
            sysconfig=SystemConfigStore(db),
            on_signal=_print_signal,
        )

    print("Cycle complete.", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
