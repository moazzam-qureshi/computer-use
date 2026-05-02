from __future__ import annotations
from typing import Optional
from psycopg.types.json import Json
from storage.connection import Database


class AgentRunStore:
    def __init__(self, db: Database):
        self._db = db

    def start(self, *, agent_name: str, trigger: str, trigger_context: dict, parent_run_id: Optional[int] = None) -> int:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_runs (agent_name, trigger, trigger_context, parent_run_id)
                    VALUES (%s, %s, %s, %s) RETURNING run_id
                """, (agent_name, trigger, Json(trigger_context), parent_run_id))
                return cur.fetchone()[0]

    def finish(self, *, run_id: int, status: str, total_tokens: Optional[int],
               total_cost_usd: Optional[float], output_summary: Optional[str]) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE agent_runs SET finished_at = now(), status = %s,
                                          total_tokens = %s, total_cost_usd = %s,
                                          output_summary = %s
                    WHERE run_id = %s
                """, (status, total_tokens, total_cost_usd, output_summary, run_id))
