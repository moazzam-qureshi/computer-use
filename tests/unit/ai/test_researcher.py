"""Unit tests: find_specific_patterns validation logic.

Mocks the LLM call (the actual gpt-5-mini call has integration coverage
elsewhere — this verifies the input shaping + hallucination filter.)
"""
from unittest.mock import MagicMock

import pytest

from ai.researcher import (
    find_specific_patterns, _budget_text, _goal_block, _job_block,
)
from ai.schemas import JobForensicFinding
from domain.types import Job
from storage.goals import Goal


def _job(job_id="~01a", **kw):
    base = dict(
        job_id=job_id,
        url=f"https://www.upwork.com/jobs/x_{job_id}",
        title="Build a RAG eval pipeline",
        description="We need eval. " * 50,
        budget_kind="hourly",
        budget_min_usd=60.0, budget_max_usd=90.0,
        skills=["RAG"],
        client_country="United States",
        client_payment_verified=True,
    )
    base.update(kw)
    return Job(**base)


def _good_finding(evidence):
    return JobForensicFinding(
        finding_type="emerging_template",
        headline="5 jobs this week want Ragas + LangSmith integration",
        why_specific="Three of five explicitly mention Ragas; quoted: 'must have Ragas'.",
        portfolio_tie="Operator's portfolio mentions LangSmith, not Ragas — gap.",
        suggested_action="Build a Ragas demo this week, push to GitHub, add to portfolio.",
        urgency="this_week",
        evidence_job_ids=evidence,
    )


# ----- input-shaping helpers -----

def test_budget_text_hourly_range():
    j = _job(budget_kind="hourly", budget_min_usd=60.0, budget_max_usd=90.0)
    assert _budget_text(j) == "hourly $60-$90"


def test_budget_text_fixed_single():
    j = _job(budget_kind="fixed", budget_min_usd=5000.0, budget_max_usd=5000.0)
    assert _budget_text(j) == "fixed $5000"


def test_budget_text_unknown():
    j = _job(budget_kind=None, budget_min_usd=None, budget_max_usd=None)
    assert "unknown" in _budget_text(j)


def test_goal_block_no_goal():
    assert "no active goal" in _goal_block(None)


def test_goal_block_with_goal():
    g = Goal(
        goal_id=1, prose="land 2 RAG clients at $80/hr",
        target_metric=None, target_value=None, horizon=None,
        min_hourly=80.0, min_budget=None,
        preferred_country="United States", notes=None,
    )
    block = _goal_block(g)
    assert "min_hourly: 80" in block
    assert "preferred_country: United States" in block


def test_job_block_truncates_long_description():
    long_desc = "x" * 5000
    j = _job(description=long_desc)
    block = _job_block(j)
    assert "(truncated)" in block
    # Should still include the job_id and title
    assert j.job_id in block
    assert j.title in block


# ----- hallucination filter -----

def test_finds_drop_findings_with_unknown_evidence_ids(monkeypatch):
    """The whole point: if the LLM returns a finding citing a job_id that
    isn't in the input batch, that finding gets silently dropped."""
    jobs = [_job("~01a"), _job("~01b"), _job("~01c"), _job("~01d")]

    valid_finding = _good_finding(evidence=["~01a", "~01b"])
    hallucinated = _good_finding(evidence=["~01zzz", "~01yyy"])  # not in batch

    fake_agent = MagicMock()
    fake_agent.invoke.return_value = {
        "structured_response": MagicMock(findings=[valid_finding, hallucinated]),
    }

    import ai.researcher as researcher_mod
    monkeypatch.setattr(
        researcher_mod, "create_agent", lambda **_kw: fake_agent,
    )

    # CostTracker requires an AgentRunStore; mock that too
    fake_store = MagicMock()
    fake_store.start.return_value = 1

    out = find_specific_patterns(
        jobs=jobs, portfolio=[],
        active_goal=None, agent_run_store=fake_store,
    )

    assert len(out) == 1
    assert out[0] is valid_finding


def test_finds_returns_empty_when_no_jobs():
    """No input jobs → don't even call the LLM, return []."""
    fake_store = MagicMock()
    out = find_specific_patterns(
        jobs=[], portfolio=[], active_goal=None,
        agent_run_store=fake_store,
    )
    assert out == []
    fake_store.start.assert_not_called()


def test_finds_partial_overlap_evidence_dropped(monkeypatch):
    """Even ONE unknown evidence_id in a finding should drop the whole
    finding — half-hallucinated evidence is still hallucinated."""
    jobs = [_job("~01a"), _job("~01b")]
    half = _good_finding(evidence=["~01a", "~01zzz"])  # mix valid + invalid

    fake_agent = MagicMock()
    fake_agent.invoke.return_value = {
        "structured_response": MagicMock(findings=[half]),
    }

    import ai.researcher as researcher_mod
    monkeypatch.setattr(
        researcher_mod, "create_agent", lambda **_kw: fake_agent,
    )
    fake_store = MagicMock()
    fake_store.start.return_value = 1

    out = find_specific_patterns(
        jobs=jobs, portfolio=[], active_goal=None,
        agent_run_store=fake_store,
    )
    assert out == []  # finding dropped


def test_finds_no_structured_response_returns_empty(monkeypatch):
    """LLM returns malformed result → empty findings, no crash."""
    jobs = [_job("~01a")]
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = {}  # no structured_response

    import ai.researcher as researcher_mod
    monkeypatch.setattr(
        researcher_mod, "create_agent", lambda **_kw: fake_agent,
    )
    fake_store = MagicMock()
    fake_store.start.return_value = 1

    out = find_specific_patterns(
        jobs=jobs, portfolio=[], active_goal=None,
        agent_run_store=fake_store,
    )
    assert out == []
