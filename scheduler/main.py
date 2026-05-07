"""Top-level. Builds the graph, runs the Bidder loop + the Discord bot in parallel."""
from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv
import discord

# Force UTF-8 on stdout/stderr. PM2 on Windows captures the child process's
# stdout with the default cp1252 codec, so any print() containing non-ASCII
# characters (em-dashes, smart quotes, accented characters in scraped Upwork
# job descriptions) crashes with UnicodeEncodeError. The error gets caught
# by scan_cycle's outer try/except and the job is silently skipped, which
# is what was happening when "scan ran but no Discord notifications" --
# every job whose panel had a smart-quote or em-dash never made it past
# the print, never reached upsert/relevance/draft.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("discord").setLevel(logging.DEBUG)
logging.getLogger("discord.http").setLevel(logging.INFO)
logging.getLogger("discord.gateway").setLevel(logging.DEBUG)

from substrate import act as _act
from scheduler.config import Settings
from storage.connection import Database

# Allow input primitives when ANY Chrome window is foreground. This covers
# the brief moment between Ctrl+T (new tab title is 'New Tab - Google Chrome')
# and the page finishing navigation to Upwork.
_act.set_target_window(("Upwork", "Google Chrome"))
from storage.jobs import JobStore
from storage.setups import SetupStore, SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.scrape_runs import ScrapeRunStore
from storage.connects_ledger import ConnectsLedgerStore
from storage.bidder_state import BidderStateStore
from storage.conversations import SystemConfigStore
from storage.scan_briefs import BriefStore
from domain.humanization import Humanizer, default_envelope
from bidder.scan_cycle import run_one_cycle
from bidder.sniper_loop import run_sniper_loop
from bidder.apply_executor import execute_approved_order
from bot.bot import build_bot
from bot.commands import register_commands
from bot.interaction_handler import register_views
from bot.alerts import build_signal_embed, OrderApprovalView


def _with_com(fn, *args, **kwargs):
    """Run a UIAutomation-using callable on a worker thread with COM initialized.

    asyncio.to_thread spawns workers without CoInitialize, which UIA requires
    per-thread on Windows. Wrap the call site rather than every primitive.
    """
    import uiautomation as ua
    with ua.UIAutomationInitializerInThread():
        return fn(*args, **kwargs)


# Shared lock both loops acquire before driving Chrome. The bidder and the
# apply executor both send keystrokes / clicks / observations to the same
# Chrome window; if they fire concurrently they fight over focus and corrupt
# each other's state. Whichever loop acquires the lock runs to completion;
# the other awaits. Acquired in scheduler.main only -- standalone harnesses
# (bin/run_one_scan.py, bin/run_apply_executor.py) don't need it because
# they run a single loop.
ui_lock: asyncio.Lock = asyncio.Lock()


