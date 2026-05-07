"""Integration test: MarketCorpusStore idempotent ingestion.

Card-level scans don't have real Upwork URLs. The corpus deduplicates via a
hash of (title, posted_text, source). Re-running the same query within a
window must not produce duplicate rows.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.market_corpus import MarketCorpusStore
from storage.migrate import apply_migrations
from upwork.search_driver import CardResult


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _cards():
    return [
        CardResult(
            title="Build a RAG eval pipeline",
            snippet="Need a senior LLM eng for Ragas-based evals",
            budget_kind="hourly",
            budget_min_usd=60.0,
            budget_max_usd=90.0,
            budget_text="Hourly: $60.00 - $90.00",
            posted_text="20 minutes ago",
            posted_at=datetime.now(timezone.utc),
            skills=["RAG", "Python", "LangChain"],
            client_country="United States",
            payment_verified=True,
        ),
        CardResult(
            title="Voice AI agent for customer support",
            snippet="Need a Twilio + LiveKit voice agent",
            budget_kind="fixed",
            budget_min_usd=5000.0,
            budget_max_usd=5000.0,
            budget_text="Fixed-price",
            posted_text="2 hours ago",
            posted_at=datetime.now(timezone.utc),
            skills=["Voice AI", "Twilio"],
            client_country="Germany",
            payment_verified=True,
        ),
    ]


def test_first_ingest_inserts_new_rows(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    result = corpus.ingest_cards(_cards(), source="ba:rag engineer|payment_verified=1")
    assert result == {"inserted": 2, "updated_existing": 0}

    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT source FROM jobs LIMIT 1")
            assert cur.fetchone()[0] == "ba:rag engineer|payment_verified=1"


def test_repeat_ingest_is_idempotent(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    source = "ba:rag engineer"
    corpus.ingest_cards(_cards(), source=source)
    second = corpus.ingest_cards(_cards(), source=source)

    # Second pass: same hashes, so all cards match existing rows.
    assert second == {"inserted": 0, "updated_existing": 2}

    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            assert cur.fetchone()[0] == 2  # no duplicates


def test_different_source_creates_new_rows(fresh_db):
    """Same listing surfaced under two BA queries should record both — they're
    different evidence (which queries are surfacing this job matters for trend
    analysis)."""
    corpus = MarketCorpusStore(fresh_db)
    corpus.ingest_cards(_cards(), source="ba:rag")
    corpus.ingest_cards(_cards(), source="ba:llm engineer")

    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            assert cur.fetchone()[0] == 4  # 2 cards × 2 sources


def test_titleless_cards_dropped(fresh_db):
    """Defense in depth — the search-driver lift drops these, but corpus
    must too in case anyone constructs CardResults directly."""
    corpus = MarketCorpusStore(fresh_db)
    cards = [CardResult(title="")]
    result = corpus.ingest_cards(cards, source="ba:test")
    assert result == {"inserted": 0, "updated_existing": 0}


def test_empty_source_rejected(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    with pytest.raises(ValueError):
        corpus.ingest_cards(_cards(), source="")


def test_skills_persisted(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    corpus.ingest_cards(_cards(), source="ba:test")
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.name FROM job_skills js
                JOIN skills s ON s.skill_id = js.skill_id
                ORDER BY s.name
            """)
            names = [r[0] for r in cur.fetchall()]
    assert set(names) == {"RAG", "Python", "LangChain", "Voice AI", "Twilio"}


# ----- ingest_jobs (deep-scan path) -----

def _deep_jobs():
    """Domain Job objects with REAL Upwork URLs — what deep_search returns."""
    from domain.types import Job
    return [
        Job(
            job_id="~01abc111",
            url="https://www.upwork.com/jobs/Build-RAG-eval_~01abc111",
            title="Build a RAG eval pipeline",
            description="Full description text from the panel walk...",
            budget_kind="hourly",
            budget_min_usd=60.0, budget_max_usd=90.0,
            skills=["RAG", "LangChain"],
            client_country="United States",
            client_payment_verified=True,
        ),
        Job(
            job_id="~01abc222",
            url="https://www.upwork.com/jobs/Voice-AI-agent_~01abc222",
            title="Voice AI agent for support desk",
            description="Need a Twilio + LiveKit voice agent...",
            budget_kind="fixed",
            budget_min_usd=5000.0, budget_max_usd=5000.0,
            skills=["Voice AI", "Twilio"],
            client_country="Germany",
            client_payment_verified=True,
        ),
    ]


def test_ingest_jobs_first_pass_inserts(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    result = corpus.ingest_jobs(_deep_jobs(), source="ba:rag deep")
    assert result == {"inserted": 2, "updated_existing": 0}
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT description FROM jobs WHERE job_id = '~01abc111'"
            )
            desc = cur.fetchone()[0]
            assert "Full description" in desc


def test_ingest_jobs_repeat_is_idempotent(fresh_db):
    """Same jobs ingested twice — second pass updates, no new rows."""
    corpus = MarketCorpusStore(fresh_db)
    corpus.ingest_jobs(_deep_jobs(), source="ba:rag deep")
    second = corpus.ingest_jobs(_deep_jobs(), source="ba:rag deep")
    assert second == {"inserted": 0, "updated_existing": 2}
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            assert cur.fetchone()[0] == 2


def test_ingest_jobs_skips_titleless(fresh_db):
    from domain.types import Job
    corpus = MarketCorpusStore(fresh_db)
    bad = Job(job_id="~xyz", url="https://www.upwork.com/jobs/x_~xyz",
              title="")
    result = corpus.ingest_jobs([bad], source="ba:test")
    assert result == {"inserted": 0, "updated_existing": 0}


def test_ingest_jobs_empty_source_rejected(fresh_db):
    corpus = MarketCorpusStore(fresh_db)
    with pytest.raises(ValueError):
        corpus.ingest_jobs(_deep_jobs(), source="")
