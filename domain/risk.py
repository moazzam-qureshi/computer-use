"""Connects budget enforcement. Pure logic — caller passes in current timestamps; risk decides."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class RiskCaps:
    daily: int
    weekly: int
    min_gap_seconds: int = 30


@dataclass
class OrderTimestamps:
    submitted_today: int
    submitted_this_week: int
    last_submission: Optional[datetime]


@dataclass
class RiskDecision:
    allowed: bool
    reason: Optional[str] = None
    retry_after_seconds: Optional[int] = None


class RiskBlocked(Exception):
    pass


def can_submit_order(ts: OrderTimestamps, caps: RiskCaps, now: datetime) -> RiskDecision:
    if ts.submitted_today >= caps.daily:
        return RiskDecision(False, reason=f"daily cap reached ({caps.daily})")
    if ts.submitted_this_week >= caps.weekly:
        return RiskDecision(False, reason=f"weekly cap reached ({caps.weekly})")
    if ts.last_submission is not None:
        elapsed = (now - ts.last_submission).total_seconds()
        if elapsed < caps.min_gap_seconds:
            remaining = int(caps.min_gap_seconds - elapsed)
            return RiskDecision(False, reason=f"min-gap not yet elapsed", retry_after_seconds=remaining)
    return RiskDecision(True)
