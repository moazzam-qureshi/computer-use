"""FeedCardQueueStore: Phase 2.B detection -> processing handoff queue.

Pass 1 (detection) enqueues by job_title (URL not yet known).
Pass 2 (processing) claims oldest queued row, walks panel to capture URL,
runs goal-relevance + drafting, marks the row processed/skipped/failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from storage.connection import Database


@dataclass
class QueueItem:
    queue_id: int
    job_url: Optional[str]
    job_title: str
    posted_text: Optional[str]
    detected_at: datetime
    triage_reasoning: Optional[str]
    status: str
    claimed_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_text: Optional[str]
    attempt_count: int
    order_id: Optional[int]
    goal_id_at_detect: Optional[int]


_COLS = """
    queue_id, job_url, job_title, posted_text, detected_at, triage_reasoning,
    status, claimed_at, finished_at, error_text, attempt_count, order_id,
    goal_id_at_detect
""".strip()


def _row_to_item(row) -> QueueItem:
    return QueueItem(
        queue_id=row[0], job_url=row[1], job_title=row[2], posted_text=row[3],
        detected_at=row[4], triage_reasoning=row[5], status=row[6],
        claimed_at=row[7], finished_at=row[8], error_text=row[9],
        attempt_count=row[10], order_id=row[11], goal_id_at_detect=row[12],
    )


class FeedCardQueueStore:
    def __init__(self, db: Database):
        self._db = db

    def enqueue(
        self,
        *,
        job_title: str,
        posted_text: Optional[str],
        triage_reasoning: Optional[str],
        goal_id: Optional[int],
    ) -> Optional[int]:
        """Insert a new queued row. Idempotent: if a row with this title already
        exists in status queued|processing, returns None (the partial unique
        index blocks the duplicate).
        """
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feed_card_queue
                        (job_title, posted_text, triage_reasoning, goal_id_at_detect)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (job_title)
                        WHERE status IN ('queued', 'processing')
                    DO NOTHING
                    RETURNING queue_id
                    """,
                    (job_title, posted_text, triage_reasoning, goal_id),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def list_inflight_titles(self) -> set[str]:
        """Titles currently queued or being processed. Pass 1 dedup input."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_title FROM feed_card_queue "
                    "WHERE status IN ('queued', 'processing')"
                )
                return {row[0] for row in cur.fetchall() if row[0]}

    def list_known_titles_recent(self, days: int = 14) -> set[str]:
        """Titles seen in the queue within the last N days, regardless of
        status. Pass 1 dedups against this so we don't keep re-triaging the
        same cards across cycles after they've been processed/skipped/failed.
        """
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_title FROM feed_card_queue WHERE detected_at >= %s",
                    (cutoff,),
                )
                return {row[0] for row in cur.fetchall() if row[0]}

    def claim_next(self) -> Optional[QueueItem]:
        """Atomic UPDATE ... FOR UPDATE SKIP LOCKED on the oldest queued row.
        Sets status=processing, claimed_at=now(), increments attempt_count.
        """
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE feed_card_queue
                    SET status = 'processing',
                        claimed_at = now(),
                        attempt_count = attempt_count + 1
                    WHERE queue_id = (
                        SELECT queue_id FROM feed_card_queue
                        WHERE status = 'queued'
                        ORDER BY detected_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING {_COLS}
                    """
                )
                row = cur.fetchone()
                return None if row is None else _row_to_item(row)

    def set_url(self, queue_id: int, job_url: str) -> None:
        """Pass 2 calls this once it captures the URL via the panel."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE feed_card_queue SET job_url = %s WHERE queue_id = %s",
                    (job_url, queue_id),
                )

    def mark_processed(self, queue_id: int, order_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE feed_card_queue
                    SET status = 'processed',
                        finished_at = now(),
                        order_id = %s
                    WHERE queue_id = %s
                    """,
                    (order_id, queue_id),
                )

    def mark_skipped(self, queue_id: int, reason: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE feed_card_queue
                    SET status = 'skipped',
                        finished_at = now(),
                        error_text = %s
                    WHERE queue_id = %s
                    """,
                    (reason[:500], queue_id),
                )

    def mark_failed(self, queue_id: int, error: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE feed_card_queue
                    SET status = 'failed',
                        finished_at = now(),
                        error_text = %s
                    WHERE queue_id = %s
                    """,
                    (error[:500], queue_id),
                )

    def requeue(self, queue_id: int) -> None:
        """Reset a row to queued. Used when Pass 2 walked the feed but didn't
        find the title in the current viewport — the card may surface again
        next cycle, so we let it retry rather than burning the row."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE feed_card_queue
                    SET status = 'queued', claimed_at = NULL
                    WHERE queue_id = %s
                    """,
                    (queue_id,),
                )

    def get(self, queue_id: int) -> Optional[QueueItem]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM feed_card_queue WHERE queue_id = %s",
                    (queue_id,),
                )
                row = cur.fetchone()
                return None if row is None else _row_to_item(row)
