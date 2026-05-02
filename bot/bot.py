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
        print(f"Bot connected as {bot.user}")
        await bot.tree.sync()
        await on_ready_callback(bot)

    from bot.commands import register_commands
    from bot.interaction_handler import register_views
    register_commands(bot, db, settings)
    register_views(bot, db, settings)

    await bot.start(settings.discord_bot_token)
