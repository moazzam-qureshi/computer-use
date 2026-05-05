"""GoalStore: single active operator goal, history kept in the same table."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from storage.connection import Database


@dataclass
class Goal:
    goal_id: Optional[int]
    prose: str
    target_metric: Optional[str]
    target_value: Optional[float]
    horizon: Optional[str]
    min_hourly: Optional[float]
    min_budget: Optional[float]
    preferred_country: Optional[str]
    notes: Optional[str]
    is_active: bool = True
    created_at: Optional[datetime] = None
    deactivated_at: Optional[datetime] = None


class GoalStore:
    def __init__(self, db: Database):
        self._db = db

    def get_active(self) -> Optional[Goal]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT goal_id, prose, target_metric, target_value, horizon,
                           min_hourly, min_budget, preferred_country, notes,
                           is_active, created_at, deactivated_at
                    FROM goals WHERE is_active = true
                """)
                row = cur.fetchone()
                if row is None:
                    return None
                return Goal(
                    goal_id=row[0], prose=row[1], target_metric=row[2],
                    target_value=row[3], horizon=row[4], min_hourly=row[5],
                    min_budget=row[6], preferred_country=row[7], notes=row[8],
                    is_active=row[9], created_at=row[10], deactivated_at=row[11],
                )

    def create(self, goal: Goal) -> int:
        """Deactivate any existing active goal, insert the new one, return goal_id."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE goals
                    SET is_active = false, deactivated_at = now()
                    WHERE is_active = true
                """)
                cur.execute("""
                    INSERT INTO goals (prose, target_metric, target_value, horizon,
                                       min_hourly, min_budget, preferred_country, notes,
                                       is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                    RETURNING goal_id
                """, (
                    goal.prose, goal.target_metric, goal.target_value, goal.horizon,
                    goal.min_hourly, goal.min_budget, goal.preferred_country, goal.notes,
                ))
                return cur.fetchone()[0]

    def clear_active(self) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE goals SET is_active = false, deactivated_at = now()
                    WHERE is_active = true
                """)

    def get_by_id(self, goal_id: int) -> Optional[Goal]:
        """Look up a goal regardless of is_active. Used by Phase 2.B
        processing to honor the goal that was active at detection time."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT goal_id, prose, target_metric, target_value, horizon,
                           min_hourly, min_budget, preferred_country, notes,
                           is_active, created_at, deactivated_at
                    FROM goals WHERE goal_id = %s
                """, (goal_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return Goal(
                    goal_id=row[0], prose=row[1], target_metric=row[2],
                    target_value=row[3], horizon=row[4], min_hourly=row[5],
                    min_budget=row[6], preferred_country=row[7], notes=row[8],
                    is_active=row[9], created_at=row[10], deactivated_at=row[11],
                )
