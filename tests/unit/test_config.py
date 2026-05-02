import os
import pytest
from scheduler.config import Settings, MissingEnvError


def test_settings_loads_from_environ(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("COMPOSIO_API_KEY", "comp-test")
    monkeypatch.setenv("COMPOSIO_USER_ID", "user-test")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "12345")
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", "67890")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    s = Settings.from_env()
    assert s.database_url == "postgresql://u:p@h:5432/d"
    assert s.discord_channel_id == 12345
    assert s.connects_daily_cap == 3
    assert s.connects_weekly_cap == 10


def test_settings_raises_on_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("COMPOSIO_API_KEY", "c")
    monkeypatch.setenv("COMPOSIO_USER_ID", "u")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "1")
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", "2")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://x")
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    with pytest.raises(MissingEnvError, match="DATABASE_URL"):
        Settings.from_env()


def test_settings_parses_optional_int_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("COMPOSIO_API_KEY", "comp-test")
    monkeypatch.setenv("COMPOSIO_USER_ID", "user-test")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "12345")
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", "67890")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("LOCAL_TIMEZONE", "America/Toronto")
    monkeypatch.setenv("CONNECTS_DAILY_CAP", "7")
    monkeypatch.setenv("CONNECTS_WEEKLY_CAP", "25")
    s = Settings.from_env()
    assert s.connects_daily_cap == 7
    assert s.connects_weekly_cap == 25
