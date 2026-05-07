"""Integration test: 007_researcher_findings migration creates the table
with the right columns, constraints, and indexes."""
from pathlib import Path

import pytest

from storage.migrate import apply_migrations


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def test_table_exists(fresh_db):
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'researcher_findings'
            """)
            assert cur.fetchone() is not None


def test_columns_present(fresh_db):
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'researcher_findings'
            """)
            rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    expected_required = {
        "finding_id", "detected_at", "finding_type", "headline",
        "why_specific", "portfolio_tie", "suggested_action", "urgency",
        "evidence_job_ids", "status", "dedup_key",
    }
    assert expected_required.issubset(rows.keys())

    # NOT NULL on the load-bearing fields
    for col in ("finding_type", "headline", "why_specific", "portfolio_tie",
                "suggested_action", "urgency", "evidence_job_ids",
                "status", "dedup_key"):
        assert rows[col][1] == "NO", f"{col} should be NOT NULL"

    # Optional/nullable
    for col in ("nudged_at", "dismissed_at", "dismissed_reason",
                "snoozed_until", "raw_llm_response"):
        assert rows[col][1] == "YES", f"{col} should be nullable"


def test_dedup_key_is_unique(fresh_db):
    """Insert two rows with same dedup_key — second must error.
    This is the dedup-contract invariant — race conditions between
    consecutive Researcher passes can't double-insert the same finding.
    """
    import psycopg
    with fresh_db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO researcher_findings
                  (finding_type, headline, why_specific, portfolio_tie,
                   suggested_action, urgency, evidence_job_ids, dedup_key)
                VALUES
                  ('emerging_template', 'h', 'w', 'p', 's',
                   'this_week', ARRAY['j1','j2'], 'samekey')
            """)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with fresh_db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO researcher_findings
                      (finding_type, headline, why_specific, portfolio_tie,
                       suggested_action, urgency, evidence_job_ids, dedup_key)
                    VALUES
                      ('emerging_template', 'h2', 'w2', 'p2', 's2',
                       'this_week', ARRAY['j3','j4'], 'samekey')
                """)


def test_finding_type_check_constraint(fresh_db):
    """finding_type CHECK rejects values outside the spec'd enum."""
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        with fresh_db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO researcher_findings
                      (finding_type, headline, why_specific, portfolio_tie,
                       suggested_action, urgency, evidence_job_ids, dedup_key)
                    VALUES
                      ('made_up_finding_type', 'h', 'w', 'p', 's',
                       'this_week', ARRAY['j1','j2'], 'k1')
                """)


def test_status_check_constraint(fresh_db):
    """status CHECK rejects values outside the FSM."""
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        with fresh_db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO researcher_findings
                      (finding_type, headline, why_specific, portfolio_tie,
                       suggested_action, urgency, evidence_job_ids,
                       dedup_key, status)
                    VALUES
                      ('emerging_template', 'h', 'w', 'p', 's',
                       'this_week', ARRAY['j1','j2'], 'k2', 'invented_state')
                """)


def test_default_status_is_new(fresh_db):
    with fresh_db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO researcher_findings
                  (finding_type, headline, why_specific, portfolio_tie,
                   suggested_action, urgency, evidence_job_ids, dedup_key)
                VALUES
                  ('budget_anomaly', 'h', 'w', 'p', 's',
                   'this_month', ARRAY['j5','j6'], 'k3')
                RETURNING status, detected_at
            """)
            status, detected_at = cur.fetchone()
            assert status == "new"
            assert detected_at is not None


def test_indexes_exist(fresh_db):
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'researcher_findings'
            """)
            names = {r[0] for r in cur.fetchall()}
    assert "idx_findings_status_detected" in names
    assert "idx_findings_snooze_due" in names
