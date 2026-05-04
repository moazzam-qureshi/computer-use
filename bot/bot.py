"""Discord bot factory. The actual gateway start lives in scheduler.main, which
also wires the on_message DM handler beside the bidder + apply_executor loops."""
from __future__ import annotations

import discord
from discord.ext import commands

from scheduler.config import Settings


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    return bot
