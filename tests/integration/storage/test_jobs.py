import pytest
from datetime import datetime, timezone
from storage.migrate import apply_migrations
from storage.jobs import JobStore
from domain.types import Job
from pathlib import Path


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _job() -> Job:
    return Job(
        job_id="~abc123",
        url="https://www.upwork.com/jobs/~abc123",
        title="Build a RAG eval pipeline",
        description="we need eval",
        budget_kind="fixed",
        budget_min_usd=4000.0,
        budget_max_usd=4000.0,
        skills=["RAG", "Pinecone"],
        client_country="United States",
        client_payment_verified=True,
    )


def test_upsert_and_fetch(fresh_db):
    store = JobStore(fresh_db)
    store.upsert(_job(), source="feed", raw_panel={})
    fetched = store.get("~abc123")
    assert fetched is not None
    assert fetched.title.startswith("Build a RAG")
    assert fetched.skills == ["RAG", "Pinecone"]


def test_upsert_idempotent(fresh_db):
    store = JobStore(fresh_db)
    store.upsert(_job(), source="feed", raw_panel={})
    store.upsert(_job(), source="feed", raw_panel={})
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jobs")
            assert cur.fetchone()[0] == 1


def test_is_known(fresh_db):
    store = JobStore(fresh_db)
    assert store.is_known("~abc123") is False
    store.upsert(_job(), source="feed", raw_panel={})
    assert store.is_known("~abc123") is True
