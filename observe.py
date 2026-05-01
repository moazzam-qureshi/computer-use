"""
Observation primitives: walk a window's UI Automation tree, return elements.

Same filtering logic as dump.py, but importable and re-callable in-process
so wait_for() can poll without spawning subprocesses.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import uiautomation as uia

from act import Element

INTERACTABLE_ROLES = {
    "ButtonControl", "HyperlinkControl", "EditControl", "ComboBoxControl",
    "CheckBoxControl", "RadioButtonControl", "ListItemControl", "TabItemControl",
    "MenuItemControl", "TreeItemControl", "SplitButtonControl", "DocumentControl",
}


@dataclass
class Observation:
    window_title: str
    window_class: str
    elements: list[Element]
    walked: int
    elapsed_sec: float


def _is_visible(ctrl) -> bool:
    try:
        if ctrl.IsOffscreen:
            return False
        r = ctrl.BoundingRectangle
        return r.width() > 0 and r.height() > 0
    except Exception:
        return False


def _role_short(ctrl) -> str:
    name = ctrl.ControlTypeName or ""
    return name.replace("Control", "").lower() if name else "unknown"


def find_window(title_substr):
    """Return foreground window if title_substr is None; else find by title.

    `title_substr` accepts a string or a tuple/list of strings; the first
    window whose title contains ANY of the substrings is returned.
    """
    if title_substr is None:
        return uia.GetForegroundControl()
    if isinstance(title_substr, str):
        needles = [title_substr.lower()]
    else:
        needles = [s.lower() for s in title_substr if s]
    desktop = uia.GetRootControl()
    win = desktop.GetFirstChildControl()
    while win is not None:
        try:
            if win.ControlTypeName == "WindowControl":
                name_l = (win.Name or "").lower()
                if any(n in name_l for n in needles):
                    return win
        except Exception:
            pass
        win = win.GetNextSiblingControl()
    return None


def wake_chrome_a11y(root) -> bool:
    try:
        doc = root.DocumentControl(searchDepth=20)
    except Exception:
        return False
    if not doc.Exists(0.3):
        return False
    try:
        if doc.IsLegacyIAccessiblePatternAvailable():
            doc.GetLegacyIAccessiblePattern()
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


TEXT_ROLES = {"TextControl", "ImageControl"}  # ImageControl included because alt text often describes content


def observe(window_title: str | None = None, max_nodes: int = 5000, wake: bool = True, include_unnamed: bool = False, include_text: bool = False) -> Observation:
    """Walk the tree and return interactable + named elements."""
    root = find_window(window_title)
    if root is None:
        raise RuntimeError(f"Window not found: {window_title!r}")

    try:
        title = root.Name or ""
        class_name = root.ClassName or ""
    except Exception:
        title, class_name = "", ""

    if wake and "Chrome_WidgetWin" in class_name:
        if wake_chrome_a11y(root):
            time.sleep(0.4)

    elements: list[Element] = []
    walked = [0]
    next_id = [0]

    def walk(ctrl, depth: int):
        if walked[0] >= max_nodes:
            return
        walked[0] += 1

        try:
            ctype = ctrl.ControlTypeName or ""
        except Exception:
            return

        if _is_visible(ctrl):
            interactable = ctype in INTERACTABLE_ROLES
            try:
                name = (ctrl.Name or "").strip()
            except Exception:
                name = ""

            # Keep if interactable+named (default) OR if include_unnamed and it's an editable surface,
            # OR if include_text and it's a long-form text control with a name.
            keep = (interactable and name) or (
                include_unnamed and ctype in {"EditControl", "DocumentControl"}
            ) or (
                include_text and ctype in TEXT_ROLES and name and len(name) >= 2
            )
            if keep:
                try:
                    r = ctrl.BoundingRectangle
                    bounds = (r.left, r.top, r.right, r.bottom)
                except Exception:
                    bounds = (0, 0, 0, 0)
                try:
                    value = ctrl.GetValuePattern().Value if ctrl.IsValuePatternAvailable() else ""
                except Exception:
                    value = ""
                elements.append(Element(
                    id=next_id[0],
                    role=_role_short(ctrl),
                    name=name,
                    bounds=bounds,
                    value=(value or "")[:200],
                ))
                next_id[0] += 1

        try:
            child = ctrl.GetFirstChildControl()
        except Exception:
            child = None
        while child is not None:
            walk(child, depth + 1)
            if walked[0] >= max_nodes:
                return
            try:
                child = child.GetNextSiblingControl()
            except Exception:
                break

    t0 = time.time()
    walk(root, 0)
    elapsed = time.time() - t0

    return Observation(
        window_title=title,
        window_class=class_name,
        elements=elements,
        walked=walked[0],
        elapsed_sec=elapsed,
    )


def diff_elements(before: list[Element], after: list[Element]) -> list[Element]:
    """Return elements present in `after` but not in `before`.

    Identity uses (role, name, bounds) as the fingerprint, since ids are not
    stable across observations. Useful for detecting newly-rendered content
    after an action (modal opened, composer expanded, etc.).
    """
    before_keys = {(e.role, e.name, e.bounds) for e in before}
    return [e for e in after if (e.role, e.name, e.bounds) not in before_keys]


def find_by_name(elements: list[Element], needle: str, role: str | None = None) -> Element | None:
    """First element whose name contains needle (case-insensitive). Optional role filter."""
    needle_l = needle.lower()
    for e in elements:
        if role and e.role != role:
            continue
        if needle_l in e.name.lower():
            return e
    return None


def wait_for_new(
    before: list[Element],
    *,
    window_title: str | None = None,
    role: str | None = None,
    name_substr: str | None = None,
    min_count: int = 1,
    timeout: float = 10.0,
    poll_interval: float = 0.4,
    include_unnamed: bool = True,
) -> list[Element]:
    """Poll until at least min_count elements appear that weren't in `before`.

    Useful for "click button, wait for modal/composer to appear, find its editor"
    flows where the new editable surface may have no usable a11y name but is
    guaranteed to be something that didn't exist before.

    Optional role / name_substr filters narrow the diff.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        after = observe(window_title=window_title, include_unnamed=include_unnamed).elements
        new = diff_elements(before, after)
        if role:
            new = [e for e in new if e.role == role]
        if name_substr:
            n = name_substr.lower()
            new = [e for e in new if n in e.name.lower()]
        if len(new) >= min_count:
            return new
        time.sleep(poll_interval)
    raise TimeoutError(
        f"Did not see {min_count} new elements (role={role}, name_substr={name_substr}) within {timeout}s"
    )


