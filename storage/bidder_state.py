"""BidderStateStore: read/write the singleton bidder_state row.

The bidder loop polls this every iteration to decide whether to run a cycle,
honor a force-run, or sleep through a pause. Discord slash commands write
control flags here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from storage.connection import Database


@dataclass
class BidderState:
    paused: bool
    paused_reason: Optional[str]
    paused_at: Optional[datetime]
    force_run_requested: bool
    force_run_requested_at: Optional[datetime]
    last_cycle_started_at: Optional[datetime]
    last_cycle_finished_at: Optional[datetime]
    last_cycle_status: Optional[str]
    last_cycle_notes: Optional[str]


class BidderStateStore:
    def __init__(self, db: Database):
        self._db = db

    def get(self) -> BidderState:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT paused, paused_reason, paused_at,
                           force_run_requested, force_run_requested_at,
                           last_cycle_started_at, last_cycle_finished_at,
                           last_cycle_status, last_cycle_notes
                    FROM bidder_state WHERE id = 1
                """)
                row = cur.fetchone()
        # Migration 003 seeds the row, so this should always exist. Defensive
        # default keeps the bidder running if someone wiped the table by hand.
        if row is None:
            return BidderState(
                paused=False, paused_reason=None, paused_at=None,
                force_run_requested=False, force_run_requested_at=None,
                last_cycle_started_at=None, last_cycle_finished_at=None,
                last_cycle_status=None, last_cycle_notes=None,
            )
        return BidderState(
            paused=row[0], paused_reason=row[1], paused_at=row[2],
            force_run_requested=row[3], force_run_requested_at=row[4],
            last_cycle_started_at=row[5], last_cycle_finished_at=row[6],
            last_cycle_status=row[7], last_cycle_notes=row[8],
        )

    def pause(self, reason: Optional[str] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET paused = true,
                        paused_reason = %s,
                        paused_at = now()
                    WHERE id = 1
                """, (reason,))

    def resume(self) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET paused = false,
                        paused_reason = NULL,
                        paused_at = NULL
                    WHERE id = 1
                """)

    def request_force_run(self) -> None:
        """Set the force-run flag. The bidder loop sees this on its next
        iteration and starts a cycle immediately, bypassing the diurnal
        envelope. The flag is cleared by consume_force_run() once the bidder
        picks it up, so two rapid /bidder run-now commands collapse to one
        cycle (as long as the loop's poll interval is short enough).
        """
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET force_run_requested = true,
                        force_run_requested_at = now()
                    WHERE id = 1
                """)

    def consume_force_run(self) -> bool:
        """Atomic check-and-clear. Returns True if a force-run was pending
        (and clears the flag), False otherwise. Called by the bidder loop.
        """
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET force_run_requested = false,
                        force_run_requested_at = NULL
                    WHERE id = 1 AND force_run_requested = true
                    RETURNING 1
                """)
                return cur.fetchone() is not None

    def record_cycle_start(self) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET last_cycle_started_at = now(),
                        last_cycle_status = NULL,
                        last_cycle_notes = NULL
                    WHERE id = 1
                """)

    def record_cycle_finish(self, status: str, notes: Optional[str] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bidder_state
                    SET last_cycle_finished_at = now(),
                        last_cycle_status = %s,
                        last_cycle_notes = %s
                    WHERE id = 1
                """, (status, notes))
