"""JobStore: write/read jobs + their skills."""
from __future__ import annotations

import json
from typing import Optional

from psycopg.types.json import Json

from domain.types import Job
from storage.connection import Database


class JobStore:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, job: Job, source: str, raw_panel: dict) -> None:
        """Insert or update a job (and its skills) by job_id. Idempotent."""
        with self._db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO jobs (
                      job_id, url, title, description,
                      budget_kind, budget_min_usd, budget_max_usd,
                      source, client_country, client_payment_verified,
                      client_rating, client_hires, client_total_spent_usd,
                      proposals_count_at_first_scrape, raw_panel_json
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s
                    )
                    ON CONFLICT (job_id) DO UPDATE SET
                      url = EXCLUDED.url,
                      title = EXCLUDED.title,
                      description = COALESCE(EXCLUDED.description, jobs.description),
                      budget_kind = COALESCE(EXCLUDED.budget_kind, jobs.budget_kind),
                      budget_min_usd = COALESCE(EXCLUDED.budget_min_usd, jobs.budget_min_usd),
                      budget_max_usd = COALESCE(EXCLUDED.budget_max_usd, jobs.budget_max_usd),
                      raw_panel_json = EXCLUDED.raw_panel_json
                """, (
                    job.job_id, job.url, job.title, job.description,
                    job.budget_kind, job.budget_min_usd, job.budget_max_usd,
                    source, job.client_country, job.client_payment_verified,
                    job.client_rating, job.client_hires, job.client_total_spent_usd,
                    job.proposals_count_at_first_scrape, Json(raw_panel),
                ))
                # Upsert skills
                for skill_name in job.skills or []:
                    cur.execute("""
                        INSERT INTO skills (name) VALUES (%s)
                        ON CONFLICT (name) DO NOTHING
                    """, (skill_name,))
                    cur.execute("""
                        INSERT INTO job_skills (job_id, skill_id, source)
                        SELECT %s, skill_id, 'upwork_chip' FROM skills WHERE name = %s
                        ON CONFLICT DO NOTHING
                    """, (job.job_id, skill_name))

    def get(self, job_id: str) -> Optional[Job]:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_id, url, title, description,
                           budget_kind, budget_min_usd, budget_max_usd,
                           client_country, client_payment_verified,
                           client_rating, client_hires, client_total_spent_usd,
                           posted_at, proposals_count_at_first_scrape
                    FROM jobs WHERE job_id = %s
                """, (job_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute("""
                    SELECT s.name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
                    WHERE js.job_id = %s
                """, (job_id,))
                skills = [r[0] for r in cur.fetchall()]
        return Job(
            job_id=row[0], url=row[1], title=row[2], description=row[3],
            budget_kind=row[4], budget_min_usd=float(row[5]) if row[5] is not None else None,
            budget_max_usd=float(row[6]) if row[6] is not None else None,
            client_country=row[7], client_payment_verified=row[8],
            client_rating=float(row[9]) if row[9] is not None else None,
            client_hires=row[10],
            client_total_spent_usd=float(row[11]) if row[11] is not None else None,
            posted_at=row[12], proposals_count_at_first_scrape=row[13],
            skills=skills,
        )

    def is_known(self, job_id: str) -> bool:
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM jobs WHERE job_id = %s", (job_id,))
                return cur.fetchone() is not None
