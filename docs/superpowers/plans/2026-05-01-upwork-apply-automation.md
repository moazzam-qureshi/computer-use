# Upwork Apply Automation — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-job JSON queue files to the scanner and build a phase-1 apply script that fills the cover letter + LLM-answered screening questions, then stops before bid/Submit and pings Discord for human review.

**Architecture:** Two changes: (1) `upwork_driver.py` writes a rich JSON to `jobs/pending/<id>.json` after Doc creation; (2) new `upwork_apply.py` reads that queue, navigates to the apply URL via the direct `/nx/proposals/job/~<id>/apply/` form, polls for the form to render, paste-fills the cover letter, detects + answers screening questions, then moves the JSON to `jobs/awaiting_review/` and pings Discord. Hard rule: never touches bid or Submit.

**Tech Stack:** Python 3.12, `openai` (gpt-4o-mini), `pyperclip`, existing `act.py` / `observe.py` / `notify.py` substrate. Pytest for pure-function unit tests; manual verification with `dump.py` for the UIA-touching paths.

**Spec:** [`docs/superpowers/specs/2026-05-01-upwork-apply-automation-design.md`](../specs/2026-05-01-upwork-apply-automation-design.md)

---

## File structure

**New files:**
- `jobs_store.py` — pure functions: job-id extraction, JSON read/write, status-directory moves, date-filtered queue listing. No UIA, no LLM.
- `upwork_apply.py` — the apply driver script. Composes `act` + `observe` + `jobs_store` + `proposal` + `notify`.
- `tests/__init__.py` — empty.
- `tests/test_jobs_store.py` — unit tests for the pure-function module.

**Modified files:**
- `upwork_driver.py` — call `jobs_store.write_pending(...)` after Doc creation; add startup `jobs_store.ensure_dirs()`; check `jobs_store.is_known_job_id(...)` for cross-status dedup.
- `proposal.py` — add `ANSWER_SYSTEM` prompt and `generate_screening_answer()` function.
- `pyproject.toml` — add `pytest` to dev deps.

**New directories (created at runtime by code):**
- `jobs/pending/`, `jobs/awaiting_review/`, `jobs/applied/`, `jobs/skipped/`, `jobs/failed/`

---

## Task 1: jobs_store skeleton + ensure_dirs

**Files:**
- Create: `jobs_store.py`
- Create: `tests/__init__.py`
- Create: `tests/test_jobs_store.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pytest to pyproject.toml**

Edit `pyproject.toml`. Find the `[project]` block and append a new section after it:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
]
```

Then run:
```bash
uv sync
```
Expected: pytest installed in `.venv`.

- [ ] **Step 2: Write the failing test for ensure_dirs**

Create `tests/__init__.py` empty.

Create `tests/test_jobs_store.py`:

```python
from pathlib import Path

import jobs_store


def test_ensure_dirs_creates_all_status_dirs(tmp_path: Path):
    jobs_store.ensure_dirs(tmp_path)
    for status in ("pending", "awaiting_review", "applied", "skipped", "failed"):
        assert (tmp_path / status).is_dir()


def test_ensure_dirs_is_idempotent(tmp_path: Path):
    jobs_store.ensure_dirs(tmp_path)
    jobs_store.ensure_dirs(tmp_path)  # second call must not raise
    assert (tmp_path / "pending").is_dir()
```

- [ ] **Step 3: Run the test, confirm it fails**

Run:
```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: `ModuleNotFoundError: No module named 'jobs_store'`.

- [ ] **Step 4: Implement jobs_store.ensure_dirs**

Create `jobs_store.py`:

```python
"""
Per-job JSON queue under jobs/<status>/<job-id>.json.

Pure-function module: no UIA, no LLM, no network. Just filesystem and JSON.
"""
from __future__ import annotations

from pathlib import Path

STATUSES = ("pending", "awaiting_review", "applied", "skipped", "failed")
DEFAULT_ROOT = Path("jobs")


def ensure_dirs(root: Path = DEFAULT_ROOT) -> None:
    """Create jobs/<status>/ directories if missing. Idempotent."""
    for status in STATUSES:
        (root / status).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 5: Run the test, confirm it passes**

Run:
```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock jobs_store.py tests/__init__.py tests/test_jobs_store.py
git commit -m "Add jobs_store skeleton with ensure_dirs"
```

---

## Task 2: extract_job_id

**Files:**
- Modify: `jobs_store.py`
- Modify: `tests/test_jobs_store.py`

- [ ] **Step 1: Write failing tests for extract_job_id**

Append to `tests/test_jobs_store.py`:

```python
import pytest


def test_extract_job_id_from_jobs_url():
    url = "https://www.upwork.com/jobs/~022050144604938339008"
    assert jobs_store.extract_job_id(url) == "022050144604938339008"


def test_extract_job_id_from_apply_url():
    url = "https://www.upwork.com/nx/proposals/job/~01abc123def/apply/"
    assert jobs_store.extract_job_id(url) == "01abc123def"


def test_extract_job_id_with_trailing_query_or_fragment():
    url = "https://www.upwork.com/jobs/~022050144604938339008?ref=foo"
    assert jobs_store.extract_job_id(url) == "022050144604938339008"


def test_extract_job_id_raises_on_no_match():
    with pytest.raises(ValueError):
        jobs_store.extract_job_id("https://example.com/foo")
```

