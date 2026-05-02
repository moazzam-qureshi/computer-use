import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.scrape_runs import ScrapeRunStore

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_start_update_finish(fresh_db):
    store = ScrapeRunStore(fresh_db)
    run_id = store.start(source="feed")
    assert run_id > 0

    store.update_counts(run_id, jobs_seen=20, jobs_new=5, jobs_signaled=2)
    store.finish(run_id, notes="all good")

    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source, jobs_seen, jobs_new, jobs_signaled, finished_at, notes FROM scrape_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
    assert row[0] == "feed"
    assert row[1] == 20
    assert row[2] == 5
    assert row[3] == 2
    assert row[4] is not None  # finished_at set
    assert row[5] == "all good"
