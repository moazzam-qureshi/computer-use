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


def test_trigger_bidder_scan_returns_status_payload(ctx):
    """Phase 2.B: detection runs every 60s automatically, so trigger_bidder_scan
    is a friendly no-op that returns the most recent cycle's status. It must
    NOT set force_run_requested anymore (Phase 2.B doesn't consume that flag)."""
    from storage.bidder_state import BidderStateStore
    bs = BidderStateStore(ctx.db)
    bs.consume_force_run()  # ensure we start with the flag cleared
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_bidder_scan")
    result = trigger.invoke({})
    assert "error" not in result
    assert "info" in result
    assert "60s" in result["info"]
    # Critical: must NOT set force_run_requested. The Phase 2.B detection
    # loop ignores this flag entirely; setting it would silently leak.
    state = bs.get()
    assert state.force_run_requested is False


def test_trigger_briefed_scan_creates_pending_brief(ctx):
    from storage.scan_briefs import BriefStore
    bs = BriefStore(ctx.db)
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_briefed_scan")
    result = trigger.invoke({
        "prose": "TEST:python AI agent jobs $80+/hr",
        "filter_patch": {"min_hourly": 80, "required_skills": ["python", "rag"]},
    })
    assert "error" not in result
    assert "brief_id" in result
    assert result["status"] == "pending"
    brief = bs.get(result["brief_id"])
    assert brief is not None
    assert brief.status == "pending"
    # Cleanup so the pending brief doesn't trigger the bidder once we restart it.
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scan_briefs WHERE brief_id=%s", (result["brief_id"],))


def test_update_setup_filters_accepts_max_post_age_minutes(ctx, setup_id):
    tools = build_tools(ctx)
    update = next(t for t in tools if t.name == "update_setup_filters")
    result = update.invoke({
        "setup_id": setup_id,
        "patch": {"max_post_age_minutes": 30},
    })
    assert "error" not in result
    rules = result["filter_dsl"]["all_of"]
    assert {"posted_within_minutes": 30} in rules


def test_set_setup_max_post_age_minutes_flat_call(ctx, setup_id):
    """The flat single-purpose tool the agent should use. No nested patch."""
    tools = build_tools(ctx)
    setter = next(t for t in tools if t.name == "set_setup_max_post_age_minutes")
    result = setter.invoke({"setup_id": setup_id, "minutes": 30})
    assert "error" not in result
    rules = result["filter_dsl"]["all_of"]
    assert {"posted_within_minutes": 30} in rules


def test_set_setup_min_hourly_flat_call(ctx, setup_id):
    tools = build_tools(ctx)
    setter = next(t for t in tools if t.name == "set_setup_min_hourly")
    result = setter.invoke({"setup_id": setup_id, "amount": 75})
    assert "error" not in result
    rules = result["filter_dsl"]["all_of"]
    assert {"min_hourly": 75.0} in rules


def test_set_setup_required_skills_flat_call(ctx, setup_id):
    tools = build_tools(ctx)
    setter = next(t for t in tools if t.name == "set_setup_required_skills")
    result = setter.invoke({"setup_id": setup_id, "skills": ["python", "rag"]})
    assert "error" not in result
    rules = result["filter_dsl"]["all_of"]
    assert {"skill_in": ["python", "rag"]} in rules


def test_clear_setup_filter_removes_rule(ctx, setup_id):
    tools = build_tools(ctx)
    setter = next(t for t in tools if t.name == "set_setup_max_post_age_minutes")
    setter.invoke({"setup_id": setup_id, "minutes": 30})
    clear = next(t for t in tools if t.name == "clear_setup_filter")
    result = clear.invoke({"setup_id": setup_id, "rule_key": "posted_within_minutes"})
    assert "error" not in result
    assert result["removed"] is True
    rules = result["filter_dsl"].get("all_of", [])
    assert all("posted_within_minutes" not in r for r in rules)