- [ ] **Step 2: Run, confirm failing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 4 new failures with `AttributeError: module 'jobs_store' has no attribute 'extract_job_id'`.

- [ ] **Step 3: Implement extract_job_id**

Append to `jobs_store.py`:

```python
import re

_JOB_ID_RE = re.compile(r"~([0-9a-f]+)")


def extract_job_id(url: str) -> str:
    """Pull the hex job-id (without the leading ~) from any Upwork job URL."""
    m = _JOB_ID_RE.search(url)
    if not m:
        raise ValueError(f"No ~<hex> job-id found in URL: {url!r}")
    return m.group(1)
```

- [ ] **Step 4: Run, confirm passing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add jobs_store.py tests/test_jobs_store.py
git commit -m "Add extract_job_id to jobs_store"
```

---

## Task 3: build_apply_url + URL safety guard

**Files:**
- Modify: `jobs_store.py`
- Modify: `tests/test_jobs_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jobs_store.py`:

```python
def test_build_apply_url():
    job_url = "https://www.upwork.com/jobs/~022050144604938339008"
    assert (
        jobs_store.build_apply_url(job_url)
        == "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/"
    )


def test_is_safe_apply_url_accepts_well_formed():
    assert jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~01abc/apply/"
    )


def test_is_safe_apply_url_rejects_other_hosts():
    assert not jobs_store.is_safe_apply_url(
        "https://evil.com/nx/proposals/job/~01abc/apply/"
    )


def test_is_safe_apply_url_rejects_missing_trailing_slash():
    assert not jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~01abc/apply"
    )


def test_is_safe_apply_url_rejects_non_hex_id():
    assert not jobs_store.is_safe_apply_url(
        "https://www.upwork.com/nx/proposals/job/~XYZ/apply/"
    )
```

- [ ] **Step 2: Run, confirm failing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 5 new failures.

- [ ] **Step 3: Implement build_apply_url + is_safe_apply_url**

Append to `jobs_store.py`:

```python
_APPLY_URL_RE = re.compile(
    r"^https://www\.upwork\.com/nx/proposals/job/~[0-9a-f]+/apply/$"
)


def build_apply_url(job_url: str) -> str:
    """Transform a job URL into the direct apply-page URL."""
    job_id = extract_job_id(job_url)
    return f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/"


def is_safe_apply_url(url: str) -> bool:
    """Strict allowlist match for the apply URL pattern."""
    return bool(_APPLY_URL_RE.match(url))
```

- [ ] **Step 4: Run, confirm passing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add jobs_store.py tests/test_jobs_store.py
git commit -m "Add build_apply_url and is_safe_apply_url"
```

---

## Task 4: write_pending + read + status-directory dedup

**Files:**
- Modify: `jobs_store.py`
- Modify: `tests/test_jobs_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jobs_store.py`:

```python
import json
from datetime import datetime


def _sample_job_data():
    return {
        "url": "https://www.upwork.com/jobs/~022050144604938339008",
        "title": "Senior AI Engineer",
        "found_at": "2026-05-01T14:32:00",
        "budget": "Fixed-price $1,750",
        "client": {"name": "Sarah", "country": "Lebanon"},
        "skills": ["Python", "LangChain"],
        "description": "We are looking for a senior AI engineer...",
        "doc_url": "https://docs.google.com/document/d/abc",
        "cover_letter": "Hey Sarah, I spent some time...",
    }


def test_write_pending_creates_file_with_id_and_apply_url(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    path = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    assert path == tmp_path / "pending" / "022050144604938339008.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["job_id"] == "022050144604938339008"
    assert data["apply_url"] == (
        "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/"
    )
    assert data["status_history"] == [
        {"status": "pending", "at": "2026-05-01T14:32:00"}
    ]


def test_read_job_round_trips(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    path = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    data = jobs_store.read_job(path)
    assert data["title"] == "Senior AI Engineer"


def test_is_known_job_id_finds_in_any_status(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    (tmp_path / "applied" / "022050144604938339008.json").write_text("{}")
    assert jobs_store.is_known_job_id("022050144604938339008", root=tmp_path)
    assert not jobs_store.is_known_job_id("999999", root=tmp_path)
```

- [ ] **Step 2: Run, confirm failing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 3 new failures.

- [ ] **Step 3: Implement write_pending, read_job, is_known_job_id**

Append to `jobs_store.py`:

