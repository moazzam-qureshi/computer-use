"""Humanization: timing distributions, voice variants, cycle mix decisions.

These tests use a seeded RNG so the distribution is checkable.
"""
import random
from datetime import datetime, timezone
from collections import Counter
from domain.humanization import (
    Humanizer,
    DiurnalEnvelope,
    default_envelope,
    CycleType,
)


def test_scan_interval_is_within_active_range():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    samples = [h.sample_scan_interval(active=True) for _ in range(1000)]
    assert all(120 <= s <= 720 for s in samples), "active scans should be 2-12 min"
    avg = sum(samples) / len(samples)
    assert 240 <= avg <= 480, f"avg {avg} should be ~5 min"


def test_scan_interval_off_hours_is_longer():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    samples = [h.sample_scan_interval(active=False) for _ in range(1000)]
    assert all(s >= 1800 for s in samples), "off-hours scans should be ≥30 min"


def test_cycle_type_mix_distribution():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    counts = Counter(h.sample_cycle_type() for _ in range(10000))
    total = sum(counts.values())
    # Allow ±3% slack
    assert 0.57 <= counts[CycleType.FULL_SCAN] / total <= 0.63
    assert 0.17 <= counts[CycleType.SKIM_ONLY] / total <= 0.23
    assert 0.07 <= counts[CycleType.PANEL_SKIM] / total <= 0.13


def test_signoff_variant_uses_all_three_over_many_calls():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    seen = {h.sample_signoff_variant("Moazzam") for _ in range(100)}
    assert len(seen) == 3


def test_envelope_active_during_workday_local_time():
    env = default_envelope()
    # 10 AM Wednesday should be 100% active
    assert env.activity_at(datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc).hour, weekday=2) == 1.0
    # 3 AM should be very low
    assert env.activity_at(3, weekday=2) <= 0.1


def test_should_take_diversion_cycle_returns_bool():
    h = Humanizer(rng=random.Random(42), envelope=default_envelope())
    decisions = [h.should_take_diversion_cycle(recent_diversions=0) for _ in range(1000)]
    rate = sum(decisions) / len(decisions)
    assert 0.03 <= rate <= 0.07, "diversion rate should be ~5%"