async def bidder_loop(bot, settings: Settings, db: Database, humanizer: Humanizer):
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)

    setups_store = SetupStore(db)
    signal_store = SignalStore(db)
    job_store = JobStore(db)
    order_store = OrderStore(db)
    enrichment_store = EnrichmentStore(db)
    portfolio_store = PortfolioStore(db)
    agent_runs = AgentRunStore(db)
    scrape_runs = ScrapeRunStore(db)
    bidder_state = BidderStateStore(db)
    brief_store = BriefStore(db)

    # on_signal is invoked by the bidder when a job matches a setup. For
    # briefed scans we suppress the channel embed; the brief-watcher DMs
    # the operator a summary instead.
    async def on_signal(signal, order, job):
        if (signal.market_state or {}).get("source") == "briefed_scan":
            print(f"[bidder] suppressing channel post for briefed-scan signal_id={signal.signal_id}", flush=True)
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
            posted_text=job.posted_text or "recent", client_summary=job.client_country or "?",
            why_matched=", ".join([m["matched_rules"][0] if m.get("matched_rules") else "" for m in signal.matched_setups]),
            cover_letter_preview=order.cover_letter_body or "",
            application_flags=application_flags,
        )
        view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
        await channel.send(embed=embed, view=view)

    def sync_on_signal(signal, order, job):
        asyncio.run_coroutine_threadsafe(on_signal(signal, order, job), bot.loop)

    first_cycle = True
    # Inter-cycle deadline. When the loop decides "no cycle yet," it picks a
    # target wake time and short-polls until reaching it, breaking early if a
    # force-run is requested or the pause flag flips. Without short-polling,
    # /bidder run-now would sit unread for up to 4 hours during the off-hours
    # sleep window.
    next_cycle_at: Optional[datetime] = None

    while True:
        now = datetime.now(timezone.utc)

        # Pull operator-tunable pacing budget from system_config. Cheap
        # (single key lookup); applies on the next action via the live
        # pacer's mutable config.
        from substrate import pacing as _pacing
        _pacing.apply_from_sysconfig(SystemConfigStore(db))

        # 1. Always check for force-run first. /bidder run-now should fire
        #    within ~30s (the polling interval), regardless of pause / sleep
        #    window state.
        force_run = bidder_state.consume_force_run()

        # 2. Pause check. /bidder pause sets the flag; bidder loop short-
        #    polls every 30s waiting for resume. Force-run breaks past pause.
        state = bidder_state.get()
        if state.paused and not force_run:
            await asyncio.sleep(30)
            continue

        # 3. Diurnal envelope + interval gating. First cycle and force-run
        #    bypass both. Otherwise: if we have a pending wake-time and we
        #    haven't reached it, short-poll for 30s and re-check force-run.
        if not first_cycle and not force_run:
            if next_cycle_at is not None and now < next_cycle_at:
                await asyncio.sleep(30)
                continue
            # Time to roll a fresh interval based on whether the envelope
            # considers us active right now.
            if not humanizer.is_active_now(now):
                interval = humanizer.sample_scan_interval(active=False)
                next_cycle_at = now + timedelta(seconds=interval)
                print(f"[bidder] off-hours sleep, next cycle at {next_cycle_at.isoformat()}", flush=True)
                await asyncio.sleep(30)
                continue

        first_cycle = False
        next_cycle_at = None

        # 4. Chrome health check. The bidder needs a Chrome window with the
        #    --force-renderer-accessibility flag and a matching window title
        #    ('Upwork' / 'Google Chrome'). If the operator closed Chrome, or
        #    Chrome crashed, or this is a fresh process where the one-shot
        #    chrome PM2 launcher hasn't run yet, relaunch it before scanning.
        #    Runs in a worker thread because UIA + subprocess.Popen aren't
        #    safe to call from the asyncio event loop directly.
        from substrate.launch_chrome import ensure_chrome_running
        ok = await asyncio.to_thread(_with_com, ensure_chrome_running)
        if not ok:
            print("[bidder] Chrome is not available; skipping this cycle", flush=True)
            bidder_state.record_cycle_finish("failed", "chrome_unavailable")
            await channel.send(
                f"<@{settings.discord_owner_user_id}> Chrome could not be launched. "
                f"Tried to relaunch automatically but the window did not appear."
                if settings.discord_owner_user_id else
                "Chrome could not be launched. Tried to relaunch automatically but the window did not appear."
            )
            await asyncio.sleep(60)
            continue

        # Briefed scan: agent-triggered, async. Consume one pending brief at the
        # top of the work-doing portion of the loop. If one is pending, it
        # preempts the scheduled cycle and runs an ephemeral cycle against
        # the brief's filter_dsl. The brief-watcher DMs the operator the
        # result summary; the channel webhook is suppressed via on_signal.
        pending_brief = brief_store.consume_pending()
        if pending_brief is not None:
            print(f"[bidder] consuming brief brief_id={pending_brief.brief_id}", flush=True)
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
                        sysconfig=SystemConfigStore(db),
                        on_signal=sync_on_signal,
                        brief=pending_brief,
                    )
                # Tally results from the brief's persisted setup.
                with db.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT count(*) FROM orders WHERE setup_id = (SELECT setup_id FROM scan_briefs WHERE brief_id = %s)",
                            (pending_brief.brief_id,),
                        )
                        drafts = cur.fetchone()[0]
                brief_store.mark_done(
                    pending_brief.brief_id,
                    result_summary={"drafts_created": drafts},
                    cycle_notes="ok",
                )
                print(f"[bidder] brief brief_id={pending_brief.brief_id} done; drafts={drafts}", flush=True)
            except Exception as e:
                print(f"[bidder] brief brief_id={pending_brief.brief_id} failed: {e!r}", flush=True)
                brief_store.mark_failed(pending_brief.brief_id, error=repr(e)[:500])
            # Skip the scheduled-cycle path this iteration; loop and re-check.
            continue

        bidder_state.record_cycle_start()

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
                    sysconfig=SystemConfigStore(db),
                    on_signal=sync_on_signal,
                )
            bidder_state.record_cycle_finish("succeeded")
        except Exception as e:
            # Distinguish login from other failures so the message is actionable.
            from scheduler.failure_pings import LoginExpired
            owner_mention = f"<@{settings.discord_owner_user_id}> " if settings.discord_owner_user_id else ""
            if isinstance(e, LoginExpired):
                bidder_state.record_cycle_finish("login_required", "Upwork session expired")
                await channel.send(
                    f"{owner_mention}Upwork login required. Open the Chrome tab and sign in. "
                    f"The bidder will resume on the next cycle."
                )
            else:
                bidder_state.record_cycle_finish("failed", repr(e)[:500])
                await channel.send(f"Bidder cycle failed: {e!r}")

        # Set the wake-time and let the top of the loop short-poll until then
        # (so /bidder run-now or /bidder pause take effect within ~30s instead
        # of the full inter-cycle interval).
        interval = humanizer.sample_scan_interval(active=True)
        next_cycle_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
        print(f"[bidder] cycle done, next scheduled at {next_cycle_at.isoformat()}", flush=True)


