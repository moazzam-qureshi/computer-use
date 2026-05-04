"""Diagnostic: how aggressively can we zoom out before things break?

The Phase 2.B recipe uses Ctrl+- x4 (67% zoom). At this zoom we get 2/4/4
cards across 3 viewports = 10 cards, AND the panel's Copy button stays at
top of viewport (no internal panel scroll).

Question: can we go further? If we hit 50% / 33% / 25%, do we get:
  - more cards in a single walk (ideally all 10)?
  - panel still fully readable + Copy still visible?
  - clicks still working on smaller targets?

This script tries each zoom level, reports cards visible per walk, and
attempts ONE click+capture to see if the panel walk still works.

Usage:
    uv run python bin/debug_zoom_extreme.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pyautogui
import uiautomation as ua

from substrate import act, observe, pacing
from upwork.feed import refresh_feed, _parse_visible_cards
from upwork import panel as upwork_panel

pacing.configure(pacing.PacingConfig(max_actions_per_hour=2000))


WINDOW = "Upwork"


def _count_cards_now() -> int:
    obs = observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
    elements = obs.elements
    return sum(
        1 for e in elements
        if e.role == "text" and (e.name or "").strip().startswith("Posted")
    )


def _list_visible_cards_now() -> list:
    return _parse_visible_cards(WINDOW)


def _try_zoom_level(name: str, ctrl_minus_presses: int):
    print(f"\n{'='*60}")
    print(f"  ZOOM LEVEL: {name}  (Ctrl+- x{ctrl_minus_presses})")
    print('='*60)

    # Reset to 100%, then zoom out
    act.focus_window(WINDOW)
    pyautogui.hotkey("ctrl", "0")
    time.sleep(0.4)
    act.key("ctrl+home")
    time.sleep(0.5)
    for _ in range(ctrl_minus_presses):
        pyautogui.hotkey("ctrl", "-")
        time.sleep(0.18)
    time.sleep(0.8)

    # PASS A: top of feed
    print("\n  [walk A] top of feed")
    t0 = time.time()
    visible_a = _list_visible_cards_now()
    walk_a_s = time.time() - t0
    posted_a = _count_cards_now()
    print(f"    cards visible: {len(visible_a)}, 'Posted' anchors: {posted_a}, walk: {walk_a_s:.2f}s")
    for i, (title, _el) in enumerate(visible_a):
        print(f"      {i+1}. {title[:80]}")

    # PASS B: Down x21
    print("\n  [walk B] after Down x21")
    act.focus_window(WINDOW)
    for _ in range(21):
        act.key("down")
    time.sleep(0.7)
    visible_b = _list_visible_cards_now()
    new_b = [t for (t, _) in visible_b if t not in {a[0] for a in visible_a}]
    print(f"    cards visible: {len(visible_b)}, new since A: {len(new_b)}")
    for i, t in enumerate(new_b):
        print(f"      +{i+1}. {t[:80]}")

    # PASS C: Down x21 again
    print("\n  [walk C] after another Down x21")
    act.focus_window(WINDOW)
    for _ in range(21):
        act.key("down")
    time.sleep(0.7)
    visible_c = _list_visible_cards_now()
    seen_so_far = {a[0] for a in visible_a} | {b[0] for b in visible_b}
    new_c = [t for (t, _) in visible_c if t not in seen_so_far]
    print(f"    cards visible: {len(visible_c)}, new since A+B: {len(new_c)}")

    total_unique = len(set([a[0] for a in visible_a] + [b[0] for b in visible_b] + [c[0] for c in visible_c]))
    print(f"\n  TOTAL UNIQUE CARDS at this zoom: {total_unique}")

    # ----- Panel-walk test on the FIRST visible card from PASS A -----
    if not visible_a:
        print("  no card to test panel walk; skipping")
        return

    # Reset to top so the first card is in view + clickable
    act.focus_window(WINDOW)
    act.key("ctrl+home")
    time.sleep(0.7)
    fresh_visible = _list_visible_cards_now()
    if not fresh_visible:
        print("  could not re-locate cards after Ctrl+Home; skipping panel test")
        return

    title, link_el = fresh_visible[0]
    bounds = link_el.bounds
    mid_y = (bounds[1] + bounds[3]) // 2
    print(f"\n  [panel-test] picked first card: {title[:60]!r}")
    print(f"    bounds={bounds}  mid_y={mid_y}  width={bounds[2]-bounds[0]}px  height={bounds[3]-bounds[1]}px")

    if mid_y > 1100:
        print("    SKIP click: y below safe zone")
        return

    print("    clicking...")
    try:
        act.click(link_el)
    except Exception as e:
        print(f"    CLICK FAILED: {e!r}")
        return
    time.sleep(2.5)

    print("    capturing panel...")
    try:
        elements, url = upwork_panel.capture_panel(WINDOW)
        n_panel_elements = len(elements)
        print(f"    panel: {n_panel_elements} elements, url={url[:60]!r}")
    except Exception as e:
        print(f"    CAPTURE FAILED: {e!r}")
        # Try to close panel anyway
        act.focus_window(WINDOW)
        act.key("escape")
        time.sleep(0.5)
        return

    # Quick parse to see what we got
    panel_data = upwork_panel.parse_panel(elements)
    print(f"    parsed: budget_kind={panel_data.budget_kind}  budget_min={panel_data.budget_min_usd}  country={panel_data.client_country}")
    print(f"            description chars: {len(panel_data.description or '')}")
    print(f"            skills count: {len(panel_data.skills)}")

    act.focus_window(WINDOW)
    act.key("escape")
    time.sleep(0.6)


def main() -> int:
    print("\n=== Extreme zoom-out diagnostic ===")
    print("Tests how aggressively we can zoom while keeping UIA + click + panel-walk working.\n")

    with ua.UIAutomationInitializerInThread():
        act.set_target_window(("Upwork", "Google Chrome"))

        # Refresh once at the start; subsequent zoom changes don't need re-refresh
        print("[setup] refresh feed (one-time)")
        refresh_feed(WINDOW)

        # Test 67% first as the baseline
        _try_zoom_level("67%", 4)
        # 50%
        _try_zoom_level("50%", 5)
        # 33%
        _try_zoom_level("33%", 6)
        # 25%
        _try_zoom_level("25%", 7)

        # Reset for cleanliness
        act.focus_window(WINDOW)
        pyautogui.hotkey("ctrl", "0")

    print("\n=== Done. ===")
    print("Look at: cards-per-walk, total-unique, panel-walk success per zoom.")
    print("If 50% gets all 10 cards in 1 walk AND panel still works, big win.")
    print("If 33% works, we may not need PASS B/C at all.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
