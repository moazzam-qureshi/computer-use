"""Settings loaded from environment. Loaded once at startup; passed via constructors."""
from __future__ import annotations

import os
from dataclasses import dataclass


class MissingEnvError(RuntimeError):
    pass


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise MissingEnvError(f"Required env var {name} is not set")
    return val


def _int_with_default(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str
    composio_api_key: str
    composio_user_id: str
    discord_bot_token: str
    discord_channel_id: int
    discord_owner_user_id: int
    discord_webhook_url: str
    local_timezone: str
    connects_daily_cap: int
    connects_weekly_cap: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=_required("DATABASE_URL"),
            openai_api_key=_required("OPENAI_API_KEY"),
            composio_api_key=_required("COMPOSIO_API_KEY"),
            composio_user_id=_required("COMPOSIO_USER_ID"),
            discord_bot_token=_required("DISCORD_BOT_TOKEN"),
            discord_channel_id=int(_required("DISCORD_CHANNEL_ID")),
            discord_owner_user_id=int(_required("DISCORD_OWNER_USER_ID")),
            discord_webhook_url=_required("DISCORD_WEBHOOK_URL"),
            local_timezone=_required("LOCAL_TIMEZONE"),
            connects_daily_cap=_int_with_default("CONNECTS_DAILY_CAP", 3),
            connects_weekly_cap=_int_with_default("CONNECTS_WEEKLY_CAP", 10),
        )
