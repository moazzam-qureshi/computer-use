"""
Diagnostic: dump the LinkedIn tree before and after opening the composer,
then print only the *new* elements (the diff). This is how we'll find
editor surfaces in the general case — they often have no usable a11y name
but they're guaranteed to be elements that didn't exist before the action.

Usage:
    uv run inspect_composer.py
    uv run inspect_composer.py > composer_diff.txt   # (utf-8 safe)
"""
from __future__ import annotations

import io
import sys
import time

# Force stdout/stderr to UTF-8 so emoji / non-latin chars don't crash printing.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import act
import observe

WINDOW = "LinkedIn"


def main():
    if not act.focus_window(WINDOW):
        print("LinkedIn window not found.", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.4)

    print("Snapshot 1 (feed, composer closed)...", file=sys.stderr)
    before = observe.observe(window_title=WINDOW, include_unnamed=True)
    print(f"  {len(before.elements)} elements", file=sys.stderr)

    start = observe.find_by_name(before.elements, "Start a post")
    if start is None:
        print("'Start a post' not found.", file=sys.stderr)
        sys.exit(2)

    print("Clicking 'Start a post'...", file=sys.stderr)
    act.click(start)
    time.sleep(2.0)

    print("Snapshot 2 (composer open)...", file=sys.stderr)
    after = observe.observe(window_title=WINDOW, include_unnamed=True)
    print(f"  {len(after.elements)} elements", file=sys.stderr)

    new_elements = observe.diff_elements(before.elements, after.elements)
    print(f"  {len(new_elements)} new elements appeared after click\n", file=sys.stderr)

    print(f"{'role':12}  {'bounds':32}  name")
    print("-" * 100)
    for e in new_elements:
        b = f"{e.bounds}"
        v = f" value={e.value!r}" if e.value else ""
        print(f"{e.role:12}  {b:32}  {e.name[:60]}{v}")


if __name__ == "__main__":
    main()
