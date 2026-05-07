"""Unit tests: Researcher scheduler timing helpers.

The async fire loop has integration coverage in
tests/integration/researcher/test_scheduler.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from researcher.scheduler import (
    _last_fire, _next_fire_at, _should_catchup_on_startup,
)


def test_next_fire_at_today_when_before_hour():
    now = datetime(2026, 5, 7, 9, 30)  # 9:30am local
    target = _next_fire_at(now, hour_local=17)
    assert target == datetime(2026, 5, 7, 17, 0)


def test_next_fire_at_tomorrow_when_after_hour():
    now = datetime(2026, 5, 7, 19, 30)  # 7:30pm local — past the 5pm fire
    target = _next_fire_at(now, hour_local=17)
    assert target == datetime(2026, 5, 8, 17, 0)


def test_next_fire_at_tomorrow_when_exactly_on_hour():
    """If we're at exactly the fire-hour, the previous fire just happened
    (or should have). Schedule for tomorrow rather than firing immediately."""
    now = datetime(2026, 5, 7, 17, 0)
    target = _next_fire_at(now, hour_local=17)
    assert target == datetime(2026, 5, 8, 17, 0)


def test_last_fire_returns_none_when_unset():
    sysconfig = MagicMock()
    sysconfig.get.return_value = None
    assert _last_fire(sysconfig) is None


def test_last_fire_parses_iso():
    sysconfig = MagicMock()
    sysconfig.get.return_value = "2026-05-07T10:30:00+00:00"
    out = _last_fire(sysconfig)
    assert out == datetime(2026, 5, 7, 10, 30, tzinfo=timezone.utc)


def test_last_fire_handles_naive():
    """Older code might've written a naive timestamp. Treat as UTC."""
    sysconfig = MagicMock()
    sysconfig.get.return_value = "2026-05-07T10:30:00"
    out = _last_fire(sysconfig)
    assert out is not None
    assert out.tzinfo is not None  # we coerce to UTC


def test_last_fire_unparseable_returns_none():
    sysconfig = MagicMock()
    sysconfig.get.return_value = "garbage"
    assert _last_fire(sysconfig) is None


def test_catchup_when_never_fired():
    sysconfig = MagicMock()
    sysconfig.get.return_value = None
    assert _should_catchup_on_startup(sysconfig) is True


def test_catchup_when_old():
    sysconfig = MagicMock()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    sysconfig.get.return_value = old
    assert _should_catchup_on_startup(sysconfig) is True


def test_no_catchup_when_recent():
    sysconfig = MagicMock()
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    sysconfig.get.return_value = recent
    assert _should_catchup_on_startup(sysconfig) is False
