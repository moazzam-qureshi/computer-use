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
import observe

WINDOW = "Upwork"
APPLY_FORM_ANCHOR_TEXT = "cover letter"  # case-insensitive substring match


class ApplyFormNotFound(Exception):
    pass


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


def navigate_to_apply(apply_url: str) -> None:
    """Focus the Upwork window, then navigate via the address bar."""
    if not act.focus_window(WINDOW):
        raise RuntimeError(f"Could not focus window matching {WINDOW!r}")
    time.sleep(0.3)
    act.navigate(apply_url)


def wait_for_apply_form(timeout: float = 10.0, poll_interval: float = 1.0) -> None:
    """Poll the UIA tree until an element whose text contains 'cover letter'
    appears. Raise ApplyFormNotFound on timeout."""
    deadline = time.time() + timeout
    needle = APPLY_FORM_ANCHOR_TEXT.lower()
    while time.time() < deadline:
        try:
            obs = observe.observe(window_title=WINDOW, include_unnamed=False, include_text=True)
        except Exception as ex:
            log(f"  observe failed during wait: {ex}")
            time.sleep(poll_interval)
            continue
        for e in obs.elements:
            if needle in (e.name or "").strip().lower():
                log(f"  Apply form anchor found: role={e.role} name={e.name[:60]!r}")
                return
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
    obs = observe.observe(window_title=WINDOW, include_unnamed=True, include_text=True)
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
    act.focus_window(WINDOW)
    act.click(el)
    time.sleep(0.4)
    pyperclip.copy(cover_letter)
    time.sleep(0.2)
    act.key("ctrl+v")
    time.sleep(0.5)
    return True


def pick_next_job(job_id_override: str | None) -> Path | None:
    """Return the path of the job JSON to apply to, or None."""
    if job_id_override:
        path = jobs_store.find_pending_by_id(job_id_override)
        if path is None:
            log(f"--job {job_id_override}: not found in jobs/pending/")
        return path
    return jobs_store.pick_newest_pending_today()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", help="Specific job-id to apply to (overrides date filter)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Observe form, generate answers, print only — paste nothing, move nothing.")
    args = ap.parse_args()

    load_dotenv()
    jobs_store.ensure_dirs()

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

    if args.dry_run:
        log("--dry-run: skipping navigation and form interaction.")
        return

    log("Navigating to apply page...")
    navigate_to_apply(job["apply_url"])

    log("Waiting for apply form to render (10s timeout)...")
    try:
        wait_for_apply_form(timeout=10.0)
    except ApplyFormNotFound as ex:
        log(f"FAIL: {ex}")
        try:
            jobs_store.move_to_status(job_path, "failed")
        except Exception as mv_ex:
            log(f"  also failed to move JSON to failed/: {mv_ex}")
        sys.exit(3)

    log("Pasting cover letter...")
    if not paste_cover_letter(job["cover_letter"]):
        log("FAIL: could not paste cover letter")
        try:
            jobs_store.move_to_status(job_path, "failed")
        except Exception as mv_ex:
            log(f"  also failed to move JSON to failed/: {mv_ex}")
        sys.exit(4)
    log("Cover letter pasted.")
    log("Screening-question handling not yet implemented (next task).")


if __name__ == "__main__":
    main()
