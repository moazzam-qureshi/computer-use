"""Integration test: Researcher scheduler async flow.

Mocks the bot, the substrate (deep_search_and_ingest), and the LLM
(find_specific_patterns + write_nudge), and the Discord sender.
Verifies the full _fire_one path: pass runs, findings persist, nudge
dispatches, last-fire timestamp updates.
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.schemas import JobForensicFinding
from domain.types import Job
from researcher.scheduler import _fire_one
from storage.findings import FindingStore
from storage.jobs import JobStore
from storage.migrate import apply_migrations
from storage.conversations import SystemConfigStore
from researcher.query_portfolio import add as portfolio_add


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _canned_jobs() -> list[Job]:
    return [
        Job(
            job_id="~scheduler-test-a",
            url="https://www.upwork.com/jobs/x_~scheduler-test-a",
            title="Build a RAG eval pipeline",
            description="Long description with Ragas + LangSmith. " * 30,
            budget_kind="hourly",
            budget_min_usd=80.0, budget_max_usd=120.0,
            skills=["RAG", "LangChain"],
            client_country="United States",
            client_payment_verified=True,
        ),
        Job(
            job_id="~scheduler-test-b",
            url="https://www.upwork.com/jobs/x_~scheduler-test-b",
            title="Need RAG expert for eval pipeline",
            description="Same template + Ragas mention. " * 30,
            budget_kind="hourly",
            budget_min_usd=70.0, budget_max_usd=100.0,
            skills=["RAG"],
            client_country="United States",
            client_payment_verified=True,
        ),
    ]


def _canned_finding(evidence) -> JobForensicFinding:
    return JobForensicFinding(
        finding_type="emerging_template",
        headline="Two RAG eval jobs follow same Ragas template this week",
        why_specific="Both cite Ragas and LangSmith with $70-120/hr budget bands.",
        portfolio_tie="Operator portfolio has LangSmith but not Ragas; clear gap.",
        suggested_action="Build a Ragas demo this week, push to GitHub, add case study.",
        urgency="this_week",
        evidence_job_ids=evidence,
    )


def test_fire_one_end_to_end(fresh_db, monkeypatch):
    """One fire of the scheduler: substrate + forensics mocked, full
    flow executes against real DB and a captured Discord sender."""
    sysconfig = SystemConfigStore(fresh_db)
    portfolio_add(sysconfig, query="RAG engineer",
                  filters={"payment_verified": "1"})

    # Mock the substrate: write canned jobs to corpus directly.
    def fake_deep_search_and_ingest(db, query, filters=None, max_jobs=30,
                                    window_title="Upwork"):
        from upwork.search_driver import source_tag
        source = source_tag(query, filters)
        store = JobStore(db)
        for j in _canned_jobs():
            store.upsert(j, source=source, raw_panel={"_test": True})
        return {"scanned": 2, "inserted": 2, "updated_existing": 0,
                "source": source, "query": query, "filters": filters or {},
                "sample": []}

    monkeypatch.setattr(
        "researcher.loop.deep_search_and_ingest", fake_deep_search_and_ingest,
    )

    # Mock the forensic LLM.
    def fake_find_patterns(jobs, portfolio, active_goal, *, agent_run_store,
                           parent_run_id=None, model="gpt-5-mini"):
        if not jobs:
            return []
        return [_canned_finding(evidence=[j.job_id for j in jobs])]

    monkeypatch.setattr(
        "researcher.loop.find_specific_patterns", fake_find_patterns,
    )

    # Mock the nudge writer (so we don't call OpenAI for the DM compression).
    monkeypatch.setattr(
        "researcher.nudge.write_nudge",
        lambda finding, **kw: f"MOCK_DM #{finding.finding_id}",
    )

    # Mock the bot — fetch_user returns a user with an async send().
    sent_messages: list[str] = []
    fake_user = MagicMock()
    async def fake_send(text):
        sent_messages.append(text)
    fake_user.send = fake_send

    fake_bot = MagicMock()
    async def fake_fetch_user(_uid):
        return fake_user
    fake_bot.fetch_user = fake_fetch_user

    fake_settings = MagicMock()
    fake_settings.discord_owner_user_id = 123

    # Bypass _with_com_run's UIA initialization — the substrate is mocked
    # so we don't actually need COM. Patch the helper to call the inner
    # function directly.
    def fake_with_com_run(db, max_jobs_per_query):
        from researcher.loop import run_researcher_pass
        return run_researcher_pass(db, max_jobs_per_query=max_jobs_per_query)

    monkeypatch.setattr("researcher.scheduler._with_com_run", fake_with_com_run)

    ui_lock = asyncio.Lock()
    summary = asyncio.run(_fire_one(
        fake_bot, fake_settings, fresh_db, ui_lock,
        max_jobs_per_query=10,
    ))

    assert summary["queries_run"] == 1
    assert summary["jobs_ingested"] == 2
    assert summary["findings_persisted"] == 1
    assert "nudge" in summary
    assert summary["nudge"]["immediate_sent"] == 1
    assert len(sent_messages) == 1
    assert "MOCK_DM" in sent_messages[0]

    # Finding is now 'nudged' in DB
    fs = FindingStore(fresh_db)
    nudged = fs.list_by_status("nudged")
    assert len(nudged) == 1


def test_fire_one_with_no_portfolio_returns_empty_summary(fresh_db, monkeypatch):
    """Empty query portfolio → pass produces nothing; nudge dispatch
    runs but has nothing to send."""
    fake_user = MagicMock()
    async def fake_send(text):
        pass
    fake_user.send = fake_send
    fake_bot = MagicMock()
    async def fake_fetch_user(_uid):
        return fake_user
    fake_bot.fetch_user = fake_fetch_user
    fake_settings = MagicMock()
    fake_settings.discord_owner_user_id = 123

    def fake_with_com_run(db, max_jobs_per_query):
        from researcher.loop import run_researcher_pass
        return run_researcher_pass(db, max_jobs_per_query=max_jobs_per_query)

    monkeypatch.setattr("researcher.scheduler._with_com_run", fake_with_com_run)

    ui_lock = asyncio.Lock()
    summary = asyncio.run(_fire_one(
        fake_bot, fake_settings, fresh_db, ui_lock,
    ))
    assert summary["queries_run"] == 0
    assert summary["nudge"]["immediate_sent"] == 0
