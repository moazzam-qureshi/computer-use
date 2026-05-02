import pytest
from pathlib import Path
from storage.migrate import apply_migrations, applied_versions

MIGRATIONS_DIR = Path(__file__).parents[3] / "storage" / "migrations"


def test_first_apply_creates_schema_and_records_versions(db):
    # Wipe before, in case prior run left state
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            conn.commit()

    apply_migrations(db, MIGRATIONS_DIR)

    versions = applied_versions(db)
    assert 1 in versions, "migration 001 should be recorded"

    # Spot-check a couple of tables exist
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.jobs');")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT to_regclass('public.setups');")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT to_regclass('public.orders');")
            assert cur.fetchone()[0] is not None


def test_idempotent_reapply_does_nothing(db):
    apply_migrations(db, MIGRATIONS_DIR)
    apply_migrations(db, MIGRATIONS_DIR)  # second call should be a no-op
    versions = applied_versions(db)
    assert versions.count(1) == 1
