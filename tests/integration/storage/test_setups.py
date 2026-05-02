import pytest
from pathlib import Path
from storage.migrate import apply_migrations
from storage.setups import SetupStore
from domain.types import Setup, FilterDsl

MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _setup(name: str, **overrides) -> Setup:
    base = dict(
        setup_id=None, name=name, status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": [{"client_payment_verified": True}]}),
        prose_definition="test", pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={"initial": 0, "repings": [], "deadline": None, "on_deadline": "queue"},
    )
    base.update(overrides)
    return Setup(**base)


def test_create_and_list_active(fresh_db):
    store = SetupStore(fresh_db)
    sid = store.create(_setup("test-1"))
    assert sid > 0
    store.create(_setup("test-2", status="disabled"))
    active = store.list_active()
    assert len(active) == 1
    assert active[0].name == "test-1"


def test_get_round_trips_filter_dsl(fresh_db):
    store = SetupStore(fresh_db)
    sid = store.create(_setup("test-1", filter_dsl=FilterDsl({"any_of": [{"skill_in": ["rag"]}]})))
    fetched = store.get(sid)
    assert fetched.filter_dsl.spec == {"any_of": [{"skill_in": ["rag"]}]}
