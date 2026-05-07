"""FindingStore: persistence + dedup for Researcher findings.

A finding is the Researcher's specific actionable output — see
docs/superpowers/specs/2026-05-05-researcher-design.md §5.5 for the
shape and the "specific not statistical" framing.

Dedup is contractual at the DB level: dedup_key is UNIQUE in the
researcher_findings table. compute_dedup_key produces a deterministic
hash of (finding_type + sorted evidence_job_ids) so the same finding
detected on a subsequent Researcher pass can't double-insert.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.types.json import Json

from storage.connection import Database


@dataclass
class ResearcherFinding:
    finding_id: Optional[int]   # None when constructed pre-insert
    detected_at: Optional[datetime]
    finding_type: str
    headline: str
    why_specific: str
    portfolio_tie: str
    suggested_action: str
    urgency: str                # 'this_week' | 'this_month' | 'monitor'
    evidence_job_ids: list[str]
    raw_llm_response: Optional[dict] = None
    status: str = "new"         # 'new' | 'nudged' | 'dismissed' | 'snoozed'
    nudged_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    dismissed_reason: Optional[str] = None
    snoozed_until: Optional[datetime] = None
    dedup_key: Optional[str] = None  # computed if absent at insert time

    def compute_dedup_key(self) -> str:
        return compute_dedup_key(self.finding_type, self.evidence_job_ids)


def compute_dedup_key(finding_type: str, evidence_job_ids: list[str]) -> str:
    """Deterministic: same (type, sorted(jobs)) -> same key.

    Order-insensitive on evidence_job_ids so two passes that surface the
    same evidence in different orders still dedup. The hash truncates to
    16 hex chars (64 bits) — collision risk negligible at our scale.
    """
    h = hashlib.sha256()
    h.update(finding_type.encode("utf-8"))
    h.update(b"|")
    h.update(",".join(sorted(evidence_job_ids)).encode("utf-8"))
    return h.hexdigest()[:32]


_COLS = """
    finding_id, detected_at, finding_type, headline, why_specific,
    portfolio_tie, suggested_action, urgency, evidence_job_ids,
    raw_llm_response, status, nudged_at, dismissed_at, dismissed_reason,
    snoozed_until, dedup_key
""".strip()


def _row_to_finding(row) -> ResearcherFinding:
    return ResearcherFinding(
        finding_id=row[0], detected_at=row[1], finding_type=row[2],
        headline=row[3], why_specific=row[4], portfolio_tie=row[5],
        suggested_action=row[6], urgency=row[7],
        evidence_job_ids=list(row[8] or []),
        raw_llm_response=row[9], status=row[10],
        nudged_at=row[11], dismissed_at=row[12], dismissed_reason=row[13],
        snoozed_until=row[14], dedup_key=row[15],
    )


class FindingStore:
    """Storage for Researcher findings. Dedup-aware; status FSM helpers.

    The status FSM is enforced by the DB CHECK constraint plus the helper
    methods on this class — direct UPDATE bypassing these methods can
    technically reach any state, but the helpers are the contract every
    in-codebase caller uses.
    """

    def __init__(self, db: Database):
        self._db = db

    def insert(self, finding: ResearcherFinding) -> int:
        """Insert a finding. Idempotent on dedup_key collision: returns
        the existing finding_id without modifying the existing row.

        Computes dedup_key from finding_type + evidence_job_ids if the
        caller didn't precompute it.
        """
        if not finding.evidence_job_ids:
            raise ValueError(
                "ResearcherFinding requires at least one evidence_job_id; "
                "single-job 'patterns' aren't patterns."
            )
        dedup_key = finding.dedup_key or finding.compute_dedup_key()

        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO researcher_findings (
                          finding_type, headline, why_specific, portfolio_tie,
                          suggested_action, urgency, evidence_job_ids,
                          raw_llm_response, dedup_key
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING finding_id
                        """,
                        (
                            finding.finding_type, finding.headline,
                            finding.why_specific, finding.portfolio_tie,
                            finding.suggested_action, finding.urgency,
                            list(finding.evidence_job_ids),
                            Json(finding.raw_llm_response) if finding.raw_llm_response else None,
                            dedup_key,
                        ),
                    )
                    return cur.fetchone()[0]
                except psycopg.errors.UniqueViolation:
                    # Race or repeat pass: another row already owns this
                    # dedup_key. Return the existing finding_id; caller
                    # treats this as "already seen, no-op".
                    pass
        # Out of the failed transaction — open a fresh connection to read
        # the existing row.
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT finding_id FROM researcher_findings WHERE dedup_key = %s",
                    (dedup_key,),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError(
                        f"dedup_key {dedup_key!r} hit UniqueViolation but no "
                        "existing row found; this should be impossible."
                    )
                return existing[0]

    def get(self, finding_id: int) -> Optional[ResearcherFinding]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM researcher_findings WHERE finding_id = %s",
                    (finding_id,),
                )
                row = cur.fetchone()
                return None if row is None else _row_to_finding(row)

    def list_by_status(
        self,
        status: str,
        *,
        urgency: Optional[str] = None,
        days_back: Optional[int] = None,
        limit: int = 50,
    ) -> list[ResearcherFinding]:
        """List findings filtered by status. Optional urgency filter, optional
        days_back filter (only rows detected within last N days).
        """
        clauses = ["status = %s"]
        args: list = [status]
        if urgency is not None:
            clauses.append("urgency = %s")
            args.append(urgency)
        if days_back is not None:
            clauses.append("detected_at >= now() - (%s || ' days')::interval")
            args.append(int(days_back))
        where = " AND ".join(clauses)
        args.append(int(limit))
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM researcher_findings WHERE {where} "
                    f"ORDER BY detected_at DESC LIMIT %s",
                    tuple(args),
                )
                return [_row_to_finding(r) for r in cur.fetchall()]

    def mark_nudged(self, finding_id: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE researcher_findings SET status = 'nudged', "
                    "nudged_at = now() WHERE finding_id = %s",
                    (finding_id,),
                )

    def mark_dismissed(self, finding_id: int, reason: Optional[str] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE researcher_findings SET status = 'dismissed', "
                    "dismissed_at = now(), dismissed_reason = %s "
                    "WHERE finding_id = %s",
                    (reason, finding_id),
                )

    def mark_snoozed(self, finding_id: int, days: int) -> None:
        if days <= 0:
            raise ValueError("snooze days must be > 0")
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE researcher_findings SET status = 'snoozed', "
                    "snoozed_until = now() + (%s || ' days')::interval "
                    "WHERE finding_id = %s",
                    (int(days), finding_id),
                )

    def promote_due_snoozes(self) -> int:
        """Snoozed rows whose snoozed_until has passed return to status='new'.
        Returns the count promoted. The Researcher loop calls this at the
        start of each pass so re-surfaced findings can be re-evaluated."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE researcher_findings SET status = 'new', "
                    "snoozed_until = NULL WHERE status = 'snoozed' "
                    "AND snoozed_until IS NOT NULL AND snoozed_until <= now()"
                )
                return cur.rowcount or 0

    def by_dedup_key(self, dedup_key: str) -> Optional[ResearcherFinding]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM researcher_findings WHERE dedup_key = %s",
                    (dedup_key,),
                )
                row = cur.fetchone()
                return None if row is None else _row_to_finding(row)
