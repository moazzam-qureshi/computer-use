# Upwork Apply Automation — Phase 1 Design

**Date:** 2026-05-01
**Status:** Approved, pending implementation plan

## Overview

Extend the existing Upwork scanner system with a phase-1 apply automation. Two changes plus one new script:

1. **`upwork_driver.py`** — write a rich JSON file to `jobs/pending/<job-id>.json` for every relevant job (in addition to the existing `seen_urls.txt` append).
2. **`upwork_apply.py`** (new) — read the queue, pick the newest job from today, navigate to the apply URL, fill cover letter + screening question answers, stop before bid/Submit, ping Discord for human review.
3. **No changes** to scanner judging logic, Doc generation, or scanner Discord alerts.

The split mirrors the existing substrate-first principle: deterministic Python driver, LLM only for judgment (here: answering screening questions).

## Key decisions

| Question | Decision | Rationale |
|---|---|---|
| Apply-URL strategy | Build directly: `https://www.upwork.com/nx/proposals/job/~<id>/apply/` | Skips the job-page → click-Apply-Now hop. One navigation, no DOM dependency on Apply button. |
| Storage shape | Per-job JSON files in status directories | Manual inspection, no file-locking, easy to veto by moving file. |
| Dedup vs `seen_urls.txt` | Keep both. `seen_urls.txt` = "don't re-judge" cache. `jobs/pending/` = apply queue. | Different lifecycles. Re-judging irrelevant jobs costs LLM tokens. |
| When scanner writes JSON | After Doc + cover letter generated, before Discord notify | Natural commit point. Apply queue independent of Discord. |
| Job selection | Newest pending from today (local date) | Fresher job = better odds of being seen by client. Older jobs ignored unless `--job <id>` override. |
| Cover-letter paste mechanic | Clipboard paste (Ctrl+V) | Fast, exact formatting, URL preserved. Pattern already proven in scanner clipboard-URL capture. |
| Screening-question answers | LLM-generated and pasted; human reviews in browser before Submit | Matches the actual valuable automation. Human reviews the filled form, not console text. |
| LLM for answers | `gpt-4o-mini` | Same model as scanner judge and proposal. Iterate on prompt before model. |
| Submit | NEVER. Human sets bid + Submits manually. Discord notification triggers review. | Hard rule from CLAUDE.md. No `--really-submit` flag in phase 1. |

## File-system layout

```
jobs/
  pending/         <id>.json  — relevant, queued for apply
  awaiting_review/ <id>.json  — apply form filled, waiting for human to set bid + Submit
  applied/         <id>.json  — human confirmed submit (manual move or tiny helper later)
  skipped/         <id>.json  — manually vetoed before apply
  failed/          <id>.json  — apply script crashed or aborted on this one
```

`<id>` is the hex suffix from the job URL (e.g., `022050144604938339008` from `~022050144604938339008`).

`seen_urls.txt` is unchanged.

## Job JSON schema

```json
{
  "job_id": "022050144604938339008",
  "url": "https://www.upwork.com/jobs/~022050144604938339008",
  "apply_url": "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/",
  "title": "Senior AI Engineer",
  "found_at": "2026-05-01T14:32:00",
  "budget": "Fixed-price $1,750",
  "client": {
    "name": "Sarah",
    "country": "Lebanon",
    "payment_verified": true,
    "rating": 4.2,
    "spend": "$9.1K"
  },
  "skills": ["Python", "LangChain", "RAG"],
  "description": "...full description...",
  "doc_url": "https://docs.google.com/document/d/...",
  "cover_letter": "Hey Sarah, I spent some time...",
  "status_history": [
    {"status": "pending", "at": "2026-05-01T14:32:00"}
  ]
}
```

`status_history` is appended on every directory move. Each move rewrites the file then `os.rename`s into the new directory.

## Scanner changes (`upwork_driver.py`)

- Add helper `write_pending_job(job_data) -> Path`. Called after Doc generation succeeds, before `notify.send_alert` / `notify.send_proposal`.
- Job-id extracted via regex from URL: `~([0-9a-f]+)`.
- At the start of each job processed, dedup-check against all five status directories: if `<id>.json` exists anywhere, skip.
- All five `jobs/*` directories created on script startup if missing (idempotent).
- `seen_urls.txt` continues to be appended for both relevant and skipped jobs (its current behavior).

## Apply script (`upwork_apply.py`)

### CLI

