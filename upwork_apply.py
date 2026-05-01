"""
Phase-1 Upwork apply driver.

Reads the next pending job from jobs/pending/, navigates to the apply page,
fills cover letter + screening question answers, then STOPS before bid/Submit
and pings Discord for human review.

HARD RULES (enforced in code):
  - Never clicks Submit.
  - Never touches the bid amount field.
  - Validates the apply URL against a strict allowlist before navigation.
  - One job per run. No batch loop.

Usage:
    uv run upwork_apply.py                # newest pending from today
    uv run upwork_apply.py --job <id>     # specific job (override date filter)
    uv run upwork_apply.py --dry-run      # no pasting, no JSON move, no Discord
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# UTF-8 console output (cover letters / answers can have unicode)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pyperclip
from dotenv import load_dotenv

import act
import db
import notify
import observe
import proposal
import vision

WINDOW = "Upwork"
# Acceptable Chrome window titles during the apply workflow. The apply page
# renders with title "Submit a Proposal", but Cloudflare may briefly show
# "Just a moment..." while challenging the request, Ctrl+T momentarily
# shows "New Tab" before navigation starts, and the user may have any tab
# active in their Chrome window when we kick off (we Ctrl+T from there).
# Any title containing 'Google Chrome' is fine — we still defend against
# wrong-window typing by checking page state (cover letter found, vision
# question discovery) downstream.
TARGET_WINDOWS = (
    "Upwork",
    "Submit a Proposal",
    "Just a moment",
    "New Tab",
    "Google Chrome",
)
# Anchor used to detect "form has finished loading". Must be visible at the
# top of the apply page on first load (no scrolling required). Cover Letter
# is virtualized out of UIA when below the fold, so it can't be the wait
# anchor — we use the H1 instead, which is always at the top.
APPLY_FORM_ANCHOR_TEXT = "submit a proposal"
APPLY_FORM_TIMEOUT = 35.0  # generous to absorb Cloudflare challenges (~10-30s)


class ApplyFormNotFound(Exception):
    pass


class LoginRequired(Exception):
    """Raised when we detect that the user needs to log in before the apply
    flow can proceed. Caller should ping the user and leave the job pending."""


# Element-name patterns that indicate Upwork is asking for a login.
# We're conservative — only match strong signals, not random page chrome that
# might say 'log in' as a link in the footer.
_LOGIN_SIGNALS = (
    "log in to upwork",
    "sign in to upwork",
    "log in to your account",
    "continue with email",
    "continue with google",
    "continue with apple",
)


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


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


def navigate_to_apply(apply_url: str) -> None:
    """Focus any Chrome window, open a NEW TAB, then navigate via the address
    bar. The new tab is left open at the end of the run for the human to
    review and click Submit.

    We try Upwork-related titles first (so we land in the right Chrome window
    if the user has multiple), then fall back to any Chrome window.
    """
    focused = False
    # Try the workflow-specific titles first
    for title in TARGET_WINDOWS:
        if act.focus_window(title):
            focused = True
            log(f"  focused window: {title!r}")
            break
    # Fall back to any Chrome window — Ctrl+T works the same regardless of
    # which page is currently active in the window.
    if not focused:
        if act.focus_window("Google Chrome"):
            focused = True
            log("  focused window: 'Google Chrome' (any tab)")
    if not focused:
        raise RuntimeError("Could not focus any Chrome window. Is Chrome running?")
    time.sleep(0.3)
    # Ctrl+T opens a new tab and focuses the address bar by default.
    # During this window the title transiently becomes 'New Tab' (covered
    # by TARGET_WINDOWS) before navigation kicks in.
    act.key("ctrl+t")
    time.sleep(0.6)
    # Belt-and-braces: explicitly focus address bar in case the new tab opened
    # somewhere unusual.
    act.key("ctrl+l")
    time.sleep(0.2)
    act.type_text(apply_url)
    time.sleep(0.15)
    act.key("enter")


def wait_for_apply_form(timeout: float = APPLY_FORM_TIMEOUT, poll_interval: float = 1.5) -> None:
    """Poll the UIA tree until an element whose text contains 'cover letter'
    appears. Tolerates the Chrome window briefly being titled 'Just a moment...'
    during a Cloudflare challenge — those rounds count as 'still loading'.
    Raises ApplyFormNotFound on timeout.
    """
    deadline = time.time() + timeout
    needle = APPLY_FORM_ANCHOR_TEXT.lower()
    last_state = ""
    while time.time() < deadline:
        try:
            obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True)
        except Exception as ex:
            # Window not findable — usually means we're between page navigations
            # or Cloudflare is showing a challenge page with a transient title.
            # Don't spam; only log on state change.
            state = "no_window"
            if state != last_state:
                log(f"  waiting (no Upwork/Submit/Just-a-moment window yet): {ex}")
                last_state = state
            time.sleep(poll_interval)
            continue
        for e in obs.elements:
            if needle in (e.name or "").strip().lower():
                log(f"  Apply form anchor found: role={e.role} name={e.name[:60]!r}")
                return
        # Window is found but cover letter isn't present yet (still loading or
        # Cloudflare challenge in progress).
        state = "loading"
        if state != last_state:
            log(f"  waiting for form to render (window present, Cover Letter not yet)...")
            last_state = state
        time.sleep(poll_interval)
    raise ApplyFormNotFound(
        f"No element containing {APPLY_FORM_ANCHOR_TEXT!r} appeared within {timeout}s"
    )


def _editable_elements(elements):
    """Filter UIA elements down to text-input surfaces (Edit/Document roles)."""
    editable_roles = {"edit", "document"}
    return [e for e in elements if e.role.lower() in editable_roles]


def _label_y(elements, label_substr: str) -> int | None:
    """Return the top-y of the topmost text element containing `label_substr`
    (case-insensitive). None if no match."""
    needle = label_substr.lower()
    candidates = [
        e for e in elements
        if e.role == "text" and needle in (e.name or "").strip().lower()
    ]
    if not candidates:
        return None
    return min(c.bounds[1] for c in candidates)


def find_cover_letter_textarea():
    """Return the Element representing the cover-letter textarea, or None.

    Heuristic: the largest editable element whose top is below the topmost
    'Cover letter' label. Confirmed by dumping a real apply page.
    """
    obs = observe.observe(window_title=TARGET_WINDOWS, include_unnamed=True, include_text=True)
    label_y = _label_y(obs.elements, "cover letter")
    if label_y is None:
        return None
    edits = [
        e for e in _editable_elements(obs.elements)
        if e.bounds[1] >= label_y
    ]
    if not edits:
        return None
    edits.sort(
        key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]),
        reverse=True,
    )
    return edits[0]


def paste_cover_letter(cover_letter: str, max_scrolls: int = 8) -> bool:
    """PageDown until the Cover Letter textarea appears in the UIA tree, click
    it, paste via clipboard. Same pattern as the scanner's
    capture_url_via_clipboard. Returns True on success.
    """
    for attempt in range(max_scrolls + 1):
        el = find_cover_letter_textarea()
        if el is not None:
            log(f"  Cover-letter textarea found after {attempt} pagedown(s): bounds={el.bounds}")
            act.click(el)
            time.sleep(0.4)
            pyperclip.copy(cover_letter)
            time.sleep(0.2)
            act.key("ctrl+v")
            time.sleep(0.5)
            return True
        if attempt < max_scrolls:
            act.scroll(1, method="key")  # PageDown
            time.sleep(0.5)
    log(f"  Cover-letter textarea not found after {max_scrolls} pagedowns")
    return False


# ============================================================================
# Rate-increase frequency dropdown
#
# Upwork blocks proposal submission unless 'How often do you want a rate
# increase?' is set. We pick "Never" for every job (safe, removes the block).
#
# UIA exposes "Select a frequency" as a text element. The actual clickable
# dropdown opener is an unnamed parent div, so we click on the text element's
# coordinates. After the click, observing again reveals the option listitems
# ("Never", "Every 3 months", etc.) — we then click "Never".
# ============================================================================


def set_rate_increase_never(max_scrolls: int = 8) -> bool:
    """Find the rate-increase frequency dropdown, open it, click 'Never'.
    Returns True on success."""
    # Scroll until 'Select a frequency' is in view. Try ctrl+home first to
    # ensure we start from a known position (cover letter has already been
    # filled, so re-scrolling won't lose user input).
    act.key("ctrl+home")
    time.sleep(0.6)
    needle = "select a frequency"
    dropdown_text_el = None
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(
                window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
            )
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
            act.scroll(1, method="key")  # PageDown
            time.sleep(0.5)

    if dropdown_text_el is None:
        log("  could not find 'Select a frequency' dropdown — skipping")
        return False

    # Click the dropdown to open it
    l, t, r, b = dropdown_text_el.bounds
    cx, cy = (l + r) // 2, (t + b) // 2
    log(f"  opening rate-increase dropdown at ({cx}, {cy})")
    act.click_xy(cx, cy)
    time.sleep(0.8)  # let dropdown menu render

    # Find the 'Never' option (listitem with name "Never")
    try:
        obs2 = observe.observe(
            window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
        )
    except Exception as ex:
        log(f"  observe failed after opening dropdown: {ex}")
        return False

    never_el = None
    for e in obs2.elements:
        if e.role == "listitem" and (e.name or "").strip().lower() == "never":
            never_el = e
            break
    if never_el is None:
        log("  'Never' option not in tree after opening dropdown")
        return False

    log(f"  clicking Never option at bounds={never_el.bounds}")
    act.click(never_el)
    time.sleep(0.5)
    return True


# ============================================================================
# Screening questions
#
# Hybrid: vision identifies WHICH questions exist, UIA tells us WHERE they are.
#
#   1. Discovery — vision.list_visible_questions() scrolls through the form
#      region and at each scroll position asks gpt-4o-mini what client
#      screening questions are visible. Vision rejects page chrome (Job
#      details, Terms, Bid section, Profile highlights) far better than any
#      UIA text-shape heuristic could.
#   2. Localization — once we know a question's text, UIA reliably exposes
#      the matching label as a 'text' element with bounds. We click ~80px
#      below the label, which lands inside the textarea (Upwork's textareas
#      sit immediately below their labels with consistent padding).
#
# We tried asking vision for the textarea bounding box directly, but
# gpt-4o-mini's spatial reasoning on screenshots is unreliable for pixel
# coordinates — kept returning the same generic 'y=100-200' band. UIA
# bounds are an order of magnitude more accurate for this.
# ============================================================================

# Anchor texts that mark the end of the questions region (everything below is
# bid / connects / submit — we must never click into that area).
_POST_QUESTION_ANCHORS = ("attachments", "profile highlights", "boost your proposal", "send for")


def _seen_post_question_anchor(elements) -> bool:
    """Return True if any element's text contains a 'questions are over' anchor."""
    for e in elements:
        if e.role != "text":
            continue
        n = (e.name or "").strip().lower()
        if any(a in n for a in _POST_QUESTION_ANCHORS):
            return True
    return False


