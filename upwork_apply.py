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
from dataclasses import dataclass
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
import proposal

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


@dataclass
class Question:
    label: str
    textarea: object  # observe Element


def _bid_section_y(elements) -> int | None:
    """Return the top-y of the bid/Connects section, or None.
    Anchors: 'Bid', 'Hourly rate', 'Connects', 'Submit a proposal'."""
    needles = ("bid", "hourly rate", "connects", "submit a proposal")
    ys = []
    for e in elements:
        if e.role != "text":
            continue
        n = (e.name or "").strip().lower()
        if any(n.startswith(needle) or needle in n for needle in needles):
            ys.append(e.bounds[1])
    return min(ys) if ys else None


def detect_screening_questions() -> list[Question]:
    """Return a list of Question(label, textarea) for the apply form.
    Excludes the cover-letter textarea. Returns [] if no questions found."""
    obs = observe.observe(window_title=WINDOW, include_unnamed=True, include_text=True)
    cover_letter_el = find_cover_letter_textarea()
    cover_letter_id = cover_letter_el.id if cover_letter_el else None

    cover_y = cover_letter_el.bounds[1] if cover_letter_el else 0
    bid_y = _bid_section_y(obs.elements) or 10**9

    edits = [
        e for e in _editable_elements(obs.elements)
        if e.id != cover_letter_id
        and e.bounds[1] > cover_y
        and e.bounds[1] < bid_y
    ]
    if not edits:
        return []

    text_elems = [e for e in obs.elements if e.role == "text" and (e.name or "").strip()]
    text_elems.sort(key=lambda e: e.bounds[1])

    questions: list[Question] = []
    for textarea in edits:
        label = ""
        for t in text_elems:
            if t.bounds[1] >= textarea.bounds[1]:
                break
            tn = t.name.strip().lower()
            if tn in ("cover letter", "bid", "connects", "submit a proposal"):
                continue
            if 5 <= len(t.name.strip()) <= 400:
                label = t.name.strip()
        if label:
            questions.append(Question(label=label, textarea=textarea))
    return questions


def _load_portfolio_text() -> str:
    p = Path("portfolio.json")
    if not p.exists():
        return "{}"
    return p.read_text(encoding="utf-8")


def answer_and_paste_questions(questions: list[Question], job: dict, dry_run: bool = False) -> int:
    """Generate an answer for each question and paste it. Returns count answered."""
    if not questions:
        log("  No screening questions detected.")
        return 0
    portfolio_text = _load_portfolio_text()
    answered = 0
    for i, q in enumerate(questions, 1):
        log(f"  Q{i}: {q.label[:120]}")
        try:
            answer = proposal.generate_screening_answer(
                question=q.label,
                job_description=job.get("description", ""),
                cover_letter=job.get("cover_letter", ""),
                portfolio_json=portfolio_text,
            )
        except Exception as ex:
            log(f"    LLM error: {ex}")
            continue
        log(f"    A{i}: {answer[:160]!r}")
        if dry_run:
            continue
        act.focus_window(WINDOW)
        act.click(q.textarea)
        time.sleep(0.4)
        pyperclip.copy(answer)
        time.sleep(0.2)
        act.key("ctrl+v")
        time.sleep(0.4)
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
    log("Detecting screening questions...")
    questions = detect_screening_questions()
    log(f"  Found {len(questions)} screening question(s).")
    answered = answer_and_paste_questions(questions, job, dry_run=args.dry_run)
    log(f"  Answered {answered}/{len(questions)} questions.")

    log("Form fill complete. Awaiting-review move + Discord notify next task.")


if __name__ == "__main__":
    main()
