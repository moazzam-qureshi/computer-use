import pytest
from pydantic import ValidationError

from ai.schemas import (
    RelevanceCheck, Enrichment, ProposalDraft, CoverLetter,
    JobForensicFinding, ProjectGapBrief, SetupProposal,
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


# ----- ProjectGapBrief: WHY enforcement -----

def _good_brief(**overrides):
    base = dict(
        title="Multi-tenant Ragas eval pipeline with LangSmith trace export",
        one_line_pitch="A drop-in evaluation harness for production RAG systems with audit trail.",
        why_demand=(
            "Corpus snapshot: 14 jobs in last 30 days name Ragas, "
            "median budget $75/hr, all from US clients."
        ),
        why_gap=(
            "Operator's 'Enterprise Agentic RAG Platform' has hybrid "
            "search but no eval layer; this fills it."
        ),
        why_goal_fit=(
            "Active goal min_hourly=$80 — this niche's $75/hr median is "
            "close enough to bid the upper end with confidence."
        ),
        relevance_tags=["ragas", "langsmith", "eval-pipeline"],
    )
    base.update(overrides)
    return ProjectGapBrief(**base)


def test_brief_full_valid():
    b = _good_brief()
    assert "Ragas" in b.title


def test_brief_rejects_short_title():
    with pytest.raises(ValidationError):
        _good_brief(title="X")


def test_brief_rejects_short_why_demand():
    with pytest.raises(ValidationError):
        _good_brief(why_demand="lots of demand")


def test_brief_rejects_empty_relevance_tags():
    with pytest.raises(ValidationError):
        _good_brief(relevance_tags=[])


# ----- SetupProposal: WHY enforcement -----

def _good_setup(**overrides):
    base = dict(
        name="rag-eval-strike-zone",
        tier="normal",
        filter_dsl={"all_of": [
            {"skill_in": ["rag", "ragas"]},
            {"min_hourly": 60.0},
        ]},
        prose=(
            "Production RAG eval engagements; specifically jobs naming "
            "Ragas or LangSmith and asking for hybrid search audits."
        ),
        backtest_count=0,  # placeholder — caller overwrites
        why_demand=(
            "Corpus shows 14 jobs/month in this niche, median $75/hr, "
            "85% payment-verified."
        ),
        why_gap=(
            "Operator's 'Enterprise Agentic RAG' platform is a direct "
            "fit for this niche."
        ),
        why_goal_fit=(
            "Goal min_hourly=$80; this niche's $75-100/hr range straddles it."
        ),
    )
    base.update(overrides)
    return SetupProposal(**base)


def test_setup_full_valid():
    s = _good_setup()
    assert s.tier == "normal"


def test_setup_rejects_invalid_tier():
    with pytest.raises(ValidationError):
        _good_setup(tier="ultra")


def test_setup_rejects_short_why_demand():
    with pytest.raises(ValidationError):
        _good_setup(why_demand="hot niche")


def test_setup_rejects_negative_backtest():
    with pytest.raises(ValidationError):
        _good_setup(backtest_count=-1)
