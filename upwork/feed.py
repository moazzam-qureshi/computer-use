"""Feed scrolling and card discovery — extracted from the legacy upwork_driver.py.

Best-effort port of the substrate-driven feed primitives. The legacy
parse_feed_cards returned (JobInfo, hyperlink_element) tuples; here we expose
the smaller surface listed in the Phase-1 plan and return raw observe.Element
title hyperlinks. Detailed per-card field parsing remains in the legacy file
until a later phase chooses to port it.
"""
from __future__ import annotations

import time

from substrate import act, observe


def refresh_feed(window_title: str) -> None:
    """Press Ctrl+R, give the feed time to fully reload + hydrate."""
    act.focus_window(window_title)
    act.key("ctrl+r")
    time.sleep(6.0)


def click_most_recent_tab(window_title: str) -> None:
    """Click the 'Most Recent' tab. Retries up to 3 times because the first
    click can land before React binds its handler on a slow connection."""
    for attempt in range(1, 4):
        act.focus_window(window_title)
        obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=False)
        most_recent = next(
            (e for e in obs.elements
             if e.role in ("button", "listitem", "tabitem")
             and e.name.strip() == "Most Recent"),
            None,
        )
        if most_recent is None:
            time.sleep(2.0)
            continue
        act.click(most_recent)
        time.sleep(2.5)
        if attempt >= 2:
            break


def top_of_feed_cards(window_title: str, max_cards: int) -> list:
    """Scroll to the top of the feed and return up to `max_cards` job-title
    hyperlink elements (one per card). Caller can pass each into open_card_panel.

    Cards are detected by the 'Posted' text label that prefixes each card; the
    card's clickable title is the first long-name hyperlink inside the slice.
    """
    act.focus_window(window_title)
    act.key("ctrl+home")
    time.sleep(1.0)

    obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
    elements = obs.elements

    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and (e.name or "").strip() == "Posted"
    ]

    title_els = []
    for idx, start in enumerate(posted_indices):
        end = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        for e in elements[start:end]:
            if e.role == "hyperlink" and len(e.name or "") >= 20:
                n = (e.name or "").strip().lower()
                if n.startswith(("save job ", "view ", "more about ", "job feedback ")):
                    continue
                title_els.append(e)
                break
        if len(title_els) >= max_cards:
            break
    return title_els


def open_card_panel(card) -> bool:
    """Click the card's title hyperlink to open the right-side detail panel.

    Returns True if the click was issued. Callers should observe afterward to
    verify the 'Apply now' button is present (panel-open signal).
    """
    if card is None:
        return False
    try:
        act.click(card)
    except Exception:
        return False
    time.sleep(3.0)
    return True


def close_panel(window_title: str) -> None:
    """Press Esc to close the open detail panel. Cursor doesn't move."""
    act.focus_window(window_title)
    act.key("escape")
    time.sleep(0.5)