def collect_question_labels() -> list[str]:
    """Scroll the apply page through the question region, asking the vision
    model at each scroll position to list any client screening questions
    visible. Dedup across positions. Stop when post-question anchor (Attachments
    / Boost your proposal) appears in UIA, or hard scroll cap is hit.

    Caller must have already handled (or skipped) the cover letter.
    """
    log("Scrolling page + asking vision for screening questions...")
    seen_questions: list[str] = []
    seen_set: set[str] = set()
    max_scrolls = 6

    for scroll_num in range(max_scrolls + 1):
        # Ask vision what questions are visible right now
        questions_here = vision.list_visible_questions(debug_log=log)
        for q in questions_here:
            key = q.strip().lower()
            if key in seen_set:
                continue
            seen_set.add(key)
            seen_questions.append(q.strip())
            log(f"  found Q: {q[:100]!r}")

        # Check if we've scrolled past the question region
        try:
            obs = observe.observe(
                window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
            )
            if _seen_post_question_anchor(obs.elements):
                log("  reached post-question anchor (Attachments/Boost) — stopping scroll")
                break
        except Exception as ex:
            log(f"  observe failed during scroll {scroll_num}: {ex}")

        if scroll_num >= max_scrolls:
            log("  hit max_scrolls cap — stopping")
            break

        # Page down to load more content (act.scroll's require_focus handles focus)
        act.scroll(1, method="key")  # PageDown
        time.sleep(0.8)  # let the next batch render

    log(f"  collected {len(seen_questions)} unique question(s)")
    return seen_questions


