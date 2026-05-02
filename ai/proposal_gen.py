"""Proposal Doc body + cover letter: single create_agent call each."""
from __future__ import annotations

from typing import Optional

from langchain.agents import create_agent

from ai.schemas import ProposalDraft, CoverLetter
from ai.prompts.proposal import PROPOSAL_SYSTEM, PROPOSAL_USER
from ai.prompts.cover_letter import COVER_LETTER_SYSTEM, COVER_LETTER_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from domain.types import Job


def generate_proposal(
    job: Job,
    portfolio_items: list,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-4o",
) -> ProposalDraft:
    portfolio_summary = "\n".join(
        f"- {p.get('name')}: {', '.join(p.get('relevance_tags') or [])}" for p in portfolio_items
    ) or "(no portfolio items provided)"
    user = PROPOSAL_USER.format(
        title=job.title,
        budget_text=_budget_text(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:3000],
        portfolio_summary=portfolio_summary,
    )
    with CostTracker(
        agent_run_store,
        agent_name="proposal",
        trigger="per_job",
        trigger_context={"job_id": job.job_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=ProposalDraft)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": PROPOSAL_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    return result["structured_response"]


def generate_cover_letter(
    job: Job,
    detected_client_name: Optional[str],
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-4o",
) -> CoverLetter:
    user = COVER_LETTER_USER.format(
        title=job.title,
        client_name=detected_client_name or "(none)",
    )
    with CostTracker(
        agent_run_store,
        agent_name="cover_letter",
        trigger="per_job",
        trigger_context={"job_id": job.job_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=CoverLetter)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": COVER_LETTER_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    return result["structured_response"]


def _budget_text(job: Job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return "unknown"
    if job.budget_min_usd == job.budget_max_usd or job.budget_max_usd is None:
        return f"{job.budget_kind} ${job.budget_min_usd:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"
