"""Phase 2.B Pass-2 worker loop.

Each iteration:
  1. Pause + goal gates.
  2. Briefs first: if BriefStore.consume_pending returns a brief, run the
     existing Phase 2.A briefed_cycle (run_one_cycle with the ephemeral
     setup) and continue.
  3. Else: claim_next from feed_card_queue, walk panel via process_queued_card,
     post drafted orders to the channel.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from storage.connection import Database
from storage.goals import GoalStore
from storage.card_queue import FeedCardQueueStore
from storage.scan_briefs import BriefStore
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.bidder_state import BidderStateStore
from storage.conversations import SystemConfigStore
from bidder.scan_cycle import run_one_cycle
from bidder.job_processing import (
    process_queued_card, CardNotVisible, PanelCaptureFailed,
)
from bot.alerts import build_signal_embed, OrderApprovalView
from scheduler.failure_pings import LoginExpired


MAX_ATTEMPTS_BEFORE_SKIP = 3
QUEUE_IDLE_SLEEP_SECONDS = 5
PAUSE_SLEEP_SECONDS = 30


def _with_com(fn, *args, **kwargs):
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return fn(*args, **kwargs)


async def run_processing_loop(
    bot,
    settings,
    db: Database,
    humanizer,
    ui_lock: asyncio.Lock,
):
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)

    goals_store = GoalStore(db)
    card_queue = FeedCardQueueStore(db)
    brief_store = BriefStore(db)
    setups_store = SetupStore(db)
    signal_store = SignalStore(db)
    job_store = JobStore(db)
    order_store = OrderStore(db)
    enrichment_store = EnrichmentStore(db)
    portfolio_store = PortfolioStore(db)
    agent_runs = AgentRunStore(db)
    scrape_runs = ScrapeRunStore(db)
    sysconfig = SystemConfigStore(db)
    bidder_state = BidderStateStore(db)

    print("[processing] starting loop", flush=True)

    # Briefed scans reuse the existing on_signal handler shape so the channel
    # post / DM-suppression logic is identical to the legacy bidder.
    async def on_signal(signal, order, job):
        if (signal.market_state or {}).get("source") == "briefed_scan":
            print(f"[processing] suppressing channel post for briefed-scan signal_id={signal.signal_id}", flush=True)
            return
        setup = setups_store.get(signal.primary_setup_id)
        primary_match = next(
            (m for m in signal.matched_setups if m.get("setup_id") == signal.primary_setup_id),
            signal.matched_setups[0] if signal.matched_setups else {},
        )
        application_flags = primary_match.get("application_flags") or []
        embed = build_signal_embed(
            setup_name=setup.name, tier=setup.tier, title=job.title,
            budget_text=f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}",
            posted_text=job.posted_text or "recent",
            client_summary=job.client_country or "?",
            why_matched=", ".join(
                m["matched_rules"][0] if m.get("matched_rules") else ""
                for m in signal.matched_setups
            ),
            cover_letter_preview=order.cover_letter_body or "",
            application_flags=application_flags,
        )
        view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
        await channel.send(embed=embed, view=view)

    def sync_on_signal(signal, order, job):
        asyncio.run_coroutine_threadsafe(on_signal(signal, order, job), bot.loop)

    while True:
        try:
            # 1. Pause gate.
            if bool(sysconfig.get("bidder_paused") or False):
                await asyncio.sleep(PAUSE_SLEEP_SECONDS)
                continue
            if bidder_state.get().paused:
                await asyncio.sleep(PAUSE_SLEEP_SECONDS)
                continue

            # 2. Briefs win priority over detection-driven cards.
            pending_brief = brief_store.consume_pending()
            if pending_brief is not None:
                print(f"[processing] consuming brief brief_id={pending_brief.brief_id}", flush=True)
                try:
                    async with ui_lock:
                        await asyncio.to_thread(
                            _with_com,
                            run_one_cycle,
                            humanizer=humanizer,
                            setups_store=setups_store,
                            signal_store=signal_store,
                            job_store=job_store,
                            order_store=order_store,
                            enrichment_store=enrichment_store,
                            portfolio=portfolio_store,
                            agent_runs=agent_runs,
                            scrape_runs=scrape_runs,
                            sysconfig=sysconfig,
                            on_signal=sync_on_signal,
                            brief=pending_brief,
                        )
                    with db.connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT count(*) FROM orders WHERE setup_id = "
                                "(SELECT setup_id FROM scan_briefs WHERE brief_id = %s)",
                                (pending_brief.brief_id,),
                            )
                            drafts = cur.fetchone()[0]
                    brief_store.mark_done(
                        pending_brief.brief_id,
                        result_summary={"drafts_created": drafts},
                        cycle_notes="ok",
                    )
                    print(f"[processing] brief brief_id={pending_brief.brief_id} done; drafts={drafts}", flush=True)
                except LoginExpired:
                    owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                    if channel is not None:
                        await channel.send(
                            f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                            f"Briefed scan will retry on the next cycle."
                        )
                    brief_store.mark_failed(pending_brief.brief_id, error="login_required")
                except Exception as e:
                    print(f"[processing] brief brief_id={pending_brief.brief_id} failed: {e!r}", flush=True)
                    brief_store.mark_failed(pending_brief.brief_id, error=repr(e)[:500])
                continue  # re-check pause + briefs immediately

            # 3. Goal gate (only relevant for queued cards; briefs bypass it).
            current_goal = goals_store.get_active()
            if current_goal is None:
                await asyncio.sleep(QUEUE_IDLE_SLEEP_SECONDS)
                continue

            # 4. Claim next queued card.
            row = card_queue.claim_next()
            if row is None:
                await asyncio.sleep(QUEUE_IDLE_SLEEP_SECONDS)
                continue

            # 5. Resolve goal_at_detect with sensible fallback.
            goal_at_detect = None
            if row.goal_id_at_detect is not None:
                goal_at_detect = goals_store.get_by_id(row.goal_id_at_detect)
            if goal_at_detect is None:
                goal_at_detect = current_goal
            print(
                f"[processing] claimed queue_id={row.queue_id} "
                f"title={row.job_title[:80]!r} attempt={row.attempt_count} "
                f"goal_id={goal_at_detect.goal_id}",
                flush=True,
            )

            # 6. Run the per-row pipeline. MUST hold ui_lock for the full
            #    duration: process_queued_card opens a new tab, walks the feed,
            #    clicks a card, walks the panel for URL capture, and runs LLM
            #    extract. If detection fires its own substrate recipe at the
            #    same time, both loops fight Chrome focus and corrupt each
            #    other's state.
            try:
                async with ui_lock:
                    signal, order, job, status = await asyncio.to_thread(
                        _with_com,
                        process_queued_card,
                        row, goal_at_detect,
                        setups_store=setups_store,
                        signal_store=signal_store,
                        job_store=job_store,
                        order_store=order_store,
                        enrichment_store=enrichment_store,
                        portfolio=portfolio_store,
                        agent_runs=agent_runs,
                    )
            except LoginExpired:
                owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
                print("[processing] LOGIN_REQUIRED during processing", flush=True)
                if channel is not None:
                    await channel.send(
                        f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                        f"The bidder will resume on the next cycle."
                    )
                # Requeue so we retry once login is fixed.
                card_queue.requeue(row.queue_id)
                await asyncio.sleep(60)
                continue
            except PanelCaptureFailed as e:
                print(f"[processing] queue_id={row.queue_id} panel capture failed: {e!r}", flush=True)
                card_queue.mark_failed(row.queue_id, repr(e))
                continue
            except Exception as e:
                print(f"[processing] queue_id={row.queue_id} unhandled error: {e!r}", flush=True)
                card_queue.mark_failed(row.queue_id, repr(e))
                continue

            # 7. Handle each terminal status.
            if status == "not_visible":
                if row.attempt_count >= MAX_ATTEMPTS_BEFORE_SKIP:
                    print(f"[processing] queue_id={row.queue_id} not visible after {row.attempt_count} attempts; skipping", flush=True)
                    card_queue.mark_skipped(row.queue_id, "card_no_longer_visible")
                else:
                    print(f"[processing] queue_id={row.queue_id} not visible; requeueing (attempt={row.attempt_count})", flush=True)
                    card_queue.requeue(row.queue_id)
                continue

            if status.startswith("skipped"):
                # Persist URL if we got it via job, even on skip.
                if job is not None and job.url:
                    card_queue.set_url(row.queue_id, job.url)
                card_queue.mark_skipped(row.queue_id, status)
                continue

            if status == "drafted":
                assert signal is not None and order is not None and job is not None
                if job.url:
                    card_queue.set_url(row.queue_id, job.url)
                card_queue.mark_processed(row.queue_id, order.order_id)
                # Post to the channel via the same alert flow the legacy bidder used.
                try:
                    setup = setups_store.get(order.setup_id)
                    primary_match = next(
                        (m for m in signal.matched_setups if m.get("setup_id") == signal.primary_setup_id),
                        signal.matched_setups[0] if signal.matched_setups else {},
                    )
                    application_flags = primary_match.get("application_flags") or []
                    embed = build_signal_embed(
                        setup_name=setup.name, tier=setup.tier, title=job.title,
                        budget_text=_budget_text(job),
                        posted_text=job.posted_text or "recent",
                        client_summary=job.client_country or "?",
                        why_matched=row.triage_reasoning or "goal:relevant",
                        cover_letter_preview=order.cover_letter_body or "",
                        application_flags=application_flags,
                    )
                    view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
                    if channel is not None:
                        await channel.send(embed=embed, view=view)
                except Exception as e:
                    print(f"[processing] channel post failed for order_id={order.order_id}: {e!r}", flush=True)
                continue

            print(f"[processing] queue_id={row.queue_id} unexpected status={status!r}; marking failed", flush=True)
            card_queue.mark_failed(row.queue_id, f"unexpected_status:{status}")
        except Exception as e:
            print(f"[processing] unhandled exception in loop: {e!r}", flush=True)
            await asyncio.sleep(QUEUE_IDLE_SLEEP_SECONDS)


def _budget_text(job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return f"{job.budget_kind or 'unknown'}"
    if job.budget_max_usd is None or job.budget_max_usd == job.budget_min_usd:
        return f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"