```python
import json


def write_pending(job_data: dict, root: Path = DEFAULT_ROOT) -> Path:
    """Write the job JSON into jobs/pending/<id>.json. Returns the file path.

    `job_data` must include 'url' and 'found_at'. job_id and apply_url are
    derived; status_history is initialized.
    """
    job_id = extract_job_id(job_data["url"])
    apply_url = build_apply_url(job_data["url"])
    payload = {
        "job_id": job_id,
        "apply_url": apply_url,
        **job_data,
        "status_history": [
            {"status": "pending", "at": job_data["found_at"]},
        ],
    }
    path = root / "pending" / f"{job_id}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_job(path: Path) -> dict:
    """Load a job JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def is_known_job_id(job_id: str, root: Path = DEFAULT_ROOT) -> bool:
    """Return True if <job_id>.json exists under any status directory."""
    for status in STATUSES:
        if (root / status / f"{job_id}.json").exists():
            return True
    return False
```

- [ ] **Step 4: Run, confirm passing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add jobs_store.py tests/test_jobs_store.py
git commit -m "Add write_pending, read_job, is_known_job_id"
```

---

## Task 5: move_to_status with status_history append + atomic rename

**Files:**
- Modify: `jobs_store.py`
- Modify: `tests/test_jobs_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jobs_store.py`:

```python
def test_move_to_status_renames_and_appends_history(tmp_path, monkeypatch):
    jobs_store.ensure_dirs(tmp_path)
    src = jobs_store.write_pending(_sample_job_data(), root=tmp_path)

    # Freeze "now" so the appended timestamp is deterministic
    fixed_now = datetime(2026, 5, 1, 15, 0, 0)

    class FakeDateTime:
        @classmethod
        def now(cls):
            return fixed_now

    monkeypatch.setattr(jobs_store, "datetime", FakeDateTime)

    new_path = jobs_store.move_to_status(src, "awaiting_review", root=tmp_path)
    assert not src.exists()
    assert new_path == tmp_path / "awaiting_review" / "022050144604938339008.json"
    data = json.loads(new_path.read_text(encoding="utf-8"))
    assert data["status_history"][-1] == {
        "status": "awaiting_review",
        "at": "2026-05-01T15:00:00",
    }