def scroll_to_label(label_text: str, max_scrolls: int = 15) -> bool:
    """Bring `label_text` into view by scrolling. First Ctrl+Home to reset,
    then PageDown until the label appears. Returns True as soon as the label
    is in the UIA tree at any visible position.

    NOTE: we don't try to re-position the label within the viewport here.
    PageDown jumps ~1000px which can over-scroll and push the label off the
    top, making the next observe miss it entirely. The label-bounds + offset
    click works regardless of where in the viewport the label sits.
    """
    needle = label_text.lower()[:80]  # match on a prefix in case of truncation
    act.key("ctrl+home")  # require_focus inside act.key handles window focus
    time.sleep(0.6)
    for i in range(max_scrolls + 1):
        try:
            obs = observe.observe(
                window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
            )
        except Exception:
            time.sleep(0.5)
            continue
        for e in obs.elements:
            if e.role != "text":
                continue
            name = (e.name or "").strip().lower()
            if needle in name:
                return True
        if i < max_scrolls:
            act.scroll(1, method="key")
            time.sleep(0.5)
    return False


def _load_portfolio_text() -> str:
    p = Path("portfolio.json")
    if not p.exists():
        return "{}"
    return p.read_text(encoding="utf-8")


def _find_label_bounds_via_uia(label_text: str):
    """Find the UIA text element whose name contains `label_text` (prefix
    match, case-insensitive). Return its bounds tuple or None."""
    needle = label_text.lower()[:80]
    try:
        obs = observe.observe(
            window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
        )
    except Exception:
        return None
    for e in obs.elements:
        if e.role != "text":
            continue
        n = (e.name or "").strip().lower()
        if needle in n:
            return e.bounds
    return None


