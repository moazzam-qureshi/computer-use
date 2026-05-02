"""Apply-form filling — verbatim port of legacy upwork_apply.py.

Key invariants ported from the working legacy code:
  - TARGET_WINDOWS lists every Chrome title we may legitimately encounter
    during the apply flow: 'Upwork', the new tab title 'New Tab',
    Cloudflare's 'Just a moment', and the apply page title 'Submit a Proposal'.
    Without all of these in the focus allow-list, require_focus() raises
    FocusLost the moment Cloudflare flips the title mid-load.
  - Apply-form readiness is detected by the H1 'Submit a Proposal' anchor.
  - APPLY_FORM_TIMEOUT = 35s. Cloudflare challenges can take 10-30s; legacy
    chose 35 specifically to absorb that.
  - On timeout, raise ApplyFormNotFound. Do NOT misclassify as cloudflare.
  - Login detection is a separate explicit check (detect_login_required).
  - We never touch the bid amount field. The user fills bid manually.
"""
from __future__ import annotations

import time
from typing import Optional

import pyperclip

from substrate import act, observe


# All Chrome window titles that are valid to interact with during the apply
# flow. require_focus() in scheduler.main / run_apply_executor accepts any of
# these as a valid foreground target.
TARGET_WINDOWS = (
    "Upwork",
    "Submit a Proposal",
    "Just a moment",
    "New Tab",
    "Google Chrome",
)

APPLY_FORM_ANCHOR_TEXT = "submit a proposal"
APPLY_FORM_TIMEOUT = 35.0  # generous to absorb Cloudflare challenges (~10-30s)

_LOGIN_SIGNALS = (
    "log in to upwork",
    "sign in to upwork",
    "log in to your account",
    "continue with email",
    "continue with google",
    "continue with apple",
)


class ApplyFormNotFound(Exception):
    pass


class LoginRequired(Exception):
    """Raised when we detect that the user needs to log in before the apply
    flow can proceed. Caller should ping the user and leave the job pending."""


def detect_login_required() -> bool:
    """Return True if the current page looks like a login/sign-in screen.
    Checks for strong signal elements (login form headings, OAuth buttons)."""
    try:
        obs = observe.observe(
            window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
        )
    except Exception:
        return False
    for e in obs.elements:
        name = (e.name or "").strip().lower()
        if not name:
            continue
        if any(sig in name for sig in _LOGIN_SIGNALS):
            return True
    return False


def navigate_to_apply(window_title: str, job_url: str) -> None:
    """Focus any Chrome window, open a new tab, navigate to the apply URL.

    `window_title` is the preferred target (typically 'Upwork') but we fall
    back to any Chrome window because the user may have any tab active when
    we kick off — Ctrl+T works the same regardless.
    """
    focused = False
    for title in TARGET_WINDOWS:
        if act.focus_window(title):
            focused = True
            break
    if not focused:
        if act.focus_window("Google Chrome"):
            focused = True
    if not focused:
        raise RuntimeError("Could not focus any Chrome window. Is Chrome running?")
    time.sleep(0.3)
    act.key("ctrl+t")
    time.sleep(0.6)
    act.key("ctrl+l")
    time.sleep(0.2)
    act.type_text(job_url)
    time.sleep(0.15)
    act.key("enter")


def wait_for_apply_form(timeout: float = APPLY_FORM_TIMEOUT, poll_interval: float = 1.5) -> None:
    """Poll the UIA tree until 'Submit a Proposal' anchor appears. Tolerates
    the Chrome window briefly being titled 'Just a moment...' during a
    Cloudflare challenge (those rounds count as 'still loading').

    Raises ApplyFormNotFound on timeout.
    """
    deadline = time.time() + timeout
    needle = APPLY_FORM_ANCHOR_TEXT.lower()
    while time.time() < deadline:
        try:
            obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
        except Exception:
            time.sleep(poll_interval)
            continue
        for e in obs.elements:
            if needle in (e.name or "").strip().lower():
                return
        time.sleep(poll_interval)
    raise ApplyFormNotFound(
        f"No element containing {APPLY_FORM_ANCHOR_TEXT!r} appeared within {timeout}s"
    )


def wait_for_form_or_login(window_title: str, timeout_s: int = 30) -> str:
    """Adapter for callers that want a string state instead of an exception.

    Returns 'ready', 'login_required', or 'cloudflare'. Used by apply_executor
    which prefers branch-by-string over try/except in the orchestration layer.
    """
    try:
        wait_for_apply_form(timeout=float(timeout_s))
        return "ready"
    except ApplyFormNotFound:
        if detect_login_required():
            return "login_required"
        return "cloudflare"


def _editable_elements(elements):
    editable_roles = {"edit", "document"}
    return [e for e in elements if e.role.lower() in editable_roles]


def _label_y(elements, label_substr: str) -> Optional[int]:
    needle = label_substr.lower()
    candidates = [
        e for e in elements
        if e.role == "text" and needle in (e.name or "").strip().lower()
    ]
    if not candidates:
        return None
    return min(c.bounds[1] for c in candidates)


