from __future__ import annotations
from typing import Optional
from psycopg.types.json import Json
from storage.connection import Database
from ai.schemas import Enrichment


class EnrichmentStore:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, job_id: str, enrichment: Enrichment, *, prompt_version: str = "2026-05-02-v1",
               raw_llm_response: Optional[dict] = None) -> None:
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO job_enrichments (
                      job_id, prompt_version, extracted_tech, pain_points, red_flags, green_flags,
                      project_shape, buyer_sophistication, raw_llm_response
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                      prompt_version = EXCLUDED.prompt_version,
                      extracted_tech = EXCLUDED.extracted_tech,
                      pain_points = EXCLUDED.pain_points,
                      red_flags = EXCLUDED.red_flags,
                      green_flags = EXCLUDED.green_flags,
                      project_shape = EXCLUDED.project_shape,
                      buyer_sophistication = EXCLUDED.buyer_sophistication,
                      enriched_at = now()
                """, (
                    job_id, prompt_version,
                    enrichment.extracted_tech, enrichment.pain_points,
                    enrichment.red_flags, enrichment.green_flags,
                    enrichment.project_shape, enrichment.buyer_sophistication,
                    Json(raw_llm_response) if raw_llm_response else None,
                ))

    def get(self, job_id: str) -> Optional[Enrichment]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT extracted_tech, pain_points, red_flags, green_flags,
                           project_shape, buyer_sophistication
                    FROM job_enrichments WHERE job_id = %s
                """, (job_id,))
                r = cur.fetchone()
                if r is None:
                    return None
        return Enrichment(
            extracted_tech=r[0] or [], pain_points=r[1] or [],
            red_flags=r[2] or [], green_flags=r[3] or [],
            project_shape=r[4], buyer_sophistication=r[5],
        )
