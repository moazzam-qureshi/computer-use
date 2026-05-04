"""SetupStore + SignalStore."""
from __future__ import annotations

from typing import Optional, List
from psycopg.types.json import Json

from domain.types import Setup, Signal, FilterDsl
from storage.connection import Database


class SetupStore:
    def __init__(self, db: Database):
        self._db = db

    def create(self, setup: Setup) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO setups (
                      name, status, tier, filter_dsl, prose_definition,
                      pitch_template_id, cover_letter_template_id,
                      auto_apply_enabled, escalation_config,
                      ignored_clients, tone_override, activated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              CASE WHEN %s = 'active' THEN now() ELSE NULL END)
                    RETURNING setup_id
                """, (
                    setup.name, setup.status, setup.tier,
                    Json(setup.filter_dsl.spec), setup.prose_definition,
                    setup.pitch_template_id, setup.cover_letter_template_id,
                    setup.auto_apply_enabled, Json(setup.escalation_config),
                    setup.ignored_clients, setup.tone_override,
                    setup.status,
                ))
                return cur.fetchone()[0]

    def get(self, setup_id: int) -> Optional[Setup]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config,
                           ignored_clients, tone_override
                    FROM setups WHERE setup_id = %s
                """, (setup_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return Setup(
                    setup_id=row[0], name=row[1], status=row[2], tier=row[3],
                    filter_dsl=FilterDsl(row[4]), prose_definition=row[5],
                    pitch_template_id=row[6], cover_letter_template_id=row[7],
                    auto_apply_enabled=row[8], escalation_config=row[9],
                    ignored_clients=list(row[10] or []), tone_override=row[11],
                )

    def list_active(self) -> List[Setup]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT setup_id, name, status, tier, filter_dsl, prose_definition,
                           pitch_template_id, cover_letter_template_id,
                           auto_apply_enabled, escalation_config,
                           ignored_clients, tone_override
                    FROM setups WHERE status = 'active'
                    ORDER BY setup_id
                """)
                rows = cur.fetchall()
        return [
            Setup(setup_id=r[0], name=r[1], status=r[2], tier=r[3],
                  filter_dsl=FilterDsl(r[4]), prose_definition=r[5],
                  pitch_template_id=r[6], cover_letter_template_id=r[7],
                  auto_apply_enabled=r[8], escalation_config=r[9],
                  ignored_clients=list(r[10] or []), tone_override=r[11])
            for r in rows
        ]

    def update_status(self, setup_id: int, new_status: str) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE setups SET status = %s WHERE setup_id = %s", (new_status, setup_id))


class SignalStore:
    def __init__(self, db: Database):
        self._db = db

    def create(self, signal: Signal) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO signals (job_id, primary_setup_id, matched_setups, market_state)
                    VALUES (%s, %s, %s, %s)
                    RETURNING signal_id
                """, (signal.job_id, signal.primary_setup_id,
                      Json(signal.matched_setups), Json(signal.market_state)))
                return cur.fetchone()[0]
