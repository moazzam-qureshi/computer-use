"""Job to enrich to score to (if matches setup) signal + draft Order."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from domain.types import Job, Setup, Signal, Order, MatchResult
from domain.scoring import score_job_against_setup
from storage.setups import SignalStore
from storage.orders import OrderStore
from storage.enrichments import EnrichmentStore
from storage.agent_runs import AgentRunStore
from ai.enrichment import enrich_job
from ai.relevance import check_relevance


def process_job_through_setups(
    job: Job,
    *,
    setups: list[Setup],
    enrichment_store: EnrichmentStore,
    signal_store: SignalStore,
    order_store: OrderStore,
    agent_run_store: AgentRunStore,
) -> Optional[tuple[Signal, Order]]:
    """For one job: enrich, then score against each active setup, return Signal+Order if any match.

    Returns None if no setup matched. Signal+Order are written to DB; Order is in 'drafting' status.
    """
    # 1. Enrich (always, for corpus value), only if not yet enriched
    if enrichment_store.get(job.job_id) is None:
        enrichment = enrich_job(job, agent_run_store=agent_run_store)
        enrichment_store.upsert(job.job_id, enrichment)

    # 2. Score against each active setup. The rule layer is a CHEAP pre-filter
    #    only; the LLM relevance check is the actual gate that decides whether
    #    to fire a signal. We run the LLM for every active setup regardless of
    #    whether rules matched, because:
    #      - skill-name matching by string is too brittle (Upwork's skill chips
    #        differ slightly from canonical names; new buzzwords appear weekly)
    #      - the LLM sees the full job context (description, budget, client trust,
    #        skills) and can apply nuanced judgment that no DSL can encode
    #      - this lets us scale: setups become prose-driven strategies, not
    #        rule trees that need constant tuning
    matches: list[tuple[Setup, MatchResult]] = []
    for setup in setups:
        result = score_job_against_setup(job, setup)
        relevance = check_relevance(job, setup, agent_run_store=agent_run_store)
        if relevance.relevant:
            matches.append((setup, result))

    if not matches:
        return None

    # 3. Pick primary setup: highest tier (critical > normal > quiet), tie-break by id
    TIER_ORDER = {"critical": 0, "normal": 1, "quiet": 2}
    matches.sort(key=lambda m: (TIER_ORDER[m[0].tier], m[0].setup_id))
    primary_setup = matches[0][0]

    matched_setups_payload = [
        {"setup_id": s.setup_id, "match_reason": "rule+llm", "matched_rules": r.matched_rules}
        for s, r in matches
    ]

    # 4. Write Signal
    signal = Signal(
        signal_id=None,
        job_id=job.job_id,
        primary_setup_id=primary_setup.setup_id,
        matched_setups=matched_setups_payload,
        fired_at=datetime.now(timezone.utc),
        market_state={
            "proposals_count": job.proposals_count_at_first_scrape,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    signal_id = signal_store.create(signal)
    signal.signal_id = signal_id

    # 5. Create draft Order
    order = Order(
        order_id=None,
        signal_id=signal_id,
        job_id=job.job_id,
        setup_id=primary_setup.setup_id,
        status="drafting",
        bid_amount_usd=None,
        connects_spent=None,
        cover_letter_body=None,
        doc_url=None,
        screening_answers_json=None,
        drafted_at=None,
        approved_at=None,
        submitted_at=None,
        failed_reason=None,
        idempotency_key=str(uuid.uuid4()),
    )
    order_id = order_store.create_draft(order)
    order.order_id = order_id

    return signal, order
