"""OrderStore + OutcomeEventStore."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from psycopg.types.json import Json

from domain.types import Order, OutcomeEvent
from storage.connection import Database


class OrderStore:
    def __init__(self, db: Database):
        self._db = db

    def create_draft(self, order: Order) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (
                      signal_id, job_id, setup_id, status,
                      bid_amount_usd, connects_spent,
                      cover_letter_body, doc_url, screening_answers_json,
                      drafted_at, idempotency_key
                    ) VALUES (%s, %s, %s, %s,
                              %s, %s,
                              %s, %s, %s,
                              now(), %s)
                    RETURNING order_id
                """, (
                    order.signal_id, order.job_id, order.setup_id, order.status,
                    order.bid_amount_usd, order.connects_spent,
                    order.cover_letter_body, order.doc_url,
                    Json(order.screening_answers_json) if order.screening_answers_json else None,
                    order.idempotency_key,
                ))
                return cur.fetchone()[0]

    def update_status(self, order_id: int, new_status: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                if new_status == "submitted":
                    cur.execute("UPDATE orders SET status = %s, submitted_at = now() WHERE order_id = %s",
                                (new_status, order_id))
                elif new_status == "approved":
                    cur.execute("UPDATE orders SET status = %s, approved_at = now() WHERE order_id = %s",
                                (new_status, order_id))
                else:
                    cur.execute("UPDATE orders SET status = %s WHERE order_id = %s",
                                (new_status, order_id))

    def set_drafted(self, order_id: int, cover_letter_body: str, doc_url: str, screening_answers_json: Optional[dict]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE orders
                    SET cover_letter_body = %s, doc_url = %s, screening_answers_json = %s, drafted_at = now()
                    WHERE order_id = %s
                """, (cover_letter_body, doc_url,
                      Json(screening_answers_json) if screening_answers_json else None, order_id))

    def mark_submitted(self, order_id: int) -> None:
        self.update_status(order_id, "submitted")

    def get(self, order_id: int) -> Optional[Order]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT order_id, signal_id, job_id, setup_id, status,
                           bid_amount_usd, connects_spent,
                           cover_letter_body, doc_url, screening_answers_json,
                           drafted_at, approved_at, submitted_at, failed_reason, idempotency_key
                    FROM orders WHERE order_id = %s
                """, (order_id,))
                r = cur.fetchone()
                if r is None:
                    return None
        return Order(
            order_id=r[0], signal_id=r[1], job_id=r[2], setup_id=r[3], status=r[4],
            bid_amount_usd=float(r[5]) if r[5] is not None else None,
            connects_spent=r[6], cover_letter_body=r[7], doc_url=r[8],
            screening_answers_json=r[9],
            drafted_at=r[10], approved_at=r[11], submitted_at=r[12],
            failed_reason=r[13], idempotency_key=r[14],
        )

    def list_by_status(self, status: str) -> List[Order]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT order_id FROM orders WHERE status = %s ORDER BY order_id", (status,))
                ids = [r[0] for r in cur.fetchall()]
        return [self.get(oid) for oid in ids]

    def count_submitted_today(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware; submitted_at is timestamptz")
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'submitted' AND submitted_at >= %s", (start,))
                return cur.fetchone()[0]

    def count_submitted_this_week(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware; submitted_at is timestamptz")
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'submitted' AND submitted_at >= %s", (start,))
                return cur.fetchone()[0]

    def last_submitted_at(self) -> Optional[datetime]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(submitted_at) FROM orders WHERE status = 'submitted'")
                return cur.fetchone()[0]


class OutcomeEventStore:
    def __init__(self, db: Database):
        self._db = db

    def record(self, order_id: int, event_type: str, source: str, notes: Optional[str]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO outcome_events (order_id, event_type, source, notes)
                    VALUES (%s, %s, %s, %s)
                """, (order_id, event_type, source, notes))

    def list_for_order(self, order_id: int) -> List[OutcomeEvent]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, order_id, event_type, observed_at, source, notes
                    FROM outcome_events WHERE order_id = %s ORDER BY observed_at
                """, (order_id,))
                rows = cur.fetchall()
        return [OutcomeEvent(id=r[0], order_id=r[1], event_type=r[2], observed_at=r[3], source=r[4], notes=r[5])
                for r in rows]