class WaitTimeout(TimeoutError):
    """Raised when wait_for / wait_for_predicate doesn't see the expected state."""


def wait_for(
    name_substr: str,
    *,
    window_title: str | None = None,
    role: str | None = None,
    timeout: float = 10.0,
    poll_interval: float = 0.4,
) -> Element:
    """Poll observe() until an element matching name_substr (and optional role) appears.

    Raises WaitTimeout if it doesn't appear in time.
    """
    deadline = time.time() + timeout
    last_count = -1
    while time.time() < deadline:
        obs = observe(window_title=window_title)
        match = find_by_name(obs.elements, name_substr, role=role)
        if match is not None:
            return match
        if len(obs.elements) != last_count:
            last_count = len(obs.elements)
        time.sleep(poll_interval)
    raise WaitTimeout(f"Element with name containing {name_substr!r} (role={role}) not found within {timeout}s")


def wait_for_predicate(
    predicate,
    *,
    window_title: str | None = None,
    include_unnamed: bool = False,
    include_text: bool = False,
    timeout: float = 10.0,
    poll_interval: float = 0.5,
    description: str = "predicate",
):
    """Poll observe() until `predicate(elements)` returns truthy. Returns the
    truthy result. Raises WaitTimeout on miss.

    Use when the post-condition isn't a single element-name match — e.g.
    "any element whose name contains 'Cover letter' (case-insensitive)" or
    "the Submit button has gone away".
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            obs = observe(
                window_title=window_title,
                include_unnamed=include_unnamed,
                include_text=include_text,
            )
        except Exception:
            time.sleep(poll_interval)
            continue
        result = predicate(obs.elements)
        if result:
            return result
        time.sleep(poll_interval)
    raise WaitTimeout(f"{description} not satisfied within {timeout}s")