def test_clear_setup_filter_no_op_when_absent(ctx, setup_id):
    tools = build_tools(ctx)
    clear = next(t for t in tools if t.name == "clear_setup_filter")
    result = clear.invoke({"setup_id": setup_id, "rule_key": "skill_in"})
    assert "error" not in result
    assert result["removed"] is False


def test_trigger_briefed_scan_accepts_omitted_filter_patch(ctx):
    from storage.scan_briefs import BriefStore
    bs = BriefStore(ctx.db)
    tools = build_tools(ctx)
    trigger = next(t for t in tools if t.name == "trigger_briefed_scan")
    result = trigger.invoke({"prose": "TEST:LLM-only brief, no hard filters"})
    assert "error" not in result
    assert "brief_id" in result
    brief = bs.get(result["brief_id"])
    assert brief is not None
    assert brief.filter_dsl == {"all_of": []}
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM scan_briefs WHERE brief_id=%s", (result["brief_id"],))


# ---- BA tools (Task 3) ----

def test_search_market_registered_and_validates(ctx):
    """The tool exists, validates filter args, surfaces errors as dicts."""
    tools = build_tools(ctx)
    sm = next(t for t in tools if t.name == "search_market")
    # Empty query → validation error
    result = sm.invoke({"query": ""})
    assert "error" in result
    assert "query is required" in result["error"]


def test_search_market_writes_corpus(ctx, monkeypatch):
    """End-to-end: tool invocation writes corpus rows via search_and_ingest.
    The substrate-level search() is patched to avoid real Chrome use; the
    storage path runs against the live test DB.
    """
    from datetime import datetime, timezone
    from upwork.search_driver import CardResult
    import upwork.search_driver as search_driver_mod

    fake_cards = [
        CardResult(
            title=f"TEST-BA: hourly RAG eval pipeline {datetime.now().timestamp()}",
            snippet="ba tool integration test",
            budget_kind="hourly",
            budget_min_usd=60.0, budget_max_usd=90.0,
            budget_text="Hourly: $60.00 - $90.00",
            posted_text="11 minutes ago",
            posted_at=datetime.now(timezone.utc),
            skills=["RAG", "Python"],
            client_country="United States",
            payment_verified=True,
        ),
        CardResult(
            title=f"TEST-BA: fixed voice agent {datetime.now().timestamp()}",
            budget_kind="fixed",
            budget_min_usd=5000.0, budget_max_usd=5000.0,
            budget_text="Fixed-price",
            posted_text="3 hours ago",
            posted_at=datetime.now(timezone.utc),
            skills=["Voice AI"],
        ),
    ]

    def fake_search(query, filters, max_cards=30, window_title="Upwork"):
        return list(fake_cards)

    monkeypatch.setattr(search_driver_mod, "search", fake_search)

    tools = build_tools(ctx)
    sm = next(t for t in tools if t.name == "search_market")
    result = sm.invoke({
        "query": "RAG engineer",
        "payment_verified": "1",
        "hourly_rate": "60-",
    })

    assert "error" not in result, result
    assert result["scanned"] == 2
    assert result["inserted"] == 2
    assert result["source"].startswith("ba:RAG engineer|")
    assert "hourly_rate=60-" in result["source"]
    assert "payment_verified=1" in result["source"]
    assert len(result["sample"]) == 2

    # Re-run same query: idempotent — no new rows.
    result2 = sm.invoke({
        "query": "RAG engineer",
        "payment_verified": "1",
        "hourly_rate": "60-",
    })
    assert result2["inserted"] == 0
    assert result2["updated_existing"] == 2

    # Cleanup the test rows so we don't pollute the live DB.
    titles = [c.title for c in fake_cards]
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE title = ANY(%s)", (titles,))


