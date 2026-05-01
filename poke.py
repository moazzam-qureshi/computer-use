"""
Manual driver for the action primitives. Useful for proving a single tool
works end-to-end without an LLM in the loop.

Usage:
    # From last_dump.json:
    uv run poke.py 12                          # click element id 12
    uv run poke.py --find "Compose"            # find + click by name
    uv run poke.py --find "Search" --type "x"  # click + type
    uv run poke.py --list                      # list elements

    # Live (no dump file needed):
    uv run poke.py --live --find "Compose"     # observe-then-click
    uv run poke.py --wait "Recipients" --type "a@b.com"   # wait_for + click + type

    # Keyboard / navigation:
    uv run poke.py --key "ctrl+l"
    uv run poke.py --nav "https://github.com"

    # Targeting a specific Chrome tab without alt-tabbing:
    uv run poke.py --window "Gmail" --live --find "Compose"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import act
import observe

DUMP_PATH = Path("last_dump.json")


def load_dump() -> dict:
    if not DUMP_PATH.exists():
        print("last_dump.json missing. Run dump.py first or use --live.", file=sys.stderr)
        sys.exit(1)
    return json.loads(DUMP_PATH.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?", type=int, help="element id from last_dump.json")
    ap.add_argument("--find", help="substring search by element name (uses dump or live)")
    ap.add_argument("--wait", help="wait_for element with this name substring, then click")
    ap.add_argument("--timeout", type=float, default=10.0, help="wait_for timeout seconds")
    ap.add_argument("--live", action="store_true", help="re-observe instead of using last_dump.json")
    ap.add_argument("--window", help="target window title substring")
    ap.add_argument("--type", dest="type_text", help="text to type after clicking")
    ap.add_argument("--key", help="key combo, e.g. 'ctrl+l' or 'enter'")
    ap.add_argument("--nav", help="navigate to URL via address bar")
    ap.add_argument("--list", action="store_true", help="list all elements and exit")
    ap.add_argument("--no-click", action="store_true", help="just move, don't click")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds before acting")
    args = ap.parse_args()

    # Pure keyboard / navigation modes
    if args.key:
        print(f"Pressing {args.key} in {args.delay}s...", file=sys.stderr)
        time.sleep(args.delay)
        if args.window:
            act.focus_window(args.window)
        act.key(args.key)
        return

    if args.nav:
        print(f"Navigating to {args.nav} in {args.delay}s...", file=sys.stderr)
        time.sleep(args.delay)
        if args.window:
            act.focus_window(args.window)
        act.navigate(args.nav)
        return

    # wait_for path
    if args.wait:
        print(f"Waiting for element matching {args.wait!r} (timeout {args.timeout}s)...", file=sys.stderr)
        if args.window:
            act.focus_window(args.window)
        try:
            target = observe.wait_for(args.wait, window_title=args.window, timeout=args.timeout)
        except TimeoutError as ex:
            print(str(ex), file=sys.stderr)
            sys.exit(2)
        print(f"Found: id={target.id} role={target.role} name={target.name!r} bounds={target.bounds}", file=sys.stderr)
        if args.window:
            act.focus_window(args.window)
        if not args.no_click:
            act.click(target)
        if args.type_text:
            act.type_text(args.type_text)
        return

    # Live observe path
    if args.live:
        if args.window:
            act.focus_window(args.window)
            time.sleep(0.3)
        obs = observe.observe(window_title=args.window)
        print(f"Live observation: {len(obs.elements)} elements in {obs.elapsed_sec:.2f}s", file=sys.stderr)

        if args.list:
            for e in obs.elements:
                print(f"{e.id:4}  {e.role:10}  {e.name[:60]}  {e.bounds}")
            return

        if not args.find:
            ap.error("--live requires --find or --list")

        target = observe.find_by_name(obs.elements, args.find)
        if target is None:
            print(f"No element matching {args.find!r} in live observation.", file=sys.stderr)
            sys.exit(2)

        print(f"Target: id={target.id} role={target.role} name={target.name!r} bounds={target.bounds}", file=sys.stderr)
        print(f"Acting in {args.delay}s. Top-left to abort.", file=sys.stderr)
        time.sleep(args.delay)

        if args.window:
            act.focus_window(args.window)
        if not args.no_click:
            act.click(target)
        if args.type_text:
            act.type_text(args.type_text)
        return

    # Dump-file path (legacy)
    dump = load_dump()
    elements: list[dict] = dump["elements"]

    if args.list:
        for e in elements:
            print(f"{e['id']:4}  {e['role']:10}  {e['name'][:60]}  {e['bounds']}")
        return

    target_dict = None
    if args.find:
        needle = args.find.lower()
        for e in elements:
            if needle in e["name"].lower():
                target_dict = e
                break
    elif args.id is not None:
        for e in elements:
            if e["id"] == args.id:
                target_dict = e
                break
    else:
        ap.error("provide an id, --find, --wait, --live, --key, --nav, or --list")

    if target_dict is None:
        print("No matching element.", file=sys.stderr)
        sys.exit(2)

    target = act.element_from_dict(target_dict)
    print(f"Target: id={target.id} role={target.role} name={target.name!r} bounds={target.bounds}", file=sys.stderr)
    print(f"Acting in {args.delay}s. Top-left to abort.", file=sys.stderr)
    time.sleep(args.delay)

    act.focus_window(dump.get("window_title", ""))

    if args.no_click:
        act.move_to(*target.center)
    else:
        act.click(target)

    if args.type_text:
        act.type_text(args.type_text)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
