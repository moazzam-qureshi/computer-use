"""
Human pacing for the agent.

What this does:
- Enforces a max-actions-per-hour budget across a session.
- Inserts bimodal per-action delays: mostly fast, occasionally medium, rarely long.
- Inserts reading-time pauses after view/diff calls based on content length.
- Adds click-position jitter so we don't always hit element centers.

What this is NOT:
- A "stealth library" that mimics mouse biometrics. Those are theatre.
- A guarantee against detection. It just removes the easy bot signals
  (uniform timing, infinite stamina, no reading pauses).

Use one Pacer per agent run.
"""
from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class PacingConfig:
    max_actions_per_hour: int = 500        # session-level rate limit (raised from 40 for active hunting; one full scan ~= 80-150 actions)
    fast_range: tuple[float, float] = (0.05, 0.20)   # 80% of actions
    medium_range: tuple[float, float] = (0.5, 2.0)   # 15% of actions
    long_range: tuple[float, float] = (3.0, 12.0)    # 5% of actions ("looked away")
    fast_pct: float = 0.80
    medium_pct: float = 0.15
    # reading pause params (after view/diff)
    chars_per_second_read: float = 80.0
    min_read_pause: float = 1.5
    max_read_pause: float = 8.0
    # click jitter as fraction of element half-extent
    click_jitter_pct: float = 0.18


class Pacer:
    def __init__(self, config: PacingConfig | None = None):
        self.cfg = config or PacingConfig()
        self._action_times: deque[float] = deque()  # epoch seconds of recent actions

    # ---- session budget ----

    def _trim_old_actions(self) -> None:
        cutoff = time.time() - 3600
        while self._action_times and self._action_times[0] < cutoff:
            self._action_times.popleft()

    def _budget_wait(self) -> float:
        """If we've used the hourly budget, wait until the oldest action ages out."""
        self._trim_old_actions()
        if len(self._action_times) < self.cfg.max_actions_per_hour:
            return 0.0
        oldest = self._action_times[0]
        wait = max(0.0, (oldest + 3600) - time.time())
        # Add a fudge so we don't all start exactly when budget reopens
        return wait + random.uniform(0.5, 3.0)

    # ---- per-action delay ----

    def _bimodal_delay(self) -> float:
        r = random.random()
        if r < self.cfg.fast_pct:
            return random.uniform(*self.cfg.fast_range)
        elif r < self.cfg.fast_pct + self.cfg.medium_pct:
            return random.uniform(*self.cfg.medium_range)
        else:
            return random.uniform(*self.cfg.long_range)

    # ---- public API ----

    def before_action(self, kind: str = "click") -> None:
        """Block until it's reasonable for a human to perform another action."""
        budget_wait = self._budget_wait()
        if budget_wait > 0:
            print(f"[pacer] hourly budget reached, sleeping {budget_wait:.0f}s", flush=True)
            time.sleep(budget_wait)
        delay = self._bimodal_delay()
        time.sleep(delay)
        self._action_times.append(time.time())

    def after_view(self, content_length: int) -> None:
        """Pause to simulate reading after observing the screen.

        Length is in characters of the rendered view text. Pause scales linearly
        until min/max bounds.
        """
        if content_length <= 0:
            return
        seconds = content_length / max(1.0, self.cfg.chars_per_second_read)
        seconds = max(self.cfg.min_read_pause, min(self.cfg.max_read_pause, seconds))
        # add some randomness so it's not deterministic per length
        seconds *= random.uniform(0.7, 1.15)
        time.sleep(seconds)

    def jitter_click_target(self, bounds: tuple[int, int, int, int]) -> tuple[int, int]:
        """Pick a click point inside `bounds`, biased toward center but not on it."""
        l, t, r, b = bounds
        cx = (l + r) / 2
        cy = (t + b) / 2
        half_w = (r - l) / 2
        half_h = (b - t) / 2
        jx = random.uniform(-self.cfg.click_jitter_pct, self.cfg.click_jitter_pct) * half_w
        jy = random.uniform(-self.cfg.click_jitter_pct, self.cfg.click_jitter_pct) * half_h
        return (int(cx + jx), int(cy + jy))

    # ---- session info ----

    def stats(self) -> dict:
        self._trim_old_actions()
        return {
            "actions_last_hour": len(self._action_times),
            "budget": self.cfg.max_actions_per_hour,
        }


# Module-level default pacer. The agent gets one per session.
_default: Pacer | None = None


def get_pacer() -> Pacer:
    global _default
    if _default is None:
        _default = Pacer()
    return _default


def configure(config: PacingConfig) -> None:
    """Replace the default pacer with one using `config`. Call once at startup."""
    global _default
    _default = Pacer(config)


def set_max_actions_per_hour(per_hour: int) -> int:
    """Mutate the live pacer's hourly budget without losing action history.

    Called by scheduler/loops to pick up operator-requested changes from
    system_config. Returns the value applied (clamped to >=1).
    """
    if per_hour < 1:
        per_hour = 1
    pacer = get_pacer()
    pacer.cfg.max_actions_per_hour = int(per_hour)
    return per_hour


def apply_from_sysconfig(sysconfig) -> None:
    """Read 'pacing_budget_per_hour' from system_config and apply if set.

    Loops call this once per cycle (cheap — single key lookup). If the
    operator changes the budget via the assistant tool, the next cycle
    picks it up. If the key is unset, the existing pacer config stays.
    """
    raw = sysconfig.get("pacing_budget_per_hour")
    if raw is None:
        return
    try:
        per_hour = int(raw)
        if per_hour > 0:
            set_max_actions_per_hour(per_hour)
    except (ValueError, TypeError):
        pass  # malformed sysconfig value; ignore
