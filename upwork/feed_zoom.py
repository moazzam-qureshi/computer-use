"""Zoom helpers matching bin/debug_zoom_extreme.py verbatim.

Validated empirically at 33% zoom: single UIA walk gets all 9-10 feed cards,
no Down×21 dance needed. Panel-walk + Copy-button click still work.

CRITICAL: every zoom call resets to 100% FIRST (Ctrl+0), then presses Ctrl+-
N times. Without the reset, repeated calls stack — if the previous cycle
ended at 33% and we press Ctrl+- ×6 again, we end up at ~10% which breaks
clicks and panel walks. The reset-then-zoom sequence in debug_zoom_extreme.py
is the only configuration empirically validated.
"""
from __future__ import annotations

import time

import pyautogui


_ZOOM_OUT_PRESSES = 6   # 33% zoom — diagnostic validated. Do not change.
_INTER_PRESS_SLEEP = 0.18
_RESET_SETTLE = 0.4
_ZOOM_SETTLE = 0.8


def zoom_to_33pct() -> None:
    """Reset to 100% via Ctrl+0, then Ctrl+- ×6 to land at 33%.
    Caller must have focused Chrome before invoking. Sequence verbatim
    from bin/debug_zoom_extreme.py:_try_zoom_level.
    """
    pyautogui.hotkey("ctrl", "0")
    time.sleep(_RESET_SETTLE)
    for _ in range(_ZOOM_OUT_PRESSES):
        pyautogui.hotkey("ctrl", "-")
        time.sleep(_INTER_PRESS_SLEEP)
    time.sleep(_ZOOM_SETTLE)


# Backwards-compat alias for callers using the older name.
zoom_to_diagnostic = zoom_to_33pct


def reset_zoom() -> None:
    """Press Ctrl+0 to reset Chrome zoom to 100%. Caller must have focused
    the Chrome window before invoking."""
    pyautogui.hotkey("ctrl", "0")
    time.sleep(0.3)