async def apply_executor_loop(bot, settings: Settings, db: Database, humanizer: Humanizer):
    """Polls for approved orders and runs them."""
    await bot.wait_until_ready()
    channel = bot.get_channel(settings.discord_channel_id)
    order_store = OrderStore(db)
    connects = ConnectsLedgerStore(db)
    job_store = JobStore(db)

    while True:
        approved = order_store.list_by_status("approved")
        for order in approved:
            job = job_store.get(order.job_id)
            try:
                async with ui_lock:
                    await asyncio.to_thread(
                        _with_com,
                        execute_approved_order,
                        order, job.url,
                        order_store=order_store, connects_ledger=connects,
                        humanizer=humanizer, bid_amount_usd=order.bid_amount_usd or 100.0,
                        really_submit=False,
                    )
                await channel.send(f"Order #{order.order_id} staged on apply page. Click Submit manually.")
            except Exception as e:
                await channel.send(f"Apply executor failed for order #{order.order_id}: {e!r}")
        await asyncio.sleep(20)


async def main():
    print("Loading settings...", flush=True)
    settings = Settings.from_env()
    print(f"Connecting to Postgres at {settings.database_url.split('@')[-1]}", flush=True)
    db = Database(settings.database_url)
    humanizer = Humanizer(rng=random.Random(), envelope=default_envelope())

    bot = build_bot(settings)
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    @bot.event
    async def on_ready():
        print(f"Bot connected as {bot.user}", flush=True)
        channel = bot.get_channel(settings.discord_channel_id)
        if channel is None or channel.guild is None:
            print("WARNING: configured channel not visible to bot; falling back to global sync", flush=True)
            synced = await bot.tree.sync()
        else:
            guild = channel.guild
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash commands to guild '{guild.name}' ({guild.id})", flush=True)

    @bot.event
    async def on_message(message):
        # Ignore self.
        if message.author == bot.user:
            return
        # DM only.
        if not isinstance(message.channel, discord.DMChannel):
            return
        # Only the configured operator.
        if int(message.author.id) != settings.discord_owner_user_id:
            return
        print(f"[assistant] DM from {message.author} ({message.author.id}): {message.content!r}", flush=True)
        from assistant.dm_handler import handle
        await handle(message, db=db)

    async def setup_hook():
        print("setup_hook fired; starting sniper + apply_executor + brief_watcher + researcher loops", flush=True)
        bot.loop.create_task(run_sniper_loop(bot, settings, db, humanizer, ui_lock))
        bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))
        from assistant.brief_watcher import run_brief_watcher
        bot.loop.create_task(run_brief_watcher(db, bot, settings))
        from researcher.scheduler import run_researcher_scheduler
        bot.loop.create_task(run_researcher_scheduler(bot, settings, db, ui_lock))

    bot.setup_hook = setup_hook

    print("Starting Discord gateway connection...", flush=True)
    await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
