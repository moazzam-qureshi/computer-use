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
import jobs_store
import notify
import observe
import proposal
import vision

WINDOW = "Upwork"
# Acceptable Chrome window titles during the apply workflow. The apply page
# renders with title "Submit a Proposal", but Cloudflare may briefly show
# "Just a moment..." while challenging the request. All three are valid
# states we may need to observe / focus during the form-load wait.
TARGET_WINDOWS = ("Upwork", "Submit a Proposal", "Just a moment")
APPLY_FORM_ANCHOR_TEXT = "cover letter"  # case-insensitive substring match
APPLY_FORM_TIMEOUT = 35.0  # generous to absorb Cloudflare challenges (~10-30s)


class ApplyFormNotFound(Exception):
    pass


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


def navigate_to_apply(apply_url: str) -> None:
    """Focus any Upwork-related Chrome window, then navigate via the address bar."""
    focused = False
    for title in TARGET_WINDOWS:
        if act.focus_window(title):
            focused = True
            break
    if not focused:
        raise RuntimeError(f"Could not focus any window matching {TARGET_WINDOWS!r}")
    time.sleep(0.3)
    act.navigate(apply_url)


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


def paste_cover_letter(cover_letter: str) -> bool:
    """Click the cover-letter textarea, paste cover letter via clipboard.
    Returns True on success."""
    el = find_cover_letter_textarea()
    if el is None:
        log("  Could not find cover-letter textarea")
        return False
    log(f"  Cover-letter textarea: bounds={el.bounds}")
    act.click(el)  # require_focus inside act.click handles window focus
    time.sleep(0.4)
    pyperclip.copy(cover_letter)
    time.sleep(0.2)
    act.key("ctrl+v")
    time.sleep(0.5)
    return True


# ============================================================================
# Screening questions
#
# Upwork renders question textareas as contenteditable divs that UIA does NOT
# surface, even when fully in the viewport. So we use a hybrid:
#   1. UIA enumerates question *labels* by scrolling and observing (cheap,
#      reliable — text elements are exposed normally).
#   2. For each label, we screenshot the page and ask gpt-4o-mini for the
#      textarea bounding box (vision.find_textarea_for_question).
#   3. Click those coordinates, paste the answer via clipboard.
# ============================================================================

# Section headings / page chrome that should NOT be treated as a question label.
_NOT_A_QUESTION = frozenset({
    "additional details",
    "cover letter",
    "attachments",
    "profile highlights",
    "boost your proposal",
    "submit a proposal",
    "proposal settings",
    "job details",
    "terms",
    "schedule a rate increase",
    "your bid",
    "summary",
    "rank",
    "bid",
    "now",
    "1st place",
    "2nd place",
    "3rd place",
    "4th place",
    "remaining balance",
})

# Anchor texts that mark the end of the questions region (everything below is
# bid / connects / submit — we must never click into that area).
_POST_QUESTION_ANCHORS = ("attachments", "profile highlights", "boost your proposal", "send for")


def _is_question_label(text: str) -> bool:
    """Heuristic: is this text element a screening-question label?"""
    t = (text or "").strip()
    if not t:
        return False
    if t.lower() in _NOT_A_QUESTION:
        return False
    # Reject very short labels (likely UI chrome) and very long blocks (likely
    # the job description copied into the apply page).
    if len(t) < 15 or len(t) > 400:
        return False
    # Must contain at least one letter (ignore pure-number labels like "$10.00")
    if not any(c.isalpha() for c in t):
        return False
    return True


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
    """Scroll down the apply page progressively, collecting unique question
    labels via UIA. Stops when a 'post-question' anchor (Attachments / Boost
    your proposal / etc.) appears, or after a hard scroll cap.

    Caller must have already pasted the cover letter (so the cover letter
    label is at the top of the form region we're scanning).
    """
    log("Scrolling page to collect question labels...")
    # Start from a known position: scroll the cover letter into view, then
    # progressively page down. We DON'T Ctrl+Home here because that may scroll
    # the entire page chrome to the top; instead we let scroll_to_label handle
    # positioning later when answering.
    seen_labels: list[str] = []  # preserve order
    seen_set: set[str] = set()
    max_scrolls = 12  # safety cap

    for scroll_num in range(max_scrolls + 1):
        try:
            obs = observe.observe(
                window_title=TARGET_WINDOWS, include_unnamed=False, include_text=True
            )
        except Exception as ex:
            log(f"  observe failed during scroll {scroll_num}: {ex}")
            return seen_labels

        # Sort visible text elements by y so the order matches reading order
        text_elems = [e for e in obs.elements if e.role == "text"]
        text_elems.sort(key=lambda e: e.bounds[1])

        new_count = 0
        for e in text_elems:
            label = (e.name or "").strip()
            if not _is_question_label(label):
                continue
            if label in seen_set:
                continue
            seen_set.add(label)
            seen_labels.append(label)
            log(f"  found Q label: {label[:100]!r}")
            new_count += 1

        if _seen_post_question_anchor(obs.elements):
            log("  reached post-question anchor (Attachments/Boost) — stopping scroll")
            break

        if scroll_num >= max_scrolls:
            log("  hit max_scrolls cap — stopping")
            break

        # Page down to load more content (act.scroll's require_focus handles focus)
        act.scroll(1, method="key")  # PageDown
        time.sleep(0.8)  # let the next batch render

    log(f"  collected {len(seen_labels)} unique question label(s)")
    return seen_labels


