"""Phase 2.B feed triage: text-LLM filter against the operator's active goal.

Sends UIA-extracted FeedCard records to gpt-5-mini and returns matched titles.
Em-dashes in titles must round-trip intact, so json.dumps uses
ensure_ascii=False. Hallucination defense (matched title not in input list)
is applied at the caller, not here.
"""
from __future__ import annotations

import json
from typing import Optional

from langchain.agents import create_agent
from pydantic import BaseModel, Field

from ai.cost_tracker import CostTracker
from storage.agent_runs import AgentRunStore
from storage.goals import Goal
from upwork.feed_cards import FeedCard


class TriageMatch(BaseModel):
    title: str = Field(..., description="The exact title text copied verbatim from the input list.")
    reason: str = Field(..., description="One sentence explaining why this card fits the goal.")


class TriageResponse(BaseModel):
    matches: list[TriageMatch] = Field(default_factory=list)


_TRIAGE_SYSTEM = (
    "You are a strict job-feed triage filter. Given the operator's active "
    "goal and a list of jobs from the Upwork feed, return ONLY the matches. "
    "Use the EXACT title text from the input list, copied verbatim. Be "
    "lenient on prose match (description preview suggesting the work could "
    "fit the goal's domain is enough), but strict on hard numeric targets "
    "if the goal specifies them (min hourly, min budget, preferred country, "
    "etc.). If a card has no budget shown, do not assume it; treat it as "
    "unknown rather than rejecting it for that reason alone. Never invent "
    "titles or paraphrase them. If no card fits, return an empty list."
)


def _goal_payload(goal: Goal) -> dict:
    return {
        "prose": goal.prose,
        "target_metric": goal.target_metric,
        "target_value": float(goal.target_value) if goal.target_value is not None else None,
        "horizon": goal.horizon,
        "min_hourly": float(goal.min_hourly) if goal.min_hourly is not None else None,
        "min_budget": float(goal.min_budget) if goal.min_budget is not None else None,
        "preferred_country": goal.preferred_country,
        "notes": goal.notes,
    }


def triage_feed_cards(
    cards: list[FeedCard],
    goal: Goal,
    *,
    agent_run_store: AgentRunStore,
    parent_run_id: Optional[int] = None,
    model: str = "gpt-5-mini",
) -> TriageResponse:
    """Run the goal triage call. Returns the structured TriageResponse.

    The caller is responsible for filtering survivors against the input
    title list (defense in depth against hallucinated / corrupted titles).
    """
    if not cards:
        return TriageResponse(matches=[])

    payload = {
        "goal": _goal_payload(goal),
        "jobs": [c.to_dict_for_llm() for c in cards],
    }
    # ensure_ascii=False is mandatory: without it, em-dashes in titles
    # become — escapes that the LLM then echoes back as control
    # characters or garbled text, breaking title-matching downstream.
    user = json.dumps(payload, indent=2, ensure_ascii=False)

    with CostTracker(
        agent_run_store,
        agent_name="feed_triage",
        trigger="scheduled",
        trigger_context={
            "n_cards": len(cards),
            "goal_id": goal.goal_id,
        },
        parent_run_id=parent_run_id,
    ) as tracker:
        agent = create_agent(model=model, response_format=TriageResponse)
        result = agent.invoke(
            {"messages": [
                {"role": "system", "content": _TRIAGE_SYSTEM},
                {"role": "user", "content": user},
            ]},
            config={"callbacks": [tracker]},
        )
    return result["structured_response"]
