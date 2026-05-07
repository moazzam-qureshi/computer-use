"""Integration tests: analyze_corpus and backtest_filter_dsl over a real DB.

Uses the same fresh-DB drop-schema fixture pattern as test_jobs.py.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai.market_analysis import analyze_corpus, backtest_filter_dsl
from domain.types import Job
from storage.jobs import JobStore
from storage.migrate import apply_migrations


MIG = Path(__file__).parents[3] / "storage" / "migrations"


@pytest.fixture
def fresh_db(db):
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    apply_migrations(db, MIG)
    return db


def _job(job_id: str, **kwargs) -> Job:
    base = dict(
        job_id=job_id,
        url=f"https://www.upwork.com/jobs/{job_id}",
        title=f"Job {job_id}",
        description="d",
        budget_kind="hourly",
        budget_min_usd=50.0,
        budget_max_usd=80.0,
        skills=["RAG"],
        client_country="United States",
        client_payment_verified=True,
    )
    base.update(kwargs)
    return Job(**base)


def _seed(db, jobs_with_source: list[tuple[Job, str]]):
    store = JobStore(db)
    for job, source in jobs_with_source:
        store.upsert(job, source=source, raw_panel={})


# ----- analyze_corpus -----

def test_empty_corpus(fresh_db):
    snap = analyze_corpus(fresh_db, window_days=30)
    assert snap.total_jobs == 0
    assert snap.budget_p50_usd is None
    assert snap.payment_verified_share is None
    assert snap.top_skills == []


def test_aggregates_skills_budgets_countries(fresh_db):
    _seed(fresh_db, [
        (_job("a1", skills=["RAG", "Python"], budget_min_usd=60.0,
              client_country="United States"), "ba:rag"),
        (_job("a2", skills=["RAG", "LangChain"], budget_min_usd=80.0,
              client_country="United States"), "ba:rag"),
        (_job("a3", skills=["Voice AI"], budget_min_usd=120.0,
              client_country="Germany", client_payment_verified=False), "ba:voice"),
    ])
    snap = analyze_corpus(fresh_db, window_days=30)

    assert snap.total_jobs == 3
    skill_names = {s for s, _, _ in snap.top_skills}
    assert {"RAG", "Python", "LangChain", "Voice AI"} <= skill_names
    rag_count = next(c for s, c, _ in snap.top_skills if s == "RAG")
    assert rag_count == 2

    # 3 budget values [60, 80, 120] → p50 = 80
    assert snap.budget_p50_usd == 80.0

    # 2 of 3 jobs are payment_verified
    assert abs(snap.payment_verified_share - (2 / 3)) < 1e-9

    # Country breakdown
    countries = {c: n for c, n, _ in snap.client_country_breakdown}
    assert countries == {"United States": 2, "Germany": 1}


def test_source_pattern_scopes(fresh_db):
    _seed(fresh_db, [
        (_job("a1", skills=["RAG"]), "ba:rag"),
        (_job("a2", skills=["Voice AI"]), "feed"),
    ])
    rag_only = analyze_corpus(fresh_db, window_days=30, source_pattern="ba:%")
    feed_only = analyze_corpus(fresh_db, window_days=30, source_pattern="feed")
    assert rag_only.total_jobs == 1
    assert feed_only.total_jobs == 1
    skill_names = {s for s, _, _ in rag_only.top_skills}
    assert "RAG" in skill_names
    assert "Voice AI" not in skill_names


def test_window_excludes_old_jobs(fresh_db):
    """Rows scraped before the window must not appear.

    JobStore.upsert sets scraped_first_at via SQL DEFAULT now(); to seed an
    old row we backdate it directly in SQL after the upsert.
    """
    _seed(fresh_db, [(_job("recent"), "ba:r")])
    _seed(fresh_db, [(_job("old"), "ba:r")])
    with fresh_db.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET scraped_first_at = %s WHERE job_id = 'old'",
                (datetime.now(timezone.utc) - timedelta(days=60),),
            )
    snap = analyze_corpus(fresh_db, window_days=30)
    assert snap.total_jobs == 1


# ----- backtest_filter_dsl -----

def test_backtest_skill_filter(fresh_db):
    _seed(fresh_db, [
        (_job("a", skills=["RAG", "Python"]), "ba:r"),
        (_job("b", skills=["Python"]), "ba:r"),
        (_job("c", skills=["Voice AI"]), "ba:r"),
    ])
    result = backtest_filter_dsl(
        fresh_db,
        filter_dsl={"all_of": [{"skill_in": ["rag"]}]},
        window_days=30,
    )
    assert result.match_count == 1
    assert result.total_in_window == 3
    assert result.sample[0]["job_id"] == "a"


def test_backtest_compound_filter(fresh_db):
    _seed(fresh_db, [
        (_job("hi-budget", skills=["RAG"], budget_min_usd=80.0), "ba:r"),
        (_job("lo-budget", skills=["RAG"], budget_min_usd=20.0), "ba:r"),
        (_job("hi-no-rag", skills=["Voice AI"], budget_min_usd=100.0), "ba:r"),
    ])
    result = backtest_filter_dsl(
        fresh_db,
        filter_dsl={"all_of": [
            {"skill_in": ["rag"]},
            {"budget_min_at_least": 50.0},
        ]},
        window_days=30,
    )
    assert result.match_count == 1
    assert result.sample[0]["job_id"] == "hi-budget"


def test_backtest_empty_dsl_matches_all(fresh_db):
    """Empty filter_dsl is the LLM-only setup convention — matches everything."""
    _seed(fresh_db, [
        (_job("a"), "ba:r"),
        (_job("b"), "ba:r"),
    ])
    result = backtest_filter_dsl(fresh_db, filter_dsl={}, window_days=30)
    assert result.match_count == 2
    assert result.total_in_window == 2


def test_backtest_sample_caps_at_5(fresh_db):
    _seed(fresh_db, [(_job(f"j{i}", skills=["RAG"]), "ba:r") for i in range(10)])
    result = backtest_filter_dsl(
        fresh_db,
        filter_dsl={"all_of": [{"skill_in": ["rag"]}]},
        window_days=30,
    )
    assert result.match_count == 10
    assert len(result.sample) == 5
