"""Operator-pulled BA proposers.

Two LLM-backed entry points called from assistant tools:

- propose_project_brief(theme, ...) -> ProjectGapBrief
- propose_setup(theme, filter_hint, ...) -> SetupProposal

Both reuse analyze_corpus + PortfolioStore + GoalStore. The setup proposer
runs backtest_setup INTERNALLY before returning so backtest_count is
grounded in real corpus matches; refuses zero-match proposals at the
caller's contract layer (see assistant.ba_tools.propose_setup_from_corpus).
"""
from __future__ import annotations

import json
from typing import Optional

from langchain.agents import create_agent
from pydantic import ValidationError

from ai.cost_tracker import CostTracker
from ai.market_analysis import analyze_corpus
from ai.prompts.ba_proposers import (
    PROJECT_BRIEF_SYSTEM, PROJECT_BRIEF_USER,
    SETUP_PROPOSAL_SYSTEM, SETUP_PROPOSAL_USER,
)
from ai.schemas import ProjectGapBrief, SetupProposal
from storage.agent_runs import AgentRunStore
from storage.connection import Database
from storage.goals import Goal


def _goal_block(goal: Optional[Goal]) -> str:
    if goal is None:
        return "(no active goal — surface broadly)"
    return (
        f"prose: {goal.prose}\n"
        f"min_hourly: {goal.min_hourly if goal.min_hourly is not None else '(unset)'}\n"
        f"min_budget: {goal.min_budget if goal.min_budget is not None else '(unset)'}\n"
        f"preferred_country: {goal.preferred_country or '(unset)'}\n"
        f"horizon: {goal.horizon or '(unset)'}\n"
        f"notes: {goal.notes or '(none)'}\n"
    )


def _portfolio_summary(items: list[dict]) -> str:
    """Compact portfolio for the LLM prompt — full text is too noisy."""
    if not items:
        return "(empty portfolio)"
    return json.dumps(
        [
            {
                "name": p.get("name"),
                "summary": (p.get("summary") or "")[:200],
                "tech": (p.get("tech") or [])[:8],
                "relevance_tags": (p.get("relevance_tags") or [])[:8],
                "outcome": (p.get("outcome") or "")[:150],
            }
            for p in items
        ],
        indent=2,
    )[:6000]


def _corpus_summary(snapshot) -> str:
    """One-line per dimension; no LLM-distracting verbosity."""
    if snapshot.total_jobs == 0:
        return "(corpus empty for this scope)"
    parts = [
        f"total_jobs: {snapshot.total_jobs}",
        f"budget_p25/p50/p75 (USD): {snapshot.budget_p25_usd}/{snapshot.budget_p50_usd}/{snapshot.budget_p75_usd}",
        f"kind_breakdown: {snapshot.budget_kind_breakdown}",
        f"payment_verified_share: {snapshot.payment_verified_share}",
    ]
    top_skills = snapshot.top_skills[:15]
    if top_skills:
        parts.append("top_skills: " + ", ".join(
            f"{s}({c}, {share:.0%})" for s, c, share in top_skills
        ))
    countries = snapshot.client_country_breakdown[:5]
    if countries:
        parts.append("top_countries: " + ", ".join(
            f"{c}({n}, {share:.0%})" for c, n, share in countries
        ))
    if snapshot.weekly_volume:
        parts.append("weekly_volume: " + ", ".join(
            f"{w}={n}" for w, n in snapshot.weekly_volume[-6:]
        ))
    return "\n".join(parts)


def propose_project_brief(
    db: Database,
    theme: str,
    *,
    portfolio: list[dict],
    active_goal: Optional[Goal],
    window_days: int = 30,
    source_pattern: Optional[str] = None,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> Optional[ProjectGapBrief]:
    """Generate a ProjectGapBrief for the operator-supplied theme.

    Returns None if the LLM produces output that fails schema validation
    (caller handles by surfacing an error to the operator).
    """
    snapshot = analyze_corpus(
        db, window_days=window_days, source_pattern=source_pattern,
    )
    user = PROJECT_BRIEF_USER.format(
        goal_block=_goal_block(active_goal),
        portfolio_summary=_portfolio_summary(portfolio),
        window_days=window_days,
        source_pattern=source_pattern,
        corpus_summary=_corpus_summary(snapshot),
        theme=theme,
    )
    agent_runs = AgentRunStore(db)
    with CostTracker(
        agent_runs,
        agent_name="ba_project_proposer",
        trigger="manual",
        trigger_context={"theme": theme},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=ProjectGapBrief)
        try:
            result = agent.invoke(
                {"messages": [
                    {"role": "system", "content": PROJECT_BRIEF_SYSTEM},
                    {"role": "user", "content": user},
                ]},
                config={"callbacks": [tracker]},
            )
        except ValidationError:
            return None
    return result.get("structured_response")


def propose_setup(
    db: Database,
    theme: str,
    *,
    portfolio: list[dict],
    active_goal: Optional[Goal],
    filter_hint: Optional[dict] = None,
    window_days: int = 30,
    source_pattern: Optional[str] = None,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> Optional[SetupProposal]:
    """Generate a SetupProposal for the operator-supplied theme.

    Caller is responsible for backtesting the returned proposal's filter_dsl
    against the corpus and overwriting backtest_count with the real number.
    Caller is also responsible for refusing zero-match proposals.
    """
    snapshot = analyze_corpus(
        db, window_days=window_days, source_pattern=source_pattern,
    )
    user = SETUP_PROPOSAL_USER.format(
        goal_block=_goal_block(active_goal),
        portfolio_summary=_portfolio_summary(portfolio),
        window_days=window_days,
        source_pattern=source_pattern,
        corpus_summary=_corpus_summary(snapshot),
        theme=theme,
        filter_hint=json.dumps(filter_hint or {}),
    )
    agent_runs = AgentRunStore(db)
    with CostTracker(
        agent_runs,
        agent_name="ba_setup_proposer",
        trigger="manual",
        trigger_context={"theme": theme},
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=SetupProposal)
        try:
            result = agent.invoke(
                {"messages": [
                    {"role": "system", "content": SETUP_PROPOSAL_SYSTEM},
                    {"role": "user", "content": user},
                ]},
                config={"callbacks": [tracker]},
            )
        except ValidationError:
            return None
    return result.get("structured_response")
