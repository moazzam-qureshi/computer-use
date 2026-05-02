import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from storage.migrate import apply_migrations
from storage.connects_ledger import ConnectsLedgerStore

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_record_and_spent_in_window(fresh_db):
    store = ConnectsLedgerStore(fresh_db)
    now = datetime.now(timezone.utc)
    # Record a debit of 6 connects and a credit of 10
    store.record(delta=-6, reason="proposal_submit", balance_after=44, order_id=None)
    store.record(delta=10, reason="refill", balance_after=54, order_id=None)

    start = now - timedelta(minutes=1)
    end = now + timedelta(minutes=1)
    spent = store.spent_in_window(start, end)
    # Only negative deltas are summed (negated), so 6
    assert spent == 6
