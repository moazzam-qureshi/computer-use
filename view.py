"""
Token-efficient view layer over observe.py.

Goals:
- Stable element IDs that survive across calls (hash of role+name+parent).
- Compact one-line element format ("b7a3f2 button \"Post\"").
- Default-hidden Chrome browser shell (window controls, tabs, address bar)
  to remove ~25-30 noise elements every page.
- Diff between snapshots so the LLM sees only changes after an action.

This module holds module-level state: a registry mapping stable ids to live
UIA elements, and the most-recent snapshot. Designed for single-agent use
in a single process. Reset between tasks with reset().
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

import observe
from act import Element

# --- Chrome browser shell elements that appear on every page (drop them) ---
CHROME_SHELL_NAMES = {
    "minimise", "minimize", "maximise", "maximize", "restore", "close",
    "back", "forward", "reload", "home",
    "view site information", "address and search bar",
    "bookmark this tab", "extensions", "moazzam", "chrome",
    "tab groups", "search tabs", "new tab", "all bookmarks",
    "menu containing hidden bookmarks", "managed bookmarks",
    "arrange split view", "ahrefs seo toolbar\nhas access to this site",
    "claim 1 free month of premium page",  # site-specific noise also possible
    "open upagents (ctrl+shift+k)",
    "find", "ask ai mode in google search",
    "install github",  # PWA install prompt — noise
}

# Roles considered "primary actions" — usually the page's main CTAs
PRIMARY_HINTS = {
    "post", "send", "compose", "submit", "publish", "save", "create",
    "start a post", "tweet", "reply", "share",
}


@dataclass
class Snapshot:
    """Server-side snapshot of an observation."""
    elements: list[Element]
    by_id: dict[str, Element] = field(default_factory=dict)
    window_title: str = ""

    def get(self, eid: str) -> Optional[Element]:
        return self.by_id.get(eid)


# Module-level state
_state: dict = {"window": None, "snapshot": None}


def reset(window_title: Optional[str] = None) -> None:
    _state["window"] = window_title
    _state["snapshot"] = None


def set_window(window_title: str) -> None:
    _state["window"] = window_title


def get_window() -> Optional[str]:
    return _state["window"]


def _stable_id(e: Element) -> str:
    """Deterministic 6-char hex id from role+name (or bounds for unnamed)."""
    role_prefix = {
        "button": "b", "hyperlink": "l", "edit": "e", "combobox": "c",
        "checkbox": "k", "radiobutton": "r", "listitem": "i",
        "tabitem": "t", "menuitem": "m", "treeitem": "n",
        "splitbutton": "s", "document": "d",
    }.get(e.role, "x")

    if e.name:
        seed = f"{e.role}|{e.name}"
    else:
        # Unnamed editable surfaces — round bounds to 50px so it's stable across small layout shifts
        l, t, r, b = e.bounds
        seed = f"{e.role}|UNNAMED|{l // 50}|{t // 50}|{r // 50}|{b // 50}"

    h = hashlib.blake2b(seed.encode("utf-8"), digest_size=3).hexdigest()
    return f"{role_prefix}{h}"


def _is_chrome_shell(e: Element) -> bool:
    n = e.name.strip().lower()
    if n in CHROME_SHELL_NAMES:
        return True
    # Tab items (other open tabs) — always Chrome shell, never page content
    if e.role == "tabitem":
        return True
    # Tab buttons (titles of other tabs) — heuristic: button at y < 60
    if e.role == "button" and e.bounds[1] < 55 and e.bounds[3] < 60:
        return True
    # Bookmark bar buttons — between the address bar and the page content
    # On 1080p Chrome with bookmark bar visible, buttons sit roughly at y ∈ [100, 150]
    if e.role == "button" and 95 <= e.bounds[1] <= 150 and e.bounds[3] - e.bounds[1] < 45:
        return True
    return False


def _classify(e: Element) -> str:
    n = e.name.lower().strip()
    if any(h == n or n.startswith(h + " ") for h in PRIMARY_HINTS):
        return "primary"
    if e.role == "edit":
        return "input"
    if e.role in ("button", "hyperlink"):
        return "action"
    return "content"


def _detect_regions(elements: list[tuple[str, Element]]) -> dict[str, list[tuple[str, Element]]]:
    """Cluster elements into spatial regions: top / left / main / right / bottom / modal.

    Pure geometry — no site-specific knowledge. Algorithm:
      1. Find page bbox from element bounds.
      2. Top-band  = elements whose y_center is in top 8% of page height.
      3. Bottom-band = bottom 6%.
      4. For the middle band, decide column layout by clustering element x_centers.
         - If most elements (>=70%) fall in one wide central band → single 'main'.
         - Else split into left / main / right by x thresholds (25% / 50% / 25%).
      5. If after filtering, total visible elements is small (<25) AND
         their bounding box is much smaller than page → label all as 'modal'.

    Returns dict keyed by region name; each list is in reading order
    (top-to-bottom, then left-to-right within similar y).
    """
    if not elements:
        return {}

    # Pull bounds
    items = [(eid, e, e.bounds) for eid, e in elements]
    xs_l = [b[0] for _, _, b in items]
    ys_t = [b[1] for _, _, b in items]
    xs_r = [b[2] for _, _, b in items]
    ys_b = [b[3] for _, _, b in items]

    page_left, page_top = min(xs_l), min(ys_t)
    page_right, page_bottom = max(xs_r), max(ys_b)
    page_w = max(1, page_right - page_left)
    page_h = max(1, page_bottom - page_top)

    # Modal detection: few elements concentrated in a small region
    if len(items) <= 25:
        item_w = page_right - page_left
        item_h = page_bottom - page_top
        # If the cluster covers <60% of typical screen width AND item count is small → modal
        # We don't know the screen size, so use a relative test against ratios:
        # if width/height ratio is roughly square-ish and centered, treat as modal
        if item_w * item_h < 1600 * 600 and item_w < 1400:
            return {"modal": _sort_reading(items)}

    top_cutoff = page_top + 0.08 * page_h
    bottom_cutoff = page_bottom - 0.06 * page_h

    middle: list = []
    top_band: list = []
    bottom_band: list = []
    for eid, e, (l, t, r, b) in items:
        y_center = (t + b) / 2
        if y_center <= top_cutoff:
            top_band.append((eid, e, (l, t, r, b)))
        elif y_center >= bottom_cutoff:
            bottom_band.append((eid, e, (l, t, r, b)))
        else:
            middle.append((eid, e, (l, t, r, b)))

    regions: dict[str, list[tuple[str, Element]]] = {}
    if top_band:
        regions["top"] = _sort_reading(top_band)

    # Column detection on middle band
    if middle:
        x_centers = [(l + r) / 2 for _, _, (l, t, r, b) in middle]
        # Page-relative thresholds
        left_thresh = page_left + 0.30 * page_w
        right_thresh = page_left + 0.70 * page_w

        in_center = sum(1 for x in x_centers if left_thresh <= x <= right_thresh)
        single_column = in_center / len(x_centers) >= 0.70

        if single_column:
            regions["main"] = _sort_reading(middle)
        else:
            left_col, main_col, right_col = [], [], []
            for tup in middle:
                _, _, (l, t, r, b) = tup
                xc = (l + r) / 2
                if xc < left_thresh:
                    left_col.append(tup)
                elif xc > right_thresh:
                    right_col.append(tup)
                else:
                    main_col.append(tup)
            if left_col:
                regions["left"] = _sort_reading(left_col)
            if main_col:
                regions["main"] = _sort_reading(main_col)
            if right_col:
                regions["right"] = _sort_reading(right_col)

    if bottom_band:
        regions["bottom"] = _sort_reading(bottom_band)

    return regions


def _sort_reading(items: list[tuple[str, Element, tuple]]) -> list[tuple[str, Element]]:
    """Sort elements in reading order: by y first (rounded to 30px rows), then by x.

    Rounding y prevents elements that are visually on the same row from
    flipping order due to a 2-pixel difference in their y position.
    """
    def key(tup):
        _, _, (l, t, r, b) = tup
        return (t // 30, l)
    items_sorted = sorted(items, key=key)
    return [(eid, e) for eid, e, _ in items_sorted]


def _layout_label(regions: dict) -> str:
    keys = [k for k in ("top", "left", "main", "right", "bottom", "modal") if k in regions]
    if "modal" in keys:
        return "modal dialog"
    if keys == ["main"] or (keys == ["top", "main"]):
        return "single-column"
    return " + ".join(keys)


def _format_element(eid: str, e: Element, max_name: int = 60) -> str:
    name = e.name[:max_name]
    if len(e.name) > max_name:
        name += "…"
    name_q = f'"{name}"'
    val = ""
    if e.value:
        v = e.value[:40] + ("…" if len(e.value) > 40 else "")
        val = f' = "{v}"'
    return f"  {eid}  {e.role:8} {name_q}{val}"


def _take_snapshot(include_unnamed: bool = True, include_text: bool | None = None) -> Snapshot:
    if include_text is None:
        include_text = _state.get("include_text", False)
    obs = observe.observe(
        window_title=_state["window"],
        include_unnamed=include_unnamed,
        include_text=include_text,
    )
    snap = Snapshot(elements=obs.elements, window_title=obs.window_title)
    for e in obs.elements:
        eid = _stable_id(e)
        snap.by_id[eid] = e
    _state["snapshot"] = snap
    return snap


def set_include_text(enabled: bool) -> None:
    """Toggle whether snapshots include long static text (job descriptions, articles)."""
    _state["include_text"] = enabled


SUBMIT_VERBS = {
    "post", "submit", "publish", "save", "confirm", "done", "ok",
    # Excluded: send/reply/comment/share/tweet — these are also feed/post action
    # buttons on social sites and produce false-positive [SUBMIT] tags everywhere.
    # The composer's actual submit button is virtually always one of the above.
}


def _is_submit_button(e: Element) -> bool:
    if e.role != "button":
        return False
    n = e.name.strip().lower()
    return n in SUBMIT_VERBS


def _format_element_with_tag(eid: str, e: Element, max_name: int = 60) -> str:
    """Format element with an inline classification tag (only for noteworthy classes)."""
    cls = _classify(e)
    tag = ""
    if _is_submit_button(e):
        tag = " [SUBMIT]"
    elif cls == "primary":
        tag = " [primary]"
    elif cls == "input":
        tag = " [input]"
    return _format_element(eid, e, max_name=max_name) + tag


def view(include_chrome: bool = False, max_per_region: int = 30) -> str:
    """Compact summary of the current window's interactables, organized by spatial region.

    Hides browser-chrome shell by default. Detects layout regions
    (top / left / main / right / bottom, or modal) from element geometry,
    then lists each region's elements in reading order.

    The LLM should use region names to disambiguate: e.g. "the first post in the
    feed" usually means the first content element under [main], not [right]
    (which is typically a sidebar with recommendations/ads).
    """
    snap = _take_snapshot()
    visible = [(eid, e) for eid, e in snap.by_id.items()
               if include_chrome or not _is_chrome_shell(e)]

    regions = _detect_regions(visible)

    lines = [f"Window: {snap.window_title!r}"]
    lines.append(f"{len(visible)} elements visible — layout: {_layout_label(regions)}")

    region_order = ("top", "left", "main", "right", "bottom", "modal")
    for region in region_order:
        items = regions.get(region, [])
        if not items:
            continue

        # Lift submit-style buttons to the top of the region so the LLM can't miss them
        submits = [(eid, e) for eid, e in items if _is_submit_button(e)]
        rest = [(eid, e) for eid, e in items if not _is_submit_button(e)]
        ordered = submits + rest

        lines.append(f"\n[{region}] ({len(items)})")
        shown = ordered[:max_per_region]
        for eid, e in shown:
            lines.append(_format_element_with_tag(eid, e))
        if len(ordered) > max_per_region:
            lines.append(f"  ... +{len(ordered) - max_per_region} more in [{region}] — use find() to search")

    return "\n".join(lines)


def find(query: str, max_results: int = 10) -> str:
    """Substring search (case-insensitive) across the current snapshot's element names.

    If no snapshot exists, takes one first.
    """
    if _state["snapshot"] is None:
        _take_snapshot()

    snap: Snapshot = _state["snapshot"]
    q = query.lower()
    matches = [(eid, e) for eid, e in snap.by_id.items()
               if q in e.name.lower() or (e.value and q in e.value.lower())]

    if not matches:
        # Take a fresh snapshot in case the page changed
        snap = _take_snapshot()
        matches = [(eid, e) for eid, e in snap.by_id.items()
                   if q in e.name.lower() or (e.value and q in e.value.lower())]

    if not matches:
        return f"No elements matching {query!r}."

    lines = [f"{len(matches)} match(es) for {query!r}:"]
    for eid, e in matches[:max_results]:
        lines.append(_format_element(eid, e))
    if len(matches) > max_results:
        lines.append(f"  ... +{len(matches) - max_results} more")
    return "\n".join(lines)


def resolve(eid: str) -> Optional[Element]:
    """Look up an element by stable id from the most recent snapshot."""
    if _state["snapshot"] is None:
        _take_snapshot()
    return _state["snapshot"].get(eid)


def find_or_scroll_to(query: str, max_scrolls: int = 8, role: str | None = None) -> str:
    """Find an element matching `query`. If not found, send Page Down once and retry.

    Uses keyboard-based scroll (Page Down) which goes to the focused element —
    works reliably inside modal panels where the mouse cursor may not be hovering
    the scroll surface. Stops the moment the target appears.

    Generic primitive for any "below the fold" element: Copy buttons, Submit at
    bottom of long forms, footer links, etc.
    """
    import act  # local import to avoid circular if any
    q = query.lower()

    for attempt in range(max_scrolls + 1):
        snap = _take_snapshot()
        matches = [(eid, e) for eid, e in snap.by_id.items()
                   if (q in e.name.lower() or (e.value and q in e.value.lower()))
                   and (role is None or e.role == role)]
        if matches:
            lines = [f"Found after {attempt} scroll(s). {len(matches)} match(es):"]
            for eid, e in matches[:5]:
                lines.append(_format_element(eid, e))
            return "\n".join(lines)
        if attempt < max_scrolls:
            act.scroll(1, method="key")  # one Page Down per attempt
            time.sleep(0.3)

    return f"Not found: no element matching {query!r} (role={role}) after {max_scrolls} scrolls."


def diff_since_last() -> str:
    """Take a fresh snapshot, return the elements that newly appeared since the previous one."""
    prev = _state["snapshot"]
    if prev is None:
        # First call — same as view()
        return view()
    prev_keys = {(e.role, e.name, e.bounds) for e in prev.elements}
    snap = _take_snapshot()
    new_elems = [(eid, e) for eid, e in snap.by_id.items()
                 if (e.role, e.name, e.bounds) not in prev_keys
                 and not _is_chrome_shell(e)]
    if not new_elems:
        return "No new elements appeared."
    return _format_diff(new_elems, prefix="new element(s)")


def _format_diff(new_elems: list[tuple[str, Element]], prefix: str = "new element(s) after wait", cap: int = 25) -> str:
    """Render a diff with submit-tagged elements lifted, and a tail message if truncated."""
    submits = [(eid, e) for eid, e in new_elems if _is_submit_button(e)]
    rest = [(eid, e) for eid, e in new_elems if not _is_submit_button(e)]
    ordered = submits + rest
    lines = [f"{len(new_elems)} {prefix}:"]
    for eid, e in ordered[:cap]:
        lines.append(_format_element_with_tag(eid, e))
    if len(ordered) > cap:
        lines.append(f"  ... +{len(ordered) - cap} more — call view_screen() if you need the full picture")
    return "\n".join(lines)


def wait_for_diff(timeout: float = 5.0, role: Optional[str] = None,
                  name_substr: Optional[str] = None, min_count: int = 1) -> str:
    """Poll until at least min_count new elements appear (optionally filtered).

    Returns formatted diff or a timeout message.
    """
    prev = _state["snapshot"]
    if prev is None:
        prev_elems: list[Element] = []
    else:
        prev_elems = prev.elements

    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = _take_snapshot()
        prev_keys = {(e.role, e.name, e.bounds) for e in prev_elems}
        new_elems = [(eid, e) for eid, e in snap.by_id.items()
                     if (e.role, e.name, e.bounds) not in prev_keys
                     and not _is_chrome_shell(e)]
        if role:
            new_elems = [(eid, e) for eid, e in new_elems if e.role == role]
        if name_substr:
            n = name_substr.lower()
            new_elems = [(eid, e) for eid, e in new_elems if n in e.name.lower()]
        if len(new_elems) >= min_count:
            return _format_diff(new_elems)
        time.sleep(0.4)
    return f"Timed out after {timeout}s waiting for new elements (role={role}, name={name_substr!r})."
