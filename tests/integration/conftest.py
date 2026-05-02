"""Integration test fixtures. Require Postgres at $TEST_DATABASE_URL or fall back to default Docker URL."""
from __future__ import annotations

import os
import pytest
import psycopg
from storage.connection import Database


def _test_dsn() -> str:
    return os.getenv("TEST_DATABASE_URL", "postgresql://upwork:upwork@localhost:5432/upwork_test")


@pytest.fixture(scope="session")
def ensure_test_db():
    """Create the test database if it doesn't exist. Run once per session."""
    admin_dsn = "postgresql://upwork:upwork@localhost:5432/postgres"
    test_db = "upwork_test"
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,))
                if cur.fetchone() is None:
                    cur.execute(f'CREATE DATABASE {test_db}')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available at localhost:5432 — start with `docker compose up -d`: {e}")
    yield


@pytest.fixture
def db(ensure_test_db):
    """Per-test database. Each test runs in a savepoint that's rolled back."""
    database = Database(_test_dsn(), min_size=1, max_size=2)
    yield database
    database.close()