def scroll_to_label(label_text: str, max_scrolls: int = 15) -> bool:
    """Bring `label_text` into view by scrolling. First Ctrl+Home to reset,
    then PageDown until the label appears. Returns True on success."""
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
                # Found it. Ideally label is in upper portion of viewport so
                # the textarea below it is also visible. If label is too low,
                # scroll one more PageDown.
                top_y = e.bounds[1]
                if top_y > 700:  # too low — bring it up
                    act.scroll(1, method="key")
                    time.sleep(0.5)
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


def answer_and_paste_questions(question_labels: list[str], job: dict, dry_run: bool = False) -> int:
    """For each question label: scroll to it, ask vision for the textarea
    coords, generate an answer, click + paste. Returns number answered."""
    if not question_labels:
        log("  No screening questions detected.")
        return 0
    portfolio_text = _load_portfolio_text()
    answered = 0
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

        # Bring the question into view (always — dry-run still validates that
        # scrolling + vision can locate the textarea)
        if not scroll_to_label(label):
            log(f"    could not scroll to label — skipping")
            continue
        time.sleep(0.4)

        # Ask vision for the textarea bounding box
        bb = vision.find_textarea_for_question(label, debug_log=log)
        if bb is None:
            log(f"    vision could not locate textarea — skipping")
            continue

        cx, cy = bb.center
        if dry_run:
            log(f"    [dry-run] would click ({cx}, {cy}) — not clicking.")
            answered += 1
            continue

        log(f"    clicking textarea at ({cx}, {cy})")
        act.click_xy(cx, cy)  # require_focus inside act.click_xy handles window focus
        time.sleep(0.4)
        pyperclip.copy(answer)
        time.sleep(0.2)
        act.key("ctrl+v")
        time.sleep(0.5)
        answered += 1
    return answered


def pick_next_job(job_id_override: str | None) -> Path | None:
    """Return the path of the job JSON to apply to, or None."""
    if job_id_override:
        path = jobs_store.find_pending_by_id(job_id_override)
        if path is None:
            log(f"--job {job_id_override}: not found in jobs/pending/")
        return path
    return jobs_store.pick_newest_pending_today()


def _move_to_failed(path: Path) -> None:
    try:
        jobs_store.move_to_status(path, "failed")
    except Exception as ex:
        log(f"  WARN: also failed to move JSON to failed/: {ex}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="Specific job-id to apply to (overrides date filter)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Observe form, generate answers, print only — paste nothing, move nothing.")
    args = ap.parse_args()

    load_dotenv()
    jobs_store.ensure_dirs()
    act.set_target_window(TARGET_WINDOWS)

    job_path = pick_next_job(args.job)
    if job_path is None:
        log("No pending jobs from today. Exiting.")
        sys.exit(0)

    job = jobs_store.read_job(job_path)
    log(f"Picked job: {job['job_id']} — {job['title'][:80]}")
    log(f"Apply URL: {job['apply_url']}")

    if not jobs_store.is_safe_apply_url(job["apply_url"]):
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
            log("--dry-run: skipping JSON move and Discord notify. Done.")
            return

        log("Moving job JSON to awaiting_review/...")
        new_path = jobs_store.move_to_status(job_path, "awaiting_review")
        log(f"  Moved to {new_path}")

        log("Sending Discord notification...")
        if notify.send_review_needed(
            title=job["title"],
            apply_url=job["apply_url"],
            questions_answered=answered,
        ):
            log("  Discord notification sent.")

        log("Done. Human: review form in browser, set bid, click Submit.")
    except ApplyFormNotFound as ex:
        log(f"FAIL: {ex}")
        _move_to_failed(job_path)
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
        _move_to_failed(job_path)
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
