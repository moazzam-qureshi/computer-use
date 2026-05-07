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


# ----- client-history bleed (real bug from VirtualBox verification) -----

def _mk_hourly_with_client_history():
    """Mimics the real bug: an hourly job whose panel includes the
    'Client's recent history' section listing the client's prior FIXED-PRICE
    postings. Without truncation the parser would walk past the anchor and
    overwrite kind='hourly' with kind='fixed' from the bleed."""
    from types import SimpleNamespace

    def el(name, role="text", bounds=(0, 0, 100, 30)):
        return SimpleNamespace(name=name, role=role, bounds=bounds, value=None)

    return [
        # ---- THIS JOB's section ----
        el("Top 1% AI Engineer - Build an LLM based Knowledge OS",
           role="hyperlink", bounds=(575, 100, 1500, 130)),
        el("Posted 1 day ago", role="text"),
        el("Hourly"),
        el("$40.00"),
        el("$200.00"),
        el("Expert"),
        el("3 to 6 months", role="text"),
        el("30+ hrs/week", role="text"),
        el("Skills and Expertise"),
        el("LLM Prompt Engineering"),
        el("Python"),
        el("AI Agent Development"),
        el("Some long description text " * 20, role="text",
           bounds=(1300, 500, 1900, 800)),
        el("About the client"),
        el("Payment verified"),
        el("United States"),
        # ---- CLIENT'S RECENT HISTORY section starts here ----
        # This is the bleed — every prior posting carries its own budget chip.
        el("Client's recent history (50)", role="text"),
        el("Jobs in progress", role="text"),
        # Prior posting #1: a fixed-price one
        el("Some completed project title"),
        el("Fixed-price"),
        el("$5,000.00"),
        # Prior posting #2: another fixed-price
        el("Another completed project"),
        el("Fixed-price"),
        el("$2,500.00"),
        # Prior posting #3: yet another fixed-price
        el("Third completed project"),
        el("Fixed-price"),
        el("$1,000.00"),
    ]


def test_client_history_bleed_does_not_clobber_budget_kind():
    """Real bug surfaced on VirtualBox 2026-05-07: an hourly job had its
    budget_kind overwritten to 'fixed' because the parser walked past the
    Client's recent history section and matched the prior postings'
    Fixed-price chips. Truncating at the anchor is the contract."""
    p = parse_panel(_mk_hourly_with_client_history())
    assert p.budget_kind == "hourly", (
        f"client-history bleed clobbered budget_kind: got {p.budget_kind!r}"
    )
    # Bonus: client-history money values must NOT pollute the actual budget
    assert p.budget_min_usd in (40.0, None), (
        f"saw budget_min_usd={p.budget_min_usd}; expected hourly $40 or None"
    )


def test_truncate_helper_no_op_when_no_anchor():
    """Panels for clients with zero prior history don't render the
    section header. Truncation must be a no-op in that case."""
    from upwork.panel import _truncate_at_client_history
    elems = _mk_elements()
    out = _truncate_at_client_history(elems)
    assert len(out) == len(elems)


def test_truncate_helper_drops_history_section():
    from upwork.panel import _truncate_at_client_history
    elems = _mk_hourly_with_client_history()
    out = _truncate_at_client_history(elems)
    assert len(out) < len(elems)
    # No element in the truncated list should mention the history anchor
    assert all(
        "client's recent history" not in (e.name or "").lower()
        for e in out
    )
