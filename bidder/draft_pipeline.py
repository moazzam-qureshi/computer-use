"""Take a draft Order; generate Doc + cover letter; update Order to awaiting_approval."""
from __future__ import annotations

from external import gdocs, mermaid
from ai.proposal_gen import generate_proposal, generate_cover_letter
from storage.orders import OrderStore
from storage.portfolio import PortfolioStore
from storage.agent_runs import AgentRunStore
from storage.setups import SetupStore
from domain.types import Job, Order


def draft_order(
    job: Job,
    order: Order,
    *,
    portfolio: PortfolioStore,
    order_store: OrderStore,
    agent_run_store: AgentRunStore,
    setups_store: SetupStore | None = None,
) -> Order:
    tone_override = None
    if setups_store is not None:
        s = setups_store.get(order.setup_id)
        if s is not None:
            tone_override = s.tone_override
    portfolio_items = portfolio.list_matching_tags(job.skills or [])
    proposal = generate_proposal(
        job, portfolio_items, agent_run_store=agent_run_store, tone_override=tone_override,
    )
    doc_url = gdocs.create_doc_with_diagram(
        proposal, mermaid_source=proposal.mermaid_diagram, job_title=job.title or "",
    )
    cover_letter = generate_cover_letter(
        job, detected_client_name=None, agent_run_store=agent_run_store,
        tone_override=tone_override,
    )
    body = cover_letter.body.replace("{{doc_url}}", doc_url or "(doc creation failed)")
    order_store.set_drafted(
        order_id=order.order_id,
        cover_letter_body=body,
        doc_url=doc_url,
        screening_answers_json=None,
    )
    order_store.update_status(order.order_id, "awaiting_approval")
    order.status = "awaiting_approval"
    order.cover_letter_body = body
    order.doc_url = doc_url
    return order
