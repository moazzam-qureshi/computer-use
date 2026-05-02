"""Job enrichment: extract structured data from a job description."""
from __future__ import annotations

from langchain.agents import create_agent

from ai.schemas import Enrichment
from ai.prompts.enrichment import ENRICHMENT_SYSTEM, ENRICHMENT_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from domain.types import Job


def enrich_job(
    job: Job,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: int | None = None,
    model: str = "gpt-4o-mini",
) -> Enrichment:
    user = ENRICHMENT_USER.format(
        title=job.title,
        budget_text=_budget_text(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:3000],
    )
    with CostTracker(
        agent_run_store,
        agent_name="enrichment",
        trigger="per_job",
        trigger_context={"job_id": job.job_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=Enrichment)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": ENRICHMENT_SYSTEM},
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
