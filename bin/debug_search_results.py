"""Diagnostic: dump what extract_cards_from_window sees on the live
Upwork SEARCH RESULTS page (not the feed).

Use when search_market is returning 0 cards but the browser visually
shows results. Prints every 'Posted'-prefixed text element and the
slice immediately following it, plus what parse_card_slice extracts.

Usage:
  1. Open the Upwork search results page in Chrome (the URL search_market
     would navigate to).
  2. Make Chrome the foreground window.
  3. Run: uv run python bin/debug_search_results.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from substrate import observe
from upwork import feed_zoom
from upwork.feed_cards import (
    FeedCard, _find_card_end, parse_card_slice,
)


WINDOW = "Upwork"


def main() -> int:
    # Apply the same 33% zoom recipe the bidder/researcher uses so the
    # dump reflects what extract_cards_from_window actually sees.
    feed_zoom.zoom_to_33pct()

    obs = observe.observe(
        window_title=WINDOW, include_unnamed=False, include_text=True,
    )
    elements = obs.elements
    print(f"=== Total observed elements: {len(elements)} ===\n")

    # Show roles distribution so we can see if buttons/hyperlinks are
    # present at all.
    from collections import Counter
    role_counts = Counter(getattr(e, "role", "?") for e in elements)
    print("Role distribution:")
    for role, n in role_counts.most_common(10):
        print(f"  {role:20s}  {n}")
    print()

    # Print every 'Posted...' text element with position + neighbors.
    posted_indices = [
        i for i, e in enumerate(elements)
        if getattr(e, "role", "") == "text"
        and (e.name or "").strip().lower().startswith("posted")
    ]
    print(f"=== 'Posted'-prefixed text elements: {len(posted_indices)} ===\n")

    for idx, start in enumerate(posted_indices[:5]):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        end = _find_card_end(elements, start, next_posted)
        slice_elems = elements[start:end]
        print(f"--- Card #{idx} (slice indices {start}..{end-1}, {len(slice_elems)} elems) ---")
        # Dump first 30 slice elements: role, name (truncated), bounds
        for j, e in enumerate(slice_elems[:30]):
            name = (e.name or "")[:80].replace("\n", "\\n")
            print(f"  [{j:3d}] {getattr(e, 'role', '?'):12s} {name!r}  bounds={e.bounds}")
        if len(slice_elems) > 30:
            print(f"  ... ({len(slice_elems) - 30} more)")

        # What parse_card_slice extracts from this slice
        card = parse_card_slice(slice_elems)
        print(f"  -> parsed: title={card.title!r}, posted_text={card.posted_text!r}, "
              f"budget_text={card.budget_text!r}, skills={card.skills}")
        print()

    print("=== Save-job buttons in the entire observation ===")
    save_btns = [
        e for e in elements
        if getattr(e, "role", "") == "button"
        and (e.name or "").lower().startswith("save job")
    ]
    print(f"Found {len(save_btns)} 'Save job ...' buttons.")
    for e in save_btns[:5]:
        print(f"  {(e.name or '')[:120]!r}")
    print()

    # Also check 'Save job ' prefix variations and heart-icon buttons
    print("=== Other button names (sample, first 30) ===")
    btn_names = [
        (e.name or "").strip()
        for e in elements
        if getattr(e, "role", "") == "button"
    ]
    seen = set()
    for n in btn_names:
        if n and n not in seen:
            seen.add(n)
            if len(seen) > 30:
                break
            print(f"  {n[:120]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
