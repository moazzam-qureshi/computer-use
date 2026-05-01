"""
LangChain tools the agent calls to drive the computer.

Each tool is a thin wrapper over view.py + act.py with a clear, narrow
docstring — that docstring is what the LLM reads to decide when to use
each tool, so it should be precise and short.
"""
from __future__ import annotations

import functools
import os
import time
from pathlib import Path

import uiautomation as uia
from langchain_core.tools import tool

import act
import pacing
import view

# Strings the agent must never click, ever. Substring match, case-insensitive.
# Protects against accidental Apply / Submit Proposal / Pay buttons during scans.
CLICK_BLOCKLIST: list[str] = []


def set_click_blocklist(blocked: list[str]) -> None:
    """Replace the click blocklist (e.g. ['apply now', 'submit proposal'])."""
    global CLICK_BLOCKLIST
    CLICK_BLOCKLIST = [b.lower() for b in blocked]


def _with_com(fn):
    """Initialize COM/UIA on the current thread for the duration of the call.

    LangChain dispatches tool calls on a worker thread; UIAutomation needs COM
    initialized per-thread or it errors with 'CoInitialize has not been called'.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with uia.UIAutomationInitializerInThread(debug=False):
            return fn(*args, **kwargs)
    return wrapper


@tool
@_with_com
def view_screen(include_chrome: bool = False) -> str:
    """Return a compact summary of the interactable elements in the current window.

    Use this once at the start of a task to see the page layout. Each element
    has a stable id (e.g. 'b7a3f2') you can pass to click(). The browser's
    own window controls and tabs are hidden by default — set include_chrome=True
    only if you need them.

    Returns a multi-section summary: primary actions, inputs, other actions, content.
    """
    return view.view(include_chrome=include_chrome)


@tool
@_with_com
def find(query: str, max_results: int = 10) -> str:
    """Search the current view for elements whose name or value contains `query` (case-insensitive).

    Cheaper than re-viewing the whole page when you know what you're looking for.
    Returns up to `max_results` matches with their stable ids.
    """
    return view.find(query, max_results=max_results)


@tool
@_with_com
def click(element_id: str) -> str:
    """Click an element by its stable id (e.g. 'b7a3f2').

    The element must come from a recent view_screen() or find() result.
    Returns 'clicked: <name>' on success, or an error if the id is not found
    or the element matches the safety blocklist for this task.
    """
    el = view.resolve(element_id)
    if el is None:
        return f"Error: no element with id {element_id!r} in current snapshot. Call view_screen() or find() first."

    name_l = el.name.lower()
    for blocked in CLICK_BLOCKLIST:
        if blocked in name_l:
            return (
                f"BLOCKED: refused to click {el.name!r} — matches safety blocklist "
                f"({blocked!r}). This task is not allowed to perform that action."
            )

    win = view.get_window()
    if win:
        act.focus_window(win)
    act.click(el)
    return f"clicked: {el.role} {el.name!r}"


@tool
@_with_com
def type_text(text: str) -> str:
    """Type `text` into the currently focused element (usually whatever you just clicked).

    Use after clicking into an input or text editor. Does NOT click first —
    if you need to focus a specific input, click(its_id) first, then type_text(...).
    """
    win = view.get_window()
    if win:
        act.focus_window(win)
        time.sleep(0.2)
    act.type_text(text)
    return f"typed {len(text)} chars"


@tool
@_with_com
def press_key(combo: str) -> str:
    """Press a single key or key combination, e.g. 'enter', 'esc', 'tab', 'ctrl+l', 'ctrl+a'.

    Use for shortcuts and submission keys. Goes to the currently focused element.
    """
    win = view.get_window()
    if win:
        act.focus_window(win)
    act.key(combo)
    return f"pressed: {combo}"


@tool
@_with_com
def navigate(url: str) -> str:
    """Navigate the browser to `url` via the address bar. Use this instead of
    clicking through links when you know the destination URL.
    """
    win = view.get_window()
    if win:
        act.focus_window(win)
    act.navigate(url)
    time.sleep(1.5)  # let the page start loading before next action
    return f"navigated to: {url}"


@tool
@_with_com
def wait_for_change(seconds: float = 5.0, role_filter: str = "", name_contains: str = "") -> str:
    """Wait for new elements to appear since the last view, then return them.

    Use AFTER an action that opens a modal, dialog, dropdown, or causes content
    to render asynchronously (e.g. clicking 'Compose' opens a dialog you need
    to wait for). Returns only the new elements, not the whole page.

    `role_filter`: optional, e.g. 'edit' to wait for a textbox specifically.
    `name_contains`: optional substring filter on the element name.
    """
    return view.wait_for_diff(
        timeout=seconds,
        role=role_filter or None,
        name_substr=name_contains or None,
    )


@tool
def read_clipboard() -> str:
    """Read the current contents of the system clipboard.

    Use this AFTER clicking a 'Copy', 'Copy link', or 'Copy to clipboard' button
    to read the value that was copied. This is the most reliable way to get a
    URL from a page when the URL is shown in a textbox whose value isn't readable
    via the accessibility tree.

    Returns the clipboard string, or an error if it's empty.
    """
    import pyperclip
    try:
        text = pyperclip.paste()
    except Exception as ex:
        return f"Error reading clipboard: {ex}"
    if not text:
        return "Clipboard is empty."
    return text[:2000]


@tool
@_with_com
def read_full_text(element_id: str) -> str:
    """Return the full untruncated text (name + value) of an element.

    The view_screen output truncates names to 60 chars to save tokens. When you
    need to read a job description, message body, or article paragraph in full,
    use this. Returns the whole string, up to 4000 chars.
    """
    el = view.resolve(element_id)
    if el is None:
        return f"Error: no element with id {element_id!r} in current snapshot."
    parts = []
    if el.name:
        parts.append(el.name)
    if el.value and el.value != el.name:
        parts.append(f"[value] {el.value}")
    text = "\n".join(parts)
    return text[:4000] if text else "(empty)"


@tool
@_with_com
def find_or_scroll_to(query: str, max_scrolls: int = 8, role: str = "") -> str:
    """Find an element matching `query`. If not found in the current view, scroll
    down in small steps and retry until found or `max_scrolls` is reached.

    Use this instead of separate scroll + view + find when looking for something
    that might be below the fold — e.g. a 'Copy link' button at the bottom of a
    detail panel, a 'Submit' button at the end of a long form. Stops as soon as
    the target appears, so it doesn't over-scroll past it.

    `role`: optional role filter ('button', 'edit', 'hyperlink', etc.).
    """
    role_arg = role if role else None
    return view.find_or_scroll_to(query, max_scrolls=max_scrolls, role=role_arg)


@tool
@_with_com
def scroll_page(direction: str = "down", amount: int = 3, method: str = "key") -> str:
    """Scroll the current window or focused panel.

    direction: 'down' or 'up'.
    amount: number of Page Down/Up presses (or wheel notches) — 1=small, 5=medium.
    method:
      'key'   — Page Down/Up. Default. Works inside modal panels (the cursor
                doesn't need to be over the scroll surface).
      'wheel' — mouse wheel at current cursor position. Use only if keyboard
                scrolling doesn't work on the site (rare).

    Returns a confirmation. Adds a brief pause to let the page render.
    """
    n = max(1, min(20, int(amount)))
    delta = n if direction.lower() == "down" else -n
    win = view.get_window()
    if win:
        act.focus_window(win)
    act.scroll(delta, method=method)
    time.sleep(0.6)
    return f"scrolled {direction} by {n} ({method})"


@tool
@_with_com
def enable_rich_text() -> str:
    """Enable inclusion of long static text (paragraphs, descriptions) in subsequent view_screen calls.

    Off by default to save tokens. Turn on when scanning content-heavy pages
    like job listings, articles, or message threads.
    """
    view.set_include_text(True)
    return "rich-text mode enabled — view_screen will include long static text"


@tool
def save_to_file(path: str, content: str, mode: str = "append") -> str:
    """Save content to a file inside the project directory.

    Use this to record findings (e.g. relevant job links, summaries, notes).
    `path` is relative to the project root. `mode` is 'append' or 'overwrite'.
    Refuses paths that escape the project directory or use absolute paths.
    """
    project_root = Path(__file__).resolve().parent
    target = (project_root / path).resolve()
    try:
        target.relative_to(project_root)
    except ValueError:
        return f"Error: path {path!r} escapes the project directory. Use a relative path."

    target.parent.mkdir(parents=True, exist_ok=True)
    write_mode = "a" if mode == "append" else "w"
    with open(target, write_mode, encoding="utf-8") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")
    return f"saved {len(content)} chars to {path} (mode={mode})"


@tool
def pacing_status() -> str:
    """Return how many actions have been used in the last hour vs the budget.

    Useful when a task is long-running and the agent wants to decide whether
    to continue scanning more items or wrap up.
    """
    s = pacing.get_pacer().stats()
    return f"actions in last hour: {s['actions_last_hour']} / {s['budget']}"


@tool
@_with_com
def set_target_window(title_substring: str) -> str:
    """Set the target window for all subsequent observe/click/type actions.

    Pass a substring of the window title, e.g. 'LinkedIn' or 'Gmail'.
    Must be called once at the start of every task before view_screen().
    """
    view.set_window(title_substring)
    if not act.focus_window(title_substring):
        return f"Set target to {title_substring!r} but could not focus a window with that title — make sure it's open."
    return f"target window set to: {title_substring!r}"


ALL_TOOLS = [
    set_target_window,
    view_screen,
    find,
    click,
    type_text,
    press_key,
    navigate,
    wait_for_change,
    read_full_text,
    read_clipboard,
    find_or_scroll_to,
    scroll_page,
    enable_rich_text,
    save_to_file,
    pacing_status,
]
