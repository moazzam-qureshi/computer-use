"""End-to-end loop test: simulate a 3-turn conversation with the agent
against a real Postgres test DB. Skipped if OPENAI_API_KEY is unset."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.setups import SetupStore
from domain.types import Setup, FilterDsl


pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="needs real OpenAI key for live agent invocation",
)


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture
def setup_id(db):
    s = SetupStore(db)
    sid = s.create(Setup(
        setup_id=0, name="loop-test-setup", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition="loop integration test",
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    yield sid
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM setups WHERE setup_id = %s", (sid,))


def test_three_turn_conversation(db, setup_id):
    from assistant.agent import run_turn
    user = "test-loop-user-2026-05-04"

    # turn 1: read
    reply1 = run_turn(db, discord_user_id=user, user_message="list active setups please")
    assert isinstance(reply1, str) and len(reply1) > 0

    # turn 2: write
    reply2 = run_turn(
        db, discord_user_id=user,
        user_message=f"add 'TestClientCorp' to ignored clients on setup {setup_id}",
    )
    assert isinstance(reply2, str)

    # confirm DB state (the truth, not the reply text)
    s = SetupStore(db).get(setup_id)
    assert "TestClientCorp" in s.ignored_clients

    # turn 3: revert
    reply3 = run_turn(db, discord_user_id=user, user_message="revert that")
    assert isinstance(reply3, str)

    s2 = SetupStore(db).get(setup_id)
    assert "TestClientCorp" not in s2.ignored_clients
