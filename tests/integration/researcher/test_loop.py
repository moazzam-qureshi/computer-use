"""Integration test: run_researcher_pass end-to-end against a real DB.

Mocks the substrate-bound deep_search_and_ingest (no real Chrome) and
the LLM-bound find_specific_patterns (no real OpenAI call). Exercises
everything else for real: portfolio loading, corpus reads, finding
persistence with dedup, summary structure.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai.schemas import JobForensicFinding
from domain.types import Job
from researcher.loop import run_researcher_pass
from researcher.query_portfolio import add as portfolio_add
from storage.findings import FindingStore
from storage.jobs import JobStore
from storage.migrate import apply_migrations
from storage.conversations import SystemConfigStore


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _seed_portfolio(db):
    sysconfig = SystemConfigStore(db)
    portfolio_add(sysconfig, query="RAG engineer",
                  filters={"payment_verified": "1", "hourly_rate": "60-"})
    portfolio_add(sysconfig, query="voice AI",
                  filters={"payment_verified": "1"})


def _canned_jobs(query_marker: str) -> list[Job]:
    """Two synthetic Job objects per query — used to feed forensics with
    a deterministic batch."""
    return [
        Job(
            job_id=f"~01-{query_marker}-a",
            url=f"https://www.upwork.com/jobs/x_~01-{query_marker}-a",
            title=f"Build a {query_marker} pipeline",
            description="Long description with Ragas + LangSmith mention. " * 30,
            budget_kind="hourly",
            budget_min_usd=80.0, budget_max_usd=120.0,
            skills=["RAG", "LangChain"],
            client_country="United States",
            client_payment_verified=True,
        ),
        Job(
            job_id=f"~01-{query_marker}-b",
            url=f"https://www.upwork.com/jobs/x_~01-{query_marker}-b",
            title=f"Need {query_marker} expert",
            description="Another long description with similar pattern. " * 30,
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
        headline="Multiple jobs follow Ragas + LangSmith template this week",
        why_specific="Two synthetic jobs in test corpus follow same shape — fixture pattern.",
        portfolio_tie="Operator's portfolio mentions LangSmith but not Ragas — gap.",
        suggested_action="Build a Ragas demo, push to GitHub, add 1-paragraph case study.",
        urgency="this_week",
        evidence_job_ids=evidence,
    )


def test_pass_with_canned_results(fresh_db, monkeypatch):
    _seed_portfolio(fresh_db)

    # Monkeypatch the substrate call to NOT touch Chrome.
    # Instead it (a) writes the canned jobs to the corpus directly via
    # JobStore, (b) returns the same shape deep_search_and_ingest does.
    def fake_deep_search_and_ingest(db, query, filters=None, max_jobs=30,
                                    window_title="Upwork"):
        from upwork.search_driver import source_tag
        source = source_tag(query, filters)
        store = JobStore(db)
        jobs = _canned_jobs(query.replace(" ", "-"))
        for j in jobs:
            store.upsert(j, source=source, raw_panel={"_test": True})
        return {
            "query": query, "filters": filters or {}, "source": source,
            "scanned": len(jobs), "inserted": len(jobs),
            "updated_existing": 0, "sample": [],
        }

    monkeypatch.setattr(
        "researcher.loop.deep_search_and_ingest",
        fake_deep_search_and_ingest,
    )

    # Monkeypatch the forensic LLM to return one canned finding per call,
    # citing the two jobs ingested in this query.
    def fake_find_patterns(jobs, portfolio, active_goal, *, agent_run_store,
                           parent_run_id=None, model="gpt-5-mini"):
        if not jobs:
            return []
        return [_canned_finding(evidence=[j.job_id for j in jobs])]

    monkeypatch.setattr(
        "researcher.loop.find_specific_patterns",
        fake_find_patterns,
    )

    summary = run_researcher_pass(fresh_db)

    # 2 queries in portfolio, both succeed
    assert summary["queries_run"] == 2
    assert summary["queries_failed"] == 0
    assert summary["jobs_ingested"] == 4  # 2 jobs × 2 queries
    assert summary["findings_persisted"] == 2  # one per query
    assert summary["findings_skipped_dedup"] == 0
    assert len(summary["per_query"]) == 2
    for pq in summary["per_query"]:
        assert pq["status"] == "ok"
        assert pq["scanned"] == 2
        assert pq["n_findings"] == 1


def test_second_pass_dedups_identical_findings(fresh_db, monkeypatch):
    _seed_portfolio(fresh_db)

    def fake_deep_search_and_ingest(db, query, filters=None, max_jobs=30,
                                    window_title="Upwork"):
        from upwork.search_driver import source_tag
        source = source_tag(query, filters)
        store = JobStore(db)
        jobs = _canned_jobs(query.replace(" ", "-"))
        for j in jobs:
            store.upsert(j, source=source, raw_panel={"_test": True})
        return {
            "query": query, "filters": filters or {}, "source": source,
            "scanned": len(jobs), "inserted": len(jobs),
            "updated_existing": 0, "sample": [],
        }

    def fake_find_patterns(jobs, portfolio, active_goal, *, agent_run_store,
                           parent_run_id=None, model="gpt-5-mini"):
        if not jobs:
            return []
        return [_canned_finding(evidence=[j.job_id for j in jobs])]

    monkeypatch.setattr(
        "researcher.loop.deep_search_and_ingest", fake_deep_search_and_ingest,
    )
    monkeypatch.setattr(
        "researcher.loop.find_specific_patterns", fake_find_patterns,
    )

    first = run_researcher_pass(fresh_db)
    second = run_researcher_pass(fresh_db)

    assert first["findings_persisted"] == 2
    assert first["findings_skipped_dedup"] == 0
    assert second["findings_persisted"] == 0
    assert second["findings_skipped_dedup"] == 2

    # Verify only 2 finding rows exist total (not 4).
    fs = FindingStore(fresh_db)
    new_rows = fs.list_by_status("new")
    assert len(new_rows) == 2


def test_per_query_failure_isolated(fresh_db, monkeypatch):
    """One query's exception shouldn't kill the rest of the pass."""
    _seed_portfolio(fresh_db)

    call_log: list[str] = []

    def fake_deep_search_and_ingest(db, query, filters=None, max_jobs=30,
                                    window_title="Upwork"):
        call_log.append(query)
        if query == "RAG engineer":
            raise RuntimeError("simulated substrate failure")
        from upwork.search_driver import source_tag
        source = source_tag(query, filters)
        store = JobStore(db)
        for j in _canned_jobs(query.replace(" ", "-")):
            store.upsert(j, source=source, raw_panel={"_test": True})
        return {"scanned": 2, "inserted": 2, "updated_existing": 0,
                "source": source, "query": query, "filters": filters or {},
                "sample": []}

    def fake_find_patterns(jobs, portfolio, active_goal, *, agent_run_store,
                           parent_run_id=None, model="gpt-5-mini"):
        if not jobs:
            return []
        return [_canned_finding(evidence=[j.job_id for j in jobs])]

    monkeypatch.setattr(
        "researcher.loop.deep_search_and_ingest", fake_deep_search_and_ingest,
    )
    monkeypatch.setattr(
        "researcher.loop.find_specific_patterns", fake_find_patterns,
    )

    summary = run_researcher_pass(fresh_db)

    # Both queries attempted
    assert len(call_log) == 2
    assert summary["queries_failed"] == 1
    assert summary["queries_run"] == 1
    # The successful one still produced a finding
    assert summary["findings_persisted"] == 1

    # The failed entry has the error captured
    failed = [pq for pq in summary["per_query"] if pq["status"] == "scan_failed"]
    assert len(failed) == 1
    assert "simulated substrate failure" in failed[0]["error"]


def test_empty_portfolio_returns_info(fresh_db):
    """No queries configured → pass returns immediately with info message."""
    summary = run_researcher_pass(fresh_db)
    assert summary["queries_run"] == 0
    assert summary["jobs_ingested"] == 0
    assert "info" in summary
    assert "empty query portfolio" in summary["info"]


def test_portfolio_override_used(fresh_db, monkeypatch):
    """Operator can pass an override portfolio for one-off pass."""
    # Don't seed system_config; override is the only source
    captured_queries: list[str] = []

    def fake_deep_search_and_ingest(db, query, filters=None, **kw):
        captured_queries.append(query)
        return {"scanned": 0, "inserted": 0, "updated_existing": 0,
                "source": f"ba:{query}", "query": query, "filters": filters or {},
                "sample": []}

    monkeypatch.setattr(
        "researcher.loop.deep_search_and_ingest", fake_deep_search_and_ingest,
    )

    summary = run_researcher_pass(
        fresh_db,
        portfolio_override=[
            {"query": "one-off query", "filters": {}, "added_at": "x", "added_by": "test"},
        ],
    )
    assert captured_queries == ["one-off query"]
    assert summary["queries_run"] == 1  # ran but produced no jobs
