"""Top-level. Builds the graph, runs the Bidder loop + the Discord bot in parallel."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from dotenv import load_dotenv

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
from domain.humanization import Humanizer, default_envelope
from bidder.scan_cycle import run_one_cycle
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

    while True:
        now = datetime.now(timezone.utc)
        if not humanizer.is_active_now(now):
            interval = humanizer.sample_scan_interval(active=False)
            await asyncio.sleep(interval)
            continue

        async def on_signal(signal, order, job):
            setup = setups_store.get(signal.primary_setup_id)
            embed = build_signal_embed(
                setup_name=setup.name, tier=setup.tier, title=job.title,
                budget_text=f"{job.budget_kind} ${job.budget_min_usd or 0:.0f}",
                posted_text="recent", client_summary=job.client_country or "?",
                why_matched=", ".join([m["matched_rules"][0] if m.get("matched_rules") else "" for m in signal.matched_setups]),
                cover_letter_preview=order.cover_letter_body or "",
            )
            view = OrderApprovalView(order_id=order.order_id, doc_url=order.doc_url or "")
            await channel.send(embed=embed, view=view)

        def sync_on_signal(signal, order, job):
            asyncio.run_coroutine_threadsafe(on_signal(signal, order, job), bot.loop)

        try:
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
                on_signal=sync_on_signal,
            )
        except Exception as e:
            await channel.send(f"Bidder cycle failed: {e!r}")

        interval = humanizer.sample_scan_interval(active=True)
        await asyncio.sleep(interval)


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

    async def setup_hook():
        print("setup_hook fired; starting bidder + apply_executor loops", flush=True)
        bot.loop.create_task(bidder_loop(bot, settings, db, humanizer))
        bot.loop.create_task(apply_executor_loop(bot, settings, db, humanizer))

    bot.setup_hook = setup_hook

    print("Starting Discord gateway connection...", flush=True)
    await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