def test_move_to_status_rejects_unknown_status(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    src = jobs_store.write_pending(_sample_job_data(), root=tmp_path)
    with pytest.raises(ValueError):
        jobs_store.move_to_status(src, "bogus", root=tmp_path)
```

- [ ] **Step 2: Run, confirm failing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 2 new failures.

- [ ] **Step 3: Implement move_to_status**

In `jobs_store.py`, add the import at the top (next to existing imports):

```python
from datetime import datetime
```

Then append:

```python
def move_to_status(path: Path, new_status: str, root: Path = DEFAULT_ROOT) -> Path:
    """Move a job JSON into jobs/<new_status>/, append status_history, return new path."""
    if new_status not in STATUSES:
        raise ValueError(f"Unknown status {new_status!r}; must be one of {STATUSES}")
    data = read_job(path)
    data.setdefault("status_history", []).append({
        "status": new_status,
        "at": datetime.now().isoformat(timespec="seconds"),
    })
    job_id = data["job_id"]
    new_path = root / new_status / f"{job_id}.json"
    new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if path.resolve() != new_path.resolve():
        path.unlink()
    return new_path
```

- [ ] **Step 4: Run, confirm passing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add jobs_store.py tests/test_jobs_store.py
git commit -m "Add move_to_status with status_history append"
```

---

## Task 6: pick_newest_pending_today

**Files:**
- Modify: `jobs_store.py`
- Modify: `tests/test_jobs_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_jobs_store.py`:

```python
def _write_pending_with_found_at(tmp_path, job_id, found_at_iso):
    (tmp_path / "pending" / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id,
        "url": f"https://www.upwork.com/jobs/~{job_id}",
        "apply_url": f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/",
        "title": f"Job {job_id}",
        "found_at": found_at_iso,
        "status_history": [{"status": "pending", "at": found_at_iso}],
    }, indent=2), encoding="utf-8")


def test_pick_newest_pending_today_returns_latest(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    _write_pending_with_found_at(tmp_path, "01aaa", "2026-05-01T09:00:00")
    _write_pending_with_found_at(tmp_path, "01bbb", "2026-05-01T14:00:00")
    _write_pending_with_found_at(tmp_path, "01ccc", "2026-05-01T11:30:00")

    path = jobs_store.pick_newest_pending_today(now=today, root=tmp_path)
    assert path is not None
    assert path.name == "01bbb.json"


def test_pick_newest_pending_today_ignores_other_days(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    _write_pending_with_found_at(tmp_path, "01yest", "2026-04-30T22:00:00")
    _write_pending_with_found_at(tmp_path, "01tom", "2026-05-02T01:00:00")

    assert jobs_store.pick_newest_pending_today(now=today, root=tmp_path) is None


def test_pick_newest_pending_today_returns_none_when_empty(tmp_path):
    jobs_store.ensure_dirs(tmp_path)
    today = datetime(2026, 5, 1, 12, 0, 0)
    assert jobs_store.pick_newest_pending_today(now=today, root=tmp_path) is None
```

- [ ] **Step 2: Run, confirm failing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 3 new failures.

- [ ] **Step 3: Implement pick_newest_pending_today**

Append to `jobs_store.py`:

```python
def pick_newest_pending_today(
    now: datetime | None = None,
    root: Path = DEFAULT_ROOT,
) -> Path | None:
    """Return the pending JSON path with the latest found_at falling on `now`'s
    local date. Returns None if no pending jobs match today."""
    now = now or datetime.now()
    today_date = now.date()
    pending_dir = root / "pending"
    if not pending_dir.exists():
        return None
    candidates: list[tuple[str, Path]] = []
    for p in pending_dir.glob("*.json"):
        try:
            data = read_job(p)
            found_at = datetime.fromisoformat(data["found_at"])
        except Exception:
            continue
        if found_at.date() == today_date:
            candidates.append((data["found_at"], p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def find_pending_by_id(job_id: str, root: Path = DEFAULT_ROOT) -> Path | None:
    """Return the path to jobs/pending/<id>.json or None if not present."""
    p = root / "pending" / f"{job_id}.json"
    return p if p.exists() else None
```

- [ ] **Step 4: Run, confirm passing**

```bash
uv run pytest tests/test_jobs_store.py -v
```
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add jobs_store.py tests/test_jobs_store.py
git commit -m "Add pick_newest_pending_today and find_pending_by_id"
```

---

## Task 7: Wire jobs_store into upwork_driver

**Files:**
- Modify: `upwork_driver.py`

- [ ] **Step 1: Add jobs_store import + ensure_dirs at startup**

In `upwork_driver.py`, after line 47 (`import proposal as proposal_mod`) add:

```python
import jobs_store
```

In `main()`, after the line `pacing.configure(...)` (currently around line 756), add:

```python
    jobs_store.ensure_dirs()
```

- [ ] **Step 2: Add cross-status dedup before processing each card**

In `upwork_driver.py`, in `main()`, find the per-card loop. Currently after `seen_titles.add(feed_info.title)` and before the title click. We don't yet have the URL at this point (URL is only captured after panel-open + judging), so the dedup check stays based on URL captured later. Therefore: leave title-click flow alone, but **add a new dedup point right after URL capture**, before the alert/proposal/notify path.

Find this block (around line 943):

```python
            # Dedup: skip alerts/proposals if we've already pinged this URL
            if url and url in seen_urls:
                log(f"  -> ALREADY ALERTED (url in seen_urls.txt) — not re-pinging")
                deduped += 1
                continue
```

Replace it with:

```python
            # Dedup: skip alerts/proposals if we've already pinged this URL
            if url and url in seen_urls:
                log(f"  -> ALREADY ALERTED (url in seen_urls.txt) — not re-pinging")
                deduped += 1
                continue

            # Cross-status dedup: skip if this job already exists in any
            # jobs/<status>/ directory (already queued or already applied).
            if url:
                try:
                    job_id_check = jobs_store.extract_job_id(url)
                    if jobs_store.is_known_job_id(job_id_check):
                        log(f"  -> ALREADY IN QUEUE (job_id={job_id_check}) — not re-queuing")
                        deduped += 1
                        continue
                except ValueError:
                    pass  # malformed URL — fall through and let later code handle it
```

- [ ] **Step 3: Add write_pending after Doc creation, before notify**

Continue in `upwork_driver.py`. Find the block that creates the Doc and substitutes the URL into the cover letter (around lines 970–985). Right after that block — after `p.cover_letter = p.cover_letter.replace("{DOC_URL}", ...)` — and **before** the `# Send Discord alert + proposal` block, add:

```python
            # Persist this job to jobs/pending/<id>.json BEFORE we notify, so
            # the apply queue is independent of Discord delivery.
            if p is not None and url:
                try:
                    job_data = {
                        "url": url,
                        "title": info.title,
                        "found_at": datetime.now().isoformat(timespec="seconds"),
                        "budget": info.budget,
                        "client": {"summary": info.client_summary},
                        "skills": info.tags or [],
                        "description": info.description,
                        "doc_url": p.doc_url,
                        "cover_letter": p.cover_letter,
                        "why_relevant": reason,
                    }
                    queue_path = jobs_store.write_pending(job_data)
                    log(f"  -> Queued for apply: {queue_path}")
                except Exception as ex:
                    log(f"  WARN: failed to queue job for apply: {ex}")
```

- [ ] **Step 4: Manual verification — startup creates dirs**

Delete any stray `jobs/` from the project root if present:
```bash
rm -rf jobs/
```

Run:
```bash
uv run python -c "import jobs_store; jobs_store.ensure_dirs()"
ls jobs/
```
Expected: lists `applied awaiting_review failed pending skipped`.

- [ ] **Step 5: Commit**

```bash
git add upwork_driver.py
git commit -m "Wire jobs_store into upwork_driver: ensure_dirs, dedup, write_pending"
```

---

## Task 8: ANSWER_SYSTEM prompt + generate_screening_answer

**Files:**
- Modify: `proposal.py`

- [ ] **Step 1: Add ANSWER_SYSTEM constant**

Open `proposal.py`. Locate the existing `DOC_PROPOSAL_SYSTEM` and `ABOUT_ME_SYSTEM` prompt constants (search for `DOC_PROPOSAL_SYSTEM = `). Right after the last of those system-prompt constants, add:

```python
ANSWER_SYSTEM = """\
You are answering a screening question on an Upwork job application, written
in the voice of a senior software engineer responding to a client.

Voice rules (HARD):
- First-person, conversational, direct.
- NO em-dashes (—), NO en-dashes (–), NO double-hyphens (--). Use commas.
- NO emojis.
- NO marketing fluff. Banned phrases include: "I'm passionate about",
  "robust solution", "leveraging cutting-edge", "I align well with your needs",
  "I have N years of experience".
- Don't repeat the cover letter content verbatim. The client will read both.
- Don't mention frameworks/tools unless the job names them or they obviously fit.
- Don't invent projects, clients, companies, or numbers. Only reference past
  work that appears in the provided portfolio data.

Length: 60-150 words. Not so short it looks lazy, not so long it looks padded.

Output: just the answer text. No preamble, no quotes, no labels."""
```

- [ ] **Step 2: Add generate_screening_answer function**

In `proposal.py`, append at the end of the file:

```python
def generate_screening_answer(
    question: str,
    job_description: str,
    cover_letter: str,
    portfolio_json: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """One LLM call to answer a single Upwork screening question.

    `portfolio_json` is the full text of portfolio.json (raw JSON string).
    Caller is responsible for stripping em-dashes from the returned text via
    the existing _strip_em_dashes helper if defense-in-depth is desired.
    """
    user_prompt = f"""\
QUESTION FROM CLIENT:
{question}

JOB DESCRIPTION (for context):
{job_description[:2000]}

COVER LETTER ALREADY SUBMITTED (do NOT repeat its content verbatim):
{cover_letter}

MY PORTFOLIO (raw JSON — pull only relevant past work):
{portfolio_json}

Write the answer now."""

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )
    text = (resp.choices[0].message.content or "").strip()
    return _strip_em_dashes(text)
```

- [ ] **Step 3: Manual verification — import works**

```bash
uv run python -c "from proposal import generate_screening_answer, ANSWER_SYSTEM; print(len(ANSWER_SYSTEM))"
```
Expected: prints a number > 500.

- [ ] **Step 4: Commit**

```bash
git add proposal.py
git commit -m "Add ANSWER_SYSTEM prompt and generate_screening_answer"
```

---

## Task 9: upwork_apply.py — skeleton + CLI + pick_next_job

**Files:**
- Create: `upwork_apply.py`

- [ ] **Step 1: Create the skeleton with CLI parsing and job-pick logic**

Create `upwork_apply.py`:

```python
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

from dotenv import load_dotenv

import jobs_store

WINDOW = "Upwork"


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


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

    log("Skeleton run complete (navigation + form fill not yet implemented).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test: no jobs case**

Make sure `jobs/pending/` is empty (move any test files aside if needed). Run:

```bash
uv run upwork_apply.py
```
Expected: prints `[apply] No pending jobs from today. Exiting.` and exits cleanly.

- [ ] **Step 3: Smoke-test: --job <id> not found**

```bash
uv run upwork_apply.py --job nonexistent
```
Expected: prints `[apply] --job nonexistent: not found in jobs/pending/` and `[apply] No pending jobs from today. Exiting.`

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py
git commit -m "Add upwork_apply.py skeleton with CLI and pick_next_job"
```

---

## Task 10: Navigation + wait_for_apply_form

**Files:**
- Modify: `upwork_apply.py`

- [ ] **Step 1: Add act/observe imports + WINDOW + helpers**

In `upwork_apply.py`, add to the imports section (after `import jobs_store`):

```python
import act
import observe
```

Add new constant after `WINDOW = "Upwork"`:

```python
APPLY_FORM_ANCHOR_TEXT = "cover letter"  # case-insensitive substring match


class ApplyFormNotFound(Exception):
    pass
```

Append new helpers before `main()`:

```python
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
```

- [ ] **Step 2: Use them in main(), guarded by --dry-run**

In `main()`, replace the final line `log("Skeleton run complete...")` with:

```python
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

    log("Apply form is up. Form-fill not yet implemented (next task).")
```

- [ ] **Step 3: Manual verification (requires running Chrome on the apply page)**

This step is interactive. Skip if no Chrome session is running. Otherwise:

1. Make sure `launch_chrome.py` was run for an Upwork-logged-in profile.
2. Manually navigate Chrome to a real apply URL (any open job's `/nx/proposals/job/~<id>/apply/`).
3. Wait for the page to load.
4. From a separate terminal run:
   ```bash
   uv run python -c "import upwork_apply; upwork_apply.wait_for_apply_form(timeout=5)"
   ```
   Expected: prints `Apply form anchor found: ...`. If it times out, run `uv run dump.py --window Upwork --text` and grep the output for `cover letter` (case-insensitive); if the actual text is different (e.g., `Cover letter`), update `APPLY_FORM_ANCHOR_TEXT`. The substring check is already case-insensitive; this step is to confirm the anchor exists at all.

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py
git commit -m "Add navigate_to_apply and wait_for_apply_form to upwork_apply"
```

---

## Task 11: Find + paste cover letter

**Files:**
- Modify: `upwork_apply.py`

> **Bootstrap warning:** the actual selector for the cover-letter textarea must be confirmed by dumping a real apply page (`uv run dump.py --window Upwork --text`). The implementation below uses a heuristic — "the editable element with the largest area whose vertical position is below the 'Cover letter' label" — that should work but may need tweaking. After Step 3, if the heuristic misses, revise based on the dump.

- [ ] **Step 1: Add pyperclip import + find_cover_letter_textarea + paste_cover_letter**

In `upwork_apply.py` add to imports:

```python
import pyperclip
```

Append before `main()`:

```python
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
```

- [ ] **Step 2: Wire into main()**

In `main()`, replace `log("Apply form is up. Form-fill not yet implemented (next task).")` with:

```python
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
```

- [ ] **Step 3: Manual verification (interactive)**

In Chrome, navigate manually to a real Upwork apply page where you have a pending job in `jobs/pending/`. With Chrome focused, run:

```bash
uv run upwork_apply.py --job <id-from-pending>
```

Expected: cover letter text appears in the cover-letter textarea. If it lands in the wrong field, run `uv run dump.py --window Upwork --text` and inspect the editable elements around the "Cover letter" label; tighten the heuristic in `find_cover_letter_textarea` based on what you see (e.g., constrain by minimum size or by position relative to other anchors).

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py
git commit -m "Add find_cover_letter_textarea and paste_cover_letter"
```

---

## Task 12: Detect + answer screening questions

**Files:**
- Modify: `upwork_apply.py`

> **Bootstrap warning:** the screening-question section has no fixed anchor on every job (only some jobs have it). The implementation below uses a fallback strategy: enumerate all editable elements that are below the cover-letter textarea AND above the bid section; their immediately-preceding text element is the question label. This must be validated against a real apply page that has questions.

- [ ] **Step 1: Add detect + generate + paste functions**

In `upwork_apply.py`, add at top:

```python
import json
from dataclasses import dataclass

import proposal
```

Append before `main()`:

```python
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

    # For each editable, find the nearest preceding text element vertically.
    text_elems = [e for e in obs.elements if e.role == "text" and (e.name or "").strip()]
    text_elems.sort(key=lambda e: e.bounds[1])

    questions: list[Question] = []
    for textarea in edits:
        label = ""
        for t in text_elems:
            if t.bounds[1] >= textarea.bounds[1]:
                break
            # Skip the cover-letter label and bid-section anchors
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
```

- [ ] **Step 2: Wire into main()**

In `main()`, replace `log("Screening-question handling not yet implemented (next task).")` with:

```python
    log("Detecting screening questions...")
    questions = detect_screening_questions()
    log(f"  Found {len(questions)} screening question(s).")
    answered = answer_and_paste_questions(questions, job, dry_run=args.dry_run)
    log(f"  Answered {answered}/{len(questions)} questions.")

    log("Form fill complete. Awaiting-review move + Discord notify next task.")
```

- [ ] **Step 3: Manual verification**

Find a job in your pending queue that has screening questions (or test on a job that has none — the script must handle both). Navigate Chrome to its apply URL. Run:

```bash
uv run upwork_apply.py --job <id> --dry-run
```

Expected: prints questions and proposed answers, pastes nothing. If labels look wrong (e.g., the script picks up unrelated text), inspect with `uv run dump.py --window Upwork --text` and adjust the label-association logic.

For a job with no screening questions, the same command should print `Found 0 screening question(s)` and complete.

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py
git commit -m "Add screening-question detection and answer-and-paste"
```

---

## Task 13: Move to awaiting_review + Discord notify

**Files:**
- Modify: `upwork_apply.py`
- Modify: `notify.py`

- [ ] **Step 1: Add a Discord notify helper for the review-needed message**

In `notify.py`, append at the end of the file:

```python
def send_review_needed(title: str, apply_url: str, questions_answered: int) -> bool:
    """Notify human that an apply form is filled and ready for review + Submit."""
    lines = [
        "**Apply form filled — review and submit:**",
        f"**{title}**",
        f"Questions answered: {questions_answered}",
        apply_url,
    ]
    return _post("\n".join(lines))
```

- [ ] **Step 2: Wire into upwork_apply.main()**

In `upwork_apply.py`, add to imports:

```python
import notify
```

Replace the line `log("Form fill complete. Awaiting-review move + Discord notify next task.")` with:

```python
    if args.dry_run:
        log("--dry-run: skipping JSON move and Discord notify.")
        return

    log("Moving job JSON to awaiting_review/...")
    try:
        new_path = jobs_store.move_to_status(job_path, "awaiting_review")
        log(f"  Moved to {new_path}")
    except Exception as ex:
        log(f"  WARN: failed to move JSON: {ex}")

    log("Sending Discord notification...")
    try:
        if notify.send_review_needed(
            title=job["title"],
            apply_url=job["apply_url"],
            questions_answered=answered,
        ):
            log("  Discord notification sent.")
        else:
            log("  Discord notification failed.")
    except Exception as ex:
        log(f"  Discord error: {ex}")

    log("Done. Human: review form in browser, set bid, click Submit.")
```

- [ ] **Step 3: Manual verification — Discord message format**

Without running the full flow, sanity-check the notify helper:

```bash
uv run python -c "import notify; print(notify.send_review_needed('Test Title', 'https://www.upwork.com/nx/proposals/job/~01abc/apply/', 2))"
```

Expected: prints `True` and a Discord message arrives in your channel. (If you don't want a real test message, skip this step.)

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py notify.py
git commit -m "Add awaiting_review move and review-needed Discord notify"
```

---

## Task 14: Top-level error handling + safety guards

**Files:**
- Modify: `upwork_apply.py`

- [ ] **Step 1: Wrap main flow in try/except for unhandled errors**

In `upwork_apply.py`, refactor `main()` so that everything from "Navigating to apply page..." through the Discord notify is inside a try/except. On unhandled exception, move JSON to `failed/` and send a Discord alert with the error.

Replace the body of `main()` AFTER the `pick_next_job` and safety-check block with:

```python
    if args.dry_run:
        log("--dry-run: skipping navigation and form interaction.")
        return

    answered = 0
    try:
        log("Navigating to apply page...")
        navigate_to_apply(job["apply_url"])

        log("Waiting for apply form to render (10s timeout)...")
        wait_for_apply_form(timeout=10.0)

        log("Pasting cover letter...")
        if not paste_cover_letter(job["cover_letter"]):
            raise RuntimeError("could not find or click cover-letter textarea")
        log("Cover letter pasted.")

        log("Detecting screening questions...")
        questions = detect_screening_questions()
        log(f"  Found {len(questions)} screening question(s).")
        answered = answer_and_paste_questions(questions, job, dry_run=False)
        log(f"  Answered {answered}/{len(questions)} questions.")

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
```

Append the helper before `main()`:

```python
def _move_to_failed(path: Path) -> None:
    try:
        jobs_store.move_to_status(path, "failed")
    except Exception as ex:
        log(f"  WARN: also failed to move JSON to failed/: {ex}")
```

- [ ] **Step 2: Verify hard-rule safety guard**

Confirm by inspection: search for `submit` (case-insensitive) and `bid` (case-insensitive) in `upwork_apply.py`. The only occurrences should be:
- The `_bid_section_y` anchor finder (used to bound the questions region)
- The string `"submit a proposal"` in that anchor list
- Comments / log messages
- No `act.click(...)` on a Submit/Apply button. No `act.type_text` into anything that resembles a bid amount field.

```bash
grep -in "submit\|bid" upwork_apply.py
```

Inspect the output and confirm no actual click/type into Submit or bid is present.

- [ ] **Step 3: Smoke-test --dry-run path still works**

```bash
uv run upwork_apply.py --dry-run
```
Expected: either "No pending jobs from today" or "--dry-run: skipping navigation and form interaction."

- [ ] **Step 4: Commit**

```bash
git add upwork_apply.py
git commit -m "Add unhandled-error path: move to failed/ and Discord alert"
```

---

## Task 15: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Prepare environment**

1. Confirm `.env` has `OPENAI_API_KEY`, `DISCORD_WEBHOOK_URL`, Composio keys.
2. Launch Chrome via `uv run launch_chrome.py --profile "Moazzam" --url "https://www.upwork.com/nx/find-work/" --kill-existing`.
3. Confirm `jobs/` directories exist (`uv run python -c "import jobs_store; jobs_store.ensure_dirs()"`).

- [ ] **Step 2: Run the scanner once to populate the queue**

```bash
uv run upwork_driver.py --reload-first --max-jobs 5
```
Expected: at least one relevant job is alerted on; check `jobs/pending/` afterwards:
```bash
ls jobs/pending/
```
Expected: one or more `*.json` files. Inspect one:
```bash
cat jobs/pending/<id>.json
```
Confirm it contains: `job_id`, `apply_url` (matches the safe pattern), `cover_letter`, `description`, `doc_url`, `status_history` with a single `pending` entry.

- [ ] **Step 3: Run the apply driver in dry-run first**

```bash
uv run upwork_apply.py --dry-run
```
Expected: prints picked job, prints planned actions, NO browser interaction.

- [ ] **Step 4: Run the real apply (with Chrome focused on Upwork tab)**

```bash
uv run upwork_apply.py
```

Expected:
- Browser navigates to the apply URL.
- Cover letter appears in the cover-letter textarea.
- Each screening question's textarea (if any) has an LLM-generated answer pasted.
- Bid section and Submit button are untouched.
- Job JSON is now in `jobs/awaiting_review/<id>.json`.
- Discord receives a "Apply form filled — review and submit" message.

- [ ] **Step 5: Verify post-state**

```bash
ls jobs/awaiting_review/
cat jobs/awaiting_review/<id>.json | python -c "import json, sys; d=json.load(sys.stdin); print(d['status_history'])"
```
Expected: history shows two entries: `pending` then `awaiting_review`, with the latter timestamp ~now.

- [ ] **Step 6: Verify dedup on a second scanner run**

Run the scanner again:
```bash
uv run upwork_driver.py --reload-first --max-jobs 5
```
The same job should NOT be re-queued (cross-status dedup). Confirm by checking the scanner's log output for `ALREADY IN QUEUE` and that `jobs/pending/<id>.json` does not reappear.

- [ ] **Step 7: Manually finish the application**

Browser is still on the apply page with cover letter + answers filled. Set the bid amount manually, click Submit. After submission:

```bash
mv jobs/awaiting_review/<id>.json jobs/applied/
```

(A small `mark-applied.py` helper is intentionally out of scope per the spec.)

- [ ] **Step 8: No commit needed (verification task)**

If any issues surfaced in steps 4-6, address them in follow-up commits and re-run the relevant manual steps.

---

## Self-review

**Spec coverage check (against `2026-05-01-upwork-apply-automation-design.md`):**

| Spec section | Plan task |
|---|---|
| File-system layout (5 status dirs) | Task 1 |
| Job JSON schema with `apply_url`, `status_history` | Task 4 |
| Scanner: `write_pending_job` after Doc gen, before notify | Task 7 step 3 |
| Scanner: cross-status dedup at the start of each job | Task 7 step 2 |
| Apply CLI (no args / `--job <id>` / `--dry-run`) | Task 9 |
| `pick_next_job()` newest-from-today | Task 6 + Task 9 |
| `navigate_to_apply()` + URL safety regex | Tasks 3 + 10 |
| `wait_for_apply_form()` polling for "Cover letter" anchor, 10s timeout | Task 10 |
| `paste_cover_letter()` clipboard paste | Task 11 |
| `detect_screening_questions()` heuristic with bootstrap warning | Task 12 |
| `generate_answer()` via gpt-4o-mini + ANSWER_SYSTEM | Task 8 |
| `paste_answer()` clipboard paste | Task 12 |
| `move_to_awaiting_review()` with status_history append | Tasks 5 + 13 |
| `notify_human_for_review()` Discord webhook | Task 13 |
| Failure handling (form-not-found, cover-letter not found, question detect fail, unhandled exception) | Tasks 10, 11, 14 |
| Hard rule: never click Submit, never touch bid | Task 14 step 2 (grep verification) |
| One job per run, no batch loop | Task 9 (CLI exits after one job) |

All spec sections covered.

**Placeholder scan:** No "TBD", no "TODO", no "implement later". Each step has either real code or an exact command + expected output. The two "Bootstrap warning" notes (Tasks 11, 12) are explicit about the heuristic-then-tighten approach that was approved in the spec — they include code AND a manual-verify step where the heuristic gets validated.

**Type / signature consistency:**

- `jobs_store.extract_job_id(url) -> str` — used in Tasks 2, 3, 7
- `jobs_store.build_apply_url(job_url) -> str` — Task 3
- `jobs_store.is_safe_apply_url(url) -> bool` — Tasks 3, 9
- `jobs_store.write_pending(job_data, root) -> Path` — Tasks 4, 7
- `jobs_store.read_job(path) -> dict` — Tasks 4, 5, 9
- `jobs_store.is_known_job_id(job_id, root) -> bool` — Tasks 4, 7
- `jobs_store.move_to_status(path, new_status, root) -> Path` — Tasks 5, 13, 14
- `jobs_store.pick_newest_pending_today(now, root) -> Path | None` — Tasks 6, 9
- `jobs_store.find_pending_by_id(job_id, root) -> Path | None` — Tasks 6, 9
- `jobs_store.ensure_dirs(root)` — Tasks 1, 7, 9
- `proposal.generate_screening_answer(question, job_description, cover_letter, portfolio_json, model) -> str` — Tasks 8, 12
- `notify.send_review_needed(title, apply_url, questions_answered) -> bool` — Tasks 13, 14
- `upwork_apply.find_cover_letter_textarea() -> Element | None` — Tasks 11, 12
- `upwork_apply.detect_screening_questions() -> list[Question]` — Task 12
- `upwork_apply.answer_and_paste_questions(questions, job, dry_run) -> int` — Tasks 12, 14
- `upwork_apply.navigate_to_apply(apply_url)` — Tasks 10, 14
- `upwork_apply.wait_for_apply_form(timeout, poll_interval)` — Tasks 10, 14

All names consistent across tasks.