def _find_cover_letter_textarea():
    """Largest editable in the MAIN column below the 'Cover letter' label.

    The main column spans roughly x ∈ [100, 1250] on a typical Upwork apply
    page. Anything to the right of x=1300 is the right sidebar (which now
    hosts Upwork's 'Uma' AI assistant chat widget — a textarea we must NOT
    confuse for the cover-letter input). The legacy upwork_apply.py predates
    Uma so it didn't need this filter; we add it explicitly.
    """
    obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=True, include_text=True)
    label_y = _label_y(obs.elements, "cover letter")
    if label_y is None:
        return None

    main_column_x_max = 1280  # right edge of the apply page's main content column
    edits = [
        e for e in _editable_elements(obs.elements)
        if e.bounds[1] >= label_y
        and e.bounds[0] < main_column_x_max
        and (e.bounds[2] - e.bounds[0]) >= 300  # cover letter textarea is wide
    ]
    if not edits:
        return None
    edits.sort(
        key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]),
        reverse=True,
    )
    return edits[0]


def paste_cover_letter(window_title: str, text: str) -> bool:
    """PageDown until the Cover Letter textarea appears, click it, paste via clipboard.
    Returns True on success, False if the textarea wasn't found.
    """
    max_scrolls = 8
    for attempt in range(max_scrolls + 1):
        el = _find_cover_letter_textarea()
        if el is not None:
            act.click(el)
            time.sleep(0.4)
            pyperclip.copy(text)
            time.sleep(0.2)
            act.key("ctrl+v")
            time.sleep(0.5)
            return True
        if attempt < max_scrolls:
            act.scroll(1, method="key")
            time.sleep(0.5)
    return False


def _scroll_to_label(label_text: str, max_scrolls: int = 15) -> bool:
    needle = label_text.lower()[:80]
    act.key("ctrl+home")
    time.sleep(0.6)
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
        except Exception:
            time.sleep(0.5)
            continue
        for e in obs.elements:
            if e.role == "text" and needle in (e.name or "").strip().lower():
                return True
        if i < max_scrolls:
            act.scroll(1, method="key")
            time.sleep(0.5)
    return False


def _label_bounds(label_text: str):
    needle = label_text.lower()[:80]
    try:
        obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
    except Exception:
        return None
    for e in obs.elements:
        if e.role == "text" and needle in (e.name or "").strip().lower():
            return e.bounds
    return None


def answer_screening_questions(window_title: str, answers: dict) -> int:
    """For each {label: answer}: scroll to label, click ~80px below the label
    (lands inside the textarea), paste the answer. Returns count answered.
    """
    if not answers:
        return 0
    offset_y = 80
    answered = 0
    for label, answer in answers.items():
        if not _scroll_to_label(label):
            continue
        time.sleep(0.4)
        bounds = _label_bounds(label)
        if bounds is None:
            continue
        l, t, r, b = bounds
        cx = (l + r) // 2
        cy = b + offset_y
        act.click_xy(cx, cy)
        time.sleep(0.4)
        pyperclip.copy(answer)
        time.sleep(0.2)
        act.key("ctrl+v")
        time.sleep(0.5)
        answered += 1
    return answered


def select_never_for_rate_increase(window_title: str) -> bool:
    """Open the rate-increase frequency dropdown and pick 'Never'.
    Upwork blocks proposal submission unless this dropdown is set.
    Returns True on success.
    """
    act.key("ctrl+home")
    time.sleep(0.6)
    needle = "select a frequency"
    dropdown_text_el = None
    max_scrolls = 8
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
        except Exception:
            time.sleep(0.5)
            continue
        for e in obs.elements:
            if e.role == "text" and needle in (e.name or "").strip().lower():
                dropdown_text_el = e
                break
        if dropdown_text_el is not None:
            break
        if i < max_scrolls:
            act.scroll(1, method="key")
            time.sleep(0.5)

    if dropdown_text_el is None:
        return False

    l, t, r, b = dropdown_text_el.bounds
    cx, cy = (l + r) // 2, (t + b) // 2
    act.click_xy(cx, cy)
    time.sleep(0.8)

    try:
        obs2 = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
    except Exception:
        return False
    for e in obs2.elements:
        if e.role == "listitem" and (e.name or "").strip().lower() == "never":
            act.click(e)
            time.sleep(0.5)
            return True
    return False


def fill_bid_amount(window_title: str, amount_usd: float) -> None:
    """NO-OP. Legacy upwork_apply.py deliberately did not touch the bid field
    (per CLAUDE.md hard rules). Keeping this signature so the apply_executor
    contract is unchanged, but we never write to the bid input — the user
    fills it manually after reviewing the staged form.
    """
    return


def submit_proposal(window_title: str) -> None:
    """Click the final 'Send' / 'Submit' button. Caller is responsible for the
    never-auto-submit policy — this function unconditionally clicks if found.
    """
    obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=False)
    for e in obs.elements:
        if e.role == "button":
            n = (e.name or "").strip().lower()
            if n in {"send", "submit", "submit a proposal", "send proposal"} or n.startswith("send for"):
                act.click(e)
                time.sleep(1.0)
                return
