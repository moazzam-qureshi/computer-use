"""Hits real OpenAI. Skipped if OPENAI_API_KEY missing."""
import os
import pytest
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from storage.migrate import apply_migrations
from storage.agent_runs import AgentRunStore
from ai.relevance import check_relevance
from domain.types import Job, Setup, FilterDsl

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


def test_relevance_call_completes_and_records_run(fresh_db):
    job = Job(
        job_id="~test1", url="https://x", title="Build a RAG eval pipeline",
        description="We have a RAG system in production with retrieval recall around 60%. Need someone to build evaluation harness with golden datasets.",
        budget_kind="fixed", budget_min_usd=5000.0, budget_max_usd=8000.0,
        skills=["RAG", "Pinecone", "Python"],
        client_payment_verified=True, client_country="United States",
    )
    setup = Setup(
        setup_id=1, name="rag-eval-shops", status="active", tier="normal",
        filter_dsl=FilterDsl({"all_of": []}),
        prose_definition="We bid on RAG evaluation work for technical buyers with real production systems.",
        pitch_template_id=None, cover_letter_template_id=None,
        auto_apply_enabled=False, escalation_config={},
    )
    runs = AgentRunStore(fresh_db)
    result = check_relevance(job, setup, agent_run_store=runs)
    assert isinstance(result.relevant, bool)
    assert 0.0 <= result.score <= 1.0
    assert result.reasoning
    with fresh_db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(agent_name) FROM agent_runs")
            count, name = cur.fetchone()
            assert count == 1
            assert name == "relevance"
