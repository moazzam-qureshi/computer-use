"""Diagnostic: dump what feed.top_of_feed_cards sees on the live Upwork window.

Prints how many 'Posted' labels were found, how many cards were extracted,
and a few raw elements around each card so you can tweak selectors quickly.

Usage: uv run python bin/debug_feed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uiautomation as ua
from substrate import act, observe


WINDOW = "Upwork"


def main() -> int:
    with ua.UIAutomationInitializerInThread():
        act.focus_window(WINDOW)
        act.key("ctrl+home")
        import time
        time.sleep(1.0)

        obs = observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
        elements = obs.elements
        print(f"Total observed elements: {len(elements)}")

        posted_indices = [
            i for i, e in enumerate(elements)
            if e.role == "text" and (e.name or "").strip() == "Posted"
        ]
        print(f"'Posted' label count: {len(posted_indices)}")

        # Look for variants in case label text changed
        posted_variants = {}
        for e in elements:
            if e.role == "text":
                n = (e.name or "").strip()
                low = n.lower()
                if "post" in low and len(n) <= 30:
                    posted_variants[n] = posted_variants.get(n, 0) + 1
        print(f"Posted-ish variants seen: {posted_variants}")

        # Dump per-card slice info
        for idx, start in enumerate(posted_indices[:5]):
            end = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
            print(f"\n--- Card #{idx + 1}: elements {start}..{end} ({end - start} items) ---")
            slice_elems = elements[start:end]
            hyperlinks = [e for e in slice_elems if e.role == "hyperlink"]
            print(f"  hyperlink count in slice: {len(hyperlinks)}")
            for h in hyperlinks[:6]:
                name_preview = (h.name or "")[:80]
                print(f"    [hyperlink len={len(h.name or '')}] {name_preview!r}")

        # Also show first 5 hyperlinks anywhere on the page (sanity)
        print("\nFirst 5 hyperlinks anywhere on page:")
        all_hls = [e for e in elements if e.role == "hyperlink"]
        print(f"  total hyperlinks: {len(all_hls)}")
        for h in all_hls[:5]:
            print(f"    [len={len(h.name or '')}] {(h.name or '')[:80]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
