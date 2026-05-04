"""Discord bot entry point. Builds the bot, registers commands, runs gateway."""
from __future__ import annotations

import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from scheduler.config import Settings
from storage.connection import Database


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    return bot


async def run_bot(settings: Settings, db: Database, on_ready):
    bot = build_bot(settings)
    on_ready_callback = on_ready

    @bot.event
    async def on_ready():
        print(f"Bot connected as {bot.user}", flush=True)
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands", flush=True)
        await on_ready_callback(bot)

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
        from assistant.dm_handler import handle
        await handle(message, db=db)
        # Do NOT process commands here; slash commands run via the tree, and
        # we have no prefix commands in this bot.

    from bot.commands import register_commands
    from bot.interaction_handler import register_views
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    await bot.start(settings.discord_bot_token)
