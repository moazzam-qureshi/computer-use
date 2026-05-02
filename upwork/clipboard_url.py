"""Click-Copy-to-clipboard URL capture — extracted from the legacy upwork_driver.py.

Ported from upwork_driver.capture_url_via_clipboard. Preserves the bounds
validation: button must be in the right column (x >= 1200) and not too close
to the panel top (y >= 200), to avoid mis-clicking 'Open job in a new window'.
Click is dead-center (no jitter) because the button is small (~76x26 px).
"""
from __future__ import annotations

import re
import time
from typing import Optional

import pyperclip

from substrate import act, observe

URL_RE = re.compile(r"https?://www\.upwork\.com/jobs/~[0-9a-f]+")


def capture_url_from_open_panel(window_title: str, max_scrolls: int = 6) -> Optional[str]:
    """Find the 'Copy to clipboard' button in an already-open panel, click it,
    return the clipboard text if it matches the canonical Upwork job URL shape.
    Returns None on any validation failure or if no URL appears.
    """
    sentinel = "__driver_sentinel_no_url_yet__"
    try:
        pyperclip.copy(sentinel)
    except Exception:
        pass

    for attempt in range(max_scrolls + 1):
        obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=False)
        copy_btns = [
            e for e in obs.elements
            if e.role == "button" and (e.name or "").strip() == "Copy to clipboard"
        ]
        if copy_btns:
            btn = copy_btns[0]
            l, t, r, b = btn.bounds

            # Bounds validation — see module docstring.
            if l < 0 or t < 0 or r <= l or b <= t or r > 5000 or b > 5000:
                return None
            if t < 200:
                return None
            if l < 1200:
                return None

            act.focus_window(window_title)
            time.sleep(0.2)
            cx, cy = btn.center
            act.click_xy(cx, cy)
            time.sleep(0.6)
            try:
                clip = pyperclip.paste().strip()
            except Exception:
                clip = ""
            if clip and clip != sentinel:
                m = URL_RE.search(clip)
                if m:
                    return m.group(0)
            return None

        if attempt < max_scrolls:
            act.focus_window(window_title)
            act.scroll(1, method="key")
            time.sleep(0.3)

    return None