def test_search_market_filter_assembly():
    """_build_filters drops Nones and produces clean dicts."""
    from assistant.ba_tools import _build_filters
    out = _build_filters(
        payment_verified="1", t="0", hourly_rate=None,
        amount=None, proposals=None, duration_v3="ongoing",
    )
    assert out == {"payment_verified": "1", "t": "0", "duration_v3": "ongoing"}

    empty = _build_filters(
        payment_verified=None, t=None, hourly_rate=None,
        amount=None, proposals=None, duration_v3=None,
    )
    assert empty == {}


def test_analyze_corpus_tool_returns_dict(ctx):
    """The tool wraps ai.market_analysis.analyze_corpus and returns its dict."""
    tools = build_tools(ctx)
    ac = next(t for t in tools if t.name == "analyze_corpus")
    result = ac.invoke({"window_days": 30})
    assert "error" not in result
    assert "total_jobs" in result
    assert "top_skills" in result
    assert "budget" in result
    assert "weekly_volume" in result
    assert "payment_verified_share" in result


def test_backtest_setup_tool_requires_at_least_one_filter(ctx):
    tools = build_tools(ctx)
    bs = next(t for t in tools if t.name == "backtest_setup")
    result = bs.invoke({})  # no filter args
    assert "error" in result
    assert "no filter args" in result["error"]


def test_backtest_setup_tool_returns_count(ctx):
    """End-to-end: tool accepts flat filter args, returns match_count."""
    tools = build_tools(ctx)
    bs = next(t for t in tools if t.name == "backtest_setup")
    result = bs.invoke({"min_hourly": 50, "window_days": 30})
    assert "error" not in result
    assert "match_count" in result
    assert "total_in_window" in result
    assert "sample" in result
    assert "filter_dsl" in result
    assert {"min_hourly": 50.0} in result["filter_dsl"]["all_of"]


# ---- Researcher operator surface (R-Task 8) ----

@pytest.fixture
def seed_finding(ctx):
    """Insert a throwaway finding for tests; clean up after."""
    from storage.findings import FindingStore, ResearcherFinding
    store = FindingStore(ctx.db)
    fid = store.insert(ResearcherFinding(
        finding_id=None, detected_at=None,
        finding_type="emerging_template",
        headline="TEST-OPSURFACE: 5 jobs want Ragas + LangSmith",
        why_specific="Three of five mention Ragas; quoted phrases show pattern.",
        portfolio_tie="Operator portfolio has LangSmith but not Ragas — gap.",
        suggested_action="Build Ragas demo, push to GitHub, add case study.",
        urgency="this_week",
        evidence_job_ids=[f"~test-opsurface-a-{os.getpid()}",
                          f"~test-opsurface-b-{os.getpid()}"],
    ))
    yield fid
    with ctx.db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM researcher_findings WHERE finding_id = %s", (fid,))


def test_list_findings_returns_compact(ctx, seed_finding):
    tools = build_tools(ctx)
    lf = next(t for t in tools if t.name == "list_findings")
    result = lf.invoke({"status": "new", "days_back": 7})
    assert "error" not in result
    assert "findings" in result
    ids = [f["finding_id"] for f in result["findings"]]
    assert seed_finding in ids
    sample = next(f for f in result["findings"] if f["finding_id"] == seed_finding)
    # Compact shape — no full WHY prose
    assert "why_specific" not in sample
    assert "evidence_job_count" in sample


def test_list_findings_validates_status(ctx):
    tools = build_tools(ctx)
    lf = next(t for t in tools if t.name == "list_findings")
    result = lf.invoke({"status": "invented_state"})
    assert "error" in result


def test_get_finding_returns_full_prose(ctx, seed_finding):
    tools = build_tools(ctx)
    gf = next(t for t in tools if t.name == "get_finding")
    result = gf.invoke({"finding_id": seed_finding})
    assert "error" not in result
    assert "why_specific" in result
    assert "portfolio_tie" in result
    assert "suggested_action" in result
    assert "evidence_job_ids" in result
    assert len(result["evidence_job_ids"]) == 2


