"""Goal-driven relevance check for Phase 2.B.

Mirrors ai/relevance.py but takes a Goal instead of a Setup. The legacy
check_relevance keeps working for briefed scans, which synthesize an
ephemeral retired Setup from a brief's prose; this is the goal-driven
path that detection-driven cards take.
"""
from __future__ import annotations

from typing import Optional

from langchain.agents import create_agent

from ai.schemas import RelevanceCheck
from ai.prompts.relevance_goal import RELEVANCE_GOAL_SYSTEM, RELEVANCE_GOAL_USER
from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from storage.goals import Goal
from domain.types import Job


def check_goal_relevance(
    job: Job,
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> RelevanceCheck:
    user = RELEVANCE_GOAL_USER.format(
        goal_prose=goal.prose,
        target_metric=goal.target_metric or "(unset)",
        target_value=goal.target_value if goal.target_value is not None else "(unset)",
        horizon=goal.horizon or "(unset)",
        min_hourly=goal.min_hourly if goal.min_hourly is not None else "(unset)",
        min_budget=goal.min_budget if goal.min_budget is not None else "(unset)",
        preferred_country=goal.preferred_country or "(unset)",
        notes=goal.notes or "(none)",
        title=job.title,
        budget_text=_budget_text(job),
        posted_text=job.posted_text or "recent",
        client_summary=_client_summary(job),
        skills=", ".join(job.skills or []),
        description=(job.description or "")[:3000],
    )
    with CostTracker(
        agent_run_store,
        agent_name="relevance_goal",
        trigger="per_job",
        trigger_context={"job_id": job.job_id, "goal_id": goal.goal_id},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=RelevanceCheck)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": RELEVANCE_GOAL_SYSTEM},
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
