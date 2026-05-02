"""Feed scrolling and card discovery — extracted from the legacy upwork_driver.py.

Best-effort port of the substrate-driven feed primitives. The legacy
parse_feed_cards returned (JobInfo, hyperlink_element) tuples; here we expose
the smaller surface listed in the Phase-1 plan and return raw observe.Element
title hyperlinks. Detailed per-card field parsing remains in the legacy file
until a later phase chooses to port it.
"""
from __future__ import annotations

import re
import time

from substrate import act, observe


MOST_RECENT_URL = "https://www.upwork.com/nx/find-work/most-recent"
CHROME_WINDOW = "Google Chrome"


def refresh_feed(window_title: str) -> None:
    """Open a fresh Chrome tab on the Upwork most-recent feed.

    Doesn't depend on which tab was foreground when the cycle started:
      1. Focus any Chrome window.
      2. Ctrl+T to open a new tab.
      3. Type the most-recent feed URL into the address bar; Enter.
      4. Wait for hydrate; subsequent input primitives gate on the Upwork
         tab being foreground, so any focus drift after this point will
         raise FocusLost cleanly.

    Caller must have set act.set_target_window so both 'Google Chrome' and
    'Upwork' are accepted foreground targets — otherwise step 2 (Ctrl+T)
    fails focus verification because the page hasn't navigated yet.
    """
    if not act.focus_window(CHROME_WINDOW):
        raise RuntimeError(
            f"Chrome window not found. Run: "
            f"uv run python substrate/launch_chrome.py --profile 'Moazzam' "
            f"--url '{MOST_RECENT_URL}' --kill-existing"
        )
    act.key("ctrl+t")
    time.sleep(0.6)
    act.navigate(MOST_RECENT_URL)
    time.sleep(6.0)


def click_most_recent_tab(window_title: str) -> None:
    """No-op: refresh_feed navigates directly to the most-recent URL, so the
    'Most Recent' tab click is unnecessary. Kept as a callable for backwards
    compatibility with run_one_cycle's call sequence."""
    return


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html_tags(s: str) -> str:
    """Remove HTML tags. Search-results pages put <span class='highlight'>...</span>
    around matched search terms inside the Save Job button name."""
    return _HTML_TAG_RE.sub("", s).replace("  ", " ").strip()


def _find_card_end(elements: list, start: int, hard_end: int) -> int:
    """Walk forward from `start`; truncate when y jumps backwards by >150px.

    Card elements have monotonically growing y. When y resets to a much smaller
    value, we've crossed into a sibling DOM subtree (sidebar) and need to stop.
    """
    if start >= hard_end:
        return start
    max_y = elements[start].bounds[1]
    for i in range(start + 1, hard_end):
        y = elements[i].bounds[1]
        if y < max_y - 150:
            return i
        if y > max_y:
            max_y = y
    return hard_end


def _parse_visible_cards(window_title: str) -> list:
    """Observe the feed once and return title hyperlinks for cards currently in view.

    Uses the legacy parse_feed_cards algorithm:
      1. Slice elements between consecutive 'Posted' anchors.
      2. Truncate slices via the y-jump heuristic (sidebar boundary).
      3. Recover canonical title from 'Save job <title>' button.
      4. Match canonical title to a hyperlink in the same slice; that's the click target.

    Returns list of (canonical_title, title_hyperlink_element) tuples.
    """
    obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
    elements = obs.elements

    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and (e.name or "").strip() == "Posted"
    ]
    if not posted_indices:
        return []

    out = []
    save_prefix = "save job "
    for idx, start in enumerate(posted_indices):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        end = _find_card_end(elements, start, next_posted)
        slice_elems = elements[start:end]

        canonical_title = ""
        for e in slice_elems:
            if e.role == "button" and (e.name or "").lower().startswith(save_prefix):
                canonical_title = _strip_html_tags(e.name[len(save_prefix):])
                break
        if not canonical_title:
            continue

        canonical_lower = canonical_title.lower()
        title_el = None
        for e in slice_elems:
            if e.role == "hyperlink" and (e.name or "").strip().lower() == canonical_lower:
                title_el = e
                break
        if title_el is None:
            for e in slice_elems:
                if (e.role == "hyperlink"
                        and len(e.name or "") > 20
                        and canonical_lower.startswith((e.name or "").strip()[:30].lower())):
                    title_el = e
                    break
        if title_el is None:
            continue
        out.append((canonical_title, title_el))
    return out


def top_of_feed_cards(window_title: str, max_cards: int) -> list:
    """Scroll to the top of the feed, then walk down collecting up to `max_cards`
    title hyperlinks (one per card). Caller passes each into open_card_panel.

    Mirrors the legacy upwork_driver scan-loop: observe what's in view; if no new
    cards surfaced, wheel-scroll the feed and re-observe; stop when we have enough
    cards OR two consecutive observations yield no new cards (end of feed).
    """
    act.focus_window(window_title)
    act.key("ctrl+home")
    time.sleep(1.0)

    seen_titles: set[str] = set()
    title_els: list = []
    no_progress_iters = 0

    while len(title_els) < max_cards:
        cards = _parse_visible_cards(window_title)
        new_cards = [(t, el) for t, el in cards if t not in seen_titles]

        if not new_cards:
            no_progress_iters += 1
            if no_progress_iters >= 2:
                break
            act.focus_window(window_title)
            act.scroll(3, method="wheel")
            time.sleep(1.0)
            continue

        no_progress_iters = 0
        for title, el in new_cards:
            if len(title_els) >= max_cards:
                break
            seen_titles.add(title)
            title_els.append(el)

        if len(title_els) >= max_cards:
            break

        # More cards likely below the fold; scroll and observe again.
        act.focus_window(window_title)
        act.scroll(3, method="wheel")
        time.sleep(1.0)

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
