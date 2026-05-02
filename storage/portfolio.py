"""PortfolioStore — list-of-dict surface for Phase 1 simplicity."""
from __future__ import annotations

from typing import List, Optional
from storage.connection import Database


class PortfolioStore:
    def __init__(self, db: Database):
        self._db = db

    def add(self, *, name: str, summary: Optional[str], client_context: Optional[str],
            outcome: Optional[str], tech: list[str], relevance_tags: list[str],
            year_completed: Optional[int]) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO portfolio_items (name, summary, client_context, outcome, tech, relevance_tags, year_completed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING portfolio_id
                """, (name, summary, client_context, outcome, tech, relevance_tags, year_completed))
                return cur.fetchone()[0]

    def list_all(self) -> List[dict]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT portfolio_id, name, summary, client_context, outcome, tech, relevance_tags, year_completed
                    FROM portfolio_items ORDER BY portfolio_id
                """)
                rows = cur.fetchall()
        return [dict(portfolio_id=r[0], name=r[1], summary=r[2], client_context=r[3],
                     outcome=r[4], tech=r[5], relevance_tags=r[6], year_completed=r[7])
                for r in rows]

    def list_matching_tags(self, tags: list[str]) -> List[dict]:
        if not tags:
            return self.list_all()
        lowered = [t.lower() for t in tags]
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT portfolio_id, name, summary, client_context, outcome, tech, relevance_tags, year_completed
                    FROM portfolio_items
                    WHERE EXISTS (
                        SELECT 1 FROM unnest(relevance_tags) tag
                        WHERE LOWER(tag) = ANY(%s)
                    )
                    ORDER BY portfolio_id
                """, (lowered,))
                rows = cur.fetchall()
        return [dict(portfolio_id=r[0], name=r[1], summary=r[2], client_context=r[3],
                     outcome=r[4], tech=r[5], relevance_tags=r[6], year_completed=r[7])
                for r in rows]