def answer_and_paste_questions(question_labels: list[str], job: dict, dry_run: bool = False) -> int:
    """For each question label: scroll to it, locate label via UIA, click into
    the textarea immediately below the label, paste the LLM answer.

    Why UIA + offset instead of vision bbox: gpt-4o-mini's spatial reasoning
    on screenshots is unreliable for pixel coordinates (returned 'y=100-200'
    band even when the textarea was halfway down the page). UIA reliably
    gives us the label's bounds, and Upwork's textareas always sit ~10-30px
    immediately below their label — a fixed offset is more reliable than
    asking vision to do bbox localization.
    """
    if not question_labels:
        log("  No screening questions detected.")
        return 0
    portfolio_text = _load_portfolio_text()
    answered = 0
    # How far below the label center to click — empirically the textarea body
    # starts ~10-30px below the label-baseline. We click ~80px below the
    # label-top to land comfortably inside the textarea body.
    label_to_textarea_offset_y = 80
    for i, label in enumerate(question_labels, 1):
        log(f"  Q{i}: {label[:120]}")

        # Generate the answer first (cheap if we end up not pasting)
        try:
            answer = proposal.generate_screening_answer(
                question=label,
                job_description=job.get("description", ""),
                cover_letter=job.get("cover_letter", ""),
                portfolio_json=portfolio_text,
            )
        except Exception as ex:
            log(f"    LLM error: {ex}")
            continue
        log(f"    A{i}: {answer[:160]!r}")

        # Bring the question into view
        if not scroll_to_label(label):
            log(f"    could not scroll to label — skipping")
            continue
        time.sleep(0.4)

        # Find the label's UIA bounds, derive a click target inside the textarea
        bounds = _find_label_bounds_via_uia(label)
        if bounds is None:
            log(f"    UIA didn't expose label after scroll — skipping")
            continue
        l, t, r, b = bounds
        cx = (l + r) // 2
        cy = b + label_to_textarea_offset_y  # below the label's bottom edge
        log(f"    label bounds={bounds}, click target=({cx}, {cy})")

        if dry_run:
            log(f"    [dry-run] would click ({cx}, {cy}) — not clicking.")
            answered += 1
            continue

        log(f"    clicking textarea at ({cx}, {cy})")
        act.click_xy(cx, cy)
        time.sleep(0.4)
        pyperclip.copy(answer)
        time.sleep(0.2)
        act.key("ctrl+v")
        time.sleep(0.5)
        answered += 1
    return answered


