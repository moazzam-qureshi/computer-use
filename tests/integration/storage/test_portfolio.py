import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.portfolio import PortfolioStore

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_add_and_list_all(fresh_db):
    store = PortfolioStore(fresh_db)
    store.add(name="P1", summary=None, client_context="ctx", outcome="out",
              tech=["python", "fastapi"], relevance_tags=["rag"], year_completed=2025)
    items = store.list_all()
    assert len(items) == 1
    assert items[0]["name"] == "P1"


def test_list_matching_tags(fresh_db):
    store = PortfolioStore(fresh_db)
    store.add(name="P1", summary=None, client_context="", outcome="",
              tech=[], relevance_tags=["rag", "vector-search"], year_completed=None)
    store.add(name="P2", summary=None, client_context="", outcome="",
              tech=[], relevance_tags=["voice", "elevenlabs"], year_completed=None)
    rag_matches = store.list_matching_tags(["rag", "pinecone"])
    assert len(rag_matches) == 1
    assert rag_matches[0]["name"] == "P1"
