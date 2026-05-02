from ai.schemas import RelevanceCheck, Enrichment, ProposalDraft, CoverLetter

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
