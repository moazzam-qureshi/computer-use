"""Diagnostic v3: probe URL extraction on the SAME window the substrate
just navigated. No enumeration, no pane-picking. Use uia.WindowControl
the same way substrate.act.focus_window does.

Procedure:
  1. Refresh feed; Ctrl+Home; Ctrl+- x4.
  2. Locate the active window via uia.WindowControl(SubName="Upwork") --
     identical to how the substrate already does it.
  3. DFS for the first long-named hyperlink (the title link).
  4. Dump every property + pattern + raw COM property on it.

Usage:
    uv run python bin/debug_uia_url_extraction.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pyautogui
import uiautomation as ua

from substrate import act
from upwork.feed import refresh_feed


WINDOW = "Upwork"


def _safe_print(label: str, fn):
    try:
        v = fn()
        s = repr(v)
        if len(s) > 250:
            s = s[:250] + "..."
        print(f"  {label:55s} -> {s}")
    except Exception as e:
        msg = repr(e)[:200]
        print(f"  {label:55s} -> EXC {type(e).__name__}: {msg}")


def _walk_for_title_hyperlink(node, depth: int = 0, max_depth: int = 25):
    if depth > max_depth:
        return None
    try:
        ct = node.LocalizedControlType
    except Exception:
        return None
    try:
        name = node.Name or ""
    except Exception:
        name = ""
    if ct == "hyperlink" and len(name) > 30:
        return node
    try:
        child = node.GetFirstChildControl()
    except Exception:
        return None
    while child is not None:
        found = _walk_for_title_hyperlink(child, depth + 1, max_depth)
        if found is not None:
            return found
        try:
            child = child.GetNextSiblingControl()
        except Exception:
            break
    return None


def main() -> int:
    print("\n=== UIA URL-extraction diagnostic v3 ===\n", flush=True)
    with ua.UIAutomationInitializerInThread():
        act.set_target_window(("Upwork", "Google Chrome"))

        print("[1] refresh + zoom out", flush=True)
        refresh_feed(WINDOW)
        act.focus_window(WINDOW)
        act.key("ctrl+home")
        time.sleep(0.6)
        for _ in range(4):
            pyautogui.hotkey("ctrl", "-")
            time.sleep(0.18)
        time.sleep(0.6)

        print("\n[2] locate the active Chrome/Upwork window via uia.WindowControl", flush=True)
        win = ua.WindowControl(searchDepth=1, SubName="Upwork")
        if not win.Exists(2):
            print("  WindowControl(SubName='Upwork') not found; bailing")
            return 1
        print(f"  window: name={win.Name!r} class={win.ClassName!r}")
        try:
            print(f"  bounds: {win.BoundingRectangle}")
        except Exception:
            pass

        print("\n[3] DFS into the window for first title hyperlink (name length > 30)", flush=True)
        t0 = time.time()
        title_link = _walk_for_title_hyperlink(win, max_depth=30)
        print(f"  walk took {time.time() - t0:.2f}s")
        if title_link is None:
            print("  no title hyperlink found in this window; bailing")
            return 1
        print(f"  picked title: {title_link.Name!r}")

        print("\n[4] every property on this LIVE element", flush=True)
        for prop in (
            "Name", "LocalizedControlType", "ControlType", "AutomationId",
            "ClassName", "AcceleratorKey", "AccessKey", "HelpText",
            "ProcessId", "BoundingRectangle", "IsKeyboardFocusable",
            "IsEnabled", "IsOffscreen", "ItemStatus", "ItemType",
        ):
            _safe_print(prop, lambda p=prop: getattr(title_link, p))

        print("\n[5] every pattern", flush=True)
        for pat_name in [
            "InvokePattern", "ValuePattern", "LegacyIAccessiblePattern",
            "TextPattern", "TextPattern2", "SelectionItemPattern",
            "ScrollItemPattern", "ExpandCollapsePattern", "TogglePattern",
        ]:
            method = f"Get{pat_name}"
            fn = getattr(title_link, method, None)
            if fn is None:
                continue
            try:
                pat = fn()
            except Exception as e:
                print(f"  {method}: raised {e!r}")
                continue
            if pat is None:
                print(f"  {method}: None")
                continue
            print(f"  {method}: {pat}")
            for p in ("Value", "DefaultAction", "Description", "Help",
                      "KeyboardShortcut", "Name", "Role", "State"):
                try:
                    v = getattr(pat, p, None)
                    if v is None:
                        continue
                    if callable(v):
                        try:
                            v = v()
                        except Exception:
                            continue
                    s = repr(v)
                    if len(s) > 250:
                        s = s[:250] + "..."
                    print(f"      .{p} = {s}")
                except Exception:
                    pass

        print("\n[6] raw COM property IDs", flush=True)
        prop_ids = {
            30005: "HelpText",
            30037: "ItemStatus",
            30045: "ValueValue",
            30049: "LegacyIAccessibleValue",
            30050: "LegacyIAccessibleHelp",
            30051: "LegacyIAccessibleDescription",
            30052: "LegacyIAccessibleRole",
            30053: "LegacyIAccessibleState",
            30070: "AriaProperties",
            30071: "AriaRole",
            30095: "FullDescription",
        }
        try:
            native = title_link.Element
            for pid, label in prop_ids.items():
                try:
                    val = native.GetCurrentPropertyValue(pid)
                    s = repr(val)
                    if len(s) > 250:
                        s = s[:250] + "..."
                    print(f"  {label:35s} ({pid}) -> {s}")
                except Exception as e:
                    print(f"  {label:35s} ({pid}) -> EXC {repr(e)[:120]}")
        except Exception as e:
            print(f"  could not get native element: {e}")

        print("\n[7] parent + first 6 children", flush=True)
        try:
            parent = title_link.GetParentControl()
            if parent is not None:
                print(f"  parent: type={parent.LocalizedControlType!r} name={(parent.Name or '')[:80]!r}")
                for pat_name in ("ValuePattern", "LegacyIAccessiblePattern"):
                    method = f"Get{pat_name}"
                    fn = getattr(parent, method, None)
                    if fn:
                        try:
                            pat = fn()
                            if pat is not None:
                                for prop in ("Value", "Description", "Help"):
                                    try:
                                        v = getattr(pat, prop, None)
                                        if callable(v):
                                            v = v()
                                        if v:
                                            print(f"    parent.{pat_name}.{prop} = {v!r}"[:300])
                                    except Exception:
                                        pass
                        except Exception:
                            pass
        except Exception as e:
            print(f"  parent dump failed: {e}")

        print("\n  children:")
        try:
            child = title_link.GetFirstChildControl()
            count = 0
            while child is not None and count < 6:
                try:
                    print(f"    [{count}] type={child.LocalizedControlType!r} name={(child.Name or '')[:80]!r}")
                    vp = child.GetValuePattern() if hasattr(child, "GetValuePattern") else None
                    if vp is not None:
                        try:
                            v = vp.Value
                            if v:
                                print(f"        ValuePattern.Value = {v!r}"[:300])
                        except Exception:
                            pass
                except Exception as e:
                    print(f"    [{count}] EXC: {e}")
                try:
                    child = child.GetNextSiblingControl()
                except Exception:
                    break
                count += 1
        except Exception as e:
            print(f"  child dump failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
