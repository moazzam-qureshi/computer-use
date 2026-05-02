"""Connects budget enforcement."""
import pytest
from datetime import datetime, timedelta, timezone
from domain.risk import RiskCaps, OrderTimestamps, can_submit_order, RiskBlocked


def _now():
    return datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)  # Wednesday


def test_under_caps_allows():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=2, submitted_this_week=5, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is True


def test_daily_cap_blocks():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=3, submitted_this_week=5, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "daily" in decision.reason


def test_weekly_cap_blocks():
    caps = RiskCaps(daily=3, weekly=10)
    ts = OrderTimestamps(submitted_today=1, submitted_this_week=10, last_submission=None)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "weekly" in decision.reason


def test_min_gap_since_last_submission():
    caps = RiskCaps(daily=3, weekly=10, min_gap_seconds=30)
    last = _now() - timedelta(seconds=10)
    ts = OrderTimestamps(submitted_today=1, submitted_this_week=2, last_submission=last)
    decision = can_submit_order(ts, caps, _now())
    assert decision.allowed is False
    assert "gap" in decision.reason
    assert decision.retry_after_seconds is not None
    assert decision.retry_after_seconds >= 19  # ~20 left
