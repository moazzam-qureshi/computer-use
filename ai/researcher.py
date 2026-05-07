"""find_specific_patterns: the forensic LLM call.

Single gpt-5-mini call. Reads a batch of full-description jobs + the
operator's portfolio + active goal, returns structured findings with
WHY fields enforced by pydantic.

The schema (ai.schemas.JobForensicFinding) does the work of forcing
specificity — empty/short WHY fields are rejected at parse time. This
module adds one more validation step: every evidence_job_id returned
must exist in the input batch, otherwise the finding is rejected as
hallucinated.

Empty findings list is a valid output. The prompt explicitly instructs
the LLM to return [] rather than invent shallow patterns.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from langchain.agents import create_agent
from pydantic import BaseModel, ValidationError

from ai.cost_tracker import CostTracker
from ai.prompts.researcher_forensics import (
    RESEARCHER_FORENSICS_SYSTEM,
    RESEARCHER_FORENSICS_USER,
)
from ai.schemas import JobForensicFinding
from domain.types import Job
from storage.agent_runs import AgentRunStore
from storage.goals import Goal


class ForensicFindings(BaseModel):
    """Wrapper so create_agent returns a list. response_format requires a
    BaseModel; a top-level List[Model] doesn't bind cleanly."""
    findings: list[JobForensicFinding]


def _job_block(job: Job) -> str:
    """Compact-but-readable per-job block for the user prompt.

    Description gets clipped at 2000 chars per job. With 30-50 jobs at
    2000 chars each + metadata, the user message lands around 60-100K
    chars — well within gpt-5-mini's context. Clipping is preferable to
    summarizing because pattern detection needs literal text (failure-
    mode mentions, tech-combo phrases).
    """
    pv_marker = (
        "yes" if job.client_payment_verified
        else "no" if job.client_payment_verified is False
        else "?"
    )
    budget = _budget_text(job)
    desc = (job.description or "").strip()
    if len(desc) > 2000:
        desc = desc[:2000] + "...(truncated)"
    skills = ", ".join(job.skills or []) or "(none listed)"
    return (
        f"--- job_id: {job.job_id}\n"
        f"title: {job.title}\n"
        f"posted: {job.posted_text or 'unknown'}\n"
        f"budget: {budget}\n"
        f"client_country: {job.client_country or '?'}  payment_verified: {pv_marker}  spent: {_spent_text(job)}\n"
        f"skills: {skills}\n"
        f"description:\n{desc}\n"
    )


def _budget_text(job: Job) -> str:
    if job.budget_min_usd is None and job.budget_max_usd is None:
        return f"{job.budget_kind or 'unknown'} (no $ shown)"
    if job.budget_min_usd == job.budget_max_usd or job.budget_max_usd is None:
        return f"{job.budget_kind} ${job.budget_min_usd:.0f}"
    return f"{job.budget_kind} ${job.budget_min_usd:.0f}-${job.budget_max_usd:.0f}"


def _spent_text(job: Job) -> str:
    if job.client_total_spent_usd is None:
        return "?"
    return f"${job.client_total_spent_usd:.0f}"


def _goal_block(goal: Optional[Goal]) -> str:
    if goal is None:
        return "(no active goal — surface findings broadly)"
    return (
        f"prose: {goal.prose}\n"
        f"min_hourly: {goal.min_hourly if goal.min_hourly is not None else '(unset)'}\n"
        f"min_budget: {goal.min_budget if goal.min_budget is not None else '(unset)'}\n"
        f"preferred_country: {goal.preferred_country or '(unset)'}\n"
        f"horizon: {goal.horizon or '(unset)'}\n"
        f"notes: {goal.notes or '(none)'}\n"
    )


def find_specific_patterns(
    jobs: Iterable[Job],
    portfolio: list[dict],
    active_goal: Optional[Goal],
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> list[JobForensicFinding]:
    """Run forensic pattern detection on a batch of jobs.

    Args:
        jobs: full-description Job objects from deep_search (or any
              source — but card-level snippets won't yield strong findings).
        portfolio: list[dict] from PortfolioStore.list_all().
        active_goal: optional active goal; threshold context for the LLM.
        agent_run_store: for cost / audit tracking.
        parent_run_id: optional parent for nested cost attribution.
        model: defaults to gpt-5-mini per spec §3.

    Returns:
        list[JobForensicFinding]. Empty if nothing specific surfaces, or
        if all returned findings failed validation (hallucinated job_ids,
        WHY field violations).

    Validation: every returned finding's evidence_job_ids are checked
    against the input batch's actual job_ids. Findings citing fabricated
    IDs are dropped (not raised) — one bad finding shouldn't kill the
    whole batch.
    """
    job_list = list(jobs)
    valid_ids = {j.job_id for j in job_list}

    if not job_list:
        return []  # nothing to analyze

    user = RESEARCHER_FORENSICS_USER.format(
        goal_block=_goal_block(active_goal),
        portfolio_json=json.dumps(portfolio, indent=2)[:10000],
        n_jobs=len(job_list),
        jobs_block="\n".join(_job_block(j) for j in job_list),
    )

    with CostTracker(
        agent_run_store,
        agent_name="researcher_forensics",
        trigger="researcher_pass",
        trigger_context={"n_jobs": len(job_list)},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=ForensicFindings)
        try:
            result = agent.invoke(
                {"messages": [
                    {"role": "system", "content": RESEARCHER_FORENSICS_SYSTEM},
                    {"role": "user", "content": user},
                ]},
                config={"callbacks": [tracker]},
            )
        except ValidationError:
            # The LLM produced something that didn't fit the schema at all
            # (e.g. all WHY fields too short). Treat as empty — schema is
            # the specificity gate, and shallow output should not pollute
            # the findings table.
            return []

    structured = result.get("structured_response")
    if structured is None:
        return []
    raw_findings: list[JobForensicFinding] = list(structured.findings)

    # Drop any finding whose evidence cites job_ids not in the input batch.
    # Hallucinated IDs would break operator trust ("get_finding 7 says
    # job ~01abc but that job doesn't exist").
    out: list[JobForensicFinding] = []
    for f in raw_findings:
        unknown = [jid for jid in f.evidence_job_ids if jid not in valid_ids]
        if unknown:
            # Silent drop. Could log/audit; keep simple for v1.
            continue
        out.append(f)
    return out
