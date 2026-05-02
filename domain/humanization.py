"""Humanization: timing distributions and behavioral mix decisions.

Pure logic, deterministic when seeded. Consumed by scheduler, bidder, apply executor.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CycleType(str, Enum):
    FULL_SCAN = "full_scan"
    SKIM_ONLY = "skim_only"
    PANEL_SKIM = "panel_skim"
    SIDE_TRIP = "side_trip"
    NO_OP = "no_op"


_CYCLE_WEIGHTS = {
    CycleType.FULL_SCAN: 0.60,
    CycleType.SKIM_ONLY: 0.20,
    CycleType.PANEL_SKIM: 0.10,
    CycleType.SIDE_TRIP: 0.05,
    CycleType.NO_OP: 0.05,
}


@dataclass
class DiurnalEnvelope:
    """Hour-of-day -> activity multiplier (0..1). Weekend dampening factor applied separately."""
    by_hour: dict[int, float]
    weekend_multiplier: float = 0.6

    def activity_at(self, hour: int, weekday: int) -> float:
        base = self.by_hour.get(hour, 0.5)
        if weekday >= 5:  # Saturday=5, Sunday=6
            return base * self.weekend_multiplier
        return base


def default_envelope() -> DiurnalEnvelope:
    return DiurnalEnvelope(by_hour={
        0: 0.05, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.05, 6: 0.05,
        7: 0.40, 8: 0.60,
        9: 1.00, 10: 1.00, 11: 1.00,
        12: 0.40, 13: 0.40,
        14: 1.00, 15: 1.00, 16: 1.00, 17: 1.00,
        18: 0.70, 19: 0.70, 20: 0.70, 21: 0.70,
        22: 0.30, 23: 0.30,
    })


@dataclass
class Humanizer:
    rng: random.Random = field(default_factory=lambda: random.Random())
    envelope: DiurnalEnvelope = field(default_factory=default_envelope)

    def sample_scan_interval(self, active: bool) -> int:
        """Seconds until the next scheduler tick should fire."""
        if active:
            # Bimodal: 80% 240-480s focused, 15% 120-240s eager, 5% 480-720s distracted.
            r = self.rng.random()
            if r < 0.80:
                return self.rng.randint(240, 480)
            elif r < 0.95:
                return self.rng.randint(120, 240)
            else:
                return self.rng.randint(480, 720)
        else:
            # Off-hours: 30 min to 4 hours
            return self.rng.randint(1800, 14400)

    def sample_panel_dwell(self) -> float:
        """Seconds spent 'reading' a panel before scoring/closing."""
        return self.rng.uniform(4.0, 15.0)

    def sample_review_pause(self, text_length: int) -> float:
        """Seconds to 'review' a pasted block of text."""
        base = max(3.0, min(15.0, text_length / 80))
        return base + self.rng.uniform(-1.5, 4.0)

    def sample_apply_form_pauses(self) -> dict[str, float]:
        return {
            "initial_idle": self.rng.uniform(8.0, 30.0),
            "after_paste": self.rng.uniform(5.0, 15.0),
            "before_screening": self.rng.uniform(5.0, 15.0),
            "final_review": self.rng.uniform(10.0, 20.0),
        }

    def sample_signoff_variant(self, name: str) -> str:
        choices = [f"- {name}", f"Cheers, {name}", f"Best, {name}"]
        return self.rng.choice(choices)

    def sample_greeting_variant(self, client_name: Optional[str]) -> Optional[str]:
        if not client_name:
            return None
        choices = [f"Hey {client_name},", f"Hi {client_name},", None]
        return self.rng.choice(choices)

    def sample_cycle_type(self) -> CycleType:
        types = list(_CYCLE_WEIGHTS.keys())
        weights = [_CYCLE_WEIGHTS[t] for t in types]
        return self.rng.choices(types, weights=weights, k=1)[0]

    def should_take_diversion_cycle(self, recent_diversions: int) -> bool:
        """5% baseline; reduce if we just did several."""
        base = 0.05
        adj = max(0.01, base - recent_diversions * 0.02)
        return self.rng.random() < adj

    def should_abandon_apply(self) -> bool:
        return self.rng.random() < 0.05

    def is_active_now(self, now: datetime) -> bool:
        """Roll against the diurnal envelope."""
        local_hour = now.hour
        weekday = now.weekday()
        activity = self.envelope.activity_at(local_hour, weekday)
        return self.rng.random() < activity
