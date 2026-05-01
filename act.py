"""
Action primitives for the computer-use agent.

All actions go through OS-level SendInput (via pyautogui), not CDP, so they
work on any normally-running Chrome window without browser-side detection.

Coordinates are screen pixels. Element bounds are (left, top, right, bottom).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import pyautogui
import uiautomation as uia

import pacing

pyautogui.FAILSAFE = True   # fling cursor to top-left to abort
pyautogui.PAUSE = 0.0


class FocusLost(RuntimeError):
    """Raised when the target window cannot be brought to / verified as foreground."""


# Module-level "what window are we automating right now". Set by
# set_target_window(); read by require_focus(); used by every input primitive
# (click/key/type/scroll/navigate) to verify focus before sending input.
# Stored as a tuple of acceptable substrings — focus is OK if ANY of them match.
_TARGET_WINDOWS: tuple[str, ...] = ()


def set_target_window(title_substr) -> None:
    """Declare the window(s) every input primitive must verify focus against.

    Accepts either a single string (legacy) or a tuple/list of acceptable
    substrings — useful when the same workflow visits pages with different
    titles (e.g. Upwork's feed says 'Upwork' but the apply page says 'Submit a
    Proposal'). Pass None or an empty tuple to clear (input primitives then
    skip the focus check, falling back to the legacy fire-and-pray behavior).
    """
    global _TARGET_WINDOWS
    if title_substr is None:
        _TARGET_WINDOWS = ()
    elif isinstance(title_substr, str):
        _TARGET_WINDOWS = (title_substr,)
    else:
        _TARGET_WINDOWS = tuple(s for s in title_substr if s)


@dataclass
class Element:
    id: int
    role: str
    name: str
    bounds: tuple[int, int, int, int]
    value: str = ""

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return (l + r) // 2, (t + b) // 2


def _humanish_pause(lo: float = 0.05, hi: float = 0.15) -> None:
    time.sleep(random.uniform(lo, hi))


def _foreground_matches(needle: str) -> bool:
    """Return True if the foreground window's name contains `needle`."""
    try:
        fg = uia.GetForegroundControl()
        if fg is None:
            return False
        name = (fg.Name or "")
        return needle.lower() in name.lower()
    except Exception:
        return False


def focus_window(title_substr: str, timeout: float = 2.0) -> bool:
    """Bring a window matching the title substring to the foreground.

    Legacy interface kept for direct callers. Returns True on success.
    Prefer require_focus() at input-primitive call sites.
    """
    if not title_substr:
        return False
    deadline = time.time() + timeout
    needle = title_substr[:30]
    while time.time() < deadline:
        try:
            win = uia.WindowControl(searchDepth=1, SubName=needle)
            if win.Exists(0.3):
                win.SetActive()
                _humanish_pause(0.1, 0.2)
                if _foreground_matches(needle):
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def require_focus(title_substr=None, timeout: float = 2.0) -> None:
    """Try-then-verify: bring an acceptable target window to the foreground,
    then verify the foreground window actually matches one of them.
    Raises FocusLost if it can't be confirmed within `timeout`.

    `title_substr` accepts a string, a tuple of strings, or None (uses the
    module-level configured targets). If no targets are configured, this is
    a no-op.
    """
    if title_substr is None:
        targets = _TARGET_WINDOWS
    elif isinstance(title_substr, str):
        targets = (title_substr,)
    else:
        targets = tuple(s for s in title_substr if s)
    if not targets:
        return  # No targets configured — skip the check
    # Fast path: foreground already matches one of the acceptable titles
    for t in targets:
        if _foreground_matches(t[:30]):
            return
    # Try each target in turn
    for t in targets:
        if focus_window(t, timeout=timeout):
            return
    # Final verification chance after the retry loops
    for t in targets:
        if _foreground_matches(t[:30]):
            return
    try:
        fg = uia.GetForegroundControl()
        fg_name = (fg.Name or "<unknown>") if fg is not None else "<none>"
    except Exception:
        fg_name = "<error>"
    raise FocusLost(
        f"Cannot confirm any of {targets!r} is foreground after {timeout}s; "
        f"current foreground: {fg_name!r}"
    )


def move_to(x: int, y: int) -> None:
    duration = random.uniform(0.12, 0.28)
    pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeInOutQuad)


def click(element: Element, button: str = "left") -> None:
    require_focus()
    pacer = pacing.get_pacer()
    pacer.before_action("click")
    x, y = pacer.jitter_click_target(element.bounds)
    move_to(x, y)
    _humanish_pause()
    pyautogui.click(button=button)
    _humanish_pause(0.08, 0.18)


def click_xy(x: int, y: int, button: str = "left") -> None:
    require_focus()
    pacing.get_pacer().before_action("click")
    move_to(x, y)
    _humanish_pause()
    pyautogui.click(button=button)
    _humanish_pause(0.08, 0.18)


def type_text(text: str, per_char_min: float = 0.02, per_char_max: float = 0.08) -> None:
    """Type with humanish per-character delays. Assumes target already focused."""
    require_focus()
    pacing.get_pacer().before_action("type")
    for ch in text:
        if ch == "\n":
            pyautogui.press("enter")
        else:
            pyautogui.write(ch)
        time.sleep(random.uniform(per_char_min, per_char_max))


def click_and_type(element: Element, text: str) -> None:
    click(element)
    _humanish_pause(0.1, 0.2)
    type_text(text)


def key(combo: str) -> None:
    """Press a key combination, e.g. 'ctrl+l', 'enter', 'esc', 'ctrl+shift+t'."""
    require_focus()
    pacing.get_pacer().before_action("key")
    parts = [p.strip().lower() for p in combo.split("+")]
    if len(parts) == 1:
        pyautogui.press(parts[0])
    else:
        pyautogui.hotkey(*parts)
    _humanish_pause()


def navigate(url: str) -> None:
    """Address-bar navigation. Assumes Chrome is focused."""
    key("ctrl+l")
    _humanish_pause(0.1, 0.2)
    type_text(url)
    _humanish_pause(0.05, 0.12)
    key("enter")


def scroll(amount: int, method: str = "key") -> None:
    """Scroll the focused window/panel.

    Positive amount = down, negative = up. `amount` is in 'clicks'
    (loosely 1 click = one Page Down or one wheel notch).

    method:
      'key'   — Page Down / Page Up. Goes to focused element. RELIABLE for
                modal panels and any context where the cursor isn't hovering
                the scroll surface. Default.
      'wheel' — mouse wheel at current cursor position. Use when 'key' doesn't
                work (some sites trap arrow keys for navigation).
    """
    require_focus()
    pacing.get_pacer().before_action("scroll")
    if method == "key":
        keyname = "pagedown" if amount > 0 else "pageup"
        for _ in range(abs(amount)):
            pyautogui.press(keyname)
            time.sleep(random.uniform(0.05, 0.12))
    else:
        pyautogui.scroll(-amount * 100)
    _humanish_pause()


def element_from_dict(d: dict) -> Element:
    return Element(
        id=d["id"],
        role=d["role"],
        name=d["name"],
        bounds=tuple(d["bounds"]),  # type: ignore[arg-type]
        value=d.get("value", ""),
    )
