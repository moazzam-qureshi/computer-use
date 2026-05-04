"""Integration tests for BriefStore."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.scan_briefs import BriefStore, Brief


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean(db):
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scan_briefs WHERE prose LIKE 'TEST:%'")
    yield


def test_request_then_consume_pending(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:scan for python", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    assert bid is not None

    brief = store.consume_pending()
    assert brief is not None
    assert brief.brief_id == bid
    assert brief.status == "running"
    # Subsequent consume returns None — the row is no longer pending.
    assert store.consume_pending() is None


def test_consume_pending_marks_running_atomically(db):
    """consume_pending must be atomic: status=pending -> status=running, consumed_at=now()."""
    store = BriefStore(db)
    bid = store.request(prose="TEST:atomic", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    brief = store.consume_pending()
    assert brief is not None
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, consumed_at FROM scan_briefs WHERE brief_id=%s", (bid,))
            row = cur.fetchone()
    assert row[0] == "running"
    assert row[1] is not None


def test_mark_done_records_summary(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:done", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    store.consume_pending()
    store.mark_done(bid, result_summary={"jobs_scanned": 10, "drafts_created": 2},
                     cycle_notes="ok")
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, finished_at, result_summary, cycle_notes FROM scan_briefs WHERE brief_id=%s",
                (bid,),
            )
            row = cur.fetchone()
    assert row[0] == "done"
    assert row[1] is not None
    assert row[2] == {"jobs_scanned": 10, "drafts_created": 2}
    assert row[3] == "ok"


def test_mark_failed(db):
    store = BriefStore(db)
    bid = store.request(prose="TEST:fail", filter_dsl={"all_of": []},
                         requested_by_conversation_id=None)
    store.consume_pending()
    store.mark_failed(bid, error="chrome unavailable")
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, cycle_notes FROM scan_briefs WHERE brief_id=%s", (bid,))
            row = cur.fetchone()
    assert row[0] == "failed"
    assert "chrome unavailable" in row[1]


def test_next_unnotified_returns_done_briefs_in_order(db):
    store = BriefStore(db)
    bid1 = store.request(prose="TEST:a", filter_dsl={}, requested_by_conversation_id=None)
    bid2 = store.request(prose="TEST:b", filter_dsl={}, requested_by_conversation_id=None)
    store.consume_pending()
    store.consume_pending()
    store.mark_done(bid1, result_summary={"x": 1}, cycle_notes=None)
    store.mark_done(bid2, result_summary={"x": 2}, cycle_notes=None)
    first = store.next_unnotified()
    assert first is not None and first.brief_id == bid1
    store.mark_notified(bid1)
    second = store.next_unnotified()
    assert second is not None and second.brief_id == bid2
    store.mark_notified(bid2)
    assert store.next_unnotified() is None