def test_get_finding_not_found(ctx):
    tools = build_tools(ctx)
    gf = next(t for t in tools if t.name == "get_finding")
    result = gf.invoke({"finding_id": 999_999_999})
    assert "error" in result


def test_dismiss_finding_round_trip(ctx, seed_finding):
    from storage.findings import FindingStore
    store = FindingStore(ctx.db)
    tools = build_tools(ctx)
    df = next(t for t in tools if t.name == "dismiss_finding")
    result = df.invoke({"finding_id": seed_finding, "reason": "not relevant"})
    assert "error" not in result
    assert result["status"] == "dismissed"
    f = store.get(seed_finding)
    assert f.status == "dismissed"
    assert f.dismissed_reason == "not relevant"


def test_dismiss_finding_revertible(ctx, seed_finding):
    from storage.findings import FindingStore
    store = FindingStore(ctx.db)
    tools = build_tools(ctx)
    df = next(t for t in tools if t.name == "dismiss_finding")
    revert = next(t for t in tools if t.name == "revert_last_change")

    df.invoke({"finding_id": seed_finding, "reason": "oops"})
    assert store.get(seed_finding).status == "dismissed"

    revert.invoke({})
    assert store.get(seed_finding).status == "new"
    assert store.get(seed_finding).dismissed_reason is None


def test_snooze_finding_round_trip(ctx, seed_finding):
    from storage.findings import FindingStore
    store = FindingStore(ctx.db)
    tools = build_tools(ctx)
    sf = next(t for t in tools if t.name == "snooze_finding")
    result = sf.invoke({"finding_id": seed_finding, "days": 7})
    assert "error" not in result
    assert result["status"] == "snoozed"
    f = store.get(seed_finding)
    assert f.status == "snoozed"
    assert f.snoozed_until is not None


def test_snooze_finding_validates_days(ctx, seed_finding):
    tools = build_tools(ctx)
    sf = next(t for t in tools if t.name == "snooze_finding")
    result = sf.invoke({"finding_id": seed_finding, "days": 0})
    assert "error" in result


def test_query_portfolio_round_trip(ctx):
    tools = build_tools(ctx)
    add = next(t for t in tools if t.name == "add_research_query")
    list_q = next(t for t in tools if t.name == "list_research_queries")
    remove = next(t for t in tools if t.name == "remove_research_query")

    test_query = f"TEST-PORTFOLIO-{os.getpid()}"

    # Initially absent
    initial = list_q.invoke({})
    assert all(e["query"] != test_query for e in initial["queries"])

    # Add
    add_result = add.invoke({"query": test_query, "payment_verified": "1"})
    assert "error" not in add_result
    assert add_result["added"] is True
    listed = list_q.invoke({})
    matching = [e for e in listed["queries"] if e["query"] == test_query]
    assert len(matching) == 1
    assert matching[0]["filters"] == {"payment_verified": "1"}
    assert matching[0]["added_by"] == "operator"

    # Remove
    rm_result = remove.invoke({"query": test_query})
    assert "error" not in rm_result
    assert rm_result["removed"] >= 1
    final = list_q.invoke({})
    assert all(e["query"] != test_query for e in final["queries"])


def test_add_research_query_revertible(ctx):
    tools = build_tools(ctx)
    add = next(t for t in tools if t.name == "add_research_query")
    list_q = next(t for t in tools if t.name == "list_research_queries")
    revert = next(t for t in tools if t.name == "revert_last_change")
    remove = next(t for t in tools if t.name == "remove_research_query")

    test_query = f"TEST-PORTFOLIO-REVERT-{os.getpid()}"

    add.invoke({"query": test_query})
    assert any(e["query"] == test_query for e in list_q.invoke({})["queries"])

    revert.invoke({})
    assert all(e["query"] != test_query for e in list_q.invoke({})["queries"])

    # Belt-and-suspenders cleanup in case revert didn't fire correctly
    remove.invoke({"query": test_query})


# ---- set_pacing_budget (R-Task 10) ----

