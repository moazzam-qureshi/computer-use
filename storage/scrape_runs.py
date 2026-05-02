from __future__ import annotations
from typing import Optional
from storage.connection import Database


class ScrapeRunStore:
    def __init__(self, db: Database):
        self._db = db

    def start(self, *, source: str, query_id: Optional[int] = None) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scrape_runs (source, query_id) VALUES (%s, %s) RETURNING run_id
                """, (source, query_id))
                return cur.fetchone()[0]

    def update_counts(self, run_id: int, *, jobs_seen: int, jobs_new: int, jobs_signaled: int) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scrape_runs SET jobs_seen = %s, jobs_new = %s, jobs_signaled = %s
                    WHERE run_id = %s
                """, (jobs_seen, jobs_new, jobs_signaled, run_id))

    def finish(self, run_id: int, *, notes: Optional[str] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE scrape_runs SET finished_at = now(), notes = %s WHERE run_id = %s
                """, (notes, run_id))
