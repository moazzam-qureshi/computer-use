import pytest
from pydantic import ValidationError

from ai.schemas import (
    RelevanceCheck, Enrichment, ProposalDraft, CoverLetter,
    JobForensicFinding,
)

def test_relevance_check_round_trip():
    r = RelevanceCheck(relevant=True, score=0.85, reasoning="strong fit")
    assert r.relevant is True
    assert r.score == 0.85

def test_enrichment_defaults():
    e = Enrichment()
    assert e.extracted_tech == []
    assert e.project_shape is None

def test_proposal_draft_required_fields():
    p = ProposalDraft(
        title="Build it",
        opener="hey",
        approach_phases=["a", "b", "c"],
        deliverables=["d"],
        timeline=["w1"],
        questions=["q?"],
        mermaid_diagram="graph LR\nA-->B",
        about_me="me",
    )
    assert p.title == "Build it"


# ----- JobForensicFinding: WHY enforcement -----

def _good_finding(**overrides):
    base = dict(
        finding_type="emerging_template",
        headline="5 jobs this week want Ragas + LangSmith integration",
        why_specific=(
            "Three of the five explicitly mention Ragas; two more name "
            "LangSmith as a hard requirement. Quoted: 'must have Ragas '."
        ),
        portfolio_tie=(
            "Operator's portfolio mentions LangSmith but not Ragas. "
            "Building a Ragas demo closes the gap."
        ),
        suggested_action=(
            "Build a Ragas eval demo, push to GitHub, add 1-paragraph "
            "case study before bidding any of these."
        ),
        urgency="this_week",
        evidence_job_ids=["~01a", "~01b", "~01c"],
    )
    base.update(overrides)
    return JobForensicFinding(**base)


def test_finding_full_valid():
    f = _good_finding()
    assert f.finding_type == "emerging_template"
    assert len(f.evidence_job_ids) == 3


def test_finding_rejects_short_headline():
    with pytest.raises(ValidationError):
        _good_finding(headline="too short")


def test_finding_rejects_short_why_specific():
    with pytest.raises(ValidationError):
        _good_finding(why_specific="multiple jobs want this")


def test_finding_rejects_short_portfolio_tie():
    with pytest.raises(ValidationError):
        _good_finding(portfolio_tie="no fit")


def test_finding_rejects_short_suggested_action():
    with pytest.raises(ValidationError):
        _good_finding(suggested_action="consider it")


def test_finding_rejects_single_evidence_job():
    """Single-job 'patterns' aren't patterns — schema enforces ≥2."""
    with pytest.raises(ValidationError):
        _good_finding(evidence_job_ids=["~01a"])


def test_finding_rejects_empty_evidence():
    with pytest.raises(ValidationError):
        _good_finding(evidence_job_ids=[])


def test_finding_rejects_invalid_finding_type():
    with pytest.raises(ValidationError):
        _good_finding(finding_type="my_invented_finding_type")


def test_finding_rejects_invalid_urgency():
    with pytest.raises(ValidationError):
        _good_finding(urgency="someday")


def test_finding_accepts_all_valid_finding_types():
    for ft in ("emerging_template", "failure_mode_pattern",
               "tech_combo_emergence", "specific_stack_demand",
               "budget_anomaly", "geographic_cluster"):
        f = _good_finding(finding_type=ft)
        assert f.finding_type == ft
