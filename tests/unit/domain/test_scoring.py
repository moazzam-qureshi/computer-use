"""Filter-DSL evaluation: given a Job and a Setup, decide if it matches and why."""
from datetime import datetime, timedelta, timezone

import pytest
from domain.types import Job, Setup, FilterDsl
from domain.scoring import score_job_against_setup, MatchResult


def _setup(filter_dsl: dict) -> Setup:
    return Setup(
        setup_id=1,
        name="test",
        status="active",
        tier="normal",
        filter_dsl=FilterDsl(filter_dsl),
        prose_definition=None,
        pitch_template_id=None,
        cover_letter_template_id=None,
        auto_apply_enabled=False,
        escalation_config={},
    )


def _job(**overrides) -> Job:
    base = dict(
        job_id="~012345",
        url="https://www.upwork.com/jobs/~012345",
        title="Build a RAG system",
        description="We need RAG, Pinecone, eval pipeline",
        budget_kind="fixed",
        budget_min_usd=3000.0,
        budget_max_usd=5000.0,
        skills=["RAG", "Pinecone", "Python"],
        client_payment_verified=True,
        client_country="United States",
    )
    base.update(overrides)
    return Job(**base)


def test_skill_in_match():
    setup = _setup({"any_of": [{"skill_in": ["pinecone", "rag", "weaviate"]}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is True
    assert "skill_in" in result.matched_rules


def test_budget_floor_excludes():
    setup = _setup({"all_of": [{"budget_min_at_least": 10000}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is False


def test_payment_verified_required():
    setup = _setup({"all_of": [{"client_payment_verified": True}]})
    assert score_job_against_setup(_job(), setup).matched is True
    assert score_job_against_setup(_job(client_payment_verified=False), setup).matched is False


def test_combined_all_of_skill_budget_verified():
    setup = _setup({
        "all_of": [
            {"skill_in": ["rag", "pinecone"]},
            {"budget_min_at_least": 1500},
            {"client_payment_verified": True},
        ]
    })
    assert score_job_against_setup(_job(), setup).matched is True


def test_no_match_returns_match_false_with_reason():
    setup = _setup({"all_of": [{"skill_in": ["solidity"]}]})
    result = score_job_against_setup(_job(), setup)
    assert result.matched is False
    assert "skill_in" in result.unmet_rules


def test_posted_within_minutes_recent_passes():
    setup = _setup({"all_of": [{"posted_within_minutes": 30}]})
    fresh = _job(posted_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    result = score_job_against_setup(fresh, setup)
    assert result.matched is True
    assert "posted_within_minutes" in result.matched_rules


def test_posted_within_minutes_stale_excluded():
    setup = _setup({"all_of": [{"posted_within_minutes": 30}]})
    stale = _job(posted_at=datetime.now(timezone.utc) - timedelta(minutes=90))
    result = score_job_against_setup(stale, setup)
    assert result.matched is False
    assert "posted_within_minutes" in result.unmet_rules


def test_posted_within_minutes_unknown_posted_at_fails_closed():
    # Operator asked for fresh-only; jobs whose posted_at couldn't be parsed
    # must be excluded rather than slipping through.
    setup = _setup({"all_of": [{"posted_within_minutes": 30}]})
    unknown = _job(posted_at=None)
    assert score_job_against_setup(unknown, setup).matched is False


# ----- min_hourly / max_hourly -----

def test_min_hourly_excludes_low_hourly():
    setup = _setup({"all_of": [{"min_hourly": 60.0}]})
    assert score_job_against_setup(
        _job(budget_kind="hourly", budget_min_usd=40.0, budget_max_usd=50.0),
        setup,
    ).matched is False


def test_min_hourly_passes_high_hourly():
    setup = _setup({"all_of": [{"min_hourly": 60.0}]})
    assert score_job_against_setup(
        _job(budget_kind="hourly", budget_min_usd=80.0, budget_max_usd=120.0),
        setup,
    ).matched is True


def test_min_hourly_passes_non_hourly():
    """Non-hourly (fixed-price) jobs should not be filtered by hourly rules."""
    setup = _setup({"all_of": [{"min_hourly": 60.0}]})
    assert score_job_against_setup(
        _job(budget_kind="fixed", budget_min_usd=5000.0, budget_max_usd=5000.0),
        setup,
    ).matched is True


def test_max_hourly_excludes_high_hourly():
    setup = _setup({"all_of": [{"max_hourly": 100.0}]})
    assert score_job_against_setup(
        _job(budget_kind="hourly", budget_min_usd=120.0, budget_max_usd=150.0),
        setup,
    ).matched is False


# ----- exclude_fixed_under -----

def test_exclude_fixed_under_kills_small_fixed():
    setup = _setup({"all_of": [{"exclude_fixed_under": 1000.0}]})
    assert score_job_against_setup(
        _job(budget_kind="fixed", budget_min_usd=500.0, budget_max_usd=500.0),
        setup,
    ).matched is False


def test_exclude_fixed_under_keeps_large_fixed():
    setup = _setup({"all_of": [{"exclude_fixed_under": 1000.0}]})
    assert score_job_against_setup(
        _job(budget_kind="fixed", budget_min_usd=5000.0, budget_max_usd=5000.0),
        setup,
    ).matched is True


def test_exclude_fixed_under_ignores_hourly():
    setup = _setup({"all_of": [{"exclude_fixed_under": 1000.0}]})
    assert score_job_against_setup(
        _job(budget_kind="hourly", budget_min_usd=20.0, budget_max_usd=30.0),
        setup,
    ).matched is True


# ----- excluded_skills -----

def test_excluded_skills_filters_out():
    setup = _setup({"all_of": [{"excluded_skills": ["solidity", "wordpress"]}]})
    assert score_job_against_setup(
        _job(skills=["RAG", "Solidity"]), setup,
    ).matched is False


def test_excluded_skills_passes_when_clean():
    setup = _setup({"all_of": [{"excluded_skills": ["solidity"]}]})
    assert score_job_against_setup(
        _job(skills=["RAG", "Python"]), setup,
    ).matched is True


# ----- min_client_spend / budget_max_at_most -----

def test_min_client_spend():
    setup = _setup({"all_of": [{"min_client_spend": 5000.0}]})
    assert score_job_against_setup(
        _job(client_total_spent_usd=10000.0), setup,
    ).matched is True
    assert score_job_against_setup(
        _job(client_total_spent_usd=1000.0), setup,
    ).matched is False
    # Unknown spend fails closed — operator asked for spend floor.
    assert score_job_against_setup(
        _job(client_total_spent_usd=None), setup,
    ).matched is False


def test_budget_max_at_most():
    setup = _setup({"all_of": [{"budget_max_at_most": 5000.0}]})
    assert score_job_against_setup(
        _job(budget_kind="fixed", budget_min_usd=3000.0, budget_max_usd=4000.0),
        setup,
    ).matched is True
    assert score_job_against_setup(
        _job(budget_kind="fixed", budget_min_usd=10000.0, budget_max_usd=20000.0),
        setup,
    ).matched is False
