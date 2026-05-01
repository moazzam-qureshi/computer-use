"""
SQLite data access layer.

Single file `upwork.db` is the source of truth for every job we've ever seen
(feed-discovered, search-discovered, applied to). This module is the only
thing that talks to it.

Pure functions, no UIA, no LLM, no network — just SQLite + JSON.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = Path("upwork.db")

APPLY_STATUSES = ("none", "pending", "awaiting_review", "applied", "skipped", "failed")

_JOB_ID_RE = re.compile(r"~([0-9a-f]+)")
_APPLY_URL_RE = re.compile(
    r"^https://www\.upwork\.com/nx/proposals/job/~[0-9a-f]+/apply/$"
)


# ----------------------------------------------------------------------------
# URL helpers (kept verbatim from the old jobs_store)
# ----------------------------------------------------------------------------

def extract_job_id(url: str) -> str:
    """Pull the hex job-id (without the leading ~) from any Upwork job URL."""
    m = _JOB_ID_RE.search(url)
    if not m:
        raise ValueError(f"No ~<hex> job-id found in URL: {url!r}")
    return m.group(1)


def build_apply_url(job_url: str) -> str:
    """Transform a job URL into the direct apply-page URL."""
    job_id = extract_job_id(job_url)
    return f"https://www.upwork.com/nx/proposals/job/~{job_id}/apply/"


def is_safe_apply_url(url: str) -> bool:
    """Strict allowlist match for the apply URL pattern."""
    return bool(_APPLY_URL_RE.match(url))


# ----------------------------------------------------------------------------
# Connection + schema
# ----------------------------------------------------------------------------

def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with Row factory and FK enforcement."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    apply_url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    budget TEXT,
    budget_type TEXT,
    budget_min REAL,
    budget_max REAL,
    client_summary TEXT,
    client_country TEXT,
    client_payment_verified INTEGER,
    client_rating REAL,
    client_total_spent TEXT,
    skills TEXT,                       -- JSON array
    posted_at_text TEXT,
    experience_level TEXT,
    project_type TEXT,
    duration TEXT,
    weekly_hours TEXT,
    source TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    last_scraped_at TEXT NOT NULL,
    doc_url TEXT,
    cover_letter TEXT,
    why_relevant TEXT,
    apply_status TEXT NOT NULL DEFAULT 'none',
    apply_status_changed_at TEXT,
    apply_status_history TEXT          -- JSON array of {status, at}
);

CREATE INDEX IF NOT EXISTS idx_jobs_apply_status ON jobs(apply_status);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);

CREATE TABLE IF NOT EXISTS scrape_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    source TEXT NOT NULL,
    proposals_text TEXT,
    posted_at_text TEXT,
    client_total_spent TEXT,
    raw_panel_json TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_scrape_events_job_id ON scrape_events(job_id);
CREATE INDEX IF NOT EXISTS idx_scrape_events_scraped_at ON scrape_events(scraped_at);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if absent. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _bool_to_int(v) -> int | None:
    """Tri-state bool → SQLite-friendly. None passes through; truthy/falsy → 1/0."""
    if v is None:
        return None
    return 1 if v else 0


# ----------------------------------------------------------------------------
# Budget parsing
# ----------------------------------------------------------------------------

# Matches "$10.00", "$10.00-$25.00", "$1,750.00"
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_FIXED_PRICE_RE = re.compile(r"fixed[- ]price", re.I)
_HOURLY_RE = re.compile(r"\bhourly\b", re.I)


def parse_budget(budget: str | None) -> tuple[str | None, float | None, float | None]:
    """Pull (budget_type, min, max) from a free-form budget string.

    Returns (None, None, None) if nothing parseable. budget_type is "Hourly",
    "Fixed-price", or None. min/max are floats; for fixed-price single values
    both are equal; for hourly ranges min < max.
    """
    if not budget:
        return None, None, None
    btype: str | None = None
    if _FIXED_PRICE_RE.search(budget):
        btype = "Fixed-price"
    elif _HOURLY_RE.search(budget):
        btype = "Hourly"
    money_strs = _MONEY_RE.findall(budget)
    money_vals: list[float] = []
    for m in money_strs:
        try:
            money_vals.append(float(m.replace(",", "")))
        except ValueError:
            pass
    if not money_vals:
        return btype, None, None
    if len(money_vals) == 1:
        return btype, money_vals[0], money_vals[0]
    return btype, min(money_vals), max(money_vals)


# ----------------------------------------------------------------------------
# Upsert + scrape events
# ----------------------------------------------------------------------------

# Columns we update on a re-scrape (everything observed at scrape time).
_UPSERT_OBSERVED_COLS = (
    "url", "apply_url", "title", "description",
    "budget", "budget_type", "budget_min", "budget_max",
    "client_summary", "client_country", "client_payment_verified",
    "client_rating", "client_total_spent",
    "skills", "posted_at_text", "experience_level",
    "project_type", "duration", "weekly_hours",
    "source", "last_scraped_at",
)


def upsert_job(conn: sqlite3.Connection, job_data: dict, source: str) -> bool:
    """Insert or update a job row. Returns True if newly inserted, False if updated.

    `job_data` must include `url`. Optional keys: title, description, budget,
    client_summary, client_country, client_payment_verified, client_rating,
    client_total_spent, skills (list), posted_at_text, experience_level,
    project_type, duration, weekly_hours.

    `discovered_at` is preserved across updates (set only on first insert).
    `last_scraped_at` is always set to now.
    """
    job_id = extract_job_id(job_data["url"])
    apply_url = build_apply_url(job_data["url"])
    now = _now_iso()
    btype, bmin, bmax = parse_budget(job_data.get("budget"))
    skills = job_data.get("skills") or []
    skills_json = json.dumps(skills, ensure_ascii=False)

    cur = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
    is_new = cur.fetchone() is None

    if is_new:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, url, apply_url, title, description,
                budget, budget_type, budget_min, budget_max,
                client_summary, client_country, client_payment_verified,
                client_rating, client_total_spent,
                skills, posted_at_text, experience_level,
                project_type, duration, weekly_hours,
                source, discovered_at, last_scraped_at,
                apply_status, apply_status_history
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                'none', '[]'
            )
            """,
            (
                job_id, job_data["url"], apply_url, job_data.get("title"), job_data.get("description"),
                job_data.get("budget"), btype, bmin, bmax,
                job_data.get("client_summary"), job_data.get("client_country"),
                _bool_to_int(job_data.get("client_payment_verified")),
                job_data.get("client_rating"), job_data.get("client_total_spent"),
                skills_json, job_data.get("posted_at_text"), job_data.get("experience_level"),
                job_data.get("project_type"), job_data.get("duration"), job_data.get("weekly_hours"),
                source, now, now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE jobs SET
                url = ?, apply_url = ?, title = ?, description = ?,
                budget = ?, budget_type = ?, budget_min = ?, budget_max = ?,
                client_summary = ?, client_country = ?, client_payment_verified = ?,
                client_rating = ?, client_total_spent = ?,
                skills = ?, posted_at_text = ?, experience_level = ?,
                project_type = ?, duration = ?, weekly_hours = ?,
                source = ?, last_scraped_at = ?
            WHERE job_id = ?
            """,
            (
                job_data["url"], apply_url, job_data.get("title"), job_data.get("description"),
                job_data.get("budget"), btype, bmin, bmax,
                job_data.get("client_summary"), job_data.get("client_country"),
                _bool_to_int(job_data.get("client_payment_verified")),
                job_data.get("client_rating"), job_data.get("client_total_spent"),
                skills_json, job_data.get("posted_at_text"), job_data.get("experience_level"),
                job_data.get("project_type"), job_data.get("duration"), job_data.get("weekly_hours"),
                source, now,
                job_id,
            ),
        )
    conn.commit()
    return is_new


def record_scrape_event(
    conn: sqlite3.Connection,
    job_id: str,
    source: str,
    proposals_text: str | None = None,
    posted_at_text: str | None = None,
    client_total_spent: str | None = None,
    raw_panel_json: str | None = None,
) -> None:
    """Insert a row into scrape_events. Append-only time-series."""
    conn.execute(
        """
        INSERT INTO scrape_events (
            job_id, scraped_at, source,
            proposals_text, posted_at_text, client_total_spent,
            raw_panel_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id, _now_iso(), source,
            proposals_text, posted_at_text, client_total_spent,
            raw_panel_json,
        ),
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------

def is_known_job_id(conn: sqlite3.Connection, job_id: str) -> bool:
    """Return True if a row exists in `jobs` with this job_id."""
    cur = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
    return cur.fetchone() is not None


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """Fetch the full row as a dict, or None if not found."""
    cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def find_pending_by_id(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """Return job dict if it exists AND apply_status is 'pending', else None."""
    cur = conn.execute(
        "SELECT * FROM jobs WHERE job_id = ? AND apply_status = 'pending'",
        (job_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def pick_newest_pending_today(
    conn: sqlite3.Connection,
    now: datetime | None = None,
) -> dict | None:
    """Return the pending job dict with the latest discovered_at on now's
    local date, or None."""
    now = now or datetime.now()
    today = now.date().isoformat()
    cur = conn.execute(
        """
        SELECT * FROM jobs
        WHERE apply_status = 'pending'
          AND date(discovered_at) = ?
        ORDER BY discovered_at DESC
        LIMIT 1
        """,
        (today,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ----------------------------------------------------------------------------
# Status transitions
# ----------------------------------------------------------------------------

def set_apply_status(conn: sqlite3.Connection, job_id: str, new_status: str) -> None:
    """Transition apply_status, append to apply_status_history, update timestamp."""
    if new_status not in APPLY_STATUSES:
        raise ValueError(
            f"Unknown apply_status {new_status!r}; must be one of {APPLY_STATUSES}"
        )
    cur = conn.execute(
        "SELECT apply_status_history FROM jobs WHERE job_id = ?",
        (job_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"No job with job_id={job_id!r}")
    try:
        history = json.loads(row["apply_status_history"] or "[]")
    except json.JSONDecodeError:
        history = []
    now = _now_iso()
    history.append({"status": new_status, "at": now})
    conn.execute(
        """
        UPDATE jobs SET
            apply_status = ?,
            apply_status_changed_at = ?,
            apply_status_history = ?
        WHERE job_id = ?
        """,
        (new_status, now, json.dumps(history, ensure_ascii=False), job_id),
    )
    conn.commit()


def mark_apply_pending(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    doc_url: str | None,
    cover_letter: str | None,
    why_relevant: str | None,
) -> None:
    """Set apply_status='pending' and write the proposal fields. Used by the
    scanner when it queues a relevant job for the apply driver."""
    cur = conn.execute(
        "SELECT apply_status_history FROM jobs WHERE job_id = ?",
        (job_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"No job with job_id={job_id!r}; upsert_job before mark_apply_pending")
    try:
        history = json.loads(row["apply_status_history"] or "[]")
    except json.JSONDecodeError:
        history = []
    now = _now_iso()
    history.append({"status": "pending", "at": now})
    conn.execute(
        """
        UPDATE jobs SET
            apply_status = 'pending',
            apply_status_changed_at = ?,
            apply_status_history = ?,
            doc_url = ?,
            cover_letter = ?,
            why_relevant = ?
        WHERE job_id = ?
        """,
        (
            now, json.dumps(history, ensure_ascii=False),
            doc_url, cover_letter, why_relevant,
            job_id,
        ),
    )
    conn.commit()
