"""Apply-form filling — extracted from the legacy upwork_apply.py.

Slim port that exposes the function signatures listed in the Phase-1 plan.
The 'never auto-submit' rule is enforced by the caller, not here:
submit_proposal exists, but whether it's invoked is a higher-layer decision.

Caveats:
  - fill_bid_amount is a starter implementation; the legacy upwork_apply.py
    intentionally avoided the bid field (TODO leave for human review).
  - answer_screening_questions takes a {label: answer} dict; localization uses
    the same UIA-label + 80px-offset trick as the legacy code.
"""
from __future__ import annotations

import time
from typing import Optional

import pyperclip

from substrate import act, observe


_LOGIN_SIGNALS = (
    "log in to upwork",
    "sign in to upwork",
    "log in to your account",
    "continue with email",
    "continue with google",
    "continue with apple",
)
_CLOUDFLARE_SIGNALS = ("just a moment", "checking your browser", "verify you are human")
_FORM_ANCHOR = "submit a proposal"


def navigate_to_apply(window_title: str, job_url: str) -> None:
    """Focus the Chrome window, open a new tab, navigate to job_url."""
    if not act.focus_window(window_title):
        # fallback to any Chrome window
        act.focus_window("Google Chrome")
    time.sleep(0.3)
    act.key("ctrl+t")
    time.sleep(0.6)
    act.key("ctrl+l")
    time.sleep(0.2)
    act.type_text(job_url)
    time.sleep(0.15)
    act.key("enter")


def wait_for_form_or_login(window_title: str, timeout_s: int = 30) -> str:
    """Poll the UIA tree. Returns one of: 'ready', 'login_required', 'cloudflare'.
    'ready' means the apply form anchor (Submit a Proposal) is present.
    'login_required' is returned only if no form appears within timeout AND
    a login-form signal is detected. 'cloudflare' means a challenge is showing
    when the timeout expires.
    """
    deadline = time.time() + float(timeout_s)
    last_state = "unknown"
    while time.time() < deadline:
        try:
            obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
        except Exception:
            time.sleep(1.0)
            continue
        names = [(e.name or "").strip().lower() for e in obs.elements]
        if any(_FORM_ANCHOR in n for n in names if n):
            return "ready"
        if any(any(sig in n for sig in _LOGIN_SIGNALS) for n in names if n):
            last_state = "login_required"
        elif any(any(sig in n for sig in _CLOUDFLARE_SIGNALS) for n in names if n):
            last_state = "cloudflare"
        time.sleep(1.5)
    if last_state in ("login_required", "cloudflare"):
        return last_state
    # final probe
    try:
        obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
        names = [(e.name or "").strip().lower() for e in obs.elements]
        if any(any(sig in n for sig in _LOGIN_SIGNALS) for n in names if n):
            return "login_required"
        if any(any(sig in n for sig in _CLOUDFLARE_SIGNALS) for n in names if n):
            return "cloudflare"
    except Exception:
        pass
    return "cloudflare"


def _find_cover_letter_textarea(window_title: str):
    """Largest editable element below the topmost 'Cover letter' text label."""
    obs = observe.observe(window_title=window_title, include_unnamed=True, include_text=True)
    label_y: Optional[int] = None
    for e in obs.elements:
        if e.role == "text" and "cover letter" in (e.name or "").strip().lower():
            label_y = e.bounds[1] if label_y is None else min(label_y, e.bounds[1])
    if label_y is None:
        return None
    edits = [
        e for e in obs.elements
        if e.role.lower() in {"edit", "document"} and e.bounds[1] >= label_y
    ]
    if not edits:
        return None
    edits.sort(
        key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]),
        reverse=True,
    )
    return edits[0]


def paste_cover_letter(window_title: str, text: str) -> None:
    """PageDown until the Cover Letter textarea appears, click it, paste via clipboard."""
    max_scrolls = 8
    for attempt in range(max_scrolls + 1):
        el = _find_cover_letter_textarea(window_title)
        if el is not None:
            act.click(el)
            time.sleep(0.4)
            pyperclip.copy(text)
            time.sleep(0.2)
            act.key("ctrl+v")
            time.sleep(0.5)
            return
        if attempt < max_scrolls:
            act.scroll(1, method="key")
            time.sleep(0.5)


def _scroll_to_label(window_title: str, label_text: str, max_scrolls: int = 15) -> bool:
    needle = label_text.lower()[:80]
    act.key("ctrl+home")
    time.sleep(0.6)
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
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


def _label_bounds(window_title: str, label_text: str):
    needle = label_text.lower()[:80]
    try:
        obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
    except Exception:
        return None
    for e in obs.elements:
        if e.role == "text" and needle in (e.name or "").strip().lower():
            return e.bounds
    return None


def answer_screening_questions(window_title: str, answers: dict[str, str]) -> None:
    """For each {label: answer}: scroll to label, click ~80px below the label
    (lands inside the textarea), paste the answer."""
    offset_y = 80
    for label, answer in answers.items():
        if not _scroll_to_label(window_title, label):
            continue
        time.sleep(0.4)
        bounds = _label_bounds(window_title, label)
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


def select_never_for_rate_increase(window_title: str) -> None:
    """Open the rate-increase frequency dropdown and pick 'Never'."""
    act.key("ctrl+home")
    time.sleep(0.6)
    needle = "select a frequency"
    dropdown_text_el = None
    max_scrolls = 8
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
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
        return
    l, t, r, b = dropdown_text_el.bounds
    cx, cy = (l + r) // 2, (t + b) // 2
    act.click_xy(cx, cy)
    time.sleep(0.8)
    try:
        obs2 = observe.observe(window_title=window_title, include_unnamed=False, include_text=True)
    except Exception:
        return
    for e in obs2.elements:
        if e.role == "listitem" and (e.name or "").strip().lower() == "never":
            act.click(e)
            time.sleep(0.5)
            return


def fill_bid_amount(window_title: str, amount_usd: float) -> None:
    """Locate the bid-amount input via 'Bid' label and type the amount.

    Caveat: legacy upwork_apply.py deliberately avoided this field. This is a
    starter implementation; verify in integration before production use.
    """
    obs = observe.observe(window_title=window_title, include_unnamed=True, include_text=True)
    label_y: Optional[int] = None
    for e in obs.elements:
        n = (e.name or "").strip().lower()
        if e.role == "text" and ("your bid" in n or n.startswith("bid ") or n == "bid"):
            label_y = e.bounds[1] if label_y is None else min(label_y, e.bounds[1])
    if label_y is None:
        return
    edits = [
        e for e in obs.elements
        if e.role.lower() in {"edit", "spinbutton"} and abs(e.bounds[1] - label_y) < 200
    ]
    if not edits:
        return
    edits.sort(key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]), reverse=True)
    target = edits[0]
    act.click(target)
    time.sleep(0.3)
    act.key("ctrl+a")
    time.sleep(0.1)
    act.type_text(f"{amount_usd:.2f}")
    time.sleep(0.3)


def submit_proposal(window_title: str) -> None:
    """Click the final 'Send' / 'Submit' button. Caller is responsible for the
    never-auto-submit policy — this function unconditionally clicks if found.
    """
    obs = observe.observe(window_title=window_title, include_unnamed=False, include_text=False)
    for e in obs.elements:
        if e.role == "button":
            n = (e.name or "").strip().lower()
            if n in {"send", "submit", "submit a proposal", "send proposal"} or n.startswith("send for"):
                act.click(e)
                time.sleep(1.0)
                return
