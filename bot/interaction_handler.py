"""Wire button click events to Order state changes."""
from __future__ import annotations

import discord
from discord.ext import commands
from storage.connection import Database
from storage.orders import OrderStore
from scheduler.config import Settings


def register_views(bot: commands.Bot, db: Database, settings: Settings):
    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if ":" not in custom_id:
            return
        action, order_id_str = custom_id.split(":", 1)
        try:
            order_id = int(order_id_str)
        except ValueError:
            await interaction.response.send_message("Malformed interaction.", ephemeral=True)
            return
        store = OrderStore(db)

        if action == "apply":
            store.update_status(order_id, "approved")
            await interaction.response.send_message(
                f"Order {order_id} approved. Apply executor will pick it up.",
                ephemeral=True,
            )
        elif action == "skip":
            store.update_status(order_id, "cancelled")
            await interaction.response.send_message(
                f"Order {order_id} skipped.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
