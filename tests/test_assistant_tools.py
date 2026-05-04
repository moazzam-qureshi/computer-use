"""Tests for assistant tools. Uses a real Postgres test DB."""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from storage.connection import Database
from storage.setups import SetupStore
from storage.conversations import ConversationStore
from domain.types import Setup, FilterDsl
from assistant.tools import build_tools, ToolContext


@pytest.fixture(scope="module")
def db():
    return Database(os.environ["DATABASE_URL"])


@pytest.fixture
def ctx(db):
    conv = ConversationStore(db)
    cid = conv.get_or_create("test-tools-user")
    return ToolContext(db=db, conversation_id=cid)


@pytest.fixture
def setup_id(db):
    """Create a throwaway setup for the test session and clean up after."""
    s = SetupStore(db)
    sid = s.create(Setup(
        setup_id=0, name="test-setup-tools", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}), prose_definition=None,
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    ))
    yield sid
    with db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM setups WHERE setup_id = %s", (sid,))


def test_list_setups_returns_dicts(ctx, setup_id):
    tools = build_tools(ctx)
    list_setups = next(t for t in tools if t.name == "list_setups")
    result = list_setups.invoke({})
    assert isinstance(result, list)
    assert any(r["setup_id"] == setup_id for r in result)


def test_get_setup_round_trip(ctx, setup_id):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    result = get_setup.invoke({"setup_id": setup_id})
    assert result["setup_id"] == setup_id
    assert result["name"] == "test-setup-tools"
    assert result["ignored_clients"] == []


def test_get_setup_not_found_returns_error(ctx):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    result = get_setup.invoke({"setup_id": 99999999})
    assert "error" in result


def test_recent_activity_runs(ctx):
    tools = build_tools(ctx)
    ra = next(t for t in tools if t.name == "recent_activity")
    result = ra.invoke({"hours": 24})
    assert "cycles_run" in result
    assert "total_llm_cost_usd" in result


def test_connects_status_runs(ctx):
    tools = build_tools(ctx)
    cs = next(t for t in tools if t.name == "connects_status")
    result = cs.invoke({})
    assert "daily_cap" in result
    assert "bidder_paused" in result


def test_list_portfolio_items_runs(ctx):
    tools = build_tools(ctx)
    lp = next(t for t in tools if t.name == "list_portfolio_items")
    result = lp.invoke({})
    assert isinstance(result, list)


def test_update_setup_filters_merges(ctx, setup_id):
    tools = build_tools(ctx)
    update = next(t for t in tools if t.name == "update_setup_filters")
    result = update.invoke({
        "setup_id": setup_id,
        "patch": {"min_budget": 500, "required_skills": ["python", "rag"]},
    })
    assert "error" not in result
    get_setup = next(t for t in tools if t.name == "get_setup")
    s = get_setup.invoke({"setup_id": setup_id})
    text = str(s["filter_dsl"])
    assert "500" in text
    assert "python" in text


def test_add_and_remove_ignored_client(ctx, setup_id):
    tools = build_tools(ctx)
    add = next(t for t in tools if t.name == "add_ignored_client")
    rem = next(t for t in tools if t.name == "remove_ignored_client")
    add.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})
    add.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})  # idempotent
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["ignored_clients"].count("Acme Corp") == 1
    rem.invoke({"setup_id": setup_id, "client_name": "Acme Corp"})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert "Acme Corp" not in s["ignored_clients"]


def test_status_transitions(ctx, setup_id):
    tools = build_tools(ctx)
    pause = next(t for t in tools if t.name == "pause_setup")
    resume = next(t for t in tools if t.name == "resume_setup")
    pause.invoke({"setup_id": setup_id})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["status"] == "disabled"
    resume.invoke({"setup_id": setup_id})
    s = next(t for t in tools if t.name == "get_setup").invoke({"setup_id": setup_id})
    assert s["status"] == "active"


def test_bidder_pause_resume_writes_system_config(ctx):
    tools = build_tools(ctx)
    pause = next(t for t in tools if t.name == "pause_bidder")
    resume = next(t for t in tools if t.name == "resume_bidder")
    pause.invoke({})
    cs = next(t for t in tools if t.name == "connects_status").invoke({})
    assert cs["bidder_paused"] is True
    resume.invoke({})
    cs = next(t for t in tools if t.name == "connects_status").invoke({})
    assert cs["bidder_paused"] is False


def test_revert_last_change_undoes_pause(ctx, setup_id):
    tools = build_tools(ctx)
    get_setup = next(t for t in tools if t.name == "get_setup")
    pause = next(t for t in tools if t.name == "pause_setup")
    revert = next(t for t in tools if t.name == "revert_last_change")
    next(t for t in tools if t.name == "resume_setup").invoke({"setup_id": setup_id})
    pause.invoke({"setup_id": setup_id})
    assert get_setup.invoke({"setup_id": setup_id})["status"] == "disabled"
    rv = revert.invoke({})
    assert "error" not in rv
    assert get_setup.invoke({"setup_id": setup_id})["status"] == "active"


def test_validation_error_is_returned_not_raised(ctx, setup_id):
    tools = build_tools(ctx)
    set_tier = next(t for t in tools if t.name == "set_setup_tier")
    result = set_tier.invoke({"setup_id": setup_id, "tier": "bogus"})
    assert "error" in result


def test_set_goal_then_get_goal(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")

    # Start clean
    clear_goal.invoke({})
    assert "error" in get_goal.invoke({})

    result = set_goal.invoke({
        "prose": "Land 5 interviews per week from US clients $80+/hr",
        "target_metric": "interviews_per_week",
        "target_value": 5,
        "horizon": "weekly",
        "min_hourly": 80,
        "preferred_country": "US",
    })
    assert "error" not in result
    g = get_goal.invoke({})
    assert g["prose"].startswith("Land 5 interviews")
    assert g["target_value"] == 5
    assert g["preferred_country"] == "US"


def test_set_goal_replaces_previous(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    set_goal.invoke({"prose": "first goal"})
    set_goal.invoke({"prose": "second goal"})
    g = get_goal.invoke({})
    assert g["prose"] == "second goal"


def test_clear_goal_makes_get_return_error(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")
    set_goal.invoke({"prose": "to be cleared"})
    clear_goal.invoke({})
    assert "error" in get_goal.invoke({})


def test_revert_undoes_set_goal(ctx):
    tools = build_tools(ctx)
    set_goal = next(t for t in tools if t.name == "set_goal")
    get_goal = next(t for t in tools if t.name == "get_goal")
    clear_goal = next(t for t in tools if t.name == "clear_goal")
    revert = next(t for t in tools if t.name == "revert_last_change")
    clear_goal.invoke({})
    set_goal.invoke({"prose": "goal A"})
    set_goal.invoke({"prose": "goal B"})
    revert.invoke({})  # should restore goal A as active
    g = get_goal.invoke({})
    assert g["prose"] == "goal A"
