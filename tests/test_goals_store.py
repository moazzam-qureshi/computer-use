"""Integration tests for GoalStore. Real Postgres."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.goals import GoalStore, Goal


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean(db):
    """Each test starts with no active goal (history rows are fine)."""
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE goals SET is_active=false, deactivated_at=now() WHERE is_active=true")
    yield


def test_get_active_returns_none_when_empty(db):
    store = GoalStore(db)
    assert store.get_active() is None


def test_create_then_get_active(db):
    store = GoalStore(db)
    gid = store.create(Goal(
        goal_id=None, prose="Land 5 interviews per week from US clients $80+/hr",
        target_metric="interviews_per_week", target_value=5, horizon="weekly",
        min_hourly=80, min_budget=None, preferred_country="US",
        notes="Focus on AI agent and RAG work",
    ))
    g = store.get_active()
    assert g is not None
    assert g.goal_id == gid
    assert g.prose.startswith("Land 5 interviews")
    assert float(g.target_value) == 5.0
    assert g.preferred_country == "US"


def test_create_deactivates_previous_active(db):
    store = GoalStore(db)
    gid1 = store.create(Goal(goal_id=None, prose="goal A", target_metric=None,
                              target_value=None, horizon=None, min_hourly=None,
                              min_budget=None, preferred_country=None, notes=None))
    gid2 = store.create(Goal(goal_id=None, prose="goal B", target_metric=None,
                              target_value=None, horizon=None, min_hourly=None,
                              min_budget=None, preferred_country=None, notes=None))
    g = store.get_active()
    assert g is not None and g.goal_id == gid2
    # Old one is deactivated, not deleted
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_active, deactivated_at FROM goals WHERE goal_id=%s", (gid1,))
            row = cur.fetchone()
    assert row[0] is False
    assert row[1] is not None


def test_clear_active(db):
    store = GoalStore(db)
    store.create(Goal(goal_id=None, prose="goal X", target_metric=None,
                       target_value=None, horizon=None, min_hourly=None,
                       min_budget=None, preferred_country=None, notes=None))
    assert store.get_active() is not None
    store.clear_active()
    assert store.get_active() is None


def test_partial_unique_index_blocks_two_active(db):
    """Defense-in-depth: even if create() were buggy, the DB enforces single-active."""
    import psycopg
    store = GoalStore(db)
    store.create(Goal(goal_id=None, prose="goal", target_metric=None,
                       target_value=None, horizon=None, min_hourly=None,
                       min_budget=None, preferred_country=None, notes=None))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO goals (prose, is_active) VALUES (%s, true)",
                    ("second active",),
                )
