"""Phase 1 slash commands: /queue, /cancel, /applied, /connects, /health."""
from __future__ import annotations

from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

from storage.connection import Database
from storage.orders import OrderStore, OutcomeEventStore
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
