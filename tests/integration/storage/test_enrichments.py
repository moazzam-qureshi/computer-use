import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.jobs import JobStore
from storage.enrichments import EnrichmentStore
from domain.types import Job
from ai.schemas import Enrichment

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_upsert_and_get(fresh_db):
    JobStore(fresh_db).upsert(Job(job_id="~e1", url="https://x", title="T", skills=[]),
                              source="feed", raw_panel={})
    store = EnrichmentStore(fresh_db)
    e = Enrichment(extracted_tech=["rag"], pain_points=["slow eval"], project_shape="mvp")
    store.upsert("~e1", e)
    fetched = store.get("~e1")
    assert fetched is not None
    assert fetched.extracted_tech == ["rag"]
    assert fetched.project_shape == "mvp"

    # Upsert again with different fields — verify update, not duplicate
    e2 = Enrichment(extracted_tech=["pinecone"], pain_points=["high cost"], project_shape="integration")
    store.upsert("~e1", e2)
    updated = store.get("~e1")
    assert updated.extracted_tech == ["pinecone"]
    assert updated.project_shape == "integration"
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM job_enrichments WHERE job_id = '~e1'")
            assert cur.fetchone()[0] == 1
