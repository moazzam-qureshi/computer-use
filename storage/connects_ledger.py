from __future__ import annotations
from datetime import datetime
from typing import Optional
from storage.connection import Database


class ConnectsLedgerStore:
    def __init__(self, db: Database):
        self._db = db

    def record(self, *, delta: int, reason: str, balance_after: Optional[int],
               order_id: Optional[int]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO connects_ledger (delta, reason, balance_after, order_id)
                    VALUES (%s, %s, %s, %s)
                """, (delta, reason, balance_after, order_id))

    def spent_in_window(self, start: datetime, end: datetime) -> int:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COALESCE(SUM(-delta), 0) FROM connects_ledger
                    WHERE delta < 0 AND occurred_at >= %s AND occurred_at < %s
                """, (start, end))
                return cur.fetchone()[0]
