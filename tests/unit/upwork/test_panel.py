"""Unit test: panel parser handles the structural anchors deterministically."""
from upwork.panel import parse_panel, PanelData


def _mk_elements():
    """Minimal mocked element list with the structural shapes the parser keys on."""
    from types import SimpleNamespace

    def el(name, role="text", bounds=(0, 0, 100, 30)):
        return SimpleNamespace(name=name, role=role, bounds=bounds, value=None)

    return [
        el("Build evaluation pipeline for our RAG system", role="text", bounds=(1300, 100, 1900, 130)),
        el("Posted 3 minutes ago", role="text"),
        el("Fixed-price"),
        el("$4,000.00"),
        el("Skills and Expertise"),
        el("RAG"),
        el("Pinecone"),
        el("Python"),
        el("About the client"),
        el("Payment verified"),
        el("United States"),
        el("Some long description text " * 20, role="text", bounds=(1300, 500, 1900, 800)),
        el("Copy to clipboard", role="button", bounds=(1820, 220, 1896, 246)),
    ]


def test_parses_title_budget_skills_description():
    p = parse_panel(_mk_elements())
    assert isinstance(p, PanelData)
    assert "evaluation pipeline" in p.title.lower()
    assert p.budget_kind == "fixed"
    assert p.budget_min_usd == 4000
    assert "RAG" in p.skills or "rag" in [s.lower() for s in p.skills]
    assert p.client_payment_verified is True
    assert "long description" in p.description