```
uv run upwork_apply.py                    # picks newest pending from today
uv run upwork_apply.py --job <id>         # targets a specific job (override date filter)
uv run upwork_apply.py --dry-run          # observe form, generate answers, print, paste nothing
```

### Top-level flow

```python
def main():
    job = pick_next_job()                # newest from today, or --job override
    navigate_to_apply(job.apply_url)
    wait_for_apply_form(timeout=10)      # poll for "Cover letter" anchor
    paste_cover_letter(job.cover_letter)
    questions = detect_screening_questions()
    for q in questions:
        answer = generate_answer(q, job)
        paste_answer(q, answer)
    move_to_awaiting_review(job)
    notify_human_for_review(job)
```

### Function responsibilities

- **`pick_next_job()`** — list `jobs/pending/*.json`, filter where `found_at` date == today (local), return latest by `found_at`. Exit cleanly with "no pending jobs from today" if none. `--job <id>` override skips date filter.
- **`navigate_to_apply(url)`** — `act.focus_window("Upwork")` → `act.navigate(url)`. Validate URL matches `https://www.upwork.com/nx/proposals/job/~[0-9a-f]+/apply/` first.
- **`wait_for_apply_form(timeout=10)`** — poll `observe.observe(WINDOW, include_text=True)` every 1s. Return when an element with text containing "Cover letter" (case-insensitive) is found. Raise `ApplyFormNotFound` on timeout.
- **`paste_cover_letter(text)`** — find the editable element nearest the "Cover letter" label. Click to focus. `pyperclip.copy(text)`, `act.key("ctrl+v")`.
- **`detect_screening_questions()`** — return a list of `Question(label, textarea_element)`. Strategy: find "Additional questions" anchor; fallback: enumerate all editable elements, exclude the cover-letter one. Final selector to be hardened after dumping a real apply page with questions.
- **`generate_answer(question, job)`** — single `gpt-4o-mini` call with `ANSWER_SYSTEM` prompt + question text + `job.description` + `job.cover_letter` + relevant `portfolio.json` slice. Plain text out.
- **`paste_answer(question, answer)`** — click textarea, clipboard-paste.
- **`move_to_awaiting_review(job)`** — append status entry, rewrite JSON, `os.rename` `jobs/pending/<id>.json` → `jobs/awaiting_review/<id>.json`.
- **`notify_human_for_review(job)`** — Discord webhook message: "Form filled for `<title>`. Set bid + Submit: `<apply_url>`".

### Failure handling

| Failure | Action |
|---|---|
| Apply form anchor not found in 10s | Move to `jobs/failed/`, Discord alert, exit non-zero. |
| Cover letter textarea not found | Same as above. |
| Screening-question detection fails | Still move to `awaiting_review` (cover letter is in). Note the failure in `status_history`. |
| Unhandled exception | Move to `failed/`, Discord alert with traceback summary. |

## New LLM prompt: `ANSWER_SYSTEM`

Lives in `proposal.py` alongside `DOC_PROPOSAL_SYSTEM` and `ABOUT_ME_SYSTEM`. Constraints:

- Voice rules consistent with existing prompts: first-person, conversational, direct. No em-dashes, no fluff phrases, no emojis.
- Length: 60–150 words per answer.
- Must reference concrete past work from `portfolio.json` when the question asks for experience.
- Must NOT repeat the cover letter content verbatim.
- Must NOT invent projects, clients, or numbers not in `portfolio.json`.

## Hard safety rules (encoded in code)

- Apply script **never** clicks Submit. No flag exists to override this in phase 1.
- Apply script **never** touches the bid amount field.
- `apply_url` must match `^https://www\.upwork\.com/nx/proposals/job/~[0-9a-f]+/apply/$` before navigation.
- One job per run. No batch loop.

## Out of scope (phase 1)

- Bid amount selection
- Connects confirmation dialog handling
- Auto-submit
- Multi-job batch loop
- Automated "mark as applied" (manual file move for now)
- Cloudflare CAPTCHA handling beyond a simple wait (if challenge appears, abort + notify)

## Operational sequence after phase 1 lands

1. Scanner runs (`scheduler.py` or one-off `upwork_driver.py`). Drops JSON files in `jobs/pending/`.
2. Human runs `uv run upwork_apply.py` when ready to apply to the freshest job from today.
3. Script fills form, moves JSON to `awaiting_review/`, pings Discord.
4. Human opens browser, reviews filled form, sets bid, clicks Submit.
5. Human manually moves JSON from `awaiting_review/` to `applied/` (or we add a tiny helper script later).
