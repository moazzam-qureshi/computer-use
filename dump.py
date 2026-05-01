"""
Walk the UI Automation tree of a target window.
Filter to interactable + visible elements with usable names.
Write JSON to last_dump.json so poke.py can act on it.

Usage:
    uv run dump.py                       # foreground window
    uv run dump.py --window "Gmail"      # find window by title substring
    uv run dump.py --all                 # don't filter, dump everything
    uv run dump.py --max 2000            # cap node count walked
    uv run dump.py --no-wake             # skip the a11y wake-up step
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import uiautomation as uia

INTERACTABLE_ROLES = {
    "ButtonControl", "HyperlinkControl", "EditControl", "ComboBoxControl",
    "CheckBoxControl", "RadioButtonControl", "ListItemControl", "TabItemControl",
    "MenuItemControl", "TreeItemControl", "SplitButtonControl", "DocumentControl",
}

# Roles we walk into but don't emit unless they're directly clickable.
CONTAINER_ROLES = {
    "PaneControl", "GroupControl", "DocumentControl", "WindowControl",
    "ListControl", "TreeControl", "TabControl", "ToolBarControl",
    "MenuControl", "MenuBarControl", "TableControl", "DataGridControl",
    "ScrollBarControl", "CustomControl",
}


def bounds_tuple(rect) -> tuple[int, int, int, int]:
    return (rect.left, rect.top, rect.right, rect.bottom)


def is_visible(ctrl) -> bool:
    try:
        if ctrl.IsOffscreen:
            return False
        r = ctrl.BoundingRectangle
        if r.width() <= 0 or r.height() <= 0:
            return False
        return True
    except Exception:
        return False


def role_short(ctrl) -> str:
    name = ctrl.ControlTypeName  # e.g. "ButtonControl"
    return name.replace("Control", "").lower() if name else "unknown"


def wake_chrome_a11y(root) -> bool:
    """Force Chrome to expose its renderer accessibility tree.

    Chrome only builds the web-content a11y tree when something requests it.
    Accessing the LegacyIAccessible pattern on the Document control triggers
    that build. Returns True if a Document was found and poked.
    """
    try:
        doc = root.DocumentControl(searchDepth=20)
    except Exception:
        return False
    if not doc.Exists(0.5):
        return False
    try:
        if doc.IsLegacyIAccessiblePatternAvailable():
            doc.GetLegacyIAccessiblePattern()  # noqa: F841 — call triggers build
        # Also iterate one level of children to nudge the tree to populate
        c = doc.GetFirstChildControl()
        for _ in range(5):
            if c is None:
                break
            try:
                _ = c.Name
            except Exception:
                pass
            c = c.GetNextSiblingControl()
        return True
    except Exception:
        return False


def find_window_by_title(needle: str):
    """Find a top-level window whose title contains needle (case-insensitive)."""
    needle_l = needle.lower()
    desktop = uia.GetRootControl()
    win = desktop.GetFirstChildControl()
    while win is not None:
        try:
            if win.ControlTypeName == "WindowControl":
                name = (win.Name or "").lower()
                if needle_l in name:
                    return win
        except Exception:
            pass
        win = win.GetNextSiblingControl()
    return None


TEXT_ROLES = {"TextControl", "ImageControl"}


def collect(root, max_nodes: int, keep_all: bool, include_text: bool = False) -> tuple[list[dict], dict]:
    out: list[dict] = []
    stats = {"walked": 0, "kept": 0, "skipped_invisible": 0, "skipped_unnamed": 0}
    next_id = [0]

    def walk(ctrl, depth: int):
        if stats["walked"] >= max_nodes:
            return
        stats["walked"] += 1

        try:
            ctype = ctrl.ControlTypeName or ""
        except Exception:
            return

        visible = is_visible(ctrl)
        if not visible:
            stats["skipped_invisible"] += 1
        else:
            interactable = ctype in INTERACTABLE_ROLES
            try:
                name = (ctrl.Name or "").strip()
            except Exception:
                name = ""

            text_keep = include_text and ctype in TEXT_ROLES and name and len(name) >= 3
            if keep_all or (interactable and name) or text_keep:
                if interactable and not name and not keep_all:
                    stats["skipped_unnamed"] += 1
                else:
                    try:
                        r = ctrl.BoundingRectangle
                        bounds = bounds_tuple(r)
                    except Exception:
                        bounds = (0, 0, 0, 0)

                    try:
                        value = ctrl.GetValuePattern().Value if ctrl.IsValuePatternAvailable() else ""
                    except Exception:
                        value = ""

                    out.append({
                        "id": next_id[0],
                        "role": role_short(ctrl),
                        "name": name,
                        "value": value[:200] if value else "",
                        "bounds": bounds,
                        "depth": depth,
                    })
                    next_id[0] += 1
                    stats["kept"] += 1

        # Recurse
        try:
            child = ctrl.GetFirstChildControl()
        except Exception:
            child = None
        while child is not None:
            walk(child, depth + 1)
            if stats["walked"] >= max_nodes:
                return
            try:
                child = child.GetNextSiblingControl()
            except Exception:
                break

    walk(root, 0)
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="don't filter, keep every node")
    ap.add_argument("--max", type=int, default=5000, help="max nodes to walk")
    ap.add_argument("--out", default="last_dump.json")
    ap.add_argument("--window", help="find window by title substring instead of using foreground")
    ap.add_argument("--no-wake", action="store_true", help="skip Chrome a11y wake-up")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds to wait before reading")
    ap.add_argument("--text", action="store_true", help="also include long static text (TextControl, ImageControl)")
    args = ap.parse_args()

    if args.window:
        root = find_window_by_title(args.window)
        if root is None:
            print(f"No window found with title containing {args.window!r}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Focus the window you want to dump. Starting in {args.delay}s...", file=sys.stderr)
        time.sleep(args.delay)
        root = uia.GetForegroundControl()
        if root is None:
            print("No foreground window found.", file=sys.stderr)
            sys.exit(1)

    try:
        title = root.Name
        class_name = root.ClassName
    except Exception:
        title = "?"
        class_name = "?"

    print(f"Target window: {title!r}  class={class_name!r}", file=sys.stderr)

    is_chrome = "Chrome_WidgetWin" in (class_name or "")
    if is_chrome and not args.no_wake:
        woke = wake_chrome_a11y(root)
        if woke:
            time.sleep(0.4)  # give the renderer a moment to build the tree
            print("Woke Chrome a11y tree.", file=sys.stderr)
        else:
            print("Could not find a Document control to wake.", file=sys.stderr)

    t0 = time.time()
    elements, stats = collect(root, args.max, args.all, include_text=args.text)
    elapsed = time.time() - t0

    payload = {
        "window_title": title,
        "window_class": class_name,
        "elapsed_sec": round(elapsed, 2),
        "stats": stats,
        "elements": elements,
    }

    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Walked {stats['walked']} nodes in {elapsed:.2f}s", file=sys.stderr)
    print(f"Kept {stats['kept']}  skipped_invisible={stats['skipped_invisible']}  skipped_unnamed={stats['skipped_unnamed']}", file=sys.stderr)
    print(f"Wrote {args.out}", file=sys.stderr)

    # Print compact preview to stdout
    preview = [
        {"id": e["id"], "role": e["role"], "name": e["name"][:60], "bounds": e["bounds"]}
        for e in elements[:40]
    ]
    print(json.dumps(preview, indent=2, ensure_ascii=False))
    if len(elements) > 40:
        print(f"... ({len(elements) - 40} more in {args.out})")


if __name__ == "__main__":
    main()
