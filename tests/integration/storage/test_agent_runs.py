import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.agent_runs import AgentRunStore

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_start_and_finish_run(fresh_db):
    store = AgentRunStore(fresh_db)
    run_id = store.start(agent_name="relevance", trigger="scheduled", trigger_context={"x": 1})
    assert run_id > 0
    store.finish(run_id=run_id, status="succeeded", total_tokens=100, total_cost_usd=0.001, output_summary="done")
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, total_tokens FROM agent_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
            assert row[0] == "succeeded"
            assert row[1] == 100
