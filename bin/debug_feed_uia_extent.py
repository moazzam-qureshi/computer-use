"""Diagnostic: full Phase 2.B substrate recipe + raw UIA dump for parser tuning.

Runs the operator's exact recipe:
    refresh -> Ctrl+Home -> Ctrl+- x4 (zoom 67%) -> walk UIA -> Down x21 -> walk
    UIA -> Down x21 -> walk UIA -> dedup -> Ctrl+0 reset zoom

Reports per pass:
  - how many "Posted" anchors are in the tree
  - which titles are NEW (vs prior passes)
Total unique cards across the three passes.

Also dumps the raw (role, name, bounds) for the first card's element slice
so we can fix the field-extractor that the previous run got wrong (posted_text
captured into skills, payment_verified missed, etc).

Usage:
    uv run python bin/debug_feed_uia_extent.py

Prereq:
    Chrome must be running with --force-renderer-accessibility, logged
    into Upwork. Launch via:
      uv run python substrate/launch_chrome.py --profile "Moazzam" \
          --url "https://www.upwork.com/nx/find-work/most-recent" --kill-existing
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import uiautomation as ua

from substrate import act, observe
from upwork.feed import refresh_feed


WINDOW = "Upwork"


def _truncate(s: str, n: int = 120) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "..."


def _find_card_end(elements: list, start: int, hard_end: int) -> int:
    """Truncate at a y-jump backwards (sibling subtree boundary)."""
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


def _read_hyperlink_url(el) -> str:
    """Try to read the href from a hyperlink element via UIA's ValuePattern.

    Returns the URL or empty string. UIA exposes hyperlink href as the
    ValuePattern.Value in Chrome; some elements expose it via the
    .value attribute that the uiautomation library precomputes, others
    only via GetCurrentPropertyValue or GetPattern(ValuePattern).
    Try the cheap path first, fall back to the pattern path."""
    cheap = (getattr(el, "value", None) or "").strip()
    if cheap:
        return cheap
    # Fallback: try to fetch ValuePattern via the underlying control.
    # The uiautomation library exposes the raw element on _automation_control.
    try:
        raw = getattr(el, "_automation_control", None) or getattr(el, "control", None)
        if raw is None:
            return ""
        vp = raw.GetValuePattern()
        if vp is None:
            return ""
        return (vp.Value or "").strip()
    except Exception:
        return ""


def _walk_and_extract_cards(window: str) -> tuple[list[dict], int, float]:
    """Single UIA walk; returns (cards, total_elements, walk_seconds).

    Each card is a dict with title, attempted URL, and a list of
    (role, name, url_if_hyperlink) tuples for every element in its
    slice (so we can hand-tune extraction later)."""
    t0 = time.time()
    obs = observe.observe(window_title=window, include_unnamed=False, include_text=True)
    elements = obs.elements
    walk_s = time.time() - t0

    posted_indices = [
        i for i, e in enumerate(elements)
        if e.role == "text" and (e.name or "").strip().startswith("Posted")
    ]

    cards = []
    save_prefix = "save job "
    for idx, start in enumerate(posted_indices):
        next_posted = posted_indices[idx + 1] if idx + 1 < len(posted_indices) else len(elements)
        end = _find_card_end(elements, start, next_posted)
        slice_elems = elements[start:end]

        title = None
        title_hyperlink_url = ""
        for e in slice_elems:
            if e.role == "button" and (e.name or "").lower().startswith(save_prefix):
                raw = e.name[len(save_prefix):]
                title = re.sub(r"<[^>]+>", "", raw).strip()
                break
        # Find the title hyperlink (its name equals the title, role=hyperlink)
        if title:
            for e in slice_elems:
                if e.role == "hyperlink" and (e.name or "").strip() == title:
                    title_hyperlink_url = _read_hyperlink_url(e)
                    break

        cards.append({
            "title": title,
            "title_url": title_hyperlink_url,
            "raw_elements": [
                {
                    "role": e.role,
                    "name": _truncate(e.name or "", 200),
                    "url": _read_hyperlink_url(e) if e.role == "hyperlink" else "",
                    "bounds": list(e.bounds),
                }
                for e in slice_elems
            ],
        })

    return cards, len(elements), walk_s


def _pass(window: str, label: str) -> list[dict]:
    print(f"\n--- {label} ---", flush=True)
    cards, n_elems, walk_s = _walk_and_extract_cards(window)
    print(f"    walk took {walk_s:.2f}s; {n_elems} elements; {len(cards)} cards visible", flush=True)
    for i, c in enumerate(cards):
        t = c["title"] or "(no title)"
        print(f"      {i+1}. {t[:90]}", flush=True)
    return cards


def main() -> int:
    print("\n=== Phase 2.B substrate diagnostic: full recipe walk ===", flush=True)

    with ua.UIAutomationInitializerInThread():
        act.set_target_window(("Upwork", "Google Chrome"))

        print("\n[1] refresh_feed (navigate + 20s hydrate)", flush=True)
        t0 = time.time()
        refresh_feed(WINDOW)
        print(f"    refresh+hydrate took {time.time() - t0:.1f}s", flush=True)

        print("\n[2] Ctrl+Home to top", flush=True)
        act.focus_window(WINDOW)
        act.key("ctrl+home")
        time.sleep(0.8)

        print("\n[3] Ctrl+- x4 to zoom 67%", flush=True)
        # pyautogui keyname for '-' is just '-' (passed through ascii); 'minus'
        # is not a recognized alias, which is why the previous run silently
        # didn't zoom. Use the literal hyphen via pyautogui.hotkey directly to
        # bypass any act-level key-name guessing.
        import pyautogui
        act.focus_window(WINDOW)  # ensure Chrome owns input
        for _ in range(4):
            pyautogui.hotkey("ctrl", "-")
            time.sleep(0.18)
        time.sleep(0.6)  # let zoom settle

        # Pass A: top of feed at 67% zoom
        cards_a = _pass(WINDOW, "PASS A: top of feed (67% zoom)")
        seen_titles: set[str] = set()
        unique = []
        for c in cards_a:
            if c["title"] and c["title"] not in seen_titles:
                seen_titles.add(c["title"])
                unique.append(c)

        # Down x21 -> Pass B
        print("\n[4] Down x21 (scroll to next batch)", flush=True)
        act.focus_window(WINDOW)
        for _ in range(21):
            act.key("down")
            time.sleep(0.04)
        time.sleep(0.6)

        cards_b = _pass(WINDOW, "PASS B: after Down x21")
        new_b = 0
        for c in cards_b:
            if c["title"] and c["title"] not in seen_titles:
                seen_titles.add(c["title"])
                unique.append(c)
                new_b += 1
        print(f"    NEW cards in pass B: {new_b}", flush=True)

        # Down x21 -> Pass C
        print("\n[5] Down x21 (scroll to last batch)", flush=True)
        act.focus_window(WINDOW)
        for _ in range(21):
            act.key("down")
            time.sleep(0.04)
        time.sleep(0.6)

        cards_c = _pass(WINDOW, "PASS C: after another Down x21")
        new_c = 0
        for c in cards_c:
            if c["title"] and c["title"] not in seen_titles:
                seen_titles.add(c["title"])
                unique.append(c)
                new_c += 1
        print(f"    NEW cards in pass C: {new_c}", flush=True)

        # Reset zoom for cleanliness
        print("\n[6] Ctrl+0 reset zoom", flush=True)
        act.focus_window(WINDOW)
        pyautogui.hotkey("ctrl", "0")
        time.sleep(0.4)

        print(f"\n=== UNIQUE CARDS COLLECTED ACROSS 3 PASSES: {len(unique)} ===", flush=True)
        n_with_url = 0
        for i, c in enumerate(unique):
            url = c.get("title_url") or ""
            has_url = bool(url and "/jobs/" in url)
            if has_url:
                n_with_url += 1
            tag = "[URL OK]" if has_url else "[NO URL]"
            print(f"  {i+1:2d}. {tag} {(c['title'] or '(no title)')[:80]}")
            if url:
                print(f"        url: {url[:120]}")

        print(f"\n=== URL EXTRACTION COVERAGE: {n_with_url}/{len(unique)} cards have a job URL ===")

        # Dump the raw element slice for the FIRST collected card so we can
        # tune the field extractor against real data.
        if unique:
            print("\n=== RAW ELEMENT SLICE — card #1 (for parser tuning) ===")
            print(f"  title: {unique[0]['title']}")
            print(f"  title_url: {unique[0].get('title_url') or '(empty)'}")
            for i, el in enumerate(unique[0]["raw_elements"]):
                url_part = f" url={el['url'][:80]!r}" if el.get("url") else ""
                print(f"   {i:3d}. role={el['role']:<12} name={el['name']!r}{url_part}")

        # And card #2 if we have one
        if len(unique) >= 2:
            print("\n=== RAW ELEMENT SLICE — card #2 ===")
            print(f"  title: {unique[1]['title']}")
            print(f"  title_url: {unique[1].get('title_url') or '(empty)'}")
            for i, el in enumerate(unique[1]["raw_elements"]):
                url_part = f" url={el['url'][:80]!r}" if el.get("url") else ""
                print(f"   {i:3d}. role={el['role']:<12} name={el['name']!r}{url_part}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