def test_set_pacing_budget_writes_sysconfig(ctx):
    from storage.conversations import SystemConfigStore
    sysconfig = SystemConfigStore(ctx.db)
    # Save prior value so we can restore after the test
    prior = sysconfig.get("pacing_budget_per_hour")
    try:
        tools = build_tools(ctx)
        spb = next(t for t in tools if t.name == "set_pacing_budget")
        result = spb.invoke({"per_hour": 250})
        assert "error" not in result
        assert result["pacing_budget_per_hour"] == 250
        assert sysconfig.get("pacing_budget_per_hour") == 250
    finally:
        # Restore for other tests / live system
        if prior is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM system_config WHERE key = 'pacing_budget_per_hour'"
                    )
        else:
            sysconfig.set("pacing_budget_per_hour", prior)


def test_set_pacing_budget_validates_positive(ctx):
    tools = build_tools(ctx)
    spb = next(t for t in tools if t.name == "set_pacing_budget")
    assert "error" in spb.invoke({"per_hour": 0})
    assert "error" in spb.invoke({"per_hour": -50})


def test_set_pacing_budget_revertible(ctx):
    """Setting then reverting restores the prior sysconfig state.

    If pacing_budget_per_hour was unset before this test, revert deletes
    the key entirely. If it was set, revert restores the prior value.
    """
    from storage.conversations import SystemConfigStore
    sysconfig = SystemConfigStore(ctx.db)
    prior = sysconfig.get("pacing_budget_per_hour")
    try:
        tools = build_tools(ctx)
        spb = next(t for t in tools if t.name == "set_pacing_budget")
        revert = next(t for t in tools if t.name == "revert_last_change")

        spb.invoke({"per_hour": 999})
        assert sysconfig.get("pacing_budget_per_hour") == 999

        revert.invoke({})
        # After revert: should be back to prior state
        post_revert = sysconfig.get("pacing_budget_per_hour")
        assert post_revert == prior
    finally:
        if prior is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM system_config WHERE key = 'pacing_budget_per_hour'"
                    )
        else:
            sysconfig.set("pacing_budget_per_hour", prior)


# ---- BA proposers (R-Task 11) ----

def test_propose_project_returns_brief(ctx, monkeypatch):
    """Tool wraps propose_project_brief LLM call. Mock the LLM, verify
    the returned dict carries all WHY fields."""
    from ai.schemas import ProjectGapBrief
    fake_brief = ProjectGapBrief(
        title="Multi-tenant Ragas eval pipeline with LangSmith",
        one_line_pitch="Drop-in eval harness for production RAG with audit trail.",
        why_demand="Corpus: 14 jobs/30d name Ragas, $75/hr median, all US.",
        why_gap="Operator's RAG platform has hybrid search but no eval.",
        why_goal_fit="Goal min_hourly=$80; niche straddles at $75-100/hr.",
        relevance_tags=["ragas", "langsmith"],
    )
    monkeypatch.setattr(
        "ai.ba_proposer.propose_project_brief",
        lambda *args, **kwargs: fake_brief,
    )

    tools = build_tools(ctx)
    pp = next(t for t in tools if t.name == "propose_project")
    result = pp.invoke({"theme": "RAG eval pipelines"})
    assert "error" not in result
    assert "Ragas" in result["title"]
    assert "why_demand" in result
    assert "why_gap" in result
    assert "why_goal_fit" in result
    assert "relevance_tags" in result


def test_propose_project_rejects_empty_theme(ctx):
    tools = build_tools(ctx)
    pp = next(t for t in tools if t.name == "propose_project")
    assert "error" in pp.invoke({"theme": ""})
    assert "error" in pp.invoke({"theme": "  "})


