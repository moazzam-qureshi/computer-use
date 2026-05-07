"""Unit tests: nudge engine pure-function helpers.

The full async process_new_findings is integration-tested
(tests/integration/researcher/test_nudge.py).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from researcher.nudge import _digest_due


def test_digest_due_when_never_sent():
    sysconfig = MagicMock()
    sysconfig.get.return_value = None
    assert _digest_due(sysconfig) is True


def test_digest_due_when_old():
    sysconfig = MagicMock()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    sysconfig.get.return_value = old
    assert _digest_due(sysconfig) is True


def test_digest_not_due_when_recent():
    sysconfig = MagicMock()
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    sysconfig.get.return_value = recent
    assert _digest_due(sysconfig) is False


def test_digest_due_when_unparseable_timestamp():
    """Defensive: if the system_config value is corrupted, treat as never sent
    rather than crashing."""
    sysconfig = MagicMock()
    sysconfig.get.return_value = "garbage-not-a-date"
    assert _digest_due(sysconfig) is True


def test_digest_due_when_naive_timestamp():
    """Older code might have written a naive (no-tz) iso string. Don't crash."""
    sysconfig = MagicMock()
    naive = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()
    sysconfig.get.return_value = naive
    # Naive treated as UTC; same instant as a tz-aware UTC value, so should
    # be NOT due (just sent).
    assert _digest_due(sysconfig) is False
