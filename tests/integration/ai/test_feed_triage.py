"""Hits real OpenAI. Skipped if OPENAI_API_KEY missing."""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from storage.migrate import apply_migrations
from storage.agent_runs import AgentRunStore
from storage.goals import Goal
from ai.feed_triage import triage_feed_cards
from upwork.feed_cards import FeedCard

pytestmark = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY")
MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _ai_card() -> FeedCard:
    return FeedCard(
        title="Senior LLM engineer for RAG evaluation pipeline",
        posted_text="2 hours ago",
        budget_text="Hourly: $80-$120",
        experience_level="Expert",
        description_preview=(
            "We have a production RAG system serving 50K queries/day. Retrieval "
            "recall sits around 60%. Looking for an engineer to build an evaluation "
            "harness with golden datasets and run experiments to push recall above 80%."
        ),
        skills=["RAG", "Python", "LLM", "Pinecone"],
        payment_verified=True,
        rating="4.9",
        spent="$120K+",
        country="United States",
        proposals="5 to 10",
    )


def _wordpress_card() -> FeedCard:
    return FeedCard(
        title="WordPress plugin tweak — quick fix",
        posted_text="1 hour ago",
        budget_text="Fixed-price",
        experience_level="Entry level",
        est_label="Est. Budget:",
        est_value="$50",
        description_preview=(
            "Need someone to update a WordPress plugin we use for our blog. "
            "Should be a 1-hour job for someone who knows PHP. No AI involved, "
            "just standard WordPress maintenance."
        ),
        skills=["WordPress", "PHP"],
        payment_verified=False,
        country="India",
        proposals="20 to 50",
    )


def _emdash_card() -> FeedCard:
    """Em-dash in title must round-trip intact through json.dumps + LLM echo."""
    return FeedCard(
        title="AI agent build — autonomous research workflow",
        posted_text="30 minutes ago",
        budget_text="Hourly: $100-$150",
        experience_level="Expert",
        description_preview=(
            "Looking to build an autonomous research agent that can plan, search, "
            "synthesize and produce structured reports. LangChain or similar stack. "
            "Multi-step tool use, must handle long-running research tasks."
        ),
        skills=["LangChain", "Python", "OpenAI"],
        payment_verified=True,
        country="Canada",
    )


def test_triage_picks_ai_jobs_and_skips_non_ai(fresh_db):
    goal = Goal(
        goal_id=1,
        prose=(
            "Find AI engineering work — LLM, RAG, agents, automation. Skip generic "
            "web/mobile dev, marketing copy, content writing, graphic design."
        ),
        target_metric=None, target_value=None, horizon=None,
        min_hourly=None, min_budget=None, preferred_country=None, notes=None,
    )
    runs = AgentRunStore(fresh_db)
    cards = [_ai_card(), _wordpress_card(), _emdash_card()]
    result = triage_feed_cards(cards, goal, agent_run_store=runs)
    matched_titles = {m.title for m in result.matches}

    # The two AI cards should match. The em-dash title must round-trip
    # exactly so downstream title-matching can find it.
    assert _ai_card().title in matched_titles
    assert _emdash_card().title in matched_titles
    assert _wordpress_card().title not in matched_titles

    # Cost-tracking row was recorded.
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(agent_name) FROM agent_runs")
            count, name = cur.fetchone()
            assert count >= 1
            assert name == "feed_triage"


def test_triage_empty_card_list_returns_empty(fresh_db):
    goal = Goal(
        goal_id=1, prose="anything",
        target_metric=None, target_value=None, horizon=None,
        min_hourly=None, min_budget=None, preferred_country=None, notes=None,
    )
    runs = AgentRunStore(fresh_db)
    result = triage_feed_cards([], goal, agent_run_store=runs)
    assert result.matches == []
