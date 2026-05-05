"""Run ONE Phase 2.B detection pass against the live Chrome/Upwork window.

Substrate recipe (refresh + Ctrl+Home + Ctrl+- ×6) + UIA walk + parse +
optional LLM triage against the active goal. No scheduler, no Discord, no
processing-loop. Useful to sanity-check the substrate side in isolation
before running scheduler.main full-time.

What it does NOT do:
  * Click any cards / open the panel.
  * Capture URLs.
  * Run goal-relevance check.
  * Draft anything.
  * Insert anything into feed_card_queue (unless you pass --enqueue).

Usage:
    # Just walk the feed, parse cards, print them. No LLM call.
    uv run python bin/run_one_detection_pass.py --no-triage

    # Walk + triage against the active goal in DB. No DB writes.
    uv run python bin/run_one_detection_pass.py

    # Walk + triage + actually enqueue survivors so processing_loop can
    # pick them up if the scheduler is also running.
    uv run python bin/run_one_detection_pass.py --enqueue

    # Override the goal in DB with an ad-hoc one for this run only.
    uv run python bin/run_one_detection_pass.py --goal "AI agents and RAG work, $80+/hr"

Pre-flight:
    Chrome must already be running with --force-renderer-accessibility and
    on the Most Recent feed. The fastest way:
        uv run python substrate/launch_chrome.py --profile "Moazzam" \\
            --url "https://www.upwork.com/nx/find-work/most-recent" --kill-existing
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import uiautomation as ua

from substrate import act, pacing


# Lift the action budget; we don't want the pacer parking us mid-cycle in dev.
pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))

from storage.connection import Database
from storage.goals import GoalStore, Goal
from storage.card_queue import FeedCardQueueStore
from storage.agent_runs import AgentRunStore
from upwork import feed, feed_zoom, feed_cards
from upwork.apply_form import detect_login_required
from ai.feed_triage import triage_feed_cards
from scheduler.failure_pings import LoginExpired


WINDOW = "Upwork"


def _print_card(idx: int, c: feed_cards.FeedCard) -> None:
    print(f"\n  [{idx + 1}] {c.title!r}")
    print(f"      posted:        {c.posted_text}")
    print(f"      budget:        {c.budget_text}")
    print(f"      experience:    {c.experience_level}")
    print(f"      est:           {c.est_label} {c.est_value}")
    if c.description_preview:
        print(f"      description:   {c.description_preview[:200]!r}")
    print(f"      skills:        {c.skills}")
    print(f"      payment_verif: {c.payment_verified}")
    print(f"      rating:        {c.rating}")
    print(f"      spent:         {c.spent}")
    print(f"      country:       {c.country}")
    print(f"      proposals:     {c.proposals}")


def _resolve_goal(db: Database, override_prose: str | None) -> Goal | None:
    if override_prose:
        # Ad-hoc goal for this run. Not persisted.
        return Goal(
            goal_id=-1,
            prose=override_prose,
            target_metric=None, target_value=None, horizon=None,
            min_hourly=None, min_budget=None, preferred_country=None, notes=None,
        )
    return GoalStore(db).get_active()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-triage", action="store_true",
                        help="Skip the LLM triage call. Just walk + parse + print.")
    parser.add_argument("--enqueue", action="store_true",
                        help="Insert survivors into feed_card_queue. Otherwise dry-run.")
    parser.add_argument("--goal", type=str, default=None,
                        help="Override the active goal with an ad-hoc prose string for this run.")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY") and not args.no_triage:
        print("OPENAI_API_KEY not set; falling back to --no-triage.", flush=True)
        args.no_triage = True

    if args.enqueue:
        if "DATABASE_URL" not in os.environ:
            print("DATABASE_URL not set but --enqueue requested; aborting.", flush=True)
            return 1

    db: Database | None = None
    if "DATABASE_URL" in os.environ:
        db = Database(os.environ["DATABASE_URL"])

    goal: Goal | None = None
    if not args.no_triage:
        if db is None:
            print("DATABASE_URL not set; cannot read active goal. Re-run with --no-triage or --goal.", flush=True)
            return 1
        goal = _resolve_goal(db, args.goal)
        if goal is None:
            print("No active goal in DB and no --goal override; nothing to triage against.", flush=True)
            print("Either set a goal via DM (the bot's set_goal tool) or pass --goal '...' here.", flush=True)
            return 1

    print("\n=== Phase 2.B one-shot detection pass ===")
    if goal is not None:
        print(f"Goal (id={goal.goal_id}): {goal.prose!r}")
    else:
        print("Triage: SKIPPED (--no-triage)")
    print(f"Enqueue: {args.enqueue}")

    with ua.UIAutomationInitializerInThread():
        # Tell substrate.act to accept either window title; refresh_feed
        # opens a new tab whose title transitions through 'Google Chrome'
        # before settling on the Upwork tab.
        act.set_target_window(("Upwork", "Google Chrome"))

        # ----- Substrate recipe -----
        print("\n[1/3] substrate recipe (refresh + Ctrl+Home + Ctrl+- ×6)", flush=True)
        t0 = time.time()
        feed.refresh_feed(WINDOW)
        if detect_login_required():
            print("LOGIN REQUIRED. Open Chrome, sign in, retry.", flush=True)
            return 2
        act.focus_window(WINDOW)
        act.key("ctrl+home")
        time.sleep(0.6)
        feed_zoom.zoom_to_33pct()
        try:
            cards = feed_cards.extract_cards_from_window(WINDOW)
        finally:
            try:
                act.focus_window(WINDOW)
                feed_zoom.reset_zoom()
            except Exception:
                pass
        print(f"      walked + parsed in {time.time() - t0:.1f}s -> {len(cards)} cards")

        # ----- Print every parsed card -----
        print(f"\n[2/3] parsed cards:")
        for i, c in enumerate(cards):
            _print_card(i, c)

        if args.no_triage:
            print("\n[3/3] triage skipped (--no-triage). Done.")
            return 0

        # ----- Triage -----
        print(f"\n[3/3] triaging {len(cards)} cards via gpt-5-mini ...", flush=True)
        agent_runs = AgentRunStore(db) if db is not None else None
        if agent_runs is None:
            # Build a no-op agent_run_store stub. CostTracker tolerates None? No,
            # it expects the store. Easiest: require DATABASE_URL when triaging.
            print("DATABASE_URL not set; required for cost-tracking.", flush=True)
            return 1
        t0 = time.time()
        try:
            triage = triage_feed_cards(cards, goal, agent_run_store=agent_runs)
        except Exception as e:
            print(f"      triage call FAILED: {e!r}", flush=True)
            return 3
        print(f"      triage took {time.time() - t0:.1f}s; {len(triage.matches)}/{len(cards)} matched")
        for m in triage.matches:
            print(f"        [MATCH] {m.title!r}")
            print(f"                {m.reason}")

        # ----- Optional enqueue -----
        if not args.enqueue:
            print("\n  --enqueue not set; survivors NOT inserted into feed_card_queue. Done.")
            return 0

        # Validate against extracted titles + enqueue.
        extracted_titles = {c.title for c in cards if c.title}
        card_queue = FeedCardQueueStore(db)
        enqueued = 0
        dropped = 0
        for m in triage.matches:
            if m.title not in extracted_titles:
                print(f"  DROPPED (not in extracted set): {m.title!r}")
                dropped += 1
                continue
            card = next((c for c in cards if c.title == m.title), None)
            if card is None:
                continue
            qid = card_queue.enqueue(
                job_title=m.title,
                posted_text=card.posted_text,
                triage_reasoning=m.reason,
                goal_id=goal.goal_id if goal.goal_id is not None and goal.goal_id > 0 else None,
            )
            if qid is None:
                print(f"  SKIPPED (already in-flight or already known): {m.title!r}")
            else:
                print(f"  ENQUEUED queue_id={qid}: {m.title!r}")
                enqueued += 1
        print(f"\n  enqueued={enqueued} dropped={dropped} already_queued={len(triage.matches) - enqueued - dropped}")
        if enqueued > 0 and (goal.goal_id is None or goal.goal_id < 0):
            print("\n  WARNING: --goal was an ad-hoc override; goal_id_at_detect is NULL on these rows.")
            print("           processing_loop will fall back to the current active goal at process time.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
