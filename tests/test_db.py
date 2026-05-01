import json
from datetime import datetime
from pathlib import Path

import pytest

import db


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path: Path):
    path = tmp_path / "t.db"
    c = db.connect(path)
    db.init_schema(c)
    yield c
    c.close()


def _sample_job(url: str = "https://www.upwork.com/jobs/~022050144604938339008", **overrides):
    base = {
        "url": url,
        "title": "Senior AI Engineer",
        "description": "We are looking for a senior AI engineer with experience in LangChain.",
        "budget": "Hourly | $25.00-$50.00 | Expert | Ongoing project",
        "client_summary": "Payment verified | Rating 4.8/5 | $9.1K spent | Lebanon",
        "client_country": "Lebanon",
        "client_payment_verified": True,
        "client_rating": 4.8,
        "client_total_spent": "$9.1K",
        "skills": ["Python", "LangChain", "RAG"],
        "posted_at_text": "6 hours ago",
        "experience_level": "Expert",
        "project_type": "Ongoing project",
        "duration": "1 to 3 months",
        "weekly_hours": "Less than 30 hrs/week",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------------
# URL helpers (kept verbatim from old jobs_store)
# ----------------------------------------------------------------------------

def test_extract_job_id_from_jobs_url():
    assert db.extract_job_id("https://www.upwork.com/jobs/~022050144604938339008") == "022050144604938339008"


def test_extract_job_id_from_apply_url():
    assert db.extract_job_id("https://www.upwork.com/nx/proposals/job/~01abc/apply/") == "01abc"


def test_extract_job_id_raises_on_no_match():
    with pytest.raises(ValueError):
        db.extract_job_id("https://example.com/foo")


def test_build_apply_url():
    out = db.build_apply_url("https://www.upwork.com/jobs/~022050144604938339008")
    assert out == "https://www.upwork.com/nx/proposals/job/~022050144604938339008/apply/"


def test_is_safe_apply_url_accepts_well_formed():
    assert db.is_safe_apply_url("https://www.upwork.com/nx/proposals/job/~01abc/apply/")


def test_is_safe_apply_url_rejects_other_hosts():
    assert not db.is_safe_apply_url("https://evil.com/nx/proposals/job/~01abc/apply/")


def test_is_safe_apply_url_rejects_missing_slash():
    assert not db.is_safe_apply_url("https://www.upwork.com/nx/proposals/job/~01abc/apply")


def test_is_safe_apply_url_rejects_non_hex():
    assert not db.is_safe_apply_url("https://www.upwork.com/nx/proposals/job/~XYZ/apply/")


# ----------------------------------------------------------------------------
# Budget parser
# ----------------------------------------------------------------------------

def test_parse_budget_fixed_price_single():
    btype, bmin, bmax = db.parse_budget("Fixed-price | $1,750.00")
    assert btype == "Fixed-price"
    assert bmin == 1750.0
    assert bmax == 1750.0


def test_parse_budget_hourly_range():
    btype, bmin, bmax = db.parse_budget("Hourly | $25.00-$50.00 | Expert")
    assert btype == "Hourly"
    assert bmin == 25.0
    assert bmax == 50.0


def test_parse_budget_empty():
    assert db.parse_budget("") == (None, None, None)
    assert db.parse_budget(None) == (None, None, None)


def test_parse_budget_no_money():
    btype, bmin, bmax = db.parse_budget("Hourly | Expert | Ongoing project")
    assert btype == "Hourly"
    assert bmin is None
    assert bmax is None


# ----------------------------------------------------------------------------
# Schema init + connect
# ----------------------------------------------------------------------------

def test_init_schema_creates_jobs_and_scrape_events(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [r[0] for r in cur.fetchall()]
    assert "jobs" in names
    assert "scrape_events" in names


def test_init_schema_idempotent(conn):
    db.init_schema(conn)
    db.init_schema(conn)
    cur = conn.execute("SELECT count(*) FROM jobs")
    assert cur.fetchone()[0] == 0


# ----------------------------------------------------------------------------
# upsert_job
# ----------------------------------------------------------------------------

def test_upsert_job_inserts_new(conn):
    is_new = db.upsert_job(conn, _sample_job(), source="feed")
    assert is_new is True
    cur = conn.execute("SELECT job_id, source, apply_status FROM jobs")
    row = cur.fetchone()
    assert row["job_id"] == "022050144604938339008"
    assert row["source"] == "feed"
    assert row["apply_status"] == "none"


def test_upsert_job_parses_budget_into_columns(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    cur = conn.execute("SELECT budget_type, budget_min, budget_max FROM jobs")
    row = cur.fetchone()
    assert row["budget_type"] == "Hourly"
    assert row["budget_min"] == 25.0
    assert row["budget_max"] == 50.0


def test_upsert_job_serializes_skills_as_json(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    cur = conn.execute("SELECT skills FROM jobs")
    row = cur.fetchone()
    assert json.loads(row["skills"]) == ["Python", "LangChain", "RAG"]


def test_upsert_job_preserves_discovered_at_on_update(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    cur = conn.execute("SELECT discovered_at FROM jobs")
    first_discovered = cur.fetchone()["discovered_at"]

    # Re-upsert with different source — discovered_at must NOT change
    db.upsert_job(conn, _sample_job(title="Updated Title"), source="search:LLM")
    cur = conn.execute("SELECT discovered_at, title, source, last_scraped_at FROM jobs")
    row = cur.fetchone()
    assert row["discovered_at"] == first_discovered
    assert row["title"] == "Updated Title"
    assert row["source"] == "search:LLM"
    # last_scraped_at must be set (>= first_discovered)
    assert row["last_scraped_at"] >= first_discovered


def test_upsert_job_returns_false_on_update(conn):
    assert db.upsert_job(conn, _sample_job(), source="feed") is True
    assert db.upsert_job(conn, _sample_job(), source="feed") is False


# ----------------------------------------------------------------------------
# scrape_events
# ----------------------------------------------------------------------------

def test_record_scrape_event_appends(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    job_id = "022050144604938339008"

    db.record_scrape_event(
        conn, job_id, source="feed",
        proposals_text="20 to 50",
        posted_at_text="6 hours ago",
        client_total_spent="$9.1K",
    )
    db.record_scrape_event(
        conn, job_id, source="search:LLM",
        proposals_text="50+",
        posted_at_text="1 day ago",
        client_total_spent="$10K",
    )

    cur = conn.execute("SELECT count(*), source FROM scrape_events GROUP BY source ORDER BY source")
    rows = cur.fetchall()
    assert rows[0]["count(*)"] == 1
    assert rows[0]["source"] == "feed"
    assert rows[1]["count(*)"] == 1
    assert rows[1]["source"] == "search:LLM"


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------

def test_is_known_job_id(conn):
    assert db.is_known_job_id(conn, "022050144604938339008") is False
    db.upsert_job(conn, _sample_job(), source="feed")
    assert db.is_known_job_id(conn, "022050144604938339008") is True


def test_get_job_returns_dict(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    job = db.get_job(conn, "022050144604938339008")
    assert job is not None
    assert job["title"] == "Senior AI Engineer"
    assert job["apply_status"] == "none"


def test_get_job_returns_none_when_absent(conn):
    assert db.get_job(conn, "nonexistent") is None


def test_find_pending_by_id_only_when_pending(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    job_id = "022050144604938339008"
    # status is 'none' — find_pending should miss
    assert db.find_pending_by_id(conn, job_id) is None
    # After mark_apply_pending it should hit
    db.mark_apply_pending(conn, job_id, doc_url="d", cover_letter="cl", why_relevant="r")
    job = db.find_pending_by_id(conn, job_id)
    assert job is not None
    assert job["cover_letter"] == "cl"


def test_pick_newest_pending_today_returns_latest(conn):
    today = datetime(2026, 5, 2, 12, 0, 0)
    # Insert three pending jobs by direct SQL with controlled discovered_at
    for jid, ts in (
        ("01aaa", "2026-05-02T09:00:00"),
        ("01bbb", "2026-05-02T14:00:00"),
        ("01ccc", "2026-05-02T11:30:00"),
    ):
        db.upsert_job(conn, _sample_job(url=f"https://www.upwork.com/jobs/~{jid}"), source="feed")
        # Override discovered_at directly for deterministic test
        conn.execute(
            "UPDATE jobs SET discovered_at = ?, apply_status = 'pending' WHERE job_id = ?",
            (ts, jid),
        )
    conn.commit()

    job = db.pick_newest_pending_today(conn, now=today)
    assert job is not None
    assert job["job_id"] == "01bbb"


def test_pick_newest_pending_today_ignores_other_days(conn):
    today = datetime(2026, 5, 2, 12, 0, 0)
    db.upsert_job(conn, _sample_job(url="https://www.upwork.com/jobs/~01yest"), source="feed")
    conn.execute(
        "UPDATE jobs SET discovered_at = '2026-05-01T22:00:00', apply_status = 'pending' WHERE job_id = '01yest'"
    )
    conn.commit()
    assert db.pick_newest_pending_today(conn, now=today) is None


def test_pick_newest_pending_today_empty(conn):
    today = datetime(2026, 5, 2, 12, 0, 0)
    assert db.pick_newest_pending_today(conn, now=today) is None


# ----------------------------------------------------------------------------
# Status transitions
# ----------------------------------------------------------------------------

def test_set_apply_status_appends_history(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    job_id = "022050144604938339008"
    db.mark_apply_pending(conn, job_id, doc_url="d", cover_letter="cl", why_relevant="r")
    db.set_apply_status(conn, job_id, "awaiting_review")

    job = db.get_job(conn, job_id)
    assert job["apply_status"] == "awaiting_review"
    history = json.loads(job["apply_status_history"])
    assert len(history) == 2
    assert history[0]["status"] == "pending"
    assert history[1]["status"] == "awaiting_review"


def test_set_apply_status_rejects_unknown(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    with pytest.raises(ValueError):
        db.set_apply_status(conn, "022050144604938339008", "bogus")


def test_set_apply_status_rejects_missing_job(conn):
    with pytest.raises(ValueError):
        db.set_apply_status(conn, "missing", "applied")


def test_mark_apply_pending_sets_proposal_fields(conn):
    db.upsert_job(conn, _sample_job(), source="feed")
    job_id = "022050144604938339008"
    db.mark_apply_pending(
        conn, job_id,
        doc_url="https://docs.google.com/d/abc",
        cover_letter="Hey...",
        why_relevant="Hourly + ongoing",
    )
    job = db.get_job(conn, job_id)
    assert job["apply_status"] == "pending"
    assert job["doc_url"] == "https://docs.google.com/d/abc"
    assert job["cover_letter"] == "Hey..."
    assert job["why_relevant"] == "Hourly + ongoing"
    history = json.loads(job["apply_status_history"])
    assert history == [{"status": "pending", "at": history[0]["at"]}]
