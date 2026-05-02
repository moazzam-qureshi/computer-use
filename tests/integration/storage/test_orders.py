import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from storage.migrate import apply_migrations
from storage.setups import SetupStore, SignalStore
from storage.jobs import JobStore
from storage.orders import OrderStore, OutcomeEventStore
from domain.types import Job, Setup, Signal, FilterDsl, Order

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _seed(fresh_db) -> tuple[int, int, str]:
    """Seed a job + setup + signal; return (signal_id, setup_id, job_id)."""
    JobStore(fresh_db).upsert(
        Job(job_id="~j1", url="https://x", title="T", skills=[]), source="feed", raw_panel={},
    )
    setup_id = SetupStore(fresh_db).create(Setup(
        setup_id=None, name="s1", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition=None,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    signal_id = SignalStore(fresh_db).create(Signal(
        signal_id=None, job_id="~j1", primary_setup_id=setup_id,
        matched_setups=[], fired_at=None, market_state={},
    ))
    return signal_id, setup_id, "~j1"


def test_create_draft_and_get(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    order = Order(
        order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
        status="drafting", bid_amount_usd=None, connects_spent=None,
        cover_letter_body=None, doc_url=None, screening_answers_json=None,
        drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
        idempotency_key="key-1",
    )
    oid = store.create_draft(order)
    fetched = store.get(oid)
    assert fetched.status == "drafting"
    assert fetched.idempotency_key == "key-1"


def test_idempotency_key_unique(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    o1 = Order(order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
               status="drafting", bid_amount_usd=None, connects_spent=None,
               cover_letter_body=None, doc_url=None, screening_answers_json=None,
               drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
               idempotency_key="dup")
    store.create_draft(o1)
    import psycopg
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.create_draft(o1)


def test_count_submitted_today(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    now = datetime.now(timezone.utc)
    # Insert 2 submitted today, 1 yesterday
    for i, when in enumerate([now, now - timedelta(hours=1), now - timedelta(days=1)]):
        with fresh_db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (signal_id, job_id, setup_id, status, submitted_at, idempotency_key)
                    VALUES (%s, %s, %s, 'submitted', %s, %s)
                """, (signal_id, job_id, setup_id, when, f"k{i}"))
    assert store.count_submitted_today(now) == 2
    assert store.count_submitted_this_week(now) == 3


def test_outcome_event_record(fresh_db):
    signal_id, setup_id, job_id = _seed(fresh_db)
    store = OrderStore(fresh_db)
    oid = store.create_draft(Order(
        order_id=None, signal_id=signal_id, job_id=job_id, setup_id=setup_id,
        status="drafting", bid_amount_usd=None, connects_spent=None,
        cover_letter_body=None, doc_url=None, screening_answers_json=None,
        drafted_at=None, approved_at=None, submitted_at=None, failed_reason=None,
        idempotency_key="oe1",
    ))
    oe = OutcomeEventStore(fresh_db)
    oe.record(order_id=oid, event_type="viewed", source="discord_manual", notes=None)
    events = oe.list_for_order(oid)
    assert len(events) == 1
    assert events[0].event_type == "viewed"
