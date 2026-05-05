"""Phase 2.B Pass-1 worker: every ~60s, detect new feed cards via UIA at 33%
zoom, triage against the operator's active goal, queue survivors for Pass 2.

Goal-gated: no active goal -> idle. Pause-gated via system_config.bidder_paused.
Holds the ui_lock only during the substrate recipe; the triage LLM call runs
without the lock so the processing loop can pick up briefs / queued rows in
parallel.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

from substrate import act
from upwork import feed, feed_zoom, feed_cards
from upwork.apply_form import detect_login_required
from storage.connection import Database
from storage.goals import GoalStore
from storage.card_queue import FeedCardQueueStore
from storage.agent_runs import AgentRunStore
from storage.conversations import SystemConfigStore
from storage.bidder_state import BidderStateStore
from ai.feed_triage import triage_feed_cards
from scheduler.failure_pings import LoginExpired


WINDOW = "Upwork"
DEFAULT_INTERVAL_SECONDS = 60
NO_GOAL_LOG_INTERVAL = 60.0  # log "no active goal" at most once per minute


def _with_com(fn, *args, **kwargs):
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return fn(*args, **kwargs)


def _detection_pass(window_title: str = WINDOW) -> list[feed_cards.FeedCard]:
    """Substrate recipe + parse: refresh -> Ctrl+Home -> Ctrl+- ×6 ->
    observe -> parse -> Ctrl+0 reset. Returns the parsed FeedCards.

    Caller holds the ui_lock for the duration. May raise LoginExpired.
    """
    feed.refresh_feed(window_title)
    if detect_login_required():
        raise LoginExpired("Upwork session expired; need to log in via Chrome")
    act.focus_window(window_title)
    act.key("ctrl+home")
    time.sleep(0.6)
    feed_zoom.zoom_to_33pct()
    try:
        cards = feed_cards.extract_cards_from_window(window_title)
    finally:
        # Always reset zoom so the next cycle (and the processing loop's
        # panel walk) starts from a clean baseline. Per-tab zoom is sticky.
        try:
            act.focus_window(window_title)
            feed_zoom.reset_zoom()
        except Exception:
            pass
    return cards


async def run_detection_loop(
    bot,
    settings,
    db: Database,
    ui_lock: asyncio.Lock,
):
    """Forever-loop. Every DETECTION_INTERVAL_SECONDS:
       1. Pause gate (system_config.bidder_paused)
       2. Goal gate (GoalStore.get_active)
       3. Acquire ui_lock; substrate recipe; release lock
       4. Dedup against feed_card_queue inflight + 14-day history
       5. If new cards: triage call (no lock held)
       6. Validate survivors, enqueue
       7. Sleep
    """
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)

    goals_store = GoalStore(db)
    card_queue = FeedCardQueueStore(db)
    agent_runs = AgentRunStore(db)
    sysconfig = SystemConfigStore(db)
    bidder_state = BidderStateStore(db)

    interval = int(os.environ.get("DETECTION_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    last_no_goal_log_at: Optional[float] = None

    print(f"[detection] starting loop; interval={interval}s", flush=True)

    while True:
        try:
            # 1. Pause gate.
            if bool(sysconfig.get("bidder_paused") or False):
                await asyncio.sleep(interval)
                continue
            if bidder_state.get().paused:
                await asyncio.sleep(interval)
                continue

            # 2. Goal gate.
            goal = goals_store.get_active()
            if goal is None:
                now = time.monotonic()
                if last_no_goal_log_at is None or (now - last_no_goal_log_at) >= NO_GOAL_LOG_INTERVAL:
                    print("[detection] no active goal, idle", flush=True)
                    last_no_goal_log_at = now
                await asyncio.sleep(interval)
                continue
            last_no_goal_log_at = None

            # 3. Chrome health check (mirrors bidder_loop's pattern).
            from substrate.launch_chrome import ensure_chrome_running
            ok = await asyncio.to_thread(_with_com, ensure_chrome_running)
            if not ok:
                print("[detection] Chrome unavailable; sleeping 60s", flush=True)
                await asyncio.sleep(60)
                continue

            # 4. Substrate recipe under the ui_lock.
            print(f"[detection] cycle start goal_id={goal.goal_id}", flush=True)
            t0 = time.time()
            try:
                async with ui_lock:
                    cards = await asyncio.to_thread(_with_com, _detection_pass, WINDOW)
            except LoginExpired:
                owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                print("[detection] LOGIN_REQUIRED", flush=True)
                if channel is not None:
                    await channel.send(
                        f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                        f"Detection will resume on the next cycle."
                    )
                await asyncio.sleep(60)
                continue
            except Exception as e:
                print(f"[detection] substrate recipe failed: {e!r}", flush=True)
                await asyncio.sleep(interval)
                continue
            print(f"[detection] extracted {len(cards)} cards in {time.time() - t0:.1f}s", flush=True)

            # 5. Dedup against inflight + recent history.
            inflight = card_queue.list_inflight_titles()
            known_recent = card_queue.list_known_titles_recent(days=14)
            new_cards = [
                c for c in cards
                if c.title and c.title not in inflight and c.title not in known_recent
            ]
            print(f"[detection] {len(new_cards)} new cards after dedup (inflight={len(inflight)}, known={len(known_recent)})", flush=True)
            if not new_cards:
                await asyncio.sleep(interval)
                continue

            # 6. Triage call (no lock held; processing loop can run in parallel).
            t0 = time.time()
            try:
                triage = await asyncio.to_thread(
                    triage_feed_cards, new_cards, goal,
                    agent_run_store=agent_runs,
                )
            except Exception as e:
                print(f"[detection] triage call failed: {e!r}; skipping cycle", flush=True)
                await asyncio.sleep(interval)
                continue
            print(f"[detection] triage took {time.time() - t0:.1f}s; matched {len(triage.matches)}/{len(new_cards)}", flush=True)

            # 7. Validate survivors against extracted titles (defense in depth
            #    against em-dash corruption / hallucinated titles).
            extracted_titles = {c.title for c in new_cards if c.title}
            survivor_count = 0
            dropped_titles: list[str] = []
            for m in triage.matches:
                if m.title not in extracted_titles:
                    dropped_titles.append(m.title)
                    continue
                # Find the matching FeedCard for posted_text.
                card = next((c for c in new_cards if c.title == m.title), None)
                if card is None:
                    continue
                qid = card_queue.enqueue(
                    job_title=m.title,
                    posted_text=card.posted_text,
                    triage_reasoning=m.reason,
                    goal_id=goal.goal_id,
                )
                if qid is not None:
                    survivor_count += 1
                    print(f"[detection]   queued queue_id={qid} title={m.title[:80]!r}", flush=True)
            if dropped_titles:
                print(f"[detection] dropped {len(dropped_titles)} hallucinated titles: {dropped_titles}", flush=True)
            print(f"[detection] survivors={survivor_count}", flush=True)

            await asyncio.sleep(interval)
        except Exception as e:
            print(f"[detection] unhandled exception: {e!r}", flush=True)
            await asyncio.sleep(interval)
