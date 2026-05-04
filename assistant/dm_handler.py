"""Discord DM glue: per-user lock, typing indicator, message split, error surface."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Dict

import discord

from storage.connection import Database
from assistant.agent import run_turn
from assistant.prompts import truncate_for_discord


_log = logging.getLogger(__name__)
_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def handle(message: discord.Message, *, db: Database) -> None:
    """Process a DM. Acquires per-user lock, runs agent in a thread, replies."""
    user_id = str(message.author.id)
    lock = _locks[user_id]
    async with lock:
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(
                    run_turn, db,
                    discord_user_id=user_id,
                    user_message=message.content,
                )
            except Exception as e:  # noqa: BLE001
                _log.exception("assistant turn failed")
                reply = _friendly_error(e)
        for chunk in truncate_for_discord(reply):
            await message.channel.send(chunk)


def _friendly_error(e: Exception) -> str:
    text = str(e).lower()
    if "connection" in text or "could not connect" in text:
        return "Database is unreachable; the bidder may also be down. Check `pm2 status`."
    if "openai" in text or "api" in text or "timed out" in text:
        return "OpenAI is unreachable, try again in a minute."
    if "recursion" in text:
        return "I got stuck mid-thought. Try rephrasing."
    return f"Something broke on my side: {e!s}"