def pick_next_job(conn, job_id_override: str | None) -> dict | None:
    """Return the pending job dict to apply to, or None."""
    if job_id_override:
        job = db.find_pending_by_id(conn, job_id_override)
        if job is None:
            log(f"--job {job_id_override}: not found in DB or not in 'pending' status")
        return job
    return db.pick_newest_pending_today(conn)


def _move_to_failed(conn, job_id: str) -> None:
    try:
        db.set_apply_status(conn, job_id, "failed")
    except Exception as ex:
        log(f"  WARN: also failed to set apply_status='failed': {ex}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="Specific job-id to apply to (overrides date filter)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Observe form, generate answers, print only — paste nothing, change no DB state.")
    args = ap.parse_args()

    load_dotenv()
    conn = db.connect()
    db.init_schema(conn)
    act.set_target_window(TARGET_WINDOWS)

    job = pick_next_job(conn, args.job)
    if job is None:
        log("No pending jobs from today. Exiting.")
        sys.exit(0)

    log(f"Picked job: {job['job_id']} — {(job.get('title') or '')[:80]}")
    log(f"Apply URL: {job['apply_url']}")

    if not db.is_safe_apply_url(job["apply_url"]):
        log(f"REFUSING to navigate: apply_url failed safety check: {job['apply_url']}")
        sys.exit(2)

    answered = 0
    try:
        log("Navigating to apply page...")
        navigate_to_apply(job["apply_url"])

        log(f"Waiting for apply form to render ({APPLY_FORM_TIMEOUT}s timeout, includes Cloudflare)...")
        wait_for_apply_form(timeout=APPLY_FORM_TIMEOUT)

        if args.dry_run:
            log("--dry-run: skipping cover-letter paste.")
        else:
            log("Pasting cover letter...")
            if not paste_cover_letter(job["cover_letter"]):
                raise RuntimeError("could not find or click cover-letter textarea")
            log("Cover letter pasted.")

        log("Collecting screening question labels (scroll + UIA)...")
        question_labels = collect_question_labels()
        log(f"  Found {len(question_labels)} screening question(s).")
        answered = answer_and_paste_questions(question_labels, job, dry_run=args.dry_run)
        log(f"  Answered {answered}/{len(question_labels)} questions.")

        if args.dry_run:
            log("--dry-run: skipping rate-increase dropdown.")
        else:
            log("Setting rate-increase frequency to 'Never'...")
            if not set_rate_increase_never():
                log("  WARN: could not set rate-increase to Never — submission will be blocked")
            else:
                log("  Rate increase set to Never.")

        if args.dry_run:
            log("--dry-run: skipping DB transition and Discord notify. Done.")
            return

        log("Setting apply_status='awaiting_review'...")
        db.set_apply_status(conn, job["job_id"], "awaiting_review")
        log("  status updated.")

        log("Sending Discord notification...")
        if notify.send_review_needed(
            title=job["title"],
            apply_url=job["apply_url"],
            questions_answered=answered,
        ):
            log("  Discord notification sent.")

        log("Done. Human: review form in browser, set bid, click Submit.")
    except ApplyFormNotFound as ex:
        # Form didn't render in 35s. Most common cause: Upwork session expired
        # and the page is asking for login. Distinguish that from a real
        # failure so we can leave the job pending for the next retry.
        log(f"Form wait timed out: {ex}")
        if detect_login_required():
            log("LOGIN REQUIRED — leaving apply_status='pending', pinging Discord")
            try:
                notify.send_login_needed(
                    title=job["title"], apply_url=job["apply_url"]
                )
            except Exception:
                pass
            sys.exit(5)
        log("FAIL (not login-related): setting apply_status='failed'")
        _move_to_failed(conn, job["job_id"])
        try:
            notify.send_review_needed(
                title=f"FAILED: {job['title']}",
                apply_url=job["apply_url"],
                questions_answered=answered,
            )
        except Exception:
            pass
        sys.exit(3)
    except Exception as ex:
        log(f"UNHANDLED ERROR: {type(ex).__name__}: {ex}")
        _move_to_failed(conn, job["job_id"])
        try:
            notify.send_review_needed(
                title=f"FAILED: {job['title']} ({type(ex).__name__})",
                apply_url=job["apply_url"],
                questions_answered=answered,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
