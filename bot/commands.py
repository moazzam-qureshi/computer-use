"""Phase 1 slash commands: /queue, /cancel, /applied, /connects, /health, /bidder."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

from storage.connection import Database
from storage.orders import OrderStore, OutcomeEventStore
from storage.bidder_state import BidderStateStore
from scheduler.config import Settings


def register_commands(bot: commands.Bot, db: Database, settings: Settings):

    @bot.tree.command(description="Show orders awaiting approval")
    async def queue(interaction: discord.Interaction):
        store = OrderStore(db)
        orders = store.list_by_status("awaiting_approval")
        if not orders:
            await interaction.response.send_message("Queue is empty.")
            return
        lines = [f"#{o.order_id}, setup={o.setup_id}, drafted at {o.drafted_at}" for o in orders]
        await interaction.response.send_message("Awaiting approval:\n" + "\n".join(lines))

    @bot.tree.command(description="Cancel an order before submission")
    @app_commands.describe(order_id="Order ID")
    async def cancel(interaction: discord.Interaction, order_id: int):
        store = OrderStore(db)
        order = store.get(order_id)
        if order is None:
            await interaction.response.send_message(f"Order {order_id} not found.", ephemeral=True)
            return
        if order.status in ("submitted",):
            await interaction.response.send_message(f"Order {order_id} already submitted, cannot cancel.", ephemeral=True)
            return
        store.update_status(order_id, "cancelled")
        await interaction.response.send_message(f"Cancelled order {order_id}.")

    @bot.tree.command(description="Record an outcome event for an order")
    @app_commands.describe(order_id="Order ID", event="One of: viewed, replied, interviewed, hired, declined, ghosted")
    async def applied(interaction: discord.Interaction, order_id: int, event: str):
        ALLOWED = {"viewed", "replied", "interviewed", "hired", "declined", "ghosted"}
        if event not in ALLOWED:
            await interaction.response.send_message(f"event must be one of {ALLOWED}", ephemeral=True)
            return
        oe = OutcomeEventStore(db)
        oe.record(order_id=order_id, event_type=event, source="discord_manual", notes=None)
        await interaction.response.send_message(f"Recorded {event} for order {order_id}.")

    @bot.tree.command(description="Show this week's Connects spend vs cap")
    async def connects(interaction: discord.Interaction):
        store = OrderStore(db)
        now = datetime.now(timezone.utc)
        today_count = store.count_submitted_today(now)
        week_count = store.count_submitted_this_week(now)
        await interaction.response.send_message(
            f"Today: {today_count}/{settings.connects_daily_cap}  |  "
            f"This week: {week_count}/{settings.connects_weekly_cap}"
        )

    @bot.tree.command(description="System health: scheduler tick, DB, OpenAI quota")
    async def health(interaction: discord.Interaction):
        try:
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            await interaction.response.send_message("DB reachable. Scheduler status: see logs.")
        except Exception as e:
            await interaction.response.send_message(f"DB error: {e}")

    # /bidder command group: runtime control of the scan loop without
    # restarting the service. All state lives in the bidder_state singleton
    # row in Postgres; the bidder loop polls it every iteration.
    bidder_group = app_commands.Group(name="bidder", description="Bidder control")

    def _fmt_dt(dt) -> str:
        if dt is None:
            return "never"
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    @bidder_group.command(name="status", description="Current bidder state + last cycle info")
    async def bidder_status(interaction: discord.Interaction):
        store = BidderStateStore(db)
        s = store.get()
        order_store = OrderStore(db)
        now = datetime.now(timezone.utc)
        today = order_store.count_submitted_today(now)
        week = order_store.count_submitted_this_week(now)

        state_word = "PAUSED" if s.paused else "RUNNING"
        if s.paused and s.paused_reason:
            state_word += f" ({s.paused_reason})"
        force_pending = " (force-run pending)" if s.force_run_requested else ""

        embed = discord.Embed(
            title=f"Bidder: {state_word}{force_pending}",
            color=0xFFA500 if s.paused else 0x00C853,
        )
        embed.add_field(
            name="Last cycle",
            value=(
                f"started: {_fmt_dt(s.last_cycle_started_at)}\n"
                f"finished: {_fmt_dt(s.last_cycle_finished_at)}\n"
                f"status: {s.last_cycle_status or 'n/a'}\n"
                f"notes: {s.last_cycle_notes or 'n/a'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Connects budget",
            value=f"today: {today}/{settings.connects_daily_cap}  |  week: {week}/{settings.connects_weekly_cap}",
            inline=False,
        )
        if s.paused_at is not None:
            embed.add_field(name="Paused at", value=_fmt_dt(s.paused_at), inline=False)

        await interaction.response.send_message(embed=embed)

    @bidder_group.command(name="pause", description="Pause the bidder. Cycles will skip until /bidder resume.")
    @app_commands.describe(reason="Optional reason shown in /bidder status")
    async def bidder_pause(interaction: discord.Interaction, reason: str = ""):
        store = BidderStateStore(db)
        store.pause(reason=reason or None)
        msg = "Bidder paused."
        if reason:
            msg += f" Reason: {reason}"
        msg += " Use /bidder resume to start cycling again."
        await interaction.response.send_message(msg)

    @bidder_group.command(name="resume", description="Resume the bidder.")
    async def bidder_resume(interaction: discord.Interaction):
        store = BidderStateStore(db)
        store.resume()
        await interaction.response.send_message("Bidder resumed. Next cycle will fire on the normal schedule.")

    @bidder_group.command(name="run-now", description="Force a scan cycle immediately, bypassing the diurnal envelope.")
    async def bidder_run_now(interaction: discord.Interaction):
        store = BidderStateStore(db)
        store.request_force_run()
        await interaction.response.send_message(
            "Force-run requested. Bidder will pick this up on its next poll iteration "
            "(within ~20s if paused, or after the current sleep otherwise)."
        )

    @bidder_group.command(name="logs", description="Tail the last N lines of the scheduler log")
    @app_commands.describe(lines="Lines to tail (default 40, max 200)")
    async def bidder_logs(interaction: discord.Interaction, lines: int = 40):
        lines = max(1, min(lines, 200))
        # Look for PM2's log files in the project's logs/ directory. Falls
        # back to ~/.pm2/logs/ if the project log isn't there. If neither
        # exists, tell the operator where to look.
        candidates = [
            Path(__file__).parent.parent / "logs" / "scheduler-out.log",
            Path(__file__).parent.parent / "logs" / "scheduler.log",
            Path.home() / ".pm2" / "logs" / "scheduler-out.log",
        ]
        log_path = next((p for p in candidates if p.exists()), None)
        if log_path is None:
            await interaction.response.send_message(
                "No log file found. PM2 may not be writing to the expected paths. "
                f"Checked: {[str(p) for p in candidates]}",
                ephemeral=True,
            )
            return
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-lines:]
        except Exception as e:
            await interaction.response.send_message(f"Could not read log: {e!r}", ephemeral=True)
            return
        body = "".join(tail)
        # Discord message limit is 2000 chars; trim from the front (older lines)
        # so the most recent activity is visible.
        if len(body) > 1900:
            body = "...(truncated)\n" + body[-1900:]
        await interaction.response.send_message(f"```\n{body}\n```")

    bot.tree.add_command(bidder_group)
