"""Relevance tie-break: single create_agent call with structured output."""
from __future__ import annotations

from langchain.agents import create_agent

from ai.schemas import RelevanceCheck
from ai.prompts.relevance import RELEVANCE_SYSTEM, RELEVANCE_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from domain.types import Job, Setup


def check_relevance(
    job: Job,
    setup: Setup,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: int | None = None,
    model: str = "gpt-4o-mini",
) -> RelevanceCheck:
    user = RELEVANCE_USER.format(
        setup_name=setup.name,
        setup_prose=setup.prose_definition or "(no prose definition)",
        title=job.title,
        budget_text=_budget_text(job),
        posted_text="recent",
        client_summary=_client_summary(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:3000],
    )
    with CostTracker(
        agent_run_store,
        agent_name="relevance",
        trigger="per_job",
        trigger_context={"job_id": job.job_id, "setup_id": setup.setup_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=RelevanceCheck)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": RELEVANCE_SYSTEM},
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


def _client_summary(job: Job) -> str:
    parts = []
    if job.client_country:
        parts.append(job.client_country)
    if job.client_payment_verified:
        parts.append("payment verified")
    if job.client_total_spent_usd:
        parts.append(f"${job.client_total_spent_usd:.0f} spent")
    return ", ".join(parts) or "unknown"
