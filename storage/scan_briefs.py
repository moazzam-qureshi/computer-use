"""BriefStore: pending/running/done one-shot scan briefs the agent triggers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from psycopg.types.json import Json

from storage.connection import Database


@dataclass
class Brief:
    brief_id: int
    prose: str
    filter_dsl: dict
    requested_by_conversation_id: Optional[int]
    requested_at: datetime
    consumed_at: Optional[datetime]
    finished_at: Optional[datetime]
    status: str
    cycle_notes: Optional[str]
    notified_at: Optional[datetime]
    result_summary: Optional[dict]
    setup_id: Optional[int]


def _row_to_brief(row) -> Brief:
    return Brief(
        brief_id=row[0], prose=row[1], filter_dsl=row[2],
        requested_by_conversation_id=row[3], requested_at=row[4],
        consumed_at=row[5], finished_at=row[6], status=row[7],
        cycle_notes=row[8], notified_at=row[9], result_summary=row[10],
        setup_id=row[11],
    )


_COLS = """
    brief_id, prose, filter_dsl, requested_by_conversation_id, requested_at,
    consumed_at, finished_at, status, cycle_notes, notified_at, result_summary,
    setup_id
""".strip()


class BriefStore:
    def __init__(self, db: Database):
        self._db = db

    def request(
        self,
        *,
        prose: str,
        filter_dsl: dict,
        requested_by_conversation_id: Optional[int],
    ) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scan_briefs (prose, filter_dsl, requested_by_conversation_id)
                    VALUES (%s, %s, %s)
                    RETURNING brief_id
                    """,
                    (prose, Json(filter_dsl), requested_by_conversation_id),
                )
                return cur.fetchone()[0]

    def consume_pending(self) -> Optional[Brief]:
        """Atomic: oldest pending row -> status=running, consumed_at=now(); returns the row."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE scan_briefs
                    SET status = 'running', consumed_at = now()
                    WHERE brief_id = (
                        SELECT brief_id FROM scan_briefs
                        WHERE status = 'pending'
                        ORDER BY requested_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING {_COLS}
                    """,
                )
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)

    def attach_setup(self, brief_id: int, setup_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scan_briefs SET setup_id = %s WHERE brief_id = %s",
                    (setup_id, brief_id),
                )

    def mark_done(
        self,
        brief_id: int,
        *,
        result_summary: dict,
        cycle_notes: Optional[str],
    ) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scan_briefs
                    SET status = 'done', finished_at = now(),
                        result_summary = %s, cycle_notes = %s
                    WHERE brief_id = %s
                    """,
                    (Json(result_summary), cycle_notes, brief_id),
                )

    def mark_failed(self, brief_id: int, *, error: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scan_briefs
                    SET status = 'failed', finished_at = now(), cycle_notes = %s
                    WHERE brief_id = %s
                    """,
                    (error, brief_id),
                )

    def next_unnotified(self) -> Optional[Brief]:
        """Oldest done|failed brief whose summary has not yet been DM'd to the operator."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_COLS} FROM scan_briefs
                    WHERE status IN ('done', 'failed') AND notified_at IS NULL
                    ORDER BY finished_at ASC
                    LIMIT 1
                    """,
                )
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)

    def mark_notified(self, brief_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE scan_briefs SET notified_at = now() WHERE brief_id = %s",
                    (brief_id,),
                )

    def get(self, brief_id: int) -> Optional[Brief]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLS} FROM scan_briefs WHERE brief_id = %s", (brief_id,))
                row = cur.fetchone()
                return None if row is None else _row_to_brief(row)
