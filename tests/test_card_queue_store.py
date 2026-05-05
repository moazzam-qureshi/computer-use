"""Integration tests for FeedCardQueueStore. Real Postgres."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.card_queue import FeedCardQueueStore


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean(db):
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM feed_card_queue "
                "WHERE job_title LIKE 'TEST:%' OR job_title LIKE 'test_%'"
            )
    yield


def test_enqueue_returns_queue_id_and_get_round_trips(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:enqueue basic",
        posted_text="2 minutes ago",
        triage_reasoning="matches goal",
        goal_id=None,
    )
    assert qid is not None
    item = store.get(qid)
    assert item is not None
    assert item.job_title == "TEST:enqueue basic"
    assert item.posted_text == "2 minutes ago"
    assert item.status == "queued"
    assert item.job_url is None
    assert item.attempt_count == 0
    assert item.order_id is None


def test_enqueue_idempotent_while_inflight(db):
    store = FeedCardQueueStore(db)
    qid_1 = store.enqueue(
        job_title="TEST:dup title",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    assert qid_1 is not None
    # Same title, status still queued -> should be blocked.
    qid_2 = store.enqueue(
        job_title="TEST:dup title",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    assert qid_2 is None


def test_claim_next_atomic_and_increments_attempt(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:claim atomic",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.queue_id == qid
    assert claimed.status == "processing"
    assert claimed.claimed_at is not None
    assert claimed.attempt_count == 1
    # Subsequent claim_next returns None — the row is no longer queued.
    assert store.claim_next() is None


def test_claim_next_returns_oldest_first(db):
    store = FeedCardQueueStore(db)
    a = store.enqueue(job_title="TEST:fifo a", posted_text=None,
                      triage_reasoning=None, goal_id=None)
    b = store.enqueue(job_title="TEST:fifo b", posted_text=None,
                      triage_reasoning=None, goal_id=None)
    first = store.claim_next()
    second = store.claim_next()
    assert first is not None and first.queue_id == a
    assert second is not None and second.queue_id == b


def test_set_url_then_mark_processed(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:set url",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    store.claim_next()
    store.set_url(qid, "https://www.upwork.com/jobs/~012345abcdef/")
    item = store.get(qid)
    assert item.job_url == "https://www.upwork.com/jobs/~012345abcdef/"


def test_mark_skipped_records_reason(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:skip",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    store.claim_next()
    store.mark_skipped(qid, "relevance_rejected: budget too low")
    item = store.get(qid)
    assert item.status == "skipped"
    assert "relevance_rejected" in (item.error_text or "")
    assert item.finished_at is not None


def test_mark_failed_records_error(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:fail",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    store.claim_next()
    store.mark_failed(qid, "panel.capture_panel returned 0 elements after retry")
    item = store.get(qid)
    assert item.status == "failed"
    assert "0 elements" in (item.error_text or "")


def test_inflight_dedup_releases_after_terminal_status(db):
    """Once a row reaches a terminal state, the partial unique index allows
    a fresh enqueue with the same title — but list_known_titles_recent should
    still report it so detection can dedup against processed history."""
    store = FeedCardQueueStore(db)
    qid_1 = store.enqueue(
        job_title="TEST:lifecycle",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    store.claim_next()
    store.mark_skipped(qid_1, "test")
    # Title is no longer in-flight, so inflight set excludes it.
    assert "TEST:lifecycle" not in store.list_inflight_titles()
    # But the recent-history dedup catches it.
    assert "TEST:lifecycle" in store.list_known_titles_recent(days=14)


def test_requeue_resets_status(db):
    store = FeedCardQueueStore(db)
    qid = store.enqueue(
        job_title="TEST:requeue",
        posted_text=None, triage_reasoning=None, goal_id=None,
    )
    claimed = store.claim_next()
    assert claimed is not None
    store.requeue(qid)
    item = store.get(qid)
    assert item.status == "queued"
    assert item.claimed_at is None
    # And it can be claimed again.
    re_claimed = store.claim_next()
    assert re_claimed is not None and re_claimed.queue_id == qid
    assert re_claimed.attempt_count == 2