def test_propose_project_handles_llm_validation_failure(ctx, monkeypatch):
    """When the LLM returns junk that doesn't pass the schema, the
    proposer returns None and the tool surfaces an error."""
    monkeypatch.setattr(
        "ai.ba_proposer.propose_project_brief",
        lambda *args, **kwargs: None,
    )
    tools = build_tools(ctx)
    pp = next(t for t in tools if t.name == "propose_project")
    result = pp.invoke({"theme": "AI"})
    assert "error" in result
    assert "schema" in result["error"].lower() or "WHY" in result["error"]


def test_propose_setup_refuses_zero_backtest(ctx, monkeypatch):
    """If the proposed filter has 0 corpus matches, the tool returns
    an error rather than a silent zero-match proposal."""
    from ai.schemas import SetupProposal
    fake_setup = SetupProposal(
        name="test-empty-setup",
        tier="normal",
        filter_dsl={"all_of": [{"skill_in": ["never-occurring-skill-xyz"]}]},
        prose="A test prose definition that satisfies the schema length floor.",
        backtest_count=0,
        why_demand="Corpus shows N jobs in this niche per the snapshot evidence.",
        why_gap="Operator portfolio has tangential coverage but not direct fit.",
        why_goal_fit="Goal min_hourly aligns with the niche's typical band.",
    )
    monkeypatch.setattr(
        "ai.ba_proposer.propose_setup",
        lambda *args, **kwargs: fake_setup,
    )

    tools = build_tools(ctx)
    ps = next(t for t in tools if t.name == "propose_setup_from_corpus")
    result = ps.invoke({"theme": "never-occurring-skill"})
    assert "error" in result
    assert "0 corpus matches" in result["error"]
    assert "proposal" in result  # we surface the proposal so operator can see what was attempted


def test_propose_setup_returns_real_backtest_count(ctx, monkeypatch):
    """When backtest yields > 0, tool overwrites the LLM-supplied
    placeholder backtest_count with the real number."""
    from ai.schemas import SetupProposal
    fake_setup = SetupProposal(
        name="empty-filter-setup",
        tier="normal",
        # Empty filter → matches every corpus job (per domain.scoring's
        # empty-spec semantics)
        filter_dsl={},
        prose="Test prose definition that satisfies the schema length floor.",
        backtest_count=0,  # LLM placeholder; we'll see this overwritten
        why_demand="Corpus snapshot shows abundant jobs in this niche per evidence.",
        why_gap="Operator portfolio coverage is partial but shippable.",
        why_goal_fit="Goal alignment is straightforward at the proposed thresholds.",
    )
    monkeypatch.setattr(
        "ai.ba_proposer.propose_setup",
        lambda *args, **kwargs: fake_setup,
    )

    tools = build_tools(ctx)
    ps = next(t for t in tools if t.name == "propose_setup_from_corpus")
    result = ps.invoke({"theme": "anything"})

    # Either: corpus is empty in the test DB → error, or has rows → real count
    if "error" in result:
        # Empty DB path — error surfaces the proposal too
        assert "0 corpus matches" in result["error"]
    else:
        assert "backtest_count" in result
        assert result["backtest_count"] > 0  # overwritten with real count
        assert "backtest" in result


def test_pacing_apply_from_sysconfig(ctx):
    """The apply_from_sysconfig helper actually mutates the live pacer."""
    from storage.conversations import SystemConfigStore
    from substrate import pacing
    sysconfig = SystemConfigStore(ctx.db)
    prior_value = sysconfig.get("pacing_budget_per_hour")
    prior_pacer = pacing.get_pacer().cfg.max_actions_per_hour
    try:
        sysconfig.set("pacing_budget_per_hour", 777)
        pacing.apply_from_sysconfig(sysconfig)
        assert pacing.get_pacer().cfg.max_actions_per_hour == 777
    finally:
        # Restore
        pacing.set_max_actions_per_hour(prior_pacer)
        if prior_value is None:
            with ctx.db.transaction() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM system_config WHERE key = 'pacing_budget_per_hour'"
                    )
        else:
            sysconfig.set("pacing_budget_per_hour", prior_value)
